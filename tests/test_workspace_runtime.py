"""Runtime observations join durable plans to actual owned runtime identities."""

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from pipedeck.compose_deployment import (
    DeploymentIntent,
    DeploymentRevision,
    DeploymentStatus,
    HttpProbe,
    RuntimeTargetState,
)
from pipedeck.contracts import (
    ApplicationEntry,
    ComposeDeploymentPlan,
    ComposeEndpoint,
    ComposeTarget,
    DeploymentEnvironmentSnapshot,
    DockerfileSource,
    HostTarget,
    HttpDeploymentProbeSpec,
    HttpReadiness,
    PlanStep,
    PlanStepKind,
    RunMode,
    RunRecord,
    RunStatus,
    RuntimeProcessListResponse,
    WorkspaceInput,
    WorkspacePlanResponse,
    WorkspaceRecord,
    WorkspaceService,
    WorkspaceUpdateRequest,
)
from pipedeck.deployment_control import DockerDeploymentRuntimeInspector
from pipedeck.execution import ExecutionEngine
from pipedeck.readiness import LocalReadinessTransport
from pipedeck.state_store import StateStore
from pipedeck.workspace_runtime import WorkspaceRuntimeObserver


class EmptyExecution:
    def list_processes(self) -> RuntimeProcessListResponse:
        return RuntimeProcessListResponse(generated_at=datetime.now(UTC), processes=())


class RuntimeInspector:
    def __init__(self, observed: RuntimeTargetState) -> None:
        self.observed = observed
        self.targets: list[str] = []

    def inspect(
        self, workspace_id: str, target_id: str, *, wait_for_readiness: bool = True
    ) -> RuntimeTargetState:
        assert workspace_id == "workspace"
        assert not wait_for_readiness
        self.targets.append(target_id)
        return self.observed


def _observer(store: StateStore, inspector: RuntimeInspector) -> WorkspaceRuntimeObserver:
    return WorkspaceRuntimeObserver(
        store,
        cast(ExecutionEngine, EmptyExecution()),
        cast(DockerDeploymentRuntimeInspector, inspector),
    )


def _target(port: int = 18101, path: str = "/old-app") -> ComposeTarget:
    return ComposeTarget(
        kind="compose",
        source=DockerfileSource(kind="dockerfile"),
        endpoints=(ComposeEndpoint(name="http", host_port=port, container_port=8080),),
        readiness=HttpReadiness(kind="http", endpoint="http", path="/health"),
        application=ApplicationEntry(endpoint="http", path=path),
    )


def _workspace(store: StateStore, service: WorkspaceService) -> WorkspaceRecord:
    return store.create_workspace(
        WorkspaceInput(name="Integration", mode=RunMode.INTEGRATED, services=(service,)),
        workspace_id="workspace",
    )


def _plan(
    store: StateStore,
    workspace: WorkspaceRecord,
    plan_id: str,
    deployment: ComposeDeploymentPlan | None = None,
) -> WorkspacePlanResponse:
    return store.save_plan(
        WorkspacePlanResponse(
            generated_at=datetime.now(UTC),
            ready=True,
            mode=workspace.mode,
            projects=(),
            service_targets={s.project_id: s.execution_target for s in workspace.services},
            steps=(
                PlanStep(
                    id="deploy",
                    kind=PlanStepKind.DEPLOY,
                    title="Deploy",
                    detail="",
                    commands=(),
                    deployments=(deployment,),
                ),
            )
            if deployment
            else (),
            blockers=(),
            warnings=(),
            plan_id=plan_id,
            workspace_id=workspace.id,
            workspace_revision=workspace.revision,
            config_fingerprint=plan_id,
            source_fingerprint=plan_id,
        )
    )


