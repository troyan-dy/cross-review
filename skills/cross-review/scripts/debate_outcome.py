#!/usr/bin/env python3
"""Fold the run and both debate rounds into one outcome per finding.

Reads items.json, for-*.json, claude-r1/codex-r1, claude-r2/codex-r2, moderator.json, extra.json (the last
four may be absent; without items.json or for-*.json, or with one not in the shape debate_build.py writes, prints
{"error": ...} and exits 1). An extra.json entry must carry the id of a round-2 side finding; the moderator only
confirms it and sets its severity — file, line, title, body and fix come from the side's round-2 answer. Writes to <debate_dir>, <run_dir>/debate/ by default:
  final.json      every item with `outcome`, `final_severity` and its `thread`, plus open questions and meta
  disputes.json   what the moderator still has to decide: disputed items with their threads, and side findings
                  nobody verified yet — the input prompt-moderator.md names
Prints one JSON line (counts, disputed ids, pending and rejected side findings); --markdown prints the Russian
summary instead. Once extra.json exists the moderator has seen every side finding: those it left out are rejected,
not pending. A side's or a moderator's answer that is not JSON or breaks its schema prints {"error": ...} and exits 1.

Outcomes: consensus (both sides raised it, the pipeline kept it) · confirmed (the other side agreed) · refined
(severity or claim changed and both sides accept it) · restored (the pipeline had set it aside, the other side says
it is real) · dropped (the author conceded, or both agree on no severity) · disputed (still open, decided by the
moderator, or left open by two independent moderators that split or where one did not answer — then a human
decides) · side (found during the debate,
verified by the moderator) · question (an open question with the sides' answers) · unexamined (no verdict arrived
for it — a side did not finish; an item both sides were asked to examine needs both verdicts).

To fix (`actionable`) is what has a severity and is settled: neither unexamined nor a dispute the moderator has not
decided — those wait for a side, the moderator or a human — nor a defect that predates the change (`pre_existing`),
which keeps its outcome but is listed apart, outside the counts.

The author's answer settles an objection only when it lands on the examiner's severity: `concede` takes it, `amend`
at the examiner's severity is `refined`. An `amend` at any other severity is no agreement — the item is `disputed`
and goes to the moderator with both positions in its thread.

Every item carries `severity_shift` {from, to, direction, by}: `from` is the severity after synthesis and verify —
`none` for an item the pipeline set aside, which stays at `none` unless the debate restores it — `to` the final
one; `direction` is raised, lowered, same, dropped (to `none`) or new (a side finding); `by` names who set
the final severity — side (the examiner's verdict), author (the author's amend), moderator, or none when it did
not change. The JSON line lists the ids under `reprioritized` {raised, lowered, dropped}; the markdown has a
«Переоценка приоритетов» section with «было → стало» and who changed it.

An item whose `vendors` names something other than claude or codex — a source outside the roster that
debate_build.py passed through — was sent to both sides, like one both found and the pipeline set aside: it needs
both verdicts. The run's findings.json feeds `meta` (the agents, the counts, the stage models) and is read by
shape: a field of the wrong type is an {"error": ...}, a missing one is left empty.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from crossreview import cli  # noqa: E402
from crossreview.jsonio import emit, fail  # noqa: E402
from crossreview.outcome_fold import Fold  # noqa: E402
from crossreview.outcome_markdown import markdown  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Fold the debate and print the JSON line or, with --markdown, the Russian summary."""
    args = _parser().parse_args(argv)
    round_dir = Path(args.round_dir).resolve()
    debate_dir = Path(args.debate_dir).resolve() if args.debate_dir else round_dir / "debate"
    try:
        return _fold(round_dir, debate_dir, as_markdown=args.markdown)
    except ValueError as error:
        return fail(str(error))


def _parser() -> argparse.ArgumentParser:
    root = cli.parser(__doc__)
    root.add_argument("round_dir", help="the run directory with findings.json")
    root.add_argument("debate_dir", nargs="?", default=None,
                      help="the debate directory with items.json and the sides' answers; default: <round_dir>/debate")
    root.add_argument("--markdown", action="store_true", help="print the Russian summary instead of the JSON line")
    return root


def _fold(round_dir: Path, debate_dir: Path, *, as_markdown: bool) -> int:
    fold = Fold(round_dir, debate_dir)
    fold.write()
    if as_markdown:
        sys.stdout.write(f"{markdown(fold)}\n")
    else:
        emit(fold.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
