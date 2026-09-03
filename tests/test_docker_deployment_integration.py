from __future__ import annotations

import hashlib
import json
import socket
import subprocess
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipedeck.compose_compiler import ComposeCompiler
from pipedeck.compose_deployment import (
    ComposeDeploymentWorkflow,
    DeploymentAction,
    DeploymentRevision,
    DeploymentStatus,
    ResolvedDeploymentVariable,
    derive_compose_project_name,
)
from pipedeck.contracts import (
    ComposeEndpoint,
    ComposeTarget,
    DockerfileSource,
    HttpReadiness,
    ProjectKind,
    ProjectSummary,
    RunMode,
    WorkspaceRecord,
    WorkspaceService,
)
from pipedeck.deployment_control import (
    DockerDeploymentRuntimeInspector,
    LocalComposeDeploymentRunner,
)
from pipedeck.readiness import ReadinessProbeRunner
from pipedeck.state_store import StateStore


class EmptyDeploymentEnvironment:
    def resolve(
        self,
        revision: DeploymentRevision,
        action: DeploymentAction,
    ) -> tuple[ResolvedDeploymentVariable, ...]:
        del revision, action
        return ()


def _docker(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ("docker", *arguments),
        check=check,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _workspace(
    checkout: Path, port: int, readiness_path: str
) -> tuple[WorkspaceRecord, ProjectSummary, ComposeTarget]:
    target = ComposeTarget(
        kind="compose",
        source=DockerfileSource(kind="dockerfile"),
        endpoints=(ComposeEndpoint(name="api", host_port=port, container_port=8080),),
        readiness=HttpReadiness(kind="http", endpoint="api", path=readiness_path),
        wait_timeout=45,
    )
    now = datetime.now(UTC)
    workspace = WorkspaceRecord(
        id="docker-acceptance-workspace",
        revision=1,
        name="Docker acceptance",
        mode=RunMode.INTEGRATED,
        services=(WorkspaceService(project_id="docker-acceptance-api", execution_target=target),),
        created_at=now,
        updated_at=now,
    )
    project = ProjectSummary(
        id="docker-acceptance-api",
        name="Docker acceptance API",
        path=str(checkout),
        kind=ProjectKind.UNKNOWN,
        branch="main",
        dirty=False,
        commands=(),
        requirements=(),
        warnings=(),
    )
    return workspace, project, target


def _source_fingerprint(checkout: Path) -> str:
    return hashlib.sha256((checkout / "ready").read_bytes()).hexdigest()


def _container_revision(workspace_id: str, project_id: str) -> tuple[str, str, str]:
    listed = _docker(
        "container",
        "ls",
        "--filter",
        f"label=tripguru.local/workspace={workspace_id}",
        "--filter",
        f"label=tripguru.local/target={project_id}",
        "--format",
        "{{.ID}}",
    ).stdout.splitlines()
    assert len(listed) == 1
    inspected = json.loads(_docker("inspect", listed[0]).stdout)[0]
    labels = inspected["Config"]["Labels"]
    return labels["tripguru.local/revision"], inspected["Image"], labels["tripguru.local/managed"]


def test_real_compose_deployment_replaces_with_immutable_revision_and_rolls_back(
    tmp_path: Path,
) -> None:
    if _docker("info", check=False).returncode != 0:
        pytest.skip("Docker daemon is unavailable")

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "Dockerfile").write_text(
        "\n".join(
            (
                "FROM python:3.12-slim",
                "WORKDIR /app",
                "COPY ready /app/ready",
                "HEALTHCHECK --interval=1s --timeout=2s --retries=20 CMD "
                'python -c "import urllib.request; urllib.request.urlopen('
                "'http://127.0.0.1:8080/ready'"
                ')"',
                'CMD ["python", "-m", "http.server", "8080", "--bind", "0.0.0.0"]',
            )
        )
        + "\n",
        encoding="utf-8",
    )
    (checkout / "ready").write_text("revision-one\n", encoding="utf-8")
    port = _available_port()
    workspace, project, healthy_target = _workspace(checkout, port, "/ready")
    compiler = ComposeCompiler(tmp_path / "artifacts")
    store = StateStore(tmp_path / "state.db")
    runner = LocalComposeDeploymentRunner()
    environment = EmptyDeploymentEnvironment()
    readiness = ReadinessProbeRunner()
    workflow = ComposeDeploymentWorkflow(
        store,
        runner,
        environment,
        readiness,
        DockerDeploymentRuntimeInspector(store, runner, environment, readiness),
    )
    first = compiler.compile(
        workspace=workspace,
        project=project,
        target=healthy_target,
        source_fingerprint=_source_fingerprint(checkout),
        target_config_fingerprint="1" * 64,
        revision_nonce="first-plan",
    )
    project_name = derive_compose_project_name(workspace.id, project.id)
    images: set[str] = set(first.immutable_images)
    try:
        first_revision = workflow.deploy(first)
        assert first_revision.status is DeploymentStatus.ACTIVE
        revision_id, image_digest, managed = _container_revision(workspace.id, project.id)
        assert revision_id == first.revision_id
        assert managed == "true"
        assert image_digest
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/ready", timeout=3) as response:
            assert response.read().strip() == b"revision-one"

        (checkout / "ready").write_text("revision-two\n", encoding="utf-8")
        updated_workspace, _, failing_target = _workspace(checkout, port, "/missing")
        second = compiler.compile(
            workspace=updated_workspace,
            project=project,
            target=failing_target,
            source_fingerprint=_source_fingerprint(checkout),
            target_config_fingerprint="2" * 64,
            revision_nonce="second-plan",
        )
        images.update(second.immutable_images)

        second_revision = workflow.deploy(second)
        assert second_revision.status is DeploymentStatus.ROLLED_BACK
        assert second_revision.previous_revision_id == first.revision_id
        current_revision, rolled_back_digest, managed = _container_revision(
            workspace.id, project.id
        )
        assert current_revision == first.revision_id
        assert rolled_back_digest == image_digest
        assert managed == "true"
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/ready", timeout=3) as response:
            assert response.read().strip() == b"revision-one"
    finally:
        _docker(
            "compose",
            "-f",
            str(first.frozen_compose_path),
            "-p",
            project_name,
            "down",
            "--remove-orphans",
            check=False,
        )
        for image in images:
            _docker("image", "rm", image, check=False)
        store.close()
