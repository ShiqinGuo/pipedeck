"""Environment 服务端到端：真实 git worktree 创建、注册、pipeline 复用与删除。"""

import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from pipedeck.contracts import (
    EnvironmentCreateRequest,
    RepositoryRecord,
    RunMode,
    WorkspaceInput,
    WorkspaceService,
)
from pipedeck.environments import (
    EnvironmentError,
    EnvironmentRefInvalidError,
    EnvironmentService,
)
from pipedeck.repositories import RepositoryService, RepositoryServiceError
from pipedeck.state_store import StateStore

_YML = "stages: [build]\nbuild_job:\n  stage: build\n  image: alpine\n  script: ['echo build']\n"


def _init_repo(path: Path, branch: str = "dev") -> None:
    path.mkdir(parents=True)
    (path / ".gitlab-ci.yml").write_text(_YML, encoding="utf-8")
    (path / "README.md").write_text("demo\n", encoding="utf-8")

    def git(*argv: str) -> None:
        subprocess.run(("git", *argv), cwd=path, check=True, capture_output=True)

    git("init", "-b", "main")
    git("config", "user.email", "e2e@example.com")
    git("config", "user.name", "e2e")
    git("add", "-A")
    git("commit", "-m", "init")
    git("checkout", "-b", branch)
    git("checkout", "main")


def _repository_record(path: Path, repo_id: str) -> RepositoryRecord:
    return RepositoryRecord(
        id=repo_id,
        name=path.name,
        path=str(path),
        origin_url=None,
        branch="main",
        head_sha="0" * 40,
        upstream=None,
        dirty=False,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.fixture()
def service(tmp_path: Path) -> Iterator[tuple[EnvironmentService, StateStore, Path]]:
    store = StateStore(tmp_path / "state.db")
    repos = RepositoryService(store)
    service = EnvironmentService(store, repos, tmp_path)
    yield service, store, tmp_path


def _register_workspace(store: StateStore, repo_path: Path, repo_id: str) -> str:
    store.upsert_repository(_repository_record(repo_path, repo_id))
    workspace_id = f"ws-{uuid4().hex[:8]}"
    store.create_workspace(
        WorkspaceInput(
            name="demo",
            mode=RunMode.INTEGRATED,
            services=(WorkspaceService(project_id=repo_id, commands=()),),
            bindings=(),
        ),
        workspace_id=workspace_id,
    )
    return workspace_id


def test_create_registers_worktree_and_pipeline_is_reusable(
    service: tuple[EnvironmentService, StateStore, Path], tmp_path: Path
) -> None:
    env_service, store, root = service
    repo_path = root / "demo-repo"
    _init_repo(repo_path)
    workspace_id = _register_workspace(store, repo_path, "repo-1")

    record = env_service.create(workspace_id, EnvironmentCreateRequest(ref="dev"))
    assert record.workspace_id == workspace_id
    assert record.ref == "dev"
    worktree = Path(record.worktree_path)
    assert worktree.is_dir()
    assert (worktree / ".gitlab-ci.yml").is_file()
    registered = store.get_repository(record.repository_id)
    assert registered is not None
    # --detach 创建的 worktree 不占用分支名,注册记录以 detached 标识(ref 在 EnvironmentRecord.ref)
    assert registered.branch == "detached"

    # 同 ref 幂等阻断 + ref 错误阻断
    with pytest.raises(EnvironmentError, match="已存在"):
        env_service.create(workspace_id, EnvironmentCreateRequest(ref="dev"))
    with pytest.raises(EnvironmentRefInvalidError):
        env_service.create(workspace_id, EnvironmentCreateRequest(ref="no-such-ref"))


def test_delete_removes_worktree_and_registration(
    service: tuple[EnvironmentService, StateStore, Path], tmp_path: Path
) -> None:
    env_service, store, root = service
    repo_path = root / "demo-repo"
    _init_repo(repo_path)
    workspace_id = _register_workspace(store, repo_path, "repo-1")
    record = env_service.create(workspace_id, EnvironmentCreateRequest(ref="dev"))

    removed = env_service.delete(record.id)
    assert not Path(removed.worktree_path).exists()
    assert store.get_repository(record.repository_id) is None
    assert store.get_environment(record.id) is None


def test_delete_blocked_when_dirty(
    service: tuple[EnvironmentService, StateStore, Path], tmp_path: Path
) -> None:
    env_service, store, root = service
    repo_path = root / "demo-repo"
    _init_repo(repo_path)
    workspace_id = _register_workspace(store, repo_path, "repo-1")
    record = env_service.create(workspace_id, EnvironmentCreateRequest(ref="dev"))
    Path(record.worktree_path, "scratch.txt").write_text("dirty", encoding="utf-8")

    with pytest.raises(RepositoryServiceError) as exc:
        env_service.delete(record.id)
    assert exc.value.code == "REPOSITORY_DIRTY"
    assert store.get_environment(record.id) is not None
    env_service.delete = env_service.delete  # 保持引用一致
    # 清理残留供后续断言
    subprocess.run(
        ("git", "worktree", "remove", "--force", record.worktree_path),
        cwd=repo_path,
        check=True,
        capture_output=True,
    )
    store.delete_repository(record.repository_id)
    store.delete_environment(record.id)
