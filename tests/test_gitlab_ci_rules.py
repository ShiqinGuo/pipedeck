import pytest

from pipedeck.gitlab_ci.rules import UnsupportedExpressionError, evaluate_rules


def test_plain_equality_and_inequality() -> None:
    outcome = evaluate_rules([{"if": '$CI_COMMIT_BRANCH == "main"'}], {"CI_COMMIT_BRANCH": "main"})
    assert outcome.included
    outcome = evaluate_rules([{"if": '$CI_COMMIT_BRANCH == "main"'}], {"CI_COMMIT_BRANCH": "dev"})
    assert not outcome.included
    outcome = evaluate_rules([{"if": '$CI_COMMIT_BRANCH != "main"'}], {"CI_COMMIT_BRANCH": "dev"})
    assert outcome.included


def test_bare_variable_truthiness() -> None:
    outcome = evaluate_rules([{"if": "$SKIP_LOCAL"}], {"SKIP_LOCAL": "1"})
    assert outcome.included
    outcome = evaluate_rules([{"if": "$SKIP_LOCAL"}], {"SKIP_LOCAL": ""})
    assert not outcome.included
    outcome = evaluate_rules([{"if": "$MISSING"}], {})
    assert not outcome.included


def test_null_comparison() -> None:
    outcome = evaluate_rules([{"if": "$CI_COMMIT_TAG == null"}], {})
    assert outcome.included
    outcome = evaluate_rules([{"if": '$CI_COMMIT_TAG != "v1"'}], {"CI_COMMIT_TAG": "v1"})
    assert not outcome.included


def test_regex_with_flag() -> None:
    outcome = evaluate_rules(
        [{"if": "$CI_COMMIT_BRANCH =~ /^release\\//"}], {"CI_COMMIT_BRANCH": "release/1.2"}
    )
    assert outcome.included
    outcome = evaluate_rules([{"if": "$CI_COMMIT_BRANCH =~ /MAIN/i"}], {"CI_COMMIT_BRANCH": "main"})
    assert outcome.included
    outcome = evaluate_rules([{"if": "$CI_COMMIT_BRANCH !~ /main/"}], {"CI_COMMIT_BRANCH": "dev"})
    assert outcome.included


def test_boolean_precedence_and_parens() -> None:
    variables = {"CI_COMMIT_BRANCH": "main", "DEPLOY": "1"}
    outcome = evaluate_rules(
        [{"if": '$CI_COMMIT_BRANCH == "main" && $DEPLOY || $CI_COMMIT_BRANCH == "hotfix"'}],
        variables,
    )
    assert outcome.included
    outcome = evaluate_rules(
        [{"if": '($CI_COMMIT_BRANCH == "x" || $DEPLOY) && $CI_COMMIT_TAG == null'}], variables
    )
    assert outcome.included


def test_when_and_allow_failure_pass_through() -> None:
    outcome = evaluate_rules(
        [{"if": '$CI == "true"', "when": "manual", "allow_failure": True}], {"CI": "true"}
    )
    assert outcome.included
    assert outcome.when == "manual"
    assert outcome.allow_failure


def test_first_matching_rule_wins() -> None:
    outcome = evaluate_rules(
        [
            {"if": '$CI_COMMIT_BRANCH == "dev"', "when": "manual"},
            {"when": "on_success"},
        ],
        {"CI_COMMIT_BRANCH": "dev"},
    )
    assert outcome.when == "manual"


def test_no_matching_rule_excludes() -> None:
    outcome = evaluate_rules([{"if": '$CI_COMMIT_BRANCH == "dev"'}], {"CI_COMMIT_BRANCH": "main"})
    assert not outcome.included
    assert outcome.when == "never"


def test_unsupported_syntax_raises() -> None:
    with pytest.raises(UnsupportedExpressionError):
        evaluate_rules([{"if": "$CI_COMMIT_BRANCH =~ /^rel$/ && $CI_OPEN_MERGE_REQUESTS"}], {})
    with pytest.raises(UnsupportedExpressionError):
        evaluate_rules([{"if": "$CI_COMMIT_BRANCH =~ /^rel$/ && $CI_MERGE_REQUEST_ID != null"}], {})
    with pytest.raises(UnsupportedExpressionError):
        evaluate_rules([{"if": "len($CI_COMMIT_BRANCH) > 3"}], {})


def test_workflow_changes_or_exists_unsupported() -> None:
    with pytest.raises(UnsupportedExpressionError):
        evaluate_rules(
            [{"if": '$CI_COMMIT_BRANCH == "main" && $CI_MERGE_REQUEST_TARGET_BRANCH_NAME'}], {}
        )
    with pytest.raises(UnsupportedExpressionError):
        evaluate_rules([{"if": "$CI_EXTERNAL_PULL_REQUEST_IID != null"}], {})
