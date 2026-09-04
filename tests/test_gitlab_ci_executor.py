import shutil
from pathlib import Path

import pytest

from pipedeck.gitlab_ci.executor import (
    DockerJobRunner,
    JobContext,
    compose_after_script,
    compose_script,
    parse_dotenv,
    run_job,
)
from pipedeck.gitlab_ci.model import ImageSpec, PipelineJob


class MemorySink:
    def __init__(self) -> None:
        self.lines: list[tuple[str, str, str]] = []

    def write(self, job_name: str, stream: str, line: str) -> None:
        self.lines.append((job_name, stream, line))


class FakeDockerCli:
    """记录 argv 序列并模拟 docker 子命令结果（不触碰真 Docker）。"""

    def __init__(self, *, start_exit_code: int = 0) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.start_exit_code = start_exit_code

    def run(
        self,
        argv: tuple[str, ...],
        *,
        job_name: str,
        sink: object,
        cancelled: object = None,
    ) -> int:
        self.calls.append(argv)
        command = argv[1] if len(argv) > 1 else ""
        if command == "image":
            return 1
        if command == "pull":
            return 0
        if command == "create":
            return 0
        if command == "start":
            return self.start_exit_code
        return 0


def _job(**kwargs: object) -> PipelineJob:
    defaults: dict[str, object] = {
        "name": "build_job",
        "stage": "build",
        "script": ("echo hi",),
    }
    defaults.update(kwargs)
    return PipelineJob(**defaults)  # type: ignore[arg-type]


def _context(
    tmp_path: Path,
    job: PipelineJob,
    *,
    variables: dict[str, str] | None = None,
) -> JobContext:
    return JobContext(
        job=job,
        variables=variables if variables is not None else {"CI_JOB_NAME": job.name},
        workspace_dir=tmp_path / "workspace",
        artifact_dir=tmp_path / "artifacts",
        project_name="demo",
    )


def test_compose_script_includes_before_and_set_e() -> None:
    job = _job(before_script=("echo before",), script=("echo main",))
    assert compose_script(job) == "set -e\necho before\necho main"


def test_compose_after_script() -> None:
    assert compose_after_script(_job()) is None
    after = compose_after_script(_job(after_script=("cleanup",)))
    assert after == "true\ncleanup"


def test_parse_dotenv() -> None:
    text = "# comment\n\nA=1\nB='two words'\nC=\"quoted\"\nD=x # not a comment\n"
    assert parse_dotenv(text) == {
        "A": "1",
        "B": "two words",
        "C": "quoted",
        "D": "x # not a comment",
    }


def test_run_job_excluded_job_is_noop(tmp_path: Path) -> None:
    sink = MemorySink()
    outcome = run_job(_context(tmp_path, _job(included=False)), sink=sink, cancelled=lambda: False)
    assert not outcome.succeeded
    assert outcome.error == "Job excluded by rules"


def test_host_job_success_streams_stdout(tmp_path: Path) -> None:
    if shutil.which("bash") is None:  # pragma: no cover
        pytest.skip("需要 bash")
    sink = MemorySink()
    outcome = run_job(_context(tmp_path, _job()), sink=sink, cancelled=lambda: False)
    assert outcome.succeeded
    assert outcome.exit_code == 0
    assert any(line == ("build_job", "stdout", "hi") for line in sink.lines)


def test_host_job_failure_exit_code(tmp_path: Path) -> None:
    if shutil.which("bash") is None:  # pragma: no cover
        pytest.skip("需要 bash")
    sink = MemorySink()
    job = _job(script=("echo oops >&2", "exit 3"))
    outcome = run_job(_context(tmp_path, job), sink=sink, cancelled=lambda: False)
    assert not outcome.succeeded
    assert outcome.exit_code == 3
    assert any(line[1] == "stderr" and "oops" in line[2] for line in sink.lines)


def test_host_job_allow_failure(tmp_path: Path) -> None:
    if shutil.which("bash") is None:  # pragma: no cover
        pytest.skip("需要 bash")
    sink = MemorySink()
    job = _job(script=("exit 3",), allow_failure=True)
    outcome = run_job(_context(tmp_path, job), sink=sink, cancelled=lambda: False)
    assert outcome.succeeded


