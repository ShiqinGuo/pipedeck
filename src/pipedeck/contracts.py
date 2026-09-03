from __future__ import annotations

import re
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

_LITERAL_ENVIRONMENT_REQUIRED = "literal 环境变量必须且只能提供 value"
_SENSITIVE_ENVIRONMENT_REFERENCE_REQUIRED = "敏感环境变量必须使用 host-env 引用"
_HOST_ENVIRONMENT_REFERENCE_REQUIRED = "host-env 环境变量必须且只能提供 reference"
_SECRET_STORE_REFERENCE_REQUIRED = "secret-store 环境变量必须且只能提供 reference"
_HOST_ENVIRONMENT_REFERENCE_INVALID = "host-env reference 必须是合法的环境变量名"
_SECRET_STORE_REFERENCE_INVALID = "secret-store reference 格式不合法"
_CONNECTION_PROFILE_KIND_DUPLICATED = "同一服务的每类中间件只能配置一个 connection profile"
_WORKSPACE_PROJECT_DUPLICATED = "同一 Workspace 中 project_id 必须唯一"
_RELATIVE_REPOSITORY_PATH_REQUIRED = "容器源路径必须是 checkout 内的相对路径且不能包含 '..'"
_ENVIRONMENT_NAME = r"^[A-Za-z_][A-Za-z0-9_]*$"
_SECRET_REFERENCE = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$"
_TARGET_NAME = r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$"


def _validate_relative_repository_path(value: str) -> str:
    normalized = value.replace("\\", "/")
    if (
        PurePosixPath(normalized).is_absolute()
        or PureWindowsPath(value).is_absolute()
        or ".." in PurePosixPath(normalized).parts
    ):
        raise ValueError(_RELATIVE_REPOSITORY_PATH_REQUIRED)
    return value


class ProjectKind(StrEnum):
    PYTHON_UV = "python-uv"
    VITE_REACT = "vite-react"
    VITE_VUE = "vite-vue"
    NUXT = "nuxt"
    COMPOSE = "compose"
    UNKNOWN = "unknown"


class MiddlewareKind(StrEnum):
    POSTGRES = "postgres"
    REDIS = "redis"
    ELASTICSEARCH = "elasticsearch"
    MINIO = "minio"


class ResourceHealth(StrEnum):
    HEALTHY = "healthy"
    RUNNING = "running"
    UNHEALTHY = "unhealthy"
    STOPPED = "stopped"


class RunMode(StrEnum):
    DEVELOPMENT = "development"
    INTEGRATED = "integrated"


class EnvironmentSource(StrEnum):
    LITERAL = "literal"
    HOST_ENV = "host-env"
    SECRET_STORE = "secret-store"


class EndpointProtocol(StrEnum):
    TCP = "tcp"
    UDP = "udp"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class RunEventKind(StrEnum):
    STATUS = "status"
    STAGE = "stage"
    STDOUT = "stdout"
    STDERR = "stderr"
    SYSTEM = "system"


class PlanStepKind(StrEnum):
    INSPECT = "inspect"
    DEPENDENCIES = "dependencies"
    QUALITY = "quality"
    BUILD = "build"
    DEPLOY = "deploy"
    START = "start"


class ProjectCommand(BaseModel):
    id: str
    label: str
    argv: tuple[str, ...]
    kind: PlanStepKind | None = None
    long_running: bool = False


