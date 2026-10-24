#!/usr/bin/env python3
"""Сверяет манифест плагина, маркетплейс и CHANGELOG: одно имя, semver-версия, раздел версии в CHANGELOG."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEMVER = re.compile(r"\d+\.\d+\.\d+")
SKILL_NAME = re.compile(r"^name:\s*(\S+)", re.M)


def read_manifest(name: str) -> dict:
    return json.loads((ROOT / ".claude-plugin" / name).read_text())


def version_errors(version: str) -> list[str]:
    if SEMVER.fullmatch(version):
        return []
    return [f"plugin.json: version {version!r} is not semver"]


def market_errors(plugin_name: object) -> list[str]:
    plugins = read_manifest("marketplace.json").get("plugins", [])
    entries = [entry for entry in plugins if entry.get("name") == plugin_name]
    if len(entries) != 1:
        return [f"marketplace.json: expected one entry named {plugin_name!r}"]
    if entries[0].get("source") != "./":
        return ["marketplace.json: the plugin source must be the repository root './'"]
    return []


def changelog_errors(version: str) -> list[str]:
    if f"## [{version}]" in (ROOT / "CHANGELOG.md").read_text():
        return []
    return [f"CHANGELOG.md: no section for version {version}"]


def skill_errors() -> list[str]:
    errors = []
    for skill in sorted((ROOT / "skills").glob("*/SKILL.md")):
        head = skill.read_text().split("---", 2)[1]
        name = SKILL_NAME.search(head)
        if not name or name.group(1) != skill.parent.name:
            errors.append(f"{skill.relative_to(ROOT)}: name must match the directory")
    return errors


def main() -> int:
    plugin = read_manifest("plugin.json")
    version = plugin.get("version", "")
    errors = [
        *version_errors(version),
        *market_errors(plugin.get("name")),
        *changelog_errors(version),
        *skill_errors(),
    ]
    summary = {"ok": not errors, "version": version, "errors": errors}
    report = json.dumps(summary, ensure_ascii=False)
    sys.stdout.write(f"{report}\n")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
