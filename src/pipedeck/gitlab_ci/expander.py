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
                    title="stages 定义无效",
                    detail="stages 必须是字符串数组",
                    recovery="修正 .gitlab-ci.yml 的 stages 段",
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
                    title="workflow rules 含受支持子集之外的表达式",
                    detail=exc.reason + "：" + exc.expression,
                    recovery="改用 ==/!=/=~/!~/&&/||/括号等受支持语法",
                )
            )
            return
        if not outcome.included:
            blockers.append(
                PipelineIssue(
                    code="GITLAB_CI_WORKFLOW_EXCLUDED",
                    title="workflow rules 排除了整条管道",
                    detail="本地变量环境下没有匹配的 workflow rule",
                    recovery="调整 workflow rules 或本地注入的变量",
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
                        title=f"extends 父级 {parent_name} 不是隐藏 job",
                        detail="GitLab 约定 extends 父级以 . 开头；可见 job 也会作为独立 job 执行",
                        recovery="将模板 job 改名为 . 开头的隐藏 job",
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
                    title=f"job {name} 使用了本地不支持的关键字 {keyword}",
                    detail=f"{keyword} 的 GitLab 语义依赖服务端能力，本地执行会得到不可信结果",
                    recovery="改写该 job 避开此关键字，或在远端 CI 中执行该 job",
                )
            )
        for keyword in sorted(_WARN_KEYWORDS.intersection(merged.keys())):
            warnings.append(
                PipelineIssue(
                    code="GITLAB_CI_KEYWORD_IGNORED",
                    title=f"job {name} 的 {keyword} 语义本地不生效",
                    detail=f"{keyword} 关键字在本地执行中被忽略",
                    recovery="确认该语义对本地验证不构成正确性影响",
                )
            )

        raw_stage = merged.get("stage")
        stage = raw_stage if is_str(raw_stage) else "test"
        if stage not in stages:
            blockers.append(
                PipelineIssue(
                    code="GITLAB_CI_STAGE_MISSING",
                    title=f"job {name} 引用了未定义的 stage",
                    detail=f"stage {stage!r} 不在 stages 列表中",
                    recovery="把该 stage 加入 stages 或改用已有 stage",
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
                        title=f"job {name} 的 rules 含受支持子集之外的表达式",
                        detail=exc.reason + "：" + exc.expression,
                        recovery="改用 ==/!=/=~/!~/&&/||/括号等受支持语法",
                    )
                )

        image = self._image_of(merged.get("image"))
        if image is None:
            warnings.append(
                PipelineIssue(
                    code="GITLAB_CI_HOST_SHELL_JOB",
                    title=f"job {name} 未声明 image，将在宿主 shell 执行",
                    detail="无 image 的 job 按 GitLab runner shell 语义在宿主机执行",
                    recovery="如需隔离请为 job 声明 image",
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
                            title=f"job {name} 的 extends 引用了非字符串父级",
                            detail=repr(parent)[:120],
                            recovery="extends 父级必须是 job 名称",
                        )
                    )
                    return chain
                if parent in seen:
                    blockers.append(
                        PipelineIssue(
                            code="GITLAB_CI_EXTENDS_CYCLE",
                            title=f"job {name} 的 extends 存在循环",
                            detail=f"extends 链包含 {parent}，链路 {sorted(seen)}",
                            recovery="打断 extends 循环引用",
                        )
                    )
                    return chain
                seen.add(parent)
                parent_raw = document.get(parent)
                if not is_mapping(parent_raw):
                    blockers.append(
                        PipelineIssue(
                            code="GITLAB_CI_EXTENDS_MISSING",
                            title=f"job {name} 的 extends 父级不存在",
                            detail=f"找不到 {parent}",
                            recovery="确认父级 job 名称（隐藏 job 以 . 开头）",
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
                        title=f"job {name} 的 extends 链过深",
                        detail=f"超过 {_MAX_EXTENDS_DEPTH} 层",
                        recovery="简化 extends 层级",
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
                        title=f"job {job_name} 的 reports:{report_kind} 本地不生成",
                        detail="该报告类型只在 GitLab 服务端有消费方",
                        recovery="如需报告产物请改用 artifacts:paths",
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
                            title=f"job {job.name} 的 needs 引用了自己",
                            detail="needs 不允许自引用",
                            recovery="移除该 needs 条目",
                        )
                    )
                elif need.job not in names and not need.optional:
                    blockers.append(
                        PipelineIssue(
                            code="GITLAB_CI_NEEDS_MISSING",
                            title=f"job {job.name} 的 needs 引用了不存在的 job",
                            detail=f"找不到 {need.job}",
                            recovery="确认 needs 引用的 job 名称",
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
                        title="needs 依赖图存在循环",
                        detail=f"涉及 {sorted(name for name, mark in state.items() if mark == 1)}",
                        recovery="打断 needs 循环引用",
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
