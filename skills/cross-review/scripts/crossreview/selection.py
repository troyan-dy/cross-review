"""The models of a run: the review agents of a tier or a one-off pair, and the models of the debate."""

from __future__ import annotations

from pathlib import Path

from crossreview.agent_models import round_models
from crossreview.config_load import load_config
from crossreview.errors import ConfigError
from crossreview.jsonio import read_object
from crossreview.models import SIDES, manifest_models, parse_model
from crossreview.texts import read_json

MANIFEST = "manifest"
MODERATORS = "moderators"


def select(
    cfg: dict,
    tier: str | None = None,
    claude: str | None = None,
    codex: str | None = None,
) -> dict:
    """The models of this run: a tier by name (the configured one by default) or a one-off claude/codex pair.
    Moderators always come from a tier — with a pair, from the configured one."""
    pair = claude is not None or codex is not None
    if pair and (claude is None or codex is None or tier is not None):
        raise ValueError("a one-off pair needs both claude and codex and no tier")
    name = tier or cfg["tier"]
    spec = _tier_spec(cfg, name)
    if pair:
        models = {
            "claude": parse_model(claude, "claude", "--claude"),
            "codex": parse_model(codex, "codex", "--codex"),
        }
    else:
        models = {side: parse_model(spec[side], side) for side in SIDES}
    moderators = {side: parse_model(spec[MODERATORS][side], side) for side in SIDES}
    return {"tier": name, "pair": pair, **models, MODERATORS: moderators}


def roster_agents(cfg: dict, models: dict) -> list[dict]:
    """<name>-claude and <name>-codex for every roster entry, on the same lenses."""
    return [
        {
            "name": f"{entry['name']}-{side}",
            "vendor": side,
            **models[side],
            "lenses": entry["lenses"],
        }
        for entry in cfg["roster"] for side in SIDES
    ]


def side_models(run_dir: Path, repo: Path | None = None) -> dict:
    """The models of the debate: both sides on the review's own models, and the two independent moderators.
    A cross-review run reads both from manifest.json; an external run reads the sides from findings.json
    `sources.agents[]` and the moderators from the configured tier of repo (the manifest's workdir by default)."""
    manifest = read_object(run_dir / "manifest.json")
    sides = manifest_models(manifest, "models")
    source = MANIFEST
    if sides is None:
        sides, source = _findings_sides(run_dir), "findings"
    moderators = manifest_models(manifest, MODERATORS)
    moderators_source = MANIFEST
    if moderators is None:
        moderators, moderators_source = _tier_moderators(manifest, repo)
    return {"source": source, **sides, MODERATORS: moderators, "moderators_source": moderators_source}


def _tier_spec(cfg: dict, name: str) -> dict:
    if name not in cfg["tiers"]:
        known = ", ".join(cfg["tiers"])
        raise ConfigError(f"no tier {name!r}, expected one of {known}")
    return cfg["tiers"][name]


def _findings_sides(run_dir: Path) -> dict:
    findings_file = run_dir / "findings.json"
    if not findings_file.is_file():
        raise ConfigError(f"{run_dir}: no models in manifest.json and no findings.json, finish the review run first")
    findings = read_json(findings_file)
    sources = (findings.get("sources") or {}) if isinstance(findings, dict) else {}
    return round_models(sources.get("agents") or [], str(findings_file))


def _tier_moderators(manifest: dict, repo: Path | None) -> tuple[dict, str]:
    if repo is None:
        workdir = manifest.get("workdir")
        workdir_found = isinstance(workdir, str) and Path(workdir).is_dir()
        repo = Path(workdir) if workdir_found else Path.cwd()
    cfg = load_config(repo)
    return select(cfg)[MODERATORS], f"config:{cfg['tier']}"
