"""Builders for the JSON schemas the scripts hold answers and run files to.

Each builder returns a fresh dict in the key order the schema files have, so a schema built here dumps the same
as the literal it replaces.
"""

from __future__ import annotations

from collections.abc import Mapping

TYPE = "type"
PROPERTIES = "properties"


def typed(*names: str, nullable: bool = False, **keywords: object) -> dict:
    """{"type": name} for one name, {"type": [names]} for several, then the keywords in the order given.
    A nullable type takes null as its last name."""
    kinds = [*names, "null"] if nullable else list(names)
    kind: object = kinds[0] if len(kinds) == 1 else kinds
    return {TYPE: kind, **keywords}


def string() -> dict:
    """Any string."""
    return typed("string")


def enum(*choices: str) -> dict:
    """One of the strings given."""
    return typed("string", enum=list(choices))


def array(element: Mapping) -> dict:
    """An array of element."""
    return typed("array", items=element)


def closed(**properties: Mapping) -> dict:
    """An object with exactly these properties, all of them required."""
    return typed("object", additionalProperties=False, required=list(properties), properties=properties)


def shaped(required: tuple[str, ...], properties: Mapping) -> dict:
    """An object with these properties, the required ones listed; other keys pass."""
    return typed("object", required=list(required), properties=dict(properties))


def with_props(schema: Mapping, **props: Mapping) -> dict:
    """schema with more properties after its own."""
    return {**schema, PROPERTIES: {**schema[PROPERTIES], **props}}
