"""The answer of the synthesis agent, read and cleaned to the synthesis schema."""

from __future__ import annotations

import json
from pathlib import Path

from crossreview.errors import ConfigError
from crossreview.jsonio import extract_json
from crossreview.keys import END_LINE, FINDINGS, FIX, ITEMS_KEY, OPEN_QUESTIONS, PRE_EXISTING, PROPERTIES
from crossreview.layers import resolve
from crossreview.schema import SHOWN_ERRORS, check_schema, summary

LISTS = (FINDINGS, OPEN_QUESTIONS, PRE_EXISTING)


def read_answer(path: Path) -> dict:
    """The three lists of a synthesis answer, cleaned to the schema: keys the schema does not name are dropped,
    a missing `fix` or `end_line` becomes empty, and a missing open_questions or pre_existing list is empty."""
    answer = _answer_object(path)
    schema = json.loads(resolve(None, "schemas", "synthesis").read_text())
    clean = _cleaned_lists(answer, schema)
    errors = check_schema(clean, schema)
    if errors:
        raise ConfigError(f"{path}: the answer does not match the synthesis schema: {_listed(errors)}")
    return clean


def _answer_object(path: Path) -> dict:
    if not path.is_file():
        raise ConfigError(f"{path}: no synthesis answer, save the agent's answer there first")
    try:
        answer = extract_json(path.read_text(errors="replace"))
    except ValueError as error:
        raise ConfigError(f"{path}: {error}") from None
    findings = answer.get(FINDINGS) if isinstance(answer, dict) else None
    if not isinstance(findings, list):
        raise ConfigError(f"{path}: expected an object with a findings array, the synthesis schema")
    return answer


def _cleaned(entries: object, props: dict) -> object:
    if not isinstance(entries, list):
        return entries
    return [_cleaned_entry(entry, props) if isinstance(entry, dict) else entry for entry in entries]


def _cleaned_entry(entry: dict, props: dict) -> dict:
    named = {key: field for key, field in entry.items() if key in props}
    return {END_LINE: 0, FIX: "", **named}


def _listed(errors: list[str]) -> str:
    extra = len(errors) - SHOWN_ERRORS
    more = f" and {extra} more" if extra > 0 else ""
    return f"{summary(errors)}{more}"


def _cleaned_lists(answer: dict, schema: dict) -> dict:
    clean = {}
    for name in LISTS:
        props = schema[PROPERTIES][name][ITEMS_KEY][PROPERTIES]
        clean[name] = _cleaned(answer.get(name, []), props)
    return clean
