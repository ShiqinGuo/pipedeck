from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from threading import RLock
from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from tripguru_local.contracts import MiddlewareKind

POSTGRES_IMAGE = "postgres:18"
POSTGRES_CONTAINER_PORT = 5432

_MANAGED_LABEL = "tripguru.local/managed"
_WORKSPACE_LABEL = "tripguru.local/workspace"
_KIND_LABEL = "tripguru.local/kind"
_RESOURCE_LABEL = "tripguru.local/resource"
_IDENTIFIER_PATTERN = r"^[A-Za-z_][A-Za-z0-9_-]{0,62}$"
_SECRET_REFERENCE_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
_RUNTIME_ID_PATTERN = re.compile(r"^[0-9a-f]{12,64}$")
_ACTIVE_RUNTIME_REQUIRED = "active managed resource requires runtime identity"
_INTENT_KIND_MISMATCH = "managed resource kind must match its provisioning intent"


class ManagedResourceStatus(StrEnum):
    PLANNED = "planned"
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    FAILED = "failed"
    REMOVED = "removed"


class ManagedMiddlewareIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: MiddlewareKind
    image: Literal["postgres:18"] = POSTGRES_IMAGE
    host_port: Annotated[int, Field(ge=1024, le=65535)]
    container_port: Literal[5432] = POSTGRES_CONTAINER_PORT
    username: Annotated[str, Field(pattern=_IDENTIFIER_PATTERN)] = "postgres"
    database: Annotated[str, Field(pattern=_IDENTIFIER_PATTERN)] = "postgres"
    password_secret_ref: Annotated[str, Field(pattern=_SECRET_REFERENCE_PATTERN)]


class ManagedMiddlewareProvisionRequest(BaseModel):
    """Proposed application contract; it is intentionally not wired to HTTP yet."""

    model_config = ConfigDict(extra="forbid")

    workspace_id: Annotated[str, Field(min_length=1, max_length=128)]
    kind: MiddlewareKind = MiddlewareKind.POSTGRES
    host_port: Annotated[int, Field(ge=1024, le=65535)]
    username: Annotated[str, Field(pattern=_IDENTIFIER_PATTERN)] = "postgres"
    database: Annotated[str, Field(pattern=_IDENTIFIER_PATTERN)] = "postgres"
    password_secret_ref: Annotated[str, Field(pattern=_SECRET_REFERENCE_PATTERN)]


class ManagedResourceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(min_length=1, max_length=96)]
    runtime_id: Annotated[str | None, Field(min_length=1, max_length=128)] = None
    name: Annotated[str, Field(min_length=1, max_length=128)]
    kind: MiddlewareKind
    workspace_id: Annotated[str, Field(min_length=1, max_length=128)]
    created_at: datetime
    updated_at: datetime | None = None
    status: ManagedResourceStatus = ManagedResourceStatus.ACTIVE
    intent: ManagedMiddlewareIntent | None = None
    failure_code: Annotated[str | None, Field(min_length=1, max_length=80)] = None

    @model_validator(mode="after")
    def validate_runtime_identity(self) -> ManagedResourceRecord:
        if self.status is ManagedResourceStatus.ACTIVE and self.runtime_id is None:
            raise ValueError(_ACTIVE_RUNTIME_REQUIRED)
        if self.intent is not None and self.intent.kind is not self.kind:
            raise ValueError(_INTENT_KIND_MISMATCH)
        return self


class ManagedResourceListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resources: tuple[ManagedResourceRecord, ...]


def runtime_identity_matches(expected: str, observed: str) -> bool:
    normalized_expected = expected.casefold()
    normalized_observed = observed.casefold()
    if normalized_expected == normalized_observed:
        return True
    if (
        _RUNTIME_ID_PATTERN.fullmatch(normalized_expected) is None
        or _RUNTIME_ID_PATTERN.fullmatch(normalized_observed) is None
    ):
        return False
    shorter, longer = sorted(
        (normalized_expected, normalized_observed),
        key=len,
    )
    return len(shorter) >= 12 and longer.startswith(shorter)


