"""`.pipedeck.yml` 本地部署声明：读取、校验、转译。

声明由开发写在项目根目录，与线上 `.gitlab-ci.yml` 同构——只声明差异
（中间件依赖、环境变量、端口、健康检查、异步 job、compose 覆盖），
模板与结构由平台补全。文件随项目进 git，GUI 可查看/编辑/保存。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from pipedeck.contracts import MiddlewareKind

_DECLARATION_FILE = ".pipedeck.yml"


class DeclarationParseError(ValueError):
    """声明文件无法解析或不符合 schema。"""


class PipedeckJob(BaseModel):
    """异步 job：部署后在独立容器/宿主执行的一次性任务。"""

    model_config = ConfigDict(extra="ignore")

    name: str
    image: str | None = None
    script: str | tuple[str, ...]

    def script_lines(self) -> tuple[str, ...]:
        if isinstance(self.script, str):
            return tuple(line for line in self.script.splitlines() if line.strip())
        return tuple(self.script)


class PipedeckCompose(BaseModel):
    """compose 部署覆盖：默认起这些服务、暴露端口与健康检查。"""

    model_config = ConfigDict(extra="ignore")

    file: str
    services: tuple[str, ...] = ()
    port: int | None = None
    health: str | None = None


class PipedeckDeclaration(BaseModel):
    """本地部署声明的完整形态；缺失字段由平台按项目类型补全。"""

    model_config = ConfigDict(extra="ignore")

    services: tuple[MiddlewareKind, ...] = ()
    environment: dict[str, str] = Field(default_factory=dict)
    port: int | None = None
    health: str | None = None
    start: tuple[str, ...] | None = None
    jobs: tuple[PipedeckJob, ...] = ()
    compose: PipedeckCompose | None = None
    skip_tests: bool = False


def load_declaration(repo_path: Path) -> PipedeckDeclaration | None:
    """读取项目根目录的 `.pipedeck.yml`；不存在返回 None，解析失败抛错。"""
    declaration_path = repo_path / _DECLARATION_FILE
    if not declaration_path.is_file():
        return None
    try:
        text = declaration_path.read_text(encoding="utf-8")
    except OSError as error:
        raise DeclarationParseError(f"无法读取 {_DECLARATION_FILE}:{error}") from error
    return parse_declaration(text)


def parse_declaration(text: str) -> PipedeckDeclaration:
    try:
        raw: Any = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise DeclarationParseError(f"{_DECLARATION_FILE} 不是合法 YAML：{error}") from error
    if raw is None:
        raise DeclarationParseError(f"{_DECLARATION_FILE} 内容为空")
    if not isinstance(raw, dict):
        raise DeclarationParseError(f"{_DECLARATION_FILE} 顶层必须是映射")
    try:
        return PipedeckDeclaration.model_validate(raw)
    except ValidationError as error:
        raise DeclarationParseError(f"{_DECLARATION_FILE} 校验失败：{error}") from error
