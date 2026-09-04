"""`.gitlab-ci.yml` 加载：include 解析、schema 校验、`!reference` 安全加载。

include 支持范围：`local`（相对主文件，支持 glob）、`remote`（URL）、
`template`（映射到 gitlab.com 官方模板库）；`project/file`（跨项目）显式阻断。
remote/template 结果按 URL hash 缓存，默认走缓存，`fetch_includes=True` 强刷。
"""

import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any, Protocol, cast

import yaml
from jsonschema import Draft7Validator

from pipedeck.gitlab_ci.coerce import is_mapping, is_object_list, is_str
from pipedeck.gitlab_ci.model import PipelineIssue, PipelineParse

_SCHEMA_PATH = Path(__file__).parent / "schema" / "ci.json"
_TEMPLATE_BASE = "https://gitlab.com/gitlab-org/gitlab/-/raw/master/lib/gitlab/ci/templates/"
_MAX_INCLUDE_DEPTH = 10
_MAX_SCHEMA_ERRORS = 5


class IncludeFetcher(Protocol):
    def fetch(self, url: str, *, force: bool) -> str: ...


class HttpIncludeFetcher:
    def __init__(self, cache_dir: Path) -> None:
        self._cache_dir = cache_dir
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch(self, url: str, *, force: bool) -> str:
        digest = hashlib.sha256(url.encode()).hexdigest()
        cached = self._cache_dir / digest
        if cached.is_file() and not force:
            return cached.read_text(encoding="utf-8")
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
            body = response.read().decode("utf-8")
        cached.write_text(body, encoding="utf-8")
        return body


class _CachedFetcher:
    """为任意 fetcher（含测试注入的）套上磁盘缓存；默认缓存、force 强刷。"""

    def __init__(self, cache_dir: Path, delegate: IncludeFetcher) -> None:
        self._cache_dir = cache_dir
        self._delegate = delegate
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch(self, url: str, *, force: bool) -> str:
        cached = self._cache_dir / hashlib.sha256(url.encode()).hexdigest()
        if cached.is_file() and not force:
            return cached.read_text(encoding="utf-8")
        body = self._delegate.fetch(url, force=force)
        cached.write_text(body, encoding="utf-8")
        return body


class _ReferenceLoader(yaml.SafeLoader):
    pass


class ReferenceNode:
    """`!reference [a, b]` 的中间表示，展开阶段解析为实际引用值。"""

    __slots__ = ("keys",)

    def __init__(self, keys: list[object]) -> None:
        self.keys = keys


def _reference_constructor(loader: yaml.Loader, node: yaml.Node) -> ReferenceNode:
    if not isinstance(node, yaml.SequenceNode):
        raise yaml.YAMLError(
            "the value of !reference must be a sequence, e.g. !reference [job, key]"
        )
    return ReferenceNode(loader.construct_sequence(node, deep=True))


_ReferenceLoader.add_constructor("!reference", _reference_constructor)


def resolve_reference_nodes(value: object, root: dict[str, object], depth: int = 0) -> object:
    """把文档中的 `!reference [a, b]` 节点解析为实际引用值；未命中返回 None。"""
    if depth > _MAX_INCLUDE_DEPTH:
        return value
    if isinstance(value, ReferenceNode):
        node: object = root
        for key in value.keys:
            if not is_mapping(node) or not is_str(key) or key not in node:
                return None
            node = node[key]
        return resolve_reference_nodes(node, root, depth + 1)
    if is_mapping(value):
        return {key: resolve_reference_nodes(item, root, depth + 1) for key, item in value.items()}
    if is_object_list(value):
        return [resolve_reference_nodes(item, root, depth + 1) for item in value]
    return value


def _issue(code: str, title: str, detail: str, recovery: str) -> PipelineIssue:
    return PipelineIssue(code=code, title=title, detail=detail, recovery=recovery)


