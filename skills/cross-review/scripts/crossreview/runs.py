"""The layout of a review run: <repo>/.cross-review/runs/<task>/<run>/ with input/, agents/, stages/ and prompts/."""

from __future__ import annotations

import argparse
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple

from crossreview.errors import ConfigError
from crossreview.links import REPO_DIR

RUNS = Path(REPO_DIR) / "runs"
TASK_FILE = "task.md"
NONE = "none provided"
OUTPUT_CONTRACT = "Return ONLY a JSON object matching the schema below. No prose before or after it.\n\nSchema:\n"
UNSAFE_PARTS = ("/", "\\", "..")
EXTRA_DIGITS = re.compile(r"(\.\d{6})\d+")
RUN_DEPTH = 4
# the run's grandparent and great-grandparent: <repo>/.cross-review/runs/<task>/<run>
RUN_LAYOUT = (RUNS.name, REPO_DIR)


class RunPlace(NamedTuple):
    """Where a run sits: <repo>/.cross-review/runs/<task>/<run>."""

    repo: Path
    task: str
    run: str


def check_name(name: str) -> str:
    """A task or run name becomes one path component: no separators, no leading dot, no parent reference."""
    unsafe = any(part in name for part in UNSAFE_PARTS)
    if not name or unsafe or name.startswith(".") or name == TASK_FILE:
        raise argparse.ArgumentTypeError(f"{name!r} is not a valid name: one path component, no leading dot")
    return name


def locate(run_dir: Path) -> RunPlace:
    """The repository, task and run of <repo>/.cross-review/runs/<task>/<run>."""
    if not (run_dir / "input").is_dir():
        raise ConfigError(f"{run_dir}: no input/ here, create the run with `review_run.py new` first")
    # a list of names: Python 3.9 cannot slice run_dir.parents
    names = [parent.name for parent in run_dir.parents]
    layout = tuple(names[1:3])
    if len(names) < RUN_DEPTH or layout != RUN_LAYOUT:
        raise ConfigError(f"{run_dir}: not a run directory, expected <repo>/{RUNS.as_posix()}/<task>/<run>")
    return RunPlace(run_dir.parents[3], run_dir.parent.name, run_dir.name)


def input_paths(run_dir: Path) -> dict:
    """What the session writes for a run: scope.md, goal.md, profile.md and context/ under input/."""
    inputs = run_dir / "input"
    return {
        "scope": str(inputs / "scope.md"),
        "goal": str(inputs / "goal.md"),
        "profile": str(inputs / "profile.md"),
        "context": str(inputs / "context"),
    }


def filled(path: Path) -> bool:
    """A file with text in it, or a directory with at least one file below it."""
    if path.is_dir():
        return any(member.is_file() for member in path.rglob("*"))
    return path.is_file() and bool(path.read_text().strip())






def input_or_none(path: str) -> str:
    """An input path as a prompt names it, or `none provided` when the session left it empty."""
    return path if filled(Path(path)) else NONE


def parse_time(stamp: object) -> datetime | None:
    """An RFC 3339 timestamp as this skill or an external run writes it; Python 3.9 takes neither `Z` nor
    nanoseconds."""
    if not isinstance(stamp, str) or not stamp:
        return None
    offset = stamp.replace("Z", "+00:00")
    text = EXTRA_DIGITS.sub(r"\1", offset)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
