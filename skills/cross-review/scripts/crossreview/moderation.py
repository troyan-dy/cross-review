"""How two independent moderators merge: a dispute is decided when both agree on whether the finding stands,
at the stricter severity; a side finding stands when both confirm it."""

from __future__ import annotations

from crossreview.keys import ID, NOTE, SEVERITY, SOURCES, VENDORS, VERDICT

STRICTNESS = ("critical", "major", "minor", "none")
NONE = "none"


def stricter(first: str, second: str) -> str:
    """The stricter of two severities; the first on a tie."""
    return min((first, second), key=STRICTNESS.index)


def both_notes(claude_note: object, codex_note: object) -> str:
    """The notes of both moderators, Claude's first."""
    return f"Модератор Claude: {claude_note}\n\nМодератор Codex: {codex_note}"


def merge_decision(finding_id: str, claude: dict | None, codex: dict | None) -> dict:
    """One dispute: decided, split between the moderators (`split`) or answered by one only (`missing`)."""
    if claude is None or codex is None:
        return _one_answer(finding_id, claude, codex)
    severity = stricter(claude[SEVERITY], codex[SEVERITY])
    note = both_notes(claude[NOTE], codex[NOTE])
    if (claude[SEVERITY] != NONE) != (codex[SEVERITY] != NONE):
        verdict = f"модераторы разошлись: Claude — {claude[VERDICT]}, Codex — {codex[VERDICT]}; решает человек"
        return {ID: finding_id, VERDICT: verdict, SEVERITY: severity, NOTE: note, "split": True}
    return {ID: finding_id, VERDICT: _agreed_verdict(claude, codex), SEVERITY: severity, NOTE: note}


def merge_extra(extra_id: str, claude: dict, codex: dict) -> dict:
    """A side finding both moderators confirmed, at the stricter severity with both notes; its side comes from its
    id (codex-side-2) when the moderator left out sources and vendors."""
    merged = dict(claude)
    first, second = claude[SEVERITY], codex[SEVERITY]
    merged[SEVERITY] = stricter(first, second)
    note = both_notes(claude.get(NOTE, ""), codex.get(NOTE, ""))
    if first != second:
        note = f"{note}\n\nСерьёзность разошлась ({first} / {second}), берём строже."
    merged[NOTE] = note
    side = _side_of(merged, extra_id)
    merged[SOURCES] = merged.get(SOURCES) or [side]
    merged[VENDORS] = merged.get(VENDORS) or [side]
    return merged


def _one_answer(finding_id: str, claude: dict | None, codex: dict | None) -> dict:
    answered = codex if claude is None else claude
    who = "Claude" if claude else "Codex"
    verdict = f"ответил только один модератор ({who}): {answered[VERDICT]}; решает человек"
    return {
        ID: finding_id,
        VERDICT: verdict,
        SEVERITY: answered[SEVERITY],
        NOTE: answered[NOTE],
        "missing": True,
    }


def _side_of(extra: dict, extra_id: str) -> str:
    from_id = [extra_id.split("-side-", 1)[0]]
    return (extra.get(VENDORS) or extra.get(SOURCES) or from_id)[0]


def _agreed_verdict(claude: dict, codex: dict) -> str:
    verdict = claude[VERDICT]
    if verdict != codex[VERDICT]:
        verdict = f"оба модератора: {claude[VERDICT]} / {codex[VERDICT]}"
    if claude[SEVERITY] != codex[SEVERITY]:
        verdict = f"{verdict}; серьёзность разошлась ({claude[SEVERITY]} / {codex[SEVERITY]}), берём строже"
    return verdict
