from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from pipedeck.compose_deployment import (
    DeploymentProbe,
    DeploymentProbeResult,
    DeploymentStatus,
    TcpProbe,
)
from pipedeck.contracts import (
    ComposeDeploymentPlan,
    DeploymentEnvironmentSnapshot,
    PlanCommand,
    PlanStep,
    PlanStepKind,
    RunEvent,
    RunEventKind,
    RunMode,
    RunRecord,
    RunStatus,
    TcpDeploymentProbeSpec,
    WorkspacePlanResponse,
)
from pipedeck.execution import (
    DeploymentExecutionResult,
    DeploymentExecutor,
    ExecutionEngine,
    ExecutionStore,
    FreshnessResult,
    LoadedExecutionPlan,
    ResolvedEnvironmentVariable,
)
from pipedeck.state_store import StateStore


class FakeStore:
    def __init__(self) -> None:
        self.runs: dict[str, RunRecord] = {}
        self.events: list[RunEvent] = []
        self.keys: dict[str, str] = {}
        self._lock = Lock()

    def create_run(self, run: RunRecord, idempotency_key: str) -> RunRecord:
        with self._lock:
            existing_id = self.keys.get(idempotency_key)
            if existing_id is not None:
                return self.runs[existing_id]
            self.runs[run.id] = run
            self.keys[idempotency_key] = run.id
            return run

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._lock:
            return self.runs.get(run_id)

    def update_run(
        self,
        run: RunRecord,
        expected_status: RunStatus | None = None,
    ) -> RunRecord:
        with self._lock:
            current = self.runs[run.id]
            if expected_status is not None:
                assert current.status is expected_status
            self.runs[run.id] = run
            return run

    def append_event(
        self,
        *,
        run_id: str,
        kind: RunEventKind,
        message: str,
        step_id: str | None = None,
        project_id: str | None = None,
    ) -> RunEvent:
        with self._lock:
            event = RunEvent(
                sequence=len(self.events) + 1,
                run_id=run_id,
                created_at=datetime.now(UTC),
                kind=kind,
                step_id=step_id,
                project_id=project_id,
                message=message,
            )
            self.events.append(event)
            return event


class FakePlanLoader:
    def __init__(self, loaded: LoadedExecutionPlan) -> None:
        self.loaded = loaded

    def load(self, plan_id: str) -> LoadedExecutionPlan | None:
        if self.loaded.plan.plan_id == plan_id:
            return self.loaded
        return None


class FakeEnvironmentResolver:
    def __init__(self, *variables: ResolvedEnvironmentVariable) -> None:
        self.variables = variables

    def resolve(
        self,
        plan: WorkspacePlanResponse,
        command: PlanCommand,
    ) -> tuple[ResolvedEnvironmentVariable, ...]:
        del plan, command
        return self.variables


class FakeFreshnessValidator:
    def __init__(self, result: FreshnessResult) -> None:
        self.result = result

    def validate(self, plan: WorkspacePlanResponse) -> FreshnessResult:
        del plan
        return self.result


class FakeRunObserver:
    def __init__(self) -> None:
        self.notifications: list[tuple[RunRecord, WorkspacePlanResponse]] = []

    def on_started(self, run: RunRecord, plan: WorkspacePlanResponse) -> None:
        self.notifications.append((run, plan))


class FakeReadinessResolver:
    def __init__(self, probe: DeploymentProbe | None) -> None:
        self.probe = probe

    def resolve(
        self,
        plan: WorkspacePlanResponse,
        command: PlanCommand,
    ) -> DeploymentProbe | None:
        del plan, command
        return self.probe


class FakeReadinessWaiter:
    def __init__(self, result: DeploymentProbeResult) -> None:
        self.result = result
        self.calls: list[DeploymentProbe] = []

    def wait(
        self,
        probe: DeploymentProbe,
        *,
        cancelled: Callable[[], bool],
        target_alive: Callable[[], bool],
    ) -> DeploymentProbeResult:
        assert cancelled() is False
        assert target_alive() is True
        self.calls.append(probe)
        return self.result


class BlockingDeploymentExecutor:
    def __init__(self, final_status: DeploymentStatus) -> None:
        self.final_status = final_status
        self.started = threading.Event()
        self.recovery_started = threading.Event()
        self.release_recovery = threading.Event()

    def execute(
        self,
        deployment: ComposeDeploymentPlan,
        cancelled: Callable[[], bool],
    ) -> DeploymentExecutionResult:
        del deployment
        self.started.set()
        while not cancelled():
            time.sleep(0.01)
        self.recovery_started.set()
        if not self.release_recovery.wait(timeout=5):
            raise TimeoutError
        return DeploymentExecutionResult(
            False,
            "DEPLOYMENT_CANCELLED",
            f"cancel settled as {self.final_status.value}",
            self.final_status,
        )


