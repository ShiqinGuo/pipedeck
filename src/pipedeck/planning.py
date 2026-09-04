from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import quote

from pipedeck.contracts import (
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
                            f"No connection adapter for {kind.value}",
                            "This version only supports structured connection injection for PostgreSQL and MinIO",  # noqa: E501
                            "Remove this project, or wait for the corresponding middleware adapter",
                        )
                    )
                elif kind not in configured:
                    blockers.append(
                        self._issue(
                            "CONNECTION_PROFILE_REQUIRED",
                            f"Missing {kind.value} connection configuration for {service.project_id}",  # noqa: E501
                            "Selecting a container only proves the instance exists; the service still needs an explicit connection output mapping",  # noqa: E501
                            "Add a connection profile to the service and re-run the preflight",
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
                            f"Connection environment variable conflict for {service.project_id}",
                            f"Environment variables {', '.join(conflicts)} are owned by both the explicit configuration and the connection configuration",  # noqa: E501
                            "Remove the conflicting explicit environment variable, or change the connection profile output name",  # noqa: E501
                        )
                    )
                used_names.update(output_names)

                binding = self._binding_for(profile.kind, workspace.bindings)
                if binding is None:
                    blockers.append(
                        self._issue(
                            "CONNECTION_BINDING_REQUIRED",
                            f"Missing {profile.kind.value} binding",
                            "The connection profile has no resolvable Docker target",
                            "Select a healthy target instance and re-run the preflight",
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
                            f"{resource.name} has no port available to the host",
                            (
                                "Docker inspect did not find "
                                f"a published port for {self._container_port(profile)}/tcp"
                            ),
                            "Publish the required port on the container, then refresh resources and re-run the preflight",  # noqa: E501
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
                            f"Connection credentials unavailable for {service.project_id}",
                            "At least one Secret reference is missing or cannot be read from the system credential store",  # noqa: E501
                            "Re-write the required Secret in the client and re-run the preflight",
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
                f"Connection target for {kind.value} does not exist",
                "The bound container is not in the current Docker snapshot",
                "Refresh resources and re-select the target",
            )
        if resource.kind is not kind:
            return cls._issue(
                "CONNECTION_RESOURCE_KIND_MISMATCH",
                f"Connection target type mismatch for {kind.value}",
                f"The resource is actually of type {resource.kind.value}",
                f"Re-select a {kind.value} instance",
            )
        if resource.health not in {ResourceHealth.HEALTHY, ResourceHealth.RUNNING}:
            return cls._issue(
                "CONNECTION_RESOURCE_UNAVAILABLE",
                f"{resource.name} is currently unavailable",
                f"The resource status is {resource.health.value}",
                "Start or repair the resource, then re-run the preflight",
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
                    title="Project catalog has changed",
                    detail="At least one selected project no longer exists in the current catalog",
                    recovery="Refresh the project catalog and re-select",
                )
            )

        required_kinds = self._required_kinds(selected)
        for kind in required_kinds:
            binding = self._binding_for(kind, request.bindings)
            if binding is None:
                blockers.append(
                    PlanIssue(
                        code="MIDDLEWARE_BINDING_REQUIRED",
                        title=f"Missing {kind.value} binding",
                        detail="The selected projects declare this local dependency, but no target instance is configured",  # noqa: E501
                        recovery="Select a healthy instance or create a platform-managed instance",
                    )
                )
                continue

            resource = self._resource_for(binding.resource_id, runtime.resources)
            if resource is None:
                blockers.append(
                    PlanIssue(
                        code="MIDDLEWARE_TARGET_MISSING",
                        title=f"{kind.value} target unavailable",
                        detail="The selected target is not in the current Docker snapshot",
                        recovery="Refresh resources and re-select the target",
                    )
                )
            elif resource.kind is not kind:
                blockers.append(
                    PlanIssue(
                        code="MIDDLEWARE_TARGET_KIND_MISMATCH",
                        title=f"{kind.value} target type mismatch",
                        detail=f"Selected resource {resource.name} is actually of type {resource.kind.value}",  # noqa: E501
                        recovery=f"Select a {kind.value} instance and regenerate the plan",
                    )
                )
            elif resource.health not in {ResourceHealth.HEALTHY, ResourceHealth.RUNNING}:
                blockers.append(
                    PlanIssue(
                        code="MIDDLEWARE_TARGET_UNAVAILABLE",
                        title=f"{kind.value} target unavailable",
                        detail=f"Selected resource {resource.name} is currently in state {resource.health.value}",  # noqa: E501
                        recovery="Start or repair this instance, or select another available instance",  # noqa: E501
                    )
                )

        for binding in request.bindings:
            if binding.kind not in required_kinds:
                warnings.append(
                    PlanIssue(
                        code="UNUSED_MIDDLEWARE_BINDING",
                        title=f"{binding.kind.value} is currently unused",
                        detail="The selected projects do not declare this dependency",
                        recovery="It can be kept, or removed from the current profile",
                    )
                )

        if not runtime.docker_available and required_kinds:
            blockers.append(
                PlanIssue(
                    code="DOCKER_UNAVAILABLE",
                    title="Docker unavailable",
                    detail="The current workspace depends on local container middleware",
                    recovery="Start Docker Desktop and regenerate the plan",
                )
            )

        for project in selected:
            if project.dirty:
                warnings.append(
                    PlanIssue(
                        code="DIRTY_WORKTREE",
                        title=f"{project.name} has uncommitted changes",
                        detail="The plan will use the current file contents; later update operations must not overwrite these changes",  # noqa: E501
                        recovery="Commit, stash, or keep the current state and continue",
                    )
                )

            if self._project_command(project, PlanStepKind.START) is None:
                blockers.append(
                    PlanIssue(
                        code="START_COMMAND_UNRESOLVED",
                        title=f"{project.name} is missing a start contract",
                        detail="No resolvable development or start command found in the project directory",  # noqa: E501
                        recovery="Declare a start command in the project run contract and re-scan",
                    )
                )
            if (
                request.mode is RunMode.INTEGRATED
                and self._project_command(project, PlanStepKind.BUILD) is None
            ):
                blockers.append(
                    PlanIssue(
                        code="BUILD_COMMAND_UNRESOLVED",
                        title=f"{project.name} is missing a build contract",
                        detail="Integrated mode requires every project to provide a resolvable build command",  # noqa: E501
                        recovery="Declare a build command in the project run contract, or switch to development mode",  # noqa: E501
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
            label="Read Git status",
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
                    title="Confirm source snapshot",
                    detail=f"Record branch and dirty state for {project_names}",
                    commands=inspect_commands,
                )
            )

        dependency_commands = cls._project_commands(projects, PlanStepKind.DEPENDENCIES)
        if dependency_commands:
            steps.append(
                PlanStep(
                    id="dependencies",
                    kind=PlanStepKind.DEPENDENCIES,
                    title="Install locked dependencies",
                    detail="Run installs with the package manager detected for each project",
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
                    title="Run project quality gates",
                    detail="Run the type-check and test commands declared by the repositories",
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
                        title="Build projects",
                        detail="Run the build command explicitly declared by each project",
                        commands=build_commands,
                    )
                )

        start_commands = cls._project_commands(projects, PlanStepKind.START)
        if start_commands:
            steps.append(
                PlanStep(
                    id="start",
                    kind=PlanStepKind.START,
                    title="Start project services",
                    detail="Start projects per the workspace configuration and keep watching long-running services",  # noqa: E501
                    commands=start_commands,
                )
            )
        return tuple(steps)
