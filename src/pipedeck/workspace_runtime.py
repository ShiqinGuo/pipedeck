"""Read-only observations of every service in a local integration workspace."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

from pipedeck.compose_deployment import HttpProbe
from pipedeck.contracts import (
    ComposeTarget,
    ExecutionTarget,
    HostTarget,
    HttpReadiness,
    RunRecord,
    RunStatus,
    RuntimeProcessRecord,
    ServiceRuntimeView,
    WorkspacePlanResponse,
    WorkspaceRecord,
    WorkspaceRuntimeResponse,
    WorkspaceService,
)
from pipedeck.deployment_control import DockerDeploymentRuntimeInspector
from pipedeck.execution import ExecutionEngine
from pipedeck.readiness import LocalReadinessTransport
from pipedeck.state_store import StateStore


class WorkspaceRuntimeObserver:
    def __init__(
        self,
        store: StateStore,
        execution: ExecutionEngine,
        inspector: DockerDeploymentRuntimeInspector,
    ) -> None:
        self._store = store
        self._execution = execution
        self._inspector = inspector

    def snapshot(self, workspace: WorkspaceRecord) -> WorkspaceRuntimeResponse:
        runs = self._store.list_runs(workspace.id)
        processes = self._execution.list_processes().processes
        services = list(workspace.services)
        configured_ids = {service.project_id for service in services}
        added_ids = set(configured_ids)
        # A removed project can still own a running process. Keep it visible until stopped.
        run_by_id = {run.id: run for run in runs}
        for process in processes:
            if process.project_id in added_ids or process.run_id not in run_by_id:
                continue
            plan = self._store.get_plan(run_by_id[process.run_id].plan_id)
            target = plan.service_targets.get(process.project_id) if plan else None
            if target is not None:
                services.append(
                    WorkspaceService(project_id=process.project_id, execution_target=target)
                )
                added_ids.add(process.project_id)
        # Removed Compose targets can outlive their completed deployment run.
        for revision in self._store.list_deployment_revisions(workspace.id):
            target_id = revision.intent.target_id
            if target_id not in added_ids:
                services.append(WorkspaceService(project_id=target_id))
                added_ids.add(target_id)

        def observe(service: WorkspaceService) -> ServiceRuntimeView:
            view = self._service(workspace, service, runs, processes)
            if service.project_id not in configured_ids:
                return view.model_copy(
                    update={
                        "configured": False,
                        "recovery": "此服务已从配置中移除；请在资源页检查并清理旧部署"
                        if view.target == "compose"
                        else "此服务已从配置中移除，但进程仍在运行；请打开对应运行记录停止",
                    }
                )
            return view

        # Independent probes are bounded and do not reconcile or mutate runtime resources.
        with ThreadPoolExecutor(max_workers=min(8, len(services))) as pool:
            views = tuple(
                pool.map(
                    observe,
                    services,
                )
            )
        views = tuple(view for view in views if view.configured or view.status != "stopped")
        count = sum(view.status == "ready" for view in views)
        return WorkspaceRuntimeResponse(
            workspace_id=workspace.id,
            generated_at=datetime.now(UTC),
            ready=count == len(views) and all(view.configured for view in views),
            ready_count=count,
            services=views,
            latest_run=runs[0] if runs else None,
        )

    def _service(
        self,
        workspace: WorkspaceRecord,
        service: WorkspaceService,
        runs: tuple[RunRecord, ...],
        processes: tuple[RuntimeProcessRecord, ...],
    ) -> ServiceRuntimeView:
        repository = self._store.get_repository(service.project_id)
        view = ServiceRuntimeView(
            project_id=service.project_id,
            name=repository.name if repository else service.project_id,
            target=service.execution_target.kind,
            status="stopped",
            detail="尚无运行中的服务",
            recovery="保存配置并运行预检，启动本地集成环境",
        )
        run_by_id = {run.id: run for run in runs}
        active = [
            p
            for p in processes
            if p.project_id == service.project_id and p.long_running and p.run_id in run_by_id
        ]
        if not active:
            if isinstance(
                service.execution_target, ComposeTarget
            ) or self._store.list_deployment_revisions(workspace.id, service.project_id):
                return self._compose(workspace, service, view, runs)
            if any(
                run.status in {RunStatus.RUNNING, RunStatus.QUEUED}
                and (plan := self._store.get_plan(run.plan_id)) is not None
                and service.project_id in plan.service_targets
                for run in runs
            ):
                return view.model_copy(
                    update={"status": "starting", "detail": "等待构建或依赖服务就绪"}
                )
            return view
        if len({p.run_id for p in active}) != 1:
            return view.model_copy(
                update={
                    "status": "unknown",
                    "detail": "存在多个运行实例，无法唯一确定测试目标",
                    "recovery": "在运行历史中停止多余实例后刷新",
                }
            )
        run = run_by_id[active[0].run_id]
        plan = self._store.get_plan(run.plan_id)
        view = self._source(view, run, plan)
        view = view.model_copy(update={"target": "host"})
        target = plan.service_targets.get(service.project_id) if plan else None
        if not isinstance(target, HostTarget) or target.readiness is None:
            return view.model_copy(
                update={
                    "status": "unknown",
                    "detail": "进程正在运行，但缺少已保存的就绪检查",
                    "recovery": "配置 HTTP/TCP 就绪检查并重新预检运行",
                }
            )
        endpoint = next((e for e in target.endpoints if e.name == target.readiness.endpoint), None)
        if endpoint is None:
            return view.model_copy(update={"status": "unknown", "detail": "就绪检查入口不存在"})
        transport = LocalReadinessTransport()
        if isinstance(target.readiness, HttpReadiness):
            ready = transport.http_ready(
                f"http://127.0.0.1:{endpoint.host_port}{target.readiness.path}", 0.5
            )
        else:
            ready = transport.tcp_ready("127.0.0.1", endpoint.host_port, 0.5)
        url = self._application_url(target) if ready else None
        return view.model_copy(
            update={
                "status": "ready" if ready else "unhealthy",
                "url": url if ready else None,
                "detail": "就绪检查通过" if ready else "进程正在运行，就绪检查未通过",
                "recovery": (
                    "应用入口暂不可访问，请检查路径或稍后刷新"
                    if target.application and not url
                    else None
                )
                if ready
                else "打开该服务运行日志，检查依赖连接和启动错误后刷新",
            }
        )

    def _compose(
        self,
        workspace: WorkspaceRecord,
        service: WorkspaceService,
        view: ServiceRuntimeView,
        runs: tuple[RunRecord, ...],
    ) -> ServiceRuntimeView:
        view = view.model_copy(update={"target": "compose"})
        revisions = self._store.list_deployment_revisions(workspace.id, service.project_id)
        if not revisions:
            return view
        try:
            observed = self._inspector.inspect(
                workspace.id, service.project_id, wait_for_readiness=False
            )
        except OSError:
            return view.model_copy(
                update={
                    "status": "unknown",
                    "detail": "无法读取 Docker 状态",
                    "recovery": "启动 Docker Desktop 后刷新",
                }
            )
        revision = next(
            (r for r in revisions if r.intent.revision_id == observed.revision_id), None
        )
        if observed.error_code:
            return view.model_copy(
                update={
                    "status": "unknown",
                    "detail": observed.error_code,
                    "recovery": "检查 Docker Desktop 连接后刷新",
                }
            )
        if not observed.target_present:
            return view
        if revision is None:
            return view.model_copy(
                update={
                    "status": "unknown",
                    "detail": "容器版本与部署记录不一致",
                    "recovery": "在资源页检查部署记录并执行状态对账",
                }
            )
        intent = revision.intent
        target: ExecutionTarget | None = None
        for run in runs:
            plan = self._store.get_plan(run.plan_id)
            if plan and any(
                d.revision_id == intent.revision_id for s in plan.steps for d in s.deployments
            ):
                view = self._source(view, run, plan)
                target = plan.service_targets.get(service.project_id)
                break
        url = None
        if observed.probe_ready and target is not None:
            url = self._application_url(target)
        elif observed.probe_ready and isinstance(intent.probe, HttpProbe):
            parsed = urlsplit(intent.probe.url)
            candidate = f"{parsed.scheme}://{parsed.netloc}/"
            if LocalReadinessTransport().http_ready(candidate, 0.5):
                url = candidate
        return view.model_copy(
            update={
                "revision_id": intent.revision_id,
                "target": "compose",
                "workspace_revision": intent.workspace_revision,
                "status": "ready" if observed.probe_ready else "unhealthy",
                "url": url,
                "detail": "容器及就绪检查通过" if observed.probe_ready else "容器或就绪检查未通过",
                "recovery": (
                    "应用入口暂不可访问，请检查路径或稍后刷新"
                    if target and target.application and not url
                    else None
                )
                if observed.probe_ready
                else "检查服务日志和依赖，必要时在资源页执行对账",
            }
        )

    @staticmethod
    def _application_url(target: ExecutionTarget) -> str | None:
        entry = target.application
        endpoint_name = (
            entry.endpoint
            if entry
            else (
                target.readiness.endpoint if isinstance(target.readiness, HttpReadiness) else None
            )
        )
        endpoint = next((item for item in target.endpoints if item.name == endpoint_name), None)
        if endpoint is None or endpoint.protocol.value != "tcp":
            return None
        path = quote(entry.path if entry else "/", safe="/%?=&:#")
        candidate = f"http://127.0.0.1:{endpoint.host_port}{path}"
        return candidate if LocalReadinessTransport().http_ready(candidate, 0.5) else None

    @staticmethod
    def _source(
        view: ServiceRuntimeView, run: RunRecord, plan: WorkspacePlanResponse | None
    ) -> ServiceRuntimeView:
        project = (
            next((p for p in plan.projects if p.id == view.project_id), None) if plan else None
        )
        return view.model_copy(
            update={
                "run_id": run.id,
                "workspace_revision": run.workspace_revision,
                "name": project.name if project else view.name,
                "branch": project.branch if project else None,
                "dirty": project.dirty if project else None,
                "head": plan.project_heads.get(view.project_id) if plan else None,
            }
        )