def _run(
    store: StateStore, plan: WorkspacePlanResponse, run_id: str, status: RunStatus
) -> RunRecord:
    assert plan.plan_id is not None
    run = store.create_run(
        RunRecord(
            id=run_id,
            workspace_id=plan.workspace_id,
            workspace_name="Integration",
            workspace_revision=plan.workspace_revision,
            plan_id=plan.plan_id,
            mode=plan.mode,
            status=RunStatus.QUEUED,
            current_step=None,
            config_fingerprint=plan.plan_id,
            source_fingerprint=plan.plan_id,
            retry_of=None,
            created_at=datetime.now(UTC),
            started_at=None,
            finished_at=None,
            failure_code=None,
            failure_detail=None,
        ),
        run_id,
    )
    if status is RunStatus.QUEUED:
        return run
    run = store.update_run(
        run.model_copy(update={"status": RunStatus.RUNNING, "started_at": datetime.now(UTC)}),
        expected_status=RunStatus.QUEUED,
    )
    if status is RunStatus.RUNNING:
        return run
    return store.update_run(
        run.model_copy(update={"status": status, "finished_at": datetime.now(UTC)}),
        expected_status=RunStatus.RUNNING,
    )


def _deployment(root: Path, workspace: WorkspaceRecord, revision_id: str) -> ComposeDeploymentPlan:
    return ComposeDeploymentPlan(
        revision_id=revision_id,
        workspace_id=workspace.id,
        project_id="api",
        workspace_revision=workspace.revision,
        source_fingerprint="a" * 64,
        target_config_fingerprint="b" * 64,
        checkout_path=str(root),
        frozen_compose_path=str(root / f"{revision_id}.json"),
        services=("api",),
        immutable_images=(f"tripguru.local/api:{revision_id}",),
        wait_timeout_seconds=10,
        probe=HttpDeploymentProbeSpec(url="http://127.0.0.1:18101/health", timeout_seconds=5),
        environment_spec=DeploymentEnvironmentSnapshot(),
    )


def _activate(store: StateStore, deployment: ComposeDeploymentPlan) -> None:
    now = datetime.now(UTC)
    revision = store.begin(
        DeploymentRevision(
            intent=DeploymentIntent(
                revision_id=deployment.revision_id,
                workspace_id=deployment.workspace_id,
                target_id=deployment.project_id,
                workspace_revision=deployment.workspace_revision,
                source_fingerprint=deployment.source_fingerprint,
                target_config_fingerprint=deployment.target_config_fingerprint,
                checkout_path=Path(deployment.checkout_path),
                frozen_compose_path=Path(deployment.frozen_compose_path),
                services=deployment.services,
                immutable_images=deployment.immutable_images,
                wait_timeout_seconds=deployment.wait_timeout_seconds,
                probe=HttpProbe("http://127.0.0.1:18101/health"),
                environment_spec=deployment.environment_spec,
            ),
            project_name="local-api",
            previous_revision_id=None,
            status=DeploymentStatus.PLANNED,
            created_at=now,
            updated_at=now,
        )
    )
    store.activate_revision(
        replace(revision, status=DeploymentStatus.ACTIVE), DeploymentStatus.PLANNED
    )


def test_compose_application_uses_matching_deployment_plan_not_newer_config_or_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    probed: list[str] = []

    def http_ready(self: LocalReadinessTransport, url: str, timeout_seconds: float) -> bool:
        del self, timeout_seconds
        probed.append(url)
        return url == "http://127.0.0.1:18101/old-app"

    monkeypatch.setattr(LocalReadinessTransport, "http_ready", http_ready)
    with StateStore(tmp_path / "state.db") as store:
        workspace = _workspace(
            store, WorkspaceService(project_id="api", execution_target=_target())
        )
        deployment = _deployment(tmp_path, workspace, "deployed")
        _activate(store, deployment)
        _run(
            store,
            _plan(store, workspace, "deployed-plan", deployment),
            "deployed-run",
            RunStatus.SUCCEEDED,
        )
        changed = store.update_workspace(
            workspace.id,
            WorkspaceUpdateRequest(
                name=workspace.name,
                mode=workspace.mode,
                expected_revision=workspace.revision,
                services=(
                    WorkspaceService(project_id="api", execution_target=_target(18102, "/new-app")),
                ),
            ),
        )
        _run(
            store,
            _plan(store, changed, "failed-plan", _deployment(tmp_path, changed, "failed")),
            "failed-run",
            RunStatus.FAILED,
        )
        inspector = RuntimeInspector(RuntimeTargetState(True, "deployed", True))
        observed = _observer(store, inspector).snapshot(changed)
        assert observed.services[0].url == "http://127.0.0.1:18101/old-app"
        assert observed.services[0].run_id == "deployed-run"
        assert observed.services[0].workspace_revision == workspace.revision
        assert observed.latest_run is not None and observed.latest_run.id == "failed-run"
        assert probed == ["http://127.0.0.1:18101/old-app"]


