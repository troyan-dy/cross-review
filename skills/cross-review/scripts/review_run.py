#!/usr/bin/env python3
"""Start a review run and compose the prompts of its finder agents.

  new <repo> --task T [--run R]                         create the run directory and print its input paths
  compose <run_dir> [--tier T | --claude M --codex M]   write the agents' prompts and manifest.json

A run lives in <repo>/.cross-review/runs/<task>/<run>/, in the same run format as an external run, so the debate
scripts read both. `new` creates the run with an empty input/ and the task's task.md, and writes
.cross-review/runs/.gitignore with `*` — the only write outside the run: a nested .gitignore outranks the
repository's own, so even a whitelist .gitignore (`*`, `!*.*`, `!*/`) cannot bring the runs back, and it works the
same in a worktree and a subdirectory. A symlink on the way to the run or task.md is an error, wherever it points, and so is one in a
prior run of the task — the prompts point the finders at those directories. The run name defaults to NN-initial
for the first run of a task and NN-after-fix after it.

`compose` reads input/scope.md (required), goal.md, profile.md and context/, which the session writes. For
every roster agent it writes prompts/agents/<agent>.md: the `review` prompt with paths in place of
{{SCOPE}} {{GOAL}} {{PROFILE}} {{CONTEXT}} {{WORKDIR}} (`none provided` when empty), a `## Lens: <name>`
section per lens, the task's prior runs with the instruction to re-evaluate them independently, then the
finder schema. It writes manifest.json and prints the launch plan: one entry per agent with its vendor,
model, prompt and output file. A run that already has answers is not composed again.

Prints one JSON line. A run or configuration error prints {"error": ...} and exits 1.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from crossreview import cli  # noqa: E402
from crossreview.compose import compose  # noqa: E402
from crossreview.errors import ConfigError  # noqa: E402
from crossreview.jsonio import emit, fail  # noqa: E402
from crossreview.run_new import new  # noqa: E402
from crossreview.runs import check_name  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Run `new` or `compose` and print its JSON line; a run or configuration error is {"error": ...} and exit 1."""
    root, compose_command = _parser()
    args = root.parse_args(argv)
    if args.command == "compose":
        cli.check_model_pair(compose_command, args)
    try:
        payload = _run(args)
    except ConfigError as error:
        return fail(str(error))
    emit(payload)
    return 0


def _parser() -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    root = cli.parser(__doc__)
    commands = root.add_subparsers(dest="command", metavar="{new,compose}", required=True)
    new_command = commands.add_parser("new", help="create a run directory")
    new_command.add_argument("repo", help="root of the reviewed repository")
    new_command.add_argument("--task", required=True, type=check_name, help="task name, e.g. proj-123 or the branch")
    new_command.add_argument("--run", type=check_name, help="run name; default NN-initial, then NN-after-fix")
    compose_command = commands.add_parser("compose", help="write the finder prompts and manifest.json of a run")
    compose_command.add_argument("run_dir", help="the run directory `new` created")
    cli.add_model_options(compose_command)
    return root, compose_command


def _run(args: argparse.Namespace) -> dict:
    if args.command == "new":
        return new(Path(args.repo).resolve(), args.task, args.run)
    run_dir = Path(args.run_dir).resolve()
    return compose(run_dir, args.tier, args.claude, args.codex)


if __name__ == "__main__":
    sys.exit(main())
