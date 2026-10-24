"""One verify group's answer applied to its findings: kept, refined, rejected, moved or left unverified."""

from __future__ import annotations

from pathlib import Path

from crossreview.agent_logs import read_answer_list
from crossreview.events import make_event
from crossreview.keys import AGENT, FINDINGS, ID, ITEMS_KEY, PROPERTIES, REASON, SEVERITY, STAGE
from crossreview.keys import VERDICT
from crossreview.layers import plugin_schema
from crossreview.schema import check_schema
from crossreview.severities import rank
from crossreview.verify_groups import VERIFY, agent_name

VERDICTS = ("confirmed", "refined", "rejected", "immaterial", "pre_existing")
UNVERIFIED = "unverified"
REJECTED = "rejected"
REFINED = "refined"
CORRECTIONS = ("line", "end_line", "severity", "confidence", "title", "body", "fix")


class GroupVerdicts:
    """The verdicts of one group: what each finding became, and the events the group leaves."""

    def __init__(self, agents_dir: Path, verify_group: dict) -> None:
        """Read agents/verify-<group>.json; without a readable answer every finding stays unverified."""
        self.agent = agent_name(verify_group)
        self.group = verify_group
        self._fields = {AGENT: self.agent, STAGE: VERIFY}
        verdicts, reason = read_answer_list(agents_dir / f"{self.agent}.json", "verdicts", VERIFY)
        self.reason = reason
        self.ignored: list[str] = []
        self.judged: dict[str, dict | None] = {}
        self.rejected: list[dict] = []
        self._kept: list[dict] = []
        self._by_id = self._matched(verdicts or [])
        props = plugin_schema(VERIFY)[PROPERTIES]["verdicts"][ITEMS_KEY][PROPERTIES]
        for finding in verify_group[FINDINGS]:
            self._judge(finding, props, answered=verdicts is not None)

    def events(self) -> list[dict]:
        """agent_degraded for a group without an answer; otherwise `rejected` when it rejected any, then agent_done."""
        if self.reason:
            return [make_event("agent_degraded", **self._fields, text=self.reason)]
        rejected = [self._rejected_event()] if self.rejected else []
        return [*rejected, self._done_event()]

    def _rejected_event(self) -> dict:
        text = f"{len(self.rejected)} findings rejected"
        return make_event(REJECTED, **self._fields, text=text, findings=self.rejected)

    def _done_event(self) -> dict:
        verdicts = [_verdict_entry(finding_id, verdict) for finding_id, verdict in self._by_id.items()]
        return make_event("agent_done", **self._fields, text=self._done_text(), verdicts=verdicts)

    def _done_text(self) -> str:
        findings = self.group[FINDINGS]
        change = shifted(findings, self._kept)
        return f"{len(self._kept)} of {len(findings)} kept{change}"

    def _matched(self, verdicts: list) -> dict[str, dict]:
        ids = {finding[ID] for finding in self.group[FINDINGS]}
        by_id = {}
        for verdict in verdicts:
            if isinstance(verdict, dict) and verdict.get(ID) in ids:
                by_id[verdict[ID]] = verdict
            else:
                self.ignored.append(_ignored_id(verdict))
        return by_id

    def _judge(self, finding: dict, props: dict, *, answered: bool) -> None:
        verdict = self._by_id.get(finding[ID]) or {}
        if not answered or verdict.get(VERDICT) not in VERDICTS:
            judged: dict | None = {**finding, VERDICT: UNVERIFIED}
        elif verdict[VERDICT] == REJECTED:
            judged = None
            reason = str(verdict.get(REASON) or "")
            self.rejected.append({**finding, VERDICT: REJECTED, REASON: reason})
        else:
            judged = corrected(finding, verdict, props)
        self.judged[finding[ID]] = judged
        if answered and judged is not None:
            self._kept.append(judged)


def corrected(finding: dict, verdict: dict, props: dict) -> dict:
    """The finding under its verdict. Only refined carries corrections, and a field that is empty, zero or
    outside the schema leaves the original value in place."""
    judged = {**finding, VERDICT: verdict[VERDICT]}
    if verdict[VERDICT] != REFINED:
        return judged
    for key in CORRECTIONS:
        correction = verdict.get(key)
        if correction and not check_schema(correction, props[key]):
            judged[key] = correction
    return judged


def shifted(before: list[dict], after: list[dict]) -> str:
    """What the verifier changed beyond keeping or rejecting, worded for the agent_done event."""
    parts = [f"{count} {what}" for count, what in _changes(before, after) if count]
    if not parts:
        return ""
    listed = ", ".join(parts)
    return f", {listed}"


def _changes(before: list[dict], after: list[dict]) -> tuple:
    was = {finding[ID]: rank(finding[SEVERITY]) for finding in before}
    moves = [_move(finding, was) for finding in after]
    refined = sum(finding[VERDICT] == REFINED for finding in after)
    return (
        (sum(move > 0 for move in moves), "lowered"),
        (sum(move < 0 for move in moves), "raised"),
        (refined, REFINED),
    )


def _verdict_entry(finding_id: str, verdict: dict) -> dict:
    return {
        ID: finding_id,
        VERDICT: verdict.get(VERDICT),
        REASON: verdict.get(REASON, ""),
    }


def _move(finding: dict, was: dict[str, int]) -> int:
    """How far the verifier moved a finding's severity: positive is lower, negative is higher."""
    return rank(finding[SEVERITY]) - was[finding[ID]]


def _ignored_id(verdict: object) -> str:
    if isinstance(verdict, dict):
        return str(verdict.get(ID))
    return repr(verdict)