class GitlabCiParser:
    def __init__(
        self,
        cache_dir: Path | None = None,
        fetcher: IncludeFetcher | None = None,
        schema_validator: Draft7Validator | None = None,
    ) -> None:
        self._fetcher = self._build_fetcher(cache_dir, fetcher)
        if schema_validator is not None:
            self._validator = schema_validator
        else:
            self._validator = Draft7Validator(json.loads(_SCHEMA_PATH.read_text(encoding="utf-8")))

    def _build_fetcher(
        self, cache_dir: Path | None, fetcher: IncludeFetcher | None
    ) -> IncludeFetcher | None:
        if fetcher is not None:
            return _CachedFetcher(cache_dir, fetcher) if cache_dir is not None else fetcher
        if cache_dir is not None:
            return HttpIncludeFetcher(cache_dir)
        return None

    def parse(
        self,
        yml_path: Path,
        *,
        fetch_includes: bool = False,
    ) -> PipelineParse:
        issues: list[PipelineIssue] = []
        included: list[str] = []
        if not yml_path.is_file():
            return PipelineParse(
                issues=(
                    _issue(
                        "GITLAB_CI_FILE_MISSING",
                        "Missing .gitlab-ci.yml",
                        f"File not found: {yml_path}",
                        "",
                    ),
                )
            )
        try:
            text = yml_path.read_text(encoding="utf-8")
        except OSError as exc:
            return PipelineParse(
                issues=(
                    _issue(
                        "GITLAB_CI_FILE_UNREADABLE", ".gitlab-ci.yml is not readable", str(exc), ""
                    ),
                )
            )
        try:
            # 全仓库唯一的 Any 出口：pyyaml 的返回值在此收敛为 object，
            # 之后的一切取值都必须经过 coerce 守卫或精确类型。
            raw_document: object = cast("object", yaml.load(text, Loader=_ReferenceLoader))
        except yaml.YAMLError as exc:
            return PipelineParse(
                issues=(
                    _issue(
                        "GITLAB_CI_YAML_INVALID",
                        ".gitlab-ci.yml is not valid YAML",
                        str(exc),
                        "Fix the YAML syntax and refresh",
                    ),
                )
            )
        if not is_mapping(raw_document):
            return PipelineParse(
                issues=(
                    _issue(
                        "GITLAB_CI_YAML_INVALID",
                        "The top level of .gitlab-ci.yml must be a mapping",
                        f"Actual type: {type(raw_document).__name__}",
                        "Write the pipeline definition as key-value pairs",
                    ),
                )
            )
        try:
            merged: dict[str, object] = {}
            self._resolve_include_list(
                raw_document.get("include"),
                yml_path.parent,
                merged,
                included,
                issues,
                fetch_includes,
                0,
            )
            _deep_merge(merged, raw_document)
            resolved = resolve_reference_nodes(merged, merged)
            if is_mapping(resolved):
                merged = resolved
        except yaml.YAMLError as exc:
            return PipelineParse(
                issues=(_issue("GITLAB_CI_YAML_INVALID", "YAML processing failed", str(exc), ""),)
            )
        self._validate(merged, issues)
        return PipelineParse(
            documents={"merged": merged}, included_files=tuple(included), issues=tuple(issues)
        )

    def _resolve_include_list(
        self,
        include: object,
        base_dir: Path,
        merged: dict[str, object],
        included: list[str],
        issues: list[PipelineIssue],
        force: bool,
        depth: int,
    ) -> None:
        if include is None:
            return
        if depth >= _MAX_INCLUDE_DEPTH:
            issues.append(
                _issue(
                    "GITLAB_CI_INCLUDE_TOO_DEEP",
                    "include nesting is too deep",
                    f"include chain exceeds {_MAX_INCLUDE_DEPTH} levels; may indicate a loop",
                    "Check for circular references between include files",
                )
            )
            return
        entries: list[object] = list(include) if is_object_list(include) else [include]
        for entry in entries:
            if is_str(entry):
                entry = {"local": entry}
            if not is_mapping(entry):
                issues.append(
                    _issue(
                        "GITLAB_CI_INCLUDE_INVALID",
                        "include entries must be path strings or mappings",
                        repr(entry)[:120],
                        "Use the local/remote/template forms",
                    )
                )
                continue
            self._resolve_include_entry(entry, base_dir, merged, included, issues, force, depth)

    def _resolve_include_entry(
        self,
        entry: dict[str, object],
        base_dir: Path,
        merged: dict[str, object],
        included: list[str],
        issues: list[PipelineIssue],
        force: bool,
        depth: int,
    ) -> None:
        if "local" in entry:
            self._include_local(entry["local"], base_dir, merged, included, issues, force, depth)
            return
        if "remote" in entry:
            self._fetch_and_merge(_as_url(entry["remote"]), merged, included, issues, force)
            return
        if "template" in entry:
            self._fetch_and_merge(
                _as_url(entry["template"]), merged, included, issues, force, _TEMPLATE_BASE
            )
            return
        if "project" in entry or "file" in entry:
            issues.append(
                _issue(
                    "GITLAB_CI_INCLUDE_PROJECT_UNSUPPORTED",
                    "include:project needs a GitLab instance; not supported locally",
                    repr(entry)[:120],
                    "Change shared configs to local includes or remote URLs",
                )
            )
            return
        issues.append(
            _issue(
                "GITLAB_CI_INCLUDE_INVALID",
                "include entry lacks a recognizable keyword",
                repr(entry)[:120],
                "",
            )
        )

    def _include_local(
        self,
        pattern: object,
        base_dir: Path,
        merged: dict[str, object],
        included: list[str],
        issues: list[PipelineIssue],
        force: bool,
        depth: int,
    ) -> None:
        if not is_str(pattern):
            issues.append(
                _issue(
                    "GITLAB_CI_INCLUDE_INVALID",
                    "include:local value must be a path string",
                    repr(pattern)[:120],
                    "",
                )
            )
            return
        matches = (
            sorted(base_dir.glob(pattern))
            if any(char in pattern for char in "*?[")
            else [base_dir / pattern]
        )
        if not matches:
            issues.append(
                _issue(
                    "GITLAB_CI_INCLUDE_LOCAL_MISSING",
                    "The included local file does not exist",
                    pattern,
                    "Check the file location relative to .gitlab-ci.yml",
                )
            )
            return
        for path in matches:
            if not path.is_file():
                continue
            included.append(str(path))
            try:
                text = path.read_text(encoding="utf-8")
                sub: object = cast("object", yaml.load(text, Loader=_ReferenceLoader))
            except (OSError, yaml.YAMLError) as exc:
                issues.append(
                    _issue(
                        "GITLAB_CI_INCLUDE_INVALID",
                        f"Cannot load include file: {path.name}",
                        str(exc),
                        "",
                    )
                )
                continue
            if not is_mapping(sub):
                issues.append(
                    _issue(
                        "GITLAB_CI_INCLUDE_INVALID",
                        f"Top level of include file must be a mapping: {path.name}",
                        "",
                        "",
                    )
                )
                continue
            self._resolve_include_list(
                sub.get("include"), base_dir, merged, included, issues, force, depth + 1
            )
            _deep_merge(merged, sub)

    def _fetch_and_merge(
        self,
        url: str,
        merged: dict[str, object],
        included: list[str],
        issues: list[PipelineIssue],
        force: bool,
        base: str = "",
    ) -> None:
        if self._fetcher is None:
            issues.append(
                _issue(
                    "GITLAB_CI_INCLUDE_REMOTE_UNSUPPORTED",
                    "remote/template include requires a cache directory configuration",
                    url,
                    "Configure a state directory and retry",
                )
            )
            return
        target = base + url
        try:
            body = self._fetcher.fetch(target, force=force)
        except OSError as exc:
            issues.append(
                _issue(
                    "GITLAB_CI_INCLUDE_FETCH_FAILED",
                    "Failed to fetch remote include content",
                    f"{target}: {exc}",
                    "Check the network and force-refresh the include",
                )
            )
            return
        try:
            sub: object = cast("object", yaml.load(body, Loader=_ReferenceLoader))
        except yaml.YAMLError as exc:
            issues.append(
                _issue(
                    "GITLAB_CI_INCLUDE_INVALID",
                    f"Remote include content is not valid YAML: {target}",
                    str(exc),
                    "",
                )
            )
            return
        included.append(target)
        if is_mapping(sub):
            _deep_merge(merged, sub)

    def _validate(self, merged: dict[str, object], issues: list[PipelineIssue]) -> None:
        # jsonschema 的 iter_errors stub 不覆盖该重载，cast 到 Any 是本文件第二个豁口。
        raw_errors: Any = cast("Any", self._validator).iter_errors(merged)
        errors = sorted(raw_errors, key=lambda e: list(e.absolute_path))
        for error in errors[:_MAX_SCHEMA_ERRORS]:
            location = "/".join(str(part) for part in error.absolute_path) or "<root>"
            issues.append(
                _issue(
                    "GITLAB_CI_SCHEMA_INVALID",
                    ".gitlab-ci.yml does not conform to GitLab CI syntax",
                    f"{location}: {error.message}",
                    "Fix the syntax against the GitLab CI YAML reference",
                )
            )
        if len(errors) > _MAX_SCHEMA_ERRORS:
            issues.append(
                _issue(
                    "GITLAB_CI_SCHEMA_INVALID",
                    ".gitlab-ci.yml has more syntax issues",
                    f"{len(errors)} issues total; only the first {_MAX_SCHEMA_ERRORS} are shown",
                    "Fix the listed issues first, then refresh",
                )
            )


def _as_url(value: object) -> str:
    return str(value) if is_str(value) else ""


def _deep_merge(target: dict[str, object], source: dict[str, object]) -> None:
    for key, value in source.items():
        existing = target.get(key)
        if is_mapping(existing) and is_mapping(value):
            _deep_merge(existing, value)
        else:
            target[key] = value
