#!/usr/bin/env python3
"""The verify stage of a review run: judge every synthesized finding against the code, then write the run's
findings.json and report.md.

  prepare <run_dir>    write prompts/stages/verify-<group>.md and print the launch plan
  apply <run_dir>      route the verdicts and write stages/3-verified.json, findings.json and report.md

`prepare` reads stages/2-synthesized.json and groups its findings for the verifiers. By default the key is
the directory of a finding's file, and the directories holding one finding are merged into one group; with
`stages.verify_group_by: source` the key is the first agent that raised it, and nothing is merged. Past
`stages.verify_groups` groups the smallest are merged into one. Each group gets the `verify` prompt with
{{SCOPE}} {{PROFILE}} {{WORKDIR}} as paths and {{FINDINGS}} as its own findings, then the verify schema. A
group runs as a Claude agent on the run's stage model, reading the code and writing nothing, and its answer
goes to agents/verify-<group>.json.

With `stages.verify: false` in the run's manifest, `prepare` marks every finding `unverified` and writes
findings.json and report.md straight away, without 3-verified.json: the stage is off, not empty. With no
finding to verify it writes all three: the stage ran and had nothing to check.

`apply` groups the findings again and reads each group's answer: the JSON object of the verify schema,
optionally wrapped in prose or a ```json fence.
  - confirmed and refined stay in findings; a refined verdict's non-empty corrections replace the fields;
  - rejected leaves the report, and a `rejected` event keeps the finding with the verifier's reason;
  - immaterial and pre_existing move to their own lists;
  - a finding with no verdict, or with a verdict outside the enum, stays as `unverified`;
  - a group without a readable answer leaves all its findings unverified, with an `agent_degraded` event.
Open questions are never verified. Applying again replaces the events of the earlier apply. report.md is in
Russian, like everything else the user reads.

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
from crossreview.verify_apply import apply  # noqa: E402
from crossreview.verify_prepare import prepare  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Run `prepare` or `apply` and print its JSON line; an error is {"error": ...} and exit 1."""
    args = _parser().parse_args(argv)
    run_dir = Path(args.run_dir).resolve()
    command = prepare if args.command == "prepare" else apply
    try:
        payload = command(run_dir)
    except ConfigError as error:
        return fail(str(error))
    emit(payload)
    return 0


def _parser() -> argparse.ArgumentParser:
    root = cli.parser(__doc__)
    commands = root.add_subparsers(dest="command", metavar="{prepare,apply}", required=True)
    commands.add_parser("prepare", help="write the verify prompts and print the launch plan").add_argument(
        "run_dir", help="the run directory, after review_synth.py")
    commands.add_parser(
        "apply", help="apply the verdicts and write 3-verified.json, findings.json and report.md",
    ).add_argument("run_dir", help="the run directory, after the verifiers answered")
    return root


if __name__ == "__main__":
    sys.exit(main())
