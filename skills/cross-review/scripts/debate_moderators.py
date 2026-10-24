#!/usr/bin/env python3
"""Merge two independent moderators (Claude and Codex on third models) into moderator.json and extra.json.

Reads moderator-claude.json + extra-claude.json (written by the Claude moderator agent) and moderator-codex.json
(the Codex moderator's schema output: {"decisions": [...], "extra": [...]}). Rule: a dispute is decided when both
moderators agree on whether the finding stands (severity none vs. a real severity); the stricter severity wins and
both notes are kept. When they disagree, the entry is written with `"split": true`, verdict «модераторы разошлись:
…» and the stricter severity for reference, and the id is listed under `split`: debate_outcome.py keeps such an item
disputed at its pipeline severity, and the report says a human decides. A dispute only one moderator answered is
written with `"missing": true` and stays open the same way. A side finding goes to extra.json only when both
confirm it, at the stricter of their severities with both notes; its side comes from its id (codex-side-2) when
the moderator left out sources and vendors.
Prints one JSON line: decided, split, extra, missing. A moderator's answer that is not JSON or breaks its schema
prints {"error": ...} and exits 1.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import NamedTuple

sys.path.insert(0, str(Path(__file__).resolve().parent))

from crossreview import cli  # noqa: E402
from crossreview.debate_schemas import DECISIONS_SCHEMA, EXTRA_SCHEMA, MODERATE_SCHEMA, load_answer  # noqa: E402
from crossreview.jsonio import emit, fail, write_json  # noqa: E402
from crossreview.moderation import merge_decision, merge_extra  # noqa: E402

ID = "id"


class Answers(NamedTuple):
    """Both moderators' decisions and extras, by id."""

    claude: dict[str, dict]
    codex: dict[str, dict]
    extra_claude: dict[str, dict]
    extra_codex: dict[str, dict]

    def decisions(self) -> list[dict]:
        """Every dispute either moderator answered, merged, in id order."""
        disputed = sorted({*self.claude, *self.codex})
        return [
            merge_decision(dispute, self.claude.get(dispute), self.codex.get(dispute))
            for dispute in disputed
        ]

    def extras(self) -> list[dict]:
        """The side findings both moderators confirmed, merged, in id order."""
        confirmed = sorted(set(self.extra_claude) & set(self.extra_codex))
        return [
            merge_extra(side_id, self.extra_claude[side_id], self.extra_codex[side_id])
            for side_id in confirmed
        ]


def main(argv: list[str] | None = None) -> int:
    """Write moderator.json and extra.json and print the ids by outcome; a broken answer is {"error": ...}, exit 1."""
    root = cli.parser(__doc__)
    root.add_argument("debate_dir", help="the debate directory with both moderators' outputs")
    debate_dir = Path(root.parse_args(argv).debate_dir).resolve()
    try:
        answers = _answers(debate_dir)
    except ValueError as error:
        return fail(str(error))
    decisions = answers.decisions()
    extra = answers.extras()
    write_json(debate_dir / "moderator.json", decisions)
    write_json(debate_dir / "extra.json", extra)
    emit(_summary(answers, decisions, extra))
    return 0


def _answers(debate_dir: Path) -> Answers:
    claude = load_answer(debate_dir / "moderator-claude.json", DECISIONS_SCHEMA, [])
    codex = load_answer(debate_dir / "moderator-codex.json", MODERATE_SCHEMA, {})
    extra_claude = load_answer(debate_dir / "extra-claude.json", EXTRA_SCHEMA, [])
    return Answers(
        claude=_by_id(claude),
        codex=_by_id(codex.get("decisions", [])),
        extra_claude=_by_id(extra_claude),
        extra_codex=_by_id(codex.get("extra", [])),
    )


def _by_id(entries: list[dict]) -> dict[str, dict]:
    return {entry[ID]: entry for entry in entries}


def _summary(answers: Answers, decisions: list[dict], extra: list[dict]) -> dict:
    split = [decision[ID] for decision in decisions if decision.get("split")]
    missing = [decision[ID] for decision in decisions if decision.get("missing")]
    open_ids = {*split, *missing}
    return {
        "decided": [decision[ID] for decision in decisions if decision[ID] not in open_ids],
        "split": split,
        "missing": missing,
        "extra": [entry[ID] for entry in extra],
        "extra_rejected_by_one": sorted(set(answers.extra_claude) ^ set(answers.extra_codex)),
    }


if __name__ == "__main__":
    sys.exit(main())
