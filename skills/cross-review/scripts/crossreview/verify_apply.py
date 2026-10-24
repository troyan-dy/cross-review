"""`review_verify.py apply`: the verdicts of every group routed, then 3-verified.json, findings.json and
report.md written."""

from __future__ import annotations

from pathlib import Path

from crossreview.errors import ConfigError
from crossreview.events import read_events, stage_duration, write_events
from crossreview.jsonio import write_json
from crossreview.keys import AGENTS, FINDINGS, ID, IMMATERIAL, NAME, PRE_EXISTING, REASON, STAGES
from crossreview.keys import VERDICT
from crossreview.report_lines import VERDICT_WORDS
from crossreview.stage_run import with_stage
from crossreview.verify_finish import finish, load_run, own_event, stage_entry
from crossreview.verify_groups import VERIFY, run_groups
from crossreview.verify_judge import REJECTED, UNVERIFIED, VERDICTS, GroupVerdicts

MOVED = (IMMATERIAL, PRE_EXISTING)
COUNTED = (*VERDICTS, UNVERIFIED)


class Verification:
    """One apply over every verify group of a run."""

    def __init__(self, run_dir: Path) -> None:
        """Read the run, its groups and every group's answer."""
        self._run_dir = run_dir
        staged = load_run(run_dir)
        self._manifest = staged.manifest
        self._synthesized = staged.report
        if not self._manifest[STAGES].get(VERIFY, True):
            raise ConfigError("stages.verify is off for this run: prepare already wrote findings.json, go to the debate")
        groups = run_groups(self._manifest, self._synthesized)
        if not groups:
            raise ConfigError("no finding to verify: prepare already wrote findings.json, go to the debate")
        self._groups = [GroupVerdicts(run_dir / AGENTS, verify_group) for verify_group in groups]
        self._judged = {
            finding_id: judged
            for verdicts in self._groups for finding_id, judged in verdicts.judged.items()
        }

    def apply(self) -> dict:
        """Write 3-verified.json, findings.json and report.md; the counts per verdict are the result."""
        lines = read_events(self._run_dir)
        events = [event for verdicts in self._groups for event in verdicts.events()]
        write_events(self._run_dir, [line for line in lines if not own_event(line)], events)
        verified = self._verified(stage_duration(lines, VERIFY))
        path = self._run_dir / STAGES / "3-verified.json"
        write_json(path, verified)
        count = {name: self._ids_with(name) for name in COUNTED}
        return {
            "verified": str(path),
            **finish(self._run_dir, self._manifest, verified, self._notes(count)),
            "groups": len(self._groups),
            "degraded": self._degraded(),
            "confirmed": len(count["confirmed"]),
            "refined": len(count["refined"]),
            REJECTED: count[REJECTED],
            IMMATERIAL: count[IMMATERIAL],
            PRE_EXISTING: count[PRE_EXISTING],
            UNVERIFIED: count[UNVERIFIED],
            "ignored": [finding_id for verdicts in self._groups for finding_id in verdicts.ignored],
        }

    def _verified(self, duration: int) -> dict:
        synthesized = self._synthesized
        judged = [self._judged[raw[ID]] for raw in synthesized[FINDINGS]]
        outcome = [finding for finding in judged if finding is not None]
        report = {
            **synthesized,
            FINDINGS: [finding for finding in outcome if finding[VERDICT] not in MOVED],
            PRE_EXISTING: [*(synthesized.get(PRE_EXISTING) or []), *_with_verdict(outcome, PRE_EXISTING)],
            IMMATERIAL: [*(synthesized.get(IMMATERIAL) or []), *_with_verdict(outcome, IMMATERIAL)],
        }
        return with_stage(report, stage_entry(self._manifest[STAGES], duration, ran=True))

    def _ids_with(self, verdict: str) -> list[str]:
        judged = self._judged.items()
        return [finding_id for finding_id, finding in judged if _verdict_of(finding) == verdict]

    def _degraded(self) -> list[dict]:
        return [
            {"group": verdicts.group[NAME], REASON: verdicts.reason}
            for verdicts in self._groups if verdicts.reason
        ]

    def _notes(self, count: dict[str, list[str]]) -> list[str]:
        notes = [f"Верификация: {_summary(count)}."]
        silent = ", ".join(entry["group"] for entry in self._degraded())
        if silent:
            notes.append(f"Не ответили верификаторы групп {silent}: их находки остались непроверенными.")
        return notes


def apply(run_dir: Path) -> dict:
    """Route the verdicts of run_dir and write its findings.json and report.md."""
    return Verification(run_dir).apply()


def _verdict_of(judged: dict | None) -> str:
    return judged[VERDICT] if judged else REJECTED


def _with_verdict(findings: list[dict], verdict: str) -> list[dict]:
    return [finding for finding in findings if finding[VERDICT] == verdict]


def _summary(count: dict[str, list[str]]) -> str:
    sizes = {name: len(ids) for name, ids in count.items()}
    return ", ".join(f"{VERDICT_WORDS[name]} {sizes[name]}" for name in COUNTED)
