"""The cards of a debate: every finding that reached any stage of the run, with its vendors and pipeline
status, and which side examines which."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from crossreview import debate_inputs
from crossreview.keys import AGENTS, EXECUTOR, FINDINGS, ID, IMMATERIAL, NAME, OPEN_QUESTIONS, PRE_EXISTING
from crossreview.keys import SOURCES, STATUS, VENDORS, VERDICT
from crossreview.models import SIDES

KEEP = (
    "id", "file", "line", "end_line", "severity", "confidence",
    "title", "body", "fix", "sources", "lenses", "verdict",
)
KEPT_VERDICTS = ("confirmed", "refined", "unverified")
VERIFIED = "3-verified.json"
SYNTHESIZED = "2-synthesized.json"
SYNTH_LISTS = (FINDINGS, PRE_EXISTING, IMMATERIAL, OPEN_QUESTIONS)


class RoundFiles(NamedTuple):
    """The stage files of a finished run; a stage that did not run is stood in for by the one after it."""

    found: dict
    synth: dict
    final: dict
    verified: dict
    verify_ran: bool

    def agents(self) -> list[dict]:
        """The roster as findings.json records it, else as the find stage did."""
        final_agents = (self.final.get(SOURCES) or {}).get(AGENTS)
        return final_agents or (self.found.get(SOURCES) or {}).get(AGENTS) or []

    def open_questions(self) -> list[dict]:
        """The open questions the sides answer: those the run ends with."""
        return _listed(self.verified, OPEN_QUESTIONS)

    def question_ids(self) -> set[str]:
        """The open questions of synthesis and verify: they count as kept."""
        reports = (self.synth, self.verified)
        return {question[ID] for report in reports for question in _listed(report, OPEN_QUESTIONS)}

    def synthesized(self) -> list[dict]:
        """Everything synthesis kept, open questions included."""
        return [finding for key in SYNTH_LISTS for finding in _listed(self.synth, key)]


class Itemizer:
    """Findings as debate cards: the fields a side needs, the vendors behind them and the pipeline status."""

    def __init__(self, agents: list[dict]) -> None:
        """Map every roster agent to its vendor."""
        self.vendor_of = {agent[NAME]: agent[EXECUTOR] for agent in agents}

    def roster(self) -> dict[str, list[str]]:
        """The agents of each side, by name."""
        return {side: self._names(side) for side in SIDES}

    def slim(self, finding: dict, status: str) -> dict:
        """One card: the fields of KEEP, the vendors of its sources and its pipeline status."""
        card = {key: finding[key] for key in KEEP if key in finding}
        sources = finding.get(SOURCES) or []
        vendor_of = self.vendor_of
        card[VENDORS] = sorted({vendor_of.get(source, source) for source in sources})
        card[STATUS] = status
        return card

    def cards(self, files: RoundFiles, round_dir: Path) -> tuple[dict[str, dict], bool]:
        """Every card by id, and whether synthesis drops were read from events.jsonl."""
        cards = self._verified_cards(files.verified)
        if files.verify_ran:
            self._add(cards, _listed(files.synth, FINDINGS), "rejected_by_verify")
        # synthesis records what it dropped in events.jsonl; the heuristic is only for a run without that log
        dropped_ids = debate_inputs.dropped_from_events(round_dir / "events.jsonl")
        self._add(cards, _dropped(files, cards, dropped_ids), "dropped_by_synthesis")
        return cards, dropped_ids is not None

    def _verified_cards(self, verified: dict) -> dict[str, dict]:
        cards = {}
        for finding in _listed(verified, FINDINGS):
            cards[finding[ID]] = self.slim(finding, _verified_status(finding))
        for moved in (IMMATERIAL, PRE_EXISTING):
            for entry in _listed(verified, moved):
                cards[entry[ID]] = self.slim(entry, moved)
        return cards

    def _names(self, side: str) -> list[str]:
        pairs = self.vendor_of.items()
        return sorted(name for name, vendor in pairs if vendor == side)

    def _add(self, cards: dict[str, dict], findings: list[dict], status: str) -> None:
        """Cards for the findings no card covers yet; the first finding of an id wins."""
        for finding in findings:
            if finding[ID] not in cards:
                cards[finding[ID]] = self.slim(finding, status)


def load_round(round_dir: Path) -> RoundFiles:
    """The stage files of a finished run; ValueError names the first that is missing or malformed."""
    stages = round_dir / "stages"
    found = debate_inputs.load(stages / "1-found.json")
    synth = _optional(stages / SYNTHESIZED, found)
    final = debate_inputs.load(round_dir / "findings.json")
    verified = _optional(stages / VERIFIED, final)
    return RoundFiles(found, synth, final, verified, (stages / VERIFIED).exists())




def _listed(report: dict, key: str) -> list[dict]:
    return report.get(key) or []


def _verified_status(finding: dict) -> str:
    verdict = finding.get(VERDICT) or ""
    return verdict if verdict in KEPT_VERDICTS else "confirmed"


def _optional(path: Path, fallback: dict) -> dict:
    return debate_inputs.load(path) if path.exists() else fallback


def _dropped(files: RoundFiles, cards: dict, dropped_ids: set[str] | None) -> list[dict]:
    # open questions count as kept: a raw question must not come back as a dropped finding
    known = {*cards, *files.question_ids()}
    raw_findings = _listed(files.found, FINDINGS)
    fresh = [raw for raw in raw_findings if raw[ID] not in known]
    if dropped_ids is None:
        kept = files.synthesized()
        return [raw for raw in fresh if not debate_inputs.covered(raw, kept)]
    return [raw for raw in fresh if raw[ID] in dropped_ids]
