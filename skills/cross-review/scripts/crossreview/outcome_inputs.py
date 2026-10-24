"""Everything the outcome fold reads from the debate directory, loaded in the order the files are checked."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from crossreview.debate_schemas import DECISIONS_SCHEMA, EXAMINE_SCHEMA, EXTRA_SCHEMA, RESPOND_SCHEMA, load_answer
from crossreview.keys import FINDINGS, ID, OPEN_QUESTIONS, SOURCES, VENDORS
from crossreview.models import SIDES
from crossreview.outcome_files import BUILT, load_built

QUESTION = "q"
ASKED = "sides"


class Assignments:
    """Who was asked what in round 1: the sides that examine each finding and answer each open question."""

    def __init__(self, debate_dir: Path) -> None:
        """Read for-claude.json and for-codex.json."""
        self.examined_by: dict[str, set[str]] = {}
        self.questions_for: dict[str, dict] = {}
        for side in SIDES:
            self._add(side, load_built(debate_dir / f"for-{side}.json"))

    def _add(self, side: str, assigned: dict) -> None:
        for card in assigned[FINDINGS]:
            self.examined_by.setdefault(card[ID], set()).add(side)
        for question in assigned[OPEN_QUESTIONS]:
            fresh = {QUESTION: question, ASKED: set()}
            self.questions_for.setdefault(question[ID], fresh)[ASKED].add(side)


class FirstRound:
    """Round 1: every examiner's verdict on a finding and every side's answer to an open question."""

    def __init__(self, debate_dir: Path) -> None:
        """Read claude-r1.json and codex-r1.json; a missing one is no answer."""
        self.verdicts: dict[str, dict[str, dict]] = {}
        self.answers: dict[str, dict[str, dict]] = {}
        for side in SIDES:
            answer = load_answer(debate_dir / f"{side}-r1.json", EXAMINE_SCHEMA, {})
            for verdict in answer.get("verdicts", []):
                self.verdicts.setdefault(verdict[ID], {})[side] = verdict
            for reply in answer.get("open_question_answers", []):
                self.answers.setdefault(reply[ID], {})[side] = reply


class SecondRound:
    """Round 2: every author's response to the objections, and the side findings raised on the way."""

    def __init__(self, debate_dir: Path) -> None:
        """Read claude-r2.json and codex-r2.json; a side finding gets the id <side>-side-<n>."""
        self.responses: dict[str, dict[str, dict]] = {}
        self.side_findings: list[dict] = []
        for side in SIDES:
            self._add(side, load_answer(debate_dir / f"{side}-r2.json", RESPOND_SCHEMA, {}))

    def _add(self, side: str, answer: dict) -> None:
        for response in answer.get("responses", []):
            self.responses.setdefault(response[ID], {})[side] = response
        for number, finding in enumerate(answer.get("side_findings", []), 1):
            raised = {ID: f"{side}-side-{number}", SOURCES: [side], VENDORS: [side]}
            self.side_findings.append({**raised, **finding})


class Moderation:
    """The moderator's decisions and the side findings it confirmed."""

    def __init__(self, debate_dir: Path, side_findings: list[dict]) -> None:
        """Read moderator.json and extra.json; an extra entry must carry the id of a round-2 side finding."""
        decisions = load_answer(debate_dir / "moderator.json", DECISIONS_SCHEMA, [])
        self.decisions = {decision[ID]: decision for decision in decisions}
        extra_file = debate_dir / "extra.json"
        # once extra.json exists the moderator has seen every side finding: those it left out are rejected
        self.ran = extra_file.exists()
        self.extra = load_answer(extra_file, EXTRA_SCHEMA, [])
        self.extra_ids = {entry[ID] for entry in self.extra}
        # the moderator confirms what a side raised in round 2; an id no side raised is not a side finding
        self.raised = {finding[ID]: finding for finding in side_findings}
        unknown = sorted(self.extra_ids - set(self.raised))
        if unknown:
            listed = ", ".join(unknown)
            raise ValueError(f"{extra_file}: {listed} is no side finding of round 2, rerun the moderator")


class DebateInputs(NamedTuple):
    """The cards debate_build.py wrote and every answer given about them."""

    cards: list[dict]
    assigned: Assignments
    first: FirstRound
    second: SecondRound
    moderation: Moderation


def read_inputs(debate_dir: Path) -> DebateInputs:
    """Every input of the fold; ValueError names the first file that is missing or breaks its shape."""
    _check_built(debate_dir)
    cards = load_built(debate_dir / "items.json")
    assigned = Assignments(debate_dir)
    first = FirstRound(debate_dir)
    second = SecondRound(debate_dir)
    return DebateInputs(cards, assigned, first, second, Moderation(debate_dir, second.side_findings))


def _check_built(debate_dir: Path) -> None:
    # what debate_build.py writes is the input itself: without it an empty outcome would pass for a clean one
    missing = [name for name in BUILT if not (debate_dir / name).is_file()]
    if missing:
        listed = ", ".join(missing)
        raise ValueError(f"{debate_dir}: no {listed}, run debate_build.py first")
