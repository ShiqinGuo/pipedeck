"""Environment：Workspace × ref 的 worktree 并存实例。

创建 = `git worktree add` 到平台拥有的 state 目录 + 注册为独立 checkout
（checkout 拥有 HEAD/branch/dirty 事实，Pipeline/Plan/Run/Deployment 全链路复用）。
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
    WorkspaceRecord,
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

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class EnvironmentWorkspaceMissingError(EnvironmentError):
    code = "WORKSPACE_NOT_FOUND"

    def __init__(self, workspace_id: str) -> None:
        super().__init__(f"工作区不存在：{workspace_id}")


class EnvironmentRefInvalidError(EnvironmentError):
    code = "ENVIRONMENT_REF_INVALID"

    def __init__(self, ref: str, detail: str = "") -> None:
        super().__init__(f"无法从 ref 创建 worktree：{ref}（{detail}）")


class EnvironmentRepositoryMissingError(EnvironmentError):
    code = "ENVIRONMENT_REPOSITORY_MISSING"

    def __init__(self, workspace_id: str) -> None:
        super().__init__(f"工作区 {workspace_id} 没有关联仓库，无法创建 worktree 环境")


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
        repository = self._primary_repository(workspace)
        ref = request.ref
        worktree_dir = self._worktree_dir(workspace_id, ref)
        if worktree_dir.exists():
            raise EnvironmentError(f"环境目录已存在：{worktree_dir}")
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
                id=self._environment_id(workspace_id, ref),
                workspace_id=workspace_id,
                repository_id=registered.id,
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
            raise EnvironmentError(f"环境不存在：{environment_id}")
        worktree = Path(record.worktree_path)
        dirty = self._command_runner.run(("git", "status", "--porcelain"), cwd=worktree)
        if dirty.return_code != 0:
            raise EnvironmentError(f"worktree不可访问：{worktree}")
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
            raise EnvironmentError(f"worktree移除失败：{worktree}")
        self._store.delete_repository(record.repository_id)
        self._store.delete_environment(environment_id)
        return record

    def _primary_repository(self, workspace: WorkspaceRecord) -> RepositoryRecord:
        project_ids = [service.project_id for service in workspace.services]
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
