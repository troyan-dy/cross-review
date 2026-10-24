"""The shape of cross-review.json and how a repository layer merges over the plugin defaults.

In a shape "*" stands for any key and a one-item list for an array of that item.
"""

from __future__ import annotations

import types
from collections.abc import Mapping
from typing import Any

from crossreview.errors import ConfigError

ANY_KEY = "*"
WHOLE_FILE = "the file"
TIER = types.MappingProxyType({
    "claude": str,
    "codex": str,
    "moderators": {"claude": str, "codex": str},
})
SHAPE = types.MappingProxyType({
    "tier": str,
    "tiers": {ANY_KEY: TIER},
    "roster": [{"name": str, "lenses": [str]}],
    "stages": {"synthesis": bool, "verify": bool, "verify_groups": int, "verify_group_by": str},
})
TYPE_NAMES = types.MappingProxyType({str: "a string", bool: "true or false", int: "an integer"})


def key_path(where: str, key: object) -> str:
    """The dotted path of key under where, as the error messages name it."""
    return f"{where}.{key}" if where else str(key)


def check_shape(candidate: object, shape: Any, where: str, *, complete: bool) -> None:
    """Check candidate against a shape. complete=False lets a layer leave keys out; an array item is always
    complete, because an array replaces the default whole."""
    if isinstance(shape, Mapping):
        _check_object(candidate, shape, where, complete=complete)
    elif isinstance(shape, list):
        _check_array(candidate, shape[0], where)
    else:
        _check_scalar(candidate, shape, where)


def merge(base: object, over: object) -> object:
    """Objects merge key by key, anything else — arrays included — is replaced by the override."""
    if not isinstance(base, dict) or not isinstance(over, dict):
        return over
    merged = dict(base)
    for key, layer_value in over.items():
        merged[key] = merge(base.get(key), layer_value)
    return merged


def _check_object(candidate: object, shape: Mapping, where: str, *, complete: bool) -> None:
    place = where or WHOLE_FILE
    if not isinstance(candidate, dict):
        raise ConfigError(f"{place}: expected an object")
    for key, member in candidate.items():
        _check_member(member, key, where, shape, complete=complete)
    if ANY_KEY in shape or not complete:
        return
    missing = [name for name in shape if name not in candidate]
    if missing:
        listed = ", ".join(missing)
        raise ConfigError(f"{place}: missing {listed}")


def _check_member(member: object, key: str, where: str, shape: Mapping, *, complete: bool) -> None:
    path = key_path(where, key)
    member_shape = shape.get(key, shape.get(ANY_KEY))
    if member_shape is None:
        known = ", ".join(shape)
        raise ConfigError(f"{path}: unknown key, expected one of {known}")
    check_shape(member, member_shape, path, complete=complete)


def _check_array(candidate: object, item_shape: Any, where: str) -> None:
    if not isinstance(candidate, list):
        raise ConfigError(f"{where}: expected an array")
    for index, member in enumerate(candidate):
        check_shape(member, item_shape, f"{where}[{index}]", complete=True)


def _check_scalar(candidate: object, shape: type, where: str) -> None:
    bool_for_int = shape is int and isinstance(candidate, bool)
    if bool_for_int or not isinstance(candidate, shape):
        raise ConfigError(f"{where}: expected {TYPE_NAMES[shape]}")
