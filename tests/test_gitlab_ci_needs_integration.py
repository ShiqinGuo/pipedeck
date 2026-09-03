"""真实 Docker needs 链集成:producer 写 artifact + dotenv,consumer 注入下游变量。"""

import shutil
import subprocess
from pathlib import Path

import pytest

from pipedeck.gitlab_ci.executor import JobContext, run_job
from pipedeck.gitlab_ci.model import ImageSpec, JobNeed, PipelineJob


class _Sink:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, job_name: str, stream: str, line: str) -> None:
        self.lines.append(f"[{job_name}] {line}")


@pytest.mark.integration
def test_docker_needs_chain_passes_dotenv_downstream(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("需要 docker")
    probe = subprocess.run(("docker", "version"), capture_output=True, check=False)
    if probe.returncode != 0:
        pytest.skip("docker daemon不可用")

    artifact_dir = tmp_path / "artifacts"
    producer_context = JobContext(
        job=PipelineJob(
            name="producer",
            stage="build",
            script=("echo VALUE=from-producer > out.env",),
            artifacts=("out.env",),
            dotenv_reports=("out.env",),
        ),
        variables={"CI_PROJECT_DIR": "/builds/demo"},
        workspace_dir=tmp_path / "workspace",
        artifact_dir=artifact_dir,
        project_name="demo",
    )
    sink = _Sink()
    producer = run_job(producer_context, sink=sink, cancelled=lambda: False)
    assert producer.succeeded, sink.lines
    assert producer.dotenv == {"VALUE": "from-producer"}

    # consumer 通过 needs 引用 producer;变量合成由调用方把 dotenv 注入其 variables
    consumer_variables = {
        "CI_PROJECT_DIR": "/builds/demo",
        **(producer.dotenv or {}),
    }
    consumer_context = JobContext(
        job=PipelineJob(
            name="consumer",
            stage="test",
            image=ImageSpec(name="alpine"),
            script=("echo passed=$VALUE",),
            needs=(JobNeed(job="producer"),),
        ),
        variables=consumer_variables,
        workspace_dir=tmp_path / "workspace",
        artifact_dir=artifact_dir,
        project_name="demo",
    )
    consumer = run_job(consumer_context, sink=sink, cancelled=lambda: False)
    assert consumer.succeeded, sink.lines
    assert any("passed=from-producer" in line for line in sink.lines)
