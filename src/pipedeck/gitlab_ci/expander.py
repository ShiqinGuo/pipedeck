"""展开 `.gitlab-ci.yml` 合并文档为可执行管道模型。

职责：extends 链合并、default 继承、`!reference` 解析、workflow/job rules、
needs DAG 校验、stages 校验、不支持语义检测。不支持语义一律生成阻断项，
对应 job 标记 `included=False`，绝不静默跳过。

输入是 parser 层已收敛为 `dict[str, object]` 的动态文档，取值一律经过
coerce 守卫收窄，本模块零 cast、零类型降级；产出是严格 Pydantic 模型。
"""

import hashlib
import json
from collections.abc import Mapping

from pipedeck.gitlab_ci.coerce import (
    is_mapping,
    is_object_list,
    is_str,
    is_str_list,
)
from pipedeck.gitlab_ci.model import (
    ExpandedPipeline,
    ImageSpec,
    JobNeed,
    PipelineIssue,
    PipelineJob,
)
from pipedeck.gitlab_ci.parser import resolve_reference_nodes
from pipedeck.gitlab_ci.rules import UnsupportedExpressionError, evaluate_rules
from pipedeck.gitlab_ci.variables import compose_variables, predefined_variables

_DEFAULT_STAGES = (".pre", "build", "test", "deploy", ".post")
_BLOCKING_KEYWORDS = frozenset(
    {
        "services",
        "parallel",
        "trigger",
        "pages",
        "secrets",
        "id_tokens",
        "resource_group",
        "release",
        "hooks",
        "dast_configuration",
        "run",
    }
)
_WARN_KEYWORDS = frozenset(
    {"cache", "timeout", "retry", "environment", "inherit", "coverage", "start_in"}
)
_MAX_EXTENDS_DEPTH = 11
_MAX_VARIABLE_EXPAND_DEPTH = 5


