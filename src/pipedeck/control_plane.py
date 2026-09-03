from __future__ import annotations

import ctypes
import hashlib
import os
import re
import sys
from contextlib import suppress
from ctypes import wintypes
from datetime import UTC, datetime
from threading import RLock
from typing import Protocol
from urllib.parse import quote
from uuid import uuid4

from pipedeck.catalog import ProjectCatalog
from pipedeck.compose_deployment import DeploymentProbe, HttpProbe, TcpProbe
from pipedeck.contracts import (
    CatalogResponse,
    EnvironmentSource,
    HostTarget,
    HttpReadiness,
    PlanCommand,
    PlanIssue,
    RunRecord,
    RuntimeResource,
    RuntimeResponse,
    SecretMetadata,
    WorkspacePlanResponse,
    WorkspaceRecord,
)
from pipedeck.execution import (
    FreshnessResult,
    LoadedExecutionPlan,
    ResolvedEnvironmentVariable,
)
from pipedeck.planning import ConnectionPlanner
from pipedeck.runtime import DockerRuntime
from pipedeck.state_store import (
    ManagedResourceRecord,
    SecretInUseError,
    SecretNotFoundError,
    SecretVersionConflictError,
    StateStore,
)
from pipedeck.workspace_planning import SavedWorkspacePlanner


class EnvironmentReferenceMissingError(RuntimeError):
    def __init__(self, reference: str) -> None:
        self.reference = reference
        super().__init__()


class SecretServiceError(Exception):
    code = "SECRET_SERVICE_ERROR"
    detail = "系统凭据操作失败"

    def __init__(self) -> None:
        super().__init__()


class SecretStoreUnavailableError(SecretServiceError):
    code = "SECRET_STORE_UNAVAILABLE"
    detail = "Windows Credential Manager 当前不可用"


class SecretCredentialMissingError(SecretServiceError):
    code = "SECRET_MISSING"

    def __init__(self, secret_id: str) -> None:
        self.detail = f"系统凭据不存在：{secret_id}"
        super().__init__()


class SecretCredentialWriteError(SecretServiceError):
    code = "SECRET_WRITE_FAILED"
    detail = "写入 Windows Credential Manager 失败"


class SecretCredentialDeleteError(SecretServiceError):
    code = "SECRET_DELETE_FAILED"
    detail = "删除 Windows Credential Manager 凭据失败"


class SecretValueTooLargeError(SecretServiceError):
    code = "SECRET_VALUE_TOO_LARGE"
    detail = "Secret 的 UTF-16LE 编码超过 Windows Credential Manager 的 2560 bytes 限制"


class SecretStore(Protocol):
    def write(self, secret_id: str, value: str) -> None: ...

    def read(self, secret_id: str) -> str: ...

    def delete(self, secret_id: str) -> None: ...


class _WindowsCredential(ctypes.Structure):
    _fields_ = (
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    )


