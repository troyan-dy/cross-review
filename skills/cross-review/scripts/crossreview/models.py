"""Model specs of a cross-review: `<model>[:<effort>]`, the Agent alias of a claude model id, the models a run
recorded in its manifest."""

from __future__ import annotations

import re

from crossreview.errors import ConfigError

CLAUDE = "claude"
CODEX = "codex"
SIDES = (CLAUDE, CODEX)
MODEL = "model"
EFFORT = "effort"
EFFORTS = ("low", "medium", "high", "xhigh", "max")
EFFORT_CHOICES = "|".join(EFFORTS)
DEFAULT_EFFORT = "high"
CLAUDE_MODELS = ("opus", "sonnet", "fable", "haiku")
ALIAS_CHOICES = "|".join(CLAUDE_MODELS)
CODEX_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# a claude model id: claude-opus-5-5, claude-3-5-sonnet-20241022, claude-fable-5-1[1m]
CLAUDE_ID = re.compile(rf"claude-(?:[0-9.]+-)*({ALIAS_CHOICES})(?:-[A-Za-z0-9.-]*)?(?:\[[a-z0-9]+\])?")
NO_ALIAS = f"expected {ALIAS_CHOICES} or claude-<alias>-…"


def parse_model(spec: str, vendor: str, where: str = "") -> dict:
    """`<model>[:<effort>]` → {model, effort}; a claude model must be an Agent alias."""
    prefix = f"{where}: " if where else ""
    model, colon, effort = spec.partition(":")
    effort = effort if colon else DEFAULT_EFFORT
    if effort not in EFFORTS:
        raise ConfigError(f"{prefix}effort {effort!r} in {spec!r}, expected one of {EFFORT_CHOICES}")
    if vendor == CLAUDE and model not in CLAUDE_MODELS:
        raise ConfigError(f"{prefix}claude model {model!r}, expected an Agent alias: {ALIAS_CHOICES}")
    if vendor == CODEX and not CODEX_MODEL.fullmatch(model):
        raise ConfigError(f"{prefix}codex model {model!r} is not a model name")
    return {MODEL: model, EFFORT: effort}


def claude_alias(model: str) -> str:
    """The Agent alias of a claude model: an alias as is, a model id by its family (claude-opus-5-5 → opus)."""
    if model in CLAUDE_MODELS:
        return model
    family = CLAUDE_ID.fullmatch(model)
    if family is None:
        raise ConfigError(f"claude model {model!r} has no Agent alias, {NO_ALIAS}")
    return family.group(1)






def manifest_models(manifest: dict, key: str) -> dict | None:
    """{claude, codex} under manifest[key] ("models" or "moderators"), validated; None when the key is absent."""
    group = manifest.get(key)
    if group is None:
        return None
    return {vendor: _manifest_model(group, key, vendor) for vendor in SIDES}








def _manifest_model(group: object, key: str, vendor: str) -> dict:
    where = f"manifest.json {key}.{vendor}"
    entry = group.get(vendor) if isinstance(group, dict) else None
    if not isinstance(entry, dict):
        entry = {}
    model, effort = entry.get(MODEL), entry.get(EFFORT)
    if not isinstance(model, str) or not isinstance(effort, str):
        raise ConfigError(f"{where}: expected {{model, effort}}")
    return parse_model(f"{model}:{effort}", vendor, where)
