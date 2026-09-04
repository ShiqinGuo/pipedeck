"""`rules:if` 表达式求值器（受支持子集）。

支持：`==` `!=` `=~` `!~` `&&` `||` 括号、`null` 比较、`$VAR` 单独存在性、
带 `i` 标志的正则、引号或不带引号的字符串字面量。
任何超出子集的语法（函数、未支持操作符）抛 `UnsupportedExpressionError`，
由上层转为计划阻断项——不做半对半错的近似求值。
"""

import re

from pipedeck.gitlab_ci.coerce import is_mapping_list

_UNSUPPORTED_PATTERN = re.compile(
    r"!=~|\$[A-Za-z_][A-Za-z0-9_]*\s*\(|\bget\b|\bisxdigit\b|%%|type\(|len\(",
)
# 本地 web pipeline 上下文中永远为空的上下文变量：引用它们的 rules 结果不可信，阻断而非近似求值。
_CONTEXT_VARIABLES_PATTERN = re.compile(
    r"\$(?:\{)?(?:CI_MERGE_REQUEST_|CI_EXTERNAL_PULL_REQUEST_|CI_OPEN_MERGE_REQUESTS"
    r"|CI_PROJECTS_API_V4_URL|CI_RELEASE_)",
)


class UnsupportedExpressionError(ValueError):
    def __init__(self, expression: str, reason: str) -> None:
        super().__init__(reason)
        self.expression = expression
        self.reason = reason


class RuleOutcome:
    __slots__ = ("included", "when", "allow_failure")

    def __init__(self, included: bool, when: str, allow_failure: bool) -> None:
        self.included = included
        self.when = when
        self.allow_failure = allow_failure