class ContainerCapabilities(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dockerfile: Annotated[str | None, Field(min_length=1, max_length=512)] = None
    compose_files: tuple[Annotated[str, Field(min_length=1, max_length=512)], ...] = ()

    @field_validator("dockerfile")
    @classmethod
    def validate_dockerfile(cls, value: str | None) -> str | None:
        return _validate_relative_repository_path(value) if value is not None else None

    @field_validator("compose_files")
    @classmethod
    def validate_compose_files(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_validate_relative_repository_path(value) for value in values)


class ProjectSummary(BaseModel):
    id: str
    name: str
    path: str
    kind: ProjectKind
    branch: str
    dirty: bool
    commands: tuple[ProjectCommand, ...]
    requirements: tuple[MiddlewareKind, ...]
    warnings: tuple[str, ...]
    container_capabilities: ContainerCapabilities = ContainerCapabilities()


class CatalogResponse(BaseModel):
    generated_at: datetime
    roots: tuple[str, ...]
    projects: tuple[ProjectSummary, ...]
    errors: tuple[str, ...]


class RuntimeEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: EndpointProtocol
    container_port: Annotated[int, Field(ge=1, le=65535)]
    host: Literal["127.0.0.1"] = "127.0.0.1"
    host_port: Annotated[int, Field(ge=1, le=65535)]


class RuntimeResource(BaseModel):
    id: str
    name: str
    kind: MiddlewareKind
    image: str
    state: str
    status_text: str
    health: ResourceHealth
    managed: bool
    protected: bool
    ports: str
    endpoints: tuple[RuntimeEndpoint, ...] = ()
    owner_workspace_id: str | None = None


class RuntimeResponse(BaseModel):
    generated_at: datetime
    docker_available: bool
    resources: tuple[RuntimeResource, ...]
    error_code: str | None = None
    recovery: str | None = None


class RuntimeProcessRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    pid: Annotated[int, Field(ge=1)]
    run_id: str
    project_id: str
    project_name: str
    command_id: str
    label: str
    cwd: str
    argv: tuple[str, ...]
    long_running: bool
    started_at: datetime


class RuntimeProcessListResponse(BaseModel):
    generated_at: datetime
    processes: tuple[RuntimeProcessRecord, ...]


class MiddlewareBinding(BaseModel):
    kind: MiddlewareKind
    resource_id: Annotated[str, Field(min_length=1)]


class EnvironmentBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
    source: EnvironmentSource
    value: str | None = None
    reference: Annotated[str | None, Field(min_length=1, max_length=128)] = None

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        if self.source is EnvironmentSource.LITERAL:
            if self.value is None or self.reference is not None:
                raise ValueError(_LITERAL_ENVIRONMENT_REQUIRED)
            sensitive_tokens = (
                "SECRET",
                "PASSWORD",
                "TOKEN",
                "API_KEY",
                "PRIVATE_KEY",
                "DATABASE_URL",
                "REDIS_URL",
                "DSN",
                "CREDENTIAL",
            )
            if any(token in self.name.upper() for token in sensitive_tokens):
                raise ValueError(_SENSITIVE_ENVIRONMENT_REFERENCE_REQUIRED)
        elif self.source is EnvironmentSource.HOST_ENV:
            if self.reference is None or self.value is not None:
                raise ValueError(_HOST_ENVIRONMENT_REFERENCE_REQUIRED)
            if re.fullmatch(_ENVIRONMENT_NAME, self.reference) is None:
                raise ValueError(_HOST_ENVIRONMENT_REFERENCE_INVALID)
        else:
            if self.reference is None or self.value is not None:
                raise ValueError(_SECRET_STORE_REFERENCE_REQUIRED)
            if re.fullmatch(_SECRET_REFERENCE, self.reference) is None:
                raise ValueError(_SECRET_STORE_REFERENCE_INVALID)
        return self


class PostgresConnectionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[MiddlewareKind.POSTGRES]
    env_var: Annotated[str, Field(pattern=_ENVIRONMENT_NAME)]
    scheme: Annotated[str, Field(pattern=r"^postgresql(?:\+[a-z0-9_]+)?$")] = "postgresql"
    username: Annotated[str, Field(min_length=1, max_length=128)]
    database: Annotated[str, Field(min_length=1, max_length=128)]
    secret_ref: Annotated[str, Field(pattern=_SECRET_REFERENCE)]


class MinioConnectionProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal[MiddlewareKind.MINIO]
    endpoint_env: Annotated[str, Field(pattern=_ENVIRONMENT_NAME)]
    access_key_env: Annotated[str, Field(pattern=_ENVIRONMENT_NAME)]
    secret_key_env: Annotated[str, Field(pattern=_ENVIRONMENT_NAME)]
    bucket_env: Annotated[str, Field(pattern=_ENVIRONMENT_NAME)]
    bucket: Annotated[str, Field(min_length=1, max_length=255)]
    access_key_secret_ref: Annotated[str, Field(pattern=_SECRET_REFERENCE)]
    secret_key_secret_ref: Annotated[str, Field(pattern=_SECRET_REFERENCE)]
    secure: bool = False


ConnectionProfile = Annotated[
    PostgresConnectionProfile | MinioConnectionProfile,
    Field(discriminator="kind"),
]


class CommandOwnedPortInjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["command-owned"]


class EnvironmentPortInjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["environment"]
    name: Annotated[str, Field(pattern=_ENVIRONMENT_NAME)]


class ArgumentPortInjection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["argument"]
    option: Annotated[str, Field(min_length=1, max_length=80)]


PortInjection = Annotated[
    CommandOwnedPortInjection | EnvironmentPortInjection | ArgumentPortInjection,
    Field(discriminator="kind"),
]


class HostEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(pattern=_TARGET_NAME)]
    protocol: EndpointProtocol = EndpointProtocol.TCP
    host_port: Annotated[int, Field(ge=1, le=65535)]
    injection: PortInjection


class ComposeEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(pattern=_TARGET_NAME)]
    protocol: EndpointProtocol = EndpointProtocol.TCP
    host_port: Annotated[int, Field(ge=1, le=65535)]
    container_port: Annotated[int, Field(ge=1, le=65535)]


class HttpReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["http"]
    endpoint: Annotated[str, Field(pattern=_TARGET_NAME)]
    path: Annotated[str, Field(pattern=r"^/", max_length=512)] = "/health"


class TcpReadiness(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["tcp"]
    endpoint: Annotated[str, Field(pattern=_TARGET_NAME)]


ReadinessCheck = Annotated[HttpReadiness | TcpReadiness, Field(discriminator="kind")]


class ExistingComposeSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["existing-compose"]
    compose_files: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=512)], ...],
        Field(min_length=1),
    ]
    profiles: tuple[Annotated[str, Field(min_length=1, max_length=80)], ...] = ()
    service_names: Annotated[
        tuple[Annotated[str, Field(min_length=1, max_length=128)], ...],
        Field(min_length=1),
    ]

    @field_validator("compose_files")
    @classmethod
    def validate_compose_files(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(_validate_relative_repository_path(value) for value in values)


class DockerfileSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["dockerfile"]
    context: Annotated[str, Field(min_length=1, max_length=512)] = "."
    dockerfile: Annotated[str, Field(min_length=1, max_length=512)] = "Dockerfile"

    @field_validator("context", "dockerfile")
    @classmethod
    def validate_paths(cls, value: str) -> str:
        return _validate_relative_repository_path(value)


ComposeSource = Annotated[
    ExistingComposeSource | DockerfileSource,
    Field(discriminator="kind"),
]


class HostTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["host"] = "host"
    endpoints: tuple[HostEndpoint, ...] = ()
    readiness: ReadinessCheck | None = None
    readiness_timeout: Annotated[int, Field(ge=1, le=900)] = 60
    stop_timeout: Annotated[int, Field(ge=1, le=300)] = 10


class ComposeTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["compose"]
    source: ComposeSource
    endpoints: Annotated[tuple[ComposeEndpoint, ...], Field(min_length=1)]
    readiness: ReadinessCheck
    wait_timeout: Annotated[int, Field(ge=1, le=900)] = 120


ExecutionTarget = Annotated[HostTarget | ComposeTarget, Field(discriminator="kind")]


class ServiceCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(min_length=1, max_length=64)]
    label: Annotated[str, Field(min_length=1, max_length=80)]
    kind: PlanStepKind
    argv: Annotated[tuple[str, ...], Field(min_length=1)]
    long_running: bool = False


class WorkspaceService(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: Annotated[str, Field(min_length=1)]
    commands: tuple[ServiceCommand, ...] = ()
    environment: tuple[EnvironmentBinding, ...] = ()
    connection_profiles: tuple[ConnectionProfile, ...] = ()
    execution_target: ExecutionTarget = HostTarget()

    @model_validator(mode="after")
    def validate_connection_profiles(self) -> Self:
        kinds = tuple(profile.kind for profile in self.connection_profiles)
        if len(kinds) != len(set(kinds)):
            raise ValueError(_CONNECTION_PROFILE_KIND_DUPLICATED)
        return self


class WorkspaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=80)]
    mode: RunMode
    services: Annotated[tuple[WorkspaceService, ...], Field(min_length=1)]
    bindings: tuple[MiddlewareBinding, ...] = ()

    @model_validator(mode="after")
    def validate_unique_projects(self) -> Self:
        project_ids = tuple(service.project_id for service in self.services)
        if len(project_ids) != len(set(project_ids)):
            raise ValueError(_WORKSPACE_PROJECT_DUPLICATED)
        return self


class WorkspaceUpdateRequest(WorkspaceInput):
    expected_revision: Annotated[int, Field(ge=1)]


class WorkspaceRecord(WorkspaceInput):
    id: str
    revision: int
    created_at: datetime
    updated_at: datetime


class WorkspaceListResponse(BaseModel):
    workspaces: tuple[WorkspaceRecord, ...]


class RepositoryImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: Annotated[str, Field(min_length=1)]


class RepositoryCloneRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: Annotated[str, Field(min_length=1)]
    destination_parent: Annotated[str, Field(min_length=1)]
    directory_name: Annotated[str | None, Field(min_length=1, max_length=120)] = None
    branch: Annotated[str | None, Field(min_length=1, max_length=200)] = None


