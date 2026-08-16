from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import quote

from tripguru_local.contracts import (
    CatalogResponse,
    ComposeTarget,
    ConnectionMappingPreview,
    ConnectionOutputPreview,
    ConnectionProfile,
    EndpointProtocol,
    MiddlewareBinding,
    MiddlewareKind,
    PlanCommand,
    PlanIssue,
    PlanStep,
    PlanStepKind,
    PostgresConnectionProfile,
    ProjectSummary,
    ResourceHealth,
    RunMode,
    RuntimeEndpoint,
    RuntimeResource,
    RuntimeResponse,
    WorkspacePlanRequest,
    WorkspacePlanResponse,
    WorkspaceRecord,
    WorkspaceService,
)

_CONNECTION_BINDING_REQUIRED = "CONNECTION_BINDING_REQUIRED"
_CONNECTION_RESOURCE_UNAVAILABLE = "CONNECTION_RESOURCE_UNAVAILABLE"
_CONNECTION_ENDPOINT_MISSING = "CONNECTION_ENDPOINT_MISSING"


class SecretReferenceReader(Protocol):
    def is_readable(self, secret_id: str) -> bool: ...

    def read(self, secret_id: str) -> str: ...


@dataclass(frozen=True, slots=True)
class ResolvedConnectionEnvironment:
    name: str
    value: str
    sensitive: bool
    redaction_values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConnectionPlanResult:
    blockers: tuple[PlanIssue, ...]
    mappings: tuple[ConnectionMappingPreview, ...]


class ConnectionResolutionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class ConnectionPlanner:
    _supported_kinds = frozenset((MiddlewareKind.POSTGRES, MiddlewareKind.MINIO))

    def __init__(self, secrets: SecretReferenceReader) -> None:
        self._secrets = secrets

    def analyze(
        self,
        workspace: WorkspaceRecord,
        projects: tuple[ProjectSummary, ...],
        runtime: RuntimeResponse,
    ) -> ConnectionPlanResult:
        blockers: list[PlanIssue] = []
        mappings: list[ConnectionMappingPreview] = []
        project_by_id = tuple((project.id, project) for project in projects)
        for service in workspace.services:
            project = next(
                (
                    project
                    for project_id, project in project_by_id
                    if project_id == service.project_id
                ),
                None,
            )
            required = project.requirements if project is not None else ()
            configured = {profile.kind for profile in service.connection_profiles}
            for kind in required:
                if kind not in self._supported_kinds:
                    blockers.append(
                        self._issue(
                            "CONNECTION_ADAPTER_UNSUPPORTED",
                            f"{kind.value} 暂无连接适配器",
                            "当前版本只支持 PostgreSQL 与 MinIO 的结构化连接注入",
                            "移除该项目，或等待对应中间件适配器",
                        )
                    )
                elif kind not in configured:
                    blockers.append(
                        self._issue(
                            "CONNECTION_PROFILE_REQUIRED",
                            f"{service.project_id} 缺少 {kind.value} 连接配置",
                            "选择容器只证明实例存在，服务仍需要显式连接输出映射",
                            "为该服务添加 connection profile 后重新预检",
                        )
                    )

            used_names = {binding.name for binding in service.environment}
            for profile in service.connection_profiles:
                output_names = self._output_names(profile)
                conflicts = tuple(name for name in output_names if name in used_names)
                if conflicts:
                    blockers.append(
                        self._issue(
                            "CONNECTION_ENV_CONFLICT",
                            f"{service.project_id} 的连接环境变量冲突",
                            f"环境变量 {', '.join(conflicts)} 同时由显式配置与连接配置拥有",
                            "删除同名显式环境变量或修改 connection profile 输出名",
                        )
                    )
                used_names.update(output_names)

                binding = self._binding_for(profile.kind, workspace.bindings)
                if binding is None:
                    blockers.append(
                        self._issue(
                            "CONNECTION_BINDING_REQUIRED",
                            f"缺少 {profile.kind.value} 绑定",
                            "connection profile 没有可解析的 Docker 目标",
                            "选择一个健康的目标实例后重新预检",
                        )
                    )
                    continue
                resource = self._resource_for(binding.resource_id, runtime.resources)
                resource_issue = self._resource_issue(profile.kind, resource)
                if resource_issue is not None:
                    blockers.append(resource_issue)
                    continue
                assert resource is not None
                endpoint = self._endpoint_for(profile, resource)
                if endpoint is None:
                    blockers.append(
                        self._issue(
                            "CONNECTION_ENDPOINT_MISSING",
                            f"{resource.name} 没有可供宿主机使用的端口",
                            (
                                "Docker inspect 未发现 "
                                f"{self._container_port(profile)}/tcp 的 published port"
                            ),
                            "为容器发布所需端口，刷新资源后重新预检",
                        )
                    )
                    continue
                missing_refs = tuple(
                    reference
                    for reference in self._secret_references(profile)
                    if not self._secrets.is_readable(reference)
                )
                if missing_refs:
                    blockers.append(
                        self._issue(
                            "CONNECTION_SECRET_MISSING",
                            f"{service.project_id} 的连接凭据不可用",
                            "至少一个 Secret 引用不存在或无法从系统凭据存储读取",
                            "在客户端重新写入所需 Secret 后重新预检",
                        )
                    )
                    continue
                if conflicts:
                    continue
                mappings.append(
                    self._preview(
                        service.project_id,
                        profile,
                        resource,
                        endpoint,
                        self._consumer_host(service, endpoint),
                    )
                )
        return ConnectionPlanResult(blockers=tuple(blockers), mappings=tuple(mappings))

    def resolve(
        self,
        service: WorkspaceService,
        bindings: tuple[MiddlewareBinding, ...],
        runtime: RuntimeResponse,
        *,
        consumer_host: str | None = None,
    ) -> tuple[ResolvedConnectionEnvironment, ...]:
        resolved: list[ResolvedConnectionEnvironment] = []
        for profile in service.connection_profiles:
            binding = self._binding_for(profile.kind, bindings)
            if binding is None:
                raise ConnectionResolutionError(_CONNECTION_BINDING_REQUIRED)
            resource = self._resource_for(binding.resource_id, runtime.resources)
            if self._resource_issue(profile.kind, resource) is not None or resource is None:
                raise ConnectionResolutionError(_CONNECTION_RESOURCE_UNAVAILABLE)
            endpoint = self._endpoint_for(profile, resource)
            if endpoint is None:
                raise ConnectionResolutionError(_CONNECTION_ENDPOINT_MISSING)
            resolved.extend(
                self._resolve_profile(
                    profile,
                    consumer_host or endpoint.host,
                    endpoint.host_port,
                )
            )
        return tuple(resolved)

    def _resolve_profile(
        self,
        profile: ConnectionProfile,
        host: str,
        host_port: int,
    ) -> tuple[ResolvedConnectionEnvironment, ...]:
        if isinstance(profile, PostgresConnectionProfile):
            password = self._secrets.read(profile.secret_ref)
            encoded_password = quote(password, safe="")
            username = quote(profile.username, safe="")
            database = quote(profile.database, safe="")
            url = f"{profile.scheme}://{username}:{encoded_password}@{host}:{host_port}/{database}"
            return (
                ResolvedConnectionEnvironment(
                    name=profile.env_var,
                    value=url,
                    sensitive=True,
                    redaction_values=self._redaction_values(password, encoded_password, url),
                ),
            )
        access_key = self._secrets.read(profile.access_key_secret_ref)
        secret_key = self._secrets.read(profile.secret_key_secret_ref)
        endpoint = f"{'https' if profile.secure else 'http'}://{host}:{host_port}"
        return (
            ResolvedConnectionEnvironment(profile.endpoint_env, endpoint, False),
            ResolvedConnectionEnvironment(
                profile.access_key_env,
                access_key,
                True,
                self._redaction_values(access_key, quote(access_key, safe="")),
            ),
            ResolvedConnectionEnvironment(
                profile.secret_key_env,
                secret_key,
                True,
                self._redaction_values(secret_key, quote(secret_key, safe="")),
            ),
            ResolvedConnectionEnvironment(profile.bucket_env, profile.bucket, False),
        )

    def _preview(
        self,
        project_id: str,
        profile: ConnectionProfile,
        resource: RuntimeResource,
        endpoint: RuntimeEndpoint,
        host: str,
    ) -> ConnectionMappingPreview:
        if isinstance(profile, PostgresConnectionProfile):
            username = quote(profile.username, safe="")
            database = quote(profile.database, safe="")
            outputs = (
                ConnectionOutputPreview(
                    name=profile.env_var,
                    redacted_value=f"{profile.scheme}://{username}:***@{host}:{endpoint.host_port}/{database}",
                    sensitive=True,
                ),
            )
        else:
            outputs = (
                ConnectionOutputPreview(
                    name=profile.endpoint_env,
                    redacted_value=f"{'https' if profile.secure else 'http'}://{host}:{endpoint.host_port}",
                    sensitive=False,
                ),
                ConnectionOutputPreview(
                    name=profile.access_key_env,
                    redacted_value="***",
                    sensitive=True,
                ),
                ConnectionOutputPreview(
                    name=profile.secret_key_env,
                    redacted_value="***",
                    sensitive=True,
                ),
                ConnectionOutputPreview(
                    name=profile.bucket_env,
                    redacted_value=profile.bucket,
                    sensitive=False,
                ),
            )
        return ConnectionMappingPreview(
            project_id=project_id,
            kind=profile.kind,
            resource_id=resource.id,
            resource_name=resource.name,
            outputs=outputs,
        )

    @staticmethod
    def _consumer_host(service: WorkspaceService, endpoint: RuntimeEndpoint) -> str:
        if isinstance(service.execution_target, ComposeTarget):
            return "host.docker.internal"
        return endpoint.host

    @staticmethod
    def _binding_for(
        kind: MiddlewareKind,
        bindings: tuple[MiddlewareBinding, ...],
    ) -> MiddlewareBinding | None:
        return next((binding for binding in bindings if binding.kind is kind), None)

    @staticmethod
    def _resource_for(
        resource_id: str,
        resources: tuple[RuntimeResource, ...],
    ) -> RuntimeResource | None:
        return next(
            (
                resource
                for resource in resources
                if resource.id == resource_id or resource.name == resource_id
            ),
            None,
        )

    @classmethod
    def _resource_issue(
        cls,
        kind: MiddlewareKind,
        resource: RuntimeResource | None,
    ) -> PlanIssue | None:
        if resource is None:
            return cls._issue(
                "CONNECTION_RESOURCE_MISSING",
                f"{kind.value} 连接目标不存在",
                "绑定的容器不在当前 Docker 快照中",
                "刷新资源并重新选择目标",
            )
        if resource.kind is not kind:
            return cls._issue(
                "CONNECTION_RESOURCE_KIND_MISMATCH",
                f"{kind.value} 连接目标类型不匹配",
                f"当前资源实际类型是 {resource.kind.value}",
                f"重新选择 {kind.value} 实例",
            )
        if resource.health not in {ResourceHealth.HEALTHY, ResourceHealth.RUNNING}:
            return cls._issue(
                "CONNECTION_RESOURCE_UNAVAILABLE",
                f"{resource.name} 当前不可用",
                f"资源状态是 {resource.health.value}",
                "启动或修复资源后重新预检",
            )
        return None

    @classmethod
    def _endpoint_for(
        cls,
        profile: ConnectionProfile,
        resource: RuntimeResource,
    ) -> RuntimeEndpoint | None:
        container_port = cls._container_port(profile)
        return next(
            (
                endpoint
                for endpoint in resource.endpoints
                if endpoint.protocol is EndpointProtocol.TCP
                and endpoint.container_port == container_port
            ),
            None,
        )

    @staticmethod
    def _container_port(profile: ConnectionProfile) -> int:
        return 5432 if isinstance(profile, PostgresConnectionProfile) else 9000

    @staticmethod
    def _output_names(profile: ConnectionProfile) -> tuple[str, ...]:
        if isinstance(profile, PostgresConnectionProfile):
            return (profile.env_var,)
        return (
            profile.endpoint_env,
            profile.access_key_env,
            profile.secret_key_env,
            profile.bucket_env,
        )

    @staticmethod
    def _secret_references(profile: ConnectionProfile) -> tuple[str, ...]:
        if isinstance(profile, PostgresConnectionProfile):
            return (profile.secret_ref,)
        return (profile.access_key_secret_ref, profile.secret_key_secret_ref)

    @staticmethod
    def _redaction_values(*values: str) -> tuple[str, ...]:
        result: list[str] = []
        for value in values:
            if value and value not in result:
                result.append(value)
        return tuple(result)

    @staticmethod
    def _issue(code: str, title: str, detail: str, recovery: str) -> PlanIssue:
        return PlanIssue(code=code, title=title, detail=detail, recovery=recovery)


