"""GitlabPipelineService 的预览、计划生成与运行接线验证。"""

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest

from pipedeck.contracts import PlanCommand, RepositoryRecord, WorkspacePlanResponse
from pipedeck.control_plane import StorePlanLoader
from pipedeck.execution import (
    ExecutionEngine,
    FreshnessResult,
    ResolvedEnvironmentVariable,
)
from pipedeck.gitlab_ci.parser import GitlabCiParser
from pipedeck.pipeline_service import (
    GitlabPipelineService,
    PipelineUnavailableError,
    RepositoryNotFoundError,
)
from pipedeck.processes import CommandResult
from pipedeck.state_store import StateStore

_YML = """
stages: [build, test]
build_job:
  stage: build
  image: alpine
  script: ["echo build"]
test_job:
  stage: test
  image: alpine
  script: ["echo test"]
  needs: [build_job]
"""


def _repository(tmp_path: Path, yml: str | None = _YML) -> RepositoryRecord:
    from datetime import UTC, datetime

    repo_dir = tmp_path / "demo-repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    if yml is not None:
        (repo_dir / ".gitlab-ci.yml").write_text(yml, encoding="utf-8")
    now = datetime.now(UTC)
    return RepositoryRecord(
        id="repo-1",
        name="demo-repo",
        path=str(repo_dir),
        origin_url=None,
        branch="main",
        head_sha="a" * 40,
        upstream=None,
        dirty=False,
        created_at=now,
        updated_at=now,
    )


class _StaticRunner:
    def run(self, argv: tuple[str, ...], cwd: Path | None = None) -> CommandResult:
        return CommandResult(return_code=0, stdout="a" * 40, stderr="")


@pytest.fixture()
def service(
    tmp_path: Path,
) -> Iterator[tuple[GitlabPipelineService, StateStore]]:
    store = StateStore(tmp_path / "state.db")
    svc = GitlabPipelineService(
        store,
        _StaticRunner(),
        tmp_path,
        parser=GitlabCiParser(cache_dir=tmp_path / "cache"),
    )
    yield svc, store
    store.close()


def _register(store: StateStore, tmp_path: Path, yml: str | None = _YML) -> str:
    record = _repository(tmp_path, yml)
    store.upsert_repository(record)
    return record.id


def test_preview_lists_jobs_and_dag(
    service: tuple[GitlabPipelineService, StateStore], tmp_path: Path
) -> None:
    svc, store = service
    repo_id = _register(store, tmp_path)
    preview = svc.preview(repo_id)
    assert preview.ready
    assert [job.name for job in preview.jobs] == ["build_job", "test_job"]
    test_job = preview.jobs[1]
    assert test_job.needs is not None and test_job.needs[0].job == "build_job"
    assert preview.blockers == ()
    assert preview.source_fingerprint == "a" * 40


def test_preview_missing_repository_raises(
    service: tuple[GitlabPipelineService, StateStore], tmp_path: Path
) -> None:
    svc, _ = service
    with pytest.raises(RepositoryNotFoundError):
        svc.preview("nope")


def test_preview_unsupported_semantics_blocks(
    service: tuple[GitlabPipelineService, StateStore], tmp_path: Path
) -> None:
    svc, store = service
    release_block = "\nrelease_job:\n  stage: deploy\n  image: alpine\n"
    release_block += "  script: ['echo']\n  release:\n    tag_name: v1\n"
    yml = _YML + release_block
    repo_id = _register(store, tmp_path, yml)
    preview = svc.preview(repo_id)
    assert not preview.ready
    assert any("release" in issue.detail or "release" in issue.title for issue in preview.blockers)


def test_preview_without_yml_raises(
    service: tuple[GitlabPipelineService, StateStore], tmp_path: Path
) -> None:
    svc, store = service
    repo_id = _register(store, tmp_path, yml=None)
    with pytest.raises(PipelineUnavailableError) as exc:
        svc.preview(repo_id)
    assert exc.value.code == "GITLAB_CI_FILE_MISSING"


def test_create_plan_orders_by_needs_and_is_idempotent(
    service: tuple[GitlabPipelineService, StateStore], tmp_path: Path
) -> None:
    svc, store = service
    repo_id = _register(store, tmp_path)
    plan = svc.create_plan(repo_id)
    assert plan.ready
    assert plan.plan_id is not None
    assert [step.id for step in plan.steps] == ["job:build_job", "job:test_job"]
    assert plan.steps[0].pipeline_job is not None
    assert plan.steps[0].pipeline_job.job.name == "build_job"
    again = svc.create_plan(repo_id)
    assert again.plan_id == plan.plan_id
    assert store.get_plan(plan.plan_id) is not None


def test_plan_fingerprint_changes_with_yml(
    service: tuple[GitlabPipelineService, StateStore], tmp_path: Path
) -> None:
    svc, store = service
    repo_dir = tmp_path / "demo-repo"
    repo_id = _register(store, tmp_path)
    first = svc.create_plan(repo_id)
    (repo_dir / ".gitlab-ci.yml").write_text(_YML + "# changed\n", encoding="utf-8")
    second = svc.create_plan(repo_id)
    assert first.config_fingerprint != second.config_fingerprint
    _ = repo_dir


def test_validate_detects_yml_change(
    service: tuple[GitlabPipelineService, StateStore], tmp_path: Path
) -> None:
    svc, store = service
    repo_dir = tmp_path / "demo-repo"
    repo_id = _register(store, tmp_path)
    plan = svc.create_plan(repo_id)
    result = svc.validate(plan)
    assert result.valid
    (repo_dir / ".gitlab-ci.yml").write_text(_YML + "# changed\n", encoding="utf-8")
    stale = svc.validate(plan)
    assert not stale.valid
    assert stale.code == "PLAN_STALE"
    _ = store


class _NullEnvironmentResolver:
    def resolve(
        self, plan: WorkspacePlanResponse, command: PlanCommand
    ) -> tuple[ResolvedEnvironmentVariable, ...]:
        return ()


class _AlwaysFresh:
    def validate(self, plan: WorkspacePlanResponse) -> FreshnessResult:
        return FreshnessResult(True)


def _engine(store: StateStore) -> ExecutionEngine:
    return ExecutionEngine(
        store=store,
        environment_resolver=_NullEnvironmentResolver(),
        plan_loader=StorePlanLoader(store),
        freshness_validator=_AlwaysFresh(),
    )


def test_pipeline_run_end_to_end_host_job(
    service: tuple[GitlabPipelineService, StateStore], tmp_path: Path
) -> None:
    svc, store = service
    repo_dir = tmp_path / "demo-repo"
    repo_id = _register(store, tmp_path)
    # 覆盖为无 image 的宿主 job（本机 bash 直接执行）
    (repo_dir / ".gitlab-ci.yml").write_text(
        "stages: [build]\nbuild_job:\n  stage: build\n  script: ['echo local-run']\n",
        encoding="utf-8",
    )
    plan = svc.create_plan(repo_id)
    engine = _engine(store)
    assert plan.plan_id is not None
    run = engine.start(plan.plan_id, uuid4().hex)
    finished = engine.wait(run.id, timeout=30)
    assert finished is not None and finished.status.value == "succeeded"
    events = store.list_events(run.id).events
    assert any("local-run" in event.message for event in events)