class WindowsCredentialStore:
    _namespace = "Pipedeck/"
    _generic_type = 1
    _persist_local_machine = 2
    _error_not_found = 1168
    _maximum_blob_bytes = 5 * 512
    _reference_pattern = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")

    def __init__(self) -> None:
        self._library: ctypes.CDLL | None = None
        if sys.platform == "win32":
            try:
                self._library = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
                self._configure_functions(self._library)
            except (AttributeError, OSError):
                self._library = None

    def write(self, secret_id: str, value: str) -> None:
        library = self._available_library()
        target = self._target(secret_id)
        encoded = value.encode("utf-16-le")
        if len(encoded) > self._maximum_blob_bytes:
            raise SecretValueTooLargeError()
        blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
        credential = _WindowsCredential()
        credential.Type = self._generic_type
        credential.TargetName = target
        credential.CredentialBlobSize = len(encoded)
        credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = self._persist_local_machine
        credential.UserName = "Pipedeck"
        if not library.CredWriteW(ctypes.byref(credential), 0):
            raise SecretCredentialWriteError()

    def read(self, secret_id: str) -> str:
        library = self._available_library()
        target = self._target(secret_id)
        pointer = ctypes.POINTER(_WindowsCredential)()
        if not library.CredReadW(target, self._generic_type, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == self._error_not_found:
                raise SecretCredentialMissingError(secret_id)
            raise SecretStoreUnavailableError()
        try:
            credential = pointer.contents
            raw = ctypes.string_at(
                credential.CredentialBlob,
                credential.CredentialBlobSize,
            )
            return raw.decode("utf-16-le")
        finally:
            library.CredFree(pointer)

    def delete(self, secret_id: str) -> None:
        library = self._available_library()
        target = self._target(secret_id)
        if not library.CredDeleteW(target, self._generic_type, 0):
            if ctypes.get_last_error() == self._error_not_found:
                raise SecretCredentialMissingError(secret_id)
            raise SecretCredentialDeleteError()

    def _available_library(self) -> ctypes.CDLL:
        if self._library is None:
            raise SecretStoreUnavailableError()
        return self._library

    @classmethod
    def _target(cls, secret_id: str) -> str:
        if cls._reference_pattern.fullmatch(secret_id) is None:
            raise SecretCredentialMissingError(secret_id)
        target = f"{cls._namespace}{secret_id}"
        if not target.startswith(cls._namespace):
            raise SecretCredentialMissingError(secret_id)
        return target

    @staticmethod
    def _configure_functions(library: ctypes.CDLL) -> None:
        library.CredWriteW.argtypes = (ctypes.POINTER(_WindowsCredential), wintypes.DWORD)
        library.CredWriteW.restype = wintypes.BOOL
        library.CredReadW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(_WindowsCredential)),
        )
        library.CredReadW.restype = wintypes.BOOL
        library.CredDeleteW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD)
        library.CredDeleteW.restype = wintypes.BOOL
        library.CredFree.argtypes = (ctypes.c_void_p,)
        library.CredFree.restype = None


class SecretService:
    def __init__(self, store: StateStore, credentials: SecretStore) -> None:
        self._store = store
        self._credentials = credentials
        self._lock = RLock()

    def create(self, name: str, value: str) -> SecretMetadata:
        with self._lock:
            secret_id = uuid4().hex
            now = datetime.now(UTC)
            metadata = SecretMetadata(
                id=secret_id,
                name=name,
                present=True,
                version=1,
                created_at=now,
                updated_at=now,
            )
            self._credentials.write(secret_id, value)
            try:
                return self._store.create_secret_metadata(metadata)
            except Exception:
                self._delete_after_failed_write(secret_id)
                raise

    def list(self) -> tuple[SecretMetadata, ...]:
        return tuple(
            self._with_current_presence(item) for item in self._store.list_secret_metadata()
        )

    def update(self, secret_id: str, expected_version: int, value: str) -> SecretMetadata:
        with self._lock:
            current = self._metadata_with_version(secret_id, expected_version)
            previous_value = self._credentials.read(secret_id)
            self._credentials.write(secret_id, value)
            updated = SecretMetadata(
                id=current.id,
                name=current.name,
                present=True,
                version=current.version + 1,
                created_at=current.created_at,
                updated_at=datetime.now(UTC),
            )
            try:
                return self._store.update_secret_metadata(updated, expected_version)
            except Exception:
                self._credentials.write(secret_id, previous_value)
                raise

    def delete(self, secret_id: str, expected_version: int) -> None:
        with self._lock:
            self._metadata_with_version(secret_id, expected_version)
            users = self._store.secret_reference_users(secret_id)
            if users:
                raise SecretInUseError(secret_id, users)
            try:
                previous_value = self._credentials.read(secret_id)
            except SecretCredentialMissingError:
                previous_value = None
            with suppress(SecretCredentialMissingError):
                self._credentials.delete(secret_id)
            try:
                self._store.delete_secret_metadata(secret_id, expected_version)
            except Exception:
                if previous_value is not None:
                    self._credentials.write(secret_id, previous_value)
                raise

    def read(self, secret_id: str) -> str:
        metadata = self._store.get_secret_metadata(secret_id)
        if metadata is None or not metadata.present:
            raise SecretCredentialMissingError(secret_id)
        return self._credentials.read(secret_id)

    def is_readable(self, secret_id: str) -> bool:
        try:
            self.read(secret_id)
        except (SecretServiceError, SecretNotFoundError):
            return False
        return True

    def _metadata_with_version(self, secret_id: str, expected_version: int) -> SecretMetadata:
        metadata = self._store.get_secret_metadata(secret_id)
        if metadata is None:
            raise SecretNotFoundError(secret_id)
        if metadata.version != expected_version:
            raise SecretVersionConflictError(secret_id, expected_version, metadata.version)
        return metadata

    def _with_current_presence(self, metadata: SecretMetadata) -> SecretMetadata:
        return SecretMetadata(
            id=metadata.id,
            name=metadata.name,
            present=self.is_readable(metadata.id),
            version=metadata.version,
            created_at=metadata.created_at,
            updated_at=metadata.updated_at,
        )

    def _delete_after_failed_write(self, secret_id: str) -> None:
        with suppress(SecretServiceError):
            self._credentials.delete(secret_id)


