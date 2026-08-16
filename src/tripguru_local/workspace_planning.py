from __future__ import annotations

import hashlib
import json
import os
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from tripguru_local.compose_compiler import (
    ComposeCompilationError,
    ComposeCompiler,
)
from tripguru_local.compose_deployment import DeploymentIntent, HttpProbe
from tripguru_local.contracts import (
    ArgumentPortInjection,
    CatalogResponse,
    CommandEnvironmentVariable,
    CommandOwnedPortInjection,
    ComposeDeploymentPlan,
    ComposeTarget,
    EndpointProtocol,
    EnvironmentPortInjection,
    EnvironmentSource,
    HostTarget,
    HttpDeploymentProbeSpec,
    PlanCommand,
    PlanIssue,
    PlanStep,
    PlanStepKind,
    ProjectCommand,
    ProjectKind,
    ProjectSummary,
    RuntimeResponse,
    TcpDeploymentProbeSpec,
    WorkspacePlanRequest,
    WorkspacePlanResponse,
    WorkspaceRecord,
)
from tripguru_local.planning import WorkspacePlanner
from tripguru_local.processes import CommandRunner


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    project_id: str
    path: str
    branch: str
    head: str
    dirty: bool
    status: str
    content_fingerprint: str


class SavedWorkspacePlanner:
    def __init__(
        self,
        command_runner: CommandRunner,
        compose_compiler: ComposeCompiler | None = None,
    ) -> None:
        self._command_runner = command_runner
        self._planner = WorkspacePlanner()
        self._compose_compiler = compose_compiler

    def create(
        self,
        workspace: WorkspaceRecord,
        catalog: CatalogResponse,
        runtime: RuntimeResponse,
    ) -> WorkspacePlanResponse:
        plan_id = uuid4().hex
        projects, source_parts, source_issues = self._projects(workspace, catalog)
        configured_catalog = CatalogResponse(
            generated_at=catalog.generated_at,
            roots=catalog.roots,
            projects=projects,
            errors=catalog.errors,
        )
        response = self._planner.create(
            WorkspacePlanRequest(
                project_ids=tuple(service.project_id for service in workspace.services),
                mode=workspace.mode,
                bindings=workspace.bindings,
            ),
            configured_catalog,
            runtime,
        )
        blockers = [
            issue
            for issue in response.blockers
            if issue.code not in {"START_COMMAND_UNRESOLVED", "BUILD_COMMAND_UNRESOLVED"}
        ]
        blockers.extend(source_issues)
        blockers.extend(self._environment_issues(workspace))
        blockers.extend(self._target_issues(workspace, response.projects))
        blockers.extend(self._port_issues(workspace, runtime))
        steps = self._target_steps(workspace, response.projects, response.steps)
        config_payload = workspace.model_dump(
            mode="json",
            exclude={"created_at", "updated_at"},
        )
        config_fingerprint = self._fingerprint(config_payload)
        source_fingerprint = self._source_fingerprint(source_parts)
        deployments, deployment_issues = self._compile_deployments(
            workspace,
            response.projects,
            runtime,
            source_fingerprint,
            config_fingerprint,
            plan_id,
        )
        blockers.extend(deployment_issues)
        steps = self._with_deployment_step(steps, deployments)
        return WorkspacePlanResponse(
            generated_at=response.generated_at,
            ready=not blockers,
            mode=response.mode,
            projects=response.projects,
            steps=steps,
            blockers=tuple(blockers),
            warnings=response.warnings,
            plan_id=plan_id,
            workspace_id=workspace.id,
            workspace_revision=workspace.revision,
            config_fingerprint=config_fingerprint,
            source_fingerprint=source_fingerprint,
        )

    def source_fingerprint(
        self, workspace: WorkspaceRecord, catalog: CatalogResponse
    ) -> tuple[str, tuple[PlanIssue, ...]]:
        _, parts, issues = self._projects(workspace, catalog)
        return self._source_fingerprint(parts), issues

    def _projects(
        self,
        workspace: WorkspaceRecord,
        catalog: CatalogResponse,
    ) -> tuple[
        tuple[ProjectSummary, ...],
        tuple[SourceSnapshot, ...],
        tuple[PlanIssue, ...],
    ]:
        projects: list[ProjectSummary] = []
        source_parts: list[SourceSnapshot] = []
        issues: list[PlanIssue] = []
        for service in workspace.services:
            project = next(
                (item for item in catalog.projects if item.id == service.project_id),
                None,
            )
            if (
                project is not None
                and project.kind is ProjectKind.COMPOSE
                and isinstance(service.execution_target, HostTarget)
            ):
                issues.append(
                    PlanIssue(
                        code="EXPLICIT_COMPOSE_TARGET_REQUIRED",
                        title=f"{service.project_id} 必须选择 Compose target",
                        detail="catalog 的 Compose 能力不能作为 unmanaged Host 命令执行",
                        recovery="配置 existing-compose 或 Dockerfile source 后重新预检",
                    )
                )
                continue
            if project is None:
                continue
            status = self._command_runner.run(("git", "status", "--porcelain"), Path(project.path))
            head = self._command_runner.run(("git", "rev-parse", "HEAD"), Path(project.path))
            content_fingerprint = self._content_fingerprint(Path(project.path))
            if status.return_code != 0 or head.return_code != 0 or content_fingerprint is None:
                issues.append(
                    PlanIssue(
                        code="SOURCE_STATE_UNAVAILABLE",
                        title=f"无法确认 {project.name} 的源码状态",
                        detail="执行前必须读取当前 Git HEAD 与工作树状态",
                        recovery="确认目录仍是可访问的 Git 工作树后重新预检",
                    )
                )
                dirty = True
                head_sha = "unavailable"
                content_hash = "unavailable"
            else:
                dirty = bool(status.stdout)
                head_sha = head.stdout
                content_hash = content_fingerprint

            commands = list(project.commands)
            for configured in service.commands:
                replacement = ProjectCommand(
                    id=configured.id,
                    label=configured.label,
                    argv=configured.argv,
                    kind=configured.kind,
                    long_running=configured.long_running,
                )
                existing_index = next(
                    (
                        index
                        for index, command in enumerate(commands)
                        if command.id == configured.id
                    ),
                    None,
                )
                if existing_index is None:
                    commands.append(replacement)
                else:
                    commands[existing_index] = replacement
            projects.append(
                ProjectSummary(
                    id=project.id,
                    name=project.name,
                    path=project.path,
                    kind=project.kind,
                    branch=project.branch,
                    dirty=dirty,
                    commands=tuple(commands),
                    requirements=project.requirements,
                    warnings=project.warnings,
                    container_capabilities=project.container_capabilities,
                )
            )
            source_parts.append(
                SourceSnapshot(
                    project_id=project.id,
                    path=project.path,
                    branch=project.branch,
                    head=head_sha,
                    dirty=dirty,
                    status=status.stdout if status.return_code == 0 else "unavailable",
                    content_fingerprint=content_hash,
                )
            )
        return tuple(projects), tuple(source_parts), tuple(issues)

    @staticmethod
    def _environment_issues(workspace: WorkspaceRecord) -> tuple[PlanIssue, ...]:
        issues: list[PlanIssue] = []
        for service in workspace.services:
            for binding in service.environment:
                if (
                    binding.source is EnvironmentSource.HOST_ENV
                    and binding.reference is not None
                    and binding.reference not in os.environ
                ):
                    issues.append(
                        PlanIssue(
                            code="ENVIRONMENT_REFERENCE_MISSING",
                            title=f"缺少环境引用 {binding.reference}",
                            detail=f"{binding.name} 只保存引用，当前控制服务环境无法解析该值",
                            recovery=f"在启动 TripGuru Local 前设置 {binding.reference}",
                        )
                    )
        return tuple(issues)

    @staticmethod
    def _target_issues(
        workspace: WorkspaceRecord,
        projects: tuple[ProjectSummary, ...],
    ) -> tuple[PlanIssue, ...]:
        issues: list[PlanIssue] = []
        for service in workspace.services:
            target = service.execution_target
            if isinstance(target, ComposeTarget):
                continue
            project = next(
                (item for item in projects if item.id == service.project_id),
                None,
            )
            start_commands = (
                tuple(
                    command
                    for command in project.commands
                    if SavedWorkspacePlanner._is_start_command(command)
                )
                if project is not None
                else ()
            )
            build_commands = (
                tuple(
                    command
                    for command in project.commands
                    if SavedWorkspacePlanner._is_build_command(command)
                )
                if project is not None
                else ()
            )
            if not start_commands:
                issues.append(
                    PlanIssue(
                        code="START_COMMAND_UNRESOLVED",
                        title=f"{service.project_id} 缺少启动契约",
                        detail="宿主机 target 没有可解析的 start 命令",
                        recovery="配置真实 start 命令后重新预检",
                    )
                )
            if workspace.mode.value == "integrated" and not build_commands:
                issues.append(
                    PlanIssue(
                        code="BUILD_COMMAND_UNRESOLVED",
                        title=f"{service.project_id} 缺少构建契约",
                        detail="集成模式的宿主机 target 没有可解析的 build 命令",
                        recovery="配置真实 build 命令或切换 development 模式",
                    )
                )
            endpoint_names = tuple(endpoint.name for endpoint in target.endpoints)
            duplicate_names = {name for name in endpoint_names if endpoint_names.count(name) > 1}
            for name in sorted(duplicate_names):
                issues.append(
                    PlanIssue(
                        code="TARGET_ENDPOINT_NAME_DUPLICATE",
                        title=f"{service.project_id} 的 endpoint 名称重复：{name}",
                        detail="readiness 与注入必须引用服务内唯一 endpoint",
                        recovery="为每个 endpoint 配置唯一名称",
                    )
                )
            if target.readiness is None and any(command.long_running for command in start_commands):
                issues.append(
                    PlanIssue(
                        code="READINESS_REQUIRED",
                        title=f"{service.project_id} 缺少 readiness",
                        detail="长期运行的宿主机服务必须声明可验证的就绪条件",
                        recovery="选择 TCP 或 HTTP readiness 并引用一个 TCP endpoint",
                    )
                )
            elif target.readiness is not None:
                readiness_endpoint = next(
                    (
                        endpoint
                        for endpoint in target.endpoints
                        if endpoint.name == target.readiness.endpoint
                    ),
                    None,
                )
                if readiness_endpoint is None:
                    issues.append(
                        PlanIssue(
                            code="READINESS_ENDPOINT_MISSING",
                            title=f"{service.project_id} 的 readiness endpoint 不存在",
                            detail=f"未找到 endpoint {target.readiness.endpoint}",
                            recovery="选择当前 target 中存在的 endpoint",
                        )
                    )
                elif readiness_endpoint.protocol is not EndpointProtocol.TCP:
                    issues.append(
                        PlanIssue(
                            code="READINESS_ENDPOINT_PROTOCOL_UNSUPPORTED",
                            title=f"{service.project_id} 的 readiness 不能使用 UDP",
                            detail="当前 HTTP/TCP readiness 只支持 TCP endpoint",
                            recovery="改用 TCP endpoint 或移除该 readiness",
                        )
                    )
            for endpoint in target.endpoints:
                if not isinstance(endpoint.injection, CommandOwnedPortInjection):
                    continue
                if not any(
                    SavedWorkspacePlanner._argv_contains_port(command.argv, endpoint.host_port)
                    for command in start_commands
                ):
                    issues.append(
                        PlanIssue(
                            code="TARGET_PORT_COMMAND_MISSING",
                            title=(
                                f"{service.project_id} 的端口 {endpoint.host_port} 未出现在启动命令"
                            ),
                            detail="command-owned endpoint 必须由真实 start argv 明确拥有",
                            recovery="在 start argv 中声明端口，或选择 environment/argument 注入",
                        )
                    )
        return tuple(issues)

    def _compile_deployments(
        self,
        workspace: WorkspaceRecord,
        projects: tuple[ProjectSummary, ...],
        runtime: RuntimeResponse,
        source_fingerprint: str,
        config_fingerprint: str,
        revision_nonce: str,
    ) -> tuple[tuple[ComposeDeploymentPlan, ...], tuple[PlanIssue, ...]]:
        deployments: list[ComposeDeploymentPlan] = []
        issues: list[PlanIssue] = []
        for service in workspace.services:
            target = service.execution_target
            if not isinstance(target, ComposeTarget):
                continue
            project = next(
                (item for item in projects if item.id == service.project_id),
                None,
            )
            if project is None:
                continue
            if not runtime.docker_available:
                issues.append(
                    PlanIssue(
                        code="DOCKER_UNAVAILABLE",
                        title=f"{project.name} 无法部署",
                        detail="Compose target 需要本机 Docker Desktop",
                        recovery="启动 Docker Desktop 后刷新并重新预检",
                    )
                )
                continue
            if self._compose_compiler is None:
                issues.append(
                    PlanIssue(
                        code="DEPLOYMENT_COMPILER_UNAVAILABLE",
                        title=f"{project.name} 的部署编译器不可用",
                        detail="控制服务当前未配置 Compose artifact owner",
                        recovery="重启 TripGuru Local 控制服务后重新预检",
                    )
                )
                continue
            try:
                intent = self._compose_compiler.compile(
                    workspace=workspace,
                    project=project,
                    target=target,
                    source_fingerprint=source_fingerprint,
                    target_config_fingerprint=config_fingerprint,
                    revision_nonce=revision_nonce,
                )
            except ComposeCompilationError as error:
                issues.append(
                    PlanIssue(
                        code=f"COMPOSE_{error.problem.name}",
                        title=f"{project.name} 的 Compose 配置无法冻结",
                        detail=str(error),
                        recovery="修正 Compose target 或仓库容器配置后重新预检",
                    )
                )
                continue
            deployments.append(self._deployment_plan(intent))
        return tuple(deployments), tuple(issues)

    @staticmethod
    def _deployment_plan(intent: DeploymentIntent) -> ComposeDeploymentPlan:
        if isinstance(intent.probe, HttpProbe):
            probe = HttpDeploymentProbeSpec(
                url=intent.probe.url,
                timeout_seconds=intent.probe.timeout_seconds,
            )
        else:
            probe = TcpDeploymentProbeSpec(
                host=intent.probe.host,
                port=intent.probe.port,
                timeout_seconds=intent.probe.timeout_seconds,
            )
        return ComposeDeploymentPlan(
            revision_id=intent.revision_id,
            workspace_id=intent.workspace_id,
            project_id=intent.target_id,
            workspace_revision=intent.workspace_revision,
            source_fingerprint=intent.source_fingerprint,
            target_config_fingerprint=intent.target_config_fingerprint,
            checkout_path=str(intent.checkout_path),
            frozen_compose_path=str(intent.frozen_compose_path),
            services=intent.services,
            immutable_images=intent.immutable_images,
            wait_timeout_seconds=intent.wait_timeout_seconds,
            probe=probe,
            environment_spec=intent.environment_spec,
        )

    @staticmethod
    def _with_deployment_step(
        steps: tuple[PlanStep, ...],
        deployments: tuple[ComposeDeploymentPlan, ...],
    ) -> tuple[PlanStep, ...]:
        if not deployments:
            return steps
        deployment_step = PlanStep(
            id="deploy",
            kind=PlanStepKind.DEPLOY,
            title="部署容器目标",
            detail=f"构建并替换 {len(deployments)} 个本地 Compose target",
            commands=(),
            deployments=deployments,
        )
        result: list[PlanStep] = []
        inserted = False
        for step in steps:
            if not inserted and step.kind is PlanStepKind.START:
                result.append(deployment_step)
                inserted = True
            result.append(step)
        if not inserted:
            result.append(deployment_step)
        return tuple(result)

    @staticmethod
    def _port_issues(
        workspace: WorkspaceRecord,
        runtime: RuntimeResponse,
    ) -> tuple[PlanIssue, ...]:
        issues: list[PlanIssue] = []
        seen: set[int] = set()
        for service in workspace.services:
            target = service.execution_target
            if not isinstance(target, HostTarget):
                continue
            for endpoint in target.endpoints:
                port = endpoint.host_port
                owned_runtime_port = any(
                    resource.owner_workspace_id == workspace.id and f":{port}->" in resource.ports
                    for resource in runtime.resources
                )
                if port in seen:
                    issues.append(
                        PlanIssue(
                            code="TARGET_PORT_DUPLICATE",
                            title=f"端口 {port} 在 Host target 中重复",
                            detail="同一 Workspace 的宿主机服务不能拥有相同 host_port",
                            recovery="为 endpoint 配置唯一 host_port",
                        )
                    )
                elif not owned_runtime_port and SavedWorkspacePlanner._port_in_use(port):
                    issues.append(
                        PlanIssue(
                            code="PORT_UNAVAILABLE",
                            title=f"端口 {port} 不可用",
                            detail="端口已被当前工作区重复配置或被本机进程占用",
                            recovery="修改服务端口或停止占用该端口的本机进程",
                        )
                    )
                seen.add(port)
        return tuple(issues)

    @staticmethod
    def _target_steps(
        workspace: WorkspaceRecord,
        projects: tuple[ProjectSummary, ...],
        steps: tuple[PlanStep, ...],
    ) -> tuple[PlanStep, ...]:
        compose_projects = {
            project.id for project in projects if project.kind is ProjectKind.COMPOSE
        }
        result: list[PlanStep] = []
        for step in steps:
            commands: list[PlanCommand] = []
            for command in step.commands:
                service = next(
                    (item for item in workspace.services if item.project_id == command.project_id),
                    None,
                )
                if service is None:
                    commands.append(command)
                    continue
                target = service.execution_target
                if (
                    isinstance(target, ComposeTarget) or command.project_id in compose_projects
                ) and step.kind in {
                    PlanStepKind.BUILD,
                    PlanStepKind.DEPLOY,
                    PlanStepKind.START,
                }:
                    continue
                if isinstance(target, HostTarget) and step.kind is PlanStepKind.START:
                    commands.append(SavedWorkspacePlanner._inject_host_endpoints(command, target))
                else:
                    commands.append(command)
            if commands:
                result.append(
                    PlanStep(
                        id=step.id,
                        kind=step.kind,
                        title=step.title,
                        detail=step.detail,
                        commands=tuple(commands),
                    )
                )
        return tuple(result)

    @staticmethod
    def _inject_host_endpoints(command: PlanCommand, target: HostTarget) -> PlanCommand:
        argv = command.argv
        environment = list(command.environment)
        for endpoint in target.endpoints:
            injection = endpoint.injection
            if isinstance(injection, EnvironmentPortInjection):
                environment.append(
                    CommandEnvironmentVariable(
                        name=injection.name,
                        value=str(endpoint.host_port),
                    )
                )
            elif isinstance(injection, ArgumentPortInjection):
                argv = (*argv, injection.option, str(endpoint.host_port))
        return PlanCommand(
            project_id=command.project_id,
            project_name=command.project_name,
            command_id=command.command_id,
            label=command.label,
            cwd=command.cwd,
            argv=argv,
            environment=tuple(environment),
            long_running=command.long_running,
        )

    @staticmethod
    def _is_start_command(command: ProjectCommand) -> bool:
        return command.kind is PlanStepKind.START or (
            command.kind is None and command.id in {"dev", "start"}
        )

    @staticmethod
    def _is_build_command(command: ProjectCommand) -> bool:
        return command.kind is PlanStepKind.BUILD or (
            command.kind is None and command.id == "build"
        )

    @staticmethod
    def _argv_contains_port(argv: tuple[str, ...], port: int) -> bool:
        pattern = re.compile(rf"(?<!\d){port}(?!\d)")
        return any(pattern.search(token) is not None for token in argv)

    def _content_fingerprint(self, path: Path) -> str | None:
        diff = self._command_runner.run(
            ("git", "diff", "--binary", "--no-ext-diff", "HEAD"),
            path,
        )
        untracked = self._command_runner.run(
            ("git", "ls-files", "--others", "--exclude-standard", "-z"),
            path,
        )
        if diff.return_code != 0 or untracked.return_code != 0:
            return None
        digest = hashlib.sha256()
        digest.update(diff.stdout.encode("utf-8"))
        relative_paths = tuple(item for item in untracked.stdout.split("\0") if item)
        for relative_path in sorted(relative_paths):
            candidate = path / relative_path
            try:
                relative = candidate.relative_to(path)
                if candidate.is_symlink():
                    content = os.readlink(candidate).encode("utf-8")
                elif candidate.is_file():
                    content = candidate.read_bytes()
                else:
                    continue
            except OSError:
                return None
            digest.update(relative.as_posix().encode("utf-8"))
            digest.update(b"\0")
            digest.update(content)
            digest.update(b"\0")
        return digest.hexdigest()

    @staticmethod
    def _port_in_use(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
            connection.settimeout(0.08)
            return connection.connect_ex(("127.0.0.1", port)) == 0

    @staticmethod
    def _fingerprint(payload: object) -> str:
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _source_fingerprint(parts: tuple[SourceSnapshot, ...]) -> str:
        encoded = "\n".join(
            "\0".join(
                (
                    part.project_id,
                    part.path,
                    part.branch,
                    part.head,
                    "dirty" if part.dirty else "clean",
                    part.status,
                    part.content_fingerprint,
                )
            )
            for part in parts
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