@pytest.mark.parametrize("present", [True, False])
def test_removed_compose_target_is_visible_only_while_runtime_remains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, present: bool
) -> None:
    def http_ready(self: LocalReadinessTransport, url: str, timeout_seconds: float) -> bool:
        del self, url, timeout_seconds
        return True

    monkeypatch.setattr(LocalReadinessTransport, "http_ready", http_ready)
    with StateStore(tmp_path / "state.db") as store:
        workspace = _workspace(
            store, WorkspaceService(project_id="api", execution_target=_target())
        )
        deployment = _deployment(tmp_path, workspace, "deployed")
        _activate(store, deployment)
        _run(
            store,
            _plan(store, workspace, "deployed-plan", deployment),
            "deployed-run",
            RunStatus.SUCCEEDED,
        )
        changed = store.update_workspace(
            workspace.id,
            WorkspaceUpdateRequest(
                name=workspace.name,
                mode=workspace.mode,
                expected_revision=workspace.revision,
                services=(WorkspaceService(project_id="replacement"),),
            ),
        )
        inspector = RuntimeInspector(
            RuntimeTargetState(present, "deployed" if present else None, present)
        )
        observed = _observer(store, inspector).snapshot(changed)
        old = [service for service in observed.services if service.project_id == "api"]
        assert bool(old) is present
        assert inspector.targets == ["api"]
        if present:
            assert not old[0].configured
            assert old[0].target == "compose" and old[0].status == "ready"
            assert old[0].run_id == "deployed-run"
        assert not observed.ready


def test_removed_compose_with_unknown_docker_state_keeps_correct_target_and_recovery(
    tmp_path: Path,
) -> None:
    with StateStore(tmp_path / "state.db") as store:
        workspace = _workspace(
            store, WorkspaceService(project_id="api", execution_target=_target())
        )
        _activate(store, _deployment(tmp_path, workspace, "deployed"))
        changed = store.update_workspace(
            workspace.id,
            WorkspaceUpdateRequest(
                name=workspace.name,
                mode=workspace.mode,
                expected_revision=workspace.revision,
                services=(WorkspaceService(project_id="replacement"),),
            ),
        )
        inspector = RuntimeInspector(RuntimeTargetState(False, None, False, "DOCKER_UNAVAILABLE"))
        observed = _observer(store, inspector).snapshot(changed)
        old = next(service for service in observed.services if service.project_id == "api")
        assert old.target == "compose" and old.status == "unknown" and not old.configured
        assert old.recovery is not None and "资源页" in old.recovery


def test_new_service_not_in_active_plan_is_stopped_instead_of_starting(tmp_path: Path) -> None:
    with StateStore(tmp_path / "state.db") as store:
        workspace = _workspace(
            store, WorkspaceService(project_id="original", execution_target=HostTarget())
        )
        _run(store, _plan(store, workspace, "active-plan"), "active-run", RunStatus.RUNNING)
        changed = store.update_workspace(
            workspace.id,
            WorkspaceUpdateRequest(
                name=workspace.name,
                mode=workspace.mode,
                expected_revision=workspace.revision,
                services=(*workspace.services, WorkspaceService(project_id="new")),
            ),
        )
        inspector = RuntimeInspector(RuntimeTargetState(False, None, False))
        observed = _observer(store, inspector).snapshot(changed)
        assert {service.project_id: service.status for service in observed.services} == {
            "original": "starting",
            "new": "stopped",
        }
        assert inspector.targets == []
