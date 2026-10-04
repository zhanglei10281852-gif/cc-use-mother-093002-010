"""类型导向的 JSON 编解码：把领域实体无损转换为可 JSON 序列化的结构。

编码按值分派（枚举存名字、日期存 ISO 字符串、字典存键值对列表），
解码按目标类型分派，因此能正确处理枚举键字典、嵌套数据类等结构。
"""
from __future__ import annotations

import dataclasses
import enum
import typing
from datetime import date


def encode(value):
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, enum.Enum):
        return value.name
    if isinstance(value, date):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: encode(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, (tuple, list)):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        return [[encode(k), encode(v)] for k, v in value.items()]
    raise TypeError(f"无法编码的值: {value!r}")


def decode(type_, value):
    if value is None:
        return None
    origin = typing.get_origin(type_)
    if origin is typing.Union:
        args = [a for a in typing.get_args(type_) if a is not type(None)]
        if len(args) == 1:
            return decode(args[0], value)
        raise TypeError(f"不支持的联合类型: {type_!r}")
    if origin in (tuple, list):
        (elem_type,) = typing.get_args(type_)[:1]
        items = [decode(elem_type, item) for item in value]
        return tuple(items) if origin is tuple else items
    if origin is dict:
        key_type, val_type = typing.get_args(type_)
        return {decode(key_type, k): decode(val_type, v) for k, v in value}
    if isinstance(type_, type):
        if issubclass(type_, enum.Enum):
            return type_[value]
        if issubclass(type_, date):
            return date.fromisoformat(value)
        if dataclasses.is_dataclass(type_):
            hints = typing.get_type_hints(type_)
            kwargs = {
                f.name: decode(hints[f.name], value[f.name])
                for f in dataclasses.fields(type_)
            }
            return type_(**kwargs)
    return value
