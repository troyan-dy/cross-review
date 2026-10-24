"""What the outcome counts: the cards to fix, the ones that predate the change and the reprioritized ones."""

from __future__ import annotations

from collections import Counter

from crossreview.keys import ID, STATUS
from crossreview.outcome_words import SHIFTS

FINAL = "final_severity"
OUTCOME = "outcome"
NONE = "none"
PRE_EXISTING = "pre_existing"
NOT_TO_FIX = ("dropped", "question", "unexamined")


def actionable(cards: list[dict]) -> list[dict]:
    """To fix: what has a severity and is settled — neither unexamined nor a dispute the moderator has not decided
    — and does not predate the change."""
    return [card for card in cards if _to_fix(card)]


def predating(cards: list[dict]) -> list[dict]:
    """Defects that predate the change: they keep their outcome but are listed apart, outside the counts."""
    return [card for card in cards if _predates(card)]


def reprioritized(cards: list[dict]) -> dict[str, list[str]]:
    """The ids whose severity the debate raised, lowered or dropped."""
    return {direction: [card[ID] for card in shifted(cards, direction)] for direction in SHIFTS}


def outcome_counts(cards: list[dict]) -> dict[str, int]:
    """How many cards ended with each outcome, in the order they first appear."""
    return dict(Counter(card[OUTCOME] for card in cards))




def _to_fix(card: dict) -> bool:
    if card[FINAL] == NONE or card[STATUS] == PRE_EXISTING:
        return False
    if card[OUTCOME] in NOT_TO_FIX:
        return False
    return card[OUTCOME] != "disputed" or card["moderated"]


def _predates(card: dict) -> bool:
    if card[STATUS] != PRE_EXISTING:
        return False
    return card[FINAL] != NONE and card[OUTCOME] != "dropped"


def shifted(cards: list[dict], direction: str) -> list[dict]:
    """The cards whose severity moved in that direction."""
    return [card for card in cards if card["severity_shift"]["direction"] == direction]