@dataclass(frozen=True, slots=True)
class DockerEnvironmentVariable:
    name: str
    value: str
    sensitive: bool = False


@dataclass(frozen=True, slots=True)
class DockerCommandResult:
    return_code: int
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class DockerLabel:
    name: str
    value: str


@dataclass(frozen=True, slots=True)
class DockerPublishedPort:
    host_ip: str
    host_port: int
    container_port: int
    protocol: str


@dataclass(frozen=True, slots=True)
class DockerContainerInspection:
    runtime_id: str
    name: str
    image: str
    running: bool
    health: str | None
    labels: tuple[DockerLabel, ...]
    published_ports: tuple[DockerPublishedPort, ...]

    def label_value(self, name: str) -> str | None:
        label = next((item for item in self.labels if item.name == name), None)
        return label.value if label is not None else None


class ManagedMiddlewareStore(Protocol):
    def begin_managed_resource(self, record: ManagedResourceRecord) -> ManagedResourceRecord: ...

    def get_managed_resource(self, resource_id: str) -> ManagedResourceRecord | None: ...

    def list_managed_resources(
        self,
        workspace_id: str | None = None,
    ) -> tuple[ManagedResourceRecord, ...]: ...

    def transition_managed_resource(
        self,
        record: ManagedResourceRecord,
        expected_status: ManagedResourceStatus,
    ) -> ManagedResourceRecord | None: ...


class ManagedMiddlewareSecretReader(Protocol):
    def read(self, secret_id: str) -> str: ...


class ManagedMiddlewareDocker(Protocol):
    def host_port_available(self, host_port: int) -> bool: ...

    def run_postgres(
        self,
        record: ManagedResourceRecord,
        password: str,
    ) -> str | None: ...

    def inspect(self, identifier: str) -> DockerContainerInspection | None: ...

    def remove(self, runtime_id: str) -> bool: ...


class DockerCommandRunner(Protocol):
    def run(
        self,
        argv: tuple[str, ...],
        environment: tuple[DockerEnvironmentVariable, ...] = (),
    ) -> DockerCommandResult: ...


class ManagedMiddlewareError(Exception):
    code = "MANAGED_MIDDLEWARE_ERROR"
    detail = "托管中间件操作失败"
    recovery = "检查 Docker 状态后重试"

    def __init__(self) -> None:
        super().__init__()


class UnsupportedManagedMiddlewareKindError(ManagedMiddlewareError):
    code = "MANAGED_MIDDLEWARE_KIND_UNSUPPORTED"
    detail = "首版只允许系统创建 PostgreSQL 18；MinIO 永不由此能力创建或删除"
    recovery = "选择 PostgreSQL，其他中间件继续使用外部实例"


class ManagedMiddlewareNotFoundError(ManagedMiddlewareError):
    code = "MANAGED_MIDDLEWARE_NOT_FOUND"
    recovery = "刷新托管中间件列表"

    def __init__(self, resource_id: str) -> None:
        self.detail = f"托管中间件记录不存在：{resource_id}"
        super().__init__()


class ManagedMiddlewareOwnershipError(ManagedMiddlewareError):
    code = "MANAGED_MIDDLEWARE_OWNERSHIP_MISMATCH"
    detail = "Docker 容器与本地托管意图不匹配，已拒绝接管或删除"
    recovery = "保留现有容器，修改端口或检查本地记录"


class ManagedMiddlewareRemoveError(ManagedMiddlewareError):
    code = "MANAGED_MIDDLEWARE_REMOVE_FAILED"
    detail = "Docker 未能删除精确匹配的托管容器"
    recovery = "检查 Docker 状态后重试删除"


class ManagedMiddlewareLifecycleError(ManagedMiddlewareError):
    code = "MANAGED_MIDDLEWARE_LIFECYCLE_CONFLICT"
    detail = "托管中间件状态已变化，请刷新后重试"
    recovery = "刷新记录并重新执行操作"


class _DockerHealthPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    status: Annotated[str, Field(alias="Status")]


class _DockerStatePayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    running: Annotated[bool, Field(alias="Running")]
    health: Annotated[_DockerHealthPayload | None, Field(alias="Health")] = None


