#!/usr/bin/env python3
"""Split a finished review run into cross-examination inputs for the two sides.

Reads the run's stages/1-found.json, stages/2-synthesized.json, stages/3-verified.json (when verify
ran) and findings.json. Writes to <debate_dir>, <run_dir>/debate/ by default:
  items.json        every finding that reached any stage, with `vendors` and pipeline `status`
  consensus.json    found by both sides and kept by the pipeline — not debated
  for-claude.json   what Claude examines: findings only Codex raised (+ open questions Codex raised)
  for-codex.json    what Codex examines: findings only Claude raised (+ open questions Claude raised)
Prints one JSON line with the counts. Exit 1 when one side has no agent in the roster.
An external run (one this skill did not compose) has the same layout and is accepted as is.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from crossreview import cli  # noqa: E402
from crossreview.debate_items import Itemizer, RoundFiles, load_round  # noqa: E402
from crossreview.debate_routes import asked, route  # noqa: E402
from crossreview.jsonio import emit, fail, write_json  # noqa: E402
from crossreview.models import CLAUDE, CODEX, SIDES  # noqa: E402

FINDINGS = "findings"
OPEN_QUESTIONS = "open_questions"


def main(argv: list[str] | None = None) -> int:
    """Write the debate inputs and print the counts; a run that is not finished is {"error": ...} and exit 1."""
    args = _parser().parse_args(argv)
    round_dir = Path(args.round_dir).resolve()
    try:
        files = load_round(round_dir)
    except ValueError as error:
        return fail(f"{error}; not a finished run: check round_dir, or run review_verify.py apply first")
    itemizer = Itemizer(files.agents())
    problem = _roster_problem(itemizer.roster())
    if problem:
        emit(problem)
        return 1
    emit(_build(files, itemizer, round_dir, args.debate_dir))
    return 0


def _parser() -> argparse.ArgumentParser:
    root = cli.parser(__doc__)
    root.add_argument("round_dir", help="the run directory: stages/, findings.json, events.jsonl")
    root.add_argument("debate_dir", nargs="?", default=None,
                      help="where the debate files go, created when missing; default: <round_dir>/debate")
    return root


def _roster_problem(roster: dict[str, list[str]]) -> dict | None:
    missing = [side for side in SIDES if not roster[side]]
    if not missing:
        return None
    sides = " and ".join(missing)
    return {"error": f"no {sides} agent in the roster; cross-examination needs both sides", "roster": roster}


def _build(files: RoundFiles, itemizer: Itemizer, round_dir: Path, debate_arg: str | None) -> dict:
    debate_dir = Path(debate_arg).resolve() if debate_arg else round_dir / "debate"
    cards, from_events = itemizer.cards(files, round_dir)
    questions = [itemizer.slim(question, "open_question") for question in files.open_questions()]
    routes = route(list(cards.values()))
    debate_dir.mkdir(parents=True, exist_ok=True)
    write_json(debate_dir / "items.json", list(cards.values()))
    write_json(debate_dir / "consensus.json", routes.consensus)
    _write_side(debate_dir / "for-codex.json", routes.to_codex, asked(questions, CLAUDE))
    _write_side(debate_dir / "for-claude.json", routes.to_claude, asked(questions, CODEX))
    return {
        "items": len(cards),
        "by_status": dict(Counter(card["status"] for card in cards.values())),
        "consensus": len(routes.consensus),
        "for_codex": len(routes.to_codex),
        "for_claude": len(routes.to_claude),
        OPEN_QUESTIONS: len(questions),
        "roster": itemizer.roster(),
        "verify_ran": files.verify_ran,
        "dropped_from_events": from_events,
        "debate_dir": str(debate_dir),
    }


def _write_side(path: Path, cards: list[dict], questions: list[dict]) -> None:
    write_json(path, {FINDINGS: cards, OPEN_QUESTIONS: questions})


if __name__ == "__main__":
    sys.exit(main())
