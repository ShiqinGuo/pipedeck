"""YAML 动态值的类型收敛层。

`yaml.load` 的返回是 `Any`，任何 `Any` 一旦进入业务代码，静态检查即失效。
本模块把弱类型收敛在边界上：全仓库只允许 parser 入口对 `Any` 做一次
`cast(object, ...)`，此后所有取值都经过本模块的运行时守卫
（验证 + `TypeGuard` 收窄成对），验证失败返回 `False`/`None`，
由上层生成计划阻断项——业务代码零 cast、零类型降级。
"""

from typing import TypeGuard


def is_mapping(value: object) -> TypeGuard[dict[str, object]]:
    """YAML 映射 → `dict[str, object]`。键约定为 str，非 str 键由 schema 校验拦截。"""
    return isinstance(value, dict)


def is_object_list(value: object) -> TypeGuard[list[object]]:
    """收窄 list 的同时把元素固定为 object，阻断 `list[Unknown]` 的传播。"""
    return isinstance(value, list)


def is_str_list(value: object) -> TypeGuard[list[str]]:
    return is_object_list(value) and all(isinstance(item, str) for item in value)


def is_mapping_list(value: object) -> TypeGuard[list[dict[str, object]]]:
    """YAML 映射数组 → `list[dict[str, object]]`（rules 等结构的入口守卫）。"""
    return is_object_list(value) and all(isinstance(item, dict) for item in value)


def is_str_mapping(value: object) -> TypeGuard[dict[str, str]]:
    return is_mapping(value) and all(isinstance(item, str) for item in value.values())


def is_str(value: object) -> TypeGuard[str]:
    return isinstance(value, str)
