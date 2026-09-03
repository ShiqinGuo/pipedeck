from datetime import UTC, datetime
from pathlib import Path

from pytest import MonkeyPatch

from pipedeck.contracts import (
    CatalogResponse,
    EnvironmentBinding,
    EnvironmentPortInjection,
    EnvironmentSource,
    HostEndpoint,
    HostTarget,
    PlanStepKind,
    ProjectKind,
    ProjectSummary,
    RunMode,
    RuntimeResponse,
    ServiceCommand,
    TcpReadiness,
    WorkspaceRecord,
    WorkspaceService,
)
from pipedeck.processes import CommandResult
from pipedeck.workspace_planning import SavedWorkspacePlanner


class GitStateRunner:
    def run(self, argv: tuple[str, ...], cwd: Path | None = None) -> CommandResult:
        if argv == ("git", "status", "--porcelain"):
            return CommandResult(return_code=0, stdout="", stderr="")
        if argv == ("git", "rev-parse", "HEAD"):
            return CommandResult(return_code=0, stdout="abc123", stderr="")
        if argv == ("git", "diff", "--binary", "--no-ext-diff", "HEAD"):
            return CommandResult(return_code=0, stdout="", stderr="")
        if argv == ("git", "ls-files", "--others", "--exclude-standard", "-z"):
            return CommandResult(return_code=0, stdout="", stderr="")
        raise AssertionError(argv)


def _port_not_in_use(_: int) -> bool:
    return False


def test_saved_workspace_uses_configured_start_command(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("SUPPLIER_LOCAL_DATABASE_URL", "postgresql://secret")
    monkeypatch.setattr(SavedWorkspacePlanner, "_port_in_use", _port_not_in_use)
    now = datetime.now(UTC)
    workspace = WorkspaceRecord(
        id="workspace-1",
        revision=2,
        name="Supplier local",
        mode=RunMode.DEVELOPMENT,
        services=(
            WorkspaceService(
                project_id="supplier",
                commands=(
                    ServiceCommand(
                        id="supplier-dev",
                        label="启动 Supplier API",
                        kind=PlanStepKind.START,
                        argv=("uv", "run", "uvicorn", "app.main:app"),
                        long_running=True,
                    ),
                ),
                environment=(
                    EnvironmentBinding(
                        name="SUPPLIER_DATABASE_URL",
                        source=EnvironmentSource.HOST_ENV,
                        reference="SUPPLIER_LOCAL_DATABASE_URL",
                    ),
                ),
                execution_target=HostTarget(
                    endpoints=(
                        HostEndpoint(
                            name="api",
                            host_port=8000,
                            injection=EnvironmentPortInjection(
                                kind="environment",
                                name="PORT",
                            ),
                        ),
                    ),
                    readiness=TcpReadiness(kind="tcp", endpoint="api"),
                ),
            ),
        ),
        created_at=now,
        updated_at=now,
    )
    catalog = CatalogResponse(
        generated_at=now,
        roots=("D:/code",),
        projects=(
            ProjectSummary(
                id="supplier",
                name="supplier-backend-v2",
                path="D:/code/supplier-backend-v2",
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
    runtime = RuntimeResponse(generated_at=now, docker_available=True, resources=())

    plan = SavedWorkspacePlanner(GitStateRunner()).create(workspace, catalog, runtime)

    assert plan.ready is True
    assert plan.workspace_id == "workspace-1"
    assert plan.workspace_revision == 2
    assert plan.config_fingerprint is not None
    assert plan.source_fingerprint is not None
    start_command = next(step for step in plan.steps if step.id == "start").commands[0]
    assert start_command.command_id == "supplier-dev"
    assert start_command.long_running is True
    assert [(item.name, item.value) for item in start_command.environment] == [("PORT", "8000")]


def test_saved_workspace_blocks_missing_host_environment_reference() -> None:
    now = datetime.now(UTC)
    workspace = WorkspaceRecord(
        id="workspace-1",
        revision=1,
        name="Supplier local",
        mode=RunMode.DEVELOPMENT,
        services=(
            WorkspaceService(
                project_id="supplier",
                commands=(
                    ServiceCommand(
                        id="start",
                        label="Start",
                        kind=PlanStepKind.START,
                        argv=("app",),
                    ),
                ),
                environment=(
                    EnvironmentBinding(
                        name="DATABASE_URL",
                        source=EnvironmentSource.HOST_ENV,
                        reference="PIPEDECK_TEST_MISSING_SECRET",
                    ),
                ),
            ),
        ),
        created_at=now,
        updated_at=now,
    )
    catalog = CatalogResponse(
        generated_at=now,
        roots=("D:/code",),
        projects=(
            ProjectSummary(
                id="supplier",
                name="supplier",
                path="D:/code/supplier",
                kind=ProjectKind.UNKNOWN,
                branch="main",
                dirty=False,
                commands=(),
                requirements=(),
                warnings=(),
            ),
        ),
        errors=(),
    )

    plan = SavedWorkspacePlanner(GitStateRunner()).create(
        workspace,
        catalog,
        RuntimeResponse(generated_at=now, docker_available=True, resources=()),
    )

    assert plan.ready is False
    assert "ENVIRONMENT_REFERENCE_MISSING" in {issue.code for issue in plan.blockers}


def test_saved_workspace_does_not_resolve_secret_store_as_host_environment() -> None:
    now = datetime.now(UTC)
    workspace = WorkspaceRecord(
        id="workspace-secret",
        revision=1,
        name="Secret reference",
        mode=RunMode.DEVELOPMENT,
        services=(
            WorkspaceService(
                project_id="supplier",
                commands=(
                    ServiceCommand(
                        id="start",
                        label="Start",
                        kind=PlanStepKind.START,
                        argv=("app",),
                    ),
                ),
                environment=(
                    EnvironmentBinding(
                        name="APP_SECRET",
                        source=EnvironmentSource.SECRET_STORE,
                        reference="0f12-secret-id",
                    ),
                ),
            ),
        ),
        created_at=now,
        updated_at=now,
    )
    catalog = CatalogResponse(
        generated_at=now,
        roots=("D:/code",),
        projects=(
            ProjectSummary(
                id="supplier",
                name="supplier",
                path="D:/code/supplier",
                kind=ProjectKind.UNKNOWN,
                branch="main",
                dirty=False,
                commands=(),
                requirements=(),
                warnings=(),
            ),
        ),
        errors=(),
    )

    plan = SavedWorkspacePlanner(GitStateRunner()).create(
        workspace,
        catalog,
        RuntimeResponse(generated_at=now, docker_available=True, resources=()),
    )

    assert "ENVIRONMENT_REFERENCE_MISSING" not in {issue.code for issue in plan.blockers}
