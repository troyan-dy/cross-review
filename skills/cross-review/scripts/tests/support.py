"""Shared helpers for the cross-review tests: paths, running a script as a CLI, fixture copies."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import unittest
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
    identity = ("-c", "user.name=test", "-c", "user.email=test@example.com")
    argv = ["git", "-C", str(repo), *identity, *args]
    return subprocess.run(argv, capture_output=True, text=True, check=True).stdout


def git_init(path: Path, commit: bool = False) -> Path:
    """A fresh repository at path, with one empty commit when commit is set."""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "master")
    if commit:
        git(path, "commit", "-q", "--allow-empty", "-m", "init")
    return path


def run(script: str, *args: object, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """Run scripts/<script>.py with the current interpreter and capture its output."""
    script_path = SCRIPTS / f"{script}.py"
    argv = [sys.executable, "-B", str(script_path), *map(str, args)]
    return subprocess.run(argv, capture_output=True, text=True, cwd=cwd)


def copy_fixture(name: str, dest: Path) -> Path:
    """Copy tests/fixtures/<name> to dest so a test can change it."""
    shutil.copytree(FIXTURES / name, dest)
    return dest


def read_json(path: Path):
    return json.loads(path.read_text())


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    path.write_text(f"{text}\n")


def assert_fields(case: unittest.TestCase, mapping: dict, **fields: object) -> None:
    """mapping holds these fields with these values; its other keys are not checked."""
    case.assertEqual({key: mapping[key] for key in fields}, fields)


def pick(mapping: dict, *keys: str) -> tuple:
    """The values of mapping under keys, in that order, for one assertEqual."""
    return tuple(mapping[key] for key in keys)


def single(entries: list) -> object:
    """The one entry of a list; a list of any other length fails the test."""
    if len(entries) != 1:
        raise AssertionError(f"expected one entry, got {len(entries)}: {entries!r}")
    return entries[0]


def last_json_line(stdout: str) -> dict:
    """The JSON line a script prints last."""
    return json.loads(stdout.strip().splitlines()[-1])


def composed_run(root: Path, config: dict | None = None) -> Path:
    """A composed run of the task demo in a fresh repository under root; config, when given, becomes the
    repository's .cross-review/cross-review.json."""
    repo = git_init(root / "repo")
    if config is not None:
        write_json(repo / ".cross-review" / "cross-review.json", config)
    created = last_json_line(run("review_run", "new", repo, "--task", "demo").stdout)
    run_dir = Path(created["run_dir"])
    (run_dir / "input" / "scope.md").write_text("`git diff master`\n")
    res = run("review_run", "compose", run_dir)
    if res.returncode != 0:
        raise AssertionError(res.stdout + res.stderr)
    return run_dir
