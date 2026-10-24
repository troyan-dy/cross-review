#!/usr/bin/env python3
"""Render final.json as the one-page report (an HTML fragment for the Artifact tool).

Needs final.json from debate_outcome.py. Every item card carries a «было → стало» badge when the debate changed its
severity, and a «Переоценка приоритетов» block counts what was raised, lowered and dropped. The footer names who
moderated by the files in the debate directory: the session, or — when moderator-claude.json or moderator-codex.json
is there — the independent pair of debate_moderators.py, with the models of the run's manifest.json when it has
them; --moderator overrides the line. Prints one JSON line with the counts per outcome and severity and the
reprioritized counts; without final.json, or with one that is not JSON or not in the shape debate_outcome.py writes,
prints {"error": ...} and exits 1 before writing the page.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from crossreview import cli  # noqa: E402
from crossreview.jsonio import emit, fail  # noqa: E402
from crossreview.outcome_words import SHIFTS  # noqa: E402
from crossreview.report_page import eyebrow, moderator_line, page_texts, write_page  # noqa: E402
from crossreview.report_schema import CARD_LISTS, final_errors  # noqa: E402
from crossreview.schema import summary  # noqa: E402

CARDS = "items"


def main(argv: list[str] | None = None) -> int:
    """Write the page and print the counts; without a valid final.json print {"error": ...} and exit 1."""
    args = _parser().parse_args(argv)
    debate_dir = Path(args.debate_dir).resolve()
    final_file = debate_dir / "final.json"
    if not final_file.is_file():
        return fail(f"{debate_dir}: no final.json, run debate_outcome.py first")
    try:
        final = _read_final(final_file)
    except ValueError as error:
        return fail(f"{final_file}: not the output of debate_outcome.py ({error}), run it again")
    _prepare(final, args, debate_dir)
    out = Path(args.out)
    write_page(out, args.title, final)
    emit(_counts(final, out), sort_keys=True)
    return 0


def area_of(path: str) -> str:
    """The first two directories of a path, the area a card is grouped under."""
    parts = [part for part in (path or "").split("/") if part]
    if len(parts) <= 1:
        return parts[0] if parts else "—"
    return "/".join(parts[:2])


def _parser() -> argparse.ArgumentParser:
    root = cli.parser(__doc__)
    root.add_argument("debate_dir", help="the debate directory with final.json")
    root.add_argument("out", help="the HTML file to write")
    root.add_argument("--title", required=True, help="page title")
    root.add_argument("--subject", required=True, help="what was reviewed, as the lede names it")
    root.add_argument("--strip-prefix", default="", help="path prefix to hide in file names")
    root.add_argument("--eyebrow", default=None, help="the line above the title, e.g. '<repo> · <branch> · <date>'")
    root.add_argument("--moderator", default=None,
                      help="who moderated the disputes, as the footer names it; by default the session, or the "
                           "independent moderators whose moderator-<vendor>.json lie in the debate directory, with "
                           "the manifest's models")
    return root


def _read_final(path: Path) -> dict:
    final = json.loads(path.read_text())
    errors = final_errors(final)
    if errors:
        raise ValueError(summary(errors))
    return final


def _prepare(final: dict, args: argparse.Namespace, debate_dir: Path) -> None:
    """The page data: each card's area and the texts of the meta."""
    for collection in CARD_LISTS:
        for card in final.get(collection) or []:
            card["area"] = area_of(card.get("file") or "")
    meta = final["meta"]
    # the debate directory sits in its run: <run_dir>/debate/ for a cross-review run and an external run alike
    moderator = args.moderator or moderator_line(debate_dir, debate_dir.parent)
    meta.update({
        "title": args.title,
        "eyebrow": args.eyebrow or eyebrow(meta),
        "strip_prefix": args.strip_prefix,
        **page_texts(meta, args.subject, moderator),
    })


def _counts(final: dict, out: Path) -> dict:
    cards = final[CARDS]
    counts = Counter(f"{card['outcome']}:{card['final_severity']}" for card in cards)
    shifts = [card.get("severity_shift") or {} for card in cards]
    directions = [shift.get("direction") for shift in shifts]
    return {
        "out": str(out.resolve()),
        "counts": dict(counts),
        "reprioritized": {direction: directions.count(direction) for direction in SHIFTS},
        "questions": len(final.get("questions") or []),
    }


if __name__ == "__main__":
    sys.exit(main())
