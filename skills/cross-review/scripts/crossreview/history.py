"""The prior-run inventory every finder prompt carries, one padded line per run."""

from __future__ import annotations

from datetime import timezone
from pathlib import Path

from crossreview.runs import parse_time

COLUMNS = 3
# the closing lines of the prior-run block: the inventory never goes without them
HISTORY_GUARD = """Each round holds report.md (rendered) and findings.json (machine shape). Read the rounds you judge relevant.

Re-evaluate everything independently. A prior round reporting an issue is not evidence that it is real,
and a prior round missing one is not evidence that it is absent."""


def history(task_dir: Path, runs: list[dict]) -> str:
    """The inventory of runs, prior_runs() order; empty without runs."""
    if not runs:
        return ""
    rows = [_history_row(entry) for entry in runs]
    columns = list(zip(*rows))[:COLUMNS]
    widths = [max(map(len, column)) for column in columns]
    lines = [f"Prior rounds for this task: {task_dir}/", *(_padded(row, widths) for row in rows)]
    return "\n".join(lines)


def _padded(row: tuple[str, ...], widths: list[int]) -> str:
    cells = [cell.ljust(width) for cell, width in zip(row, widths)]
    joined = "  ".join([*cells, row[-1]])
    return f"  {joined}"


def _history_row(entry: dict) -> tuple[str, str, str, str]:
    started = parse_time(entry["started_at"])
    when = started.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ") if started else "unknown"
    return entry["run"], when, _counts_text(entry), _sources_text(entry)


def _counts_text(entry: dict) -> str:
    if entry["findings"] is None:
        return "counts unknown"
    by_severity = entry["by_severity"]
    severities = f"{by_severity['critical']} critical, {by_severity['major']} major, {by_severity['minor']} minor"
    return f"{entry['findings']} findings ({severities})"


def _sources_text(entry: dict) -> str:
    if entry["findings"] is None:
        return "sources unknown"
    degraded = ", ".join(entry["degraded"])
    suffix = f", {degraded} degraded" if entry["degraded"] else ""
    return f"sources {entry['sources']}{suffix}"
