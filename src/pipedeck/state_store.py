from __future__ import annotations

import sqlite3
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import RLock
from typing import Annotated, Literal, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from pipedeck.compose_deployment import (
    DeploymentIntent,
    DeploymentProbe,
    DeploymentRevision,
    DeploymentStatus,
    HttpProbe,
    TargetDeploymentConflictError,
    TcpProbe,
)
from pipedeck.contracts import (
    CommandOwnedPortInjection,
    ConnectionProfile,
    DeploymentEnvironmentSnapshot,
    EnvironmentBinding,
    EnvironmentRecord,
    EnvironmentSource,
    HostEndpoint,
    HostTarget,
    MiddlewareBinding,
    MiddlewareKind,
    PostgresConnectionProfile,
    RepositoryRecord,
    RunEvent,
    RunEventKind,
    RunEventListResponse,
    RunMode,
    RunRecord,
    RunStatus,
    SecretMetadata,
    ServiceCommand,
    WorkspaceInput,
    WorkspacePlanResponse,
    WorkspaceRecord,
    WorkspaceService,
    WorkspaceUpdateRequest,
)
from pipedeck.managed_middleware import (
    ManagedResourceRecord,
    ManagedResourceStatus,
    runtime_identity_matches,
)

_SCHEMA_VERSION = 7


class _HttpProbePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["http"]
    url: str
    timeout_seconds: float


class _TcpProbePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["tcp"]
    host: str
    port: int
    timeout_seconds: float


_DeploymentProbePayload = Annotated[
    _HttpProbePayload | _TcpProbePayload,
    Field(discriminator="kind"),
]


class _DeploymentIntentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

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
    probe: _DeploymentProbePayload
    environment_spec: DeploymentEnvironmentSnapshot


class _DeploymentRevisionPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: _DeploymentIntentPayload
    project_name: str
    previous_revision_id: str | None
    status: DeploymentStatus
    created_at: datetime
    updated_at: datetime
    failure_code: str | None = None
    failure_detail: str | None = None
    recovery_detail: str | None = None

    @classmethod
    def from_revision(cls, revision: DeploymentRevision) -> _DeploymentRevisionPayload:
        probe: _HttpProbePayload | _TcpProbePayload
        if isinstance(revision.intent.probe, HttpProbe):
            probe = _HttpProbePayload(
                kind="http",
                url=revision.intent.probe.url,
                timeout_seconds=revision.intent.probe.timeout_seconds,
            )
        else:
            probe = _TcpProbePayload(
                kind="tcp",
                host=revision.intent.probe.host,
                port=revision.intent.probe.port,
                timeout_seconds=revision.intent.probe.timeout_seconds,
            )
        intent = revision.intent
        return cls(
            intent=_DeploymentIntentPayload(
                revision_id=intent.revision_id,
                workspace_id=intent.workspace_id,
                target_id=intent.target_id,
                workspace_revision=intent.workspace_revision,
                source_fingerprint=intent.source_fingerprint,
                target_config_fingerprint=intent.target_config_fingerprint,
                checkout_path=intent.checkout_path,
                frozen_compose_path=intent.frozen_compose_path,
                services=intent.services,
                immutable_images=intent.immutable_images,
                wait_timeout_seconds=intent.wait_timeout_seconds,
                probe=probe,
                environment_spec=intent.environment_spec,
            ),
            project_name=revision.project_name,
            previous_revision_id=revision.previous_revision_id,
            status=revision.status,
            created_at=revision.created_at,
            updated_at=revision.updated_at,
            failure_code=revision.failure_code,
            failure_detail=revision.failure_detail,
            recovery_detail=revision.recovery_detail,
        )

    def to_revision(self) -> DeploymentRevision:
        if isinstance(self.intent.probe, _HttpProbePayload):
            probe: DeploymentProbe = HttpProbe(
                url=self.intent.probe.url,
                timeout_seconds=self.intent.probe.timeout_seconds,
            )
        else:
            probe = TcpProbe(
                host=self.intent.probe.host,
                port=self.intent.probe.port,
                timeout_seconds=self.intent.probe.timeout_seconds,
            )
        return DeploymentRevision(
            intent=DeploymentIntent(
                revision_id=self.intent.revision_id,
                workspace_id=self.intent.workspace_id,
                target_id=self.intent.target_id,
                workspace_revision=self.intent.workspace_revision,
                source_fingerprint=self.intent.source_fingerprint,
                target_config_fingerprint=self.intent.target_config_fingerprint,
                checkout_path=self.intent.checkout_path,
                frozen_compose_path=self.intent.frozen_compose_path,
                services=self.intent.services,
                immutable_images=self.intent.immutable_images,
                wait_timeout_seconds=self.intent.wait_timeout_seconds,
                probe=probe,
                environment_spec=self.intent.environment_spec,
            ),
            project_name=self.project_name,
            previous_revision_id=self.previous_revision_id,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
            failure_code=self.failure_code,
            failure_detail=self.failure_detail,
            recovery_detail=self.recovery_detail,
        )


