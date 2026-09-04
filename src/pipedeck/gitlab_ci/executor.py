"""GitLab CI job 执行器。

有 image 的 job 在其镜像容器内执行（workspace bind-mount + 变量注入 +
`docker start -a` 流式日志），无 image 的 job 在宿主 bash 执行；
`artifacts:paths` 归档到 state 目录，`reports:dotenv` 解析后供下游 needs job 注入。

执行器不拥有 Run 状态：日志与取消通过 Protocol 注入，由 Plan/Run 状态机消费。
`before_script` 失败使 job 失败；`after_script` 始终执行，其失败不改变 job 结果。
"""

import contextlib
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, TextIO
from uuid import uuid4

from pipedeck.gitlab_ci.model import PipelineJob

_CONTAINER_PROJECT_ROOT = "/builds"
_UNIX_SHELL = "sh"
_CANCEL_POLL_SECONDS = 0.1


class JobLogSink(Protocol):
    def write(self, job_name: str, stream: str, line: str) -> None: ...


@dataclass(frozen=True, slots=True)
class JobContext:
    job: PipelineJob
    variables: dict[str, str]
    workspace_dir: Path
    artifact_dir: Path
    project_name: str
    container_executable: str = "docker"

    @property
    def container_project_dir(self) -> str:
        return f"{_CONTAINER_PROJECT_ROOT}/{self.project_name}"


@dataclass(frozen=True, slots=True)
class JobStepResult:
    succeeded: bool
    exit_code: int | None = None
    cancelled: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True)
class JobOutcome:
    succeeded: bool
    cancelled: bool = False
    exit_code: int | None = None
    artifact_paths: tuple[str, ...] = ()
    dotenv: dict[str, str] | None = None
    error: str | None = None


def compose_script(job: PipelineJob) -> str:
    lines = ["set -e"]
    lines.extend(job.before_script)
    lines.extend(job.script)
    return "\n".join(lines)


def compose_after_script(job: PipelineJob) -> str | None:
    if not job.after_script:
        return None
    return "\n".join(["true"] + list(job.after_script))


def parse_dotenv(text: str) -> dict[str, str]:
    """解析 dotenv 报告：忽略注释与空行，值可带单/双引号，不递归展开。"""
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key:
            values[key] = value
    return values


class JobRunner(Protocol):
    def run(
        self,
        context: JobContext,
        *,
        script: str,
        sink: JobLogSink,
        cancelled: Callable[[], bool],
    ) -> JobStepResult: ...


class DockerCli(Protocol):
    """docker CLI 抽象：LocalDockerCli 是默认实现，测试可注入假实现。"""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        job_name: str,
        sink: JobLogSink,
        cancelled: Callable[[], bool],
    ) -> int: ...


class LocalDockerCli:
    """`docker` CLI 流式适配：合并输出逐行进 sink，进程可被取消杀死。"""

    def run(
        self,
        argv: tuple[str, ...],
        *,
        job_name: str,
        sink: JobLogSink,
        cancelled: Callable[[], bool],
    ) -> int:
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        process = subprocess.Popen(  # noqa: S603
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creation_flags,
        )
        assert process.stdout is not None
        while True:
            if cancelled() and process.poll() is None:
                _terminate_tree(process)
                break
            line = process.stdout.readline()
            if not line:
                break
            sink.write(job_name, "stdout", line.rstrip("\n"))
        return process.wait()


class DockerJobRunner:
    """在 job 声明的镜像内执行：ensure image → create → start -a → cp → rm。"""

    def __init__(self, cli: DockerCli | None = None) -> None:
        self._cli = cli or LocalDockerCli()

    def run(
        self,
        context: JobContext,
        *,
        script: str,
        sink: JobLogSink,
        cancelled: Callable[[], bool],
    ) -> JobStepResult:
        job = context.job
        image = job.image.name if job.image is not None else ""
        container = f"pipedeck-{context.project_name}-{job.name}-{uuid4().hex[:8]}"
        docker = context.container_executable

        if cancelled():
            return JobStepResult(False, cancelled=True)
        if self._ensure_image(context, image, sink, cancelled) != 0:
            return JobStepResult(False, error=f"镜像 {image} 不可用（inspect/pull 失败）")
        if cancelled():
            return JobStepResult(False, cancelled=True)
        try:
            create_code = self._cli.run(
                (
                    docker,
                    "create",
                    "--name",
                    container,
                    "--workdir",
                    context.container_project_dir,
                    *self._environment_args(context),
                    "-v",
                    _bind_mount(context.workspace_dir, context.container_project_dir),
                    *self._entrypoint_args(job),
                    image,
                    _UNIX_SHELL,
                    "-c",
                    script,
                ),
                job_name=job.name,
                sink=sink,
                cancelled=cancelled,
            )
            if create_code != 0:
                return JobStepResult(False, error="docker create 失败")
            exit_code = self._cli.run(
                (docker, "start", "-a", container),
                job_name=job.name,
                sink=sink,
                cancelled=cancelled,
            )
            if cancelled():
                return JobStepResult(False, cancelled=True)
            return JobStepResult(exit_code == 0, exit_code=exit_code)
        finally:
            self._collect_artifacts(context, container, sink, cancelled)
            self._cli.run(
                (docker, "rm", "-f", container),
                job_name=job.name,
                sink=sink,
                cancelled=cancelled,
            )

    def _ensure_image(
        self,
        context: JobContext,
        image: str,
        sink: JobLogSink,
        cancelled: Callable[[], bool],
    ) -> int:
        docker = context.container_executable
        inspect = self._cli.run(
            (docker, "image", "inspect", image),
            job_name=context.job.name,
            sink=sink,
            cancelled=cancelled,
        )
        if inspect == 0 or cancelled():
            return inspect
        return self._cli.run(
            (docker, "pull", image),
            job_name=context.job.name,
            sink=sink,
            cancelled=cancelled,
        )

    def _environment_args(self, context: JobContext) -> tuple[str, ...]:
        args: list[str] = []
        for key, value in context.variables.items():
            args.extend(["-e", f"{key}={value}"])
        return tuple(args)

    def _entrypoint_args(self, job: PipelineJob) -> tuple[str, ...]:
        entrypoint = job.image.entrypoint if job.image is not None else ()
        if not entrypoint:
            return ()
        return ("--entrypoint", entrypoint[0], *entrypoint[1:])

    def _collect_artifacts(
        self,
        context: JobContext,
        container: str,
        sink: JobLogSink,
        cancelled: Callable[[], bool],
    ) -> None:
        docker = context.container_executable
        for relative in context.job.artifacts:
            if cancelled():
                return
            destination = context.artifact_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            source = f"{container}:{context.container_project_dir}/{relative}"
            self._cli.run(
                (docker, "cp", source, str(destination)),
                job_name=context.job.name,
                sink=sink,
                cancelled=cancelled,
            )


