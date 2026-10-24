#!/usr/bin/env python3
"""Turn round-1 verdicts into round-2 inputs: each side gets the other side's objections to its own findings.

claude-r1.json holds Claude's verdicts on Codex findings  -> objections go to Codex  (for-codex-r2.json)
codex-r1.json  holds Codex's verdicts on Claude findings  -> objections go to Claude (for-claude-r2.json)
An objection is any verdict other than `real` at the finding's own severity. Findings both sides raised
were examined by both in round 1 and have no single author to answer, so they skip round 2.
Prints one JSON line with the counts; a missing r1 file yields an empty list for that side. An r1 answer that is
not JSON or breaks schema-examine.json prints {"error": ...} and exits 1.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from crossreview import cli  # noqa: E402
from crossreview.debate_schemas import EXAMINE_SCHEMA, load_answer  # noqa: E402
from crossreview.jsonio import emit, fail, write_json  # noqa: E402
from crossreview.keys import ID, SEVERITY, VENDORS  # noqa: E402
from crossreview.models import SIDES  # noqa: E402

PAIRS = (("codex", "claude"), ("claude", "codex"))  # (examiner, author)
VERDICTS = "verdicts"


def objections(cards: dict[str, dict], verdicts: list[dict]) -> list[dict]:
    """The verdicts the author has to answer, each paired with the finding it is about."""
    found = []
    for verdict in verdicts:
        card = cards.get(verdict[ID])
        if card is None or set(card[VENDORS]) == set(SIDES):
            continue
        if verdict["stance"] != "real" or verdict[SEVERITY] != card[SEVERITY]:
            found.append({"finding": card, "objection": verdict})
    return found


def main(argv: list[str] | None = None) -> int:
    """Write for-<author>-r2.json for both sides and print the counts; a broken input is {"error": ...}, exit 1."""
    root = cli.parser(__doc__)
    root.add_argument("debate_dir", help="the debate directory with items.json and the round-1 verdicts")
    debate_dir = Path(root.parse_args(argv).debate_dir).resolve()
    path = debate_dir / "items.json"
    try:
        cards = _read_cards(path)
    except ValueError as error:
        return fail(f"{path}: {error}; run debate_build.py first")
    try:
        counts = _round2(debate_dir, cards)
    except ValueError as error:
        return fail(str(error))
    emit(counts)
    return 0


def _read_cards(path: Path) -> dict[str, dict]:
    """items.json by id; ValueError with the reason when it is missing, not JSON or not a list of cards."""
    try:
        listed = json.loads(path.read_text())
    except OSError as error:
        raise ValueError(error.strerror or str(error)) from None
    except ValueError as error:
        raise ValueError(str(error)) from None
    try:
        return {card[ID]: card for card in listed}
    except (TypeError, KeyError) as error:
        raise ValueError(str(error)) from None


def _round2(debate_dir: Path, cards: dict[str, dict]) -> dict[str, object]:
    counts: dict[str, object] = {}
    for examiner, author in PAIRS:
        counts.update(_pair(debate_dir, cards, examiner, author))
    return counts


def _pair(debate_dir: Path, cards: dict, examiner: str, author: str) -> dict:
    """The examiner's objections to the author's findings, written for the author's round 2."""
    source = debate_dir / f"{examiner}-r1.json"
    verdicts = load_answer(source, EXAMINE_SCHEMA, {VERDICTS: []})[VERDICTS]
    found = objections(cards, verdicts)
    write_json(debate_dir / f"for-{author}-r2.json", found)
    return {f"for_{author}_r2": len(found), f"{examiner}_r1_present": source.exists()}


if __name__ == "__main__":
    sys.exit(main())
