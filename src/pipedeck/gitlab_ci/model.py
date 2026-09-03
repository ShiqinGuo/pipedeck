"""GitLab CI 管道的 Pydantic 模型。"""

from pydantic import BaseModel, ConfigDict, Field


class PipelineIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    title: str
    detail: str
    recovery: str


class ImageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    entrypoint: tuple[str, ...] = ()


class JobNeed(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: str
    artifacts: bool = True
    optional: bool = False


class PipelineJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    stage: str
    script: tuple[str, ...] = ()
    before_script: tuple[str, ...] = ()
    after_script: tuple[str, ...] = ()
    image: ImageSpec | None = None
    variables: dict[str, str] = Field(default_factory=dict)
    needs: tuple[JobNeed, ...] | None = None
    artifacts: tuple[str, ...] = ()
    dotenv_reports: tuple[str, ...] = ()
    allow_failure: bool = False
    when: str = "on_success"
    included: bool = True
    unsupported: tuple[str, ...] = ()


class ExpandedPipeline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jobs: tuple[PipelineJob, ...]
    stages: tuple[str, ...] = ()
    global_variables: dict[str, str] = Field(default_factory=dict)
    blockers: tuple[PipelineIssue, ...] = ()
    warnings: tuple[PipelineIssue, ...] = ()
    fingerprint: str = ""


class PipelineParse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: dict[str, dict[str, object]] = Field(default_factory=dict)
    included_files: tuple[str, ...] = ()
    issues: tuple[PipelineIssue, ...] = ()
