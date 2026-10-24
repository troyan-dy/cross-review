"""The lines of the Russian summary of debate_outcome.py --markdown, one per card."""

from __future__ import annotations

from crossreview.keys import ID, TITLE
from crossreview.outcome_words import RU_BY, RU_SHIFT, SHIFTS

SHIFT = "severity_shift"
NONE = "none"
FROM = "from"
SHIFTED = ("raised", "lowered")


def location(card: dict) -> str:
    """file:line of a card, ? and 0 for what it lacks."""
    path = card.get("file") or "?"
    line = card.get("line") or 0
    return f"{path}:{line}"


def card_line(card: dict, note: str) -> str:
    """A card's line: id, final severity, location, title and the note after it."""
    head = f"`{card[ID]}` [{card['final_severity']}]"
    return f"  - {head} {location(card)} — {card[TITLE]}{note}"


def outcome_note(card: dict, decision: dict | None) -> str:
    """What follows a card's title: how its severity moved and, for a dispute, where it stands."""
    note = shift_note(card)
    if card["outcome"] != "disputed":
        return note
    if card["moderators_split"]:
        if decision and decision.get("missing"):
            return f"{note} — {decision['verdict']}"
        return f"{note} — модераторы разошлись, решает человек"
    if decision:
        return f"{note} — модератор: {decision['verdict']}, {decision['severity']}"
    return f"{note} — ждёт модератора"


def shift_note(card: dict) -> str:
    """ (было X → Y) when the debate raised or lowered the severity."""
    shift = card[SHIFT]
    if shift["direction"] not in SHIFTED:
        return ""
    return f" (было {_was(shift)} → {shift['to']})"


def shift_line(card: dict, direction: str) -> str:
    """A card's line in «Переоценка приоритетов»: was → now and who changed it."""
    shift = card[SHIFT]
    now = "снято" if shift["to"] == NONE else shift["to"]
    move = f"`{card[ID]}` {_was(shift)} → {now}"
    who = RU_BY[shift["by"]]
    tail = f"{location(card)} — {card[TITLE]} ({who})"
    return f"  - {RU_SHIFT[direction]}: {move} {tail}"


def shift_counts(moved: dict[str, list[str]]) -> str:
    """raised, lowered and dropped with their counts, as «Переоценка приоритетов» opens."""
    sizes = {direction: len(ids) for direction, ids in moved.items()}
    words = {direction: RU_SHIFT[direction].lower() for direction in SHIFTS}
    return ", ".join(f"{words[direction]} {sizes[direction]}" for direction in SHIFTS)


def _was(shift: dict) -> str:
    return "отложено" if shift[FROM] == NONE else shift[FROM]
