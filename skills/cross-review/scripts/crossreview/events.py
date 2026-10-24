"""events.jsonl of a run: one JSON event per line, which stages append and rewrite."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from crossreview import keys
from crossreview.jsonio import now
from crossreview.runs import parse_time

EVENTS = "events.jsonl"
MS_IN_SECOND = 1000


def read_events(run_dir: Path) -> list[str]:
    """The lines of events.jsonl; none when the run has no log yet."""
    path = run_dir / EVENTS
    return path.read_text().splitlines() if path.is_file() else []


def write_events(run_dir: Path, lines: list[str], events: list[dict]) -> None:
    """Replace events.jsonl with lines kept from it, then the new events."""
    fresh = [json.dumps(event, ensure_ascii=False) for event in events]
    rows = (*lines, *fresh)
    (run_dir / EVENTS).write_text("".join(f"{row}\n" for row in rows))


def append_events(run_dir: Path, events: list[dict]) -> None:
    """Add events at the end of events.jsonl."""
    with (run_dir / EVENTS).open("a") as log:
        for event in events:
            log.write(f"{json.dumps(event, ensure_ascii=False)}\n")


def make_event(kind: str, at: object = None, **fields: object) -> dict:
    """An event in the layout of events.jsonl: its kind, the fields in the order given, then when it happened."""
    return {keys.KIND: kind, **fields, keys.AT: at or now()}


def parse_event(line: str) -> object:
    """One line of events.jsonl as JSON; None for a line that is not JSON."""
    try:
        return json.loads(line)
    except ValueError:
        return None


def stage_duration(lines: list[str], stage: str) -> int:
    """Milliseconds since the last stage event of `stage`, the one its prepare wrote; 0 without it."""
    starts = [mark for mark in map(parse_event, lines) if _is_stage(mark, stage)]
    if not starts:
        return 0
    started = parse_time(starts[-1].get(keys.AT))
    if started is None:
        return 0
    elapsed = datetime.now().astimezone() - started
    return int(elapsed.total_seconds() * MS_IN_SECOND)


def _is_stage(event: object, stage: str) -> bool:
    if not isinstance(event, dict):
        return False
    kind = event.get(keys.KIND)
    return kind == keys.STAGE and event.get(keys.STAGE) == stage
