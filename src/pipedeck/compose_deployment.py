from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pipedeck.contracts import DeploymentEnvironmentSnapshot


class DeploymentStatus(StrEnum):
    PLANNED = "planned"
    BUILDING = "building"
    APPLYING = "applying"
    VERIFYING = "verifying"
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    FAILED = "failed"
    RECOVERING = "recovering"
    ROLLED_BACK = "rolled_back"
    DEGRADED = "degraded"


TARGET_EXCLUSIVE_DEPLOYMENT_STATUSES = (
    DeploymentStatus.PLANNED,
    DeploymentStatus.BUILDING,
    DeploymentStatus.APPLYING,
    DeploymentStatus.VERIFYING,
    DeploymentStatus.RECOVERING,
)

TERMINAL_DEPLOYMENT_STATUSES = (
    DeploymentStatus.SUPERSEDED,
    DeploymentStatus.FAILED,
    DeploymentStatus.ROLLED_BACK,
    DeploymentStatus.DEGRADED,
)


class DeploymentAction(StrEnum):
    BUILD = "build"
    APPLY = "apply"
    VERIFY = "verify"
    ROLLBACK = "rollback"
    DOWN = "down"


@dataclass(frozen=True, slots=True)
class HttpProbe:
    url: str
    timeout_seconds: float = 10


@dataclass(frozen=True, slots=True)
class TcpProbe:
    host: str
    port: int
    timeout_seconds: float = 10


DeploymentProbe = HttpProbe | TcpProbe


class DeploymentIntentProblem(StrEnum):
    IDENTITY_REQUIRED = "Deployment intent must include revision, workspace, and target identity"
    SERVICES_REQUIRED = "Compose deployment requires at least one service"
    IMMUTABLE_IMAGE_REQUIRED = "Compose deployment must pin immutable images"
    FROZEN_JSON_REQUIRED = "Compose deployment must use a frozen JSON config"
    WAIT_TIMEOUT_INVALID = "Compose wait timeout must be greater than 0"


class InvalidDeploymentIntentError(ValueError):
    def __init__(self, problem: DeploymentIntentProblem) -> None:
        super().__init__(problem.value)


@dataclass(frozen=True, slots=True)
class DeploymentIntent:
    revision_id: str
    workspace_id: str
    target_id: str
    workspace_revision: int
    source_fingerprint: str
    target_config_fingerprint: str
    checkout_path: Path
    frozen_compose_path: Path
    services: tuple[str, ...]
    immutable_images: tuple[str, ...]
    wait_timeout_seconds: int
    probe: DeploymentProbe
    environment_spec: DeploymentEnvironmentSnapshot
    ref: str = ""

    def __post_init__(self) -> None:
        if not self.revision_id or not self.workspace_id or not self.target_id:
            raise InvalidDeploymentIntentError(DeploymentIntentProblem.IDENTITY_REQUIRED)
        if not self.services:
            raise InvalidDeploymentIntentError(DeploymentIntentProblem.SERVICES_REQUIRED)
        if not self.immutable_images:
            raise InvalidDeploymentIntentError(DeploymentIntentProblem.IMMUTABLE_IMAGE_REQUIRED)
        if self.frozen_compose_path.suffix.lower() != ".json":
            raise InvalidDeploymentIntentError(DeploymentIntentProblem.FROZEN_JSON_REQUIRED)
        if self.wait_timeout_seconds <= 0:
            raise InvalidDeploymentIntentError(DeploymentIntentProblem.WAIT_TIMEOUT_INVALID)


@dataclass(frozen=True, slots=True)
class DeploymentRevision:
    intent: DeploymentIntent
    project_name: str
    previous_revision_id: str | None
    status: DeploymentStatus
    created_at: datetime
    updated_at: datetime
    failure_code: str | None = None
    failure_detail: str | None = None
    recovery_detail: str | None = None


@dataclass(frozen=True, slots=True)
class ResolvedDeploymentVariable:
    name: str
    value: str
    sensitive: bool = False
    redaction_values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DeploymentCommandResult:
    return_code: int
    stdout: str = ""
    stderr: str = ""
    cancelled: bool = False
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class DeploymentProbeResult:
    ready: bool
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class RuntimeTargetState:
    target_present: bool
    revision_id: str | None
    probe_ready: bool


