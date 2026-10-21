#!/usr/bin/env python3
"""Сверяет манифест плагина, маркетплейс и CHANGELOG: одно имя, semver-версия, раздел версии в CHANGELOG."""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SEMVER = re.compile(r"\d+\.\d+\.\d+")


def main() -> int:
    plugin = json.loads((ROOT / ".claude-plugin/plugin.json").read_text())
    market = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text())
    changelog = (ROOT / "CHANGELOG.md").read_text()
    errors = []
    version = plugin.get("version", "")
    if not SEMVER.fullmatch(version):
        errors.append(f"plugin.json: version {version!r} is not semver")
    entries = [p for p in market.get("plugins", []) if p.get("name") == plugin.get("name")]
    if len(entries) != 1:
        errors.append(f"marketplace.json: expected one entry named {plugin.get('name')!r}")
    elif entries[0].get("source") != "./":
        errors.append("marketplace.json: the plugin source must be the repository root './'")
    if f"## [{version}]" not in changelog:
        errors.append(f"CHANGELOG.md: no section for version {version}")
    for skill in sorted((ROOT / "skills").glob("*/SKILL.md")):
        head = skill.read_text().split("---", 2)[1]
        name = re.search(r"^name:\s*(\S+)", head, re.M)
        if not name or name.group(1) != skill.parent.name:
            errors.append(f"{skill.relative_to(ROOT)}: name must match the directory")
    print(json.dumps({"ok": not errors, "version": version, "errors": errors}, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
