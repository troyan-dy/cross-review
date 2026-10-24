"""The output schemas of the debate: the sides' answers in both rounds and the moderators'."""

from __future__ import annotations

import json
import types
from pathlib import Path

from crossreview.jsonio import pretty_json
from crossreview.json_schema import array, closed, enum, string, typed, with_props
from crossreview.keys import ITEMS_KEY, PROPERTIES
from crossreview.schema import check_schema, summary

SEVERITIES = ("critical", "major", "minor")
SEVERITY_OR_NONE = (*SEVERITIES, "none")

EXAMINE_SCHEMA = types.MappingProxyType(closed(
    verdicts=array(closed(
        id=string(),
        stance=enum("real", "real_but_different", "not_real", "not_worth_fixing"),
        severity=enum(*SEVERITY_OR_NONE),
        argument=string(),
        evidence=string(),
    )),
    open_question_answers=array(closed(id=string(), answer=string(), evidence=string())),
))

RESPOND_SCHEMA = types.MappingProxyType(closed(
    responses=array(closed(
        id=string(),
        position=enum("concede", "defend", "amend"),
        severity=enum(*SEVERITY_OR_NONE),
        argument=string(),
        evidence=string(),
    )),
    side_findings=array(closed(
        file=string(),
        line=typed("integer"),
        severity=enum(*SEVERITIES),
        title=string(),
        body=string(),
        fix=string(),
        evidence=string(),
    )),
))

MODERATE_SCHEMA = types.MappingProxyType(closed(
    decisions=array(closed(
        id=string(),
        verdict=string(),
        severity=enum(*SEVERITY_OR_NONE),
        note=string(),
    )),
    extra=array(closed(
        id=string(),
        file=string(),
        line=typed("integer"),
        severity=enum(*SEVERITIES),
        title=string(),
        body=string(),
        fix=string(),
        note=string(),
    )),
))

SCHEMAS = types.MappingProxyType({"examine": EXAMINE_SCHEMA, "respond": RESPOND_SCHEMA, "moderate": MODERATE_SCHEMA})

# the arrays a Claude moderator and debate_moderators.py write: decisions with the split marks, extras with their side
DECISIONS_SCHEMA = types.MappingProxyType(array(with_props(
    MODERATE_SCHEMA[PROPERTIES]["decisions"][ITEMS_KEY],
    split=typed("boolean"),
    missing=typed("boolean"),
)))
EXTRA_SCHEMA = types.MappingProxyType(array(with_props(
    MODERATE_SCHEMA[PROPERTIES]["extra"][ITEMS_KEY],
    sources=array(string()),
    vendors=array(string()),
)))


def write_schemas(paths: dict[str, Path]) -> None:
    """The output schemas of the debate as files, one per prompt name."""
    for name, path in paths.items():
        path.write_text(pretty_json(dict(SCHEMAS[name]), ensure_ascii=True))


def load_answer(path: Path, schema: types.MappingProxyType, default: object) -> object:
    """A side's or a moderator's answer checked against its schema, default when the file is missing.

    An answer that is not JSON or breaks the schema raises ValueError naming the file, so the step is rerun.
    """
    if not path.exists():
        return default
    try:
        answer = json.loads(path.read_text())
    except ValueError as error:
        raise ValueError(f"{path}: not JSON: {error}") from None
    errors = check_schema(answer, schema, path.name)
    if errors:
        raise ValueError(f"{path}: does not match the schema, rerun the step: {summary(errors)}")
    return answer