class DeploymentStore(Protocol):
    """Durable owner of target exclusion, transitions, and atomic activation."""

    def begin(self, revision: DeploymentRevision) -> DeploymentRevision:
        """Persist planned intent and reject another pending revision for the target."""
        ...

    def get_revision(self, revision_id: str) -> DeploymentRevision | None: ...

    def list_deployment_revisions(
        self,
        workspace_id: str | None = None,
        target_id: str | None = None,
    ) -> tuple[DeploymentRevision, ...]: ...

    def update_revision(
        self,
        revision: DeploymentRevision,
        expected_status: DeploymentStatus,
    ) -> DeploymentRevision: ...

    def activate_revision(
        self,
        revision: DeploymentRevision,
        expected_status: DeploymentStatus,
    ) -> DeploymentRevision:
        """Activate revision and supersede the previous active in one transaction."""
        ...

    def list_pending_revisions(self) -> tuple[DeploymentRevision, ...]: ...


class DeploymentEnvironmentResolver(Protocol):
    def resolve(
        self,
        revision: DeploymentRevision,
        action: DeploymentAction,
    ) -> tuple[ResolvedDeploymentVariable, ...]: ...


class ComposeDeploymentRunner(Protocol):
    def run(
        self,
        *,
        argv: tuple[str, ...],
        cwd: Path,
        environment: tuple[ResolvedDeploymentVariable, ...],
        cancellation: DeploymentCancellation | None = None,
        timeout_seconds: float | None = None,
    ) -> DeploymentCommandResult: ...


class DeploymentProbeRunner(Protocol):
    def verify(
        self,
        probe: DeploymentProbe,
        environment: tuple[ResolvedDeploymentVariable, ...],
    ) -> DeploymentProbeResult: ...


class DeploymentRuntimeInspector(Protocol):
    def inspect(self, workspace_id: str, target_id: str) -> RuntimeTargetState: ...


class DeploymentCancellation(Protocol):
    def is_cancelled(self) -> bool: ...


class DeploymentError(RuntimeError):
    code = "DEPLOYMENT_ERROR"
    detail = "Compose deployment failed"

    def __init__(self) -> None:
        super().__init__(self.detail)


class TargetDeploymentConflictError(DeploymentError):
    code = "DEPLOYMENT_TARGET_BUSY"
    detail = "The target already has an unfinished deployment"


class DeploymentRevisionNotFoundError(DeploymentError):
    code = "DEPLOYMENT_REVISION_NOT_FOUND"
    detail = "Deployment revision not found"


class _NeverCancelled:
    def is_cancelled(self) -> bool:
        return False


class _Redactor:
    def __init__(self, variables: tuple[ResolvedDeploymentVariable, ...]) -> None:
        values = {
            value
            for variable in variables
            for value in (
                *((variable.value,) if variable.sensitive else ()),
                *variable.redaction_values,
            )
            if value
        }
        self._values = tuple(sorted(values, key=len, reverse=True))

    def redact(self, detail: str) -> str:
        redacted = detail
        for value in self._values:
            redacted = redacted.replace(value, "***")
        return redacted[:2000]


@dataclass(frozen=True, slots=True)
class _DeploymentCommandOutcome:
    succeeded: bool
    detail: str | None = None
    cancelled: bool = False
    timed_out: bool = False


_BUILD_COMMAND_TIMEOUT_SECONDS = 900.0
_DOWN_COMMAND_TIMEOUT_SECONDS = 120.0
_COMPOSE_COMMAND_TIMEOUT_PADDING_SECONDS = 30.0


_COMPOSE_NAME_TOKEN = re.compile(r"[^a-z0-9_-]+")


def derive_compose_project_name(workspace_id: str, target_id: str, ref: str = "") -> str:
    """Compose project identity：workspace + target 基础上纳入 ref，使不同 ref 的并存部署互不冲突。

    ref 为空时保持历史派生（workspace+target），兼容既有部署。
    """
    identity = f"{workspace_id}:{target_id}" if not ref else f"{workspace_id}:{target_id}:{ref}"
    slug_source = f"{workspace_id}-{target_id}" if not ref else f"{workspace_id}-{target_id}-{ref}"
    slug = _COMPOSE_NAME_TOKEN.sub("-", slug_source.lower()).strip("-_")
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    prefix = slug[:42] or "target"
    return f"tgl-{prefix}-{digest}"


