"""What the shape of cross-review.json cannot say: known tier, model names, roster names, lenses that exist,
stage values and repository prompts that replace a plugin one."""

from __future__ import annotations

from pathlib import Path

from crossreview.errors import ConfigError
from crossreview.layers import NAME, PLUGIN, repo_layer, resolve
from crossreview.models import SIDES, parse_model

GROUP_BY = ("dir", "source")
GROUP_BY_CHOICES = "|".join(GROUP_BY)
# verify answers share agents/ with the finders as verify-<group>.json, so a roster entry cannot take the name
RESERVED_NAMES = ("verify",)
TIERS = "tiers"


def validate(cfg: dict, repo: Path | None) -> None:
    """Every rule of a merged configuration; the first broken one raises ConfigError."""
    _validate_tiers(cfg)
    _validate_roster(cfg["roster"], repo)
    _validate_stages(cfg["stages"])
    _validate_prompts(repo)


def _validate_tiers(cfg: dict) -> None:
    tiers = cfg[TIERS]
    if cfg["tier"] not in tiers:
        known = ", ".join(tiers)
        raise ConfigError(f"tier: {cfg['tier']!r} is not one of the tiers: {known}")
    for name, tier in tiers.items():
        for vendor in SIDES:
            parse_model(tier[vendor], vendor, f"{TIERS}.{name}.{vendor}")
        for vendor in SIDES:
            parse_model(tier["moderators"][vendor], vendor, f"{TIERS}.{name}.moderators.{vendor}")


def _validate_roster(roster: list, repo: Path | None) -> None:
    if not roster:
        raise ConfigError("roster: empty, list at least one entry")
    seen: set[str] = set()
    for index, entry in enumerate(roster):
        where = f"roster[{index}]"
        _validate_name(entry["name"], where, seen)
        seen.add(entry["name"])
        _validate_lenses(entry["lenses"], where, repo)


def _validate_name(name: str, where: str, seen: set[str]) -> None:
    if not NAME.fullmatch(name):
        raise ConfigError(f"{where}.name: {name!r}, use lowercase letters, digits and hyphens")
    if name in RESERVED_NAMES:
        raise ConfigError(f"{where}.name: {name!r} is reserved for the verify stage, pick another name")
    if name in seen:
        raise ConfigError(f"{where}.name: {name!r} is already in the roster")


def _validate_lenses(lenses: list, where: str, repo: Path | None) -> None:
    if not lenses:
        raise ConfigError(f"{where}.lenses: empty, give the entry at least one lens")
    for lens in lenses:
        try:
            resolve(repo, "lenses", lens)
        except ConfigError as error:
            raise ConfigError(f"{where}.lenses: {error}") from None


def _validate_stages(stages: dict) -> None:
    if stages["verify_groups"] < 1:
        raise ConfigError(f"stages.verify_groups: {stages['verify_groups']}, expected 1 or more")
    if stages["verify_group_by"] not in GROUP_BY:
        raise ConfigError(f"stages.verify_group_by: {stages['verify_group_by']!r}, expected one of {GROUP_BY_CHOICES}")


def _validate_prompts(repo: Path | None) -> None:
    layer = repo_layer(repo)
    known = {prompt.stem for prompt in (PLUGIN / "prompts").glob("*.md")}
    for own in sorted(layer.glob("prompts/*.md")) if layer else []:
        if own.stem not in known:
            expected = ", ".join(sorted(known))
            raise ConfigError(f"{own}: no plugin prompt {own.stem!r} to replace, expected one of {expected}")
