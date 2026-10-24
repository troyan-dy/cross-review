"""Where a prompt, lens or schema comes from: the plugin layer or the reviewed repository's .cross-review/."""

from __future__ import annotations

import hashlib
import json
import re
import types
from pathlib import Path

from crossreview.errors import ConfigError
from crossreview.links import REPO_DIR, confined

SKILL = Path(__file__).resolve().parents[2]
PLUGIN = SKILL / "assets" / "config"
CONFIG_FILE = "cross-review.json"
SCHEMAS = "schemas"
KINDS = types.MappingProxyType({"prompts": "prompt", "lenses": "lens", SCHEMAS: "schema"})
NAME = re.compile(r"[a-z0-9][a-z0-9-]*")








def repo_layer(repo: Path | None) -> Path | None:
    """<repo>/.cross-review when the repository has one."""
    if repo is None:
        return None
    layer = Path(repo) / REPO_DIR
    return confined(repo, layer) if layer.is_dir() else None


def resolve(repo: Path | None, kind: str, name: str) -> Path:
    """The file a prompt, lens or schema comes from: the repository layer first, then the plugin.
    Schemas come from the plugin only."""
    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    if not NAME.fullmatch(name):
        raise ConfigError(f"{kind}: {name!r} is not a valid name, use lowercase letters, digits and hyphens")
    path = _layered(repo, kind, name)
    if not path.is_file():
        where = "the plugin" if kind == SCHEMAS else f"{REPO_DIR}/{kind}/ or the plugin"
        raise ConfigError(f"no {KINDS[kind]} {name!r} in {where}")
    return path


def plugin_schema(name: str) -> dict:
    """A schema of the plugin layer, parsed: the scripts read every answer by that shape."""
    return json.loads(resolve(None, SCHEMAS, name).read_text())


def layer_of(path: Path) -> str:
    """The layer a resolved file belongs to: plugin or repo."""
    return "plugin" if PLUGIN in path.parents else "repo"


def digest(path: Path) -> str:
    """The sha256 of a file, as the manifest records a prompt."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source(repo: Path, kind: str, name: str) -> dict:
    """Where a prompt, lens or schema came from, for the manifest."""
    path = resolve(repo, kind, name)
    return {
        "path": f"{kind}/{path.name}",
        "layer": layer_of(path),
        "file": str(path),
        "hash": digest(path),
    }


def _layered(repo: Path | None, kind: str, name: str) -> Path:
    if kind == SCHEMAS:
        return PLUGIN / kind / f"{name}.json"
    layer = repo_layer(repo)
    own = layer / kind / f"{name}.md" if layer else None
    if own is not None and own.is_file():
        return confined(repo, own)
    return PLUGIN / kind / f"{name}.md"
