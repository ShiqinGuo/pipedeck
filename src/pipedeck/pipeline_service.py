"""GitLab CI 管道服务：仓库 → 解析预览 → 可执行 Plan → 新鲜度校验。

把 `.gitlab-ci.yml` 的展开结果接入既有 Plan/Run 状态机：
- Plan 的 steps = included jobs 的 needs 拓扑序列，每个 step 内嵌 PipelineJobSpec。
- plan_id 取展开指纹，内容不变则幂等复用；指纹包含 yml 内容与展开结果。
- 新鲜度校验对比当前 yml 指纹与 HEAD SHA，变化则要求重新预览。
"""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from pipedeck.contracts import (
    GitlabPipelinePreview,
    PipelineJobSpec,
    PlanIssue,
    PlanStep,
    PlanStepKind,
    RepositoryRecord,
    RunMode,
    WorkspacePlanResponse,
)
from pipedeck.execution import FreshnessResult
from pipedeck.gitlab_ci.expander import PipelineExpander
from pipedeck.gitlab_ci.model import (
    ExpandedPipeline,
    PipelineIssue,  # noqa: F401  re-export 供扩展
    PipelineJob,
)
from pipedeck.gitlab_ci.parser import GitlabCiParser
from pipedeck.processes import CommandRunner
from pipedeck.state_store import StateStore

_PLAN_ID_PREFIX = "pl-"


class RepositoryNotFoundError(RuntimeError):
    def __init__(self, repository_id: str) -> None:
        super().__init__(f"仓库不存在：{repository_id}")
        self.repository_id = repository_id