TERMINAL_STATUSES = {
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.INTERRUPTED,
}

DEFAULT_FRESHNESS = FreshnessResult(valid=True)


def make_plan(
    tmp_path: Path,
    *commands: PlanCommand,
    deployments: tuple[ComposeDeploymentPlan, ...] = (),
) -> LoadedExecutionPlan:
    return LoadedExecutionPlan(
        workspace_name="Local integration",
        plan=WorkspacePlanResponse(
            generated_at=datetime.now(UTC),
            ready=True,
            mode=RunMode.INTEGRATED,
            projects=(),
            steps=(
                PlanStep(
                    id="start",
                    kind=PlanStepKind.START,
                    title="启动服务",
                    detail="test",
                    commands=commands,
                    deployments=deployments,
                ),
            ),
            blockers=(),
            warnings=(),
            plan_id="plan-1",
            workspace_id="workspace-1",
            workspace_revision=2,
            config_fingerprint="config-fingerprint",
            source_fingerprint="source-fingerprint",
        ),
    )


def command(
    tmp_path: Path,
    command_id: str,
    script: str,
    *,
    long_running: bool = False,
) -> PlanCommand:
    return PlanCommand(
        project_id="project-1",
        project_name="Project",
        command_id=command_id,
        label=command_id,
        cwd=str(tmp_path),
        argv=(sys.executable, "-c", script),
        long_running=long_running,
    )


def deployment(tmp_path: Path) -> ComposeDeploymentPlan:
    return ComposeDeploymentPlan(
        revision_id="revision-cancel",
        workspace_id="workspace-1",
        project_id="project-1",
        workspace_revision=2,
        source_fingerprint="a" * 64,
        target_config_fingerprint="b" * 64,
        checkout_path=str(tmp_path),
        frozen_compose_path=str(tmp_path / "compose.json"),
        services=("api",),
        immutable_images=("tripguru.local/api:revision-cancel",),
        wait_timeout_seconds=30,
        probe=TcpDeploymentProbeSpec(
            host="127.0.0.1",
            port=18000,
            timeout_seconds=5,
        ),
        environment_spec=DeploymentEnvironmentSnapshot(),
    )


def engine_for(
    loaded: LoadedExecutionPlan,
    store: FakeStore,
    *,
    variables: tuple[ResolvedEnvironmentVariable, ...] = (),
    freshness: FreshnessResult = DEFAULT_FRESHNESS,
    observer: FakeRunObserver | None = None,
    readiness_resolver: FakeReadinessResolver | None = None,
    readiness_waiter: FakeReadinessWaiter | None = None,
    deployment_executor: DeploymentExecutor | None = None,
) -> ExecutionEngine:
    return ExecutionEngine(
        store,
        FakeEnvironmentResolver(*variables),
        FakePlanLoader(loaded),
        FakeFreshnessValidator(freshness),
        observer,
        readiness_resolver,
        readiness_waiter,
        deployment_executor,
    )


def wait_until(predicate: Callable[[], bool], timeout: float = 8) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condition was not met before timeout")


def test_runs_finite_commands_in_order_and_redacts_resolved_secrets(tmp_path: Path) -> None:
    order_path = tmp_path / "order.txt"
    secret = "local-secret-value"
    first = command(
        tmp_path,
        "first",
        (
            "import os, pathlib, sys; "
            f"pathlib.Path({str(order_path)!r}).write_text('first', encoding='utf-8'); "
            "print(os.environ['APP_SECRET'], flush=True); "
            "print(os.environ['APP_SECRET'], file=sys.stderr, flush=True)"
        ),
    )
    second = command(
        tmp_path,
        "second",
        (
            "import pathlib; "
            f"path = pathlib.Path({str(order_path)!r}); "
            "assert path.read_text(encoding='utf-8') == 'first'; "
            "path.write_text('first,second', encoding='utf-8'); print('done', flush=True)"
        ),
    )
    store = FakeStore()
    observer = FakeRunObserver()
    engine = engine_for(
        make_plan(tmp_path, first, second),
        store,
        variables=(ResolvedEnvironmentVariable("APP_SECRET", secret, sensitive=True),),
        observer=observer,
    )

    created = engine.start("plan-1", "finite-idempotency-key")
    completed = engine.wait(created.id, timeout=10)

    assert completed.status is RunStatus.SUCCEEDED
    assert order_path.read_text(encoding="utf-8") == "first,second"
    messages = [event.message for event in store.events]
    assert secret not in "\n".join(messages)
    assert sum(message == "***" for message in messages) == 2
    assert any(event.kind is RunEventKind.STDOUT for event in store.events)
    assert any(event.kind is RunEventKind.STDERR for event in store.events)
    assert len(observer.notifications) == 1
    assert observer.notifications[0][0].status is RunStatus.SUCCEEDED
    assert observer.notifications[0][1].plan_id == "plan-1"