class ComposeDeploymentWorkflow:
    def __init__(
        self,
        store: DeploymentStore,
        runner: ComposeDeploymentRunner,
        environment_resolver: DeploymentEnvironmentResolver,
        probe_runner: DeploymentProbeRunner,
        runtime_inspector: DeploymentRuntimeInspector,
    ) -> None:
        self._store = store
        self._runner = runner
        self._environment_resolver = environment_resolver
        self._probe_runner = probe_runner
        self._runtime_inspector = runtime_inspector

    def deploy(
        self,
        intent: DeploymentIntent,
        cancellation: DeploymentCancellation | None = None,
    ) -> DeploymentRevision:
        signal = cancellation or _NeverCancelled()
        now = datetime.now(UTC)
        proposed = DeploymentRevision(
            intent=intent,
            project_name=derive_compose_project_name(intent.workspace_id, intent.target_id),
            previous_revision_id=None,
            status=DeploymentStatus.PLANNED,
            created_at=now,
            updated_at=now,
        )
        record = self._store.begin(proposed)
        if signal.is_cancelled():
            return self._fail(
                record, "DEPLOYMENT_CANCELLED", "Deployment cancelled before image build"
            )

        record = self._transition(record, DeploymentStatus.BUILDING)
        build = self._run_command(
            record,
            DeploymentAction.BUILD,
            self._build_command(record),
            cancellation=signal,
        )
        if build.cancelled:
            return self._fail(
                record, "DEPLOYMENT_CANCELLED", "Image build cancelled and process tree terminated"
            )
        if build.timed_out:
            return self._fail(record, "DEPLOYMENT_BUILD_TIMED_OUT", build.detail)
        if not build.succeeded:
            return self._fail(record, "DEPLOYMENT_BUILD_FAILED", build.detail)
        if signal.is_cancelled():
            return self._fail(
                record, "DEPLOYMENT_CANCELLED", "Deployment cancelled before applying replacement"
            )

        record = self._transition(record, DeploymentStatus.APPLYING)
        apply = self._run_command(
            record,
            DeploymentAction.APPLY,
            self._apply_command(record),
            cancellation=signal,
        )
        if apply.cancelled:
            return self._recover(
                record,
                "DEPLOYMENT_CANCELLED",
                "Compose apply cancelled and process tree terminated",
            )
        if apply.timed_out:
            return self._recover(record, "DEPLOYMENT_APPLY_TIMED_OUT", apply.detail)
        if not apply.succeeded:
            return self._recover(record, "DEPLOYMENT_APPLY_FAILED", apply.detail)
        if signal.is_cancelled():
            return self._recover(
                record, "DEPLOYMENT_CANCELLED", "Deployment cancelled after applying Compose"
            )

        record = self._transition(record, DeploymentStatus.VERIFYING)
        if signal.is_cancelled():
            return self._recover(
                record, "DEPLOYMENT_CANCELLED", "Deployment cancelled before readiness verification"
            )
        ready, verify_detail = self._verify(record)
        if signal.is_cancelled():
            return self._recover(
                record, "DEPLOYMENT_CANCELLED", "Deployment cancelled after readiness verification"
            )
        if not ready:
            return self._recover(record, "DEPLOYMENT_VERIFY_FAILED", verify_detail)
        return self._activate(record)

    def reconcile_pending(self) -> tuple[DeploymentRevision, ...]:
        return tuple(
            self._reconcile_pending_revision(record)
            for record in self._store.list_pending_revisions()
        )

    def reconcile_revision(self, revision_id: str) -> DeploymentRevision:
        record = self._store.get_revision(revision_id)
        if record is None:
            raise DeploymentRevisionNotFoundError()
        if record.status in TARGET_EXCLUSIVE_DEPLOYMENT_STATUSES:
            return self._reconcile_pending_revision(record)
        if record.status is DeploymentStatus.DEGRADED:
            recovering = self._transition(record, DeploymentStatus.RECOVERING)
            return self._perform_recovery(recovering)
        return record

    def _reconcile_pending_revision(
        self,
        record: DeploymentRevision,
    ) -> DeploymentRevision:
        if record.status in (DeploymentStatus.PLANNED, DeploymentStatus.BUILDING):
            code = (
                "DEPLOYMENT_PLANNED_INTERRUPTED"
                if record.status is DeploymentStatus.PLANNED
                else "DEPLOYMENT_BUILD_INTERRUPTED"
            )
            detail = (
                "Control service interrupted after deployment intent persisted, before image build"
                if record.status is DeploymentStatus.PLANNED
                else "Control service interrupted during image build; "
                "build was not retried automatically"
            )
            return self._fail(record, code, detail)
        return self._reconcile_runtime(record)

    def _reconcile_runtime(self, record: DeploymentRevision) -> DeploymentRevision:
        try:
            runtime = self._runtime_inspector.inspect(
                record.intent.workspace_id,
                record.intent.target_id,
            )
        except Exception as error:
            return self._recover(
                record,
                "DEPLOYMENT_INTERRUPTED",
                f"Runtime inspection failed: {type(error).__name__}",
            )

        if (
            record.status in (DeploymentStatus.APPLYING, DeploymentStatus.VERIFYING)
            and runtime.target_present
            and runtime.revision_id == record.intent.revision_id
            and runtime.probe_ready
        ):
            return self._activate(record)
        if (
            record.previous_revision_id is not None
            and runtime.target_present
            and runtime.revision_id == record.previous_revision_id
            and runtime.probe_ready
        ):
            return self._reconcile_rolled_back(
                record, "Previous revision restored and readiness passed"
            )
        if record.previous_revision_id is None and not runtime.target_present:
            return self._reconcile_rolled_back(
                record, "First deployment reverted to no application containers"
            )
        return self._recover(
            record,
            "DEPLOYMENT_INTERRUPTED",
            "Docker revision label and readiness cannot prove active or rollback completed",
        )

    def _reconcile_rolled_back(
        self,
        record: DeploymentRevision,
        detail: str,
    ) -> DeploymentRevision:
        recovering = self._ensure_recovering(record, "DEPLOYMENT_INTERRUPTED", detail)
        return self._transition(
            recovering,
            DeploymentStatus.ROLLED_BACK,
            recovery_detail=detail,
        )

    def _ensure_recovering(
        self,
        record: DeploymentRevision,
        failure_code: str,
        failure_detail: str,
    ) -> DeploymentRevision:
        if record.status is DeploymentStatus.RECOVERING:
            return record
        return self._transition(
            record,
            DeploymentStatus.RECOVERING,
            failure_code=failure_code,
            failure_detail=failure_detail,
        )

    def _recover(
        self,
        record: DeploymentRevision,
        failure_code: str,
        failure_detail: str | None,
    ) -> DeploymentRevision:
        recovering = self._ensure_recovering(
            record,
            failure_code,
            failure_detail or "Compose deployment failed; recovering previous revision",
        )
        return self._perform_recovery(recovering)

    def _perform_recovery(self, recovering: DeploymentRevision) -> DeploymentRevision:
        if recovering.previous_revision_id is None:
            rollback = self._run_command(
                recovering,
                DeploymentAction.DOWN,
                self._down_command(recovering),
            )
            if rollback.succeeded:
                return self._transition(
                    recovering,
                    DeploymentStatus.ROLLED_BACK,
                    recovery_detail="First deployment reverted to no application containers",
                )
            return self._transition(
                recovering,
                DeploymentStatus.DEGRADED,
                recovery_detail=rollback.detail
                or "Failed to remove application containers for first deployment",
            )

        previous = self._store.get_revision(recovering.previous_revision_id)
        if previous is None:
            return self._transition(
                recovering,
                DeploymentStatus.DEGRADED,
                recovery_detail="Previous revision record not found; recovery impossible",
            )
        rollback = self._run_command(
            previous,
            DeploymentAction.ROLLBACK,
            self._apply_command(previous),
        )
        if not rollback.succeeded:
            return self._transition(
                recovering,
                DeploymentStatus.DEGRADED,
                recovery_detail=rollback.detail or "Previous revision Compose recovery failed",
            )
        previous_ready, previous_detail = self._verify(previous)
        if not previous_ready:
            return self._transition(
                recovering,
                DeploymentStatus.DEGRADED,
                recovery_detail=previous_detail
                or "Previous revision readiness verification failed",
            )
        return self._transition(
            recovering,
            DeploymentStatus.ROLLED_BACK,
            recovery_detail="Previous revision restored and readiness passed",
        )

    def _verify(self, record: DeploymentRevision) -> tuple[bool, str | None]:
        try:
            environment = self._environment_resolver.resolve(record, DeploymentAction.VERIFY)
        except Exception as error:
            return False, f"Readiness environment resolution failed: {type(error).__name__}"
        redactor = _Redactor(environment)
        try:
            result = self._probe_runner.verify(record.intent.probe, environment)
        except Exception as error:
            return False, f"Readiness probe failed: {type(error).__name__}"
        if result.ready:
            return True, None
        return False, redactor.redact(result.detail or "Explicit readiness probe did not pass")

    def _run_command(
        self,
        record: DeploymentRevision,
        action: DeploymentAction,
        argv: tuple[str, ...],
        *,
        cancellation: DeploymentCancellation | None = None,
    ) -> _DeploymentCommandOutcome:
        try:
            environment = self._environment_resolver.resolve(record, action)
        except Exception as error:
            return _DeploymentCommandOutcome(
                False,
                f"Runtime environment resolution failed: {type(error).__name__}",
            )
        redactor = _Redactor(environment)
        try:
            result = self._runner.run(
                argv=argv,
                cwd=record.intent.checkout_path,
                environment=environment,
                cancellation=cancellation,
                timeout_seconds=self._command_timeout_seconds(record, action),
            )
        except Exception as error:
            return _DeploymentCommandOutcome(
                False, f"Compose runner failed: {type(error).__name__}"
            )
        if result.cancelled:
            return _DeploymentCommandOutcome(False, cancelled=True)
        if result.timed_out:
            detail = (
                result.stderr
                or result.stdout
                or "Compose command timed out and process tree was terminated"
            )
            return _DeploymentCommandOutcome(False, redactor.redact(detail), timed_out=True)
        if result.return_code == 0:
            return _DeploymentCommandOutcome(True)
        detail = (
            result.stderr
            or result.stdout
            or f"Compose command exited with code {result.return_code}"
        )
        return _DeploymentCommandOutcome(False, redactor.redact(detail))

    @staticmethod
    def _command_timeout_seconds(
        record: DeploymentRevision,
        action: DeploymentAction,
    ) -> float:
        if action is DeploymentAction.BUILD:
            return _BUILD_COMMAND_TIMEOUT_SECONDS
        if action is DeploymentAction.DOWN:
            return _DOWN_COMMAND_TIMEOUT_SECONDS
        return record.intent.wait_timeout_seconds + _COMPOSE_COMMAND_TIMEOUT_PADDING_SECONDS

    def _activate(self, record: DeploymentRevision) -> DeploymentRevision:
        active = replace(
            record,
            status=DeploymentStatus.ACTIVE,
            updated_at=datetime.now(UTC),
            failure_code=None,
            failure_detail=None,
            recovery_detail=None,
        )
        return self._store.activate_revision(active, record.status)

    def _fail(
        self,
        record: DeploymentRevision,
        code: str,
        detail: str | None,
    ) -> DeploymentRevision:
        return self._transition(
            record,
            DeploymentStatus.FAILED,
            failure_code=code,
            failure_detail=detail or "Compose deployment failed",
        )

    def _transition(
        self,
        record: DeploymentRevision,
        status: DeploymentStatus,
        *,
        failure_code: str | None = None,
        failure_detail: str | None = None,
        recovery_detail: str | None = None,
    ) -> DeploymentRevision:
        updated = replace(
            record,
            status=status,
            updated_at=datetime.now(UTC),
            failure_code=(failure_code if failure_code is not None else record.failure_code),
            failure_detail=(
                failure_detail if failure_detail is not None else record.failure_detail
            ),
            recovery_detail=(
                recovery_detail if recovery_detail is not None else record.recovery_detail
            ),
        )
        return self._store.update_revision(updated, record.status)

    @staticmethod
    def _build_command(record: DeploymentRevision) -> tuple[str, ...]:
        return (
            "docker",
            "compose",
            "-f",
            str(record.intent.frozen_compose_path),
            "-p",
            record.project_name,
            "build",
            *record.intent.services,
        )

    @staticmethod
    def _apply_command(record: DeploymentRevision) -> tuple[str, ...]:
        return (
            "docker",
            "compose",
            "-f",
            str(record.intent.frozen_compose_path),
            "-p",
            record.project_name,
            "up",
            "-d",
            "--no-deps",
            "--no-build",
            "--pull",
            "never",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            str(record.intent.wait_timeout_seconds),
            *record.intent.services,
        )

    @staticmethod
    def _down_command(record: DeploymentRevision) -> tuple[str, ...]:
        return (
            "docker",
            "compose",
            "-f",
            str(record.intent.frozen_compose_path),
            "-p",
            record.project_name,
            "down",
        )
