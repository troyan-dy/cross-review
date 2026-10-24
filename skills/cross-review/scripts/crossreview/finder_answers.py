"""One finder's answer as the run keeps it: findings normalized to the schema, numbered, and the agent's row."""

from __future__ import annotations

from pathlib import Path

from crossreview.keys import DEGRADED, END_LINE, EXECUTOR, FINDINGS, FIX, ID, LENSES, NAME
from crossreview.keys import PROPERTIES, SOURCES, VERDICT
from crossreview.agent_logs import exit_code, header_model, limit_hint, read_answer_list
from crossreview.events import make_event
from crossreview.schema import check_schema

# the order a finding's fields are written in, as the run format has it; id, sources and verdict are added here
FIELDS = ("file", "line", "end_line", "severity", "confidence", "title", "body", "fix")


class FinderAnswer:
    """One finder's answer: the findings it keeps, the events of the ones left out, and why the agent is
    degraded when it is."""

    def __init__(self, agents_dir: Path, agent: dict, item_schema: dict) -> None:
        """Read agents/<agent>.json and .exit; a degraded agent's reason names a usage limit its .log mentions."""
        self.agent = agent
        self.log = agents_dir / f"{agent[NAME]}.log"
        raw, reason = _answer(agents_dir, agent[NAME])
        self.reason = f"{reason}{limit_hint(self.log)}" if reason else ""
        self.events: list[dict] = []
        self.kept = [] if reason else self._normalized(raw or [], item_schema)

    def _normalized(self, raw: list, item_schema: dict) -> list[dict]:
        kept = []
        for raw_finding in raw:
            finding, errors = normalize(raw_finding, item_schema, self.agent)
            if finding is None:
                self._reject(raw_finding, errors)
            else:
                kept.append(finding)
        return kept

    def _reject(self, raw_finding: object, errors: list[str]) -> None:
        name = self.agent[NAME]
        event = make_event("finding_rejected", agent=name, text="; ".join(errors), finding=raw_finding)
        self.events.append(event)


def normalize(
    raw: object,
    item_schema: dict,
    agent: dict,
) -> tuple[dict | None, list[str]]:
    """One finding as the run keeps it, or None and the schema errors that leave it out."""
    if not isinstance(raw, dict):
        return None, ["finding: expected object"]
    props = item_schema.get(PROPERTIES) or {}
    finding = {key: field for key, field in raw.items() if key in props}
    finding.setdefault(END_LINE, 0)
    finding.setdefault(FIX, "")
    errors = check_schema(finding, item_schema, "finding")
    if errors:
        return None, errors
    finding[LENSES] = _own_lenses(finding[LENSES], agent[LENSES])
    return finding, []


def numbered(name: str, kept: list[dict]) -> list[dict]:
    """The kept findings of agent `name` with the ids <agent>-<n> and the agent as their only source."""
    return [_numbered(name, number, finding) for number, finding in enumerate(kept, 1)]


def agent_stats(answer: FinderAnswer) -> dict:
    """The agent's row of sources.agents: who it was, what it ran on and how it went."""
    agent = answer.agent
    return {
        NAME: agent[NAME],
        LENSES: agent[LENSES],
        EXECUTOR: agent[EXECUTOR],
        "requested_model": agent["requested_model"],
        "actual_model": header_model(answer.log),
        "effort": agent["effort"],
        "tokens": 0,
        "raised": len(answer.kept),
        DEGRADED: bool(answer.reason),
    }


def _own_lenses(named: list[str], given: list[str]) -> list[str]:
    """The lenses of a finding the agent was given; all of them when it named none of those."""
    return [lens for lens in named if lens in given] or list(given)


def _numbered(name: str, number: int, finding: dict) -> dict:
    fields = {key: finding[key] for key in FIELDS}
    return {
        ID: f"{name}-{number}",
        **fields,
        SOURCES: [name],
        LENSES: finding[LENSES],
        VERDICT: "",
    }


def _answer(agents_dir: Path, name: str) -> tuple[list | None, str]:
    raw, reason = read_answer_list(agents_dir / f"{name}.json", FINDINGS, "finder")
    code = exit_code(agents_dir / f"{name}.exit")
    if code:
        suffix = f"; {reason}" if reason else ""
        reason = f"exited {code}{suffix}"
    return raw, reason