def test_after_script_runs_and_does_not_affect_result(tmp_path: Path) -> None:
    if shutil.which("bash") is None:  # pragma: no cover
        pytest.skip("需要 bash")
    sink = MemorySink()
    job = _job(script=("echo ok",), after_script=("echo after", "exit 5"))
    outcome = run_job(_context(tmp_path, job), sink=sink, cancelled=lambda: False)
    assert outcome.succeeded
    assert any("after" in line[2] for line in sink.lines)


def test_host_artifacts_and_dotenv_collected(tmp_path: Path) -> None:
    if shutil.which("bash") is None:  # pragma: no cover
        pytest.skip("需要 bash")
    context = _context(
        tmp_path,
        _job(
            script=("mkdir -p out && echo data > out/a.txt && echo FOO=bar > build.env",),
            artifacts=("out/",),
            dotenv_reports=("build.env",),
        ),
    )
    context.workspace_dir.mkdir(parents=True)
    sink = MemorySink()
    outcome = run_job(context, sink=sink, cancelled=lambda: False)
    assert outcome.succeeded
    assert (context.artifact_dir / "out" / "a.txt").read_text(encoding="utf-8").strip() == "data"
    assert outcome.dotenv == {"FOO": "bar"}


def test_host_job_cancelled(tmp_path: Path) -> None:
    if shutil.which("bash") is None:  # pragma: no cover
        pytest.skip("需要 bash")
    sink = MemorySink()
    outcome = run_job(
        _context(tmp_path, _job(script=("sleep 5",))),
        sink=sink,
        cancelled=lambda: True,
    )
    assert outcome.cancelled


def test_docker_job_happy_path(tmp_path: Path) -> None:
    fake = FakeDockerCli()
    job = _job(image=ImageSpec(name="alpine"), script=("echo in container",))
    context = _context(tmp_path, job, variables={"CI_JOB_NAME": "build_job", "CUSTOM": "1"})
    sink = MemorySink()
    outcome = run_job(
        context, sink=sink, cancelled=lambda: False, docker_runner=DockerJobRunner(cli=fake)
    )
    assert outcome.succeeded
    commands = [argv[1] for argv in fake.calls]
    assert commands[0:3] == ["image", "pull", "create"]
    assert commands[-1] == "rm"
    create = next(argv for argv in fake.calls if argv[1] == "create")
    text = " ".join(create)
    assert "-e CI_JOB_NAME=build_job" in text
    assert "-e CUSTOM=1" in text
    assert "-v " in text and ":/builds/demo" in text
    assert text.endswith("echo in container")
    assert "alpine" in text
    start = next(argv for argv in fake.calls if argv[1] == "start")
    assert start[:3] == ("docker", "start", "-a")


def test_docker_job_start_failure(tmp_path: Path) -> None:
    fake = FakeDockerCli(start_exit_code=2)
    job = _job(image=ImageSpec(name="alpine"))
    context = _context(tmp_path, job)
    sink = MemorySink()
    outcome = run_job(
        context, sink=sink, cancelled=lambda: False, docker_runner=DockerJobRunner(cli=fake)
    )
    assert not outcome.succeeded
    assert outcome.exit_code == 2
    commands = [argv[1] for argv in fake.calls]
    assert commands[-1] == "rm"


def test_docker_job_cancelled_before_create(tmp_path: Path) -> None:
    fake = FakeDockerCli()
    job = _job(image=ImageSpec(name="alpine"))
    context = _context(tmp_path, job)
    sink = MemorySink()
    outcome = run_job(
        context, sink=sink, cancelled=lambda: True, docker_runner=DockerJobRunner(cli=fake)
    )
    assert outcome.cancelled
    assert all(argv[1] != "create" for argv in fake.calls)


def test_docker_job_artifacts_copied(tmp_path: Path) -> None:
    fake = FakeDockerCli()
    job = _job(image=ImageSpec(name="alpine"), artifacts=("dist/",))
    context = _context(tmp_path, job)
    sink = MemorySink()
    run_job(context, sink=sink, cancelled=lambda: False, docker_runner=DockerJobRunner(cli=fake))
    assert any(argv[1] == "cp" and argv[2].endswith(":/builds/demo/dist/") for argv in fake.calls)


def test_host_job_without_bash_reports_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _fake_which(*_args: object, **_kwargs: object) -> str | None:
        return None

    monkeypatch.setattr(shutil, "which", _fake_which)
    sink = MemorySink()
    outcome = run_job(_context(tmp_path, _job()), sink=sink, cancelled=lambda: False)
    assert not outcome.succeeded
    assert outcome.error is not None and "bash" in outcome.error