class FreshRuntimeProvider:
    def __init__(self, runtime: DockerRuntime) -> None:
        self._runtime = runtime

    def snapshot(self) -> RuntimeResponse:
        self._runtime.invalidate()
        return self._runtime.snapshot()


class ConnectionAwareWorkspacePlanner:
    def __init__(
        self,
        planner: SavedWorkspacePlanner,
        connections: ConnectionPlanner,
        secrets: SecretService,
    ) -> None:
        self._planner = planner
        self._connections = connections
        self._secrets = secrets

    def create(
        self,
        workspace: WorkspaceRecord,
        catalog: CatalogResponse,
        runtime: RuntimeResponse,
    ) -> WorkspacePlanResponse:
        base = self._planner.create(workspace, catalog, runtime)
        blockers = [
            issue for issue in base.blockers if issue.code != "ENVIRONMENT_REFERENCE_MISSING"
        ]
        blockers.extend(self._environment_issues(workspace))
        connection_plan = self._connections.analyze(workspace, base.projects, runtime)
        blockers.extend(connection_plan.blockers)
        return WorkspacePlanResponse(
            generated_at=base.generated_at,
            ready=not blockers,
            mode=base.mode,
            projects=base.projects,
            steps=base.steps,
            blockers=tuple(blockers),
            warnings=base.warnings,
            connection_mappings=connection_plan.mappings,
            plan_id=base.plan_id,
            workspace_id=base.workspace_id,
            workspace_revision=base.workspace_revision,
            config_fingerprint=base.config_fingerprint,
            source_fingerprint=base.source_fingerprint,
        )

    def source_fingerprint(
        self,
        workspace: WorkspaceRecord,
        catalog: CatalogResponse,
    ) -> tuple[str, tuple[PlanIssue, ...]]:
        return self._planner.source_fingerprint(workspace, catalog)

    def _environment_issues(self, workspace: WorkspaceRecord) -> tuple[PlanIssue, ...]:
        issues: list[PlanIssue] = []
        for service in workspace.services:
            for binding in service.environment:
                missing = (
                    binding.source is EnvironmentSource.HOST_ENV
                    and (binding.reference is None or binding.reference not in os.environ)
                ) or (
                    binding.source is EnvironmentSource.SECRET_STORE
                    and not self._secrets.is_readable(binding.reference or "")
                )
                if missing:
                    issues.append(
                        PlanIssue(
                            code="ENVIRONMENT_REFERENCE_MISSING",
                            title=f"缺少环境引用 {binding.reference}",
                            detail=f"{binding.name} 的引用当前无法解析",
                            recovery="设置宿主机环境变量或在客户端重新写入 Secret",
                        )
                    )
        return tuple(issues)


