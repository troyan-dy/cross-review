"""Who examines what in round 1: each side looks at what only the other side raised."""

from __future__ import annotations

from typing import NamedTuple

from crossreview.keys import STATUS, VENDORS
from crossreview.models import CLAUDE, CODEX, SIDES

AGREED = ("confirmed", "refined")
BOTH = frozenset(SIDES)


class Routes(NamedTuple):
    """Consensus, and the cards each side examines."""

    consensus: list[dict]
    to_codex: list[dict]
    to_claude: list[dict]


def route(cards: list[dict]) -> Routes:
    """Consensus is what both sides raised and the pipeline kept; everything else goes to the other side."""
    routes = Routes([], [], [])
    for card in cards:
        vendors = set(card[VENDORS])
        if vendors == BOTH and card[STATUS] in AGREED:
            routes.consensus.append(card)
            continue
        # both found it and the pipeline still set it aside, or nobody known did: each side looks again
        if vendors != {CODEX}:
            routes.to_codex.append(card)
        if vendors != {CLAUDE}:
            routes.to_claude.append(card)
    return routes


def asked(questions: list[dict], author: str) -> list[dict]:
    """The open questions the other side of `author` answers: those `author` raised, and those nobody did."""
    return [question for question in questions if _raised_by(question[VENDORS], author)]


def _raised_by(vendors: list[str], author: str) -> bool:
    return author in vendors or not vendors
