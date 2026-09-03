"""GitLab CI 本地解析与执行子集。

解析 `.gitlab-ci.yml` 为可执行管道模型；不支持语义显式阻断，不做静默降级。
"""

from pipedeck.gitlab_ci.expander import PipelineExpander
from pipedeck.gitlab_ci.model import (
    ExpandedPipeline,
    ImageSpec,
    JobNeed,
    PipelineIssue,
    PipelineJob,
    PipelineParse,
)
from pipedeck.gitlab_ci.parser import GitlabCiParser
from pipedeck.gitlab_ci.rules import RuleOutcome, evaluate_rules
from pipedeck.gitlab_ci.variables import compose_variables, predefined_variables

__all__ = [
    "ExpandedPipeline",
    "GitlabCiParser",
    "ImageSpec",
    "JobNeed",
    "PipelineIssue",
    "PipelineJob",
    "PipelineParse",
    "PipelineExpander",
    "RuleOutcome",
    "compose_variables",
    "evaluate_rules",
    "predefined_variables",
]
