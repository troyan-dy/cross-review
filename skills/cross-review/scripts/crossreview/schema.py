"""The JSON Schema subset the plugin schemas use, checked with the standard library only.

Supported: type (one name or a list of them), enum, required, properties, additionalProperties, items, minimum
and maximum. A schema may be a dict or a read-only mapping.
"""

from __future__ import annotations

import types
from collections.abc import Mapping

JSON_TYPES = types.MappingProxyType({
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "number": (int, float),
    "null": type(None),
})
SHOWN_ERRORS = 5


def is_type(candidate: object, name: str) -> bool:
    """Whether candidate is of the JSON type name; a bool is neither an integer nor a number."""
    if name == "integer":
        return isinstance(candidate, int) and not isinstance(candidate, bool)
    if name == "number":
        return isinstance(candidate, (int, float)) and not isinstance(candidate, bool)
    return isinstance(candidate, JSON_TYPES[name])


def check_schema(candidate: object, schema: Mapping, where: str = "answer") -> list[str]:
    """Errors of candidate against schema; an empty list is a match. A wrong type is the only error reported."""
    kind = schema.get("type")
    kinds = [kind] if isinstance(kind, str) else kind or []
    if kinds and not any(is_type(candidate, name) for name in kinds):
        expected = " or ".join(kinds)
        return [f"{where}: expected {expected}"]
    return [
        *_enum_errors(candidate, schema, where),
        *_range_errors(candidate, schema, where),
        *_object_errors(candidate, schema, where),
        *_array_errors(candidate, schema, where),
    ]


def summary(errors: list[str]) -> str:
    """The first errors of a check, joined for one message."""
    return "; ".join(errors[:SHOWN_ERRORS])


def _enum_errors(candidate: object, schema: Mapping, where: str) -> list[str]:
    allowed = schema.get("enum")
    if allowed is None or candidate in allowed:
        return []
    choices = ", ".join(map(str, allowed))
    return [f"{where}: {candidate!r} is not one of {choices}"]


def _range_errors(candidate: object, schema: Mapping, where: str) -> list[str]:
    if not is_type(candidate, "number"):
        return []
    errors = []
    minimum, maximum = schema.get("minimum"), schema.get("maximum")
    if minimum is not None and candidate < minimum:
        errors.append(f"{where}: {candidate} is below {minimum}")
    if maximum is not None and candidate > maximum:
        errors.append(f"{where}: {candidate} is above {maximum}")
    return errors


def _object_errors(candidate: object, schema: Mapping, where: str) -> list[str]:
    if not isinstance(candidate, dict):
        return []
    required = schema.get("required") or []
    errors = [f"{where}.{name}: missing" for name in required if name not in candidate]
    for key, member in candidate.items():
        member_schema = (schema.get("properties") or {}).get(key)
        if member_schema is not None:
            errors.extend(check_schema(member, member_schema, f"{where}.{key}"))
        elif schema.get("additionalProperties") is False:
            errors.append(f"{where}.{key}: unknown key")
    return errors


def _array_errors(candidate: object, schema: Mapping, where: str) -> list[str]:
    item_schema = schema.get("items")
    if not isinstance(candidate, list) or not isinstance(item_schema, Mapping):
        return []
    errors = []
    for index, member in enumerate(candidate):
        errors.extend(check_schema(member, item_schema, f"{where}[{index}]"))
    return errors
