"""The shape of final.json as debate_outcome.py writes it.

The page builds its markup from these values, so every one it uses is typed here, and the page still escapes each
of them: a number is a number, a severity or an outcome is one of the known words, a side or a status is a string.
"""

from __future__ import annotations

import types

from crossreview.debate_schemas import EXAMINE_SCHEMA, RESPOND_SCHEMA
from crossreview.json_schema import array, enum, shaped, string, typed
from crossreview.keys import ITEMS_KEY, PROPERTIES
from crossreview.outcome_files import AGENT
from crossreview.outcome_words import OUT_RANK, SEV_RANK
from crossreview.schema import check_schema

PIPELINE = types.MappingProxyType({
    "cross-review": "cross-review (находки, склейка, проверка по коду)",
    "external": "внешний прогон",
})
STRINGS = types.MappingProxyType(array(string()))
OPTIONAL_STRING = types.MappingProxyType(typed("string", nullable=True))
COUNT = types.MappingProxyType(typed("integer", minimum=0))
SEVERITY = types.MappingProxyType(enum(*SEV_RANK))
VERDICT = EXAMINE_SCHEMA[PROPERTIES]["verdicts"][ITEMS_KEY][PROPERTIES]
RESPONSE = RESPOND_SCHEMA[PROPERTIES]["responses"][ITEMS_KEY][PROPERTIES]


def _entry(**properties: object) -> dict:
    """A thread entry of one kind: an object that needs every property it names."""
    return shaped(tuple(properties), properties)


# the thread entries by kind; a `side` is claude, codex, claude+codex or the name of a source outside the roster
ENTRY = types.MappingProxyType({
    "finding": _entry(side=string(), sources=STRINGS),
    "pipeline": _entry(status=string()),
    "examine": _entry(
        side=string(),
        stance=VERDICT["stance"],
        severity=SEVERITY,
        argument=string(),
        evidence=string(),
    ),
    "respond": _entry(
        side=string(),
        position=RESPONSE["position"],
        severity=SEVERITY,
        argument=string(),
        evidence=string(),
    ),
    "answer": _entry(side=string(), answer=string(), evidence=string()),
    "moderator": _entry(verdict=string(), severity=SEVERITY, note=string()),
})
THREAD = types.MappingProxyType(array(_entry(kind=enum(*ENTRY))))
SHIFT = types.MappingProxyType(shaped(("from", "to", "direction", "by"), {
    "from": {"enum": [*SEV_RANK, None]},
    "to": SEVERITY,
    "direction": enum("raised", "lowered", "same", "dropped", "new"),
    "by": enum("side", "author", "moderator", "none"),
}))
CARD = types.MappingProxyType({
    "id": string(),
    "file": string(),
    "line": typed("integer"),
    "title": string(),
    "body": string(),
    "fix": OPTIONAL_STRING,
    "sources": STRINGS,
    "vendors": STRINGS,
    "status": string(),
    "outcome": enum(*OUT_RANK),
    "final_severity": SEVERITY,
    "thread": THREAD,
})
CARD_REQUIRED = ("id", "file", "line", "title", "body", "sources", "vendors", "status", "outcome", "final_severity",
                 "thread")
FLAG = types.MappingProxyType(typed("boolean"))
CARD_ITEM = types.MappingProxyType(shaped(
    (*CARD_REQUIRED, "severity_shift", "moderated", "moderators_split"),
    {**CARD, "severity_shift": SHIFT, "moderated": FLAG, "moderators_split": FLAG},
))
QUESTION = types.MappingProxyType(shaped(CARD_REQUIRED, CARD))
META_REQUIRED = ("agents", "raw", "synthesized", "duration_min", "pipeline", "round_dir", "debate_dir")
META = types.MappingProxyType(shaped(META_REQUIRED, {
    "agents": array(AGENT),
    "raw": COUNT,
    "synthesized": COUNT,
    "duration_min": COUNT,
    "pipeline": enum(*PIPELINE),
    "round_dir": string(),
    "debate_dir": string(),
    "task": OPTIONAL_STRING,
    "run": OPTIONAL_STRING,
    "started_at": OPTIONAL_STRING,
    "verify_model": OPTIONAL_STRING,
    "synthesis_model": OPTIONAL_STRING,
    "degraded": STRINGS,
}))
FINAL_SCHEMA = types.MappingProxyType(shaped(("meta", "items", "questions"), {
    "meta": META,
    "items": array(CARD_ITEM),
    "questions": array(QUESTION),
}))
CARD_LISTS = ("items", "questions")


def final_errors(final: object) -> list[str]:
    """Errors of final.json against its shape, then of each thread entry against the shape of its kind, which the
    schema subset cannot pick by itself."""
    errors = check_schema(final, FINAL_SCHEMA, "final.json")
    if errors:
        return errors
    return [error for where, entry in _thread_entries(final) for error in _entry_errors(where, entry)]


def _cards_of(final: dict) -> list[tuple[str, dict]]:
    return [
        (f"final.json.{collection}[{number}]", card)
        for collection in CARD_LISTS
        for number, card in enumerate(final[collection])
    ]


def _thread_entries(final: dict) -> list[tuple[str, dict]]:
    return [
        (f"{where}.thread[{index}]", entry)
        for where, card in _cards_of(final)
        for index, entry in enumerate(card["thread"])
    ]


def _entry_errors(where: str, entry: dict) -> list[str]:
    return check_schema(entry, ENTRY[entry["kind"]], where)
