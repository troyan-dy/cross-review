"""JSON in and out of the scripts: the one line a script prints, the run files, an agent's answer."""

from __future__ import annotations

import json
import re
import sys
from contextlib import suppress
from datetime import datetime
from pathlib import Path

FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n\s*```", re.S)


def now() -> str:
    """The current local time as the run files record it."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def emit(payload: object, *, sort_keys: bool = False) -> None:
    """Print payload as the script's one JSON line."""
    line = json.dumps(payload, ensure_ascii=False, sort_keys=sort_keys)
    sys.stdout.write(f"{line}\n")


def fail(message: str) -> int:
    """Print {"error": message} and give the exit code of a failed script."""
    emit({"error": message})
    return 1


def pretty_json(payload: object, *, ensure_ascii: bool = False) -> str:
    """payload as a run file holds it: indented by two, with a trailing newline."""
    text = json.dumps(payload, ensure_ascii=ensure_ascii, indent=2)
    return f"{text}\n"


def write_json(path: Path, payload: object) -> None:
    """Write payload to path in the run-file layout of pretty_json()."""
    path.write_text(pretty_json(payload))


def read_object(path: Path) -> dict:
    """A JSON object file; {} when it is missing, unreadable or holds anything but an object."""
    try:
        parsed = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def extract_json(text: str) -> object:
    """The JSON answer in an agent's text: the whole text, a ```json fence, or the outermost {...}."""
    fenced = [fence.group(1) for fence in FENCE.finditer(text)]
    candidates = [text.strip(), *fenced]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        with suppress(ValueError):
            return json.loads(candidate)
    raise ValueError("no JSON object in the answer")
