"""The cards of final.json besides the judged ones: side findings the moderator confirmed and open questions."""

from __future__ import annotations

from crossreview.keys import ID, SEVERITY, SOURCES, VENDORS
from crossreview.outcome_judge import severity_shift
from crossreview.outcome_words import OUT_RANK, SEV_RANK, UNRANKED

KIND = "kind"
SIDE = "side"
QUESTION_KEYS = ("id", "file", "line", "title", "body", "sources", "vendors")
SIDE_KEYS = ("file", "line", "title", "body", "fix")


def side_card(extra: dict, raised_by_id: dict[str, dict]) -> dict:
    """A side finding the moderator confirmed: the finding comes from round 2, only its severity and note from the
    moderator, so an entry that reuses a real id cannot put another finding under it."""
    raised = raised_by_id[extra[ID]]
    side = raised[VENDORS][0]
    severity = extra[SEVERITY]
    thread = [
        {KIND: "finding", SIDE: side, SOURCES: [f"{side}, раунд 2"]},
        {KIND: "moderator", "verdict": "подтверждаю по коду", SEVERITY: severity, "note": extra.get("note", "")},
    ]
    return {
        ID: extra[ID],
        **{key: raised.get(key) for key in SIDE_KEYS},
        SEVERITY: severity,
        SOURCES: [side],
        VENDORS: [side],
        "status": "side_finding",
        "outcome": "side",
        "final_severity": severity,
        "severity_shift": severity_shift(None, severity, "moderator"),
        "moderated": True,
        "moderators_split": False,
        "thread": thread,
    }


def question_card(question: dict, asked: set[str], answers: dict[str, dict]) -> dict:
    """An open question with the sides' answers."""
    found_by = "+".join(question.get(VENDORS) or []) or "unknown"
    sources = question.get(SOURCES) or []
    thread = [{KIND: "finding", SIDE: found_by, SOURCES: sources}]
    for side, answer in sorted(answers.items()):
        thread.append({KIND: "answer", SIDE: side, **answer})
    return {
        **{key: question.get(key) for key in QUESTION_KEYS},
        "status": "open_question",
        "outcome": "question",
        "final_severity": "none",
        "answered_by": sorted(answers),
        "asked": sorted(asked),
        "thread": thread,
    }


def card_order(card: dict) -> tuple[int, int, str, int]:
    """The order of final.json: severity, outcome, then file and line."""
    return (
        SEV_RANK.get(card["final_severity"], UNRANKED),
        OUT_RANK.get(card["outcome"], UNRANKED),
        card.get("file") or "",
        card.get("line") or 0,
    )
