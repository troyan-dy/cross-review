"""`review_synth.py prepare`: the synthesis prompt and its launch plan, or the stage skipped."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from crossreview.errors import ConfigError
from crossreview.events import append_events, make_event
from crossreview.keys import AGENTS, DEGRADED, FINDINGS, STAGE, STAGES
from crossreview.layers import resolve
from crossreview.runs import locate
from crossreview.stage_run import StageInput, degraded_names, read_manifest, record_prompts, write_prompt
from crossreview.synth_sources import sources_block
from crossreview.texts import body, render

SYNTHESIS = "synthesis"
FOUND = "1-found.json"
SYNTHESIZED = "2-synthesized.json"


def load_run(run_dir: Path) -> StageInput:
    """The repository, the manifest and stages/1-found.json of a collected run."""
    repo = locate(run_dir).repo
    manifest = read_manifest(run_dir)
    try:
        found = json.loads((run_dir / STAGES / FOUND).read_text())
    except (OSError, ValueError):
        raise ConfigError(f"{run_dir}: no readable stages/1-found.json, collect the run with review_collect.py") from None
    if (run_dir / STAGES / "3-verified.json").exists():
        raise ConfigError(f"{run_dir}: verify already ran over this synthesis, start a new run")
    return StageInput(repo, manifest, found)


def prepare(run_dir: Path) -> dict:
    """Write prompts/stages/synthesis.md and print the launch plan; skip the stage when it is off or empty."""
    repo, manifest, found = load_run(run_dir)
    reason = _skip_reason(manifest, found)
    if reason:
        return _skip(run_dir, reason, len(found[FINDINGS]))
    prompt = _write_prompt(run_dir, repo, found)
    record_prompts(run_dir, manifest, repo, ("prompts", SYNTHESIS), ("schemas", SYNTHESIS))
    append_events(run_dir, [make_event(STAGE, stage=SYNTHESIS)])
    return {
        "skipped": False,
        STAGE: SYNTHESIS,
        "vendor": "claude",
        "alias": manifest[STAGES]["model"],
        "effort": manifest[STAGES]["effort"],
        "prompt": str(prompt),
        "output": str(run_dir / AGENTS / f"{SYNTHESIS}.json"),
        FINDINGS: len(found[FINDINGS]),
        DEGRADED: degraded_names(found),
    }


def _skip_reason(manifest: dict, found: dict) -> str:
    if not manifest[STAGES].get(SYNTHESIS, True):
        return "stages.synthesis is off for this run"
    if not found[FINDINGS]:
        return "no finding to merge"
    return ""


def _skip(run_dir: Path, reason: str, findings: int) -> dict:
    out = run_dir / STAGES / SYNTHESIZED
    shutil.copyfile(run_dir / STAGES / FOUND, out)
    append_events(run_dir, [make_event(STAGE, stage=SYNTHESIS, text=f"skipped: {reason}")])
    return {"skipped": True, "reason": reason, "synthesized": str(out), FINDINGS: findings}


def _write_prompt(run_dir: Path, repo: Path, found: dict) -> Path:
    replacements = {
        "SOURCES": sources_block(found),
        "FINDINGS": json.dumps(found[FINDINGS], ensure_ascii=False, indent=2),
    }
    text = render(body(repo, "prompts", SYNTHESIS), replacements, f"prompts/{SYNTHESIS}.md")
    schema = resolve(repo, "schemas", SYNTHESIS)
    prompt = run_dir / "prompts" / STAGES / f"{SYNTHESIS}.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    write_prompt(prompt, text, schema.read_text().strip())
    return prompt
