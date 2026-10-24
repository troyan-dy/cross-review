"""The launch plan of the debate: every step with its prompt, output, model and, for Codex, the codex_run.sh
command."""

from __future__ import annotations

import shlex
from pathlib import Path

from crossreview.debate_steps import FRESH, MODERATOR_SLOTS, ROUND_STEPS, RoundStep
from crossreview.layers import SKILL
from crossreview.models import CLAUDE, CODEX, EFFORT, MODEL, SIDES

CODEX_RUN = SKILL / "scripts" / "codex_run.sh"
MODERATE = "moderate"
SCHEMA = "schema"
COMMAND = "command"
MODERATOR_FILES = ("moderator", "extra")


class LaunchPlan:
    """Who runs which prompt: both sides in both rounds, then the three moderator slots."""

    def __init__(self, debate_dir: Path, schemas: dict[str, Path], workdir: str, models: dict) -> None:
        """models are the debate's: both sides and the moderators, as cross_config.py sides resolves them."""
        self._dir = debate_dir
        self._schemas = schemas
        self._workdir = workdir
        self._sides = {vendor: models[vendor] for vendor in SIDES}
        self._moderators = models["moderators"]

    def entries(self) -> list[dict]:
        """Every step in the order the session runs them."""
        sides = [self._side(round_step, side) for round_step in ROUND_STEPS for side in SIDES]
        moderators = [self._moderator(suffix, vendor) for suffix, vendor in MODERATOR_SLOTS]
        return [*sides, *moderators]

    def _side(self, round_step: RoundStep, side: str) -> dict:
        prompt = self._dir / f"prompt-{round_step.step}-{side}.md"
        output = self._dir / f"{side}-{round_step.step}.json"
        model = self._sides[side]
        entry = launch_entry(round_step.step, side, prompt, str(output), model)
        if side == CODEX:
            return self._codex(entry, round_step, prompt, output)
        # a Claude side continues its round-1 agent in round 2
        if round_step.step == "r2":
            entry["continues"] = "r1"
        return entry

    def _codex(self, entry: dict, round_step: RoundStep, prompt: Path, output: Path) -> dict:
        """Round 1 fresh; round 2 resumes codex-r1.log, or runs fresh with prompt-r2-codex.fresh.md."""
        schema = self._schemas[round_step.prompt]
        model = self._sides[CODEX]
        entry[SCHEMA] = str(schema)
        if round_step.step != "r2":
            entry[COMMAND] = codex_command(self._workdir, (prompt, schema, output), model)
            return entry
        resume = self._dir / "codex-r1.log"
        fresh = self._dir / FRESH
        entry.update(resume=str(resume), fresh=str(fresh))
        entry[COMMAND] = codex_command(self._workdir, (prompt, schema, output), model, resume)
        entry["fresh_command"] = codex_command(self._workdir, (fresh, schema, output), model)
        return entry

    def _moderator(self, suffix: str, vendor: str) -> dict:
        prompt = self._dir / f"prompt-moderator{suffix}.md"
        if vendor == CODEX:
            return self._codex_moderator(prompt)
        outputs = [str(self._dir / f"{stem}{suffix}.json") for stem in MODERATOR_FILES]
        model = self._moderators.get(vendor)
        return launch_entry(MODERATE, vendor, prompt, outputs, model)

    def _codex_moderator(self, prompt: Path) -> dict:
        output = self._dir / "moderator-codex.json"
        schema = self._schemas[MODERATE]
        model = self._moderators[CODEX]
        entry = launch_entry(MODERATE, CODEX, prompt, str(output), model)
        entry[SCHEMA] = str(schema)
        entry[COMMAND] = codex_command(self._workdir, (prompt, schema, output), model)
        return entry


def codex_command(
    workdir: str,
    paths: tuple[Path, Path, Path],
    model: dict,
    resume: Path | None = None,
) -> str:
    """codex_run.sh with the prompt, schema and output paths, the model and, for round 2, the log to resume."""
    run_args = [*map(str, paths), model[MODEL], model[EFFORT]]
    argv = ["bash", str(CODEX_RUN), workdir, *run_args]
    if resume:
        argv.extend(["--resume", str(resume)])
    return shlex.join(argv)


def launch_entry(step: str, vendor: str, prompt: Path, output: object, model: dict | None) -> dict:
    """A step of the plan; a Claude step names its model as an Agent alias, a Codex step as a model."""
    entry: dict = {"step": step, "vendor": vendor, "prompt": str(prompt), "output": output}
    if model is not None:
        model_key = "alias" if vendor == CLAUDE else MODEL
        entry.update({model_key: model[MODEL], EFFORT: model[EFFORT]})
    return entry
