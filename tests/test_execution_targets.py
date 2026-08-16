from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_workspace_planning import GitStateRunner

from tripguru_local.contracts import (
    ArgumentPortInjection,
    CatalogResponse,
    CommandOwnedPortInjection,
    ComposeEndpoint,
    ComposeTarget,
    DockerfileSource,
    EnvironmentPortInjection,
    HostEndpoint,
    HostTarget,
    PlanStepKind,
    ProjectCommand,
    ProjectKind,
    ProjectSummary,
    RunMode,
    RuntimeResponse,
    ServiceCommand,
    TcpReadiness,
    WorkspaceInput,
    WorkspaceRecord,
    WorkspaceService,
)
from tripguru_local.processes import SubprocessRunner
from tripguru_local.workspace_planning import SavedWorkspacePlanner


def _port_not_in_use(_: int) -> bool:
    return False


def _catalog(path: Path, now: datetime) -> CatalogResponse:
    return CatalogResponse(
        generated_at=now,
        roots=(str(path.parent),),
        projects=(
            ProjectSummary(
                id="supplier",
                name="supplier",
                path=str(path),
                kind=ProjectKind.PYTHON_UV,
                branch="main",
                dirty=False,
                commands=(),
                requirements=(),
                warnings=(),
            ),
        ),
        errors=(),
    )


def _workspace(service: WorkspaceService, now: datetime) -> WorkspaceRecord:
    return WorkspaceRecord(
        id="workspace-targets",
        revision=1,
        name="Target contract",
        mode=RunMode.DEVELOPMENT,
        services=(service,),
        created_at=now,
        updated_at=now,
    )


def _runtime(now: datetime) -> RuntimeResponse:
    return RuntimeResponse(generated_at=now, docker_available=True, resources=())


def test_workspace_rejects_duplicate_project_identity() -> None:
    service = WorkspaceService(project_id="supplier")

    with pytest.raises(ValidationError):
        WorkspaceInput(
            name="Duplicate",
            mode=RunMode.DEVELOPMENT,
            services=(service, service),
        )


def test_target_union_rejects_checkout_escape_and_requires_compose_readiness() -> None:
    with pytest.raises(ValidationError):
        DockerfileSource(kind="dockerfile", context="../outside", dockerfile="Dockerfile")

    with pytest.raises(ValidationError):
        WorkspaceService.model_validate(
            {
                "project_id": "supplier",
                "execution_target": {
                    "kind": "compose",
                    "source": {
                        "kind": "dockerfile",
                        "context": ".",
                        "dockerfile": "Dockerfile",
                    },
                    "endpoints": [
                        {
                            "name": "api",
                            "protocol": "tcp",
                            "host_port": 18000,
                            "container_port": 8000,
                        }
                    ],
                },
            }
        )


def test_host_target_injects_environment_and_argument_without_changing_owned_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SavedWorkspacePlanner, "_port_in_use", _port_not_in_use)
    now = datetime.now(UTC)
    service = WorkspaceService(
        project_id="supplier",
        commands=(
            ServiceCommand(
                id="start",
                label="Start supplier",
                kind=PlanStepKind.START,
                argv=("supplier", "serve", "--metrics=43103"),
                long_running=True,
            ),
        ),
        execution_target=HostTarget(
            endpoints=(
                HostEndpoint(
                    name="api",
                    host_port=43101,
                    injection=EnvironmentPortInjection(kind="environment", name="PORT"),
                ),
                HostEndpoint(
                    name="admin",
                    host_port=43102,
                    injection=ArgumentPortInjection(kind="argument", option="--admin-port"),
                ),
                HostEndpoint(
                    name="metrics",
                    host_port=43103,
                    injection=CommandOwnedPortInjection(kind="command-owned"),
                ),
            ),
            readiness=TcpReadiness(kind="tcp", endpoint="api"),
        ),
    )

    plan = SavedWorkspacePlanner(GitStateRunner()).create(
        _workspace(service, now),
        _catalog(Path("D:/code/supplier"), now),
        _runtime(now),
    )

    assert plan.ready is True
    command = next(step for step in plan.steps if step.kind is PlanStepKind.START).commands[0]
    assert command.argv == (
        "supplier",
        "serve",
        "--metrics=43103",
        "--admin-port",
        "43102",
    )
    assert tuple((item.name, item.value) for item in command.environment) == (("PORT", "43101"),)


def test_host_target_blocks_duplicate_endpoints_missing_readiness_and_unowned_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SavedWorkspacePlanner, "_port_in_use", _port_not_in_use)
    now = datetime.now(UTC)
    service = WorkspaceService(
        project_id="supplier",
        commands=(
            ServiceCommand(
                id="start",
                label="Start supplier",
                kind=PlanStepKind.START,
                argv=("supplier", "serve"),
                long_running=True,
            ),
        ),
        execution_target=HostTarget(
            endpoints=(
                HostEndpoint(
                    name="api",
                    host_port=43201,
                    injection=CommandOwnedPortInjection(kind="command-owned"),
                ),
                HostEndpoint(
                    name="api",
                    host_port=43201,
                    injection=EnvironmentPortInjection(kind="environment", name="OTHER_PORT"),
                ),
            )
        ),
    )

    plan = SavedWorkspacePlanner(GitStateRunner()).create(
        _workspace(service, now),
        _catalog(Path("D:/code/supplier"), now),
        _runtime(now),
    )

    codes = {issue.code for issue in plan.blockers}
    assert {
        "TARGET_ENDPOINT_NAME_DUPLICATE",
        "TARGET_PORT_DUPLICATE",
        "TARGET_PORT_COMMAND_MISSING",
        "READINESS_REQUIRED",
    } <= codes


