"""Order local service deployment and startup by explicit readiness dependencies."""

from pipedeck.contracts import PlanIssue, PlanStep, PlanStepKind, WorkspaceRecord


def service_order(workspace: WorkspaceRecord) -> tuple[tuple[str, ...], tuple[PlanIssue, ...]]:
    pending = {service.project_id: set(service.depends_on) for service in workspace.services}
    unknown = {dep for dependencies in pending.values() for dep in dependencies} - pending.keys()
    if unknown:
        return (), (
            PlanIssue(
                code="SERVICE_DEPENDENCY_MISSING",
                title="服务依赖不在工作区中",
                detail=", ".join(sorted(unknown)),
                recovery="将依赖项目加入工作区，或移除这条启动依赖后重新预检",
            ),
        )
    ordered: list[str] = []
    while pending:
        available = [project_id for project_id, dependencies in pending.items() if not dependencies]
        if not available:
            return (), (
                PlanIssue(
                    code="SERVICE_DEPENDENCY_CYCLE",
                    title="服务启动依赖形成循环",
                    detail=", ".join(pending),
                    recovery="移除循环或自身依赖，让被依赖服务能够先独立就绪",
                ),
            )
        for project_id in available:
            ordered.append(project_id)
            del pending[project_id]
        for dependencies in pending.values():
            dependencies.difference_update(available)
    return tuple(ordered), ()


def order_startup_steps(
    steps: tuple[PlanStep, ...], ordered: tuple[str, ...]
) -> tuple[PlanStep, ...]:
    """Keep finite preparation stages, then interleave host/Compose targets by dependency."""
    startup = [step for step in steps if step.kind in {PlanStepKind.START, PlanStepKind.DEPLOY}]
    result = [step for step in steps if step.kind not in {PlanStepKind.START, PlanStepKind.DEPLOY}]
    for project_id in ordered:
        for step in startup:
            commands = tuple(item for item in step.commands if item.project_id == project_id)
            deployments = tuple(item for item in step.deployments if item.project_id == project_id)
            if commands or deployments:
                result.append(
                    step.model_copy(
                        update={
                            "id": f"{step.id}:{project_id}",
                            "commands": commands,
                            "deployments": deployments,
                        }
                    )
                )
    return tuple(result)