_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
    |(?P<op>==|!=|=~|!~|&&|\|\||\(|\))
    |(?P<dquoted>"(?:[^"\\]|\\.)*")
    |(?P<squoted>'[^']*')
    |(?P<regex>/(?:[^/\\]|\\.)*/[a-zA-Z]*)
    |(?P<var>\$\{[A-Za-z_][A-Za-z0-9_]*\}|\$[A-Za-z_][A-Za-z0-9_]*)
    |(?P<bare>[^\s()&|=~/]+)
    """,
    re.VERBOSE,
)


def _tokenize(expression: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(expression):
        match = _TOKEN_RE.match(expression, pos)
        if match is None:
            raise UnsupportedExpressionError(
                expression,
                f"Cannot parse fragment near position {pos}: {expression[pos : pos + 20]!r}",
            )
        pos = match.end()
        kind = match.lastgroup or ""
        if kind == "ws":
            continue
        tokens.append((kind, match.group()))
    return tokens


class _TokenStream:
    def __init__(self, tokens: list[tuple[str, str]], expression: str) -> None:
        self._tokens = tokens
        self._pos = 0
        self._expression = expression

    def peek(self) -> tuple[str, str] | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def take(self) -> tuple[str, str]:
        token = self.peek()
        if token is None:
            raise UnsupportedExpressionError(self._expression, "Expression ended unexpectedly")
        self._pos += 1
        return token

    def at_end(self) -> bool:
        return self._pos >= len(self._tokens)


def evaluate_rules(
    rules: object,
    variables: dict[str, str],
) -> RuleOutcome:
    """按序求值 rules，返回首个匹配结果；无 rules 或无匹配走 GitLab 默认语义。"""
    if rules is None:
        return RuleOutcome(included=True, when="on_success", allow_failure=False)
    if not is_mapping_list(rules):
        raise UnsupportedExpressionError(str(rules)[:120], "rules must be an array of mappings")
    if not rules:
        return RuleOutcome(included=True, when="on_success", allow_failure=False)
    for rule in rules:
        outcome = _evaluate_rule(rule, variables)
        if outcome.included:
            return outcome
    return RuleOutcome(included=False, when="never", allow_failure=False)


def _evaluate_rule(rule: dict[str, object], variables: dict[str, str]) -> RuleOutcome:
    raw_if = rule.get("if")
    if raw_if is not None:
        if not isinstance(raw_if, str):
            raise UnsupportedExpressionError(str(raw_if)[:120], "rules:if value must be a string")
        if not _evaluate_if(raw_if, variables):
            return RuleOutcome(included=False, when="never", allow_failure=False)
    raw_when = rule.get("when", "on_success")
    when = raw_when if isinstance(raw_when, str) else "on_success"
    if when not in {"on_success", "always", "manual", "delayed", "never"}:
        raise UnsupportedExpressionError(when, f"Unsupported when value: {when}")
    allow_failure = rule.get("allow_failure") is True
    return RuleOutcome(
        included=when != "never",
        when="manual" if when == "delayed" else when,
        allow_failure=allow_failure,
    )


def _evaluate_if(expression: str, variables: dict[str, str]) -> bool:
    if _UNSUPPORTED_PATTERN.search(expression):
        raise UnsupportedExpressionError(
            expression, "Contains syntax or functions outside the supported subset"
        )
    if _CONTEXT_VARIABLES_PATTERN.search(expression):
        raise UnsupportedExpressionError(
            expression,
            "References MR/release variables always empty locally; results unreliable",
        )
    tokens = _TokenStream(_tokenize(expression), expression)
    result = _parse_or(tokens, variables)
    if not tokens.at_end():
        raise UnsupportedExpressionError(expression, "Expression has unconsumed trailing fragments")
    return result


def _parse_or(tokens: _TokenStream, variables: dict[str, str]) -> bool:
    left = _parse_and(tokens, variables)
    while (token := tokens.peek()) and token[1] == "||":
        tokens.take()
        right = _parse_and(tokens, variables)
        left = left or right
    return left


def _parse_and(tokens: _TokenStream, variables: dict[str, str]) -> bool:
    left = _parse_comparison(tokens, variables)
    while (token := tokens.peek()) and token[1] == "&&":
        tokens.take()
        right = _parse_comparison(tokens, variables)
        left = left and right
    return left


def _parse_comparison(tokens: _TokenStream, variables: dict[str, str]) -> bool:
    left = _parse_atom(tokens, variables)
    token = tokens.peek()
    if token is None:
        return _truthy(left)
    op = token[1]
    if op in {"==", "!="}:
        tokens.take()
        right = _parse_atom(tokens, variables)
        return (left == right) if op == "==" else (left != right)
    if op in {"=~", "!~"}:
        tokens.take()
        pattern_token = tokens.take()
        if pattern_token[0] not in {"regex", "bare", "dquoted", "squoted"}:
            raise UnsupportedExpressionError(
                op, "The right side of a regex comparison must be a pattern literal"
            )
        return _regex_match(left, pattern_token[1], op)
    return _truthy(left)


def _regex_match(value: str, pattern: str, op: str) -> bool:
    flags = 0
    body = pattern
    match = re.match(r"^/(.*)/([a-zA-Z]*)$", pattern, re.DOTALL)
    if match:
        body = match.group(1)
        flag_chars = match.group(2)
        if set(flag_chars) - {"i", "m", "s"}:
            raise UnsupportedExpressionError(pattern, f"Unsupported regex flags: {flag_chars}")
        if "i" in flag_chars:
            flags |= re.IGNORECASE
        if "m" in flag_chars:
            flags |= re.MULTILINE
        if "s" in flag_chars:
            flags |= re.DOTALL
    try:
        hit = re.search(body, value, flags) is not None
    except re.error as exc:
        raise UnsupportedExpressionError(pattern, f"Invalid regex: {exc}") from exc
    return hit if op == "=~" else not hit


def _parse_atom(tokens: _TokenStream, variables: dict[str, str]) -> str:
    token = tokens.take()
    kind, text = token
    if kind == "op" and text == "(":
        value = _parse_or(tokens, variables)
        closing = tokens.take()
        if closing[1] != ")":
            raise UnsupportedExpressionError(text, "Unclosed parenthesis")
        return str(value)
    if kind == "var":
        name = text[2:-1] if text.startswith("${") else text[1:]
        return variables.get(name, "")
    if kind in {"dquoted", "squoted"}:
        return _unquote(text)
    if kind == "bare":
        if text == "null":
            return ""
        if text.startswith("$"):
            return variables.get(text[1:], "")
        return text
    raise UnsupportedExpressionError(text, f"Unsupported expression token: {kind}")


def _unquote(text: str) -> str:
    body = text[1:-1]
    return body.replace('\\"', '"').replace("\\\\", "\\")


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value != "" and value != "null"
    return bool(value)
