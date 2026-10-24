"""The closing lines of the Russian summary: open questions and the side findings left without a decision."""

from __future__ import annotations

from crossreview.keys import ID, TITLE
from crossreview.outcome_fold import Fold
from crossreview.outcome_lines import location
from crossreview.outcome_words import RU_OUT


def tail_lines(fold: Fold) -> list[str]:
    """Open questions with who answered them, then side findings pending or rejected by the moderator."""
    lines = []
    if fold.questions:
        lines.append(f"- **{RU_OUT['question']}, {len(fold.questions)}:**")
        lines.extend(_question_line(question) for question in fold.questions)
    if fold.pending_side:
        lines.append(_side_findings_line("Попутные находки без проверки модератора", fold.pending_side))
    if fold.rejected_side:
        lines.append(_side_findings_line("Попутные находки, которые модератор не подтвердил", fold.rejected_side))
    return lines


def _question_line(question: dict) -> str:
    answered = ", ".join(question["answered_by"]) or "никто"
    where = location(question)
    return f"  - `{question[ID]}` {where} — {question[TITLE]} (ответили: {answered})"


def _side_findings_line(heading: str, findings: list[dict]) -> str:
    listed = ", ".join(_side_entry(finding) for finding in findings)
    return f"- **{heading}, {len(findings)}:** {listed}"


def _side_entry(finding: dict) -> str:
    where = f"{finding['file']}:{finding['line']}"
    return f"`{finding[ID]}` {where} — {finding[TITLE]}"
