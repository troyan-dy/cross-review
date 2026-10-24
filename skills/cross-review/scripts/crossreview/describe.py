"""What `cross_config.py show` prints: the resolved configuration and where each of its files comes from."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from crossreview.layers import CONFIG_FILE, PLUGIN, layer_of, repo_layer, resolve
from crossreview.selection import roster_agents

MODERATORS = "moderators"
LENSES = "lenses"
PROMPTS = "prompts"


def describe(repo: Path | None, cfg: dict, models: dict) -> dict:
    """Everything `show` prints: layers, the agents, the stage model, the moderators and where each file comes from."""
    lenses = sorted({lens for entry in cfg["roster"] for lens in entry[LENSES]})
    prompts = [prompt.stem for prompt in sorted((PLUGIN / PROMPTS).glob("*.md"))]
    schemas = sorted((PLUGIN / "schemas").glob("*.json"))
    return {
        "layers": _layers(repo),
        **{key: models[key] for key in ("tier", "pair", "claude", "codex")},
        "agents": roster_agents(cfg, models),
        "stages": {**cfg["stages"], **models["claude"]},
        MODERATORS: models[MODERATORS],
        PROMPTS: {prompt: _where(repo, PROMPTS, prompt) for prompt in prompts},
        LENSES: {lens: _where(repo, LENSES, lens) for lens in lenses},
        "schemas": {schema.stem: str(schema) for schema in schemas},
    }


def _layers(repo: Path | None) -> list[dict[str, Any]]:
    layers: list[dict[str, Any]] = [{"name": "plugin", "dir": str(PLUGIN)}]
    layer = repo_layer(repo)
    if layer:
        has_config = (layer / CONFIG_FILE).is_file()
        layers.append({"name": "repo", "dir": str(layer), "config": has_config})
    return layers


def _where(repo: Path | None, kind: str, name: str) -> dict:
    path = resolve(repo, kind, name)
    return {"path": str(path), "layer": layer_of(path)}