def test_redacts_raw_encoded_and_assembled_secret_variants(tmp_path: Path) -> None:
    raw = "p@ ss/word"
    encoded = "p%40%20ss%2Fword"
    assembled = f"postgresql://supplier:{encoded}@127.0.0.1:15432/supplier"
    planned = command(
        tmp_path,
        "redaction-variants",
        (
            "import os; "
            f"print({raw!r}, flush=True); "
            f"print({encoded!r}, flush=True); "
            "print(os.environ['DATABASE_URL'], flush=True)"
        ),
    )
    store = FakeStore()
    engine = engine_for(
        make_plan(tmp_path, planned),
        store,
        variables=(
            ResolvedEnvironmentVariable(
                "DATABASE_URL",
                assembled,
                sensitive=True,
                redaction_values=(raw, encoded, assembled),
            ),
        ),
    )

    created = engine.start("plan-1", "redaction-variants-key")
    completed = engine.wait(created.id, timeout=10)
    messages = "\n".join(event.message for event in store.events)

    assert completed.status is RunStatus.SUCCEEDED
    assert raw not in messages
    assert encoded not in messages
    assert assembled not in messages


def test_freshness_failure_creates_failed_run_without_starting_command(tmp_path: Path) -> None:
    marker = tmp_path / "should-not-exist"
    planned = command(
        tmp_path,
        "stale",
        f"import pathlib; pathlib.Path({str(marker)!r}).touch()",
    )
    store = FakeStore()
    engine = engine_for(
        make_plan(tmp_path, planned),
        store,
        freshness=FreshnessResult(
            valid=False,
            code="SOURCE_CHANGED",
            detail="源码已变化",
        ),
    )

    created = engine.start("plan-1", "freshness-idempotency-key")
    completed = engine.wait(created.id, timeout=5)

    assert completed.status is RunStatus.FAILED
    assert completed.failure_code == "SOURCE_CHANGED"
    assert not marker.exists()


def test_cancel_is_idempotent_and_kills_real_process_tree(tmp_path: Path) -> None:
    tree_script = (
        "import os, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)']); "
        "print(f'TREE {os.getpid()} {child.pid}', flush=True); time.sleep(120)"
    )
    planned = command(tmp_path, "serve", tree_script, long_running=True)
    store = FakeStore()
    observer = FakeRunObserver()
    engine = engine_for(make_plan(tmp_path, planned), store, observer=observer)

    created = engine.start("plan-1", "tree-idempotency-key")
    wait_until(lambda: any(event.message.startswith("TREE ") for event in store.events))
    tree_event = next(event for event in store.events if event.message.startswith("TREE "))
    parent_pid, child_pid = (int(value) for value in tree_event.message.split()[1:])
    assert store.get_run(created.id).status is RunStatus.RUNNING  # type: ignore[union-attr]
    assert process_exists(parent_pid)
    assert process_exists(child_pid)
    wait_until(lambda: len(observer.notifications) == 1)
    assert observer.notifications[0][0].status is RunStatus.RUNNING

    first_cancel = engine.cancel(created.id)
    second_cancel = engine.cancel(created.id)
    engine.wait(created.id, timeout=10)
    wait_until(lambda: not process_exists(parent_pid) and not process_exists(child_pid))

    assert first_cancel.status is RunStatus.CANCELLED
    assert second_cancel.status is RunStatus.CANCELLED
    assert sum(event.message == "Run cancelled" for event in store.events) == 1


def test_compose_cancel_waits_for_rollback_before_run_becomes_cancelled(
    tmp_path: Path,
) -> None:
    executor = BlockingDeploymentExecutor(DeploymentStatus.ROLLED_BACK)
    store = FakeStore()
    engine = engine_for(
        make_plan(tmp_path, deployments=(deployment(tmp_path),)),
        store,
        deployment_executor=executor,
    )
    created = engine.start("plan-1", "deployment-cancel-key")
    assert executor.started.wait(timeout=5)

    cancel_response = engine.cancel(created.id)
    assert cancel_response.status is RunStatus.RUNNING
    assert executor.recovery_started.wait(timeout=5)
    assert store.get_run(created.id).status is RunStatus.RUNNING  # type: ignore[union-attr]
    assert not any(event.message == "Run cancelled" for event in store.events)

    executor.release_recovery.set()
    completed = engine.wait(created.id, timeout=5)

    assert completed.status is RunStatus.CANCELLED
    messages = [event.message for event in store.events]
    requested = messages.index("Cancellation requested, waiting for Compose side effects to settle")
    settled = next(
        index for index, message in enumerate(messages) if "cancellation settled as rolled_back" in message
    )
    cancelled = messages.index("Run cancelled")
    assert requested < settled < cancelled


