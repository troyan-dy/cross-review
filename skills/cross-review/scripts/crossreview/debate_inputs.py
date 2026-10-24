"""The files of a finished run the debate starts from, and how a raw finding is matched to a synthesized one."""

from __future__ import annotations

import json
from pathlib import Path

from crossreview.events import parse_event
from crossreview.keys import FINDINGS, ID, KIND, LINE, SEVERITY, SOURCES, TITLE

NEAR_LINES = 5


def load(path: Path) -> dict:
    """A run file as an object; a missing, unreadable or malformed one is a ValueError that names the path."""
    try:
        parsed = json.loads(path.read_text())
    except OSError as error:
        reason = error.strerror or error
        raise ValueError(f"{path}: {reason}") from None
    except ValueError as error:
        raise ValueError(f"{path}: not JSON: {error}") from None
    if not isinstance(parsed, dict):
        raise ValueError(f"{path}: expected an object")
    return parsed


def tag(title: str) -> str:
    """A leading `[...]` tag in a title, used to match a raw finding to the synthesized one it was folded into."""
    if not title.startswith("[") or "]" not in title:
        return ""
    head = title.split("]", 1)[0]
    return f"{head}]"


def dropped_from_events(events: Path) -> set[str] | None:
    """Ids synthesis dropped, from the `dropped` events of events.jsonl; None when the run has no such log."""
    if not events.exists():
        return None
    ids: set[str] = set()
    for line in events.read_text().splitlines():
        event = parse_event(line)
        if isinstance(event, dict) and event.get(KIND) == "dropped":
            listed = event.get(FINDINGS) or []
            ids.update(finding[ID] for finding in listed if ID in finding)
    return ids


def covered(raw: dict, synthesized: list[dict]) -> bool:
    """A raw finding is covered when synthesis could not have dropped it or when it was folded into a kept one.

    Synthesis drops only single-source minors, so a missing major/critical was merged. Otherwise: same id,
    same leading tag, or the same agent in the same file within five lines of a synthesized finding.
    """
    if raw.get(SEVERITY) != "minor":
        return True
    title_tag = _title_tag(raw)
    if title_tag and any(_title_tag(other) == title_tag for other in synthesized):
        return True
    return any(_folded(raw, other) for other in synthesized)


def _folded(raw: dict, other: dict) -> bool:
    if raw[ID] == other[ID]:
        return True
    source = (raw.get(SOURCES) or [None])[0]
    sources = other.get(SOURCES) or []
    if source not in sources or raw.get("file") != other.get("file"):
        return False
    return abs(_line(raw) - _line(other)) <= NEAR_LINES


def _title_tag(finding: dict) -> str:
    return tag(finding.get(TITLE) or "")


def _line(finding: dict) -> int:
    return finding.get(LINE) or 0
