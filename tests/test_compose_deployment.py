from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tripguru_local.compose_deployment import (
    TARGET_EXCLUSIVE_DEPLOYMENT_STATUSES,
    TERMINAL_DEPLOYMENT_STATUSES,
    ComposeDeploymentWorkflow,
    DeploymentAction,
    DeploymentCancellation,
    DeploymentCommandResult,
    DeploymentIntent,
    DeploymentProbe,
    DeploymentProbeResult,
    DeploymentRevision,
    DeploymentRevisionNotFoundError,
    DeploymentStatus,
    HttpProbe,
    ResolvedDeploymentVariable,
    RuntimeTargetState,
    TargetDeploymentConflictError,
    TcpProbe,
    derive_compose_project_name,
)
from tripguru_local.contracts import DeploymentEnvironmentSnapshot


class FakeStore:
    def __init__(self, journal: list[str]) -> None:
        self.journal = journal
        self.records: dict[str, DeploymentRevision] = {}
        self.status_history: dict[str, list[DeploymentStatus]] = {}

    def seed(self, record: DeploymentRevision) -> None:
        self.records[record.intent.revision_id] = record
        self.status_history[record.intent.revision_id] = [record.status]

    def begin(self, revision: DeploymentRevision) -> DeploymentRevision:
        conflict = next(
            (
                current
                for current in self.records.values()
                if self._same_target(current, revision)
                and current.status in TARGET_EXCLUSIVE_DEPLOYMENT_STATUSES
            ),
            None,
        )
        if conflict is not None:
            raise TargetDeploymentConflictError()
        previous = next(
            (
                current
                for current in self.records.values()
                if self._same_target(current, revision)
                and current.status is DeploymentStatus.ACTIVE
            ),
            None,
        )
        created = replace(
            revision,
            previous_revision_id=(previous.intent.revision_id if previous is not None else None),
        )
        self.records[created.intent.revision_id] = created
        self.status_history[created.intent.revision_id] = [created.status]
        self.journal.append(f"store:{created.status.value}:{created.intent.revision_id}")
        return created

    def get_revision(self, revision_id: str) -> DeploymentRevision | None:
        return self.records.get(revision_id)

    def list_deployment_revisions(
        self,
        workspace_id: str | None = None,
        target_id: str | None = None,
    ) -> tuple[DeploymentRevision, ...]:
        return tuple(
            revision
            for revision in reversed(tuple(self.records.values()))
            if (workspace_id is None or revision.intent.workspace_id == workspace_id)
            and (target_id is None or revision.intent.target_id == target_id)
        )

    def update_revision(
        self,
        revision: DeploymentRevision,
        expected_status: DeploymentStatus,
    ) -> DeploymentRevision:
        current = self.records[revision.intent.revision_id]
        assert current.status is expected_status
        self.records[revision.intent.revision_id] = revision
        self.status_history[revision.intent.revision_id].append(revision.status)
        self.journal.append(f"store:{revision.status.value}:{revision.intent.revision_id}")
        return revision

    def activate_revision(
        self,
        revision: DeploymentRevision,
        expected_status: DeploymentStatus,
    ) -> DeploymentRevision:
        current = self.records[revision.intent.revision_id]
        assert current.status is expected_status
        for candidate in tuple(self.records.values()):
            if (
                candidate.intent.revision_id != revision.intent.revision_id
                and self._same_target(candidate, revision)
                and candidate.status is DeploymentStatus.ACTIVE
            ):
                superseded = replace(
                    candidate,
                    status=DeploymentStatus.SUPERSEDED,
                    updated_at=revision.updated_at,
                )
                self.records[candidate.intent.revision_id] = superseded
                self.status_history[candidate.intent.revision_id].append(
                    DeploymentStatus.SUPERSEDED
                )
        self.records[revision.intent.revision_id] = revision
        self.status_history[revision.intent.revision_id].append(revision.status)
        self.journal.append(f"store:activate:{revision.intent.revision_id}")
        return revision

    def list_pending_revisions(self) -> tuple[DeploymentRevision, ...]:
        return tuple(
            record
            for record in self.records.values()
            if record.status in TARGET_EXCLUSIVE_DEPLOYMENT_STATUSES
        )

    @staticmethod
    def _same_target(first: DeploymentRevision, second: DeploymentRevision) -> bool:
        return (
            first.intent.workspace_id == second.intent.workspace_id
            and first.intent.target_id == second.intent.target_id
        )


