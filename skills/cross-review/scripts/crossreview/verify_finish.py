"""What the verify stage leaves for the user: findings.json in the run format and report.md."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from crossreview.errors import ConfigError
from crossreview.events import parse_event
from crossreview.jsonio import write_json
from crossreview.keys import FINDINGS, NAME, SOURCES, STAGE, STAGES
from crossreview.report_md import markdown
from crossreview.runs import locate, parse_time
from crossreview.stage_run import StageInput, read_manifest
from crossreview.verify_groups import VERIFY

STARTED_AT = "started_at"


def load_run(run_dir: Path) -> StageInput:
    """The repository, the manifest and stages/2-synthesized.json of a synthesized run."""
    repo = locate(run_dir).repo
    manifest = read_manifest(run_dir)
    try:
        synthesized = json.loads((run_dir / STAGES / "2-synthesized.json").read_text())
    except (OSError, ValueError):
        raise ConfigError(f"{run_dir}: no readable stages/2-synthesized.json, run review_synth.py first") from None
    if (run_dir / "debate").exists():
        raise ConfigError(f"{run_dir}: the debate already started over this verification, start a new run")
    return StageInput(repo, manifest, synthesized)


def stage_entry(stages: dict, duration: int, *, ran: bool) -> dict:
    """The verify row of stats.stages; a stage that dispatched nothing names no runner."""
    entry = {NAME: VERIFY, "duration_ms": duration}
    if ran:
        entry.update(executor="claude", model=stages["model"], effort=stages["effort"])
    return entry


def own_event(line: str) -> bool:
    """An event an earlier apply wrote; prepare's stage event stays, it times the stage."""
    event = parse_event(line)
    if not isinstance(event, dict):
        return False
    return event.get(STAGE) == VERIFY and event.get("kind") != STAGE


def finish(run_dir: Path, manifest: dict, report: dict, notes: list[str]) -> dict:
    """Write findings.json in the run format and report.md for the user."""
    final = _final(manifest, report)
    path = run_dir / "findings.json"
    write_json(path, final)
    report_md = run_dir / "report.md"
    report_md.write_text(markdown(final, notes))
    return {
        "findings_json": str(path),
        "report": str(report_md),
        FINDINGS: len(final[FINDINGS]),
    }


def _final(manifest: dict, report: dict) -> dict:
    return {
        "scope": report["scope"],
        SOURCES: report[SOURCES],
        FINDINGS: report[FINDINGS],
        "open_questions": report.get("open_questions") or [],
        "pre_existing": report.get("pre_existing") or [],
        "immaterial": report.get("immaterial") or [],
        "stats": _final_stats(manifest, report.get("stats") or {}),
    }


def _final_stats(manifest: dict, stats: dict) -> dict:
    started_at = stats.get(STARTED_AT) or manifest.get(STARTED_AT)
    started = parse_time(started_at)
    finished = datetime.now().astimezone()
    duration = int((finished - started).total_seconds() * 1000) if started else 0
    return {
        STARTED_AT: started_at,
        "finished_at": finished.isoformat(timespec="seconds"),
        "duration_ms": duration,
        "tokens": stats.get("tokens", 0),
        STAGES: stats.get(STAGES) or [],
    }
