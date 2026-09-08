from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Protocol
from uuid import uuid4

from pipedeck.compose_deployment import (
    DeploymentProbe,
    DeploymentProbeResult,
    DeploymentStatus,
)
from pipedeck.contracts import (
    ComposeDeploymentPlan,
    PipelineJobSpec,
    PlanCommand,
    PlanStep,
    RunEvent,
    RunEventKind,
    RunRecord,
    RunStatus,
    RuntimeProcessListResponse,
    RuntimeProcessRecord,
    WorkspacePlanResponse,
)


@dataclass(frozen=True, slots=True)
class LoadedExecutionPlan:
    plan: WorkspacePlanResponse
    workspace_name: str


@dataclass(frozen=True, slots=True)
class ResolvedEnvironmentVariable:
    name: str
    value: str
    sensitive: bool = False
    redaction_values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FreshnessResult:
    valid: bool
    code: str | None = None
    detail: str | None = None


class ExecutionPlanLoader(Protocol):
    def load(self, plan_id: str) -> LoadedExecutionPlan | None: ...


class EnvironmentResolver(Protocol):
    def resolve(
        self,
        plan: WorkspacePlanResponse,
        command: PlanCommand,
    ) -> tuple[ResolvedEnvironmentVariable, ...]: ...


class FreshnessValidator(Protocol):
    def validate(self, plan: WorkspacePlanResponse) -> FreshnessResult: ...


class RunObserver(Protocol):
    def on_started(self, run: RunRecord, plan: WorkspacePlanResponse) -> None: ...


class CommandReadinessResolver(Protocol):
    def resolve(
        self,
        plan: WorkspacePlanResponse,
        command: PlanCommand,
    ) -> DeploymentProbe | None: ...


class ReadinessWaiter(Protocol):
    def wait(
        self,
        probe: DeploymentProbe,
        *,
        cancelled: Callable[[], bool],
        target_alive: Callable[[], bool],
    ) -> DeploymentProbeResult: ...


@dataclass(frozen=True, slots=True)
class DeploymentExecutionResult:
    succeeded: bool
    code: str | None = None
    detail: str | None = None
    deployment_status: DeploymentStatus | None = None


class DeploymentExecutor(Protocol):
    def execute(
        self,
        deployment: ComposeDeploymentPlan,
        cancelled: Callable[[], bool],
    ) -> DeploymentExecutionResult: ...


class JobLogSinkProtocol(Protocol):
    def write(self, job_name: str, stream: str, line: str) -> None: ...


class PipelineJobExecutor(Protocol):
    """执行 PlanStep 内嵌的 GitLab CI job；日志逐行经 sink，取消由 cancelled 注入。"""

    def execute(
        self,
        spec: PipelineJobSpec,
        *,
        sink: JobLogSinkProtocol,
        cancelled: Callable[[], bool],
    ) -> bool: ...


class GitlabJobExecutor:
    """PipelineJobExecutor 的默认实现，直接对接 gitlab_ci.executor.run_job。"""

    def execute(
        self,
        spec: PipelineJobSpec,
        *,
        sink: JobLogSinkProtocol,
        cancelled: Callable[[], bool],
    ) -> bool:
        from pipedeck.gitlab_ci.executor import JobContext, run_job

        outcome = run_job(
            JobContext(
                job=spec.job,
                variables=dict(spec.job.variables),
                workspace_dir=Path(spec.workspace_dir),
                artifact_dir=Path(spec.artifact_dir),
                project_name=spec.project_name,
            ),
            sink=sink,
            cancelled=cancelled,
        )
        return outcome.succeeded


class ExecutionStore(Protocol):
    def create_run(self, run: RunRecord, idempotency_key: str) -> RunRecord: ...

    def get_run(self, run_id: str) -> RunRecord | None: ...

    def update_run(
        self,
        run: RunRecord,
        expected_status: RunStatus | None = None,
    ) -> RunRecord: ...

    def append_event(
        self,
        *,
        run_id: str,
        kind: RunEventKind,
        message: str,
        step_id: str | None = None,
        project_id: str | None = None,
    ) -> RunEvent: ...


class ExecutionError(RuntimeError):
    code = "EXECUTION_ERROR"
    detail = "Local execution failed"

    def __init__(self) -> None:
        super().__init__(self.detail)