@dataclass(frozen=True, slots=True)
class RunnerCall:
    argv: tuple[str, ...]
    cwd: Path
    environment: tuple[ResolvedDeploymentVariable, ...]


class FakeRunner:
    def __init__(
        self,
        journal: list[str],
        *results: DeploymentCommandResult,
    ) -> None:
        self.journal = journal
        self.results = deque(results)
        self.calls: list[RunnerCall] = []

    def run(
        self,
        *,
        argv: tuple[str, ...],
        cwd: Path,
        environment: tuple[ResolvedDeploymentVariable, ...],
        cancellation: DeploymentCancellation | None = None,
        timeout_seconds: float | None = None,
    ) -> DeploymentCommandResult:
        del cancellation, timeout_seconds
        self.calls.append(RunnerCall(argv=argv, cwd=cwd, environment=environment))
        self.journal.append(f"docker:{' '.join(argv)}")
        if self.results:
            return self.results.popleft()
        return DeploymentCommandResult(return_code=0)


class FakeEnvironmentResolver:
    def __init__(
        self,
        journal: list[str],
        variables: tuple[ResolvedDeploymentVariable, ...] = (),
    ) -> None:
        self.journal = journal
        self.variables = variables
        self.actions: list[DeploymentAction] = []

    def resolve(
        self,
        revision: DeploymentRevision,
        action: DeploymentAction,
    ) -> tuple[ResolvedDeploymentVariable, ...]:
        del revision
        self.actions.append(action)
        self.journal.append(f"resolve:{action.value}")
        return self.variables


class FakeProbeRunner:
    def __init__(
        self,
        journal: list[str],
        *results: DeploymentProbeResult,
    ) -> None:
        self.journal = journal
        self.results = deque(results)
        self.calls: list[DeploymentProbe] = []

    def verify(
        self,
        probe: DeploymentProbe,
        environment: tuple[ResolvedDeploymentVariable, ...],
    ) -> DeploymentProbeResult:
        del environment
        self.calls.append(probe)
        self.journal.append("probe:verify")
        if self.results:
            return self.results.popleft()
        return DeploymentProbeResult(ready=True)


class FakeRuntimeInspector:
    def __init__(self, state: RuntimeTargetState) -> None:
        self.state = state
        self.calls: list[tuple[str, str]] = []

    def inspect(self, workspace_id: str, target_id: str) -> RuntimeTargetState:
        self.calls.append((workspace_id, target_id))
        return self.state


class CancelAfterRunnerCalls:
    def __init__(self, runner: FakeRunner, call_count: int) -> None:
        self.runner = runner
        self.call_count = call_count

    def is_cancelled(self) -> bool:
        return len(self.runner.calls) >= self.call_count


def deployment_intent(
    tmp_path: Path,
    revision_id: str,
    *,
    frozen_name: str | None = None,
    services: tuple[str, ...] = ("api", "worker"),
    wait_timeout_seconds: int = 45,
) -> DeploymentIntent:
    return DeploymentIntent(
        revision_id=revision_id,
        workspace_id="workspace-supplier",
        target_id="supplier-stack",
        workspace_revision=7,
        source_fingerprint=f"source-{revision_id}",
        target_config_fingerprint=f"target-{revision_id}",
        checkout_path=tmp_path,
        frozen_compose_path=tmp_path / (frozen_name or f"{revision_id}.json"),
        services=services,
        immutable_images=tuple(f"tripguru.local/{service}:{revision_id}" for service in services),
        wait_timeout_seconds=wait_timeout_seconds,
        probe=HttpProbe(url="http://127.0.0.1:18000/ready", timeout_seconds=3),
        environment_spec=DeploymentEnvironmentSnapshot(),
    )


