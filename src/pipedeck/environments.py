"""Environment：Workspace × 源项目 × ref 的独立 worktree。

创建 = `git worktree add` 到平台拥有的 state 目录 + 注册为独立 checkout
（checkout 拥有 HEAD/branch/dirty 事实，应用后替换工作区中对应项目的源码）。
删除 = dirty 阻断 + `git worktree remove` + 移除 checkout 注册；
关联的 Compose 资源由清理服务按 ownership label 治理。
"""

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

from pipedeck.contracts import (
    EnvironmentCreateRequest,
    EnvironmentRecord,
    RepositoryRecord,
    RunStatus,
    WorkspaceRecord,
    WorkspaceUpdateRequest,
)
from pipedeck.processes import CommandRunner, SubprocessRunner
from pipedeck.repositories import (
    RepositoryDirtyError,
    RepositoryService,
)
from pipedeck.state_store import StateStore

_SLUG_TOKEN = re.compile(r"[^a-zA-Z0-9_-]+")


class EnvironmentError(Exception):
    code = "ENVIRONMENT_OPERATION_FAILED"
    recovery = "刷新工作区，确认目标项目和检出状态后重试"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class EnvironmentRunActiveError(EnvironmentError):
    code = "ENVIRONMENT_RUN_ACTIVE"

    def __init__(self, run_id: str) -> None:
        self.recovery = f"打开运行 {run_id}，停止运行并等待进程退出后重试"
        super().__init__(f"分支检出仍被运行 {run_id} 使用")


class EnvironmentWorkspaceMissingError(EnvironmentError):
    code = "WORKSPACE_NOT_FOUND"

    def __init__(self, workspace_id: str) -> None:
        super().__init__(f"Workspace not found: {workspace_id}")


class EnvironmentRefInvalidError(EnvironmentError):
    code = "ENVIRONMENT_REF_INVALID"

    def __init__(self, ref: str, detail: str = "") -> None:
        super().__init__(f"Cannot create worktree from ref: {ref} ({detail})")


class EnvironmentRepositoryMissingError(EnvironmentError):
    code = "ENVIRONMENT_REPOSITORY_MISSING"

    def __init__(self, workspace_id: str) -> None:
        super().__init__(
            f"Workspace {workspace_id} has no associated repository; "
            f"cannot create a worktree environment"
        )


