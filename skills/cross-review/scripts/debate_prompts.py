#!/usr/bin/env python3
"""Write the prompts and output schemas for both sides of the debate, plus the moderator briefs, and print
the launch plan.

The texts are the prompts `examine` (round 1), `respond` (round 2) and `moderate` (the moderator), resolved
through both configuration layers: <repo>/.cross-review/prompts/<name>.md first, the plugin's assets/config/prompts/
second. The repository is the run's own for a cross-review run, the --workdir for an external run. The parts
that depend on the run are built here and substituted as values: the panel sentence (how many reviewers per
vendor, on which models, from the run's findings.json), the context list, the state of the working tree, the
status legend, and the input and output of each prompt. A placeholder left without a value is an error naming
the prompt and the placeholder.

--ref is the commit under review (the last sha of the range); the prompts then state whether the working tree
contains it and, when it does not, tell the sides to read the code through `git show <sha>:<path>`.

Writes to <debate_dir>, <run_dir>/debate/ by default:
  prompt-r1-claude.md, prompt-r1-codex.md     round 1, `examine`: what the other side's reviewers raised
  prompt-r2-claude.md, prompt-r2-codex.md     round 2, `respond`: the objections to the side's own findings
  prompt-r2-codex.fresh.md                    round 2 for a Codex session that cannot be resumed: the round-1
                                              prompt, a note that its verdicts are in codex-r1.json, then round 2
  prompt-moderator.md                         the session or a Claude agent moderates: moderator.json, extra.json
  prompt-moderator-claude.md                  an independent Claude moderator: moderator-claude.json, extra-claude.json
  prompt-moderator-codex.md                   an independent Codex moderator, answer held to schema-moderate.json
  schema-examine.json, schema-respond.json, schema-moderate.json
Paths in the prompts are absolute. Context defaults to the run's own input: scope.md, goal.md, context/*, and
the profile that calibrated the run (input/profile.md when non-empty, else prompts/input-profile.md of an external
run). --context adds more.

A Claude side runs as an Agent that writes its answer with the Write tool; round 2 continues the round-1 agent.
A Codex side runs through codex_run.sh: round 1 fresh, round 2 with --resume codex-r1.log, and on exit 3 (no
session id in the log) or without that log, fresh with prompt-r2-codex.fresh.md. The launch plan lists every
step with its prompt, schema, output, model and, for Codex, the codex_run.sh command. The models are the review's
own (`cross_config.py sides`): a cross-review run's manifest.json, or for an external run its findings.json with the
moderators of the configured tier. A cross-review run records the three prompts in its manifest.json.

Prints one JSON line. A run or configuration error prints {"error": ...} and exits 1.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from crossreview import cli  # noqa: E402
from crossreview.debate_plan import DebatePlan, Request  # noqa: E402
from crossreview.errors import ConfigError  # noqa: E402
from crossreview.jsonio import emit, fail  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Write the debate prompts and print the launch plan; a run or configuration error is {"error": ...}, exit 1."""
    root = _parser()
    request = _request(root, root.parse_args(argv))
    try:
        payload = DebatePlan(request).build()
    except ConfigError as error:
        return fail(str(error))
    emit(payload)
    return 0


def _parser() -> argparse.ArgumentParser:
    root = cli.parser(__doc__)
    root.add_argument("round_dir", help="the run directory with findings.json and input/")
    root.add_argument("debate_dir", nargs="?", default=None,
                      help="where the prompts and schemas go, created when missing; default: <round_dir>/debate")
    root.add_argument("--subject", required=True,
                      help="what is under review, one line, e.g. 'PR #765 `feature/PROJ-42` (range 88e879874..747aec1db)'")
    root.add_argument("--workdir", default=None,
                      help="repository root the sides run in; default: the run's repository for a cross-review run, "
                           "the current directory for an external run")
    root.add_argument("--context", action="append", default=[], metavar="PATH=WHY",
                      help="extra context file, absolute path and why to read it")
    root.add_argument("--ref", default=None,
                      help="the commit under review (last sha of the range); the prompts then say whether the working "
                           "tree contains it and how to read the code when it does not")
    return root


def _request(root: argparse.ArgumentParser, args: argparse.Namespace) -> Request:
    context = _extra_context(root, args.context)
    round_dir = Path(args.round_dir).resolve()
    return Request(
        round_dir=round_dir,
        debate_dir=Path(args.debate_dir).resolve() if args.debate_dir else round_dir / "debate",
        subject=args.subject,
        workdir=str(Path(args.workdir).resolve()) if args.workdir else None,
        context=context,
        ref=args.ref,
    )


def _extra_context(root: argparse.ArgumentParser, entries: list[str]) -> list[tuple[str, str]]:
    """--context PATH=WHY entries as (absolute path, why); a malformed one is a usage error, exit 2."""
    extra = []
    for entry in entries:
        if "=" not in entry:
            root.error(f"--context wants PATH=WHY, got {entry!r}")
        path, why = entry.split("=", 1)
        resolved = str(Path(path).resolve())
        extra.append((resolved, why.strip()))
    return extra


if __name__ == "__main__":
    sys.exit(main())
