"""The outcome of one debate card: what the other side, the author and the moderator made of it.

Outcomes: consensus · confirmed · refined · restored · dropped · disputed · unexamined; debate_outcome.py
describes each.
"""

from __future__ import annotations

import types
from collections.abc import Iterable
from typing import NamedTuple

from crossreview.keys import ID, SEVERITY, SOURCES, STATUS, VENDORS
from crossreview.models import CLAUDE, CODEX, SIDES
from crossreview.outcome_inputs import DebateInputs
from crossreview.outcome_words import SET_ASIDE, SEV_RANK, UNRANKED

OTHER = types.MappingProxyType({CLAUDE: CODEX, CODEX: CLAUDE})
BOTH = frozenset(SIDES)
CONFIRMED = "confirmed"
AGREED = (CONFIRMED, "refined")
REAL = frozenset(("real", "real_but_different"))
NOT_REAL = frozenset(("not_real", "not_worth_fixing"))
NONE = "none"
KIND = "kind"
SIDE = "side"
STANCE = "stance"
DISPUTED = "disputed"
REFINED = "refined"
RESTORED = "restored"
DROPPED = "dropped"
# what the outcome keeps of a card, in this order
CARD_KEYS = ("id", "file", "line", "end_line", "title", "body", "fix", "severity", "confidence", "sources", "vendors",
             "status")


class Ruling(NamedTuple):
    """An outcome, the final severity and who set it."""

    outcome: str
    final: str
    by: str


class Judgement:
    """One card judged: its thread of verdicts and answers, its outcome and its final severity."""

    def __init__(self, card: dict, inputs: DebateInputs) -> None:
        """Judge the card against round 1, round 2 and the moderator."""
        self._card = card
        self._inputs = inputs
        self._vendors = set(card.get(VENDORS) or [])
        self._status = card.get(STATUS) or CONFIRMED
        self._base = card.get(SEVERITY) or "minor"
        # what the pipeline left to fix: a set-aside item is at none until the debate restores it
        self._kept = NONE if self._status in SET_ASIDE else self._base
        found_by = "+".join(sorted(self._vendors)) or "unknown"
        self._thread = [
            {KIND: "finding", SIDE: found_by, SOURCES: card[SOURCES]},
            {KIND: "pipeline", STATUS: self._status},
        ]
        ruling = self._judged()
        if ruling.outcome in (REFINED, RESTORED) and ruling.final == NONE:
            ruling = Ruling(DROPPED, ruling.final, ruling.by)
        self._decided = False
        self._split = False
        self._ruling = self._moderated(ruling)

    def card(self) -> dict:
        """The card as final.json has it."""
        ruling = self._ruling
        return {
            **{key: self._card.get(key) for key in CARD_KEYS},
            "outcome": ruling.outcome,
            "final_severity": ruling.final,
            "severity_shift": severity_shift(self._kept, ruling.final, ruling.by),
            "moderated": self._decided,
            "moderators_split": self._split,
            "thread": self._thread,
        }

    def _judged(self) -> Ruling:
        if self._vendors == BOTH and self._status in AGREED:
            return Ruling("consensus", self._kept, NONE)
        verdicts = self._inputs.first.verdicts.get(self._card[ID], {})
        for examiner in sorted(verdicts):
            self._thread.append({KIND: "examine", SIDE: examiner, **verdicts[examiner]})
        authors = list(self._vendors)
        author = authors[0] if len(authors) == 1 else None
        if author in OTHER:
            return self._authored(author, verdicts)
        # both sides examined it: both found it and the pipeline set it aside, nobody is named, or the
        # source is outside the roster, so neither side is its author
        return self._examined_by_both(verdicts)

    def _examined_by_both(self, verdicts: dict[str, dict]) -> Ruling:
        examined_by = self._inputs.assigned.examined_by
        asked = examined_by.get(self._card[ID], set(OTHER))
        # every side that was asked has to answer: one verdict alone settles nothing
        if not verdicts or asked - set(verdicts):
            return Ruling("unexamined", self._kept, NONE)
        stances = {verdict[STANCE] for verdict in verdicts.values()}
        if stances <= REAL:
            final = worst(verdict[SEVERITY] for verdict in verdicts.values())
            # restored only when the pipeline had set it aside; an unverified or pre-existing one was kept
            if self._status in SET_ASIDE:
                return Ruling(RESTORED, final, SIDE)
            return Ruling(CONFIRMED if final == self._base else REFINED, final, SIDE)
        if stances <= NOT_REAL:
            return Ruling(DROPPED, NONE, SIDE)
        return Ruling(DISPUTED, self._kept, NONE)

    def _authored(self, author: str, verdicts: dict[str, dict]) -> Ruling:
        verdict = verdicts.get(OTHER[author])
        if verdict is None:
            return Ruling("unexamined", self._kept, NONE)
        agrees = verdict[STANCE] == "real"
        if agrees and verdict[SEVERITY] == self._card[SEVERITY]:
            return Ruling(RESTORED if self._status in SET_ASIDE else CONFIRMED, verdict[SEVERITY], SIDE)
        ruling = self._answered(author, verdict)
        if ruling.outcome == REFINED and self._status in SET_ASIDE:
            return Ruling(RESTORED, ruling.final, ruling.by)
        return ruling

    def _answered(self, author: str, verdict: dict) -> Ruling:
        """The author's answer settles an objection only when it lands on the examiner's severity."""
        responses = self._inputs.second.responses.get(self._card[ID], {})
        response = responses.get(author)
        if response is not None:
            self._thread.append({KIND: "respond", SIDE: author, **response})
        if response is None or response["position"] == "defend":
            return Ruling(DISPUTED, self._kept, NONE)
        if response["position"] == "concede":
            if verdict[STANCE] in NOT_REAL:
                return Ruling(DROPPED, NONE, SIDE)
            return Ruling(REFINED, verdict[SEVERITY], SIDE)
        # an amend that keeps a severity the examiner did not give is still a dispute
        if response[SEVERITY] != verdict[SEVERITY]:
            return Ruling(DISPUTED, self._kept, NONE)
        return Ruling(REFINED, response[SEVERITY], "author")

    def _moderated(self, ruling: Ruling) -> Ruling:
        decision = self._inputs.moderation.decisions.get(self._card[ID])
        if ruling.outcome != DISPUTED or decision is None:
            return ruling
        self._thread.append({KIND: "moderator", **decision})
        if decision.get("split") or decision.get("missing"):
            self._split = True
            return ruling
        self._decided = True
        return Ruling(DISPUTED, decision[SEVERITY], "moderator")


def worst(severities: Iterable[str]) -> str:
    """The strictest known severity; minor when none is known."""
    known = [severity for severity in severities if severity in SEV_RANK]
    return min(known, key=SEV_RANK.__getitem__) if known else "minor"


def severity_shift(base: str | None, final: str, by: str) -> dict:
    """How the final severity differs from the one the pipeline gave; base None is a side finding."""
    if base is None:
        return {"from": None, "to": final, "direction": "new", "by": by}
    if final == base:
        return {"from": base, "to": final, "direction": "same", "by": NONE}
    if final == NONE:
        direction = DROPPED
    else:
        raised = SEV_RANK.get(final, UNRANKED) < SEV_RANK.get(base, UNRANKED)
        direction = "raised" if raised else "lowered"
    return {"from": base, "to": final, "direction": direction, "by": by}
