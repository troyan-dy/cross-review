"""`setup`: prepare <repo>/.cross-review/ for its owner.

It writes what the owner tunes from: a minimal cross-review.json (`tier` only) and README.md, each only when
missing, and defaults/ — a copy of the plugin layer that no script reads — refreshed on every call, stale files
removed.
"""

from __future__ import annotations

import json
from pathlib import Path

from crossreview.errors import ConfigError
from crossreview.jsonio import pretty_json
from crossreview.layers import CONFIG_FILE, PLUGIN, SKILL
from crossreview.links import REPO_DIR, confined

DEFAULTS_DIR = "defaults"
SETUP_README = SKILL / "assets" / "templates" / "setup-README.md"
COPIED_KINDS = ("prompts", "lenses")


def setup(repo: Path) -> dict:
    """Prepare <repo>/.cross-review/: cross-review.json and README.md when missing, defaults/ refreshed from the
    plugin."""
    if not repo.is_dir():
        raise ConfigError(f"{repo}: no such directory")
    layer = confined(repo, repo / REPO_DIR)
    created, kept = _write_owned(repo, layer)
    defaults = confined(repo, layer / DEFAULTS_DIR)
    changed = [*_remove_stale(repo, defaults), *_copy_wanted(repo, defaults)]
    return {
        "dir": str(layer),
        "created": created,
        "kept": kept,
        "defaults": str(defaults),
        "defaults_changed": changed,
    }


def _owned_files() -> dict[str, str]:
    defaults = json.loads((PLUGIN / CONFIG_FILE).read_text())
    minimal = pretty_json({"tier": defaults["tier"]}, ensure_ascii=True)
    return {CONFIG_FILE: minimal, "README.md": SETUP_README.read_text()}


def _write_owned(repo: Path, layer: Path) -> tuple[list[str], list[str]]:
    created: list[str] = []
    kept: list[str] = []
    for name, text in _owned_files().items():
        path = confined(repo, layer / name)
        if path.exists():
            kept.append(name)
        else:
            path.parent.mkdir(exist_ok=True)
            path.write_text(text)
            created.append(name)
    return created, kept


def _wanted_defaults() -> dict[str, Path]:
    wanted = {CONFIG_FILE: PLUGIN / CONFIG_FILE, "LICENSE": PLUGIN / "LICENSE"}
    for kind in COPIED_KINDS:
        sources = sorted((PLUGIN / kind).glob("*.md"))
        wanted.update({f"{kind}/{source.name}": source for source in sources})
    return wanted


def _remove_stale(repo: Path, defaults: Path) -> list[str]:
    wanted = _wanted_defaults()
    present = [
        (f"{kind}/{path.name}", path)
        for kind in COPIED_KINDS for path in sorted((defaults / kind).glob("*.md"))
    ]
    removed = []
    for rel, stale in present:
        if rel not in wanted:
            confined(repo, stale).unlink()
            removed.append(f"-{rel}")
    return removed


def _copy_wanted(repo: Path, defaults: Path) -> list[str]:
    copied = []
    for rel, source in _wanted_defaults().items():
        if _write_changed(confined(repo, defaults / rel), source.read_text()):
            copied.append(rel)
    return copied


def _write_changed(path: Path, text: str) -> bool:
    current = path.read_text() if path.is_file() else None
    if current == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return True