class PipelineUnavailableError(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _issues(issues: tuple[PipelineIssue, ...]) -> tuple[PlanIssue, ...]:
    return tuple(
        PlanIssue(code=issue.code, title=issue.title, detail=issue.detail, recovery=issue.recovery)
        for issue in issues
    )


class GitlabPipelineService:
    def __init__(
        self,
        store: StateStore,
        command_runner: CommandRunner,
        state_dir: Path,
        parser: GitlabCiParser | None = None,
    ) -> None:
        self._store = store
        self._command_runner = command_runner
        self._cache_dir = state_dir / "gitlab-ci-cache"
        self._artifacts_root = state_dir / "gitlab-artifacts"
        self._parser = parser or GitlabCiParser(cache_dir=self._cache_dir)

    def preview(self, repository_id: str, *, fetch_includes: bool = False) -> GitlabPipelinePreview:
        repository = self._repository(repository_id)
        expanded, config_fingerprint = self._expand(repository, fetch_includes)
        return GitlabPipelinePreview(
            repository_id=repository_id,
            ready=not expanded.blockers and any(job.included for job in expanded.jobs),
            stages=expanded.stages,
            jobs=expanded.jobs,
            global_variables=expanded.global_variables,
            blockers=_issues(expanded.blockers),
            warnings=_issues(expanded.warnings),
            config_fingerprint=config_fingerprint,
            source_fingerprint=repository.head_sha,
            generated_at=datetime.now(UTC),
        )

    def create_plan(
        self,
        repository_id: str,
        *,
        fetch_includes: bool = False,
        only_job: str | None = None,
    ) -> WorkspacePlanResponse:
        repository = self._repository(repository_id)
        expanded, config_fingerprint = self._expand(repository, fetch_includes)
        if expanded.blockers:
            issue = expanded.blockers[0]
            raise PipelineUnavailableError(issue.code, f"{issue.title}：{issue.detail}")
        jobs = self._order_jobs(expanded)
        if only_job is not None:
            jobs = self._job_closure(jobs, only_job)
        if not jobs:
            raise PipelineUnavailableError("GITLAB_CI_NO_RUNNABLE_JOBS", "管道中没有可执行的 job")
        artifact_dir = self._artifacts_root / repository_id
        steps = tuple(
            PlanStep(
                id=f"job:{job.name}",
                kind=PlanStepKind.PIPELINE,
                title=job.name,
                detail=f"stage: {job.stage}",
                commands=(),
                pipeline_job=PipelineJobSpec(
                    job=job,
                    workspace_dir=str(Path(repository.path).resolve()),
                    artifact_dir=str(artifact_dir),
                    project_name=repository.name,
                ),
            )
            for job in jobs
        )
        plan_id = f"{_PLAN_ID_PREFIX}{config_fingerprint[:32]}"
        if only_job is not None:
            plan_id += f"-j-{hashlib.sha256(only_job.encode()).hexdigest()[:8]}"
        return self._store.save_plan(
            WorkspacePlanResponse(
                generated_at=datetime.now(UTC),
                ready=True,
                mode=RunMode.DEVELOPMENT,
                projects=(),
                steps=steps,
                blockers=(),
                warnings=_issues(expanded.warnings),
                plan_id=plan_id,
                config_fingerprint=config_fingerprint,
                source_fingerprint=repository.head_sha,
            )
        )

    def validate(self, plan: WorkspacePlanResponse) -> FreshnessResult:
        """PipelineFreshnessValidator：对比当前 yml 指纹与 plan 记录的指纹。"""
        if plan.config_fingerprint is None or plan.source_fingerprint is None:
            return FreshnessResult(False, "PLAN_IDENTITY_REQUIRED", "计划缺少指纹")
        repository_id = self._repository_id_for_plan(plan)
        if repository_id is None:
            return FreshnessResult(False, "PIPELINE_REPOSITORY_MISSING", "计划未关联仓库")
        repository = self._store.get_repository(repository_id)
        if repository is None:
            return FreshnessResult(False, "REPOSITORY_NOT_FOUND", "仓库已被移除")
        try:
            _, current_fingerprint = self._expand(repository, fetch_includes=False)
        except PipelineUnavailableError as error:
            return FreshnessResult(False, error.code, error.detail)
        if current_fingerprint != plan.config_fingerprint:
            return FreshnessResult(False, "PLAN_STALE", ".gitlab-ci.yml 已变化，请重新预览")
        if repository.head_sha != plan.source_fingerprint:
            return FreshnessResult(False, "PLAN_STALE", "源码已变化，请重新预览")
        return FreshnessResult(True)

    def _repository_id_for_plan(self, plan: WorkspacePlanResponse) -> str | None:
        for step in plan.steps:
            if step.pipeline_job is not None:
                # artifact_dir 约定为 <state>/gitlab-artifacts/<repository_id>
                return Path(step.pipeline_job.artifact_dir).name
        return None

    def _repository(self, repository_id: str) -> RepositoryRecord:
        repository = self._store.get_repository(repository_id)
        if repository is None:
            raise RepositoryNotFoundError(repository_id)
        return repository

    def _expand(
        self, repository: RepositoryRecord, fetch_includes: bool
    ) -> tuple[ExpandedPipeline, str]:
        yml_path = Path(repository.path) / ".gitlab-ci.yml"
        parse = self._parser.parse(yml_path, fetch_includes=fetch_includes)
        raw = parse.documents.get("merged")
        if raw is None:
            issue = parse.issues[0] if parse.issues else None
            raise PipelineUnavailableError(
                issue.code if issue else "GITLAB_CI_UNKNOWN",
                issue.detail if issue else "解析失败",
            )
        yml_bytes = yml_path.read_bytes()
        expanded = PipelineExpander().expand(
            raw,
            project_name=repository.name,
            ref_name=repository.branch,
            commit_sha=repository.head_sha,
            project_dir=f"/builds/{repository.name}",
        )
        if parse.issues:
            expanded = expanded.model_copy(update={"blockers": expanded.blockers + parse.issues})
        config_fingerprint = hashlib.sha256(yml_bytes + expanded.fingerprint.encode()).hexdigest()
        return expanded, config_fingerprint

    def _job_closure(self, jobs: tuple[PipelineJob, ...], only_job: str) -> tuple[PipelineJob, ...]:
        """目标 job 及其传递 needs 依赖闭包（保持拓扑顺序）。"""
        by_name = {job.name: job for job in jobs}
        if only_job not in by_name:
            raise PipelineUnavailableError("GITLAB_CI_JOB_MISSING", f"管道中没有 job：{only_job}")
        keep: set[str] = set()
        stack = [only_job]
        while stack:
            name = stack.pop()
            if name in keep:
                continue
            keep.add(name)
            for need in by_name[name].needs or ():
                if need.job in by_name:
                    stack.append(need.job)
        return tuple(job for job in jobs if job.name in keep)

    def _order_jobs(self, expanded: ExpandedPipeline) -> tuple[PipelineJob, ...]:
        jobs = [job for job in expanded.jobs if job.included]
        by_name = {job.name: job for job in jobs}
        stage_rank = {stage: index for index, stage in enumerate(expanded.stages)}
        needs: dict[str, list[str]] = {}
        for job in jobs:
            if job.needs is None:
                needs[job.name] = []
                continue
            needs[job.name] = [need.job for need in job.needs if need.job in by_name]
        ordered: list[PipelineJob] = []
        placed: set[str] = set()
        remaining = {job.name for job in jobs}
        while remaining:
            ready = sorted(
                (name for name in remaining if all(dep in placed for dep in needs[name])),
                key=lambda name: (
                    stage_rank.get(by_name[name].stage, len(stage_rank)),
                    name,
                ),
            )
            if not ready:  # 防御：展开层应已拦截 needs 环
                ordered.extend(by_name[name] for name in sorted(remaining))
                break
            for name in ready:
                ordered.append(by_name[name])
                placed.add(name)
                remaining.discard(name)
        return tuple(ordered)
