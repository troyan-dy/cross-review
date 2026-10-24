"""The models a review round actually ran on, from the agents its findings.json lists."""

from __future__ import annotations

import json
from collections import Counter

from crossreview import models
from crossreview.errors import ConfigError


def agent_model(agent: dict, vendor: str) -> dict:
    """{model, effort} one review agent ran on: the actual model first, the requested one when that is missing or
    has no alias."""
    name = agent.get("name")
    effort = _agent_effort(agent, name)
    candidates = (agent.get("actual_model"), agent.get("requested_model"))
    names = [model for model in candidates if model and isinstance(model, str)]
    if not names:
        raise ConfigError(f"agent {name!r} names no model")
    if vendor == models.CODEX and not models.CODEX_MODEL.fullmatch(names[0]):
        raise ConfigError(f"agent {name!r}: codex model {names[0]!r} is not a model name")
    model = names[0] if vendor == models.CODEX else _first_alias(name, names)
    return {models.MODEL: model, models.EFFORT: effort}


def round_models(agents: list, where: str) -> dict:
    """{claude, codex} of a round from its agents: the model most of a vendor's healthy agents ran on."""
    return {vendor: _usual_model(agents, vendor, where) for vendor in models.SIDES}


def _agent_effort(agent: dict, name: object) -> str:
    effort = agent.get(models.EFFORT) or models.DEFAULT_EFFORT
    if effort not in models.EFFORTS:
        raise ConfigError(f"agent {name!r}: effort {effort!r}, expected one of {models.EFFORT_CHOICES}")
    return effort


def _first_alias(name: object, names: list[str]) -> str:
    for model in names:
        family = models.CLAUDE_ID.fullmatch(model)
        if model in models.CLAUDE_MODELS or family is not None:
            return models.claude_alias(model)
    raise ConfigError(f"agent {name!r}: claude model {names[0]!r} has no Agent alias, {models.NO_ALIAS}")


def _usual_model(agents: list, vendor: str, where: str) -> dict:
    mine = [
        agent for agent in agents
        if isinstance(agent, dict) and agent.get("executor") == vendor
    ]
    if not mine:
        raise ConfigError(f"{where}: no {vendor} agent, the debate needs both sides")
    healthy = [agent for agent in mine if not agent.get("degraded")] or mine
    counts = Counter(_model_key(agent_model(agent, vendor)) for agent in healthy)
    usual, _ = counts.most_common(1)[0]
    return json.loads(usual)


def _model_key(model: dict) -> str:
    return json.dumps(model, sort_keys=True)
