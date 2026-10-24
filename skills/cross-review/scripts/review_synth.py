#!/usr/bin/env python3
"""The synthesis stage of a review run: merge every source's findings into one set.

  prepare <run_dir>            write prompts/stages/synthesis.md and print the launch plan
  apply <run_dir> [answer]     apply the rules over the model's answer and write stages/2-synthesized.json

`prepare` reads stages/1-found.json and renders the `synthesis` prompt: {{SOURCES}} is the roster as it ran,
degraded agents included, {{FINDINGS}} is every finding as JSON, and the synthesis schema follows. The stage
runs as a Claude agent on the run's stage model, without access to the code, and its answer goes to
agents/synthesis.json. With `stages.synthesis: false` in the run's manifest, or with no finding to merge,
`prepare` copies 1-found.json to 2-synthesized.json and prints `skipped`: the run goes straight to verify.

`apply` reads the answer — the JSON object of the synthesis schema, optionally wrapped in prose or a ```json
fence — and takes from the model only what needs judgement: which inputs are one problem (`merged_ids`),
which list each goes to, and the wording and anchor. The rest are the synthesis rules, applied here:
  - the id is the first merged id, `merged_ids` keeps them all, sources and lenses are their union;
  - a finding's confidence is min(99, highest input confidence + 10 * (sources - 1)); an open question or a
    pre-existing issue keeps the highest input confidence, never boosted;
  - severity is the highest input severity;
  - a finding with one source, confidence below 80, neither critical nor major is dropped unless the round is
    degraded; an input the answer left out is dropped when that rule drops it and restored as its own
    finding otherwise.
What was dropped and restored goes to events.jsonl as `dropped` and `restored` events carrying the findings;
applying again replaces them. An id the input does not hold, an output with no ids and an input claimed by
two outputs are errors.

Prints one JSON line. A run, configuration or answer error prints {"error": ...} and exits 1.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from crossreview import cli  # noqa: E402
from crossreview.errors import ConfigError  # noqa: E402
from crossreview.jsonio import emit, fail  # noqa: E402
from crossreview.synth_apply import apply  # noqa: E402
from crossreview.synth_prepare import prepare  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Run `prepare` or `apply` and print its JSON line; an error is {"error": ...} and exit 1."""
    args = _parser().parse_args(argv)
    try:
        payload = _run(args)
    except ConfigError as error:
        return fail(str(error))
    emit(payload)
    return 0


def _parser() -> argparse.ArgumentParser:
    root = cli.parser(__doc__)
    commands = root.add_subparsers(dest="command", metavar="{prepare,apply}", required=True)
    commands.add_parser("prepare", help="write the synthesis prompt and print the launch plan").add_argument(
        "run_dir", help="the run directory, after review_collect.py")
    apply_command = commands.add_parser("apply", help="apply the synthesis answer and write stages/2-synthesized.json")
    apply_command.add_argument("run_dir", help="the run directory, after prepare")
    apply_command.add_argument("answer", nargs="?",
                               help="the synthesis agent's answer; default: <run_dir>/agents/synthesis.json")
    return root


def _run(args: argparse.Namespace) -> dict:
    run_dir = Path(args.run_dir).resolve()
    if args.command == "prepare":
        return prepare(run_dir)
    answer = Path(args.answer).resolve() if args.answer else None
    return apply(run_dir, answer)


if __name__ == "__main__":
    sys.exit(main())