class StorePlanLoader:
    def __init__(self, store: StateStore) -> None:
        self._store = store

    def load(self, plan_id: str) -> LoadedExecutionPlan | None:
        plan = self._store.get_plan(plan_id)
        if plan is None:
            return None
        if plan.workspace_id is None:
            name = plan.projects[0].name if plan.projects else "pipeline"
            return LoadedExecutionPlan(plan=plan, workspace_name=name)
        workspace = self._store.get_workspace(plan.workspace_id)
        if workspace is None:
            return None
        return LoadedExecutionPlan(plan=plan, workspace_name=workspace.name)


class WorkspaceEnvironmentResolver:
    def __init__(
        self,
        store: StateStore,
        runtime: DockerRuntime,
        secrets: SecretService,
        connections: ConnectionPlanner,
    ) -> None:
        self._store = store
        self._runtime = runtime
        self._secrets = secrets
        self._connections = connections

    def resolve(
        self,
        plan: WorkspacePlanResponse,
        command: PlanCommand,
    ) -> tuple[ResolvedEnvironmentVariable, ...]:
        if plan.workspace_id is None:
            return ()
        workspace = self._store.get_workspace(plan.workspace_id)
        if workspace is None:
            return ()
        service = next(
            (item for item in workspace.services if item.project_id == command.project_id),
            None,
        )
        if service is None:
            return ()
        resolved: list[ResolvedEnvironmentVariable] = []
        used_names: set[str] = set()
        for variable in command.environment:
            resolved.append(
                ResolvedEnvironmentVariable(
                    name=variable.name,
                    value=variable.value,
                )
            )
            used_names.add(variable.name)
        for binding in service.environment:
            if binding.name in used_names:
                raise EnvironmentReferenceMissingError(binding.name)
            if binding.source is EnvironmentSource.HOST_ENV:
                if binding.reference is None or binding.reference not in os.environ:
                    raise EnvironmentReferenceMissingError(binding.reference or binding.name)
                resolved.append(
                    ResolvedEnvironmentVariable(
                        name=binding.name,
                        value=os.environ[binding.reference],
                        sensitive=True,
                        redaction_values=(
                            os.environ[binding.reference],
                            quote(os.environ[binding.reference], safe=""),
                        ),
                    )
                )
            elif binding.source is EnvironmentSource.SECRET_STORE:
                if binding.reference is None:
                    raise EnvironmentReferenceMissingError(binding.name)
                value = self._secrets.read(binding.reference)
                resolved.append(
                    ResolvedEnvironmentVariable(
                        name=binding.name,
                        value=value,
                        sensitive=True,
                        redaction_values=(value, quote(value, safe="")),
                    )
                )
            elif binding.value is not None:
                resolved.append(
                    ResolvedEnvironmentVariable(
                        name=binding.name,
                        value=binding.value,
                    )
                )
            used_names.add(binding.name)

        if service.connection_profiles:
            self._runtime.invalidate()
            runtime = self._runtime.snapshot()
            connection_values = self._connections.resolve(service, workspace.bindings, runtime)
            for value in connection_values:
                if value.name in used_names:
                    raise EnvironmentReferenceMissingError(value.name)
                used_names.add(value.name)
                resolved.append(
                    ResolvedEnvironmentVariable(
                        name=value.name,
                        value=value.value,
                        sensitive=value.sensitive,
                        redaction_values=value.redaction_values,
                    )
                )
        return tuple(resolved)


class WorkspaceReadinessResolver:
    def __init__(self, store: StateStore) -> None:
        self._store = store

    def resolve(
        self,
        plan: WorkspacePlanResponse,
        command: PlanCommand,
    ) -> DeploymentProbe | None:
        if plan.workspace_id is None:
            return None
        workspace = self._store.get_workspace(plan.workspace_id)
        if workspace is None:
            return None
        service = next(
            (item for item in workspace.services if item.project_id == command.project_id),
            None,
        )
        if service is None or not isinstance(service.execution_target, HostTarget):
            return None
        target = service.execution_target
        readiness = target.readiness
        if readiness is None:
            return None
        endpoint = next(
            (item for item in target.endpoints if item.name == readiness.endpoint),
            None,
        )
        if endpoint is None:
            return None
        if isinstance(readiness, HttpReadiness):
            return HttpProbe(
                url=f"http://127.0.0.1:{endpoint.host_port}{readiness.path}",
                timeout_seconds=target.readiness_timeout,
            )
        return TcpProbe(
            host="127.0.0.1",
            port=endpoint.host_port,
            timeout_seconds=target.readiness_timeout,
        )


