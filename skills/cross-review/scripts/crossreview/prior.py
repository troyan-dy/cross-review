"""The prior runs of a task: what each recorded in its findings.json, oldest first.

The finder prompts name these directories for the models to read, so a symlink in the task directory or anywhere
below a prior run is an error, wherever it points.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from operator import itemgetter
from pathlib import Path

from crossreview.links import no_symlinks, symlink_error
from crossreview.runs import parse_time

SEVERITIES = ("critical", "major", "minor")
STARTED_AT = "started_at"


def has_run(run_dir: Path) -> bool:
    """A run that got as far as its manifest; an empty manifest is an external run still open."""
    manifest = run_dir / "manifest.json"
    return manifest.is_file() and manifest.stat().st_size > 0


def prior_runs(task_dir: Path, current: str | None = None) -> list[dict]:
    """Every run of the task but `current`, oldest first, with the counts its findings.json records."""
    folders = sorted(task_dir.iterdir()) if task_dir.is_dir() else []
    dated = [_dated(run_dir) for run_dir in folders if _is_prior(run_dir, current)]
    dated.sort(key=itemgetter(0))
    return [entry for _, entry in dated]


def _is_prior(run_dir: Path, current: str | None) -> bool:
    if run_dir.is_symlink():
        raise symlink_error(run_dir)
    if not run_dir.is_dir() or run_dir.name == current:
        return False
    no_symlinks(run_dir)
    return has_run(run_dir)


def _dated(run_dir: Path) -> tuple[datetime, dict]:
    entry = _prior_entry(run_dir)
    started = parse_time(entry[STARTED_AT])
    return started or datetime.fromtimestamp(run_dir.stat().st_mtime, timezone.utc), entry


def _prior_entry(run_dir: Path) -> dict:
    entry: dict = {
        "run": run_dir.name,
        "path": str(run_dir),
        STARTED_AT: None,
        "findings": None,
        "by_severity": None,
        "sources": None,
        "degraded": [],
    }
    record = _findings_record(run_dir)
    if record is not None:
        entry.update(_record_counts(record))
    return entry


def _findings_record(run_dir: Path) -> dict | None:
    try:
        record = json.loads((run_dir / "findings.json").read_text())
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _record_counts(record: dict) -> dict:
    listed = record.get("findings") or []
    findings = [finding for finding in listed if isinstance(finding, dict)]
    severities = [finding.get("severity") for finding in findings]
    sources = record.get("sources") or {}
    return {
        STARTED_AT: (record.get("stats") or {}).get(STARTED_AT),
        "findings": len(severities),
        "by_severity": {severity: severities.count(severity) for severity in SEVERITIES},
        "sources": f"{sources.get('reported', 0)}/{sources.get('expected', 0)}",
        "degraded": list(sources.get("degraded") or []),
    }
