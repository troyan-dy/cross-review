"""report.md of a review run: the run's report layout, worded in Russian, with the verify summary under the
header."""

from __future__ import annotations

import types

from crossreview.keys import AGENTS, EXPECTED, FINDINGS, IMMATERIAL, OPEN_QUESTIONS, PRE_EXISTING, REPORTED
from crossreview.keys import SEVERITY, SOURCES
from crossreview.report_lines import agent_row, finding_lines
from crossreview.severities import rank
from crossreview.stage_run import degraded_names

SEVERITY_HEADINGS = types.MappingProxyType({
    "critical": "Критические",
    "major": "Серьёзные",
    "minor": "Незначительные",
})
MOVED_SECTIONS = (
    ("Открытые вопросы", OPEN_QUESTIONS),
    ("Было до изменений", PRE_EXISTING),
    ("Несущественное", IMMATERIAL),
)
SOURCES_TABLE = (
    "## Источники",
    "",
    "| агент | исполнитель | модель | effort | находок | статус |",
    "| --- | --- | --- | --- | --- | --- |",
)
_NO_HEADING = object()


def markdown(report: dict, notes: list[str]) -> str:
    """report.md for a findings.json shape and the notes that go under its header."""
    degraded = degraded_names(report)
    lines = [
        *_head_lines(report, degraded),
        *(line for note in notes for line in (note, "")),
        *_severity_lines(report[FINDINGS]),
        *_moved_lines(report),
        *_agents_table(report[SOURCES], degraded),
    ]
    while lines and not lines[-1]:
        lines.pop()
    text = "\n".join(lines)
    return f"{text}\n"


def _head_lines(report: dict, degraded: list[str]) -> list[str]:
    scope = report["scope"]
    lines = [_title(scope), ""]
    if scope.get("scope_path"):
        lines.extend([f"Предмет: `{scope['scope_path']}`", ""])
    if degraded:
        lines.extend([_degraded_line(report[SOURCES], degraded), ""])
    return lines


def _degraded_line(sources: dict, degraded: list[str]) -> str:
    answered = sources.get(REPORTED, 0)
    expected = sources.get(EXPECTED, 0)
    missing = ", ".join(degraded)
    return f"**Раунд неполный**: ответили {answered} из {expected} источников, нет {missing}"


def _title(scope: dict) -> str:
    if not scope.get("task"):
        return "# Ревью"
    run = f" / {scope['run']}" if scope.get("run") else ""
    return f"# Ревью: {scope['task']}{run}"


def _severity_lines(findings: list[dict]) -> list[str]:
    ordered = sorted(findings, key=lambda finding: rank(finding[SEVERITY]))
    if not ordered:
        return ["Находок нет.", ""]
    lines = []
    current: object = _NO_HEADING
    for finding in ordered:
        if finding[SEVERITY] != current:
            current = finding[SEVERITY]
            heading = SEVERITY_HEADINGS.get(current, current or "Без severity")
            lines.extend([f"## {heading}", ""])
        lines.extend(finding_lines(finding))
    return lines


def _moved_lines(report: dict) -> list[str]:
    lines = []
    for heading, name in MOVED_SECTIONS:
        if report[name]:
            lines.extend([f"## {heading}", ""])
            lines.extend(line for finding in report[name] for line in finding_lines(finding))
    return lines


def _agents_table(sources: dict, degraded: list[str]) -> list[str]:
    agents = sources.get(AGENTS) or []
    if not agents:
        return []
    return [*SOURCES_TABLE, *(agent_row(agent, degraded) for agent in agents), ""]