class _DockerConfigPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    image: Annotated[str, Field(alias="Image")]
    # tripguru-ast: ignore[TG-DS001] - Docker owns arbitrary label keys at this boundary.
    labels: Annotated[dict[str, str], Field(alias="Labels")]


class _DockerPortBindingPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    host_ip: Annotated[str, Field(alias="HostIp")]
    host_port: Annotated[str, Field(alias="HostPort")]


class _DockerHostConfigPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # tripguru-ast: ignore[TG-DS001] - Docker owns dynamic "port/protocol" keys.
    port_bindings: Annotated[
        dict[str, tuple[_DockerPortBindingPayload, ...] | None],
        Field(alias="PortBindings"),
    ]


class _DockerInspectPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")

    runtime_id: Annotated[str, Field(alias="Id")]
    name: Annotated[str, Field(alias="Name")]
    config: Annotated[_DockerConfigPayload, Field(alias="Config")]
    state: Annotated[_DockerStatePayload, Field(alias="State")]
    host_config: Annotated[_DockerHostConfigPayload, Field(alias="HostConfig")]


class SubprocessDockerCommandRunner:
    def run(
        self,
        argv: tuple[str, ...],
        environment: tuple[DockerEnvironmentVariable, ...] = (),
    ) -> DockerCommandResult:
        # tripguru-ast: ignore[TG-DS001] - subprocess requires an OS environment mapping.
        child_environment = os.environ.copy()
        for variable in environment:
            child_environment[variable.name] = variable.value
        creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                check=False,
                creationflags=creation_flags,
                encoding="utf-8",
                env=child_environment,
                errors="replace",
                text=True,
                timeout=60,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return DockerCommandResult(
                return_code=1,
                stdout="",
                stderr=type(error).__name__,
            )
        return DockerCommandResult(
            return_code=completed.returncode,
            stdout=(completed.stdout or "").strip(),
            stderr=(completed.stderr or "").strip(),
        )


