"""`review_run.py new`: the run directory with an empty input/, the task's task.md and the runs' .gitignore."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from crossreview.errors import ConfigError
from crossreview.links import confined
from crossreview.prior import prior_runs
from crossreview.runs import RUNS, TASK_FILE, input_paths

IGNORE_FILE = ".gitignore"
IGNORE_ALL = "*"
RUN_NUMBER = re.compile(r"(\d+)-")
TASK_TEMPLATE = """---
# every key is optional; the scripts only report what is here and never resolve a branch or a ref.
# Uncomment what applies.
#
# description: one line naming what this task reviews
# url: https://github.com/owner/repo/pull/123
# branch: feature/TICKET-123
# base: master
---

<!-- what this task covers, in prose -->
"""


def new(repo: Path, task: str, run: str | None) -> dict:
    """Create <repo>/.cross-review/runs/<task>/<run>/ and say where its input goes."""
    if not repo.is_dir():
        raise ConfigError(f"{repo}: no such directory")
    task_dir = confined(repo, repo / RUNS / task)
    previous = prior_runs(task_dir)
    run = run or _next_run_name(task_dir, previous)
    run_dir = confined(repo, task_dir / run)
    if run_dir.exists():
        raise ConfigError(f"{run_dir} already exists, give another --run")
    task_file = confined(repo, task_dir / TASK_FILE)
    (run_dir / "input" / "context").mkdir(parents=True)
    if not task_file.exists():
        task_file.write_text(TASK_TEMPLATE)
    return {
        "task": task,
        "run": run,
        "task_dir": str(task_dir),
        "run_dir": str(run_dir),
        "task_file": str(task_file),
        "input": input_paths(run_dir),
        "previous": previous,
        "ignore": ensure_ignored(repo, RUNS / task / TASK_FILE),
    }


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """Run git in repo, its output captured."""
    argv = ["git", "-C", str(repo), *args]
    return subprocess.run(argv, capture_output=True, text=True)


def ensure_ignored(repo: Path, probe: Path) -> dict | None:
    """Write .cross-review/runs/.gitignore with `*` unless it is there, then ask git whether `probe` — a file of
    the run, relative to the repository — is ignored; None outside a git repository.

    The file ignores itself too, so nothing under runs/ ever shows in `git status`. `ignored: false` means git
    still sees the run, which no .gitignore pattern can cause: the session warns before any agent starts.
    """
    if git(repo, "rev-parse", "--git-dir").returncode != 0:
        return None
    path = confined(repo, repo / RUNS / IGNORE_FILE)
    patterns = path.read_text().split() if path.is_file() else None
    added = patterns != [IGNORE_ALL]
    if added:
        path.write_text(f"{IGNORE_ALL}\n")
    ignored = git(repo, "check-ignore", "-q", probe.as_posix()).returncode == 0
    return {"path": str(path), "added": added, "ignored": ignored}


def _next_run_name(task_dir: Path, previous: list[dict]) -> str:
    """NN-initial for the first run of a task and NN-after-fix after it."""
    folders = task_dir.iterdir() if task_dir.is_dir() else []
    numbers = [RUN_NUMBER.match(folder.name) for folder in folders if folder.is_dir()]
    taken = [int(number.group(1)) for number in numbers if number]
    kind = "after-fix" if previous else "initial"
    number = str(max(taken, default=0) + 1)
    return f"{number.zfill(2)}-{kind}"
