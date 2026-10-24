"""debate_outcome.py --markdown: the Russian summary of the outcome."""

from __future__ import annotations

from crossreview.keys import ID
from crossreview.outcome_fold import Fold
from crossreview.outcome_lines import card_line, outcome_note, shift_counts, shift_line
from crossreview.outcome_report import actionable, predating, reprioritized, shifted
from crossreview.outcome_tail import tail_lines
from crossreview.outcome_words import RU_OUT, SHIFTS

LISTED_OUTCOMES = ("consensus", "confirmed", "restored", "refined", "dropped", "disputed", "side", "unexamined")
ONE = 1
FEW = (2, 3, 4)
TEENS = (11, 12, 13, 14)
DECIMAL = 10
HUNDRED = 100


def plural(count: int, one: str, few: str, many: str) -> str:
    """`count` with the Russian noun form for it: 1 пункт, 2 пункта, 5 пунктов, 11 пунктов, 21 пункт."""
    last, last_two = count % DECIMAL, count % HUNDRED
    if last == ONE and last_two != TEENS[0]:
        return f"{count} {one}"
    if last in FEW and last_two not in TEENS[1:]:
        return f"{count} {few}"
    return f"{count} {many}"


def markdown(fold: Fold) -> str:
    """The summary: the counts to fix, then the cards by outcome, the reprioritized ones and the rest."""
    before = predating(fold.cards)
    lines = [
        *_head_lines(fold),
        *_outcome_lines(fold, before),
        *_before_lines(before),
        *_shift_lines(fold.cards),
        *tail_lines(fold),
    ]
    return "\n".join(lines)


def _head_lines(fold: Fold) -> list[str]:
    counts = fold.by_severity()
    total = plural(len(actionable(fold.cards)), "пункт", "пункта", "пунктов")
    severities = f"{counts['critical']} critical, {counts['major']} major, {counts['minor']} minor"
    lines = [f"**Итог: {total} к исправлению** — {severities}."]
    if fold.meta["degraded"]:
        degraded = ", ".join(fold.meta["degraded"])
        lines.append(f"**Неполный раунд:** выпали агенты {degraded}.")
    return lines


def _outcome_lines(fold: Fold, before: list[dict]) -> list[str]:
    lines = []
    listed = [card for card in fold.cards if card not in before]
    for outcome in LISTED_OUTCOMES:
        group = [card for card in listed if card["outcome"] == outcome]
        if group:
            lines.append(f"- **{RU_OUT[outcome]}, {len(group)}:**")
            lines.extend(_outcome_line(card, fold) for card in group)
    return lines


def _outcome_line(card: dict, fold: Fold) -> str:
    return card_line(card, outcome_note(card, fold.decisions.get(card[ID])))


def _before_lines(before: list[dict]) -> list[str]:
    if not before:
        return []
    lines = [f"- **Было до изменения, вне подсчёта, {len(before)}:**"]
    for card in before:
        outcome = RU_OUT[card["outcome"]].lower()
        lines.append(card_line(card, f" ({outcome})"))
    return lines


def _shift_lines(cards: list[dict]) -> list[str]:
    moved = reprioritized(cards)
    if not any(moved.values()):
        return []
    lines = [f"- **Переоценка приоритетов:** {shift_counts(moved)}."]
    for direction in SHIFTS:
        lines.extend(shift_line(card, direction) for card in shifted(cards, direction))
    return lines