class _V2WorkspaceService(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: str
    commands: tuple[ServiceCommand, ...] = ()
    environment: tuple[EnvironmentBinding, ...] = ()
    ports: tuple[int, ...] = ()
    connection_profiles: tuple[ConnectionProfile, ...] = ()


class _V2WorkspaceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    revision: int
    created_at: datetime
    updated_at: datetime
    name: str
    mode: RunMode
    services: tuple[_V2WorkspaceService, ...]
    bindings: tuple[MiddlewareBinding, ...] = ()


class StateStoreError(Exception):
    code = "STATE_STORE_ERROR"
    detail = "本地状态存储操作失败"

    def __init__(self) -> None:
        super().__init__()


class UnsupportedSchemaVersionError(StateStoreError):
    code = "STATE_SCHEMA_UNSUPPORTED"

    def __init__(self, version: int) -> None:
        self.detail = f"本地状态 schema 版本 {version} 不受支持"
        super().__init__()


class RepositoryPathConflictError(StateStoreError):
    code = "REPOSITORY_PATH_CONFLICT"

    def __init__(self, path: str) -> None:
        self.detail = f"规范化仓库路径已被其他 Checkout 占用：{path}"
        super().__init__()


class RepositoryInUseError(StateStoreError):
    code = "REPOSITORY_IN_USE"

    def __init__(self, repository_id: str) -> None:
        self.detail = f"Checkout 正被 Workspace 使用：{repository_id}"
        super().__init__()


class ManagedResourceRuntimeConflictError(StateStoreError):
    code = "MANAGED_RESOURCE_RUNTIME_CONFLICT"

    def __init__(self, runtime_id: str) -> None:
        self.detail = f"Docker runtime ID 已绑定到其他 ManagedResource：{runtime_id}"
        super().__init__()


class ManagedResourceIntentConflictError(StateStoreError):
    code = "MANAGED_RESOURCE_INTENT_CONFLICT"

    def __init__(self, resource_id: str) -> None:
        self.detail = f"托管资源 ID 已绑定到不同 intent：{resource_id}"
        super().__init__()


class DeploymentRevisionAlreadyExistsError(StateStoreError):
    code = "DEPLOYMENT_REVISION_EXISTS"

    def __init__(self, revision_id: str) -> None:
        self.detail = f"Deployment revision 已存在：{revision_id}"
        super().__init__()


class DeploymentRevisionNotFoundError(StateStoreError):
    code = "DEPLOYMENT_REVISION_NOT_FOUND"

    def __init__(self, revision_id: str) -> None:
        self.detail = f"Deployment revision 不存在：{revision_id}"
        super().__init__()


class DeploymentTransitionConflictError(StateStoreError):
    code = "DEPLOYMENT_TRANSITION_CONFLICT"

    def __init__(
        self,
        revision_id: str,
        expected: DeploymentStatus,
        actual: DeploymentStatus,
    ) -> None:
        self.detail = (
            f"Deployment revision {revision_id} 状态已变化："
            f"期望 {expected.value}，实际 {actual.value}"
        )
        super().__init__()


class DeploymentImmutableFieldError(StateStoreError):
    code = "DEPLOYMENT_IMMUTABLE_FIELD_CHANGED"

    def __init__(self, revision_id: str) -> None:
        self.detail = f"Deployment revision 的不可变 intent 或 identity 已变化：{revision_id}"
        super().__init__()


class DeploymentActivationRequiredError(StateStoreError):
    code = "DEPLOYMENT_ACTIVATION_REQUIRED"

    def __init__(self, revision_id: str) -> None:
        self.detail = f"Deployment revision 必须通过原子 activate 接口进入 active：{revision_id}"
        super().__init__()


class DeploymentBeginStatusError(StateStoreError):
    code = "DEPLOYMENT_BEGIN_STATUS_INVALID"

    def __init__(self, status: DeploymentStatus) -> None:
        self.detail = f"Deployment begin 只接受 planned，实际为：{status.value}"
        super().__init__()


class DeploymentPreviousRevisionError(StateStoreError):
    code = "DEPLOYMENT_PREVIOUS_REVISION_INVALID"

    def __init__(self, revision_id: str) -> None:
        self.detail = f"Deployment previous active revision 无法原子 supersede：{revision_id}"
        super().__init__()


class SecretAlreadyExistsError(StateStoreError):
    code = "SECRET_ALREADY_EXISTS"

    def __init__(self, secret_id: str) -> None:
        self.detail = f"Secret metadata 已存在：{secret_id}"
        super().__init__()


class SecretNotFoundError(StateStoreError):
    code = "SECRET_NOT_FOUND"

    def __init__(self, secret_id: str) -> None:
        self.detail = f"Secret metadata 不存在：{secret_id}"
        super().__init__()


class SecretVersionConflictError(StateStoreError):
    code = "SECRET_VERSION_CONFLICT"

    def __init__(self, secret_id: str, expected: int, actual: int) -> None:
        self.detail = f"Secret {secret_id} version 已变化：期望 {expected}，实际 {actual}"
        super().__init__()


class SecretInUseError(StateStoreError):
    code = "SECRET_IN_USE"

    def __init__(self, secret_id: str, workspace_ids: tuple[str, ...]) -> None:
        joined = ", ".join(workspace_ids)
        self.detail = f"Secret {secret_id} 正被 Workspace 使用：{joined}"
        super().__init__()


class WorkspaceNotFoundError(StateStoreError):
    code = "WORKSPACE_NOT_FOUND"

    def __init__(self, workspace_id: str) -> None:
        self.detail = f"Workspace 不存在：{workspace_id}"
        super().__init__()


class WorkspaceAlreadyExistsError(StateStoreError):
    code = "WORKSPACE_ALREADY_EXISTS"

    def __init__(self, workspace_id: str) -> None:
        self.detail = f"Workspace ID 已存在：{workspace_id}"
        super().__init__()


class WorkspaceRevisionConflictError(StateStoreError):
    code = "WORKSPACE_REVISION_CONFLICT"

    def __init__(self, workspace_id: str, expected: int, actual: int) -> None:
        self.detail = f"Workspace {workspace_id} revision 已变化：期望 {expected}，实际 {actual}"
        super().__init__()


class WorkspaceInUseError(StateStoreError):
    code = "WORKSPACE_IN_USE"

    def __init__(self, workspace_id: str) -> None:
        self.detail = f"Workspace 已有 Plan 或 Run 历史，不能删除：{workspace_id}"
        super().__init__()


class PlanIdentityRequiredError(StateStoreError):
    code = "PLAN_IDENTITY_REQUIRED"
    detail = "持久化 Plan 必须包含 plan、workspace、revision 和指纹"


class PlanNotFoundError(StateStoreError):
    code = "PLAN_NOT_FOUND"

    def __init__(self, plan_id: str) -> None:
        self.detail = f"Plan 不存在：{plan_id}"
        super().__init__()


class PlanConflictError(StateStoreError):
    code = "PLAN_IMMUTABLE_CONFLICT"

    def __init__(self, plan_id: str) -> None:
        self.detail = f"相同 ID 的 Plan 已存在且内容不同：{plan_id}"
        super().__init__()


class PlanWorkspaceRevisionError(StateStoreError):
    code = "PLAN_WORKSPACE_REVISION_STALE"

    def __init__(self, plan_id: str) -> None:
        self.detail = f"Plan 对应的 Workspace revision 已失效：{plan_id}"
        super().__init__()


class RunNotFoundError(StateStoreError):
    code = "RUN_NOT_FOUND"

    def __init__(self, run_id: str) -> None:
        self.detail = f"Run 不存在：{run_id}"
        super().__init__()


class RunAlreadyExistsError(StateStoreError):
    code = "RUN_ALREADY_EXISTS"

    def __init__(self, run_id: str) -> None:
        self.detail = f"Run ID 已存在：{run_id}"
        super().__init__()


class RunStateInvariantError(StateStoreError):
    code = "RUN_STATE_INVARIANT_INVALID"

    def __init__(self, run_id: str, status: RunStatus) -> None:
        self.detail = f"Run {run_id} 的时间字段与 {status.value} 状态不一致"
        super().__init__()


class RunIdempotencyConflictError(StateStoreError):
    code = "RUN_IDEMPOTENCY_CONFLICT"

    def __init__(self, idempotency_key: str) -> None:
        self.detail = f"幂等键已绑定到不同的 Run 请求：{idempotency_key}"
        super().__init__()


class RunTransitionError(StateStoreError):
    code = "RUN_TRANSITION_INVALID"

    def __init__(self, run_id: str, current: RunStatus, requested: RunStatus) -> None:
        self.detail = f"Run {run_id} 不能从 {current.value} 转为 {requested.value}"
        super().__init__()


class RunImmutableFieldError(StateStoreError):
    code = "RUN_IMMUTABLE_FIELD_CHANGED"

    def __init__(self, run_id: str) -> None:
        self.detail = f"Run 的不可变身份字段发生变化：{run_id}"
        super().__init__()


def _roundtrip_json[ModelT: BaseModel](value: ModelT, model_type: type[ModelT]) -> str:
    payload = value.model_dump_json()
    model_type.model_validate_json(payload)
    return payload


def _payload_from_row(row: tuple[object, ...] | None) -> str | None:
    if row is None:
        return None
    return cast(str, row[0])


def _deployment_json(revision: DeploymentRevision) -> str:
    payload = _DeploymentRevisionPayload.from_revision(revision).model_dump_json()
    _DeploymentRevisionPayload.model_validate_json(payload).to_revision()
    return payload


def _deployment_from_json(payload: str) -> DeploymentRevision:
    return _DeploymentRevisionPayload.model_validate_json(payload).to_revision()


class StateStore:
    def __init__(self, path: Path) -> None:
        self._path = path.expanduser().resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._connection = sqlite3.connect(
            self._path, check_same_thread=False, isolation_level=None
        )
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._initialize_schema()
        self.interrupt_active_runs()

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> StateStore:
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        self.close()

    def _initialize_schema(self) -> None:
        with self._lock:
            version_row = self._connection.execute("PRAGMA user_version").fetchone()
            version = int(cast(int, version_row[0])) if version_row is not None else 0
            if version not in {0, 1, 2, 3, 4, 5, 6, _SCHEMA_VERSION}:
                raise UnsupportedSchemaVersionError(version)
            if version == _SCHEMA_VERSION:
                return
            if version == 1:
                self._connection.executescript(
                    """
                    BEGIN IMMEDIATE;
                    CREATE TABLE secrets (
                        id TEXT PRIMARY KEY,
                        version INTEGER NOT NULL,
                        payload TEXT NOT NULL
                    );
                    PRAGMA user_version = 2;
                    COMMIT;
                    """
                )
                version = 2
            if version == 2:
                self._migrate_v2_workspaces()
                version = 3
            if version == 3:
                self._migrate_v3_deployments()
                version = 4
            if version == 4:
                self._migrate_v4_managed_resources()
                version = 5
            if version == 5:
                self._migrate_v5_pipeline_runs()
                version = 6
            if version == 6:
                self._migrate_v6_environments()
                version = _SCHEMA_VERSION
                return
            self._connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE repositories (
                    id TEXT PRIMARY KEY,
                    canonical_path TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL
                );
                CREATE TABLE workspaces (
                    id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE managed_resources (
                    id TEXT PRIMARY KEY,
                    runtime_id TEXT UNIQUE,
                    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE plans (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT REFERENCES workspaces(id) ON DELETE RESTRICT,
                    workspace_revision INTEGER,
                    payload TEXT NOT NULL
                );
                CREATE TABLE runs (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT REFERENCES workspaces(id) ON DELETE RESTRICT,
                    plan_id TEXT REFERENCES plans(id) ON DELETE RESTRICT,
                    idempotency_key TEXT UNIQUE,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE run_events (
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (run_id, sequence)
                );
                CREATE TABLE secrets (
                    id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE deployment_revisions (
                    revision_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    previous_revision_id TEXT,
                    payload TEXT NOT NULL
                );
                CREATE UNIQUE INDEX deployment_revisions_pending_target
                ON deployment_revisions (workspace_id, target_id)
                WHERE status IN ('planned', 'building', 'applying', 'verifying', 'recovering');
                CREATE UNIQUE INDEX deployment_revisions_active_target
                ON deployment_revisions (workspace_id, target_id)
                WHERE status = 'active';
                CREATE INDEX deployment_revisions_previous
                ON deployment_revisions (previous_revision_id);
                CREATE TABLE environments (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                    repository_id TEXT NOT NULL,
                    worktree_path TEXT NOT NULL UNIQUE,
                    payload TEXT NOT NULL
                );
                CREATE INDEX environments_by_workspace ON environments (workspace_id);
                PRAGMA user_version = 7;
                COMMIT;
                """
            )

    def _migrate_v2_workspaces(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            rows = self._connection.execute(
                "SELECT id, payload FROM workspaces ORDER BY id"
            ).fetchall()
            for workspace_id, payload in rows:
                legacy = _V2WorkspaceRecord.model_validate_json(cast(str, payload))
                migrated = WorkspaceRecord(
                    id=legacy.id,
                    revision=legacy.revision,
                    created_at=legacy.created_at,
                    updated_at=legacy.updated_at,
                    name=legacy.name,
                    mode=legacy.mode,
                    services=tuple(
                        WorkspaceService(
                            project_id=service.project_id,
                            commands=service.commands,
                            environment=service.environment,
                            connection_profiles=service.connection_profiles,
                            execution_target=HostTarget(
                                endpoints=tuple(
                                    HostEndpoint(
                                        name=f"port-{port}",
                                        host_port=port,
                                        injection=CommandOwnedPortInjection(kind="command-owned"),
                                    )
                                    for port in service.ports
                                )
                            ),
                        )
                        for service in legacy.services
                    ),
                    bindings=legacy.bindings,
                )
                migrated_payload = _roundtrip_json(migrated, WorkspaceRecord)
                self._connection.execute(
                    "UPDATE workspaces SET payload = ? WHERE id = ?",
                    (migrated_payload, cast(str, workspace_id)),
                )
            self._connection.execute("PRAGMA user_version = 3")
            self._connection.execute("COMMIT")
        except Exception:
            self._connection.execute("ROLLBACK")
            raise

    def _migrate_v3_deployments(self) -> None:
        self._connection.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE deployment_revisions (
                revision_id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                target_id TEXT NOT NULL,
                status TEXT NOT NULL,
                previous_revision_id TEXT,
                payload TEXT NOT NULL
            );
            CREATE UNIQUE INDEX deployment_revisions_pending_target
            ON deployment_revisions (workspace_id, target_id)
            WHERE status IN ('planned', 'building', 'applying', 'verifying', 'recovering');
            CREATE UNIQUE INDEX deployment_revisions_active_target
            ON deployment_revisions (workspace_id, target_id)
            WHERE status = 'active';
            CREATE INDEX deployment_revisions_previous
            ON deployment_revisions (previous_revision_id);
            PRAGMA user_version = 4;
            COMMIT;
            """
        )

    def _migrate_v4_managed_resources(self) -> None:
        managed_table = self._connection.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'managed_resources'
            """
        ).fetchone()
        if managed_table is None:
            self._connection.executescript(
                """
                BEGIN IMMEDIATE;
                CREATE TABLE managed_resources (
                    id TEXT PRIMARY KEY,
                    runtime_id TEXT UNIQUE,
                    workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                PRAGMA user_version = 5;
                COMMIT;
                """
            )
            return
        self._connection.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE managed_resources_v5 (
                id TEXT PRIMARY KEY,
                runtime_id TEXT UNIQUE,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE RESTRICT,
                status TEXT NOT NULL,
                payload TEXT NOT NULL
            );
            INSERT INTO managed_resources_v5 (
                id, runtime_id, workspace_id, status, payload
            )
            SELECT id, runtime_id, workspace_id, 'active', payload
            FROM managed_resources;
            DROP TABLE managed_resources;
            ALTER TABLE managed_resources_v5 RENAME TO managed_resources;
            PRAGMA user_version = 5;
            COMMIT;
            """
        )

    def _migrate_v5_pipeline_runs(self) -> None:
        """放宽 plans/runs 的 workspace 关联为可空，承载仓库级 pipeline plan/run。"""
        self._connection.execute("PRAGMA foreign_keys = OFF")
        try:
            plans_exists = self._table_exists("plans")
            runs_exists = self._table_exists("runs")
            if plans_exists:
                self._connection.executescript(
                    """
                    BEGIN IMMEDIATE;
                    CREATE TABLE plans_v6 (
                        id TEXT PRIMARY KEY,
                        workspace_id TEXT REFERENCES workspaces(id) ON DELETE RESTRICT,
                        workspace_revision INTEGER,
                        payload TEXT NOT NULL
                    );
                    INSERT INTO plans_v6 (id, workspace_id, workspace_revision, payload)
                        SELECT id, workspace_id, workspace_revision, payload FROM plans;
                    DROP TABLE plans;
                    ALTER TABLE plans_v6 RENAME TO plans;
                    COMMIT;
                    """
                )
            else:
                self._connection.executescript(
                    """
                    BEGIN IMMEDIATE;
                    CREATE TABLE plans (
                        id TEXT PRIMARY KEY,
                        workspace_id TEXT REFERENCES workspaces(id) ON DELETE RESTRICT,
                        workspace_revision INTEGER,
                        payload TEXT NOT NULL
                    );
                    COMMIT;
                    """
                )
            runs_columns: set[str] = (
                {str(row[1]) for row in self._connection.execute("PRAGMA table_info(runs)")}
                if runs_exists
                else set()
            )
            runs_copyable = runs_exists and {"plan_id", "idempotency_key"}.issubset(runs_columns)
            copy_statement = ""
            if runs_copyable:
                copy_statement = """
                    INSERT INTO runs_v6 (
                        id, workspace_id, plan_id, idempotency_key, status, payload
                    )
                    SELECT id, workspace_id, plan_id, idempotency_key, status, payload FROM runs;
                """
            elif runs_exists:
                # 极早期结构（缺 plan_id/idempotency_key）按列交集保留历史行
                shared = [
                    c for c in ("id", "workspace_id", "status", "payload") if c in runs_columns
                ]
                copy_statement = f"""
                    INSERT INTO runs_v6 ({", ".join(shared)})
                    SELECT {", ".join(shared)} FROM runs;
                """
            self._connection.executescript(
                f"""
                BEGIN IMMEDIATE;
                CREATE TABLE runs_v6 (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT REFERENCES workspaces(id) ON DELETE RESTRICT,
                    plan_id TEXT REFERENCES plans(id) ON DELETE RESTRICT,
                    idempotency_key TEXT UNIQUE,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                {copy_statement}
                DROP TABLE runs;
                ALTER TABLE runs_v6 RENAME TO runs;
                PRAGMA user_version = 6;
                COMMIT;
                """
            )
        except Exception:
            self._connection.execute("ROLLBACK")
            self._connection.execute("PRAGMA foreign_keys = ON")
            raise
        self._connection.execute("PRAGMA foreign_keys = ON")

    def upsert_environment(self, payload: EnvironmentRecord) -> EnvironmentRecord:
        encoded = _roundtrip_json(payload, EnvironmentRecord)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute(
                    """
                    INSERT INTO environments
                        (id, workspace_id, repository_id, worktree_path, payload)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        repository_id = excluded.repository_id,
                        worktree_path = excluded.worktree_path,
                        payload = excluded.payload
                    """,
                    (
                        payload.id,
                        payload.workspace_id,
                        payload.repository_id,
                        payload.worktree_path,
                        encoded,
                    ),
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return EnvironmentRecord.model_validate_json(encoded)

    def get_environment(self, environment_id: str) -> EnvironmentRecord | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload FROM environments WHERE id = ?", (environment_id,)
            ).fetchone()
        return EnvironmentRecord.model_validate_json(row[0]) if row is not None else None

    def list_environments(self, workspace_id: str | None = None) -> tuple[EnvironmentRecord, ...]:
        with self._lock:
            if workspace_id is None:
                rows = self._connection.execute("SELECT payload FROM environments").fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT payload FROM environments WHERE workspace_id = ?",
                    (workspace_id,),
                ).fetchall()
        records = tuple(EnvironmentRecord.model_validate_json(cast(str, row[0])) for row in rows)
        # environments 表的 created_at 在 payload JSON 内(表无该列),在 Python 侧排序
        return tuple(sorted(records, key=lambda record: record.created_at))

    def delete_environment(self, environment_id: str) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._connection.execute("DELETE FROM environments WHERE id = ?", (environment_id,))
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def _migrate_v6_environments(self) -> None:
        if self._table_exists("environments"):
            self._connection.executescript("PRAGMA user_version = 7;")
            return
        self._connection.executescript(
            """
            BEGIN IMMEDIATE;
            CREATE TABLE environments (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
                repository_id TEXT NOT NULL,
                worktree_path TEXT NOT NULL UNIQUE,
                payload TEXT NOT NULL
            );
            CREATE INDEX environments_by_workspace ON environments (workspace_id);
            PRAGMA user_version = 7;
            COMMIT;
            """
        )

    def _table_exists(self, name: str) -> bool:
        return (
            self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)
            ).fetchone()
            is not None
        )

    def begin(self, revision: DeploymentRevision) -> DeploymentRevision:
        if revision.status is not DeploymentStatus.PLANNED:
            raise DeploymentBeginStatusError(revision.status)
        revision_id = revision.intent.revision_id
        workspace_id = revision.intent.workspace_id
        target_id = revision.intent.target_id
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                if (
                    self._connection.execute(
                        "SELECT 1 FROM deployment_revisions WHERE revision_id = ?",
                        (revision_id,),
                    ).fetchone()
                    is not None
                ):
                    raise DeploymentRevisionAlreadyExistsError(revision_id)
                pending = self._connection.execute(
                    """
                    SELECT 1 FROM deployment_revisions
                    WHERE workspace_id = ? AND target_id = ?
                    AND status IN ('planned', 'building', 'applying', 'verifying', 'recovering')
                    """,
                    (workspace_id, target_id),
                ).fetchone()
                if pending is not None:
                    raise TargetDeploymentConflictError()
                active_payload = _payload_from_row(
                    self._connection.execute(
                        """
                        SELECT payload FROM deployment_revisions
                        WHERE workspace_id = ? AND target_id = ? AND status = ?
                        """,
                        (workspace_id, target_id, DeploymentStatus.ACTIVE.value),
                    ).fetchone()
                )
                active = (
                    _deployment_from_json(active_payload) if active_payload is not None else None
                )
                created = replace(
                    revision,
                    previous_revision_id=(
                        active.intent.revision_id if active is not None else None
                    ),
                )
                payload = _deployment_json(created)
                self._connection.execute(
                    """
                    INSERT INTO deployment_revisions (
                        revision_id, workspace_id, target_id, status,
                        previous_revision_id, payload
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        revision_id,
                        workspace_id,
                        target_id,
                        created.status.value,
                        created.previous_revision_id,
                        payload,
                    ),
                )
                self._connection.execute("COMMIT")
                return _deployment_from_json(payload)
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def get_revision(self, revision_id: str) -> DeploymentRevision | None:
        with self._lock:
            payload = _payload_from_row(
                self._connection.execute(
                    "SELECT payload FROM deployment_revisions WHERE revision_id = ?",
                    (revision_id,),
                ).fetchone()
            )
        return _deployment_from_json(payload) if payload is not None else None

    def update_revision(
        self,
        revision: DeploymentRevision,
        expected_status: DeploymentStatus,
    ) -> DeploymentRevision:
        if revision.status is DeploymentStatus.ACTIVE:
            raise DeploymentActivationRequiredError(revision.intent.revision_id)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._deployment_for_update(revision.intent.revision_id)
                self._validate_deployment_update(current, revision, expected_status)
                payload = _deployment_json(revision)
                try:
                    cursor = self._connection.execute(
                        """
                        UPDATE deployment_revisions SET status = ?, payload = ?
                        WHERE revision_id = ? AND status = ?
                        """,
                        (
                            revision.status.value,
                            payload,
                            revision.intent.revision_id,
                            expected_status.value,
                        ),
                    )
                except sqlite3.IntegrityError as error:
                    raise TargetDeploymentConflictError() from error
                if cursor.rowcount != 1:
                    actual = self._deployment_for_update(revision.intent.revision_id)
                    raise DeploymentTransitionConflictError(
                        revision.intent.revision_id,
                        expected_status,
                        actual.status,
                    )
                self._connection.execute("COMMIT")
                return _deployment_from_json(payload)
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def activate_revision(
        self,
        revision: DeploymentRevision,
        expected_status: DeploymentStatus,
    ) -> DeploymentRevision:
        if revision.status is not DeploymentStatus.ACTIVE:
            raise DeploymentActivationRequiredError(revision.intent.revision_id)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._deployment_for_update(revision.intent.revision_id)
                self._validate_deployment_update(current, revision, expected_status)
                if current.previous_revision_id is not None:
                    previous = self._deployment_for_update(current.previous_revision_id)
                    if (
                        previous.status is not DeploymentStatus.ACTIVE
                        or previous.intent.workspace_id != current.intent.workspace_id
                        or previous.intent.target_id != current.intent.target_id
                    ):
                        raise DeploymentPreviousRevisionError(current.previous_revision_id)
                    superseded = replace(
                        previous,
                        status=DeploymentStatus.SUPERSEDED,
                        updated_at=revision.updated_at,
                    )
                    previous_payload = _deployment_json(superseded)
                    previous_cursor = self._connection.execute(
                        """
                        UPDATE deployment_revisions SET status = ?, payload = ?
                        WHERE revision_id = ? AND status = ?
                        """,
                        (
                            DeploymentStatus.SUPERSEDED.value,
                            previous_payload,
                            previous.intent.revision_id,
                            DeploymentStatus.ACTIVE.value,
                        ),
                    )
                    if previous_cursor.rowcount != 1:
                        raise DeploymentPreviousRevisionError(previous.intent.revision_id)
                elif self._active_target_exists(current):
                    raise DeploymentPreviousRevisionError(revision.intent.revision_id)
                payload = _deployment_json(revision)
                cursor = self._connection.execute(
                    """
                    UPDATE deployment_revisions SET status = ?, payload = ?
                    WHERE revision_id = ? AND status = ?
                    """,
                    (
                        DeploymentStatus.ACTIVE.value,
                        payload,
                        revision.intent.revision_id,
                        expected_status.value,
                    ),
                )
                if cursor.rowcount != 1:
                    actual = self._deployment_for_update(revision.intent.revision_id)
                    raise DeploymentTransitionConflictError(
                        revision.intent.revision_id,
                        expected_status,
                        actual.status,
                    )
                self._connection.execute("COMMIT")
                return _deployment_from_json(payload)
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def list_pending_revisions(self) -> tuple[DeploymentRevision, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT payload FROM deployment_revisions
                WHERE status IN ('planned', 'building', 'applying', 'verifying', 'recovering')
                ORDER BY rowid
                """
            ).fetchall()
        return tuple(_deployment_from_json(cast(str, row[0])) for row in rows)

    def list_deployment_revisions(
        self,
        workspace_id: str | None = None,
        target_id: str | None = None,
    ) -> tuple[DeploymentRevision, ...]:
        with self._lock:
            if workspace_id is not None and target_id is not None:
                rows = self._connection.execute(
                    """
                    SELECT payload FROM deployment_revisions
                    WHERE workspace_id = ? AND target_id = ?
                    ORDER BY rowid DESC
                    """,
                    (workspace_id, target_id),
                ).fetchall()
            elif workspace_id is not None:
                rows = self._connection.execute(
                    """
                    SELECT payload FROM deployment_revisions
                    WHERE workspace_id = ? ORDER BY rowid DESC
                    """,
                    (workspace_id,),
                ).fetchall()
            elif target_id is not None:
                rows = self._connection.execute(
                    """
                    SELECT payload FROM deployment_revisions
                    WHERE target_id = ? ORDER BY rowid DESC
                    """,
                    (target_id,),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT payload FROM deployment_revisions ORDER BY rowid DESC"
                ).fetchall()
        return tuple(_deployment_from_json(cast(str, row[0])) for row in rows)

    def _deployment_for_update(self, revision_id: str) -> DeploymentRevision:
        payload = _payload_from_row(
            self._connection.execute(
                "SELECT payload FROM deployment_revisions WHERE revision_id = ?",
                (revision_id,),
            ).fetchone()
        )
        if payload is None:
            raise DeploymentRevisionNotFoundError(revision_id)
        return _deployment_from_json(payload)

    @staticmethod
    def _validate_deployment_update(
        current: DeploymentRevision,
        updated: DeploymentRevision,
        expected_status: DeploymentStatus,
    ) -> None:
        if current.status is not expected_status:
            raise DeploymentTransitionConflictError(
                current.intent.revision_id,
                expected_status,
                current.status,
            )
        if (
            current.intent != updated.intent
            or current.project_name != updated.project_name
            or current.previous_revision_id != updated.previous_revision_id
            or current.created_at != updated.created_at
        ):
            raise DeploymentImmutableFieldError(current.intent.revision_id)

    def _active_target_exists(self, revision: DeploymentRevision) -> bool:
        return (
            self._connection.execute(
                """
                SELECT 1 FROM deployment_revisions
                WHERE workspace_id = ? AND target_id = ? AND status = ?
                AND revision_id != ?
                """,
                (
                    revision.intent.workspace_id,
                    revision.intent.target_id,
                    DeploymentStatus.ACTIVE.value,
                    revision.intent.revision_id,
                ),
            ).fetchone()
            is not None
        )

    def create_secret_metadata(self, metadata: SecretMetadata) -> SecretMetadata:
        payload = _roundtrip_json(metadata, SecretMetadata)
        with self._lock:
            try:
                self._connection.execute(
                    "INSERT INTO secrets (id, version, payload) VALUES (?, ?, ?)",
                    (metadata.id, metadata.version, payload),
                )
            except sqlite3.IntegrityError as error:
                raise SecretAlreadyExistsError(metadata.id) from error
        return SecretMetadata.model_validate_json(payload)

    def get_secret_metadata(self, secret_id: str) -> SecretMetadata | None:
        with self._lock:
            payload = _payload_from_row(
                self._connection.execute(
                    "SELECT payload FROM secrets WHERE id = ?", (secret_id,)
                ).fetchone()
            )
        return SecretMetadata.model_validate_json(payload) if payload is not None else None

    def list_secret_metadata(self) -> tuple[SecretMetadata, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM secrets ORDER BY rowid DESC"
            ).fetchall()
        return tuple(SecretMetadata.model_validate_json(cast(str, row[0])) for row in rows)

    def update_secret_metadata(
        self,
        metadata: SecretMetadata,
        expected_version: int,
    ) -> SecretMetadata:
        payload = _roundtrip_json(metadata, SecretMetadata)
        with self._lock:
            cursor = self._connection.execute(
                """
                UPDATE secrets SET version = ?, payload = ?
                WHERE id = ? AND version = ?
                """,
                (metadata.version, payload, metadata.id, expected_version),
            )
            if cursor.rowcount != 1:
                current = self.get_secret_metadata(metadata.id)
                if current is None:
                    raise SecretNotFoundError(metadata.id)
                raise SecretVersionConflictError(
                    metadata.id,
                    expected_version,
                    current.version,
                )
        return SecretMetadata.model_validate_json(payload)

    def delete_secret_metadata(self, secret_id: str, expected_version: int) -> None:
        with self._lock:
            users = self._secret_reference_users_unlocked(secret_id)
            if users:
                raise SecretInUseError(secret_id, users)
            cursor = self._connection.execute(
                "DELETE FROM secrets WHERE id = ? AND version = ?",
                (secret_id, expected_version),
            )
            if cursor.rowcount != 1:
                current = self.get_secret_metadata(secret_id)
                if current is None:
                    raise SecretNotFoundError(secret_id)
                raise SecretVersionConflictError(secret_id, expected_version, current.version)

    def secret_reference_users(self, secret_id: str) -> tuple[str, ...]:
        with self._lock:
            return self._secret_reference_users_unlocked(secret_id)

    def _secret_reference_users_unlocked(self, secret_id: str) -> tuple[str, ...]:
        users: set[str] = set()
        rows = self._connection.execute("SELECT payload FROM workspaces ORDER BY id").fetchall()
        for row in rows:
            workspace = WorkspaceRecord.model_validate_json(cast(str, row[0]))
            if any(
                self._service_references_secret(service, secret_id)
                for service in workspace.services
            ):
                users.add(workspace.id)
        managed_rows = self._connection.execute(
            "SELECT payload FROM managed_resources ORDER BY id"
        ).fetchall()
        for row in managed_rows:
            resource = ManagedResourceRecord.model_validate_json(cast(str, row[0]))
            if (
                resource.status is not ManagedResourceStatus.REMOVED
                and resource.intent is not None
                and resource.intent.password_secret_ref == secret_id
            ):
                users.add(resource.workspace_id)
        return tuple(sorted(users))

    @staticmethod
    def _service_references_secret(service: WorkspaceService, secret_id: str) -> bool:
        if any(
            binding.source is EnvironmentSource.SECRET_STORE and binding.reference == secret_id
            for binding in service.environment
        ):
            return True
        for profile in service.connection_profiles:
            if isinstance(profile, PostgresConnectionProfile):
                if profile.secret_ref == secret_id:
                    return True
            elif secret_id in {
                profile.access_key_secret_ref,
                profile.secret_key_secret_ref,
            }:
                return True
        return False

    def upsert_repository(self, record: RepositoryRecord) -> RepositoryRecord:
        payload = _roundtrip_json(record, RepositoryRecord)
        canonical_path = str(Path(record.path).expanduser().resolve())
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._connection.execute(
                    """
                    INSERT INTO repositories (id, canonical_path, payload)
                    VALUES (?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        canonical_path = excluded.canonical_path,
                        payload = excluded.payload
                    """,
                    (record.id, canonical_path, payload),
                )
                self._connection.execute("COMMIT")
            except sqlite3.IntegrityError as error:
                self._connection.execute("ROLLBACK")
                raise RepositoryPathConflictError(canonical_path) from error
        return RepositoryRecord.model_validate_json(payload)

    def get_repository(self, repository_id: str) -> RepositoryRecord | None:
        with self._lock:
            payload = _payload_from_row(
                self._connection.execute(
                    "SELECT payload FROM repositories WHERE id = ?", (repository_id,)
                ).fetchone()
            )
        return RepositoryRecord.model_validate_json(payload) if payload is not None else None

    def get_repository_by_path(self, path: Path) -> RepositoryRecord | None:
        canonical_path = str(path.expanduser().resolve())
        with self._lock:
            payload = _payload_from_row(
                self._connection.execute(
                    "SELECT payload FROM repositories WHERE canonical_path = ?",
                    (canonical_path,),
                ).fetchone()
            )
        return RepositoryRecord.model_validate_json(payload) if payload is not None else None

    def list_repositories(self) -> tuple[RepositoryRecord, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT payload FROM repositories ORDER BY canonical_path"
            ).fetchall()
        return tuple(RepositoryRecord.model_validate_json(cast(str, row[0])) for row in rows)

    def delete_repository(self, repository_id: str) -> bool:
        with self._lock:
            workspaces = self.list_workspaces()
            if any(
                service.project_id == repository_id
                for workspace in workspaces
                for service in workspace.services
            ):
                raise RepositoryInUseError(repository_id)
            cursor = self._connection.execute(
                "DELETE FROM repositories WHERE id = ?", (repository_id,)
            )
        return cursor.rowcount > 0

    def upsert_managed_resource(self, record: ManagedResourceRecord) -> ManagedResourceRecord:
        payload = _roundtrip_json(record, ManagedResourceRecord)
        with self._lock:
            try:
                self._connection.execute("BEGIN IMMEDIATE")
                self._workspace_for_update(record.workspace_id)
                self._connection.execute(
                    """
                    INSERT INTO managed_resources (
                        id, runtime_id, workspace_id, status, payload
                    ) VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        runtime_id = excluded.runtime_id,
                        workspace_id = excluded.workspace_id,
                        status = excluded.status,
                        payload = excluded.payload
                    """,
                    (
                        record.id,
                        record.runtime_id,
                        record.workspace_id,
                        record.status.value,
                        payload,
                    ),
                )
                self._connection.execute("COMMIT")
            except sqlite3.IntegrityError as error:
                self._connection.execute("ROLLBACK")
                raise ManagedResourceRuntimeConflictError(record.runtime_id or record.id) from error
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return ManagedResourceRecord.model_validate_json(payload)

    def begin_managed_resource(self, record: ManagedResourceRecord) -> ManagedResourceRecord:
        if record.status is not ManagedResourceStatus.PLANNED or record.runtime_id is not None:
            raise ManagedResourceIntentConflictError(record.id)
        payload = _roundtrip_json(record, ManagedResourceRecord)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._workspace_for_update(record.workspace_id)
                current_payload = _payload_from_row(
                    self._connection.execute(
                        "SELECT payload FROM managed_resources WHERE id = ?",
                        (record.id,),
                    ).fetchone()
                )
                if current_payload is not None:
                    current = ManagedResourceRecord.model_validate_json(current_payload)
                    if not self._same_managed_resource_intent(current, record):
                        raise ManagedResourceIntentConflictError(record.id)
                    self._connection.execute("COMMIT")
                    return current
                self._connection.execute(
                    """
                    INSERT INTO managed_resources (
                        id, runtime_id, workspace_id, status, payload
                    ) VALUES (?, NULL, ?, ?, ?)
                    """,
                    (
                        record.id,
                        record.workspace_id,
                        record.status.value,
                        payload,
                    ),
                )
                self._connection.execute("COMMIT")
                return ManagedResourceRecord.model_validate_json(payload)
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def transition_managed_resource(
        self,
        record: ManagedResourceRecord,
        expected_status: ManagedResourceStatus,
    ) -> ManagedResourceRecord | None:
        payload = _roundtrip_json(record, ManagedResourceRecord)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current_payload = _payload_from_row(
                    self._connection.execute(
                        "SELECT payload FROM managed_resources WHERE id = ?",
                        (record.id,),
                    ).fetchone()
                )
                if current_payload is None:
                    self._connection.execute("ROLLBACK")
                    return None
                current = ManagedResourceRecord.model_validate_json(current_payload)
                if not self._valid_managed_resource_transition(current, record):
                    raise ManagedResourceIntentConflictError(record.id)
                cursor = self._connection.execute(
                    """
                    UPDATE managed_resources
                    SET runtime_id = ?, status = ?, payload = ?
                    WHERE id = ? AND status = ?
                    """,
                    (
                        record.runtime_id,
                        record.status.value,
                        payload,
                        record.id,
                        expected_status.value,
                    ),
                )
                if cursor.rowcount != 1:
                    self._connection.execute("ROLLBACK")
                    return None
                self._connection.execute("COMMIT")
                return ManagedResourceRecord.model_validate_json(payload)
            except sqlite3.IntegrityError as error:
                self._connection.execute("ROLLBACK")
                raise ManagedResourceRuntimeConflictError(record.runtime_id or record.id) from error
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def get_managed_resource(self, resource_id: str) -> ManagedResourceRecord | None:
        with self._lock:
            payload = _payload_from_row(
                self._connection.execute(
                    "SELECT payload FROM managed_resources WHERE id = ?", (resource_id,)
                ).fetchone()
            )
        return ManagedResourceRecord.model_validate_json(payload) if payload is not None else None

    def get_managed_resource_by_runtime_id(self, runtime_id: str) -> ManagedResourceRecord | None:
        matches = tuple(
            record
            for record in self.list_managed_resources()
            if record.runtime_id is not None
            and runtime_identity_matches(record.runtime_id, runtime_id)
        )
        return matches[0] if len(matches) == 1 else None

    def list_managed_resources(
        self, workspace_id: str | None = None
    ) -> tuple[ManagedResourceRecord, ...]:
        with self._lock:
            if workspace_id is None:
                rows = self._connection.execute(
                    "SELECT payload FROM managed_resources ORDER BY id"
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT payload FROM managed_resources
                    WHERE workspace_id = ? ORDER BY id
                    """,
                    (workspace_id,),
                ).fetchall()
        return tuple(ManagedResourceRecord.model_validate_json(cast(str, row[0])) for row in rows)

    def matches_managed_resource(
        self, *, resource_id: str, runtime_id: str, kind: MiddlewareKind
    ) -> bool:
        record = self.get_managed_resource(resource_id)
        return (
            record is not None
            and record.status is ManagedResourceStatus.ACTIVE
            and record.intent is not None
            and record.runtime_id is not None
            and runtime_identity_matches(record.runtime_id, runtime_id)
            and record.kind is kind
        )

    @staticmethod
    def _same_managed_resource_intent(
        current: ManagedResourceRecord,
        requested: ManagedResourceRecord,
    ) -> bool:
        return (
            current.id == requested.id
            and current.name == requested.name
            and current.kind is requested.kind
            and current.workspace_id == requested.workspace_id
            and current.intent == requested.intent
        )

    @classmethod
    def _valid_managed_resource_transition(
        cls,
        current: ManagedResourceRecord,
        requested: ManagedResourceRecord,
    ) -> bool:
        return (
            cls._same_managed_resource_intent(current, requested)
            and current.created_at == requested.created_at
            and (current.runtime_id is None or current.runtime_id == requested.runtime_id)
        )

    def create_workspace(
        self, workspace: WorkspaceInput, workspace_id: str | None = None
    ) -> WorkspaceRecord:
        now = datetime.now(UTC)
        record = WorkspaceRecord(
            id=workspace_id or uuid4().hex,
            revision=1,
            created_at=now,
            updated_at=now,
            name=workspace.name,
            mode=workspace.mode,
            services=workspace.services,
            bindings=workspace.bindings,
        )
        payload = _roundtrip_json(record, WorkspaceRecord)
        with self._lock:
            try:
                self._connection.execute(
                    "INSERT INTO workspaces (id, revision, payload) VALUES (?, ?, ?)",
                    (record.id, record.revision, payload),
                )
            except sqlite3.IntegrityError as error:
                raise WorkspaceAlreadyExistsError(record.id) from error
        return WorkspaceRecord.model_validate_json(payload)

    def list_workspaces(self) -> tuple[WorkspaceRecord, ...]:
        with self._lock:
            rows = self._connection.execute("SELECT payload FROM workspaces ORDER BY id").fetchall()
        return tuple(WorkspaceRecord.model_validate_json(cast(str, row[0])) for row in rows)

    def get_workspace(self, workspace_id: str) -> WorkspaceRecord | None:
        with self._lock:
            payload = _payload_from_row(
                self._connection.execute(
                    "SELECT payload FROM workspaces WHERE id = ?", (workspace_id,)
                ).fetchone()
            )
        return WorkspaceRecord.model_validate_json(payload) if payload is not None else None

    def update_workspace(
        self, workspace_id: str, request: WorkspaceUpdateRequest
    ) -> WorkspaceRecord:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._workspace_for_update(workspace_id)
                if current.revision != request.expected_revision:
                    raise WorkspaceRevisionConflictError(
                        workspace_id, request.expected_revision, current.revision
                    )
                updated = WorkspaceRecord(
                    id=current.id,
                    revision=current.revision + 1,
                    created_at=current.created_at,
                    updated_at=datetime.now(UTC),
                    name=request.name,
                    mode=request.mode,
                    services=request.services,
                    bindings=request.bindings,
                )
                payload = _roundtrip_json(updated, WorkspaceRecord)
                cursor = self._connection.execute(
                    """
                    UPDATE workspaces SET revision = ?, payload = ?
                    WHERE id = ? AND revision = ?
                    """,
                    (updated.revision, payload, workspace_id, request.expected_revision),
                )
                if cursor.rowcount != 1:
                    actual = self._workspace_for_update(workspace_id)
                    raise WorkspaceRevisionConflictError(
                        workspace_id, request.expected_revision, actual.revision
                    )
                self._connection.execute("COMMIT")
                return WorkspaceRecord.model_validate_json(payload)
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def _workspace_for_update(self, workspace_id: str) -> WorkspaceRecord:
        payload = _payload_from_row(
            self._connection.execute(
                "SELECT payload FROM workspaces WHERE id = ?", (workspace_id,)
            ).fetchone()
        )
        if payload is None:
            raise WorkspaceNotFoundError(workspace_id)
        return WorkspaceRecord.model_validate_json(payload)

    def delete_workspace(self, workspace_id: str, expected_revision: int | None = None) -> None:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._workspace_for_update(workspace_id)
                if expected_revision is not None and current.revision != expected_revision:
                    raise WorkspaceRevisionConflictError(
                        workspace_id, expected_revision, current.revision
                    )
                try:
                    self._connection.execute("DELETE FROM workspaces WHERE id = ?", (workspace_id,))
                except sqlite3.IntegrityError as error:
                    raise WorkspaceInUseError(workspace_id) from error
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise

    def save_plan(self, plan: WorkspacePlanResponse) -> WorkspacePlanResponse:
        if (
            plan.plan_id is None
            or plan.config_fingerprint is None
            or plan.source_fingerprint is None
        ):
            raise PlanIdentityRequiredError()
        if plan.workspace_id is None:
            return self._save_standalone_plan(plan)
        payload = _roundtrip_json(plan, WorkspacePlanResponse)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                workspace = self._workspace_for_update(plan.workspace_id)
                if workspace.revision != plan.workspace_revision:
                    raise PlanWorkspaceRevisionError(plan.plan_id)
                existing = _payload_from_row(
                    self._connection.execute(
                        "SELECT payload FROM plans WHERE id = ?", (plan.plan_id,)
                    ).fetchone()
                )
                if existing is not None:
                    result = self._resolve_existing_plan(plan.plan_id, existing, payload)
                    self._connection.execute("COMMIT")
                    return result
                self._connection.execute(
                    """
                    INSERT INTO plans (id, workspace_id, workspace_revision, payload)
                    VALUES (?, ?, ?, ?)
                    """,
                    (plan.plan_id, plan.workspace_id, plan.workspace_revision, payload),
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return WorkspacePlanResponse.model_validate_json(payload)

    def _save_standalone_plan(self, plan: WorkspacePlanResponse) -> WorkspacePlanResponse:
        """仓库级 pipeline plan：无 workspace 归属，按 plan_id（内容指纹）幂等保存。"""
        payload = _roundtrip_json(plan, WorkspacePlanResponse)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = _payload_from_row(
                    self._connection.execute(
                        "SELECT payload FROM plans WHERE id = ?", (plan.plan_id,)
                    ).fetchone()
                )
                if existing is not None:
                    # 同指纹复用已有计划；source 变化由 pipeline 新鲜度校验拦截
                    result = WorkspacePlanResponse.model_validate_json(existing)
                    self._connection.execute("COMMIT")
                    return result
                self._connection.execute(
                    "INSERT INTO plans (id, workspace_id, workspace_revision, payload) "
                    "VALUES (?, NULL, NULL, ?)",
                    (plan.plan_id, payload),
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return WorkspacePlanResponse.model_validate_json(payload)

    @staticmethod
    def _resolve_existing_plan(
        plan_id: str, existing: str, requested: str
    ) -> WorkspacePlanResponse:
        if existing != requested:
            raise PlanConflictError(plan_id)
        return WorkspacePlanResponse.model_validate_json(existing)

    def get_plan(self, plan_id: str) -> WorkspacePlanResponse | None:
        with self._lock:
            payload = _payload_from_row(
                self._connection.execute(
                    "SELECT payload FROM plans WHERE id = ?", (plan_id,)
                ).fetchone()
            )
        return WorkspacePlanResponse.model_validate_json(payload) if payload is not None else None

    def create_run(self, run: RunRecord, idempotency_key: str) -> RunRecord:
        if run.status is not RunStatus.QUEUED:
            raise RunStateInvariantError(run.id, run.status)
        self._validate_run_state(run)
        payload = _roundtrip_json(run, RunRecord)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                existing = self._run_by_idempotency_key(idempotency_key)
                if existing is not None:
                    result = self._resolve_idempotent_run(existing, run, idempotency_key)
                    self._connection.execute("COMMIT")
                    return result
                if self.get_plan(run.plan_id) is None:
                    raise PlanNotFoundError(run.plan_id)
                self._connection.execute(
                    """
                    INSERT INTO runs (id, workspace_id, plan_id, idempotency_key, status, payload)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run.id,
                        run.workspace_id,
                        run.plan_id,
                        idempotency_key,
                        run.status.value,
                        payload,
                    ),
                )
                self._connection.execute("COMMIT")
            except sqlite3.IntegrityError as error:
                self._connection.execute("ROLLBACK")
                raise RunAlreadyExistsError(run.id) from error
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return RunRecord.model_validate_json(payload)

    @staticmethod
    def _resolve_idempotent_run(
        existing: RunRecord, requested: RunRecord, idempotency_key: str
    ) -> RunRecord:
        if (
            existing.workspace_id != requested.workspace_id
            or existing.plan_id != requested.plan_id
            or existing.retry_of != requested.retry_of
        ):
            raise RunIdempotencyConflictError(idempotency_key)
        return existing

    def _run_by_idempotency_key(self, idempotency_key: str) -> RunRecord | None:
        payload = _payload_from_row(
            self._connection.execute(
                "SELECT payload FROM runs WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
        )
        return RunRecord.model_validate_json(payload) if payload is not None else None

    def get_run(self, run_id: str) -> RunRecord | None:
        with self._lock:
            payload = _payload_from_row(
                self._connection.execute(
                    "SELECT payload FROM runs WHERE id = ?", (run_id,)
                ).fetchone()
            )
        return RunRecord.model_validate_json(payload) if payload is not None else None

    def list_runs(self, workspace_id: str | None = None) -> tuple[RunRecord, ...]:
        with self._lock:
            if workspace_id is None:
                rows = self._connection.execute(
                    "SELECT payload FROM runs ORDER BY rowid DESC"
                ).fetchall()
            else:
                rows = self._connection.execute(
                    "SELECT payload FROM runs WHERE workspace_id = ? ORDER BY rowid DESC",
                    (workspace_id,),
                ).fetchall()
        return tuple(RunRecord.model_validate_json(cast(str, row[0])) for row in rows)

    def update_run(self, run: RunRecord, expected_status: RunStatus | None = None) -> RunRecord:
        self._validate_run_state(run)
        payload = _roundtrip_json(run, RunRecord)
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                current = self._run_for_update(run.id)
                if expected_status is not None and current.status is not expected_status:
                    raise RunTransitionError(run.id, current.status, run.status)
                if not self._same_run_identity(current, run):
                    raise RunImmutableFieldError(run.id)
                if current == run:
                    self._connection.execute("COMMIT")
                    return current
                if not self._transition_allowed(current.status, run.status):
                    raise RunTransitionError(run.id, current.status, run.status)
                cursor = self._connection.execute(
                    "UPDATE runs SET status = ?, payload = ? WHERE id = ? AND status = ?",
                    (run.status.value, payload, run.id, current.status.value),
                )
                if cursor.rowcount != 1:
                    actual = self._run_for_update(run.id)
                    raise RunTransitionError(run.id, actual.status, run.status)
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return RunRecord.model_validate_json(payload)

    def _run_for_update(self, run_id: str) -> RunRecord:
        payload = _payload_from_row(
            self._connection.execute("SELECT payload FROM runs WHERE id = ?", (run_id,)).fetchone()
        )
        if payload is None:
            raise RunNotFoundError(run_id)
        return RunRecord.model_validate_json(payload)

    @staticmethod
    def _same_run_identity(current: RunRecord, requested: RunRecord) -> bool:
        return (
            current.id == requested.id
            and current.workspace_id == requested.workspace_id
            and current.workspace_name == requested.workspace_name
            and current.workspace_revision == requested.workspace_revision
            and current.plan_id == requested.plan_id
            and current.mode is requested.mode
            and current.config_fingerprint == requested.config_fingerprint
            and current.source_fingerprint == requested.source_fingerprint
            and current.retry_of == requested.retry_of
            and current.created_at == requested.created_at
        )

    @staticmethod
    def _transition_allowed(current: RunStatus, requested: RunStatus) -> bool:
        if current is requested:
            return current in {RunStatus.QUEUED, RunStatus.RUNNING}
        if current is RunStatus.QUEUED:
            return requested in {
                RunStatus.RUNNING,
                RunStatus.CANCELLED,
                RunStatus.INTERRUPTED,
            }
        if current is RunStatus.RUNNING:
            return requested in {
                RunStatus.SUCCEEDED,
                RunStatus.FAILED,
                RunStatus.CANCELLED,
                RunStatus.INTERRUPTED,
            }
        return False

    @staticmethod
    def _validate_run_state(run: RunRecord) -> None:
        if run.status is RunStatus.QUEUED:
            valid = run.started_at is None and run.finished_at is None
        elif run.status is RunStatus.RUNNING:
            valid = run.started_at is not None and run.finished_at is None
        else:
            valid = run.finished_at is not None
        if not valid:
            raise RunStateInvariantError(run.id, run.status)

    def append_event(
        self,
        *,
        run_id: str,
        kind: RunEventKind,
        message: str,
        step_id: str | None = None,
        project_id: str | None = None,
        created_at: datetime | None = None,
    ) -> RunEvent:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                self._run_for_update(run_id)
                sequence = self._next_event_sequence(run_id)
                event = RunEvent(
                    sequence=sequence,
                    run_id=run_id,
                    created_at=created_at or datetime.now(UTC),
                    kind=kind,
                    step_id=step_id,
                    project_id=project_id,
                    message=message,
                )
                payload = _roundtrip_json(event, RunEvent)
                self._connection.execute(
                    "INSERT INTO run_events (run_id, sequence, payload) VALUES (?, ?, ?)",
                    (run_id, sequence, payload),
                )
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return RunEvent.model_validate_json(payload)

    def _next_event_sequence(self, run_id: str) -> int:
        row = self._connection.execute(
            "SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_events WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        return int(cast(int, row[0])) if row is not None else 1

    def list_events(self, run_id: str, after: int = 0, limit: int = 500) -> RunEventListResponse:
        with self._lock:
            if self.get_run(run_id) is None:
                raise RunNotFoundError(run_id)
            rows = self._connection.execute(
                """
                SELECT payload FROM run_events
                WHERE run_id = ? AND sequence > ?
                ORDER BY sequence LIMIT ?
                """,
                (run_id, after, limit),
            ).fetchall()
        events = tuple(RunEvent.model_validate_json(cast(str, row[0])) for row in rows)
        next_after = events[-1].sequence if events else after
        return RunEventListResponse(events=events, next_after=next_after)

    def interrupt_active_runs(self) -> tuple[RunRecord, ...]:
        interrupted: list[RunRecord] = []
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                rows = self._connection.execute(
                    """
                    SELECT payload FROM runs
                    WHERE status IN (?, ?)
                    ORDER BY rowid
                    """,
                    (RunStatus.QUEUED.value, RunStatus.RUNNING.value),
                ).fetchall()
                for row in rows:
                    current = RunRecord.model_validate_json(cast(str, row[0]))
                    finished_at = datetime.now(UTC)
                    updated = RunRecord(
                        id=current.id,
                        workspace_id=current.workspace_id,
                        workspace_name=current.workspace_name,
                        workspace_revision=current.workspace_revision,
                        plan_id=current.plan_id,
                        mode=current.mode,
                        status=RunStatus.INTERRUPTED,
                        current_step=current.current_step,
                        config_fingerprint=current.config_fingerprint,
                        source_fingerprint=current.source_fingerprint,
                        retry_of=current.retry_of,
                        created_at=current.created_at,
                        started_at=current.started_at,
                        finished_at=finished_at,
                        failure_code="SIDECAR_RESTARTED",
                        failure_detail="控制服务重启，无法继续拥有原运行进程",
                    )
                    payload = _roundtrip_json(updated, RunRecord)
                    self._connection.execute(
                        "UPDATE runs SET status = ?, payload = ? WHERE id = ?",
                        (RunStatus.INTERRUPTED.value, payload, current.id),
                    )
                    event = RunEvent(
                        sequence=self._next_event_sequence(current.id),
                        run_id=current.id,
                        created_at=finished_at,
                        kind=RunEventKind.STATUS,
                        step_id=current.current_step,
                        project_id=None,
                        message="控制服务重启，Run 已标记为 interrupted",
                    )
                    event_payload = _roundtrip_json(event, RunEvent)
                    self._connection.execute(
                        "INSERT INTO run_events (run_id, sequence, payload) VALUES (?, ?, ?)",
                        (event.run_id, event.sequence, event_payload),
                    )
                    interrupted.append(updated)
                self._connection.execute("COMMIT")
            except Exception:
                self._connection.execute("ROLLBACK")
                raise
        return tuple(interrupted)
