from datetime import UTC, datetime

from pipedeck.contracts import (
    PlanCommand,
    PlanStep,
    PlanStepKind,
    RunMode,
    WorkspaceRecord,
    WorkspaceService,
)
from pipedeck.service_dependencies import order_startup_steps, service_order


def _workspace(services: tuple[WorkspaceService, ...]) -> WorkspaceRecord:
    return WorkspaceRecord(
        id="ws",
        name="integration",
        revision=1,
        mode=RunMode.INTEGRATED,
        services=services,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_missing_and_cyclic_dependencies_block_planning() -> None:
    _, issues = service_order(
        _workspace((WorkspaceService(project_id="front", depends_on=("api",)),))
    )
    assert issues[0].code == "SERVICE_DEPENDENCY_MISSING"
    _, issues = service_order(
        _workspace(
            (
                WorkspaceService(project_id="front", depends_on=("api",)),
                WorkspaceService(project_id="api", depends_on=("front",)),
            )
        )
    )
    assert issues[0].code == "SERVICE_DEPENDENCY_CYCLE"


def test_startup_order_respects_transitive_dependencies_without_moving_build_after_start() -> None:
    order, issues = service_order(
        _workspace(
            (
                WorkspaceService(project_id="front", depends_on=("api",)),
                WorkspaceService(project_id="api", depends_on=("worker",)),
                WorkspaceService(project_id="worker"),
            )
        )
    )
    assert not issues
    assert order == ("worker", "api", "front")
    commands = tuple(
        PlanCommand(
            project_id=p,
            project_name=p,
            command_id="start",
            label="start",
            cwd=".",
            argv=("python", "app.py"),
            long_running=True,
        )
        for p in reversed(order)
    )
    steps = order_startup_steps(
        (
            PlanStep(id="build", kind=PlanStepKind.BUILD, title="build", detail="", commands=()),
            PlanStep(
                id="start", kind=PlanStepKind.START, title="start", detail="", commands=commands
            ),
        ),
        order,
    )
    assert steps[0].kind is PlanStepKind.BUILD
    assert [step.commands[0].project_id for step in steps[1:]] == list(order)
