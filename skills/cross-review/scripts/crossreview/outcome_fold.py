"""The fold of debate_outcome.py: the run and both debate rounds folded into one outcome per card."""

from __future__ import annotations

from pathlib import Path

from crossreview.jsonio import write_json
from crossreview.keys import ID
from crossreview.outcome_cards import card_order, question_card, side_card
from crossreview.outcome_files import load_findings
from crossreview.outcome_inputs import ASKED, QUESTION, DebateInputs, read_inputs
from crossreview.outcome_judge import Judgement
from crossreview.outcome_meta import run_meta
from crossreview.outcome_report import actionable, outcome_counts, predating, reprioritized
from crossreview.severities import SEVERITIES

FINAL = "final.json"
DISPUTES = "disputes.json"


class Fold:
    """Every card with its outcome, the open questions, the side findings still pending and the run's meta."""

    def __init__(self, round_dir: Path, debate_dir: Path) -> None:
        """Read the debate directory and judge every card; findings.json of the run feeds the meta."""
        self._dir = debate_dir
        inputs = read_inputs(debate_dir)
        self.decisions = inputs.moderation.decisions
        self.cards = sorted(_cards(inputs), key=card_order)
        self.questions = _questions(inputs)
        moderation = inputs.moderation
        undecided = [finding for finding in inputs.second.side_findings if finding[ID] not in moderation.extra_ids]
        self.pending_side = [] if moderation.ran else undecided
        self.rejected_side = undecided if moderation.ran else []
        self.meta = run_meta(load_findings(round_dir / "findings.json"), round_dir, debate_dir)

    def disputed_open(self) -> list[dict]:
        """What the moderator still has to decide."""
        disputed = [card for card in self.cards if card["outcome"] == "disputed"]
        return [card for card in disputed if card[ID] not in self.decisions]

    def by_severity(self) -> dict[str, int]:
        """The cards to fix per severity."""
        fixes = actionable(self.cards)
        return {severity: _count(fixes, severity) for severity in SEVERITIES}

    def write(self) -> None:
        """final.json with every card, and disputes.json — the input prompt-moderator.md names."""
        final = {"items": self.cards, "questions": self.questions, "meta": self.meta}
        write_json(self._dir / FINAL, final)
        disputes = {"disputes": self.disputed_open(), "side_findings": self.pending_side}
        write_json(self._dir / DISPUTES, disputes)

    def summary(self) -> dict:
        """The JSON line debate_outcome.py prints."""
        cards = self.cards
        return {
            "items": len(cards),
            "outcomes": outcome_counts(cards),
            "actionable": len(actionable(cards)),
            "by_severity": self.by_severity(),
            "needs_moderator": _ids(self.disputed_open()),
            "moderators_split": [card[ID] for card in cards if card["moderators_split"]],
            "reprioritized": reprioritized(cards),
            "side_findings_pending": _ids(self.pending_side),
            "side_findings_rejected": _ids(self.rejected_side),
            "unexamined": [card[ID] for card in cards if card["outcome"] == "unexamined"],
            "questions": len(self.questions),
            "pre_existing": _ids(predating(cards)),
            "degraded": self.meta["degraded"],
            "final": str(self._dir / FINAL),
            "disputes": str(self._dir / DISPUTES),
        }


def _cards(inputs: DebateInputs) -> list[dict]:
    judged = [Judgement(card, inputs).card() for card in inputs.cards]
    moderation = inputs.moderation
    sides = [side_card(entry, moderation.raised) for entry in moderation.extra]
    return [*judged, *sides]


def _questions(inputs: DebateInputs) -> list[dict]:
    answers = inputs.first.answers
    return [
        question_card(entry[QUESTION], entry[ASKED], answers.get(question_id, {}))
        for question_id, entry in inputs.assigned.questions_for.items()
    ]


def _count(cards: list[dict], severity: str) -> int:
    return sum(card["final_severity"] == severity for card in cards)


def _ids(cards: list[dict]) -> list[str]:
    return [card[ID] for card in cards]
