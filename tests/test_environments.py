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
    RunRecord,
    RunStatus,
    WorkspaceInput,
    WorkspacePlanResponse,
    WorkspaceService,
    WorkspaceUpdateRequest,
)
from pipedeck.environments import (
    EnvironmentError,
    EnvironmentRefInvalidError,
    EnvironmentRunActiveError,
    EnvironmentService,
)
from pipedeck.repositories import RepositoryService, RepositoryServiceError
from pipedeck.state_store import StateStore


def test_multi_project_branch_selection_applies_only_the_selected_source(
    service: tuple[EnvironmentService, StateStore, Path],
) -> None:
    env_service, store, root = service
    backend, frontend = root / "backend", root / "frontend"
    _init_repo(backend)
    _init_repo(frontend)
    workspace_id = _register_workspace(store, backend, "backend")
    store.upsert_repository(_repository_record(frontend, "frontend"))
    workspace = store.update_workspace(
        workspace_id,
        WorkspaceUpdateRequest(
            name="integration",
            mode=RunMode.INTEGRATED,
            expected_revision=1,
            services=(
                WorkspaceService(project_id="frontend", depends_on=("backend",)),
                WorkspaceService(project_id="backend"),
            ),
        ),
    )
    with pytest.raises(EnvironmentError, match="明确选择"):
        env_service.create(workspace_id, EnvironmentCreateRequest(ref="dev"))
    backend_env = env_service.create(
        workspace_id, EnvironmentCreateRequest(ref="dev", repository_id="backend")
    )
    frontend_env = env_service.create(
        workspace_id, EnvironmentCreateRequest(ref="dev", repository_id="frontend")
    )
    assert backend_env.id != frontend_env.id
    assert backend_env.worktree_path != frontend_env.worktree_path
    applied = env_service.apply(workspace_id, backend_env.id, workspace.revision)
    assert [s.project_id for s in applied.services] == ["frontend", backend_env.repository_id]
    assert applied.services[0].depends_on == (backend_env.repository_id,)
    assert store.get_repository(backend_env.repository_id) is not None
    with pytest.raises(EnvironmentError, match="仍被工作区使用"):
        env_service.delete(backend_env.id)
    # A stale browser must not overwrite the version selection from another client.
    from pipedeck.state_store import WorkspaceRevisionConflictError

    with pytest.raises(WorkspaceRevisionConflictError):
        env_service.apply(workspace_id, frontend_env.id, workspace.revision)


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
    with pytest.raises(EnvironmentError, match="already exists"):
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


@pytest.mark.parametrize("status", [RunStatus.QUEUED, RunStatus.RUNNING])
def test_active_checkout_run_blocks_branch_apply_and_delete(
    service: tuple[EnvironmentService, StateStore, Path], status: RunStatus
) -> None:
    env_service, store, root = service
    repo_path = root / "demo-repo"
    _init_repo(repo_path)
    workspace_id = _register_workspace(store, repo_path, "repo-1")
    record = env_service.create(workspace_id, EnvironmentCreateRequest(ref="dev"))
    workspace = store.get_workspace(workspace_id)
    assert workspace is not None
    # The original source is being used by a workspace run.
    plan = WorkspacePlanResponse(
        generated_at=datetime.now(UTC),
        ready=True,
        mode=RunMode.INTEGRATED,
        projects=(),
        steps=(),
        blockers=(),
        warnings=(),
        plan_id="active-plan",
        workspace_id=workspace_id,
        workspace_revision=workspace.revision,
        config_fingerprint="config",
        source_fingerprint="source",
        service_targets={"repo-1": workspace.services[0].execution_target},
    )
    store.save_plan(plan)
    run = store.create_run(
        RunRecord(
            id="active-run",
            workspace_id=workspace_id,
            workspace_name=workspace.name,
            workspace_revision=workspace.revision,
            plan_id="active-plan",
            mode=workspace.mode,
            config_fingerprint="config",
            source_fingerprint="source",
            retry_of=None,
            status=RunStatus.QUEUED,
            current_step=None,
            created_at=datetime.now(UTC),
            started_at=None,
            finished_at=None,
            failure_code=None,
            failure_detail=None,
        ),
        "active-request",
    )
    if status is RunStatus.RUNNING:
        run = store.update_run(
            run.model_copy(update={"status": status, "started_at": datetime.now(UTC)}),
            expected_status=RunStatus.QUEUED,
        )
    with pytest.raises(EnvironmentRunActiveError, match="active-run"):
        env_service.apply(workspace_id, record.id, workspace.revision)
    assert store.get_workspace(workspace_id) == workspace

    # An unselected worktree can also be referenced by a standalone pipeline.
    checkout_plan = plan.model_copy(
        update={
            "plan_id": "checkout-plan",
            "workspace_id": None,
            "workspace_revision": None,
            "service_targets": {record.repository_id: workspace.services[0].execution_target},
        }
    )
    store.save_plan(checkout_plan)
    store.create_run(
        run.model_copy(
            update={
                "id": "checkout-run",
                "workspace_id": None,
                "workspace_revision": None,
                "plan_id": "checkout-plan",
                "status": RunStatus.QUEUED,
                "started_at": None,
            }
        ),
        "checkout-request",
    )
    with pytest.raises(EnvironmentRunActiveError, match="checkout-run"):
        env_service.delete(record.id)
    assert Path(record.worktree_path).exists()
    assert store.get_environment(record.id) is not None
