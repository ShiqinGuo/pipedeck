from __future__ import annotations

import hmac
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Never
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware

from pipedeck.catalog import ProjectCatalog
from pipedeck.cleanup import CleanupError, CleanupService
from pipedeck.compose_compiler import ComposeCompiler
from pipedeck.compose_deployment import (
    ComposeDeploymentWorkflow,
    DeploymentError,
    DeploymentRevision,
)
from pipedeck.contracts import (
    ApiProblem,
    CatalogResponse,
    CleanupApplyRequest,
    CleanupApplyResponse,
    CleanupPreviewResponse,
    DeploymentRevisionListResponse,
    DeploymentRevisionView,
    MiddlewareKind,
    OverviewResponse,
    RepositoryCloneRequest,
    RepositoryImportRequest,
    RepositoryListResponse,
    RepositoryRecord,
    RunCreateRequest,
    RunEventListResponse,
    RunListResponse,
    RunRecord,
    RunRetryRequest,
    RunStatus,
    RuntimeProcessListResponse,
    RuntimeResponse,
    SecretCreateRequest,
    SecretListResponse,
    SecretMetadata,
    SecretUpdateRequest,
    SessionResponse,
    WorkspaceInput,
    WorkspaceListResponse,
    WorkspacePlanCreateRequest,
    WorkspacePlanRequest,
    WorkspacePlanResponse,
    WorkspaceRecord,
    WorkspaceUpdateRequest,
)
from pipedeck.control_plane import (
    ConnectionAwareWorkspacePlanner,
    CurrentPlanFreshnessValidator,
    FreshRuntimeProvider,
    ManagedResourceObserver,
    SecretService,
    SecretServiceError,
    SecretStore,
    StoreManagedResourceVerifier,
    StorePlanLoader,
    WindowsCredentialStore,
    WorkspaceEnvironmentResolver,
    WorkspaceReadinessResolver,
)
from pipedeck.deployment_control import (
    DockerDeploymentRuntimeInspector,
    LocalComposeDeploymentRunner,
    SnapshotDeploymentEnvironmentResolver,
    WorkspaceDeploymentExecutor,
)
from pipedeck.execution import ExecutionEngine, ExecutionError
from pipedeck.managed_middleware import (
    DockerCliManagedMiddleware,
    ManagedMiddlewareError,
    ManagedMiddlewareProvisionRequest,
    ManagedMiddlewareService,
    ManagedResourceListResponse,
    ManagedResourceRecord,
    SubprocessDockerCommandRunner,
)
from pipedeck.planning import ConnectionPlanner, WorkspacePlanner
from pipedeck.processes import SubprocessRunner
from pipedeck.readiness import ReadinessProbeRunner
from pipedeck.repositories import RepositoryService, RepositoryServiceError
from pipedeck.runtime import DockerRuntime
from pipedeck.settings import LocalSettings
from pipedeck.state_store import StateStore, StateStoreError
from pipedeck.workspace_planning import SavedWorkspacePlanner


