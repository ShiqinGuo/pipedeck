from pathlib import Path

from pipedeck.gitlab_ci.expander import PipelineExpander
from pipedeck.gitlab_ci.model import ExpandedPipeline
from pipedeck.gitlab_ci.parser import GitlabCiParser


def _expand(tmp_path: Path, content: str, **kwargs: str) -> ExpandedPipeline:
    (tmp_path / ".gitlab-ci.yml").write_text(content, encoding="utf-8")
    parse = GitlabCiParser().parse(tmp_path / ".gitlab-ci.yml")
    assert parse.issues == ()
    return PipelineExpander().expand(
        parse.documents["merged"],
        project_name="demo",
        ref_name=kwargs.get("ref_name", "main"),
        commit_sha=kwargs.get("commit_sha", "0123456789abcdef"),
        project_dir="/builds/demo",
    )


def test_expand_basic_stages_and_fingerprint(tmp_path: Path) -> None:
    pipeline = _expand(
        tmp_path,
        """
stages: [build, test]
build_job:
  stage: build
  image: python:3.12
  script: ["echo build"]
test_job:
  stage: test
  image: python:3.12
  script: ["echo test"]
""",
    )
    assert pipeline.stages == ("build", "test")
    assert [job.name for job in pipeline.jobs] == ["build_job", "test_job"]
    assert pipeline.blockers == ()
    assert len(pipeline.fingerprint) == 64
    again = _expand(tmp_path, (tmp_path / ".gitlab-ci.yml").read_text(encoding="utf-8"))
    assert again.fingerprint == pipeline.fingerprint


def test_default_and_extends_merge(tmp_path: Path) -> None:
    pipeline = _expand(
        tmp_path,
        """
default:
  image: ruby:3
  before_script: ["echo before"]
.template:
  script: ["echo from template"]
  stage: test
child_job:
  extends: .template
  script: ["echo child"]
""",
    )
    child = next(job for job in pipeline.jobs if job.name == "child_job")
    assert child.image is not None and child.image.name == "ruby:3"
    assert child.before_script == ("echo before",)
    assert child.script == ("echo child",)
    assert child.stage == "test"
    assert all(job.name != ".template" for job in pipeline.jobs)


def test_rules_gitlab_ci_false_includes(tmp_path: Path) -> None:
    pipeline = _expand(
        tmp_path,
        """
job_a:
  stage: test
  image: alpine
  script: ["echo hi"]
  rules:
    - if: '$GITLAB_CI == "false"'
""",
    )
    job = pipeline.jobs[0]
    assert job.included
    assert job.when == "on_success"


def test_rules_manual_and_never(tmp_path: Path) -> None:
    pipeline = _expand(
        tmp_path,
        """
job_manual:
  stage: test
  image: alpine
  script: ["echo"]
  rules:
    - if: '$GITLAB_CI == "false"'
      when: manual
job_never:
  stage: test
  image: alpine
  script: ["echo"]
  rules:
    - if: '$GITLAB_CI == "true"'
""",
    )
    by_name = {job.name: job for job in pipeline.jobs}
    assert by_name["job_manual"].when == "manual"
    assert not by_name["job_never"].included


def test_unsupported_keywords_block_job(tmp_path: Path) -> None:
    pipeline = _expand(
        tmp_path,
        """
with_service:
  stage: test
  image: alpine
  script: ["echo"]
  services:
    - postgres:16
with_cache:
  stage: test
  image: alpine
  script: ["echo"]
  cache:
    paths: [.venv]
""",
    )
    by_name = {job.name: job for job in pipeline.jobs}
    assert not by_name["with_service"].included
    assert by_name["with_service"].unsupported == ("services",)
    assert any(b.code == "GITLAB_CI_UNSUPPORTED_KEYWORD" or b.title for b in pipeline.blockers)
    assert by_name["with_cache"].included
    assert any(w.code == "GITLAB_CI_KEYWORD_IGNORED" for w in pipeline.warnings)


def test_needs_validation(tmp_path: Path) -> None:
    pipeline = _expand(
        tmp_path,
        """
a_job:
  stage: build
  image: alpine
  script: ["echo a"]
b_job:
  stage: test
  image: alpine
  script: ["echo b"]
  needs: [a_job]
c_job:
  stage: test
  image: alpine
  script: ["echo c"]
  needs: [{job: missing_job, optional: true}]
d_job:
  stage: test
  image: alpine
  script: ["echo d"]
  needs: [not_there]
""",
    )
    codes = {blocker.code for blocker in pipeline.blockers}
    assert "GITLAB_CI_NEEDS_MISSING" in codes
    by_name = {job.name: job for job in pipeline.jobs}
    assert by_name["b_job"].needs is not None
    assert by_name["b_job"].needs[0].job == "a_job"


def test_needs_cycle_blocks(tmp_path: Path) -> None:
    pipeline = _expand(
        tmp_path,
        """
a_job:
  stage: test
  image: alpine
  script: ["echo"]
  needs: [b_job]
b_job:
  stage: test
  image: alpine
  script: ["echo"]
  needs: [a_job]
""",
    )
    codes = {blocker.code for blocker in pipeline.blockers}
    assert "GITLAB_CI_NEEDS_CYCLE" in codes


def test_workflow_rules_excluded_pipeline(tmp_path: Path) -> None:
    pipeline = _expand(
        tmp_path,
        """
workflow:
  rules:
    - if: '$GITLAB_CI == "true"'
job_a:
  stage: test
  image: alpine
  script: ["echo"]
""",
    )
    codes = {blocker.code for blocker in pipeline.blockers}
    assert "GITLAB_CI_WORKFLOW_EXCLUDED" in codes


def test_unsupported_rules_expression_blocks_job(tmp_path: Path) -> None:
    pipeline = _expand(
        tmp_path,
        """
job_a:
  stage: test
  image: alpine
  script: ["echo"]
  rules:
    - if: '$CI_COMMIT_BRANCH == "main" && $CI_OPEN_MERGE_REQUESTS != null'
""",
    )
    codes = {blocker.code for blocker in pipeline.blockers}
    assert "GITLAB_CI_RULES_UNSUPPORTED" in codes
    assert not pipeline.jobs[0].included


def test_artifacts_and_dotenv_extraction(tmp_path: Path) -> None:
    pipeline = _expand(
        tmp_path,
        """
producer:
  stage: build
  image: alpine
  script: ["make"]
  artifacts:
    paths: [dist/]
    reports:
      dotenv: build.env
      junit: report.xml
""",
    )
    producer = pipeline.jobs[0]
    assert producer.artifacts == ("dist/",)
    assert producer.dotenv_reports == ("build.env",)
    assert any(w.code == "GITLAB_CI_REPORT_IGNORED" for w in pipeline.warnings)


def test_stage_missing_blocks(tmp_path: Path) -> None:
    pipeline = _expand(
        tmp_path,
        """
job_a:
  stage: unknown_stage
  image: alpine
  script: ["echo"]
""",
    )
    codes = {blocker.code for blocker in pipeline.blockers}
    assert "GITLAB_CI_STAGE_MISSING" in codes


def test_hidden_jobs_and_non_jobs_skipped(tmp_path: Path) -> None:
    pipeline = _expand(
        tmp_path,
        """
default:
  image: alpine
variables:
  TOP: "1"
.hidden:
  script: ["x"]
real_job:
  stage: test
  script: ["echo"]
""",
    )
    assert [job.name for job in pipeline.jobs] == ["real_job"]
    assert pipeline.global_variables == {"TOP": "1"}
    job = pipeline.jobs[0]
    assert job.image is not None and job.image.name == "alpine"