class EnvironmentService:
    def __init__(
        self,
        store: StateStore,
        repositories: RepositoryService,
        state_dir: Path,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self._store = store
        self._repositories = repositories
        self._worktree_root = state_dir / "worktrees"
        self._command_runner = command_runner or SubprocessRunner()

    def create(self, workspace_id: str, request: EnvironmentCreateRequest) -> EnvironmentRecord:
        workspace = self._store.get_workspace(workspace_id)
        if workspace is None:
            raise EnvironmentWorkspaceMissingError(workspace_id)
        repository = self._primary_repository(workspace, request.repository_id)
        source_repository_id = next(
            (
                item.source_repository_id or repository.id
                for item in self._store.list_environments(workspace_id)
                if item.repository_id == repository.id
            ),
            repository.id,
        )
        ref = request.ref
        identity = f"{workspace_id}:{source_repository_id}"
        worktree_dir = self._worktree_dir(workspace_id, f"{source_repository_id}/{ref}")
        if worktree_dir.exists():
            raise EnvironmentError(f"Environment directory already exists: {worktree_dir}")
        worktree_dir.parent.mkdir(parents=True, exist_ok=True)
        # --detach: 以目标 ref 的 commit 建独立 worktree,不占用分支名,
        # 因此 main(主 checkout) 与已 checkout 的分支也能并存部署。
        add = self._command_runner.run(
            ("git", "worktree", "add", "--detach", str(worktree_dir), ref),
            cwd=Path(repository.path),
        )
        if add.return_code != 0:
            raise EnvironmentRefInvalidError(ref, add.stderr.strip())
        registered = self._repositories.register_worktree(worktree_dir)
        return self._store.upsert_environment(
            EnvironmentRecord(
                id=self._environment_id(identity, ref),
                workspace_id=workspace_id,
                repository_id=registered.id,
                source_repository_id=source_repository_id,
                ref=ref,
                worktree_path=str(worktree_dir),
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
        )

    def list(self, workspace_id: str | None = None) -> tuple[EnvironmentRecord, ...]:
        return self._store.list_environments(workspace_id)

    def delete(self, environment_id: str) -> EnvironmentRecord:
        record = self._store.get_environment(environment_id)
        if record is None:
            raise EnvironmentError(f"Environment not found: {environment_id}")
        self._ensure_checkouts_inactive({record.repository_id})
        if any(
            service.project_id == record.repository_id
            for workspace in self._store.list_workspaces()
            for service in workspace.services
        ):
            raise EnvironmentError("该分支检出仍被工作区使用，请先切换或移除对应服务")
        worktree = Path(record.worktree_path)
        dirty = self._command_runner.run(("git", "status", "--porcelain"), cwd=worktree)
        if dirty.return_code != 0:
            raise EnvironmentError(f"Worktree is not accessible: {worktree}")
        if dirty.stdout:
            raise RepositoryDirtyError(str(worktree))
        common_dir = self._command_runner.run(
            ("git", "rev-parse", "--path-format=absolute", "--git-common-dir"), cwd=worktree
        )
        main_dir = (
            Path(common_dir.stdout).parent
            if common_dir.return_code == 0 and common_dir.stdout
            else worktree
        )
        remove = self._command_runner.run(
            ("git", "worktree", "remove", str(worktree)), cwd=main_dir
        )
        if remove.return_code != 0:
            raise EnvironmentError(f"Failed to remove worktree: {worktree}")
        self._store.delete_repository(record.repository_id)
        self._store.delete_environment(environment_id)
        return record

    def apply(
        self, workspace_id: str, environment_id: str, expected_revision: int
    ) -> WorkspaceRecord:
        workspace = self._store.get_workspace(workspace_id)
        record = self._store.get_environment(environment_id)
        if workspace is None:
            raise EnvironmentWorkspaceMissingError(workspace_id)
        if record is None or record.workspace_id != workspace_id:
            raise EnvironmentError("分支检出不属于当前工作区")
        if self._store.get_repository(record.repository_id) is None:
            raise EnvironmentError("分支检出不存在，请重新创建")
        source_id = record.source_repository_id
        related = {record.repository_id, source_id}
        if source_id:
            related.update(
                item.repository_id
                for item in self._store.list_environments(workspace_id)
                if item.source_repository_id == source_id
            )
        candidates = [s.project_id for s in workspace.services if s.project_id in related]
        if len(candidates) != 1:
            raise EnvironmentError("无法唯一确定要替换的项目，请重新创建并选择源项目")
        previous_id = candidates[0]
        self._ensure_checkouts_inactive({previous_id, record.repository_id})
        services = tuple(
            service.model_copy(
                update={
                    "project_id": record.repository_id
                    if service.project_id == previous_id
                    else service.project_id,
                    "depends_on": tuple(
                        record.repository_id if dep == previous_id else dep
                        for dep in service.depends_on
                    ),
                }
            )
            for service in workspace.services
        )
        return self._store.update_workspace(
            workspace_id,
            WorkspaceUpdateRequest(
                name=workspace.name,
                mode=workspace.mode,
                services=services,
                bindings=workspace.bindings,
                expected_revision=expected_revision,
            ),
            require_idle=True,
        )

    def _ensure_checkouts_inactive(self, project_ids: set[str]) -> None:
        for run in self._store.list_runs():
            if run.status not in {RunStatus.QUEUED, RunStatus.RUNNING}:
                continue
            plan = self._store.get_plan(run.plan_id)
            if plan is None:
                continue
            referenced = {project.id for project in plan.projects} | set(plan.service_targets)
            referenced.update(
                command.project_id for step in plan.steps for command in step.commands
            )
            referenced.update(
                deployment.project_id for step in plan.steps for deployment in step.deployments
            )
            if referenced & project_ids:
                raise EnvironmentRunActiveError(run.id)

    def _primary_repository(
        self, workspace: WorkspaceRecord, repository_id: str | None = None
    ) -> RepositoryRecord:
        project_ids = [service.project_id for service in workspace.services]
        if repository_id is not None:
            if repository_id not in project_ids:
                raise EnvironmentError("请选择当前工作区中的项目")
            project_ids = [repository_id]
        elif len(project_ids) > 1:
            raise EnvironmentError("多项目工作区必须明确选择创建分支检出的项目")
        for project_id in project_ids:
            record = self._store.get_repository(project_id)
            if record is not None:
                return record
        raise EnvironmentRepositoryMissingError(workspace.id)

    def _worktree_dir(self, workspace_id: str, ref: str) -> Path:
        slug = _SLUG_TOKEN.sub("-", ref).strip("-") or "ref"
        digest = hashlib.sha256(f"{workspace_id}:{ref}".encode()).hexdigest()[:8]
        return self._worktree_root / workspace_id / f"{slug}-{digest}"

    @staticmethod
    def _environment_id(workspace_id: str, ref: str) -> str:
        digest = hashlib.sha256(f"{workspace_id}:{ref}".encode()).hexdigest()[:24]
        return f"env-{digest}"
