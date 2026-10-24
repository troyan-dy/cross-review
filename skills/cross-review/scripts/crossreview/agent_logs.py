"""What an agent left in agents/: its answer, its exit code and the log of its run."""

from __future__ import annotations

from pathlib import Path

from crossreview.jsonio import extract_json

LIMIT_MARKERS = ("usage limit", "rate limit", "quota", "429", "too many requests")
LIMIT_TAIL = 2048
HEADER_LINES = 40


def read_answer_list(path: Path, key: str, stage: str) -> tuple[list | None, str]:
    """The `key` array of an agent's answer, or None and why the agent is degraded."""
    if not path.is_file():
        return None, f"no answer: {path.name} is missing"
    try:
        answer = extract_json(path.read_text(errors="replace"))
    except ValueError as error:
        return None, f"answer is not JSON: {error}"
    listed = answer.get(key) if isinstance(answer, dict) else None
    if not isinstance(listed, list):
        return None, f"answer does not match the {stage} schema: expected an object with a {key} array"
    return listed, ""


def exit_code(path: Path) -> int | None:
    """The code in agents/<agent>.exit; None when there is none."""
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def header_model(path: Path) -> str:
    """The model Codex resolved, from the `model:` line of its log header."""
    try:
        lines = path.read_text(errors="replace").splitlines()[:HEADER_LINES]
    except OSError:
        return ""
    for line in lines:
        key, _, rest = line.partition(":")
        if key.strip().lower() == "model" and rest.strip():
            return rest.strip()
    return ""


def limit_hint(log: Path) -> str:
    """A note for a degraded agent whose log tail names a usage limit; empty otherwise."""
    try:
        tail = log.read_text(errors="replace")[-LIMIT_TAIL:].lower()
    except OSError:
        return ""
    limit = next((marker for marker in LIMIT_MARKERS if marker in tail), None)
    return f"; the log tail mentions {limit!r}, a usage limit is likely" if limit else ""
