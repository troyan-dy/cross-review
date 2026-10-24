"""`review_synth.py apply`: the synthesis rules over the model's answer, written to stages/2-synthesized.json.

The model decides only what needs judgement: which inputs are one problem, which list each goes to, the wording
and the anchor. Attribution, confidence, severity and the drop rule are applied here.
"""

from __future__ import annotations

from operator import itemgetter
from pathlib import Path

from crossreview.errors import ConfigError
from crossreview.events import make_event, parse_event, read_events, stage_duration, write_events
from crossreview.jsonio import write_json
from crossreview.keys import AGENT, AGENTS, FINDINGS, ID, IMMATERIAL, NAME, OPEN_QUESTIONS, PRE_EXISTING
from crossreview.keys import STAGES
from crossreview.stage_run import degraded_names, with_stage
from crossreview.synth_answer import LISTS, read_answer
from crossreview.synth_prepare import SYNTHESIS, SYNTHESIZED, load_run
from crossreview.synth_rules import attribute, combine, droppable, severity_order

MERGED_IDS = "merged_ids"


class Synthesis:
    """One apply: the answer attributed to its inputs, the drop rule run, the result and its events written."""

    def __init__(self, run_dir: Path, answer_path: Path | None) -> None:
        """Read the run and the answer; every input id the answer claims is resolved here."""
        self._run_dir = run_dir
        staged = load_run(run_dir)
        self._stages = staged.manifest[STAGES]
        self._found = staged.report
        if not self._stages.get(SYNTHESIS, True):
            raise ConfigError("stages.synthesis is off for this run: prepare already wrote 2-synthesized.json, go to verify")
        answer = read_answer(answer_path or run_dir / AGENTS / f"{SYNTHESIS}.json")
        self._inputs = {finding[ID]: finding for finding in self._found[FINDINGS]}
        self._degraded = degraded_names(self._found)
        # one claimed set across the three lists: an input reported both as a finding and as a question is the
        # same contract violation as one reported twice in either
        self._claimed: set[str] = set()
        self._lists = {name: self._attributed(answer[name], name) for name in LISTS}

    def apply(self) -> dict:
        """Write 2-synthesized.json and replace the events of an earlier apply; the counts are the result."""
        findings, dropped, restored = self._partition()
        lines = self._rewrite_events(findings, dropped, restored)
        out = self._write(findings, lines)
        return {
            "synthesized": str(out),
            "inputs": len(self._inputs),
            FINDINGS: len(findings),
            OPEN_QUESTIONS: len(self._lists[OPEN_QUESTIONS]),
            PRE_EXISTING: len(self._lists[PRE_EXISTING]),
            "merged": self._merged(),
            "dropped": [finding[ID] for finding in dropped],
            "restored": [finding[ID] for finding in restored],
            "degraded": self._degraded,
        }

    def _attributed(self, outputs: list[dict], name: str) -> list[dict]:
        return attribute(outputs, self._inputs, self._claimed, name == FINDINGS)

    def _merged(self) -> int:
        outputs = [output for name in LISTS for output in self._lists[name]]
        return sum(len(output[MERGED_IDS]) - 1 for output in outputs)

    def _rewrite_events(self, findings: list, dropped: list, restored: list) -> list[str]:
        lines = read_events(self._run_dir)
        kept = [line for line in lines if not own_event(line)]
        write_events(self._run_dir, kept, _events(findings, dropped, restored))
        return lines

    def _partition(self) -> tuple[list[dict], list[dict], list[dict]]:
        """What stays, what the drop rule drops, and the inputs the answer left out that it keeps."""
        kept, dropped = _split(self._lists[FINDINGS], self._degraded)
        left_out = _left_out(self._inputs, self._claimed)
        restored, dropped_own = _split(left_out, self._degraded)
        dropped.extend(self._inputs[own[ID]] for own in dropped_own)
        dropped.sort(key=itemgetter(ID))
        kept.extend(restored)
        kept.sort(key=severity_order)
        return kept, dropped, restored

    def _write(self, findings: list[dict], lines: list[str]) -> Path:
        report = {
            **self._found,
            FINDINGS: findings,
            OPEN_QUESTIONS: self._lists[OPEN_QUESTIONS],
            PRE_EXISTING: self._lists[PRE_EXISTING],
            IMMATERIAL: [],
        }
        row = {
            NAME: SYNTHESIS,
            "duration_ms": stage_duration(lines, SYNTHESIS),
            "executor": "claude",
            "model": self._stages["model"],
            "effort": self._stages["effort"],
        }
        out = self._run_dir / STAGES / SYNTHESIZED
        write_json(out, with_stage(report, row))
        return out


def apply(run_dir: Path, answer_path: Path | None) -> dict:
    """Apply the synthesis answer of run_dir; answer_path defaults to agents/synthesis.json."""
    return Synthesis(run_dir, answer_path).apply()


def own_event(line: str) -> bool:
    """An event an earlier apply wrote: applying again replaces them rather than adding a second set."""
    event = parse_event(line)
    return isinstance(event, dict) and event.get(AGENT) == SYNTHESIS


def _events(findings: list[dict], dropped: list[dict], restored: list[dict]) -> list:
    events = []
    if dropped:
        text = f"{len(dropped)} findings dropped"
        events.append(make_event("dropped", agent=SYNTHESIS, text=text, findings=dropped))
    if restored:
        text = f"{len(restored)} findings the answer left out restored, the drop rule keeps them"
        events.append(make_event("restored", agent=SYNTHESIS, text=text, findings=restored))
    events.append(make_event("agent_done", agent=SYNTHESIS, text=f"{len(findings)} findings"))
    return events


def _split(findings: list[dict], degraded: list[str]) -> tuple[list, list]:
    """The findings the drop rule keeps and the ones it drops; nothing drops in a degraded round."""
    gate = not degraded
    dropped = [finding for finding in findings if gate and droppable(finding)]
    kept = [finding for finding in findings if not (gate and droppable(finding))]
    return kept, dropped


def _left_out(inputs: dict[str, dict], claimed: set[str]) -> list[dict]:
    """The inputs the answer claimed nowhere, each as its own synthesized finding."""
    unclaimed = [raw for finding_id, raw in inputs.items() if finding_id not in claimed]
    return [combine(raw, [raw], True) for raw in unclaimed]
