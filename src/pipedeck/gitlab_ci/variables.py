"""GitLab CI 变量合成与 `$VAR` 展开。

优先级从低到高：预定义变量 < yml 全局 < yml job < 调用方注入（对应用户在客户端显式补全）。
未定义变量展开为空串（与 GitLab shell 展开一致）；`$$` 转义为字面 `$`。
"""

from collections.abc import Mapping

from pipedeck.gitlab_ci.coerce import is_str_mapping

_VARIABLE_PREFIX = "$"
_MAX_EXPAND_DEPTH = 5


def predefined_variables(
    *,
    project_name: str,
    ref_name: str,
    commit_sha: str,
    job_name: str,
    project_dir: str,
) -> dict[str, str]:
    return {
        "GITLAB_CI": "false",
        "CI": "true",
        "CI_PIPELINE_SOURCE": "web",
        "CI_SERVER": "yes",
        "CI_SERVER_URL": "http://127.0.0.1",
        "CI_PROJECT_NAME": project_name,
        "CI_PROJECT_TITLE": project_name,
        "CI_PROJECT_PATH": project_name,
        "CI_PROJECT_DIR": project_dir,
        "CI_COMMIT_REF_NAME": ref_name,
        "CI_COMMIT_SHA": commit_sha,
        "CI_COMMIT_SHORT_SHA": commit_sha[:8],
        "CI_JOB_NAME": job_name,
        "CI_JOB_STAGE": "",
        "CI_REGISTRY": "",
        "CI_ENVIRONMENT_NAME": "",
    }


def _expand_value(value: str, env: dict[str, str], depth: int = 0) -> str:
    if depth > _MAX_EXPAND_DEPTH:
        return value
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch != _VARIABLE_PREFIX:
            out.append(ch)
            i += 1
            continue
        if value.startswith("$$", i):
            out.append("$")
            i += 2
            continue
        if value.startswith("${", i):
            end = value.find("}", i + 2)
            if end == -1:
                out.append(ch)
                i += 1
                continue
            name = value[i + 2 : end]
            out.append(_lookup(name, env, depth))
            i = end + 1
            continue
        j = i + 1
        while j < len(value) and (value[j].isalnum() or value[j] == "_"):
            j += 1
        if j == i + 1:
            out.append(ch)
            i += 1
            continue
        name = value[i + 1 : j]
        out.append(_lookup(name, env, depth))
        i = j
    return "".join(out)


def _lookup(name: str, env: dict[str, str], depth: int) -> str:
    raw = env.get(name, "")
    return _expand_value(raw, env, depth + 1) if _VARIABLE_PREFIX in raw else raw


def _normalise(spec: Mapping[str, object]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in spec.items():
        if is_str_mapping(value):
            raw = value.get("value", "")
        elif value is None:
            raw = ""
        else:
            raw = str(value)
        out[key] = raw
    return out


def compose_variables(
    predefined: Mapping[str, str],
    global_variables: Mapping[str, object] | None,
    job_variables: Mapping[str, object] | None,
    caller_variables: Mapping[str, str] | None,
) -> dict[str, str]:
    merged: dict[str, str] = dict(predefined)
    if global_variables:
        merged.update(_normalise(global_variables))
    if job_variables:
        merged.update(_normalise(job_variables))
    if caller_variables:
        merged.update(caller_variables)
    return {key: _expand_value(value, merged) for key, value in merged.items()}
