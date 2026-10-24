"""The panel sentence of the debate prompts: how many reviewers per vendor raised the findings, on which
models."""

from __future__ import annotations

import json
import types
from pathlib import Path

from crossreview.errors import ConfigError
from crossreview.models import SIDES

SIDE_NAME = types.MappingProxyType({"claude": "Claude", "codex": "Codex"})
NUMBER_WORDS = ("", "one", "two", "three", "four", "five", "six", "seven", "eight")


def run_panel(round_dir: Path) -> str:
    """The panel sentence of a finished run, from the agents its findings.json lists."""
    try:
        findings = json.loads((round_dir / "findings.json").read_text())
    except (OSError, ValueError):
        raise ConfigError(f"{round_dir}: no readable findings.json, finish the review run first") from None
    return panel_sentence((findings.get("sources") or {}).get("agents") or [])


def panel_sentence(agents: list[dict]) -> str:
    """How many reviewers per vendor and on which models, as the prompts open with it."""
    parts = [_side_part(agents, side) for side in SIDES]
    parts = [part for part in parts if part]
    if not parts:
        return "The panel's reviewers"
    total = len(agents)
    noun = "reviewer" if total == 1 else "reviewers"
    count = str(_count_word(total)).capitalize()
    sides = " and ".join(parts)
    return f"{count} {noun} — {sides} —"


def _side_part(agents: list[dict], side: str) -> str:
    names = [agent for agent in agents if agent.get("executor") == side]
    if not names:
        return ""
    models = sorted({model_name(agent) for agent in names})
    listed = ", ".join(model for model in models if model)
    return f"{_count_word(len(names))} {SIDE_NAME[side]} ({listed})"


def _count_word(count: int) -> object:
    return NUMBER_WORDS[count] if 0 < count < len(NUMBER_WORDS) else count


def model_name(agent: dict) -> str:
    """The model an agent ran on, else the one it was asked to run on."""
    return agent.get("actual_model") or agent.get("requested_model") or ""