class PipelineFreshnessValidator(Protocol):
    """仓库级 pipeline plan 的源码/配置新鲜度校验。"""

    def validate(self, plan: WorkspacePlanResponse) -> FreshnessResult: ...


class CurrentPlanFreshnessValidator:
    def __init__(
        self,
        store: StateStore,
        catalog: ProjectCatalog,
        runtime: DockerRuntime,
        planner: ConnectionAwareWorkspacePlanner,
        pipeline_freshness: PipelineFreshnessValidator | None = None,
    ) -> None:
        self._store = store
        self._catalog = catalog
        self._runtime = runtime
        self._planner = planner
        self._pipeline_freshness = pipeline_freshness

    def validate(self, plan: WorkspacePlanResponse) -> FreshnessResult:
        if plan.workspace_id is None:
            if self._pipeline_freshness is None:
                return FreshnessResult(
                    False, "PLAN_IDENTITY_REQUIRED", "计划缺少工作区身份且未配置 pipeline 校验"
                )
            return self._pipeline_freshness.validate(plan)
        if plan.workspace_revision is None:
            return FreshnessResult(False, "PLAN_IDENTITY_REQUIRED", "计划缺少工作区版本")
        workspace = self._store.get_workspace(plan.workspace_id)
        if workspace is None:
            return FreshnessResult(False, "WORKSPACE_NOT_FOUND", "工作区已不存在")
        if workspace.revision != plan.workspace_revision:
            return FreshnessResult(False, "PLAN_STALE", "工作区配置已变化，请重新预检")
        self._catalog.invalidate()
        self._runtime.invalidate()
        current = self._planner.create(workspace, self._catalog.scan(), self._runtime.snapshot())
        if not current.ready:
            issue = current.blockers[0]
            return FreshnessResult(False, issue.code, issue.detail)
        if (
            current.config_fingerprint != plan.config_fingerprint
            or current.source_fingerprint != plan.source_fingerprint
        ):
            return FreshnessResult(False, "PLAN_STALE", "源码或配置已变化，请重新预检")
        return FreshnessResult(True)


class StoreManagedResourceVerifier:
    def __init__(self, store: StateStore) -> None:
        self._store = store

    def matches(self, resource: RuntimeResource) -> bool:
        if resource.owner_workspace_id is None:
            return False
        record = self._store.get_managed_resource_by_runtime_id(resource.id)
        return (
            record is not None
            and record.workspace_id == resource.owner_workspace_id
            and self._store.matches_managed_resource(
                resource_id=record.id,
                runtime_id=resource.id,
                kind=resource.kind,
            )
        )


class ManagedResourceObserver:
    def __init__(self, store: StateStore, runtime: DockerRuntime) -> None:
        self._store = store
        self._runtime = runtime

    def on_started(self, run: RunRecord, plan: WorkspacePlanResponse) -> None:
        if run.workspace_id is None:
            return
        self._runtime.invalidate()
        snapshot = self._runtime.snapshot()
        for resource in snapshot.resources:
            if not resource.managed or resource.owner_workspace_id != run.workspace_id:
                continue
            stable_id = hashlib.sha256(
                f"{run.workspace_id}:{resource.kind.value}:{resource.name}".encode()
            ).hexdigest()[:24]
            existing = self._store.get_managed_resource(stable_id)
            self._store.upsert_managed_resource(
                ManagedResourceRecord(
                    id=stable_id,
                    runtime_id=resource.id,
                    name=resource.name,
                    kind=resource.kind,
                    workspace_id=run.workspace_id,
                    created_at=existing.created_at if existing is not None else datetime.now(UTC),
                )
            )
