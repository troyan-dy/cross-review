"""What debate_outcome.py reads besides the sides' answers: the files debate_build.py wrote and the run's
findings.json, each checked by its shape."""

from __future__ import annotations

import json
import types
from pathlib import Path

from crossreview.json_schema import array, enum, shaped, string, typed
from crossreview.keys import ID
from crossreview.schema import check_schema, summary

STRING = "string"
OBJECT = "object"
ARRAY = "array"
NUMBER = "number"
BOOLEAN = "boolean"
STRINGS = types.MappingProxyType(array(string()))
OPTIONAL_STRING = types.MappingProxyType(typed(STRING, nullable=True))
COUNT = types.MappingProxyType(typed("integer", minimum=0))
# what debate_build.py copies from a finding and adds to it; a card without the content is no finding at all
FINDING = types.MappingProxyType({
    ID: string(),
    "file": string(),
    "line": typed("integer", minimum=0),
    "severity": enum("critical", "major", "minor"),
    "title": string(),
    "body": string(),
    "fix": string(),
    "sources": STRINGS,
    "vendors": STRINGS,
})
CARD_FIELDS = (ID, "file", "line", "title", "body", "sources", "vendors")
STATUSES = ("confirmed", "refined", "unverified", "immaterial", "pre_existing", "rejected_by_verify", "dropped_by_synthesis")
ITEMS_SCHEMA = types.MappingProxyType(array(shaped(
    (*CARD_FIELDS, "severity", "status"),
    {**FINDING, "status": enum(*STATUSES)},
)))
FOR_SCHEMA = types.MappingProxyType(shaped(("findings", "open_questions"), {
    "findings": array(shaped((ID,), {ID: string()})),
    "open_questions": array(shaped(CARD_FIELDS, FINDING)),
}))
BUILT = types.MappingProxyType({"items.json": ITEMS_SCHEMA, "for-claude.json": FOR_SCHEMA, "for-codex.json": FOR_SCHEMA})
# what `meta` takes from the run's findings.json, as review_verify.py or an external run writes it; every key is optional
AGENT = types.MappingProxyType(shaped(("name", "executor", "raised"), {
    "name": string(),
    "executor": string(),
    "actual_model": OPTIONAL_STRING,
    "requested_model": OPTIONAL_STRING,
    "effort": OPTIONAL_STRING,
    "raised": COUNT,
    "degraded": typed(BOOLEAN, nullable=True),
}))
STAGE = types.MappingProxyType(typed(OBJECT, properties={"name": OPTIONAL_STRING, "model": OPTIONAL_STRING}))
FINDINGS_SCHEMA = types.MappingProxyType(typed(OBJECT, properties={
    "scope": typed(OBJECT, nullable=True, properties={"task": OPTIONAL_STRING, "run": OPTIONAL_STRING}),
    "stats": typed(OBJECT, nullable=True, properties={
        "duration_ms": typed(NUMBER, nullable=True, minimum=0),
        "started_at": OPTIONAL_STRING,
        "stages": typed(ARRAY, nullable=True, items=STAGE),
    }),
    "sources": typed(OBJECT, nullable=True, properties={
        "agents": typed(ARRAY, nullable=True, items=AGENT),
        "degraded": typed(ARRAY, nullable=True, items=string()),
    }),
    "findings": typed(ARRAY, nullable=True),
    "immaterial": typed(ARRAY, nullable=True),
    "pre_existing": typed(ARRAY, nullable=True),
}))


def load_findings(path: Path) -> dict:
    """The run's findings.json for `meta`, checked by shape; {} when the run has none."""
    if not path.exists():
        return {}
    try:
        findings = json.loads(path.read_text())
    except ValueError as error:
        raise ValueError(f"{path}: not JSON: {error}") from None
    errors = check_schema(findings, FINDINGS_SCHEMA, "findings.json")
    if errors:
        raise ValueError(f"{path}: not a review's findings.json: {summary(errors)}")
    return findings


def load_built(path: Path) -> object:
    """What debate_build.py wrote, checked by its shape: an `{}` in place of the item list is no empty debate."""
    try:
        built = json.loads(path.read_text())
    except ValueError as error:
        raise ValueError(f"{path}: not JSON: {error}, run debate_build.py again") from None
    errors = check_schema(built, BUILT[path.name], path.name)
    if errors:
        raise ValueError(f"{path}: not the output of debate_build.py, run it again: {summary(errors)}")
    return built


def pipeline_of(round_dir: Path) -> str:
    """cross-review for a run this plugin composed (its manifest names a tier), external for any other run."""
    try:
        manifest = json.loads((round_dir / "manifest.json").read_text())
    except (OSError, ValueError):
        return "external"
    return "cross-review" if isinstance(manifest, dict) and "tier" in manifest else "external"
