import hashlib
import json
from pathlib import Path

from pipedeck.gitlab_ci.coerce import is_mapping
from pipedeck.gitlab_ci.parser import GitlabCiParser, HttpIncludeFetcher
from pipedeck.gitlab_ci.variables import compose_variables, predefined_variables


class FakeFetcher:
    def __init__(self, bodies: dict[str, str]) -> None:
        self.bodies = bodies
        self.calls: list[str] = []

    def fetch(self, url: str, *, force: bool) -> str:
        self.calls.append(url)
        return self.bodies[url]


def _write(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_parse_basic_document(tmp_path: Path) -> None:
    yml = _write(
        tmp_path / ".gitlab-ci.yml",
        """
stages: [build, test]
build_job:
  stage: build
  script: ["echo build"]
  image: python:3.12
test_job:
  stage: test
  script: ["echo test"]
""",
    )
    parse = GitlabCiParser().parse(yml)
    assert parse.issues == ()
    merged = parse.documents["merged"]
    assert is_mapping(merged)
    assert merged["stages"] == ["build", "test"]
    build_job = merged["build_job"]
    assert is_mapping(build_job)
    assert build_job["image"] == "python:3.12"


def test_parse_yaml_error(tmp_path: Path) -> None:
    yml = _write(tmp_path / ".gitlab-ci.yml", "stages: [build, \n  bad")
    parse = GitlabCiParser().parse(yml)
    assert parse.issues[0].code == "GITLAB_CI_YAML_INVALID"


def test_parse_missing_file(tmp_path: Path) -> None:
    parse = GitlabCiParser().parse(tmp_path / "nope.yml")
    assert parse.issues[0].code == "GITLAB_CI_FILE_MISSING"


def test_include_local_merge_and_override(tmp_path: Path) -> None:
    _write(
        tmp_path / "common.yml",
        """
variables: {GLOBAL_A: "1"}
shared_job:
  script: ["echo shared"]
  stage: test
""",
    )
    yml = _write(
        tmp_path / ".gitlab-ci.yml",
        """
include: common.yml
variables: {GLOBAL_A: "2"}
main_job:
  stage: build
  script: ["echo main"]
""",
    )
    parse = GitlabCiParser().parse(yml)
    assert parse.issues == ()
    merged = parse.documents["merged"]
    assert merged["variables"] == {"GLOBAL_A": "2"}
    assert "shared_job" in merged
    assert "main_job" in merged
    assert parse.included_files == (str(tmp_path / "common.yml"),)


def test_include_remote_with_cache(tmp_path: Path) -> None:
    _write(
        tmp_path / ".gitlab-ci.yml",
        "include:\n  - remote: 'https://example.com/x.yml'\nmain_job:\n  script: [x]\n",
    )
    fetcher = FakeFetcher({"https://example.com/x.yml": "remote_job:\n  script: ['echo remote']\n"})
    parse = GitlabCiParser(cache_dir=tmp_path / "cache", fetcher=fetcher).parse(
        tmp_path / ".gitlab-ci.yml"
    )
    assert parse.issues == ()
    merged = parse.documents["merged"]
    assert "remote_job" in merged
    assert fetcher.calls == ["https://example.com/x.yml"]
    # 第二次解析默认走缓存
    GitlabCiParser(cache_dir=tmp_path / "cache", fetcher=fetcher).parse(tmp_path / ".gitlab-ci.yml")
    assert len(fetcher.calls) == 1


def test_template_include_maps_to_official_repo(tmp_path: Path) -> None:
    _write(
        tmp_path / ".gitlab-ci.yml", "include:\n  - template: 'Jobs/Code-Quality.gitlab-ci.yml'\n"
    )
    url = "https://gitlab.com/gitlab-org/gitlab/-/raw/master/lib/gitlab/ci/templates/Jobs/Code-Quality.gitlab-ci.yml"
    fetcher = FakeFetcher({url: "codequality_job:\n  script: ['echo']\n"})
    parse = GitlabCiParser(cache_dir=tmp_path / "cache", fetcher=fetcher).parse(
        tmp_path / ".gitlab-ci.yml"
    )
    assert parse.issues == ()
    assert fetcher.calls == [url]


def test_include_project_is_blocked(tmp_path: Path) -> None:
    _write(tmp_path / ".gitlab-ci.yml", "include:\n  - project: 'org/shared'\n    file: 'ci.yml'\n")
    parse = GitlabCiParser().parse(tmp_path / ".gitlab-ci.yml")
    codes = {issue.code for issue in parse.issues}
    assert "GITLAB_CI_INCLUDE_PROJECT_UNSUPPORTED" in codes


def test_schema_invalid_blocks(tmp_path: Path) -> None:
    yml = _write(
        tmp_path / ".gitlab-ci.yml",
        """
bad_job:
  stage: build
  script: 42
""",
    )
    parse = GitlabCiParser().parse(yml)
    assert any(issue.code == "GITLAB_CI_SCHEMA_INVALID" for issue in parse.issues)


def test_reference_tag_loads_and_resolves(tmp_path: Path) -> None:
    yml = _write(
        tmp_path / ".gitlab-ci.yml",
        """
.prepare:
  script:
    - echo one
    - echo two
job_a:
  stage: test
  script: !reference [.prepare, script]
""",
    )
    parse = GitlabCiParser().parse(yml)
    assert parse.issues == ()
    from pipedeck.gitlab_ci.expander import PipelineExpander

    expanded = PipelineExpander().expand(
        parse.documents["merged"],
        project_name="demo",
        ref_name="main",
        commit_sha="0123456789abcdef",
        project_dir="/builds/demo",
    )
    job = next(j for j in expanded.jobs if j.name == "job_a")
    assert job.script == ("echo one", "echo two")


def test_variable_compose_precedence() -> None:
    predefined = predefined_variables(
        project_name="demo", ref_name="main", commit_sha="a" * 40, job_name="j", project_dir="/b/d"
    )
    merged = compose_variables(
        predefined,
        {"A": "global", "FROM_GLOBAL": "$A-x"},
        {"A": "job"},
        {"A": "caller"},
    )
    assert merged["A"] == "caller"
    # 展开发生在最终合并上下文：job/caller 对 A 的覆盖会传导到引用它的全局变量
    assert merged["FROM_GLOBAL"] == "caller-x"
    assert merged["GITLAB_CI"] == "false"


def test_variable_escape_and_missing() -> None:
    merged = compose_variables({}, {"LITERAL": "$$HOME", "MISS": "$NOPE"}, None, None)
    assert merged["LITERAL"] == "$HOME"
    assert merged["MISS"] == ""


def test_http_fetcher_caches(tmp_path: Path) -> None:
    fetcher = HttpIncludeFetcher(tmp_path)
    body = json.dumps({"stages": ["test"]})
    url = "https://example.invalid/ci.json"
    digest = hashlib.sha256(url.encode()).hexdigest()
    (tmp_path / digest).write_text(body, encoding="utf-8")
    assert fetcher.fetch(url, force=False) == body
