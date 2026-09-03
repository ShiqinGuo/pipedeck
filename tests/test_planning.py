from datetime import UTC, datetime

import pytest

from pipedeck.contracts import (
    CatalogResponse,
    MiddlewareBinding,
    MiddlewareKind,
    ProjectCommand,
    ProjectKind,
    ProjectSummary,
    ResourceHealth,
    RunMode,
    RuntimeResource,
    RuntimeResponse,
    WorkspacePlanRequest,
)
from pipedeck.planning import WorkspacePlanner


def _catalog(
    dirty: bool = False, *, include_start: bool = True, include_build: bool = True
) -> CatalogResponse:
    commands = [
        ProjectCommand(id="install", label="安装依赖", argv=("uv", "sync")),
        ProjectCommand(id="test", label="运行测试", argv=("uv", "run", "pytest")),
    ]
    if include_start:
        commands.append(
            ProjectCommand(
                id="dev",
                label="开发启动",
                argv=("uv", "run", "uvicorn", "app.main:app", "--reload"),
            )
        )
    if include_build:
        commands.append(ProjectCommand(id="build", label="构建项目", argv=("uv", "build")))
    return CatalogResponse(
        generated_at=datetime.now(UTC),
        roots=("D:/code",),
        projects=(
            ProjectSummary(
                id="project-1",
                name="supplier-backend-v2",
                path="D:/code/supplier-backend-v2",
                kind=ProjectKind.PYTHON_UV,
                branch="main",
                dirty=dirty,
                commands=tuple(commands),
                requirements=(MiddlewareKind.POSTGRES, MiddlewareKind.MINIO),
                warnings=(),
            ),
        ),
        errors=(),
    )


def _runtime(
    *,
    postgres_kind: MiddlewareKind = MiddlewareKind.POSTGRES,
    postgres_health: ResourceHealth = ResourceHealth.HEALTHY,
) -> RuntimeResponse:
    return RuntimeResponse(
        generated_at=datetime.now(UTC),
        docker_available=True,
        resources=(
            RuntimeResource(
                id="pg-1",
                name="local-postgres",
                kind=postgres_kind,
                image="postgres:18",
                state="running",
                status_text="Up 2 hours (healthy)",
                health=postgres_health,
                managed=True,
                protected=True,
                ports="5433->5432/tcp",
            ),
            RuntimeResource(
                id="minio-1",
                name="local-minio",
                kind=MiddlewareKind.MINIO,
                image="minio/minio:latest",
                state="running",
                status_text="Up 2 hours (healthy)",
                health=ResourceHealth.HEALTHY,
                managed=True,
                protected=True,
                ports="9000->9000/tcp",
            ),
        ),
    )


def test_plan_blocks_when_required_binding_is_missing() -> None:
    response = WorkspacePlanner().create(
        WorkspacePlanRequest(
            project_ids=("project-1",),
            mode=RunMode.DEVELOPMENT,
            bindings=(MiddlewareBinding(kind=MiddlewareKind.POSTGRES, resource_id="pg-1"),),
        ),
        _catalog(),
        _runtime(),
    )

    assert response.ready is False
    assert [blocker.code for blocker in response.blockers] == ["MIDDLEWARE_BINDING_REQUIRED"]


def test_integrated_plan_is_ready_with_all_bindings() -> None:
    response = WorkspacePlanner().create(
        WorkspacePlanRequest(
            project_ids=("project-1",),
            mode=RunMode.INTEGRATED,
            bindings=(
                MiddlewareBinding(kind=MiddlewareKind.POSTGRES, resource_id="pg-1"),
                MiddlewareBinding(kind=MiddlewareKind.MINIO, resource_id="minio-1"),
            ),
        ),
        _catalog(dirty=True),
        _runtime(),
    )

    assert response.ready is True
    assert any(step.id == "build" for step in response.steps)
    assert [warning.code for warning in response.warnings] == ["DIRTY_WORKTREE"]

    start_step = next(step for step in response.steps if step.id == "start")
    assert start_step.commands[0].project_id == "project-1"
    assert start_step.commands[0].project_name == "supplier-backend-v2"
    assert start_step.commands[0].cwd == "D:/code/supplier-backend-v2"
    assert start_step.commands[0].argv == (
        "uv",
        "run",
        "uvicorn",
        "app.main:app",
        "--reload",
    )

    serialized_commands = [command for step in response.steps for command in step.commands]
    assert all("<" not in argument for command in serialized_commands for argument in command.argv)
    assert all(command.argv != ("workspace", "health", "checks") for command in serialized_commands)


