"""The synthesis rules the script applies over the model's grouping: attribution, confidence, severity, drop."""

from __future__ import annotations

from crossreview.errors import ConfigError
from crossreview.keys import CONFIDENCE, END_LINE, FIX, ID, LENSES, SEVERITY, SOURCES
from crossreview.severities import rank

GATING = ("critical", "major")
DROP_BELOW = 80
BOOST = 10
CAP = 99


def union(lists: list[list[str]]) -> list[str]:
    """Every name once, in first-seen order: one agent under two lenses is still one source."""
    merged: list[str] = []
    for names in lists:
        merged.extend([name for name in names if name not in merged])
    return merged


def combine(output: dict, inputs: list[dict], boost: bool) -> dict:
    """One synthesized finding: the wording and anchor of output, attribution, confidence and severity from the
    inputs it merges."""
    ids = [finding[ID] for finding in inputs]
    sources = union([finding[SOURCES] for finding in inputs])
    top = max(finding[CONFIDENCE] for finding in inputs)
    boosted = min(CAP, top + BOOST * (len(sources) - 1))
    return {
        ID: ids[0],
        "file": output["file"],
        "line": output["line"],
        END_LINE: output.get(END_LINE, 0),
        SEVERITY: min((finding[SEVERITY] for finding in inputs), key=rank),
        CONFIDENCE: boosted if boost else top,
        "title": output["title"],
        "body": output["body"],
        FIX: output.get(FIX, ""),
        SOURCES: sources,
        LENSES: union([finding[LENSES] for finding in inputs]),
        "verdict": "",
        "merged_ids": ids,
    }


def attribute(
    outputs: list[dict],
    inputs: dict[str, dict],
    claimed: set[str],
    boost: bool,
) -> list[dict]:
    """The outputs of one list with their inputs resolved. An output listing one id twice merges it once."""
    attributed = []
    for output in outputs:
        ids = _claim(output, inputs, claimed)
        attributed.append(combine(output, [inputs[finding_id] for finding_id in ids], boost))
    return attributed


def droppable(finding: dict) -> bool:
    """The drop rule: one source, confidence below 80, and neither critical nor major."""
    single = len(finding[SOURCES]) == 1
    return single and finding[CONFIDENCE] < DROP_BELOW and finding[SEVERITY] not in GATING


def severity_order(finding: dict) -> int:
    """The sort key of a finding: critical first."""
    return rank(finding[SEVERITY])


def _claim(output: dict, inputs: dict, claimed: set[str]) -> list[str]:
    ids = list(dict.fromkeys(output["merged_ids"]))
    if not ids:
        raise ConfigError(f"synthesized finding {output['title']!r} merged no input ids")
    for finding_id in ids:
        if finding_id not in inputs:
            raise ConfigError(f"synthesis merged unknown finding id {finding_id!r}")
        if finding_id in claimed:
            raise ConfigError(f"synthesis merged finding id {finding_id!r} into more than one output")
        claimed.add(finding_id)
    return ids
