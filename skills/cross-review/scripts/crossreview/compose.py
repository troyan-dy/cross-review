"""`review_run.py compose`: the finder prompts of a run, its launch plan and manifest.json."""

from __future__ import annotations

import shlex
from pathlib import Path

from crossreview.config_load import load_config
from crossreview.errors import ConfigError
from crossreview.finder_prompts import FinderPrompts
from crossreview.jsonio import now, write_json
from crossreview.layers import SKILL, digest, source
from crossreview.models import CLAUDE, CODEX, EFFORT, MODEL
from crossreview.runs import filled, input_paths, locate
from crossreview.selection import roster_agents, select

CODEX_RUN = SKILL / "scripts" / "codex_run.sh"
LENSES = "lenses"
AGENTS = "agents"
VENDOR = "vendor"
MODERATORS = "moderators"


class Composition:
    """One compose of a run: the models chosen, the finder prompts written, the manifest recorded."""

    def __init__(
        self,
        run_dir: Path,
        tier: str | None,
        claude: str | None,
        codex: str | None,
    ) -> None:
        """Check the run can be composed, then load its configuration and render the prompts."""
        self._run_dir = run_dir
        self._place = locate(run_dir)
        self._inputs = _unanswered_inputs(run_dir)
        self._cfg = load_config(self._place.repo)
        self._models = select(self._cfg, tier, claude, codex)
        self._agents = roster_agents(self._cfg, self._models)
        self._prompts = FinderPrompts(self._place.repo, run_dir, self._agents)

    def write(self) -> dict:
        """Write every agent's prompt and manifest.json; the launch plan is the result."""
        launch, manifest_agents = self._write_agents()
        manifest = self._manifest(manifest_agents)
        write_json(self._run_dir / "manifest.json", manifest)
        return {
            "run_dir": str(self._run_dir),
            **{key: manifest[key] for key in ("tier", "pair")},
            "previous": manifest["prior_runs"],
            "launch": launch,
        }

    def _write_agents(self) -> tuple[list[dict], list[dict]]:
        prompts_dir = _fresh_prompts_dir(self._run_dir)
        launch, manifest_agents = [], []
        for agent in self._agents:
            prompt = prompts_dir / f"{agent['name']}.md"
            prompt.write_text(self._prompts.text(agent))
            launch.append(self._launch_entry(agent, prompt))
            manifest_agents.append(_manifest_agent(agent, prompt))
        return launch, manifest_agents

    def _launch_entry(self, agent: dict, prompt: Path) -> dict:
        output = self._run_dir / AGENTS / f"{agent['name']}.json"
        model_key = "alias" if agent[VENDOR] == CLAUDE else MODEL
        entry = {
            "agent": agent["name"],
            VENDOR: agent[VENDOR],
            model_key: agent[MODEL],
            EFFORT: agent[EFFORT],
            "prompt": str(prompt),
            "output": str(output),
        }
        if agent[VENDOR] == CODEX:
            paths = (CODEX_RUN, self._place.repo, prompt, self._prompts.schema, output)
            argv = [*map(str, paths), agent[MODEL], agent[EFFORT]]
            entry["schema"] = str(self._prompts.schema)
            entry["command"] = shlex.join(["bash", *argv])
        return entry

    def _manifest(self, manifest_agents: list[dict]) -> dict:
        models = self._models
        return {
            "task": self._place.task,
            "run": self._place.run,
            "scope_path": self._inputs["scope"],
            "workdir": str(self._place.repo),
            "started_at": now(),
            "tier": models["tier"],
            "pair": models["pair"],
            "models": {CLAUDE: models[CLAUDE], CODEX: models[CODEX]},
            MODERATORS: models[MODERATORS],
            "stages": {**self._cfg["stages"], **models[CLAUDE]},
            AGENTS: manifest_agents,
            "prior_runs": [entry["run"] for entry in self._prompts.previous],
            "prompts": self._sources(),
        }

    def _sources(self) -> list[dict]:
        repo = self._place.repo
        lenses = [source(repo, LENSES, lens) for lens in self._prompts.lenses]
        return [source(repo, "prompts", "review"), *lenses, source(repo, "schemas", "finder")]


def compose(
    run_dir: Path,
    tier: str | None,
    claude: str | None,
    codex: str | None,
) -> dict:
    """Write prompts/agents/<agent>.md for every roster agent and manifest.json; the launch plan is the result."""
    return Composition(run_dir, tier, claude, codex).write()


def _unanswered_inputs(run_dir: Path) -> dict:
    present = (run_dir / AGENTS).glob("*")
    answered = sorted(path.name for path in present if path.is_file())
    if answered or (run_dir / "stages").exists():
        listed = ", ".join(answered) or "stages/"
        raise ConfigError(f"{run_dir} already has answers ({listed}), start a new run")
    inputs = input_paths(run_dir)
    if not filled(Path(inputs["scope"])):
        raise ConfigError(f"{inputs['scope']} is missing or empty: write what is under review and the diff command first")
    return inputs


def _fresh_prompts_dir(run_dir: Path) -> Path:
    prompts_dir = run_dir / "prompts" / AGENTS
    prompts_dir.mkdir(parents=True, exist_ok=True)
    for stale in prompts_dir.glob("*.md"):
        stale.unlink()
    (run_dir / AGENTS).mkdir(exist_ok=True)
    return prompts_dir


def _manifest_agent(agent: dict, prompt: Path) -> dict:
    return {
        "name": agent["name"],
        LENSES: agent[LENSES],
        "executor": agent[VENDOR],
        "requested_model": agent[MODEL],
        EFFORT: agent[EFFORT],
        "prompt": f"prompts/agents/{prompt.name}",
        "prompt_hash": digest(prompt),
    }
