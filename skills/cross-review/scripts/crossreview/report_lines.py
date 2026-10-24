"""The pieces of report.md: one finding's block, its location, an agent's row of the sources table."""

from __future__ import annotations

import types

from crossreview.keys import BODY, CONFIDENCE, DEGRADED, END_LINE, EXECUTOR, FILE_KEY, FIX, ID
from crossreview.keys import LENSES, LINE, NAME, SOURCES, TITLE, VERDICT

VERDICT_WORDS = types.MappingProxyType({
    "confirmed": "подтверждено",
    "refined": "уточнено",
    "rejected": "отклонено",
    "immaterial": "несущественно",
    "pre_existing": "было до изменений",
    "unverified": "не проверено",
})
RAISED = "raised"


def location(finding: dict) -> str:
    """file:line or file:line-end of a finding; the file alone without a line."""
    if not finding.get(FILE_KEY):
        return "(без файла)"
    line = finding.get(LINE) or 0
    end = finding.get(END_LINE) or 0
    if line <= 0:
        return finding[FILE_KEY]
    span = f"{line}-{end}" if end > line else str(line)
    return f"{finding[FILE_KEY]}:{span}"


def finding_lines(finding: dict) -> list[str]:
    """One finding's block: title, location, body, fix and the meta line."""
    lines = [f"### {finding[TITLE]}", "", f"`{location(finding)}`", ""]
    if finding.get(BODY):
        lines.extend([finding[BODY], ""])
    if finding.get(FIX):
        lines.extend([f"Исправление: {finding[FIX]}", ""])
    meta = " | ".join(_meta(finding))
    return [*lines, f"_{meta}_", ""]


def model_of(agent: dict) -> str:
    """The model an agent ran on, and the requested one when it differs."""
    actual = agent.get("actual_model") or ""
    requested = agent.get("requested_model") or ""
    if not actual:
        return requested or "-"
    if not requested or actual == requested:
        return actual
    return f"{actual} (запрошена {requested})"


def agent_row(agent: dict, degraded: list[str]) -> str:
    """An agent's row of the sources table."""
    status = "ответил"
    if agent.get(DEGRADED) or agent[NAME] in degraded:
        status = "не ответил"
    elif not agent.get(RAISED):
        status = "ответил, ничего не нашёл"
    cells = (
        agent[NAME],
        agent.get(EXECUTOR, ""),
        model_of(agent),
        agent.get("effort", ""),
        agent.get(RAISED, 0),
        status,
    )
    joined = " | ".join(map(str, cells))
    return f"| {joined} |"


def _meta(finding: dict) -> list[str]:
    meta = [f"id: {finding[ID]}", f"уверенность: {finding[CONFIDENCE]}"]
    if finding.get(SOURCES):
        sources = ", ".join(finding[SOURCES])
        meta.append(f"источники: {sources}")
    if finding.get(LENSES):
        lenses = ", ".join(finding[LENSES])
        meta.append(f"линзы: {lenses}")
    if finding.get(VERDICT):
        verdict = VERDICT_WORDS.get(finding[VERDICT], finding[VERDICT])
        meta.append(f"вердикт: {verdict}")
    return meta