class PlanNotFoundError(ExecutionError):
    code = "PLAN_NOT_FOUND"
    detail = "Execution plan not found"


class PlanNotRunnableError(ExecutionError):
    code = "PLAN_NOT_RUNNABLE"
    detail = "Execution plan failed preflight or is missing persisted metadata"


class RunNotFoundError(ExecutionError):
    code = "RUN_NOT_FOUND"
    detail = "Run record not found"


class RunNotRetryableError(ExecutionError):
    code = "RUN_NOT_RETRYABLE"
    detail = "Only finished runs can be retried"


@dataclass(slots=True)
class _ProcessHandle:
    process: subprocess.Popen[str]
    stdout_thread: threading.Thread
    stderr_thread: threading.Thread
    command: PlanCommand
    started_at: datetime
    long_running: bool


def _empty_processes() -> list[_ProcessHandle]:
    return []


@dataclass(slots=True)
class _ActiveRun:
    run_id: str
    cancel_requested: threading.Event = field(default_factory=threading.Event)
    deployment_in_progress: threading.Event = field(default_factory=threading.Event)
    cancel_pending_announced: threading.Event = field(default_factory=threading.Event)
    processes: list[_ProcessHandle] = field(default_factory=_empty_processes)
    process_lock: threading.Lock = field(default_factory=threading.Lock)
    termination_lock: threading.Lock = field(default_factory=threading.Lock)
    state_lock: threading.Lock = field(default_factory=threading.Lock)
    worker: threading.Thread | None = None


class _Redactor:
    def __init__(self, values: tuple[str, ...]) -> None:
        self._values = tuple(sorted({value for value in values if value}, key=len, reverse=True))

    def redact(self, message: str) -> str:
        redacted = message
        for value in self._values:
            redacted = redacted.replace(value, "***")
        return redacted


_TERMINAL_STATUSES = (
    RunStatus.SUCCEEDED,
    RunStatus.FAILED,
    RunStatus.CANCELLED,
    RunStatus.INTERRUPTED,
)