def revision(
    intent: DeploymentIntent,
    status: DeploymentStatus,
    *,
    previous_revision_id: str | None = None,
) -> DeploymentRevision:
    now = datetime.now(UTC)
    return DeploymentRevision(
        intent=intent,
        project_name=derive_compose_project_name(intent.workspace_id, intent.target_id),
        previous_revision_id=previous_revision_id,
        status=status,
        created_at=now,
        updated_at=now,
    )


def workflow(
    store: FakeStore,
    runner: FakeRunner,
    resolver: FakeEnvironmentResolver,
    probe: FakeProbeRunner,
    inspector: FakeRuntimeInspector | None = None,
) -> ComposeDeploymentWorkflow:
    return ComposeDeploymentWorkflow(
        store,
        runner,
        resolver,
        probe,
        inspector
        or FakeRuntimeInspector(
            RuntimeTargetState(target_present=False, revision_id=None, probe_ready=False)
        ),
    )


def test_persists_intent_before_exact_build_apply_and_explicit_probe(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    runner = FakeRunner(journal)
    resolver = FakeEnvironmentResolver(journal)
    probe = FakeProbeRunner(journal)
    intent = deployment_intent(tmp_path, "revision-new")

    result = workflow(store, runner, resolver, probe).deploy(intent)

    project = derive_compose_project_name(intent.workspace_id, intent.target_id)
    assert result.status is DeploymentStatus.ACTIVE
    assert runner.calls[0].argv == (
        "docker",
        "compose",
        "-f",
        str(intent.frozen_compose_path),
        "-p",
        project,
        "build",
        "api",
        "worker",
    )
    assert runner.calls[1].argv == (
        "docker",
        "compose",
        "-f",
        str(intent.frozen_compose_path),
        "-p",
        project,
        "up",
        "-d",
        "--no-deps",
        "--no-build",
        "--pull",
        "never",
        "--force-recreate",
        "--wait",
        "--wait-timeout",
        "45",
        "api",
        "worker",
    )
    assert journal.index("store:planned:revision-new") < next(
        index for index, entry in enumerate(journal) if entry.startswith("docker:")
    )
    assert resolver.actions == [
        DeploymentAction.BUILD,
        DeploymentAction.APPLY,
        DeploymentAction.VERIFY,
    ]
    assert probe.calls == [intent.probe]
    assert store.status_history[intent.revision_id] == [
        DeploymentStatus.PLANNED,
        DeploymentStatus.BUILDING,
        DeploymentStatus.APPLYING,
        DeploymentStatus.VERIFYING,
        DeploymentStatus.ACTIVE,
    ]


def test_compose_wait_does_not_replace_explicit_tcp_probe(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    runner = FakeRunner(journal)
    probe = FakeProbeRunner(journal)
    base = deployment_intent(tmp_path, "revision-tcp")
    intent = replace(base, probe=TcpProbe(host="127.0.0.1", port=18000))

    result = workflow(
        store,
        runner,
        FakeEnvironmentResolver(journal),
        probe,
    ).deploy(intent)

    assert result.status is DeploymentStatus.ACTIVE
    assert "--wait" in runner.calls[1].argv
    assert probe.calls == [intent.probe]


def test_build_failure_leaves_previous_active_untouched(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    previous = revision(deployment_intent(tmp_path, "revision-old"), DeploymentStatus.ACTIVE)
    store.seed(previous)
    runner = FakeRunner(
        journal,
        DeploymentCommandResult(return_code=1, stderr="image build failed"),
    )
    resolver = FakeEnvironmentResolver(journal)

    result = workflow(store, runner, resolver, FakeProbeRunner(journal)).deploy(
        deployment_intent(tmp_path, "revision-new")
    )

    assert result.status is DeploymentStatus.FAILED
    assert result.failure_code == "DEPLOYMENT_BUILD_FAILED"
    assert len(runner.calls) == 1
    assert store.records[previous.intent.revision_id].status is DeploymentStatus.ACTIVE
    assert DeploymentStatus.RECOVERING not in store.status_history[result.intent.revision_id]


def test_verify_failure_restores_previous_frozen_config_and_image(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    previous_intent = deployment_intent(
        tmp_path,
        "revision-old",
        frozen_name="previous-frozen.json",
        services=("api",),
        wait_timeout_seconds=21,
    )
    previous = revision(previous_intent, DeploymentStatus.ACTIVE)
    store.seed(previous)
    runner = FakeRunner(journal)
    resolver = FakeEnvironmentResolver(journal)
    probe = FakeProbeRunner(
        journal,
        DeploymentProbeResult(ready=False, detail="new revision not ready"),
        DeploymentProbeResult(ready=True),
    )

    result = workflow(store, runner, resolver, probe).deploy(
        deployment_intent(tmp_path, "revision-new")
    )

    assert result.status is DeploymentStatus.ROLLED_BACK
    assert store.status_history[result.intent.revision_id][-2:] == [
        DeploymentStatus.RECOVERING,
        DeploymentStatus.ROLLED_BACK,
    ]
    assert runner.calls[2].argv == (
        "docker",
        "compose",
        "-f",
        str(previous_intent.frozen_compose_path),
        "-p",
        previous.project_name,
        "up",
        "-d",
        "--no-deps",
        "--no-build",
        "--pull",
        "never",
        "--force-recreate",
        "--wait",
        "--wait-timeout",
        "21",
        "api",
    )
    assert store.records[previous.intent.revision_id].intent.immutable_images == (
        "tripguru.local/api:revision-old",
    )
    assert resolver.actions[-2:] == [DeploymentAction.ROLLBACK, DeploymentAction.VERIFY]


def test_apply_and_rollback_failure_enters_degraded(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    previous = revision(deployment_intent(tmp_path, "revision-old"), DeploymentStatus.ACTIVE)
    store.seed(previous)
    runner = FakeRunner(
        journal,
        DeploymentCommandResult(return_code=0),
        DeploymentCommandResult(return_code=1, stderr="apply failed"),
        DeploymentCommandResult(return_code=1, stderr="rollback failed"),
    )

    result = workflow(
        store,
        runner,
        FakeEnvironmentResolver(journal),
        FakeProbeRunner(journal),
    ).deploy(deployment_intent(tmp_path, "revision-new"))

    assert result.status is DeploymentStatus.DEGRADED
    assert result.failure_code == "DEPLOYMENT_APPLY_FAILED"
    assert result.recovery_detail == "rollback failed"
    assert store.status_history[result.intent.revision_id][-2:] == [
        DeploymentStatus.RECOVERING,
        DeploymentStatus.DEGRADED,
    ]


def test_cancel_after_apply_recovers_previous_revision(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    previous = revision(deployment_intent(tmp_path, "revision-old"), DeploymentStatus.ACTIVE)
    store.seed(previous)
    runner = FakeRunner(journal)
    cancellation = CancelAfterRunnerCalls(runner, 2)
    probe = FakeProbeRunner(journal, DeploymentProbeResult(ready=True))

    result = workflow(
        store,
        runner,
        FakeEnvironmentResolver(journal),
        probe,
    ).deploy(deployment_intent(tmp_path, "revision-new"), cancellation)

    assert result.status is DeploymentStatus.ROLLED_BACK
    assert result.failure_code == "DEPLOYMENT_CANCELLED"
    assert len(runner.calls) == 3
    assert probe.calls == [previous.intent.probe]
    assert store.status_history[result.intent.revision_id][-2:] == [
        DeploymentStatus.RECOVERING,
        DeploymentStatus.ROLLED_BACK,
    ]


def test_cancel_during_apply_terminates_command_then_recovers_previous_revision(
    tmp_path: Path,
) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    previous = revision(deployment_intent(tmp_path, "revision-old"), DeploymentStatus.ACTIVE)
    store.seed(previous)
    runner = FakeRunner(
        journal,
        DeploymentCommandResult(return_code=0),
        DeploymentCommandResult(return_code=1, cancelled=True),
        DeploymentCommandResult(return_code=0),
    )
    probe = FakeProbeRunner(journal, DeploymentProbeResult(ready=True))

    result = workflow(
        store,
        runner,
        FakeEnvironmentResolver(journal),
        probe,
    ).deploy(deployment_intent(tmp_path, "revision-new"))

    assert result.status is DeploymentStatus.ROLLED_BACK
    assert result.failure_code == "DEPLOYMENT_CANCELLED"
    assert len(runner.calls) == 3
    assert probe.calls == [previous.intent.probe]


def test_build_timeout_stops_before_apply_without_replacing_previous_revision(
    tmp_path: Path,
) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    previous = revision(deployment_intent(tmp_path, "revision-old"), DeploymentStatus.ACTIVE)
    store.seed(previous)
    runner = FakeRunner(
        journal,
        DeploymentCommandResult(return_code=1, stderr="build timeout", timed_out=True),
    )

    result = workflow(
        store,
        runner,
        FakeEnvironmentResolver(journal),
        FakeProbeRunner(journal),
    ).deploy(deployment_intent(tmp_path, "revision-new"))

    assert result.status is DeploymentStatus.FAILED
    assert result.failure_code == "DEPLOYMENT_BUILD_TIMED_OUT"
    assert result.failure_detail == "build timeout"
    assert len(runner.calls) == 1
    assert store.records[previous.intent.revision_id].status is DeploymentStatus.ACTIVE


def test_cancel_during_build_stops_before_apply(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    runner = FakeRunner(
        journal,
        DeploymentCommandResult(return_code=1, cancelled=True),
    )

    result = workflow(
        store,
        runner,
        FakeEnvironmentResolver(journal),
        FakeProbeRunner(journal),
    ).deploy(deployment_intent(tmp_path, "revision-new"))

    assert result.status is DeploymentStatus.FAILED
    assert result.failure_code == "DEPLOYMENT_CANCELLED"
    assert len(runner.calls) == 1


def test_apply_timeout_enters_recovery_before_reporting_rolled_back(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    previous = revision(deployment_intent(tmp_path, "revision-old"), DeploymentStatus.ACTIVE)
    store.seed(previous)
    runner = FakeRunner(
        journal,
        DeploymentCommandResult(return_code=0),
        DeploymentCommandResult(return_code=1, stderr="apply timeout", timed_out=True),
        DeploymentCommandResult(return_code=0),
    )

    result = workflow(
        store,
        runner,
        FakeEnvironmentResolver(journal),
        FakeProbeRunner(journal, DeploymentProbeResult(ready=True)),
    ).deploy(deployment_intent(tmp_path, "revision-new"))

    assert result.status is DeploymentStatus.ROLLED_BACK
    assert result.failure_code == "DEPLOYMENT_APPLY_TIMED_OUT"
    assert result.failure_detail == "apply timeout"
    assert store.status_history[result.intent.revision_id][-2:] == [
        DeploymentStatus.RECOVERING,
        DeploymentStatus.ROLLED_BACK,
    ]


def test_first_deployment_failure_rolls_back_to_absent_without_volumes(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    runner = FakeRunner(journal)
    probe = FakeProbeRunner(
        journal,
        DeploymentProbeResult(ready=False, detail="not ready"),
    )
    intent = deployment_intent(tmp_path, "revision-first")

    result = workflow(
        store,
        runner,
        FakeEnvironmentResolver(journal),
        probe,
    ).deploy(intent)

    assert result.status is DeploymentStatus.ROLLED_BACK
    assert runner.calls[-1].argv == (
        "docker",
        "compose",
        "-f",
        str(intent.frozen_compose_path),
        "-p",
        derive_compose_project_name(intent.workspace_id, intent.target_id),
        "down",
    )
    assert "-v" not in runner.calls[-1].argv


def test_activation_atomically_supersedes_previous_active(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    previous = revision(deployment_intent(tmp_path, "revision-old"), DeploymentStatus.ACTIVE)
    store.seed(previous)

    result = workflow(
        store,
        FakeRunner(journal),
        FakeEnvironmentResolver(journal),
        FakeProbeRunner(journal),
    ).deploy(deployment_intent(tmp_path, "revision-new"))

    assert result.status is DeploymentStatus.ACTIVE
    assert store.records[previous.intent.revision_id].status is DeploymentStatus.SUPERSEDED
    assert store.status_history[previous.intent.revision_id][-1] is DeploymentStatus.SUPERSEDED
    assert DeploymentStatus.SUPERSEDED in TERMINAL_DEPLOYMENT_STATUSES
    assert journal[-1] == "store:activate:revision-new"


def test_reconcile_building_interruption_fails_without_runtime_mutation(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    pending = revision(deployment_intent(tmp_path, "revision-new"), DeploymentStatus.BUILDING)
    store.seed(pending)
    inspector = FakeRuntimeInspector(
        RuntimeTargetState(
            target_present=True,
            revision_id=pending.intent.revision_id,
            probe_ready=True,
        )
    )
    runner = FakeRunner(journal)

    results = workflow(
        store,
        runner,
        FakeEnvironmentResolver(journal),
        FakeProbeRunner(journal),
        inspector,
    ).reconcile_pending()

    assert results[0].status is DeploymentStatus.FAILED
    assert results[0].failure_code == "DEPLOYMENT_BUILD_INTERRUPTED"
    assert inspector.calls == []
    assert runner.calls == []


def test_reconcile_planned_interruption_releases_target_without_docker(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    pending = revision(deployment_intent(tmp_path, "revision-new"), DeploymentStatus.PLANNED)
    store.seed(pending)
    runner = FakeRunner(journal)

    result = workflow(
        store,
        runner,
        FakeEnvironmentResolver(journal),
        FakeProbeRunner(journal),
    ).reconcile_pending()[0]

    assert result.status is DeploymentStatus.FAILED
    assert result.failure_code == "DEPLOYMENT_PLANNED_INTERRUPTED"
    assert runner.calls == []


def test_reconcile_applying_ready_revision_activates_and_supersedes(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    previous = revision(deployment_intent(tmp_path, "revision-old"), DeploymentStatus.ACTIVE)
    pending = revision(
        deployment_intent(tmp_path, "revision-new"),
        DeploymentStatus.APPLYING,
        previous_revision_id=previous.intent.revision_id,
    )
    store.seed(previous)
    store.seed(pending)

    results = workflow(
        store,
        FakeRunner(journal),
        FakeEnvironmentResolver(journal),
        FakeProbeRunner(journal),
        FakeRuntimeInspector(
            RuntimeTargetState(
                target_present=True,
                revision_id=pending.intent.revision_id,
                probe_ready=True,
            )
        ),
    ).reconcile_pending()

    assert results[0].status is DeploymentStatus.ACTIVE
    assert store.records[previous.intent.revision_id].status is DeploymentStatus.SUPERSEDED


def test_reconcile_verifying_previous_ready_marks_rolled_back(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    previous = revision(deployment_intent(tmp_path, "revision-old"), DeploymentStatus.ACTIVE)
    pending = revision(
        deployment_intent(tmp_path, "revision-new"),
        DeploymentStatus.VERIFYING,
        previous_revision_id=previous.intent.revision_id,
    )
    store.seed(previous)
    store.seed(pending)

    result = workflow(
        store,
        FakeRunner(journal),
        FakeEnvironmentResolver(journal),
        FakeProbeRunner(journal),
        FakeRuntimeInspector(
            RuntimeTargetState(
                target_present=True,
                revision_id=previous.intent.revision_id,
                probe_ready=True,
            )
        ),
    ).reconcile_pending()[0]

    assert result.status is DeploymentStatus.ROLLED_BACK
    assert store.status_history[pending.intent.revision_id][-2:] == [
        DeploymentStatus.RECOVERING,
        DeploymentStatus.ROLLED_BACK,
    ]


def test_reconcile_recovering_current_ready_continues_previous_rollback(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    previous = revision(deployment_intent(tmp_path, "revision-old"), DeploymentStatus.ACTIVE)
    pending = revision(
        deployment_intent(tmp_path, "revision-new"),
        DeploymentStatus.RECOVERING,
        previous_revision_id=previous.intent.revision_id,
    )
    store.seed(previous)
    store.seed(pending)

    runner = FakeRunner(journal)
    result = workflow(
        store,
        runner,
        FakeEnvironmentResolver(journal),
        FakeProbeRunner(journal),
        FakeRuntimeInspector(
            RuntimeTargetState(
                target_present=True,
                revision_id=pending.intent.revision_id,
                probe_ready=True,
            )
        ),
    ).reconcile_pending()[0]

    assert result.status is DeploymentStatus.ROLLED_BACK
    assert store.status_history[pending.intent.revision_id][-1] is DeploymentStatus.ROLLED_BACK
    assert len(runner.calls) == 1
    assert runner.calls[0].argv[3] == str(previous.intent.frozen_compose_path)


def test_reconcile_unknown_revision_or_unready_runtime_degrades(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    pending = revision(deployment_intent(tmp_path, "revision-new"), DeploymentStatus.VERIFYING)
    store.seed(pending)

    runner = FakeRunner(
        journal,
        DeploymentCommandResult(return_code=1, stderr="down during reconcile failed"),
    )
    result = workflow(
        store,
        runner,
        FakeEnvironmentResolver(journal),
        FakeProbeRunner(journal),
        FakeRuntimeInspector(
            RuntimeTargetState(
                target_present=True,
                revision_id="foreign-revision",
                probe_ready=False,
            )
        ),
    ).reconcile_pending()[0]

    assert result.status is DeploymentStatus.DEGRADED
    assert result.recovery_detail == "down during reconcile failed"
    assert runner.calls[0].argv[-1] == "down"


def test_reconcile_first_deployment_unknown_runtime_executes_down(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    pending = revision(deployment_intent(tmp_path, "revision-new"), DeploymentStatus.VERIFYING)
    store.seed(pending)
    runner = FakeRunner(journal)

    result = workflow(
        store,
        runner,
        FakeEnvironmentResolver(journal),
        FakeProbeRunner(journal),
        FakeRuntimeInspector(
            RuntimeTargetState(
                target_present=True,
                revision_id=pending.intent.revision_id,
                probe_ready=False,
            )
        ),
    ).reconcile_pending()[0]

    assert result.status is DeploymentStatus.ROLLED_BACK
    assert runner.calls[0].argv[-1] == "down"
    assert "-v" not in runner.calls[0].argv


def test_reconcile_revision_not_found_has_stable_error(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)

    with pytest.raises(
        DeploymentRevisionNotFoundError,
        match="部署 revision 不存在",
    ) as captured:
        workflow(
            store,
            FakeRunner(journal),
            FakeEnvironmentResolver(journal),
            FakeProbeRunner(journal),
        ).reconcile_revision("missing-revision")

    assert captured.value.code == "DEPLOYMENT_REVISION_NOT_FOUND"
    assert journal == []


def test_reconcile_revision_reuses_pending_runtime_reconcile(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    previous = revision(deployment_intent(tmp_path, "revision-old"), DeploymentStatus.ACTIVE)
    pending = revision(
        deployment_intent(tmp_path, "revision-new"),
        DeploymentStatus.APPLYING,
        previous_revision_id=previous.intent.revision_id,
    )
    store.seed(previous)
    store.seed(pending)
    inspector = FakeRuntimeInspector(
        RuntimeTargetState(
            target_present=True,
            revision_id=pending.intent.revision_id,
            probe_ready=True,
        )
    )

    result = workflow(
        store,
        FakeRunner(journal),
        FakeEnvironmentResolver(journal),
        FakeProbeRunner(journal),
        inspector,
    ).reconcile_revision(pending.intent.revision_id)

    assert result.status is DeploymentStatus.ACTIVE
    assert store.records[previous.intent.revision_id].status is DeploymentStatus.SUPERSEDED
    assert inspector.calls == [(pending.intent.workspace_id, pending.intent.target_id)]


def test_reconcile_degraded_revision_retries_previous_recovery(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    previous = revision(deployment_intent(tmp_path, "revision-old"), DeploymentStatus.ACTIVE)
    degraded = revision(
        deployment_intent(tmp_path, "revision-new"),
        DeploymentStatus.DEGRADED,
        previous_revision_id=previous.intent.revision_id,
    )
    store.seed(previous)
    store.seed(degraded)
    runner = FakeRunner(journal)
    probe = FakeProbeRunner(journal, DeploymentProbeResult(ready=True))

    result = workflow(
        store,
        runner,
        FakeEnvironmentResolver(journal),
        probe,
    ).reconcile_revision(degraded.intent.revision_id)

    assert result.status is DeploymentStatus.ROLLED_BACK
    assert store.status_history[degraded.intent.revision_id] == [
        DeploymentStatus.DEGRADED,
        DeploymentStatus.RECOVERING,
        DeploymentStatus.ROLLED_BACK,
    ]
    assert runner.calls[0].argv[3] == str(previous.intent.frozen_compose_path)
    assert probe.calls == [previous.intent.probe]


@pytest.mark.parametrize(
    "status",
    [
        DeploymentStatus.ACTIVE,
        DeploymentStatus.SUPERSEDED,
        DeploymentStatus.FAILED,
        DeploymentStatus.ROLLED_BACK,
    ],
)
def test_reconcile_stable_revision_is_idempotent(
    tmp_path: Path,
    status: DeploymentStatus,
) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    record = revision(deployment_intent(tmp_path, f"revision-{status.value}"), status)
    store.seed(record)
    runner = FakeRunner(journal)
    resolver = FakeEnvironmentResolver(journal)
    probe = FakeProbeRunner(journal)
    inspector = FakeRuntimeInspector(
        RuntimeTargetState(target_present=False, revision_id=None, probe_ready=False)
    )

    result = workflow(store, runner, resolver, probe, inspector).reconcile_revision(
        record.intent.revision_id
    )

    assert result == record
    assert store.status_history[record.intent.revision_id] == [status]
    assert runner.calls == []
    assert resolver.actions == []
    assert probe.calls == []
    assert inspector.calls == []


def test_store_begin_exclusively_rejects_same_target_pending_revision(tmp_path: Path) -> None:
    journal: list[str] = []
    store = FakeStore(journal)
    store.seed(revision(deployment_intent(tmp_path, "revision-one"), DeploymentStatus.PLANNED))
    runner = FakeRunner(journal)

    with pytest.raises(TargetDeploymentConflictError, match="已有未完成"):
        workflow(
            store,
            runner,
            FakeEnvironmentResolver(journal),
            FakeProbeRunner(journal),
        ).deploy(deployment_intent(tmp_path, "revision-two"))

    assert runner.calls == []
    assert "revision-two" not in store.records


def test_resolved_environment_never_enters_record_error_or_log(tmp_path: Path) -> None:
    journal: list[str] = []
    raw_secret = "p@ss word/value"
    encoded_secret = "p%40ss%20word%2Fvalue"
    connection = f"postgresql://local:{encoded_secret}@127.0.0.1/app"
    variables = (
        ResolvedDeploymentVariable(
            name="SUPPLIER_DATABASE_URL",
            value=connection,
            sensitive=True,
            redaction_values=(raw_secret, encoded_secret),
        ),
    )
    store = FakeStore(journal)
    runner = FakeRunner(
        journal,
        DeploymentCommandResult(
            return_code=1,
            stderr=f"failed {raw_secret} / {encoded_secret} / {connection}",
        ),
    )

    result = workflow(
        store,
        runner,
        FakeEnvironmentResolver(journal, variables),
        FakeProbeRunner(journal),
    ).deploy(deployment_intent(tmp_path, "revision-secret"))

    persisted_and_logged = "\n".join((repr(tuple(store.records.values())), *journal))
    for secret in (raw_secret, encoded_secret, connection):
        assert secret not in persisted_and_logged
        assert secret not in (result.failure_detail or "")
    assert result.failure_detail == "failed *** / *** / ***"
    assert runner.calls[0].environment == variables
