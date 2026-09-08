"""真实 Docker 端到端验证：容器 job、artifacts 归档、dotenv 传递（无 Docker 时跳过）。"""

import shutil
import subprocess
from pathlib import Path

import pytest

from pipedeck.gitlab_ci.executor import JobContext, run_job
from pipedeck.gitlab_ci.model import ImageSpec, PipelineJob


class _Sink:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, job_name: str, stream: str, line: str) -> None:
        self.lines.append(f"[{job_name}] {line}")


@pytest.mark.integration
def test_docker_container_job_end_to_end(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("需要 docker")
    probe = subprocess.run(("docker", "version"), capture_output=True, check=False)
    if probe.returncode != 0:
        pytest.skip("docker daemon不可用")

    context = JobContext(
        job=PipelineJob(
            name="e2e",
            image=ImageSpec(name="alpine:3.20"),
            stage="build",
            script=("echo pipeline=$GITLAB_CI", "mkdir -p out && echo step > out/echo.txt"),
            artifacts=("out/",),
            dotenv_reports=("missing.env",),
        ),
        variables={"GITLAB_CI": "false", "CI_PROJECT_DIR": "/builds/demo"},
        workspace_dir=tmp_path / "workspace",
        artifact_dir=tmp_path / "artifacts",
        project_name="demo",
    )
    context.workspace_dir.mkdir(parents=True)
    sink = _Sink()
    outcome = run_job(context, sink=sink, cancelled=lambda: False)

    assert outcome.succeeded, sink.lines
    assert any("pipeline=false" in line for line in sink.lines)
    assert (context.artifact_dir / "out" / "echo.txt").read_text(encoding="utf-8").strip() == "step"
    assert outcome.dotenv == {}