class RepositoryRecord(BaseModel):
    id: str
    name: str
    path: str
    origin_url: str | None
    branch: str
    head_sha: str
    upstream: str | None
    dirty: bool
    created_at: datetime
    updated_at: datetime


class RepositoryListResponse(BaseModel):
    repositories: tuple[RepositoryRecord, ...]


class WorkspacePlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_ids: Annotated[tuple[str, ...], Field(min_length=1)]
    mode: RunMode
    bindings: tuple[MiddlewareBinding, ...] = ()


class WorkspacePlanCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: Annotated[int, Field(ge=1)]


class PlanIssue(BaseModel):
    code: str
    title: str
    detail: str
    recovery: str


class ConnectionOutputPreview(BaseModel):
    name: str
    redacted_value: str
    sensitive: bool


class ConnectionMappingPreview(BaseModel):
    project_id: str
    kind: MiddlewareKind
    resource_id: str
    resource_name: str
    outputs: tuple[ConnectionOutputPreview, ...]


class CommandEnvironmentVariable(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(pattern=_ENVIRONMENT_NAME)]
    value: str


class PlanCommand(BaseModel):
    project_id: str
    project_name: str
    command_id: str
    label: str
    cwd: str
    argv: tuple[str, ...]
    environment: tuple[CommandEnvironmentVariable, ...] = ()
    long_running: bool = False


class HttpDeploymentProbeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["http"] = "http"
    url: Annotated[str, Field(pattern=r"^https?://", min_length=1)]
    timeout_seconds: Annotated[float, Field(gt=0, le=900)]


class TcpDeploymentProbeSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["tcp"] = "tcp"
    host: Annotated[str, Field(min_length=1, max_length=255)]
    port: Annotated[int, Field(ge=1, le=65535)]
    timeout_seconds: Annotated[float, Field(gt=0, le=900)]


DeploymentProbeSpec = Annotated[
    HttpDeploymentProbeSpec | TcpDeploymentProbeSpec,
    Field(discriminator="kind"),
]


class DeploymentEnvironmentSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    environment: tuple[EnvironmentBinding, ...] = ()
    connection_profiles: tuple[ConnectionProfile, ...] = ()
    bindings: tuple[MiddlewareBinding, ...] = ()


class ComposeDeploymentPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: str
    workspace_id: str
    project_id: str
    workspace_revision: Annotated[int, Field(ge=1)]
    source_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    target_config_fingerprint: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    checkout_path: str
    frozen_compose_path: str
    services: Annotated[tuple[str, ...], Field(min_length=1)]
    immutable_images: Annotated[tuple[str, ...], Field(min_length=1)]
    wait_timeout_seconds: Annotated[int, Field(ge=1, le=900)]
    probe: DeploymentProbeSpec
    environment_spec: DeploymentEnvironmentSnapshot


class PlanStep(BaseModel):
    id: str
    kind: PlanStepKind
    title: str
    detail: str
    commands: tuple[PlanCommand, ...]
    deployments: tuple[ComposeDeploymentPlan, ...] = ()


class WorkspacePlanResponse(BaseModel):
    generated_at: datetime
    ready: bool
    mode: RunMode
    projects: tuple[ProjectSummary, ...]
    steps: tuple[PlanStep, ...]
    blockers: tuple[PlanIssue, ...]
    warnings: tuple[PlanIssue, ...]
    connection_mappings: tuple[ConnectionMappingPreview, ...] = ()
    plan_id: str | None = None
    workspace_id: str | None = None
    workspace_revision: int | None = None
    config_fingerprint: str | None = None
    source_fingerprint: str | None = None


class RunCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: Annotated[str, Field(min_length=1)]
    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)]


class RunRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idempotency_key: Annotated[str, Field(min_length=8, max_length=128)]


class RunRecord(BaseModel):
    id: str
    workspace_id: str
    workspace_name: str
    workspace_revision: int
    plan_id: str
    mode: RunMode
    status: RunStatus
    current_step: str | None
    config_fingerprint: str
    source_fingerprint: str
    retry_of: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    failure_code: str | None
    failure_detail: str | None


class RunListResponse(BaseModel):
    runs: tuple[RunRecord, ...]


class RunEvent(BaseModel):
    sequence: int
    run_id: str
    created_at: datetime
    kind: RunEventKind
    step_id: str | None
    project_id: str | None
    message: str