class PipelineExpander:
    def __init__(self, max_variable_expand_depth: int = _MAX_VARIABLE_EXPAND_DEPTH) -> None:
        self._max_variable_expand_depth = max_variable_expand_depth

    def expand(
        self,
        document: dict[str, object],
        *,
        project_name: str,
        ref_name: str,
        commit_sha: str,
        project_dir: str,
    ) -> ExpandedPipeline:
        blockers: list[PipelineIssue] = []
        warnings: list[PipelineIssue] = []

        stages = self._stages(document, blockers)
        global_variables = self._variables_of(document.get("variables"))
        default = self._default_section(document)
        base_vars = compose_variables(
            predefined_variables(
                project_name=project_name,
                ref_name=ref_name,
                commit_sha=commit_sha,
                job_name="*pipeline*",
                project_dir=project_dir,
            ),
            global_variables,
            None,
            None,
        )
        self._evaluate_workflow(document.get("workflow"), base_vars, blockers, warnings)

        jobs: list[PipelineJob] = []
        for name, raw in document.items():
            if name.startswith(".") or not self._is_job(raw) or not is_mapping(raw):
                continue
            jobs.append(
                self._expand_job(
                    name,
                    raw,
                    document,
                    default,
                    stages,
                    base_vars,
                    project_dir,
                    blockers,
                    warnings,
                )
            )
        self._validate_needs(jobs, blockers)
        self._validate_job_rules(jobs, blockers)

        model = ExpandedPipeline(
            jobs=tuple(jobs),
            stages=stages,
            global_variables=global_variables,
            blockers=tuple(blockers),
            warnings=tuple(warnings),
        )
        model.fingerprint = self._fingerprint(model)
        return model

    def _is_job(self, raw: object) -> bool:
        return is_mapping(raw) and ("script" in raw or "extends" in raw or "trigger" in raw)

    def _default_section(self, document: dict[str, object]) -> dict[str, object]:
        raw_default = document.get("default")
        default: dict[str, object] = dict(raw_default) if is_mapping(raw_default) else {}
        raw_image = document.get("image")
        if isinstance(raw_image, (str, dict)) and "image" not in default:
            default = {**default, "image": raw_image}
        return default

    def _stages(
        self, document: dict[str, object], blockers: list[PipelineIssue]
    ) -> tuple[str, ...]:
        raw = document.get("stages")
        if raw is None:
            return _DEFAULT_STAGES
        if not is_str_list(raw):
            blockers.append(
                PipelineIssue(
                    code="GITLAB_CI_STAGES_INVALID",
                    title="Invalid stages definition",
                    detail="stages must be an array of strings",
                    recovery="Fix the stages section in .gitlab-ci.yml",
                )
            )
            return _DEFAULT_STAGES
        return tuple(raw)

    def _variables_of(self, raw: object) -> dict[str, str]:
        if not is_mapping(raw):
            return {}
        return compose_variables({}, raw, None, None)

    def _evaluate_workflow(
        self,
        workflow: object,
        base_vars: dict[str, str],
        blockers: list[PipelineIssue],
        warnings: list[PipelineIssue],
    ) -> None:
        if not is_mapping(workflow) or "rules" not in workflow:
            return
        try:
            outcome = evaluate_rules(workflow["rules"], base_vars)
        except UnsupportedExpressionError as exc:
            blockers.append(
                PipelineIssue(
                    code="GITLAB_CI_RULES_UNSUPPORTED",
                    title="workflow rules contain expressions outside the supported subset",
                    detail=exc.reason + ": " + exc.expression,
                    recovery="Use supported syntax such as ==/!=/=~/!~/&&/|| and parentheses",
                )
            )
            return
        if not outcome.included:
            blockers.append(
                PipelineIssue(
                    code="GITLAB_CI_WORKFLOW_EXCLUDED",
                    title="workflow rules exclude the entire pipeline",
                    detail="No workflow rule matches in the local variable environment",
                    recovery="Adjust workflow rules or locally injected variables",
                )
            )

    def _expand_job(
        self,
        name: str,
        raw: dict[str, object],
        document: dict[str, object],
        default: dict[str, object],
        stages: tuple[str, ...],
        base_vars: dict[str, str],
        project_dir: str,
        blockers: list[PipelineIssue],
        warnings: list[PipelineIssue],
    ) -> PipelineJob:
        resolved = self._resolve_job_references(raw, document)
        chain = self._extends_chain(name, resolved, document, blockers)
        merged: dict[str, object] = {}
        for parent_name, parent_raw in chain:
            _deep_merge(merged, parent_raw)
            if not parent_name.startswith("."):
                warnings.append(
                    PipelineIssue(
                        code="GITLAB_CI_EXTENDS_VISIBLE_PARENT",
                        title=f"extends parent {parent_name} is not a hidden job",
                        detail="extends parents should be hidden jobs; visible ones also run",
                        recovery="Rename the template job to a hidden job starting with .",
                    )
                )
        _deep_merge(merged, resolved)
        for key in ("image", "before_script", "after_script", "variables"):
            if key in default and key not in merged:
                merged[key] = default[key]

        unsupported = tuple(sorted(_BLOCKING_KEYWORDS.intersection(merged.keys())))
        for keyword in unsupported:
            blockers.append(
                PipelineIssue(
                    code="GITLAB_CI_UNSUPPORTED_KEYWORD",
                    title=f"job {name} uses keyword {keyword}, which is not supported locally",
                    detail=f"{keyword} needs the GitLab server; local runs are unreliable",
                    recovery="Rewrite the job to avoid this keyword, or run it in remote CI",
                )
            )
        for keyword in sorted(_WARN_KEYWORDS.intersection(merged.keys())):
            warnings.append(
                PipelineIssue(
                    code="GITLAB_CI_KEYWORD_IGNORED",
                    title=f"job {name} {keyword} semantics are not applied locally",
                    detail=f"The {keyword} keyword is ignored during local execution",
                    recovery="Confirm this does not affect local validation correctness",
                )
            )

        raw_stage = merged.get("stage")
        stage = raw_stage if is_str(raw_stage) else "test"
        if stage not in stages:
            blockers.append(
                PipelineIssue(
                    code="GITLAB_CI_STAGE_MISSING",
                    title=f"job {name} references an undefined stage",
                    detail=f"stage {stage!r} is not in the stages list",
                    recovery="Add the stage to stages or use an existing stage",
                )
            )

        job_vars_raw = self._variables_of(merged.get("variables"))
        job_vars = dict(compose_variables(base_vars, None, job_vars_raw, None))
        job_vars["CI_JOB_NAME"] = name
        job_vars["CI_JOB_STAGE"] = stage
        allow_failure = merged.get("allow_failure") is True
        included = True
        when = "on_success"
        if "rules" in merged:
            try:
                outcome = evaluate_rules(merged["rules"], job_vars)
                when = outcome.when
                allow_failure = allow_failure or outcome.allow_failure
                included = outcome.included
            except UnsupportedExpressionError as exc:
                included = False
                blockers.append(
                    PipelineIssue(
                        code="GITLAB_CI_RULES_UNSUPPORTED",
                        title=f"job {name} rules contain expressions outside the supported subset",
                        detail=exc.reason + ": " + exc.expression,
                        recovery="Use supported syntax such as ==/!=/=~/!~/&&/|| and parentheses",
                    )
                )

        image = self._image_of(merged.get("image"))
        if image is None:
            warnings.append(
                PipelineIssue(
                    code="GITLAB_CI_HOST_SHELL_JOB",
                    title=f"job {name} has no image declared and will run in the host shell",
                    detail="Jobs without an image run in host shell (GitLab runner semantics)",
                    recovery="Declare an image for the job if isolation is required",
                )
            )

        artifacts_paths, dotenv_reports = self._artifacts_of(
            merged.get("artifacts"), name, warnings
        )

        return PipelineJob(
            name=name,
            stage=stage,
            script=_script_of(merged.get("script")),
            before_script=_script_of(merged.get("before_script")),
            after_script=_script_of(merged.get("after_script")),
            image=image,
            variables=job_vars,
            needs=self._needs_of(merged.get("needs")),
            artifacts=artifacts_paths,
            dotenv_reports=dotenv_reports,
            allow_failure=allow_failure,
            when=when,
            included=included and not unsupported,
            unsupported=unsupported,
        )

    def _resolve_job_references(
        self, raw: dict[str, object], document: dict[str, object]
    ) -> dict[str, object]:
        resolved = resolve_reference_nodes(raw, document)
        return resolved if is_mapping(resolved) else raw

    def _extends_chain(
        self,
        name: str,
        raw: dict[str, object],
        document: dict[str, object],
        blockers: list[PipelineIssue],
    ) -> list[tuple[str, dict[str, object]]]:
        chain: list[tuple[str, dict[str, object]]] = []
        seen: set[str] = {name}
        current: object = raw.get("extends")
        depth = 0
        while current is not None:
            if is_object_list(current):
                parents: list[object] = list(current)
            elif is_str(current):
                parents = [current]
            else:
                break
            for parent in reversed(parents):
                if not is_str(parent):
                    blockers.append(
                        PipelineIssue(
                            code="GITLAB_CI_EXTENDS_INVALID",
                            title=f"job {name} extends references a non-string parent",
                            detail=repr(parent)[:120],
                            recovery="extends parents must be job names",
                        )
                    )
                    return chain
                if parent in seen:
                    blockers.append(
                        PipelineIssue(
                            code="GITLAB_CI_EXTENDS_CYCLE",
                            title=f"job {name} has a cycle in its extends chain",
                            detail=f"extends chain contains {parent}; chain {sorted(seen)}",
                            recovery="Break the extends cycle",
                        )
                    )
                    return chain
                seen.add(parent)
                parent_raw = document.get(parent)
                if not is_mapping(parent_raw):
                    blockers.append(
                        PipelineIssue(
                            code="GITLAB_CI_EXTENDS_MISSING",
                            title=f"job {name} extends parent does not exist",
                            detail=f"Cannot find {parent}",
                            recovery="Check the parent job name (hidden jobs start with .)",
                        )
                    )
                    return chain
                chain.append((parent, parent_raw))
                parent_extends = parent_raw.get("extends")
                if is_str(parent_extends) or is_object_list(parent_extends):
                    current = parent_extends
                else:
                    current = None
            depth += 1
            if depth >= _MAX_EXTENDS_DEPTH:
                blockers.append(
                    PipelineIssue(
                        code="GITLAB_CI_EXTENDS_TOO_DEEP",
                        title=f"job {name} extends chain is too deep",
                        detail=f"exceeds {_MAX_EXTENDS_DEPTH} levels",
                        recovery="Simplify the extends hierarchy",
                    )
                )
                return chain
        chain.reverse()
        return chain

    def _image_of(self, raw: object) -> ImageSpec | None:
        if is_str(raw):
            return ImageSpec(name=raw)
        if is_mapping(raw) and is_str(raw.get("name")):
            entrypoint = raw.get("entrypoint")
            entrypoint_tuple = (
                tuple(str(item) for item in entrypoint) if is_str_list(entrypoint) else ()
            )
            return ImageSpec(name=str(raw["name"]), entrypoint=entrypoint_tuple)
        return None

    def _artifacts_of(
        self, raw: object, job_name: str, warnings: list[PipelineIssue]
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if not is_mapping(raw):
            return (), ()
        paths = raw.get("paths")
        paths_tuple = tuple(paths) if is_str_list(paths) else ()
        dotenv: list[str] = []
        reports = raw.get("reports")
        if is_mapping(reports):
            dotenv_raw = reports.get("dotenv")
            if is_str(dotenv_raw):
                dotenv = [dotenv_raw]
            elif is_str_list(dotenv_raw):
                dotenv = list(dotenv_raw)
            for report_kind in sorted(set(reports.keys()) - {"dotenv"}):
                warnings.append(
                    PipelineIssue(
                        code="GITLAB_CI_REPORT_IGNORED",
                        title=f"job {job_name} reports:{report_kind} are not produced locally",
                        detail="This report type is only consumed on the GitLab server",
                        recovery="Use artifacts:paths if you need report artifacts",
                    )
                )
        return paths_tuple, tuple(dotenv)

    def _needs_of(self, raw: object) -> tuple[JobNeed, ...] | None:
        if raw is None:
            return None
        if is_object_list(raw) and not raw:
            return ()
        if is_str(raw) or is_mapping(raw):
            items: list[object] = [raw]
        elif is_object_list(raw):
            items = list(raw)
        else:
            items = []
        needs: list[JobNeed] = []
        for item in items:
            if is_str(item):
                needs.append(JobNeed(job=item))
            elif is_mapping(item) and is_str(item.get("job")):
                job_name = item.get("job")
                if is_str(job_name):
                    needs.append(
                        JobNeed(
                            job=job_name,
                            artifacts=item.get("artifacts") is not False,
                            optional=item.get("optional") is True,
                        )
                    )
        return tuple(needs)

    def _validate_needs(self, jobs: list[PipelineJob], blockers: list[PipelineIssue]) -> None:
        names = {job.name for job in jobs}
        for job in jobs:
            if job.needs is None:
                continue
            for need in job.needs:
                if need.job == job.name:
                    blockers.append(
                        PipelineIssue(
                            code="GITLAB_CI_NEEDS_SELF",
                            title=f"job {job.name} needs references itself",
                            detail="needs does not allow self-references",
                            recovery="Remove that needs entry",
                        )
                    )
                elif need.job not in names and not need.optional:
                    blockers.append(
                        PipelineIssue(
                            code="GITLAB_CI_NEEDS_MISSING",
                            title=f"job {job.name} needs references a job that does not exist",
                            detail=f"Cannot find {need.job}",
                            recovery="Check the job name referenced by needs",
                        )
                    )

    def _validate_job_rules(self, jobs: list[PipelineJob], blockers: list[PipelineIssue]) -> None:
        names = {job.name for job in jobs}
        graph: dict[str, list[str]] = {}
        for job in jobs:
            graph[job.name] = (
                []
                if job.needs is None
                else [need.job for need in job.needs if need.job != job.name and need.job in names]
            )
        state: dict[str, int] = {}
        for start in graph:
            if self._has_cycle(start, graph, state):
                blockers.append(
                    PipelineIssue(
                        code="GITLAB_CI_NEEDS_CYCLE",
                        title="The needs dependency graph contains a cycle",
                        detail=f"involves {sorted(name for name, mark in state.items() if mark == 1)}",  # noqa: E501
                        recovery="Break the needs cycle",
                    )
                )
                return

    def _has_cycle(self, node: str, graph: Mapping[str, list[str]], state: dict[str, int]) -> bool:
        mark = state.get(node, 0)
        if mark == 1:
            return True
        if mark == 2:
            return False
        state[node] = 1
        for nxt in graph.get(node, ()):
            if self._has_cycle(nxt, graph, state):
                return True
        state[node] = 2
        return False

    def _fingerprint(self, model: ExpandedPipeline) -> str:
        payload = model.model_dump(mode="json")
        payload.pop("fingerprint", None)
        canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _script_of(raw: object) -> tuple[str, ...]:
    if is_str(raw):
        return (raw,)
    if is_str_list(raw):
        return tuple(raw)
    return ()


def _deep_merge(target: dict[str, object], source: dict[str, object]) -> None:
    for key, value in source.items():
        existing = target.get(key)
        if is_mapping(existing) and is_mapping(value):
            _deep_merge(existing, value)
        else:
            target[key] = value