class HostJobRunner:
    """无 image 的 job：在宿主 bash 中执行（工作区目录即 CI_PROJECT_DIR）。"""

    def run(
        self,
        context: JobContext,
        *,
        script: str,
        sink: JobLogSink,
        cancelled: Callable[[], bool],
    ) -> JobStepResult:
        bash = shutil.which("bash")
        if bash is None:
            return JobStepResult(False, error="宿主机未找到 bash，无法执行无 image 的 job")
        environment = dict(os.environ)
        environment.update(context.variables)
        environment["CI_PROJECT_DIR"] = str(context.workspace_dir)
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        process = subprocess.Popen(  # noqa: S603
            (bash, "-c", script),
            cwd=context.workspace_dir,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=creation_flags,
        )
        assert process.stdout is not None and process.stderr is not None

        def _pump(stream: TextIO, stream_name: str) -> None:
            assert stream is not None
            for line in stream:
                sink.write(context.job.name, stream_name, line.rstrip("\n"))

        pump_out = threading.Thread(target=_pump, args=(process.stdout, "stdout"), daemon=True)
        pump_err = threading.Thread(target=_pump, args=(process.stderr, "stderr"), daemon=True)
        pump_out.start()
        pump_err.start()
        was_cancelled = False
        while process.poll() is None:
            if cancelled():
                was_cancelled = True
                _terminate_tree(process)
                break
            time.sleep(_CANCEL_POLL_SECONDS)
        pump_out.join(timeout=5)
        pump_err.join(timeout=5)
        exit_code = process.wait()
        if was_cancelled:
            return JobStepResult(False, cancelled=True)
        return JobStepResult(exit_code == 0, exit_code=exit_code)


def run_job(
    context: JobContext,
    *,
    sink: JobLogSink,
    cancelled: Callable[[], bool],
    docker_runner: DockerJobRunner | None = None,
    host_runner: HostJobRunner | None = None,
) -> JobOutcome:
    """执行单个 job：主脚本决定结果，after_script 始终执行并输出。"""
    job = context.job
    if not job.included:
        return JobOutcome(succeeded=False, error="Job excluded by rules")
    if job.image is not None:
        runner: JobRunner = docker_runner or DockerJobRunner()
    else:
        runner = host_runner or HostJobRunner()
    context.artifact_dir.mkdir(parents=True, exist_ok=True)
    context.workspace_dir.mkdir(parents=True, exist_ok=True)

    main = runner.run(context, script=compose_script(job), sink=sink, cancelled=cancelled)
    if main.cancelled:
        return JobOutcome(succeeded=False, cancelled=True, error=main.error)
    after = compose_after_script(job)
    if after is not None and not cancelled():
        runner.run(context, script=after, sink=sink, cancelled=cancelled)
    _collect_host_artifacts(context)

    dotenv = _collect_dotenv(context)
    if not main.succeeded and job.allow_failure:
        return JobOutcome(
            succeeded=True,
            exit_code=main.exit_code,
            artifact_paths=job.artifacts,
            dotenv=dotenv,
            error=main.error,
        )
    if not main.succeeded:
        return JobOutcome(
            succeeded=False,
            exit_code=main.exit_code,
            artifact_paths=job.artifacts,
            dotenv=dotenv,
            error=main.error,
        )
    return JobOutcome(
        succeeded=True,
        exit_code=main.exit_code,
        artifact_paths=job.artifacts,
        dotenv=dotenv,
    )


def _collect_host_artifacts(context: JobContext) -> None:
    """宿主 job 的 artifacts 直接从工作区拷贝（容器 job 由 docker cp 负责）。"""
    if context.job.image is not None:
        return
    for relative in context.job.artifacts:
        source = context.workspace_dir / relative
        destination = context.artifact_dir / relative
        if not source.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)


def _collect_dotenv(context: JobContext) -> dict[str, str] | None:
    if not context.job.dotenv_reports:
        return None
    values: dict[str, str] = {}
    for relative in context.job.dotenv_reports:
        for candidate in (context.artifact_dir / relative, context.workspace_dir / relative):
            if candidate.is_file():
                values.update(parse_dotenv(candidate.read_text(encoding="utf-8")))
                break
    return values


def _bind_mount(host_dir: Path, container_dir: str) -> str:
    # Docker Desktop（Windows）接受 D:/code/x 形式的绑定挂载；POSIX 直接 as_posix。
    return f"{host_dir.resolve().as_posix()}:{container_dir}"


def _terminate_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603
            ("taskkill", "/PID", str(process.pid), "/T", "/F"),
            capture_output=True,
            check=False,
        )
    else:
        process.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=10)