class DockerCliManagedMiddleware:
    _runtime_id_pattern = re.compile(r"^[0-9a-f]{12,64}$")
    _loopback_bindings = {"", "0.0.0.0", "127.0.0.1", "::", "::1"}

    def __init__(self, runner: DockerCommandRunner) -> None:
        self._runner = runner

    def host_port_available(self, host_port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            try:
                probe.bind(("127.0.0.1", host_port))
            except OSError:
                return False
        return True

    def run_postgres(self, record: ManagedResourceRecord, password: str) -> str | None:
        intent = _required_intent(record)
        labels = _ownership_labels(record)
        environment = (
            DockerEnvironmentVariable("POSTGRES_USER", intent.username),
            DockerEnvironmentVariable("POSTGRES_DB", intent.database),
            DockerEnvironmentVariable("POSTGRES_PASSWORD", password, sensitive=True),
        )
        argv = (
            "docker",
            "container",
            "run",
            "--detach",
            "--name",
            record.name,
            *(
                argument
                for label in labels
                for argument in ("--label", f"{label.name}={label.value}")
            ),
            "--publish",
            f"127.0.0.1:{intent.host_port}:{intent.container_port}/tcp",
            "--env",
            "POSTGRES_USER",
            "--env",
            "POSTGRES_DB",
            "--env",
            "POSTGRES_PASSWORD",
            "--health-cmd",
            "pg_isready -U $POSTGRES_USER -d $POSTGRES_DB",
            "--health-interval",
            "2s",
            "--health-timeout",
            "3s",
            "--health-retries",
            "15",
            intent.image,
        )
        result = self._runner.run(argv, environment)
        runtime_id = result.stdout.strip().casefold()
        if result.return_code != 0 or self._runtime_id_pattern.fullmatch(runtime_id) is None:
            return None
        return runtime_id

    def inspect(self, identifier: str) -> DockerContainerInspection | None:
        result = self._runner.run(("docker", "container", "inspect", identifier))
        if result.return_code != 0:
            return None
        try:
            # tripguru-ast: ignore[TG-DS001] - Docker JSON is validated immediately.
            raw = json.loads(result.stdout)
            rows = TypeAdapter(tuple[_DockerInspectPayload, ...]).validate_python(raw)
        except (json.JSONDecodeError, ValidationError):
            return None
        if len(rows) != 1:
            return None
        row = rows[0]
        return DockerContainerInspection(
            runtime_id=row.runtime_id,
            name=row.name.removeprefix("/"),
            image=row.config.image,
            running=row.state.running,
            health=row.state.health.status.casefold() if row.state.health is not None else None,
            labels=tuple(
                DockerLabel(name=name, value=value)
                for name, value in sorted(row.config.labels.items())
            ),
            published_ports=self._published_ports(row.host_config),
        )

    def remove(self, runtime_id: str) -> bool:
        result = self._runner.run(("docker", "container", "rm", "--force", runtime_id))
        return result.return_code == 0

    @classmethod
    def _published_ports(
        cls,
        host_config: _DockerHostConfigPayload,
    ) -> tuple[DockerPublishedPort, ...]:
        ports: list[DockerPublishedPort] = []
        for identity, bindings in host_config.port_bindings.items():
            container_text, separator, protocol = identity.partition("/")
            if separator != "/" or not container_text.isdigit() or bindings is None:
                continue
            for binding in bindings:
                if binding.host_ip not in cls._loopback_bindings or not binding.host_port.isdigit():
                    continue
                ports.append(
                    DockerPublishedPort(
                        host_ip=binding.host_ip,
                        host_port=int(binding.host_port),
                        container_port=int(container_text),
                        protocol=protocol,
                    )
                )
        return tuple(
            sorted(
                ports,
                key=lambda item: (
                    item.container_port,
                    item.protocol,
                    item.host_port,
                    item.host_ip,
                ),
            )
        )


class ManagedMiddlewareService:
    def __init__(
        self,
        store: ManagedMiddlewareStore,
        docker: ManagedMiddlewareDocker,
        secrets: ManagedMiddlewareSecretReader,
    ) -> None:
        self._store = store
        self._docker = docker
        self._secrets = secrets
        self._lock = RLock()

    def list(self, workspace_id: str | None = None) -> tuple[ManagedResourceRecord, ...]:
        return self._store.list_managed_resources(workspace_id)

    def provision(self, request: ManagedMiddlewareProvisionRequest) -> ManagedResourceRecord:
        self._require_postgres(request.kind)
        intent = ManagedMiddlewareIntent(
            kind=request.kind,
            host_port=request.host_port,
            username=request.username,
            database=request.database,
            password_secret_ref=request.password_secret_ref,
        )
        now = datetime.now(UTC)
        resource_id = derive_managed_resource_id(request.workspace_id, request.kind)
        planned = ManagedResourceRecord(
            id=resource_id,
            name=derive_managed_postgres_name(request.workspace_id),
            kind=request.kind,
            workspace_id=request.workspace_id,
            created_at=now,
            updated_at=now,
            status=ManagedResourceStatus.PLANNED,
            intent=intent,
        )
        with self._lock:
            current = self._store.begin_managed_resource(planned)
            if current.intent != intent:
                raise ManagedMiddlewareLifecycleError()
            return self._reconcile_locked(current)

    def reconcile(self, resource_id: str) -> ManagedResourceRecord:
        with self._lock:
            record = self._required_record(resource_id)
            self._require_postgres(record.kind)
            return self._reconcile_locked(record)

    def delete(self, resource_id: str) -> ManagedResourceRecord:
        with self._lock:
            record = self._required_record(resource_id)
            self._require_postgres(record.kind)
            if record.status is ManagedResourceStatus.REMOVED:
                return record
            inspection = self._inspection_for(record)
            if inspection is None:
                return self._transition(record, ManagedResourceStatus.REMOVED)
            self._validate_ownership(record, inspection)
            if record.runtime_id is None:
                record = self._bind_runtime(record, inspection.runtime_id)
            if not self._docker.remove(inspection.runtime_id):
                raise ManagedMiddlewareRemoveError()
            return self._transition(record, ManagedResourceStatus.REMOVED)

    def remove_managed_runtime(
        self,
        *,
        runtime_id: str,
        kind: MiddlewareKind,
        workspace_id: str,
    ) -> bool:
        with self._lock:
            candidates = tuple(
                record
                for record in self._store.list_managed_resources(workspace_id)
                if record.kind is kind
                and record.runtime_id is not None
                and runtime_identity_matches(record.runtime_id, runtime_id)
            )
            if len(candidates) != 1:
                raise ManagedMiddlewareOwnershipError()
            removed = self.delete(candidates[0].id)
            return removed.status is ManagedResourceStatus.REMOVED

    def _reconcile_locked(self, record: ManagedResourceRecord) -> ManagedResourceRecord:
        if record.status is ManagedResourceStatus.REMOVED:
            return record
        if record.status is ManagedResourceStatus.ACTIVE:
            return self._reconcile_active(record)
        if record.status in {ManagedResourceStatus.PLANNED, ManagedResourceStatus.FAILED}:
            claimed = self._transition_or_none(record, ManagedResourceStatus.PROVISIONING)
            if claimed is None:
                return self._required_record(record.id)
            record = claimed
        return self._ensure_provisioned(record)

    def _reconcile_active(self, record: ManagedResourceRecord) -> ManagedResourceRecord:
        inspection = self._inspection_for(record)
        if inspection is None:
            return self._fail(record, "MANAGED_RUNTIME_MISSING")
        self._validate_ownership(record, inspection)
        if not inspection.running or inspection.health != "healthy":
            return self._fail(record, "MANAGED_RUNTIME_NOT_HEALTHY")
        return record

    def _ensure_provisioned(self, record: ManagedResourceRecord) -> ManagedResourceRecord:
        inspection = self._inspection_for(record)
        if inspection is None:
            record, inspection = self._create_container(record)
            if inspection is None:
                return record
        try:
            self._validate_ownership(record, inspection)
        except ManagedMiddlewareOwnershipError:
            return self._fail(record, "MANAGED_OWNERSHIP_MISMATCH")
        if record.runtime_id is None:
            record = self._bind_runtime(record, inspection.runtime_id)
        if inspection.running and inspection.health == "healthy":
            return self._transition(record, ManagedResourceStatus.ACTIVE)
        if not inspection.running or inspection.health == "unhealthy":
            return self._fail(record, "MANAGED_RUNTIME_NOT_HEALTHY")
        return record

    def _create_container(
        self,
        record: ManagedResourceRecord,
    ) -> tuple[ManagedResourceRecord, DockerContainerInspection | None]:
        intent = _required_intent(record)
        if not self._docker.host_port_available(intent.host_port):
            return self._fail(record, "MANAGED_HOST_PORT_IN_USE"), None
        try:
            password = self._secrets.read(intent.password_secret_ref)
        except Exception:
            return self._fail(record, "MANAGED_SECRET_UNAVAILABLE"), None
        runtime_id = self._docker.run_postgres(record, password)
        if runtime_id is None:
            possible = self._docker.inspect(record.name)
            if possible is None:
                return self._fail(record, "MANAGED_DOCKER_RUN_FAILED"), None
            return record, possible
        inspection = self._docker.inspect(runtime_id)
        if inspection is None:
            bound = self._bind_runtime(record, runtime_id)
            return self._fail(bound, "MANAGED_DOCKER_INSPECT_FAILED"), None
        return record, inspection

    def _inspection_for(self, record: ManagedResourceRecord) -> DockerContainerInspection | None:
        return self._docker.inspect(record.runtime_id or record.name)

    def _bind_runtime(
        self, record: ManagedResourceRecord, runtime_id: str
    ) -> ManagedResourceRecord:
        if record.runtime_id is not None and record.runtime_id != runtime_id:
            raise ManagedMiddlewareOwnershipError()
        if record.runtime_id == runtime_id:
            return record
        updated = ManagedResourceRecord(
            id=record.id,
            runtime_id=runtime_id,
            name=record.name,
            kind=record.kind,
            workspace_id=record.workspace_id,
            created_at=record.created_at,
            updated_at=datetime.now(UTC),
            status=record.status,
            intent=record.intent,
            failure_code=record.failure_code,
        )
        stored = self._store.transition_managed_resource(updated, record.status)
        if stored is None:
            raise ManagedMiddlewareLifecycleError()
        return stored

    def _fail(self, record: ManagedResourceRecord, code: str) -> ManagedResourceRecord:
        updated = ManagedResourceRecord(
            id=record.id,
            runtime_id=record.runtime_id,
            name=record.name,
            kind=record.kind,
            workspace_id=record.workspace_id,
            created_at=record.created_at,
            updated_at=datetime.now(UTC),
            status=ManagedResourceStatus.FAILED,
            failure_code=code,
            intent=record.intent,
        )
        stored = self._store.transition_managed_resource(updated, record.status)
        if stored is None:
            raise ManagedMiddlewareLifecycleError()
        return stored

    def _transition(
        self,
        record: ManagedResourceRecord,
        status: ManagedResourceStatus,
    ) -> ManagedResourceRecord:
        stored = self._transition_or_none(record, status)
        if stored is None:
            raise ManagedMiddlewareLifecycleError()
        return stored

    def _transition_or_none(
        self,
        record: ManagedResourceRecord,
        status: ManagedResourceStatus,
    ) -> ManagedResourceRecord | None:
        updated = ManagedResourceRecord(
            id=record.id,
            runtime_id=record.runtime_id,
            name=record.name,
            kind=record.kind,
            workspace_id=record.workspace_id,
            created_at=record.created_at,
            updated_at=datetime.now(UTC),
            status=status,
            failure_code=None,
            intent=record.intent,
        )
        return self._store.transition_managed_resource(updated, record.status)

    def _required_record(self, resource_id: str) -> ManagedResourceRecord:
        record = self._store.get_managed_resource(resource_id)
        if record is None:
            raise ManagedMiddlewareNotFoundError(resource_id)
        return record

    @staticmethod
    def _require_postgres(kind: MiddlewareKind) -> None:
        if kind is not MiddlewareKind.POSTGRES:
            raise UnsupportedManagedMiddlewareKindError()

    @staticmethod
    def _validate_ownership(
        record: ManagedResourceRecord,
        inspection: DockerContainerInspection,
    ) -> None:
        intent = _required_intent(record)
        expected_labels = _ownership_labels(record)
        expected_port = next(
            (
                port
                for port in inspection.published_ports
                if port.host_port == intent.host_port
                and port.container_port == intent.container_port
                and port.protocol == "tcp"
                and port.host_ip in {"", "0.0.0.0", "127.0.0.1", "::", "::1"}
            ),
            None,
        )
        if (
            (record.runtime_id is not None and inspection.runtime_id != record.runtime_id)
            or inspection.name != record.name
            or inspection.image != intent.image
            or expected_port is None
            or any(inspection.label_value(label.name) != label.value for label in expected_labels)
        ):
            raise ManagedMiddlewareOwnershipError()


def derive_managed_resource_id(workspace_id: str, kind: MiddlewareKind) -> str:
    digest = hashlib.sha256(f"{workspace_id}\x1f{kind.value}".encode()).hexdigest()[:24]
    return f"managed-{kind.value}-{digest}"


def derive_managed_postgres_name(workspace_id: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", workspace_id.casefold()).strip("-")[:28] or "workspace"
    digest = hashlib.sha256(workspace_id.encode()).hexdigest()[:10]
    return f"tripguru-pg-{slug}-{digest}"


def _required_intent(record: ManagedResourceRecord) -> ManagedMiddlewareIntent:
    if record.intent is None or record.intent.kind is not MiddlewareKind.POSTGRES:
        raise UnsupportedManagedMiddlewareKindError()
    return record.intent


def _ownership_labels(record: ManagedResourceRecord) -> tuple[DockerLabel, ...]:
    return (
        DockerLabel(_MANAGED_LABEL, "true"),
        DockerLabel(_WORKSPACE_LABEL, record.workspace_id),
        DockerLabel(_KIND_LABEL, record.kind.value),
        DockerLabel(_RESOURCE_LABEL, record.id),
    )