class ExecutionEngine:
    def __init__(
        self,
        store: ExecutionStore,
        environment_resolver: EnvironmentResolver,
        plan_loader: ExecutionPlanLoader,
        freshness_validator: FreshnessValidator,
        run_observer: RunObserver | None = None,
        readiness_resolver: CommandReadinessResolver | None = None,
        readiness_waiter: ReadinessWaiter | None = None,
        deployment_executor: DeploymentExecutor | None = None,
        pipeline_job_executor: PipelineJobExecutor | None = None,
    ) -> None:
        self._store = store
        self._environment_resolver = environment_resolver
        self._plan_loader = plan_loader
        self._freshness_validator = freshness_validator
        self._run_observer = run_observer
        self._readiness_resolver = readiness_resolver
        self._readiness_waiter = readiness_waiter
        self._deployment_executor = deployment_executor
        self._pipeline_job_executor = pipeline_job_executor or GitlabJobExecutor()
        self._active_runs: list[_ActiveRun] = []
        self._active_lock = threading.Lock()

    def start(
        self,
        plan_id: str,
        idempotency_key: str,
        *,
        retry_of: str | None = None,
    ) -> RunRecord:
        loaded = self._plan_loader.load(plan_id)
        if loaded is None:
            raise PlanNotFoundError()
        proposed = self._new_run(loaded, retry_of)
        created = self._store.create_run(proposed, idempotency_key)
        if created.id != proposed.id:
            return created

        active = _ActiveRun(run_id=created.id)
        worker = threading.Thread(
            target=self._execute,
            args=(active, loaded.plan),
            name=f"pipedeck-run-{created.id}",
            daemon=True,
        )
        active.worker = worker
        with self._active_lock:
            self._active_runs.append(active)
        worker.start()
        return created

    def retry(self, run_id: str, idempotency_key: str) -> RunRecord:
        previous = self._store.get_run(run_id)
        if previous is None:
            raise RunNotFoundError()
        if previous.status not in _TERMINAL_STATUSES:
            raise RunNotRetryableError()
        return self.start(
            previous.plan_id,
            idempotency_key,
            retry_of=previous.id,
        )

    def cancel(self, run_id: str) -> RunRecord:
        current = self._store.get_run(run_id)
        if current is None:
            raise RunNotFoundError()
        if current.status in _TERMINAL_STATUSES:
            return current

        active = self._find_active(run_id)
        if active is not None:
            active.cancel_requested.set()
            with active.state_lock:
                current = self._store.get_run(run_id)
                if current is None:
                    raise RunNotFoundError()
                if current.status in _TERMINAL_STATUSES:
                    return current
                if active.deployment_in_progress.is_set():
                    if not active.cancel_pending_announced.is_set():
                        active.cancel_pending_announced.set()
                        self._store.append_event(
                            run_id=run_id,
                            kind=RunEventKind.STATUS,
                            message=(
                                "Cancellation requested, waiting for Compose side effects to settle"
                            ),
                            step_id=current.current_step,
                        )
                    return current
            self._terminate_processes(active)
            return self._transition_cancelled(active)
        return self._transition(run_id, RunStatus.CANCELLED)

    def wait(self, run_id: str, timeout: float | None = None) -> RunRecord:
        active = self._find_active(run_id)
        if active is not None and active.worker is not None:
            active.worker.join(timeout)
        run = self._store.get_run(run_id)
        if run is None:
            raise RunNotFoundError()
        return run

    def list_processes(self) -> RuntimeProcessListResponse:
        with self._active_lock:
            active_runs = tuple(self._active_runs)
        records: list[RuntimeProcessRecord] = []
        for active in active_runs:
            with active.process_lock:
                handles = tuple(active.processes)
            for handle in handles:
                if handle.process.poll() is not None:
                    continue
                command = handle.command
                records.append(
                    RuntimeProcessRecord(
                        id=f"{active.run_id}:{handle.process.pid}",
                        pid=handle.process.pid,
                        run_id=active.run_id,
                        project_id=command.project_id,
                        project_name=command.project_name,
                        command_id=command.command_id,
                        label=command.label,
                        cwd=command.cwd,
                        argv=command.argv,
                        long_running=handle.long_running,
                        started_at=handle.started_at,
                    )
                )
        return RuntimeProcessListResponse(
            generated_at=datetime.now(UTC),
            processes=tuple(sorted(records, key=lambda item: (item.run_id, item.pid))),
        )

    @staticmethod
    def _new_run(
        loaded: LoadedExecutionPlan,
        retry_of: str | None,
    ) -> RunRecord:
        plan = loaded.plan
        if not plan.ready:
            raise PlanNotRunnableError()
        if (
            plan.plan_id is None
            or plan.config_fingerprint is None
            or plan.source_fingerprint is None
        ):
            raise PlanNotRunnableError()
        return RunRecord(
            id=uuid4().hex,
            workspace_id=plan.workspace_id,
            workspace_name=loaded.workspace_name,
            workspace_revision=plan.workspace_revision,
            plan_id=plan.plan_id,
            mode=plan.mode,
            config_fingerprint=plan.config_fingerprint,
            source_fingerprint=plan.source_fingerprint,
            retry_of=retry_of,
            status=RunStatus.QUEUED,
            current_step=None,
            created_at=datetime.now(UTC),
            started_at=None,
            finished_at=None,
            failure_code=None,
            failure_detail=None,
        )

    def _execute(self, active: _ActiveRun, plan: WorkspacePlanResponse) -> None:
        try:
            if active.cancel_requested.is_set():
                self._transition_cancelled(active)
                return
            self._transition(active.run_id, RunStatus.RUNNING)
            try:
                freshness = self._freshness_validator.validate(plan)
            except Exception as error:
                self._fail(
                    active,
                    "PLAN_FRESHNESS_CHECK_FAILED",
                    f"Plan freshness check failed: {type(error).__name__}",
                )
                return
            if not freshness.valid:
                self._fail(
                    active,
                    freshness.code or "PLAN_STALE",
                    freshness.detail
                    or "Workspace, config, or source changed; please re-run preflight",
                )
                return
            if active.cancel_requested.is_set():
                self._transition_cancelled(active)
                return

            self._store.append_event(
                run_id=active.run_id,
                kind=RunEventKind.STATUS,
                message="Run started",
            )
            for step in plan.steps:
                if active.cancel_requested.is_set():
                    self._transition_cancelled(active)
                    return
                self._transition(active.run_id, RunStatus.RUNNING, current_step=step.id)
                self._store.append_event(
                    run_id=active.run_id,
                    kind=RunEventKind.STAGE,
                    message=step.title,
                    step_id=step.id,
                )
                if not self._run_step(active, plan, step):
                    return

            long_running = self._long_running_processes(active)
            if not long_running:
                completed = self._succeed(active)
                if completed is not None:
                    self._notify_observer(completed, plan)
                return
            exited = next(
                (handle for handle in long_running if handle.process.poll() is not None),
                None,
            )
            if exited is not None:
                self._terminate_processes(active)
                self._fail(
                    active,
                    "LONG_RUNNING_PROCESS_EXITED",
                    f"Long-running process exited unexpectedly with code "
                    f"{exited.process.returncode}",
                )
                return
            self._store.append_event(
                run_id=active.run_id,
                kind=RunEventKind.SYSTEM,
                message="Long-running process started; run stays active",
            )
            current = self._store.get_run(active.run_id)
            if current is not None:
                self._notify_observer(current, plan)
            self._monitor_long_running(active)
        finally:
            run = self._store.get_run(active.run_id)
            if run is not None and run.status in _TERMINAL_STATUSES:
                # A downstream Compose failure/cancellation may follow started host services.
                # Keep their handles owned until every process has actually been stopped.
                self._terminate_processes(active)
                self._remove_active(active)

    def _run_step(
        self,
        active: _ActiveRun,
        plan: WorkspacePlanResponse,
        step: PlanStep,
    ) -> bool:
        if step.pipeline_job is not None:
            return self._run_pipeline_job(active, step)
        commands_succeeded = all(
            self._run_command(active, plan, step.id, command) for command in step.commands
        )
        if not commands_succeeded:
            return False
        return all(
            self._run_deployment(active, step.id, deployment) for deployment in step.deployments
        )

    def _run_pipeline_job(self, active: _ActiveRun, step: PlanStep) -> bool:
        spec = step.pipeline_job
        if spec is None:  # pragma: no cover - 由 _run_step 分支保证
            return True
        sink = _RunEventSink(self._store, active.run_id, step.id)

        def _on_event(kind: RunEventKind, message: str) -> None:
            self._store.append_event(
                run_id=active.run_id, kind=kind, message=message, step_id=step.id
            )

        if active.cancel_requested.is_set():
            self._transition_cancelled(active)
            return False
        _on_event(RunEventKind.SYSTEM, f"Starting GitLab job {spec.job.name}")
        try:
            succeeded = self._pipeline_job_executor.execute(
                spec, sink=sink, cancelled=active.cancel_requested.is_set
            )
        except Exception as error:
            self._fail(
                active,
                "PIPELINE_JOB_EXECUTION_FAILED",
                f"GitLab job {spec.job.name} failed: {type(error).__name__}",
            )
            return False
        if active.cancel_requested.is_set():
            self._transition_cancelled(active)
            return False
        if not succeeded:
            self._fail(
                active,
                "PIPELINE_JOB_FAILED",
                f"GitLab job {spec.job.name} failed",
            )
            return False
        _on_event(RunEventKind.SYSTEM, f"GitLab job {spec.job.name} succeeded")
        return True

    def _run_deployment(
        self,
        active: _ActiveRun,
        step_id: str,
        deployment: ComposeDeploymentPlan,
    ) -> bool:
        if self._deployment_executor is None:
            self._fail(
                active,
                "DEPLOYMENT_EXECUTOR_UNAVAILABLE",
                "Control service is not configured with a Compose deployment executor",
            )
            return False
        with active.state_lock:
            current = self._store.get_run(active.run_id)
            if current is None or current.status in _TERMINAL_STATUSES:
                return False
            if active.cancel_requested.is_set():
                should_cancel = True
            else:
                active.deployment_in_progress.set()
                should_cancel = False
        if should_cancel:
            self._transition_cancelled(active)
            return False
        try:
            try:
                result = self._deployment_executor.execute(
                    deployment,
                    active.cancel_requested.is_set,
                )
            except Exception as error:
                self._fail(
                    active,
                    "DEPLOYMENT_EXECUTION_FAILED",
                    f"Container deployment failed: {type(error).__name__}",
                )
                return False
            if not result.succeeded:
                if result.code == "DEPLOYMENT_CANCELLED" and active.cancel_requested.is_set():
                    return self._settle_cancelled_deployment(active, deployment, result)
                self._fail(
                    active,
                    result.code or "DEPLOYMENT_FAILED",
                    result.detail or "Compose deployment is not active",
                )
                return False
            self._store.append_event(
                run_id=active.run_id,
                kind=RunEventKind.SYSTEM,
                message=f"DeploymentRevision {deployment.revision_id} activated",
                step_id=step_id,
                project_id=deployment.project_id,
            )
            return True
        finally:
            active.deployment_in_progress.clear()

    def _settle_cancelled_deployment(
        self,
        active: _ActiveRun,
        deployment: ComposeDeploymentPlan,
        result: DeploymentExecutionResult,
    ) -> bool:
        settled_status = result.deployment_status
        if settled_status is DeploymentStatus.DEGRADED:
            self._fail(
                active,
                "DEPLOYMENT_CANCEL_RECOVERY_DEGRADED",
                result.detail
                or "Recovery after cancelled deployment did not converge; reconcile required",
            )
            return False
        if (
            settled_status is not DeploymentStatus.FAILED
            and settled_status is not DeploymentStatus.ROLLED_BACK
        ):
            self._fail(
                active,
                "DEPLOYMENT_CANCEL_STATE_UNKNOWN",
                "Revision state after cancelled deployment cannot prove side effects converged",
            )
            return False
        self._store.append_event(
            run_id=active.run_id,
            kind=RunEventKind.SYSTEM,
            message=(
                f"DeploymentRevision {deployment.revision_id} cancellation settled as "
                f"{settled_status.value}"
            ),
            project_id=deployment.project_id,
        )
        self._transition_cancelled(active)
        return False

    def _run_command(
        self,
        active: _ActiveRun,
        plan: WorkspacePlanResponse,
        step_id: str,
        command: PlanCommand,
    ) -> bool:
        process: _ProcessHandle | None = None
        try:
            variables = self._environment_resolver.resolve(plan, command)
            with active.state_lock:
                current = self._store.get_run(active.run_id)
                should_cancel = (
                    active.cancel_requested.is_set()
                    or current is None
                    or current.status in _TERMINAL_STATUSES
                )
                if not should_cancel:
                    process = self._spawn(active, command, step_id, variables)
            if process is None:
                self._transition_cancelled(active)
                return False
        except Exception as error:
            self._terminate_processes(active)
            self._fail(
                active,
                "COMMAND_START_FAILED",
                f"Command failed to start: {type(error).__name__}",
            )
            return False
        if command.long_running:
            self._store.append_event(
                run_id=active.run_id,
                kind=RunEventKind.SYSTEM,
                message=f"Long-running process started (PID {process.process.pid})",
                step_id=step_id,
                project_id=command.project_id,
            )
            return self._wait_for_readiness(active, plan, step_id, command, process)

        return_code = process.process.wait()
        process.stdout_thread.join()
        process.stderr_thread.join()
        self._remove_process(active, process)
        if active.cancel_requested.is_set():
            self._transition_cancelled(active)
            return False
        if return_code != 0:
            self._terminate_processes(active)
            self._fail(
                active,
                "COMMAND_FAILED",
                f"Command {command.label} exited with code {return_code}",
            )
            return False
        return True

    def _wait_for_readiness(
        self,
        active: _ActiveRun,
        plan: WorkspacePlanResponse,
        step_id: str,
        command: PlanCommand,
        handle: _ProcessHandle,
    ) -> bool:
        if self._readiness_resolver is None or self._readiness_waiter is None:
            return True
        try:
            probe = self._readiness_resolver.resolve(plan, command)
            if probe is None:
                return True
            result = self._readiness_waiter.wait(
                probe,
                cancelled=active.cancel_requested.is_set,
                target_alive=lambda: handle.process.poll() is None,
            )
        except Exception as error:
            self._terminate_processes(active)
            self._fail(
                active,
                "READINESS_CHECK_FAILED",
                f"Readiness check failed: {type(error).__name__}",
            )
            return False
        if active.cancel_requested.is_set():
            self._transition_cancelled(active)
            return False
        if not result.ready:
            self._terminate_processes(active)
            self._fail(
                active,
                "READINESS_FAILED",
                result.detail or "Explicit readiness probe did not pass",
            )
            return False
        self._store.append_event(
            run_id=active.run_id,
            kind=RunEventKind.SYSTEM,
            message="Readiness probe passed",
            step_id=step_id,
            project_id=command.project_id,
        )
        return True

    def _spawn(
        self,
        active: _ActiveRun,
        command: PlanCommand,
        step_id: str,
        variables: tuple[ResolvedEnvironmentVariable, ...],
    ) -> _ProcessHandle:
        # pipedeck-ast: ignore[TG-DS001] - subprocess requires a concrete environment mapping.
        environment = os.environ.copy()
        for variable in variables:
            environment[variable.name] = variable.value
        redaction_values = tuple(
            value
            for variable in variables
            for value in (
                *((variable.value,) if variable.sensitive else ()),
                *variable.redaction_values,
            )
        )
        redactor = _Redactor(redaction_values)
        creation_flags = 0
        if sys.platform == "win32":
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen(
            command.argv,
            cwd=Path(command.cwd),
            env=environment,
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creation_flags,
            start_new_session=sys.platform != "win32",
        )
        if process.stdout is None or process.stderr is None:
            process.kill()
            raise ExecutionError()
        stdout_thread = self._reader_thread(
            process.stdout,
            active.run_id,
            RunEventKind.STDOUT,
            step_id,
            command.project_id,
            redactor,
        )
        stderr_thread = self._reader_thread(
            process.stderr,
            active.run_id,
            RunEventKind.STDERR,
            step_id,
            command.project_id,
            redactor,
        )
        handle = _ProcessHandle(
            process=process,
            stdout_thread=stdout_thread,
            stderr_thread=stderr_thread,
            command=command,
            started_at=datetime.now(UTC),
            long_running=command.long_running,
        )
        with active.process_lock:
            active.processes.append(handle)
        stdout_thread.start()
        stderr_thread.start()
        return handle

    def _reader_thread(
        self,
        stream: IO[str],
        run_id: str,
        kind: RunEventKind,
        step_id: str,
        project_id: str,
        redactor: _Redactor,
    ) -> threading.Thread:
        def read() -> None:
            try:
                for raw_line in stream:
                    message = raw_line.rstrip("\r\n")
                    if message:
                        self._store.append_event(
                            run_id=run_id,
                            kind=kind,
                            message=redactor.redact(message),
                            step_id=step_id,
                            project_id=project_id,
                        )
            finally:
                stream.close()

        return threading.Thread(
            target=read,
            name=f"pipedeck-log-{run_id}-{kind.value}",
            daemon=True,
        )

    def _monitor_long_running(self, active: _ActiveRun) -> None:
        while not active.cancel_requested.wait(0.05):
            for handle in self._long_running_processes(active):
                return_code = handle.process.poll()
                if return_code is None:
                    continue
                handle.stdout_thread.join()
                handle.stderr_thread.join()
                self._remove_process(active, handle)
                self._terminate_processes(active)
                self._fail(
                    active,
                    "LONG_RUNNING_PROCESS_EXITED",
                    f"Long-running process exited unexpectedly with code {return_code}",
                )
                return
        self._transition_cancelled(active)

    def _succeed(self, active: _ActiveRun) -> RunRecord | None:
        with active.state_lock:
            current = self._store.get_run(active.run_id)
            if current is None or current.status in _TERMINAL_STATUSES:
                return current
            succeeded = self._transition(active.run_id, RunStatus.SUCCEEDED)
            self._store.append_event(
                run_id=active.run_id,
                kind=RunEventKind.STATUS,
                message="Run succeeded",
            )
            return succeeded

    def _notify_observer(self, run: RunRecord, plan: WorkspacePlanResponse) -> None:
        if self._run_observer is None:
            return
        try:
            self._run_observer.on_started(run, plan)
        except Exception as error:
            self._store.append_event(
                run_id=run.id,
                kind=RunEventKind.SYSTEM,
                message=f"Run observer notification failed: {type(error).__name__}",
            )

    def _fail(self, active: _ActiveRun, code: str, detail: str) -> None:
        with active.state_lock:
            current = self._store.get_run(active.run_id)
            if current is None or current.status in _TERMINAL_STATUSES:
                return
            self._transition(
                active.run_id,
                RunStatus.FAILED,
                failure_code=code,
                failure_detail=detail,
            )
            self._store.append_event(
                run_id=active.run_id,
                kind=RunEventKind.STATUS,
                message=detail,
            )

    def _transition_cancelled(self, active: _ActiveRun) -> RunRecord:
        with active.state_lock:
            current = self._store.get_run(active.run_id)
            if current is None:
                raise RunNotFoundError()
            if current.status in _TERMINAL_STATUSES:
                return current
            cancelled = self._transition(active.run_id, RunStatus.CANCELLED)
            self._store.append_event(
                run_id=active.run_id,
                kind=RunEventKind.STATUS,
                message="Run cancelled",
            )
            return cancelled

    def _transition(
        self,
        run_id: str,
        status: RunStatus,
        *,
        current_step: str | None = None,
        failure_code: str | None = None,
        failure_detail: str | None = None,
    ) -> RunRecord:
        current = self._store.get_run(run_id)
        if current is None:
            raise RunNotFoundError()
        now = datetime.now(UTC)
        updated = RunRecord(
            id=current.id,
            workspace_id=current.workspace_id,
            workspace_name=current.workspace_name,
            workspace_revision=current.workspace_revision,
            plan_id=current.plan_id,
            mode=current.mode,
            status=status,
            current_step=current_step if current_step is not None else current.current_step,
            config_fingerprint=current.config_fingerprint,
            source_fingerprint=current.source_fingerprint,
            retry_of=current.retry_of,
            created_at=current.created_at,
            started_at=(
                now
                if status is RunStatus.RUNNING and current.started_at is None
                else current.started_at
            ),
            finished_at=now if status in _TERMINAL_STATUSES else current.finished_at,
            failure_code=failure_code,
            failure_detail=failure_detail,
        )
        return self._store.update_run(updated, expected_status=current.status)

    @staticmethod
    def _terminate_process(handle: _ProcessHandle) -> None:
        process = handle.process
        if process.poll() is not None:
            return
        if sys.platform == "win32":
            subprocess.run(
                ("taskkill", "/PID", str(process.pid), "/T", "/F"),
                check=False,
                capture_output=True,
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            process.kill()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        handle.stdout_thread.join(timeout=2)
        handle.stderr_thread.join(timeout=2)

    def _terminate_processes(self, active: _ActiveRun) -> None:
        with active.termination_lock:
            with active.process_lock:
                processes = tuple(active.processes)
            for handle in processes:
                self._terminate_process(handle)
                self._remove_process(active, handle)

    @staticmethod
    def _remove_process(active: _ActiveRun, handle: _ProcessHandle) -> None:
        with active.process_lock:
            if handle in active.processes:
                active.processes.remove(handle)

    @staticmethod
    def _long_running_processes(active: _ActiveRun) -> tuple[_ProcessHandle, ...]:
        with active.process_lock:
            return tuple(handle for handle in active.processes if handle.long_running)

    def _find_active(self, run_id: str) -> _ActiveRun | None:
        with self._active_lock:
            return next((active for active in self._active_runs if active.run_id == run_id), None)

    def _remove_active(self, active: _ActiveRun) -> None:
        with self._active_lock:
            if active in self._active_runs:
                self._active_runs.remove(active)


class _RunEventSink:
    """把 job 执行日志逐行落到 RunEvent 流。"""

    def __init__(self, store: ExecutionStore, run_id: str, step_id: str) -> None:
        self._store = store
        self._run_id = run_id
        self._step_id = step_id

    def write(self, job_name: str, stream: str, line: str) -> None:
        self._store.append_event(
            run_id=self._run_id,
            kind=RunEventKind.STDOUT if stream == "stdout" else RunEventKind.STDERR,
            message=f"[{job_name}] {line}",
            step_id=self._step_id,
        )
