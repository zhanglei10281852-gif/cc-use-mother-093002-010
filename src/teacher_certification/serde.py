"""数据类与 JSON 可序列化结构之间的通用转换。

持久化层只依赖标准库：日期、枚举、嵌套数据类、tuple/list/frozenset
以及以枚举为键的字典都在这里统一处理，业务数据类因此保持干净。
"""
from __future__ import annotations

import types
import typing
from dataclasses import fields, is_dataclass
from datetime import date
from enum import Enum

_DATE_TAG = "$date"
_ENUM_TAG = "$enum"
_DICT_TAG = "$dict"


def to_jsonable(value):
    """把领域对象递归转换为 json.dumps 可接受的结构。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, date):
        return {_DATE_TAG: value.isoformat()}
    if isinstance(value, Enum):
        return {_ENUM_TAG: f"{type(value).__name__}.{value.name}"}
    if is_dataclass(value) and not isinstance(value, type):
        return {f.name: to_jsonable(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, (list, tuple, frozenset, set)):
        return [to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {_DICT_TAG: [[to_jsonable(k), to_jsonable(v)] for k, v in value.items()]}
    raise TypeError(f"无法序列化的值: {value!r}")


def from_jsonable(node, hint):
    """按类型提示把 JSON 结构还原为领域对象。"""
    if node is None:
        return None
    origin = typing.get_origin(hint)
    if origin in (typing.Union, types.UnionType):
        options = [a for a in typing.get_args(hint) if a is not type(None)]
        if len(options) != 1:
            raise TypeError(f"不支持的联合类型: {hint!r}")
        return from_jsonable(node, options[0])
    if hint is date:
        return date.fromisoformat(node[_DATE_TAG])
    if isinstance(hint, type) and issubclass(hint, Enum):
        return hint[node[_ENUM_TAG].split(".")[-1]]
    if isinstance(hint, type) and is_dataclass(hint):
        hints = typing.get_type_hints(hint)
        return hint(**{f.name: from_jsonable(node[f.name], hints[f.name]) for f in fields(hint)})
    if origin in (list, tuple, frozenset, set):
        item_hint = typing.get_args(hint)[0]
        items = [from_jsonable(v, item_hint) for v in node]
        if origin is tuple:
            return tuple(items)
        if origin in (frozenset, set):
            return origin(items)
        return items
    if origin is dict:
        key_hint, value_hint = typing.get_args(hint)
        return {from_jsonable(k, key_hint): from_jsonable(v, value_hint) for k, v in node[_DICT_TAG]}
    if hint in (str, int, float, bool):
        return hint(node) if hint is not bool else bool(node)
    raise TypeError(f"无法反序列化的类型: {hint!r}")