def test_plan_blocks_when_binding_target_kind_does_not_match() -> None:
    response = WorkspacePlanner().create(
        WorkspacePlanRequest(
            project_ids=("project-1",),
            mode=RunMode.DEVELOPMENT,
            bindings=(
                MiddlewareBinding(kind=MiddlewareKind.POSTGRES, resource_id="pg-1"),
                MiddlewareBinding(kind=MiddlewareKind.MINIO, resource_id="minio-1"),
            ),
        ),
        _catalog(),
        _runtime(postgres_kind=MiddlewareKind.REDIS),
    )

    assert response.ready is False
    assert [blocker.code for blocker in response.blockers] == ["MIDDLEWARE_TARGET_KIND_MISMATCH"]


@pytest.mark.parametrize("health", (ResourceHealth.STOPPED, ResourceHealth.UNHEALTHY))
def test_plan_blocks_when_binding_target_is_not_available(health: ResourceHealth) -> None:
    response = WorkspacePlanner().create(
        WorkspacePlanRequest(
            project_ids=("project-1",),
            mode=RunMode.DEVELOPMENT,
            bindings=(
                MiddlewareBinding(kind=MiddlewareKind.POSTGRES, resource_id="pg-1"),
                MiddlewareBinding(kind=MiddlewareKind.MINIO, resource_id="minio-1"),
            ),
        ),
        _catalog(),
        _runtime(postgres_health=health),
    )

    assert response.ready is False
    assert [blocker.code for blocker in response.blockers] == ["MIDDLEWARE_TARGET_UNAVAILABLE"]


def test_plan_accepts_running_binding_target_without_healthcheck() -> None:
    response = WorkspacePlanner().create(
        WorkspacePlanRequest(
            project_ids=("project-1",),
            mode=RunMode.DEVELOPMENT,
            bindings=(
                MiddlewareBinding(kind=MiddlewareKind.POSTGRES, resource_id="pg-1"),
                MiddlewareBinding(kind=MiddlewareKind.MINIO, resource_id="minio-1"),
            ),
        ),
        _catalog(),
        _runtime(postgres_health=ResourceHealth.RUNNING),
    )

    assert response.ready is True


def test_plan_blocks_when_project_has_no_start_contract() -> None:
    response = WorkspacePlanner().create(
        WorkspacePlanRequest(
            project_ids=("project-1",),
            mode=RunMode.DEVELOPMENT,
            bindings=(
                MiddlewareBinding(kind=MiddlewareKind.POSTGRES, resource_id="pg-1"),
                MiddlewareBinding(kind=MiddlewareKind.MINIO, resource_id="minio-1"),
            ),
        ),
        _catalog(include_start=False),
        _runtime(),
    )

    assert response.ready is False
    assert [blocker.code for blocker in response.blockers] == ["START_COMMAND_UNRESOLVED"]


def test_integrated_plan_blocks_when_project_has_no_build_contract() -> None:
    response = WorkspacePlanner().create(
        WorkspacePlanRequest(
            project_ids=("project-1",),
            mode=RunMode.INTEGRATED,
            bindings=(
                MiddlewareBinding(kind=MiddlewareKind.POSTGRES, resource_id="pg-1"),
                MiddlewareBinding(kind=MiddlewareKind.MINIO, resource_id="minio-1"),
            ),
        ),
        _catalog(include_build=False),
        _runtime(),
    )

    assert response.ready is False
    assert [blocker.code for blocker in response.blockers] == ["BUILD_COMMAND_UNRESOLVED"]