def create_app(
    settings: LocalSettings | None = None,
    secret_store: SecretStore | None = None,
) -> FastAPI:
    resolved_settings = settings or LocalSettings()
    runner = SubprocessRunner()
    store = StateStore(resolved_settings.state_db_path)
    catalog = ProjectCatalog(
        roots=resolved_settings.scan_roots,
        max_depth=resolved_settings.scan_max_depth,
        command_runner=runner,
        registered_repositories=store.list_repositories,
    )
    runtime = DockerRuntime(command_runner=runner)
    planner = WorkspacePlanner()
    secrets = SecretService(store, secret_store or WindowsCredentialStore())
    connection_planner = ConnectionPlanner(secrets)
    readiness = ReadinessProbeRunner()
    deployment_runner = LocalComposeDeploymentRunner()
    deployment_environment = SnapshotDeploymentEnvironmentResolver(
        runtime,
        secrets,
        connection_planner,
    )
    deployment_workflow = ComposeDeploymentWorkflow(
        store,
        deployment_runner,
        deployment_environment,
        readiness,
        DockerDeploymentRuntimeInspector(
            store,
            deployment_runner,
            deployment_environment,
            readiness,
        ),
    )
    saved_planner = ConnectionAwareWorkspacePlanner(
        SavedWorkspacePlanner(
            command_runner=runner,
            compose_compiler=ComposeCompiler(
                resolved_settings.state_db_path.parent / "deployments"
            ),
        ),
        connection_planner,
        secrets,
    )
    repositories = RepositoryService(store=store, command_runner=runner)
    execution = ExecutionEngine(
        store=store,
        environment_resolver=WorkspaceEnvironmentResolver(
            store,
            runtime,
            secrets,
            connection_planner,
        ),
        plan_loader=StorePlanLoader(store),
        freshness_validator=CurrentPlanFreshnessValidator(
            store,
            catalog,
            runtime,
            saved_planner,
        ),
        run_observer=ManagedResourceObserver(store, runtime),
        readiness_resolver=WorkspaceReadinessResolver(store),
        readiness_waiter=readiness,
        deployment_executor=WorkspaceDeploymentExecutor(store, deployment_workflow),
    )
    managed_middleware = ManagedMiddlewareService(
        store,
        DockerCliManagedMiddleware(SubprocessDockerCommandRunner()),
        secrets,
    )
    cleanup = CleanupService(
        runtime=FreshRuntimeProvider(runtime),
        command_runner=runner,
        managed_resource_verifier=StoreManagedResourceVerifier(store),
        managed_resource_remover=managed_middleware,
    )
    configured_token = (
        resolved_settings.api_token.get_secret_value() if resolved_settings.api_token else None
    )

    @asynccontextmanager
    async def _lifespan(_: FastAPI) -> AsyncGenerator[None, None]:
        deployment_workflow.reconcile_pending()
        for resource in managed_middleware.list():
            if resource.status.value in {"planned", "provisioning"}:
                managed_middleware.reconcile(resource.id)
        try:
            yield
        finally:
            for run in store.list_runs():
                if run.status in {RunStatus.QUEUED, RunStatus.RUNNING}:
                    execution.cancel(run.id)
            for run in store.list_runs():
                execution.wait(run.id, timeout=10)
            store.close()

    app = FastAPI(
        title="Pipedeck API",
        version="0.2.0",
        lifespan=_lifespan,
    )
    app.state.control_store = store
    app.state.secret_service = secrets
    app.state.managed_middleware_service = managed_middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=(
            "http://127.0.0.1:5173",
            "http://localhost:5173",
            "http://tauri.localhost",
            "tauri://localhost",
        ),
        allow_methods=("GET", "POST", "PUT", "DELETE", "OPTIONS"),
        allow_headers=("content-type", "x-request-id", "x-pipedeck-token"),
    )

    async def _request_id_middleware(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = uuid4().hex
        response = await call_next(request)
        response.headers["x-request-id"] = request_id
        return response

    def _http_problem(
        response_status: int,
        code: str,
        detail: str,
        recovery: str,
    ) -> Never:
        problem = ApiProblem(code=code, detail=detail, recovery=recovery)
        raise HTTPException(status_code=response_status, detail=problem.model_dump())

    def _problem(
        error: (
            StateStoreError
            | RepositoryServiceError
            | ExecutionError
            | CleanupError
            | SecretServiceError
            | DeploymentError
            | ManagedMiddlewareError
        ),
        fallback_status: int = status.HTTP_400_BAD_REQUEST,
    ) -> Never:
        code = error.code
        detail = error.detail
        if code.endswith("NOT_FOUND") or code in {
            "WORKSPACE_NOT_FOUND",
            "PLAN_NOT_FOUND",
            "SECRET_MISSING",
        }:
            response_status = status.HTTP_404_NOT_FOUND
        elif code == "SECRET_STORE_UNAVAILABLE":
            response_status = status.HTTP_503_SERVICE_UNAVAILABLE
        elif code in {
            "MANAGED_MIDDLEWARE_OWNERSHIP_MISMATCH",
            "MANAGED_MIDDLEWARE_LIFECYCLE_CONFLICT",
        }:
            response_status = status.HTTP_409_CONFLICT
        elif code == "MANAGED_MIDDLEWARE_REMOVE_FAILED":
            response_status = status.HTTP_500_INTERNAL_SERVER_ERROR
        elif any(token in code for token in ("CONFLICT", "STALE", "IN_USE", "NOT_RUNNABLE")):
            response_status = status.HTTP_409_CONFLICT
        else:
            response_status = fallback_status
        try:
            _http_problem(
                response_status,
                code,
                detail,
                "刷新本地状态，修正配置后重试",
            )
        except HTTPException as http_error:
            raise http_error from error

    def _require_write_token(
        token: Annotated[str | None, Header(alias="x-pipedeck-token")] = None,
    ) -> None:
        if configured_token is None:
            _http_problem(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "WRITE_AUTH_NOT_CONFIGURED",
                "本地控制服务未配置写入 token",
                "通过 Tauri 启动，或同时设置 API 与 Web 的本地开发 token",
            )
        if token is None or not hmac.compare_digest(token, configured_token):
            _http_problem(
                status.HTTP_401_UNAUTHORIZED,
                "WRITE_AUTH_REQUIRED",
                "写操作需要当前 sidecar 生命周期 token",
                "重新打开桌面客户端，或检查本地开发 token 配置",
            )

    write_guard = [Depends(_require_write_token)]

    def _health() -> Response:
        return Response(content="ok", media_type="text/plain")

    def _session() -> SessionResponse:
        return SessionResponse(
            write_enabled=configured_token is not None,
            authentication="tauri-command-or-explicit-environment",
        )

    def _list_projects() -> CatalogResponse:
        return catalog.scan()

    def _list_repositories() -> RepositoryListResponse:
        return RepositoryListResponse(repositories=store.list_repositories())

    def _list_secrets() -> SecretListResponse:
        return SecretListResponse(secrets=secrets.list())

    def _create_secret(request: SecretCreateRequest) -> SecretMetadata:
        try:
            return secrets.create(request.name, request.value.get_secret_value())
        except (SecretServiceError, StateStoreError) as error:
            _problem(error)

    def _update_secret(secret_id: str, request: SecretUpdateRequest) -> SecretMetadata:
        try:
            return secrets.update(
                secret_id,
                request.expected_version,
                request.value.get_secret_value(),
            )
        except (SecretServiceError, StateStoreError) as error:
            _problem(error)

    def _delete_secret(
        secret_id: str,
        expected_version: Annotated[int, Query(ge=1)],
    ) -> Response:
        try:
            secrets.delete(secret_id, expected_version)
        except (SecretServiceError, StateStoreError) as error:
            _problem(error)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    def _import_repository(request: RepositoryImportRequest) -> RepositoryRecord:
        try:
            record = repositories.import_repository(request)
        except (RepositoryServiceError, StateStoreError) as error:
            _problem(error)
        catalog.invalidate()
        return record

    def _clone_repository(request: RepositoryCloneRequest) -> RepositoryRecord:
        try:
            record = repositories.clone_repository(request)
        except (RepositoryServiceError, StateStoreError) as error:
            _problem(error)
        catalog.invalidate()
        return record

    def _update_repository(repository_id: str) -> RepositoryRecord:
        try:
            record = repositories.update_repository(repository_id)
        except (RepositoryServiceError, StateStoreError) as error:
            _problem(error)
        catalog.invalidate()
        return record

    def _list_runtime_resources() -> RuntimeResponse:
        return runtime.snapshot()

    def _list_runtime_processes() -> RuntimeProcessListResponse:
        return execution.list_processes()

    def _list_managed_middleware(
        workspace_id: str | None = None,
    ) -> ManagedResourceListResponse:
        return ManagedResourceListResponse(
            resources=managed_middleware.list(workspace_id),
        )

    def _provision_managed_middleware(
        request: ManagedMiddlewareProvisionRequest,
    ) -> ManagedResourceRecord:
        if store.get_workspace(request.workspace_id) is None:
            _http_problem(
                status.HTTP_404_NOT_FOUND,
                "WORKSPACE_NOT_FOUND",
                "托管中间件对应的工作区不存在",
                "刷新工作区列表后重试",
            )
        try:
            return managed_middleware.provision(request)
        except (ManagedMiddlewareError, StateStoreError) as error:
            _problem(error)

    def _reconcile_managed_middleware(resource_id: str) -> ManagedResourceRecord:
        try:
            return managed_middleware.reconcile(resource_id)
        except (ManagedMiddlewareError, StateStoreError) as error:
            _problem(error)

    def _delete_managed_middleware(resource_id: str) -> ManagedResourceRecord:
        try:
            return managed_middleware.delete(resource_id)
        except (ManagedMiddlewareError, StateStoreError) as error:
            _problem(error)

    def _deployment_view(revision: DeploymentRevision) -> DeploymentRevisionView:
        intent = revision.intent
        return DeploymentRevisionView(
            revision_id=intent.revision_id,
            workspace_id=intent.workspace_id,
            project_id=intent.target_id,
            workspace_revision=intent.workspace_revision,
            project_name=revision.project_name,
            previous_revision_id=revision.previous_revision_id,
            status=revision.status.value,
            source_fingerprint=intent.source_fingerprint,
            target_config_fingerprint=intent.target_config_fingerprint,
            services=intent.services,
            immutable_images=intent.immutable_images,
            created_at=revision.created_at,
            updated_at=revision.updated_at,
            failure_code=revision.failure_code,
            failure_detail=revision.failure_detail,
            recovery_detail=revision.recovery_detail,
        )

    def _list_deployments(
        workspace_id: str | None = None,
        target_id: str | None = None,
    ) -> DeploymentRevisionListResponse:
        return DeploymentRevisionListResponse(
            deployments=tuple(
                _deployment_view(revision)
                for revision in store.list_deployment_revisions(workspace_id, target_id)
            )
        )

    def _reconcile_deployment(revision_id: str) -> DeploymentRevisionView:
        try:
            return _deployment_view(deployment_workflow.reconcile_revision(revision_id))
        except (DeploymentError, StateStoreError) as error:
            _problem(error)

    def _overview() -> OverviewResponse:
        project_snapshot = catalog.scan()
        runtime_snapshot = runtime.snapshot()
        present_kinds = {resource.kind for resource in runtime_snapshot.resources}
        protected_kinds = tuple(kind for kind in MiddlewareKind if kind in present_kinds)
        return OverviewResponse(
            generated_at=max(project_snapshot.generated_at, runtime_snapshot.generated_at),
            api_status="ready",
            docker_available=runtime_snapshot.docker_available,
            project_count=len(project_snapshot.projects),
            dirty_project_count=sum(project.dirty for project in project_snapshot.projects),
            middleware_count=len(runtime_snapshot.resources),
            protected_kinds=protected_kinds,
        )

    def _create_workspace(request: WorkspaceInput) -> WorkspaceRecord:
        try:
            return store.create_workspace(request)
        except StateStoreError as error:
            _problem(error)

    def _list_workspaces() -> WorkspaceListResponse:
        return WorkspaceListResponse(workspaces=store.list_workspaces())

    def _get_workspace(workspace_id: str) -> WorkspaceRecord:
        workspace = store.get_workspace(workspace_id)
        if workspace is None:
            _http_problem(
                status.HTTP_404_NOT_FOUND,
                "WORKSPACE_NOT_FOUND",
                "工作区不存在",
                "刷新工作区列表后重新选择",
            )
        return workspace

    def _update_workspace(
        workspace_id: str,
        request: WorkspaceUpdateRequest,
    ) -> WorkspaceRecord:
        try:
            return store.update_workspace(workspace_id, request)
        except StateStoreError as error:
            _problem(error)

    def _delete_workspace(
        workspace_id: str,
        expected_revision: Annotated[int, Query(ge=1)],
    ) -> Response:
        try:
            store.delete_workspace(workspace_id, expected_revision)
        except StateStoreError as error:
            _problem(error)
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    def _create_legacy_plan(request: WorkspacePlanRequest) -> WorkspacePlanResponse:
        return planner.create(request, catalog.scan(), runtime.snapshot())

    def _create_saved_plan(
        workspace_id: str,
        request: WorkspacePlanCreateRequest,
    ) -> WorkspacePlanResponse:
        workspace = store.get_workspace(workspace_id)
        if workspace is None:
            _http_problem(
                status.HTTP_404_NOT_FOUND,
                "WORKSPACE_NOT_FOUND",
                "工作区不存在",
                "刷新工作区列表后重新选择",
            )
        if workspace.revision != request.expected_revision:
            _http_problem(
                status.HTTP_409_CONFLICT,
                "WORKSPACE_REVISION_CONFLICT",
                "工作区配置已变化",
                "刷新工作区后重新预检",
            )
        catalog.invalidate()
        runtime.invalidate()
        plan = saved_planner.create(workspace, catalog.scan(), runtime.snapshot())
        try:
            return store.save_plan(plan)
        except StateStoreError as error:
            _problem(error)

    def _create_run(request: RunCreateRequest) -> RunRecord:
        try:
            return execution.start(request.plan_id, request.idempotency_key)
        except (ExecutionError, StateStoreError) as error:
            _problem(error)

    def _list_runs(workspace_id: str | None = None) -> RunListResponse:
        return RunListResponse(runs=store.list_runs(workspace_id))

    def _get_run(run_id: str) -> RunRecord:
        run = store.get_run(run_id)
        if run is None:
            _http_problem(
                status.HTTP_404_NOT_FOUND,
                "RUN_NOT_FOUND",
                "运行记录不存在",
                "刷新运行记录列表",
            )
        return run

    def _get_run_events(
        run_id: str,
        after: Annotated[int, Query(ge=0)] = 0,
    ) -> RunEventListResponse:
        try:
            return store.list_events(run_id, after)
        except StateStoreError as error:
            _problem(error)

    def _cancel_run(run_id: str) -> RunRecord:
        try:
            return execution.cancel(run_id)
        except (ExecutionError, StateStoreError) as error:
            _problem(error)

    def _retry_run(run_id: str, request: RunRetryRequest) -> RunRecord:
        previous = store.get_run(run_id)
        if previous is None:
            _http_problem(
                status.HTTP_404_NOT_FOUND,
                "RUN_NOT_FOUND",
                "运行记录不存在",
                "刷新运行记录列表",
            )
        if previous.status not in {
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
            RunStatus.INTERRUPTED,
        }:
            _http_problem(
                status.HTTP_409_CONFLICT,
                "RUN_NOT_RETRYABLE",
                "只能重试已结束的运行",
                "等待当前 Run 结束或先取消",
            )
        workspace = store.get_workspace(previous.workspace_id)
        if workspace is None:
            _http_problem(
                status.HTTP_404_NOT_FOUND,
                "WORKSPACE_NOT_FOUND",
                "原运行对应的工作区不存在",
                "重新创建工作区并预检",
            )
        catalog.invalidate()
        runtime.invalidate()
        refreshed_plan = saved_planner.create(
            workspace,
            catalog.scan(),
            runtime.snapshot(),
        )
        if not refreshed_plan.ready:
            _http_problem(
                status.HTTP_409_CONFLICT,
                "RETRY_PLAN_NOT_RUNNABLE",
                "当前源码或配置未通过重新预检",
                "打开工作区修正 blocker 后重新运行",
            )
        try:
            saved_plan = store.save_plan(refreshed_plan)
            if saved_plan.plan_id is None:
                _http_problem(
                    status.HTTP_500_INTERNAL_SERVER_ERROR,
                    "PLAN_IDENTITY_REQUIRED",
                    "重新预检未生成 Plan identity",
                    "重启本地控制服务后重试",
                )
            return execution.start(
                saved_plan.plan_id,
                request.idempotency_key,
                retry_of=run_id,
            )
        except (ExecutionError, StateStoreError) as error:
            _problem(error)

    def _create_cleanup_preview() -> CleanupPreviewResponse:
        return cleanup.preview()

    def _apply_cleanup(
        preview_id: str,
        request: CleanupApplyRequest,
    ) -> CleanupApplyResponse:
        if request.preview_id != preview_id:
            _http_problem(
                status.HTTP_409_CONFLICT,
                "CLEANUP_PREVIEW_ID_MISMATCH",
                "清理预览身份不一致",
                "重新生成清理预览",
            )
        try:
            return cleanup.apply(preview_id, request.resource_ids)
        except CleanupError as error:
            _problem(error)

    # pipedeck-ast: ignore[TG-DS001] - FastAPI transport method allowlists.
    get_methods = ["GET"]
    # pipedeck-ast: ignore[TG-DS001] - FastAPI transport method allowlists.
    post_methods = ["POST"]
    # pipedeck-ast: ignore[TG-DS001] - FastAPI transport method allowlists.
    put_methods = ["PUT"]
    # pipedeck-ast: ignore[TG-DS001] - FastAPI transport method allowlists.
    delete_methods = ["DELETE"]
    app.middleware("http")(_request_id_middleware)
    app.add_api_route("/health", _health, methods=get_methods)
    app.add_api_route(
        "/api/v1/session", _session, methods=get_methods, response_model=SessionResponse
    )
    app.add_api_route(
        "/api/v1/catalog/projects",
        _list_projects,
        methods=get_methods,
        response_model=CatalogResponse,
    )
    app.add_api_route(
        "/api/v1/repositories",
        _list_repositories,
        methods=get_methods,
        response_model=RepositoryListResponse,
    )
    app.add_api_route(
        "/api/v1/secrets",
        _list_secrets,
        methods=get_methods,
        response_model=SecretListResponse,
    )
    app.add_api_route(
        "/api/v1/secrets",
        _create_secret,
        methods=post_methods,
        response_model=SecretMetadata,
        status_code=status.HTTP_201_CREATED,
        dependencies=write_guard,
    )
    app.add_api_route(
        "/api/v1/secrets/{secret_id}",
        _update_secret,
        methods=put_methods,
        response_model=SecretMetadata,
        dependencies=write_guard,
    )
    app.add_api_route(
        "/api/v1/secrets/{secret_id}",
        _delete_secret,
        methods=delete_methods,
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=write_guard,
    )
    app.add_api_route(
        "/api/v1/repositories/import",
        _import_repository,
        methods=post_methods,
        response_model=RepositoryRecord,
        status_code=status.HTTP_201_CREATED,
        dependencies=write_guard,
    )
    app.add_api_route(
        "/api/v1/repositories/clone",
        _clone_repository,
        methods=post_methods,
        response_model=RepositoryRecord,
        status_code=status.HTTP_201_CREATED,
        dependencies=write_guard,
    )
    app.add_api_route(
        "/api/v1/repositories/{repository_id}/update",
        _update_repository,
        methods=post_methods,
        response_model=RepositoryRecord,
        dependencies=write_guard,
    )
    app.add_api_route(
        "/api/v1/runtime/resources",
        _list_runtime_resources,
        methods=get_methods,
        response_model=RuntimeResponse,
    )
    app.add_api_route(
        "/api/v1/runtime/processes",
        _list_runtime_processes,
        methods=get_methods,
        response_model=RuntimeProcessListResponse,
    )
    app.add_api_route(
        "/api/v1/managed-middleware",
        _list_managed_middleware,
        methods=get_methods,
        response_model=ManagedResourceListResponse,
    )
    app.add_api_route(
        "/api/v1/managed-middleware",
        _provision_managed_middleware,
        methods=post_methods,
        response_model=ManagedResourceRecord,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=write_guard,
    )
    app.add_api_route(
        "/api/v1/managed-middleware/{resource_id}/reconcile",
        _reconcile_managed_middleware,
        methods=post_methods,
        response_model=ManagedResourceRecord,
        status_code=status.HTTP_202_ACCEPTED,
        dependencies=write_guard,
    )
    app.add_api_route(
        "/api/v1/managed-middleware/{resource_id}",
        _delete_managed_middleware,
        methods=delete_methods,
        response_model=ManagedResourceRecord,
        dependencies=write_guard,
    )
    app.add_api_route(
        "/api/v1/deployments",
        _list_deployments,
        methods=get_methods,
        response_model=DeploymentRevisionListResponse,
    )
    app.add_api_route(
        "/api/v1/deployments/{revision_id}/reconcile",
        _reconcile_deployment,
        methods=post_methods,
        response_model=DeploymentRevisionView,
        dependencies=write_guard,
    )
    app.add_api_route(
        "/api/v1/overview",
        _overview,
        methods=get_methods,
        response_model=OverviewResponse,
    )
    app.add_api_route(
        "/api/v1/workspace-plans",
        _create_legacy_plan,
        methods=post_methods,
        response_model=WorkspacePlanResponse,
    )
    app.add_api_route(
        "/api/v1/workspaces",
        _list_workspaces,
        methods=get_methods,
        response_model=WorkspaceListResponse,
    )
    app.add_api_route(
        "/api/v1/workspaces",
        _create_workspace,
        methods=post_methods,
        response_model=WorkspaceRecord,
        status_code=status.HTTP_201_CREATED,
        dependencies=write_guard,
    )
    app.add_api_route(
        "/api/v1/workspaces/{workspace_id}",
        _get_workspace,
        methods=get_methods,
        response_model=WorkspaceRecord,
    )
    app.add_api_route(
        "/api/v1/workspaces/{workspace_id}",
        _update_workspace,
        methods=put_methods,
        response_model=WorkspaceRecord,
        dependencies=write_guard,
    )
    app.add_api_route(
        "/api/v1/workspaces/{workspace_id}",
        _delete_workspace,
        methods=delete_methods,
        status_code=status.HTTP_204_NO_CONTENT,
        dependencies=write_guard,
    )
    app.add_api_route(
        "/api/v1/workspaces/{workspace_id}/plans",
        _create_saved_plan,
        methods=post_methods,
        response_model=WorkspacePlanResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=write_guard,
    )
    app.add_api_route(
        "/api/v1/runs",
        _list_runs,
        methods=get_methods,
        response_model=RunListResponse,
    )
    app.add_api_route(
        "/api/v1/runs",
        _create_run,
        methods=post_methods,
        response_model=RunRecord,
        status_code=status.HTTP_201_CREATED,
        dependencies=write_guard,
    )
    app.add_api_route(
        "/api/v1/runs/{run_id}",
        _get_run,
        methods=get_methods,
        response_model=RunRecord,
    )
    app.add_api_route(
        "/api/v1/runs/{run_id}/events",
        _get_run_events,
        methods=get_methods,
        response_model=RunEventListResponse,
    )
    app.add_api_route(
        "/api/v1/runs/{run_id}/cancel",
        _cancel_run,
        methods=post_methods,
        response_model=RunRecord,
        dependencies=write_guard,
    )
    app.add_api_route(
        "/api/v1/runs/{run_id}/retry",
        _retry_run,
        methods=post_methods,
        response_model=RunRecord,
        status_code=status.HTTP_201_CREATED,
        dependencies=write_guard,
    )
    app.add_api_route(
        "/api/v1/runtime/cleanup-previews",
        _create_cleanup_preview,
        methods=post_methods,
        response_model=CleanupPreviewResponse,
        status_code=status.HTTP_201_CREATED,
        dependencies=write_guard,
    )
    app.add_api_route(
        "/api/v1/runtime/cleanup-previews/{preview_id}/apply",
        _apply_cleanup,
        methods=post_methods,
        response_model=CleanupApplyResponse,
        dependencies=write_guard,
    )
    return app


app = create_app()