def test_compose_cancel_with_degraded_recovery_finishes_run_as_failed(tmp_path: Path) -> None:
    executor = BlockingDeploymentExecutor(DeploymentStatus.DEGRADED)
    store = FakeStore()
    engine = engine_for(
        make_plan(tmp_path, deployments=(deployment(tmp_path),)),
        store,
        deployment_executor=executor,
    )
    created = engine.start("plan-1", "deployment-degraded-key")
    assert executor.started.wait(timeout=5)

    assert engine.cancel(created.id).status is RunStatus.RUNNING
    assert executor.recovery_started.wait(timeout=5)
    executor.release_recovery.set()
    completed = engine.wait(created.id, timeout=5)

    assert completed.status is RunStatus.FAILED
    assert completed.failure_code == "DEPLOYMENT_CANCEL_RECOVERY_DEGRADED"
    assert "degraded" in (completed.failure_detail or "")


def test_long_running_process_must_pass_readiness_before_remaining_active(tmp_path: Path) -> None:
    planned = command(
        tmp_path,
        "serve",
        "import time; print('booted', flush=True); time.sleep(120)",
        long_running=True,
    )
    store = FakeStore()
    probe = TcpProbe("127.0.0.1", 48101, timeout_seconds=3)
    waiter = FakeReadinessWaiter(DeploymentProbeResult(True))
    engine = engine_for(
        make_plan(tmp_path, planned),
        store,
        readiness_resolver=FakeReadinessResolver(probe),
        readiness_waiter=waiter,
    )

    created = engine.start("plan-1", "readiness-success-key")
    wait_until(lambda: any(event.message == "Readiness probe passed" for event in store.events))

    assert store.get_run(created.id).status is RunStatus.RUNNING  # type: ignore[union-attr]
    assert waiter.calls == [probe]
    processes = engine.list_processes().processes
    assert len(processes) == 1
    assert processes[0].run_id == created.id
    assert processes[0].project_id == "project-1"
    assert processes[0].long_running is True
    engine.cancel(created.id)
    assert engine.wait(created.id, timeout=10).status is RunStatus.CANCELLED
    assert engine.list_processes().processes == ()


def test_readiness_failure_fails_run_and_terminates_long_running_process(tmp_path: Path) -> None:
    planned = command(
        tmp_path,
        "serve",
        "import time; time.sleep(120)",
        long_running=True,
    )
    store = FakeStore()
    engine = engine_for(
        make_plan(tmp_path, planned),
        store,
        readiness_resolver=FakeReadinessResolver(TcpProbe("127.0.0.1", 48102, timeout_seconds=3)),
        readiness_waiter=FakeReadinessWaiter(
            DeploymentProbeResult(False, "Explicit readiness probe timed out")
        ),
    )

    created = engine.start("plan-1", "readiness-failure-key")
    completed = engine.wait(created.id, timeout=10)

    assert completed.status is RunStatus.FAILED
    assert completed.failure_code == "READINESS_FAILED"
    assert completed.failure_detail == "Explicit readiness probe timed out"


def test_retry_creates_new_run_and_is_idempotent(tmp_path: Path) -> None:
    planned = command(tmp_path, "fail", "raise SystemExit(7)")
    store = FakeStore()
    engine = engine_for(make_plan(tmp_path, planned), store)
    original = engine.start("plan-1", "original-idempotency-key")
    failed = engine.wait(original.id, timeout=5)
    assert failed.status is RunStatus.FAILED

    retried = engine.retry(original.id, "retry-idempotency-key")
    same_retry = engine.retry(original.id, "retry-idempotency-key")
    retried_final = engine.wait(retried.id, timeout=5)

    assert retried.id != original.id
    assert same_retry.id == retried.id
    assert retried_final.retry_of == original.id
    assert len(store.runs) == 2


def process_exists(pid: int) -> bool:
    if sys.platform == "win32":
        result = subprocess.run(
            ("tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"),
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return f'"{pid}"' in result.stdout
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def test_state_store_structurally_satisfies_execution_store_protocol(tmp_path: Path) -> None:
    concrete = StateStore(tmp_path / "state.db")
    store: ExecutionStore = concrete

    assert store is concrete
    concrete.close()