class WorkspacePlanner:
    def create(
        self,
        request: WorkspacePlanRequest,
        catalog: CatalogResponse,
        runtime: RuntimeResponse,
    ) -> WorkspacePlanResponse:
        selected = self._selected_projects(request.project_ids, catalog.projects)
        blockers: list[PlanIssue] = []
        warnings: list[PlanIssue] = []

        selected_ids = {project.id for project in selected}
        missing_ids = tuple(
            project_id for project_id in request.project_ids if project_id not in selected_ids
        )
        if missing_ids:
            blockers.append(
                PlanIssue(
                    code="PROJECT_CATALOG_STALE",
                    title="项目目录已经变化",
                    detail="至少一个已选项目不再存在于当前目录",
                    recovery="刷新项目目录并重新选择",
                )
            )

        required_kinds = self._required_kinds(selected)
        for kind in required_kinds:
            binding = self._binding_for(kind, request.bindings)
            if binding is None:
                blockers.append(
                    PlanIssue(
                        code="MIDDLEWARE_BINDING_REQUIRED",
                        title=f"缺少 {kind.value} 绑定",
                        detail="所选项目声明了该本地依赖，但配置中没有目标实例",
                        recovery="选择一个健康实例或创建平台托管实例",
                    )
                )
                continue

            resource = self._resource_for(binding.resource_id, runtime.resources)
            if resource is None:
                blockers.append(
                    PlanIssue(
                        code="MIDDLEWARE_TARGET_MISSING",
                        title=f"{kind.value} 目标不可用",
                        detail="已选目标不在当前 Docker 快照中",
                        recovery="刷新资源并重新选择目标",
                    )
                )
            elif resource.kind is not kind:
                blockers.append(
                    PlanIssue(
                        code="MIDDLEWARE_TARGET_KIND_MISMATCH",
                        title=f"{kind.value} 目标类型不匹配",
                        detail=f"已选资源 {resource.name} 实际类型是 {resource.kind.value}",
                        recovery=f"选择一个 {kind.value} 实例后重新生成预检计划",
                    )
                )
            elif resource.health not in {ResourceHealth.HEALTHY, ResourceHealth.RUNNING}:
                blockers.append(
                    PlanIssue(
                        code="MIDDLEWARE_TARGET_UNAVAILABLE",
                        title=f"{kind.value} 目标不可用",
                        detail=f"已选资源 {resource.name} 当前状态是 {resource.health.value}",
                        recovery="启动或修复该实例，或选择另一个可用实例",
                    )
                )

        for binding in request.bindings:
            if binding.kind not in required_kinds:
                warnings.append(
                    PlanIssue(
                        code="UNUSED_MIDDLEWARE_BINDING",
                        title=f"{binding.kind.value} 当前未被使用",
                        detail="所选项目没有声明该依赖",
                        recovery="可以保留，或从当前 Profile 中移除",
                    )
                )

        if not runtime.docker_available and required_kinds:
            blockers.append(
                PlanIssue(
                    code="DOCKER_UNAVAILABLE",
                    title="Docker 不可用",
                    detail="当前工作区依赖本地容器中间件",
                    recovery="启动 Docker Desktop 后重新生成计划",
                )
            )

        for project in selected:
            if project.dirty:
                warnings.append(
                    PlanIssue(
                        code="DIRTY_WORKTREE",
                        title=f"{project.name} 有未提交修改",
                        detail="计划会使用当前文件内容，后续更新操作不得覆盖这些修改",
                        recovery="提交、暂存或保留当前状态后继续",
                    )
                )

            if self._project_command(project, PlanStepKind.START) is None:
                blockers.append(
                    PlanIssue(
                        code="START_COMMAND_UNRESOLVED",
                        title=f"{project.name} 缺少启动契约",
                        detail="项目目录中没有可解析的开发或启动命令",
                        recovery="在项目运行契约中声明启动命令后重新扫描",
                    )
                )
            if (
                request.mode is RunMode.INTEGRATED
                and self._project_command(project, PlanStepKind.BUILD) is None
            ):
                blockers.append(
                    PlanIssue(
                        code="BUILD_COMMAND_UNRESOLVED",
                        title=f"{project.name} 缺少构建契约",
                        detail="集成模式要求每个项目提供可解析的构建命令",
                        recovery="在项目运行契约中声明构建命令，或改用开发模式",
                    )
                )

        steps = self._steps(selected, request.mode)
        return WorkspacePlanResponse(
            generated_at=datetime.now(UTC),
            ready=not blockers,
            mode=request.mode,
            projects=selected,
            steps=steps,
            blockers=tuple(blockers),
            warnings=tuple(warnings),
        )

    @staticmethod
    def _selected_projects(
        project_ids: tuple[str, ...], projects: tuple[ProjectSummary, ...]
    ) -> tuple[ProjectSummary, ...]:
        selected_ids = set(project_ids)
        return tuple(project for project in projects if project.id in selected_ids)

    @staticmethod
    def _required_kinds(projects: tuple[ProjectSummary, ...]) -> set[MiddlewareKind]:
        return {kind for project in projects for kind in project.requirements}

    @staticmethod
    def _binding_for(
        kind: MiddlewareKind, bindings: tuple[MiddlewareBinding, ...]
    ) -> MiddlewareBinding | None:
        return next((binding for binding in bindings if binding.kind is kind), None)

    @staticmethod
    def _resource_for(
        resource_id: str, resources: tuple[RuntimeResource, ...]
    ) -> RuntimeResource | None:
        return next(
            (
                resource
                for resource in resources
                if resource.id == resource_id or resource.name == resource_id
            ),
            None,
        )

    @staticmethod
    def _project_command(project: ProjectSummary, kind: PlanStepKind) -> PlanCommand | None:
        command = next(
            (
                command
                for command in project.commands
                if WorkspacePlanner._kind(command.id, command.kind) is kind
            ),
            None,
        )
        if command is None:
            return None
        return PlanCommand(
            project_id=project.id,
            project_name=project.name,
            command_id=command.id,
            label=command.label,
            cwd=project.path,
            argv=command.argv,
            long_running=command.long_running,
        )

    @classmethod
    def _project_commands(
        cls, projects: tuple[ProjectSummary, ...], kind: PlanStepKind
    ) -> tuple[PlanCommand, ...]:
        return tuple(
            plan_command
            for project in projects
            if (plan_command := cls._project_command(project, kind)) is not None
        )

    @staticmethod
    def _kind(command_id: str, explicit: PlanStepKind | None) -> PlanStepKind | None:
        if explicit is not None:
            return explicit
        if command_id == "install":
            return PlanStepKind.DEPENDENCIES
        if command_id in {"lint", "typecheck", "test"}:
            return PlanStepKind.QUALITY
        if command_id == "build":
            return PlanStepKind.BUILD
        if command_id in {"dev", "start"}:
            return PlanStepKind.START
        return None

    @staticmethod
    def _source_command(project: ProjectSummary) -> PlanCommand:
        return PlanCommand(
            project_id=project.id,
            project_name=project.name,
            command_id="source-status",
            label="读取 Git 状态",
            cwd=project.path,
            argv=("git", "status", "--short", "--branch"),
        )

    @classmethod
    def _steps(cls, projects: tuple[ProjectSummary, ...], mode: RunMode) -> tuple[PlanStep, ...]:
        project_names = ", ".join(project.name for project in projects)
        steps: list[PlanStep] = []
        inspect_commands = tuple(cls._source_command(project) for project in projects)
        if inspect_commands:
            steps.append(
                PlanStep(
                    id="inspect",
                    kind=PlanStepKind.INSPECT,
                    title="确认源码快照",
                    detail=f"记录 {project_names} 的分支和 dirty 状态",
                    commands=inspect_commands,
                )
            )

        dependency_commands = cls._project_commands(projects, PlanStepKind.DEPENDENCIES)
        if dependency_commands:
            steps.append(
                PlanStep(
                    id="dependencies",
                    kind=PlanStepKind.DEPENDENCIES,
                    title="安装锁定依赖",
                    detail="按每个项目识别到的包管理器执行安装",
                    commands=dependency_commands,
                )
            )

        quality_commands = tuple(
            command
            for project in projects
            for command in project.commands
            if cls._kind(command.id, command.kind) is PlanStepKind.QUALITY
        )
        if quality_commands:
            steps.append(
                PlanStep(
                    id="quality",
                    kind=PlanStepKind.QUALITY,
                    title="运行项目质量门禁",
                    detail="执行仓库声明的类型检查与测试命令",
                    commands=tuple(
                        PlanCommand(
                            project_id=project.id,
                            project_name=project.name,
                            command_id=command.id,
                            label=command.label,
                            cwd=project.path,
                            argv=command.argv,
                            long_running=command.long_running,
                        )
                        for project in projects
                        for command in project.commands
                        if cls._kind(command.id, command.kind) is PlanStepKind.QUALITY
                    ),
                )
            )
        if mode is RunMode.INTEGRATED:
            build_commands = cls._project_commands(projects, PlanStepKind.BUILD)
            if build_commands:
                steps.append(
                    PlanStep(
                        id="build",
                        kind=PlanStepKind.BUILD,
                        title="构建项目",
                        detail="执行每个项目明确声明的构建命令",
                        commands=build_commands,
                    )
                )

        start_commands = cls._project_commands(projects, PlanStepKind.START)
        if start_commands:
            steps.append(
                PlanStep(
                    id="start",
                    kind=PlanStepKind.START,
                    title="启动项目服务",
                    detail="按工作区配置启动项目并持续观察长期服务",
                    commands=start_commands,
                )
            )
        return tuple(steps)