class RunEventListResponse(BaseModel):
    events: tuple[RunEvent, ...]
    next_after: int


DeploymentRevisionStatus = Literal[
    "planned",
    "building",
    "applying",
    "verifying",
    "active",
    "superseded",
    "failed",
    "recovering",
    "rolled_back",
    "degraded",
]


class DeploymentRevisionView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_id: str
    workspace_id: str
    project_id: str
    workspace_revision: int
    project_name: str
    previous_revision_id: str | None
    status: DeploymentRevisionStatus
    source_fingerprint: str
    target_config_fingerprint: str
    services: tuple[str, ...]
    immutable_images: tuple[str, ...]
    created_at: datetime
    updated_at: datetime
    failure_code: str | None
    failure_detail: str | None
    recovery_detail: str | None


class DeploymentRevisionListResponse(BaseModel):
    deployments: tuple[DeploymentRevisionView, ...]


class CleanupItem(BaseModel):
    resource: RuntimeResource
    eligible: bool
    reason_code: str | None
    reason: str | None


class CleanupPreviewResponse(BaseModel):
    id: str
    generated_at: datetime
    runtime_fingerprint: str
    items: tuple[CleanupItem, ...]


class CleanupApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preview_id: Annotated[str, Field(min_length=1)]
    resource_ids: Annotated[tuple[str, ...], Field(min_length=1)]


class CleanupResultItem(BaseModel):
    resource_id: str
    resource_name: str
    status: str
    reason_code: str | None = None
    detail: str | None = None


class CleanupApplyResponse(BaseModel):
    preview_id: str
    results: tuple[CleanupResultItem, ...]


class OverviewResponse(BaseModel):
    generated_at: datetime
    api_status: str
    docker_available: bool
    project_count: int
    dirty_project_count: int
    middleware_count: int
    protected_kinds: tuple[MiddlewareKind, ...]


class SessionResponse(BaseModel):
    write_enabled: bool
    authentication: str


class SecretMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(pattern=_SECRET_REFERENCE)]
    name: Annotated[str, Field(min_length=1, max_length=80)]
    present: bool
    version: Annotated[int, Field(ge=1)]
    created_at: datetime
    updated_at: datetime


class SecretListResponse(BaseModel):
    secrets: tuple[SecretMetadata, ...]


class SecretCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=80)]
    value: Annotated[SecretStr, Field(min_length=1)]


class SecretUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: Annotated[int, Field(ge=1)]
    value: Annotated[SecretStr, Field(min_length=1)]


class ApiProblem(BaseModel):
    code: str
    detail: str
    recovery: str


class PackageScripts(BaseModel):
    model_config = ConfigDict(extra="ignore")

    dev: str | None = None
    build: str | None = None
    test: str | None = None
    test_ci: Annotated[str | None, Field(alias="test:ci")] = None
    lint: str | None = None
    typecheck: str | None = None


class PackageDependencies(BaseModel):
    model_config = ConfigDict(extra="ignore")

    react: str | None = None
    vue: str | None = None
    nuxt: str | None = None


class PackageManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    scripts: PackageScripts = PackageScripts()
    dependencies: PackageDependencies = PackageDependencies()
    dev_dependencies: Annotated[PackageDependencies, Field(alias="devDependencies")] = (
        PackageDependencies()
    )


class PyProjectDefinition(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    dependencies: tuple[str, ...] = ()


class PyProjectManifest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    project: PyProjectDefinition | None = None


class DockerCliRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: Annotated[str, Field(alias="ID")]
    image: Annotated[str, Field(alias="Image")]
    labels: Annotated[str, Field(alias="Labels")] = ""
    names: Annotated[str, Field(alias="Names")]
    ports: Annotated[str, Field(alias="Ports")] = ""
    state: Annotated[str, Field(alias="State")]
    status: Annotated[str, Field(alias="Status")]


class DockerPublishedPort(BaseModel):
    model_config = ConfigDict(extra="ignore")

    host_ip: Annotated[str, Field(alias="HostIp")]
    host_port: Annotated[str, Field(alias="HostPort")]


class DockerNetworkSettings(BaseModel):
    model_config = ConfigDict(extra="ignore")

    # pipedeck-ast: ignore[TG-DS001] - Docker owns dynamic "port/protocol" JSON keys.
    ports: Annotated[dict[str, tuple[DockerPublishedPort, ...] | None], Field(alias="Ports")]


class DockerInspectRow(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: Annotated[str, Field(alias="Id")]
    network_settings: Annotated[DockerNetworkSettings, Field(alias="NetworkSettings")]
