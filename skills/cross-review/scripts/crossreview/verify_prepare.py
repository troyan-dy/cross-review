"""`review_verify.py prepare`: a verify prompt per group and the launch plan, or the stage skipped."""

from __future__ import annotations

import json
from pathlib import Path

from crossreview.events import append_events, make_event
from crossreview.jsonio import write_json
from crossreview.keys import AGENTS, FINDINGS, ID, NAME, STAGE, STAGES, VERDICT
from crossreview.layers import resolve
from crossreview.runs import input_or_none, input_paths
from crossreview.stage_run import record_prompts, with_stage, write_prompt
from crossreview.texts import body, render
from crossreview.verify_finish import finish, load_run, stage_entry
from crossreview.verify_groups import KEYS, VERIFY, agent_name, run_groups

# what a verifier sees of a finding: the fields it judges, without the synthesis bookkeeping
SHOWN = ("id", "file", "line", "end_line", "severity", "confidence", "title", "body", "fix", "sources", "lenses")
PROMPT_INPUTS = ("scope", "profile")
UNVERIFIED = "unverified"
SKIPPED = "skipped"
REASON = "reason"
PROMPTS = "prompts"


class VerifyPrompts:
    """The verify prompt with the run's paths filled in; write() adds one group's findings."""

    def __init__(self, repo: Path, run_dir: Path) -> None:
        """Fill {{SCOPE}} {{PROFILE}} {{WORKDIR}} and read the template and the schema of the stage."""
        inputs = input_paths(run_dir)
        self._replacements = {key.upper(): input_or_none(inputs[key]) for key in PROMPT_INPUTS}
        self._replacements["WORKDIR"] = str(repo)
        self._template = body(repo, PROMPTS, VERIFY)
        self._schema = resolve(repo, "schemas", VERIFY).read_text().strip()
        self._run_dir = run_dir

    def launch(self, groups: list[dict]) -> list[dict]:
        """Write prompts/stages/verify-<group>.md for every group; their launch entries are the result."""
        prompts_dir = self._run_dir / PROMPTS / STAGES
        prompts_dir.mkdir(parents=True, exist_ok=True)
        (self._run_dir / AGENTS).mkdir(exist_ok=True)
        return [self._write(verify_group, prompts_dir) for verify_group in groups]

    def _write(self, verify_group: dict, prompts_dir: Path) -> dict:
        shown = [_shown(finding) for finding in verify_group[FINDINGS]]
        findings_json = json.dumps(shown, ensure_ascii=False, indent=2)
        text = render(self._template, {**self._replacements, "FINDINGS": findings_json}, f"prompts/{VERIFY}.md")
        prompt = prompts_dir / f"{agent_name(verify_group)}.md"
        write_prompt(prompt, text, self._schema)
        return {
            "group": verify_group[NAME],
            KEYS: verify_group[KEYS],
            FINDINGS: [finding[ID] for finding in verify_group[FINDINGS]],
            "prompt": str(prompt),
            "output": str(self._run_dir / AGENTS / f"{agent_name(verify_group)}.json"),
        }


def prepare(run_dir: Path) -> dict:
    """Write the verify prompts and print the launch plan; with the stage off or nothing to verify, finish here."""
    staged = load_run(run_dir)
    for stale in (run_dir / PROMPTS / STAGES).glob(f"{VERIFY}-*.md"):
        stale.unlink()
    stages = staged.manifest[STAGES]
    if not stages.get(VERIFY, True):
        return _verify_off(run_dir, staged.manifest, staged.report)
    groups = run_groups(staged.manifest, staged.report)
    if not groups:
        return _nothing_to_verify(run_dir, staged.manifest, staged.report)
    launch = VerifyPrompts(staged.repo, run_dir).launch(groups)
    record_prompts(run_dir, staged.manifest, staged.repo, (PROMPTS, VERIFY), ("schemas", VERIFY))
    append_events(run_dir, [make_event(STAGE, stage=VERIFY)])
    return {
        SKIPPED: False,
        STAGE: VERIFY,
        "vendor": "claude",
        "alias": stages["model"],
        "effort": stages["effort"],
        "group_by": stages.get("verify_group_by", "dir"),
        FINDINGS: len(staged.report[FINDINGS]),
        "groups": len(groups),
        "launch": launch,
    }


def _shown(finding: dict) -> dict:
    return {key: finding[key] for key in SHOWN if key in finding}


def _verify_off(run_dir: Path, manifest: dict, synthesized: dict) -> dict:
    unverified = [{**finding, VERDICT: UNVERIFIED} for finding in synthesized[FINDINGS]]
    note = "Верификация выключена в настройках прогона: находки не проверялись."
    out = finish(run_dir, manifest, {**synthesized, FINDINGS: unverified}, [note])
    append_events(run_dir, [make_event(STAGE, stage=VERIFY, text="skipped: stages.verify is off")])
    return {SKIPPED: True, REASON: "stages.verify is off for this run", **out}


def _nothing_to_verify(run_dir: Path, manifest: dict, synthesized: dict) -> dict:
    verified = with_stage(synthesized, stage_entry(manifest[STAGES], 0, ran=False))
    path = run_dir / STAGES / "3-verified.json"
    write_json(path, verified)
    out = finish(run_dir, manifest, verified, [])
    append_events(run_dir, [make_event(STAGE, stage=VERIFY, text="skipped: no finding to verify")])
    return {SKIPPED: True, REASON: "no finding to verify", "verified": str(path), **out}