def test_host_target_blocks_readiness_reference_outside_endpoint_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(SavedWorkspacePlanner, "_port_in_use", _port_not_in_use)
    now = datetime.now(UTC)
    service = WorkspaceService(
        project_id="supplier",
        commands=(
            ServiceCommand(
                id="start",
                label="Start supplier",
                kind=PlanStepKind.START,
                argv=("supplier", "serve"),
                long_running=True,
            ),
        ),
        execution_target=HostTarget(
            endpoints=(
                HostEndpoint(
                    name="api",
                    host_port=43202,
                    injection=EnvironmentPortInjection(kind="environment", name="PORT"),
                ),
            ),
            readiness=TcpReadiness(kind="tcp", endpoint="missing"),
        ),
    )

    plan = SavedWorkspacePlanner(GitStateRunner()).create(
        _workspace(service, now),
        _catalog(Path("D:/code/supplier"), now),
        _runtime(now),
    )

    assert "READINESS_ENDPOINT_MISSING" in {issue.code for issue in plan.blockers}


def test_compose_target_returns_compiler_blocker_without_generic_commands() -> None:
    now = datetime.now(UTC)
    service = WorkspaceService(
        project_id="supplier",
        commands=(
            ServiceCommand(
                id="build",
                label="Generic build",
                kind=PlanStepKind.BUILD,
                argv=("docker", "build", "."),
            ),
            ServiceCommand(
                id="start",
                label="Generic start",
                kind=PlanStepKind.START,
                argv=("docker", "run", "supplier"),
            ),
        ),
        execution_target=ComposeTarget(
            kind="compose",
            source=DockerfileSource(kind="dockerfile"),
            endpoints=(ComposeEndpoint(name="api", host_port=43301, container_port=8000),),
            readiness=TcpReadiness(kind="tcp", endpoint="api"),
        ),
    )

    plan = SavedWorkspacePlanner(GitStateRunner()).create(
        _workspace(service, now),
        _catalog(Path("D:/code/supplier"), now),
        _runtime(now),
    )

    assert "DEPLOYMENT_COMPILER_UNAVAILABLE" in {issue.code for issue in plan.blockers}
    assert all(
        command.project_id != "supplier"
        for step in plan.steps
        if step.kind in {PlanStepKind.BUILD, PlanStepKind.DEPLOY, PlanStepKind.START}
        for command in step.commands
    )


def test_catalog_compose_project_requires_explicit_compose_target() -> None:
    now = datetime.now(UTC)
    service = WorkspaceService(project_id="supplier")
    catalog = _catalog(Path("D:/code/supplier"), now)
    compose_project = catalog.projects[0].model_copy(
        update={
            "kind": ProjectKind.COMPOSE,
            "commands": (
                ProjectCommand(
                    id="build",
                    label="Generic build",
                    kind=PlanStepKind.BUILD,
                    argv=("docker", "compose", "build"),
                ),
                ProjectCommand(
                    id="start",
                    label="Generic start",
                    kind=PlanStepKind.START,
                    argv=("docker", "compose", "up", "-d"),
                ),
            ),
        }
    )

    plan = SavedWorkspacePlanner(GitStateRunner()).create(
        _workspace(service, now),
        catalog.model_copy(update={"projects": (compose_project,)}),
        _runtime(now),
    )

    assert "EXPLICIT_COMPOSE_TARGET_REQUIRED" in {issue.code for issue in plan.blockers}
    assert all(
        command.project_id != "supplier"
        for step in plan.steps
        if step.kind in {PlanStepKind.BUILD, PlanStepKind.DEPLOY, PlanStepKind.START}
        for command in step.commands
    )


def _git(repository: Path, *arguments: str) -> None:
    subprocess.run(
        ("git", *arguments),
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    )


def test_source_fingerprint_changes_with_same_dirty_path_content_and_untracked_content(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "supplier"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.email", "tripguru-local@example.test")
    _git(repository, "config", "user.name", "TripGuru Local Test")
    (repository / ".gitignore").write_text("ignored.txt\n", encoding="utf-8")
    tracked = repository / "service.py"
    tracked.write_text("value = 0\n", encoding="utf-8")
    _git(repository, "add", ".gitignore", "service.py")
    _git(repository, "commit", "-m", "initial")
    now = datetime.now(UTC)
    service = WorkspaceService(
        project_id="supplier",
        commands=(
            ServiceCommand(
                id="start",
                label="Start",
                kind=PlanStepKind.START,
                argv=("python", "service.py"),
            ),
        ),
    )
    workspace = _workspace(service, now)
    catalog = _catalog(repository, now)
    planner = SavedWorkspacePlanner(SubprocessRunner())

    tracked.write_text("value = 1\n", encoding="utf-8")
    first = planner.create(workspace, catalog, _runtime(now))
    tracked.write_text("value = 2\n", encoding="utf-8")
    second = planner.create(workspace, catalog, _runtime(now))
    assert first.source_fingerprint != second.source_fingerprint

    untracked = repository / "local-config.txt"
    untracked.write_text("first\n", encoding="utf-8")
    third = planner.create(workspace, catalog, _runtime(now))
    untracked.write_text("second\n", encoding="utf-8")
    fourth = planner.create(workspace, catalog, _runtime(now))
    assert third.source_fingerprint != fourth.source_fingerprint

    ignored = repository / "ignored.txt"
    ignored.write_text("first\n", encoding="utf-8")
    fifth = planner.create(workspace, catalog, _runtime(now))
    ignored.write_text("second\n", encoding="utf-8")
    sixth = planner.create(workspace, catalog, _runtime(now))
    assert fifth.source_fingerprint == sixth.source_fingerprint
