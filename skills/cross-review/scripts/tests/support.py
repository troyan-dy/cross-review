"""Shared helpers for the cross-review tests: paths, running a script as a CLI, fixture copies."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SCRIPTS = TESTS.parent
SKILL = SCRIPTS.parent
FIXTURES = TESTS / "fixtures"

# scripts import as plain modules, the way they import each other
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# git in the tests and in the scripts they run sees no global or system config: a user's excludesFile
# would already ignore what a test expects the scripts to exclude
os.environ["GIT_CONFIG_GLOBAL"] = os.devnull
os.environ["GIT_CONFIG_NOSYSTEM"] = "1"


def git(repo: Path, *args: str) -> str:
    """Run git in repo with a throwaway identity and return its stdout; a failure raises."""
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=test", "-c", "user.email=test@example.com", *args],
                          capture_output=True, text=True, check=True).stdout


def git_init(path: Path, commit: bool = False) -> Path:
    """A fresh repository at path, with one empty commit when commit is set."""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "master")
    if commit:
        git(path, "commit", "-q", "--allow-empty", "-m", "init")
    return path


def run(script: str, *args: object, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run scripts/<script>.py with the current interpreter and capture its output."""
    return subprocess.run([sys.executable, "-B", str(SCRIPTS / f"{script}.py"), *map(str, args)],
                          capture_output=True, text=True, cwd=cwd)


def copy_fixture(name: str, dest: Path) -> Path:
    """Copy tests/fixtures/<name> to dest so a test can change it."""
    shutil.copytree(FIXTURES / name, dest)
    return dest


def read_json(path: Path):
    return json.loads(path.read_text())


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def last_json_line(stdout: str) -> dict:
    """The JSON line a script prints last."""
    return json.loads(stdout.strip().splitlines()[-1])


def composed_run(root: Path, config: dict | None = None) -> Path:
    """A composed run of the task demo in a fresh repository under root; config, when given, becomes the
    repository's .cross-review/cross-review.json."""
    repo = git_init(root / "repo")
    if config is not None:
        write_json(repo / ".cross-review" / "cross-review.json", config)
    run_dir = Path(last_json_line(run("review_run", "new", repo, "--task", "demo").stdout)["run_dir"])
    (run_dir / "input" / "scope.md").write_text("`git diff master`\n")
    res = run("review_run", "compose", run_dir)
    if res.returncode != 0:
        raise AssertionError(res.stdout + res.stderr)
    return run_dir
