#!/usr/bin/env python3
"""The two-layer configuration of a cross-review: the plugin defaults and the reviewed repository's .cross-review/.

The plugin layer is assets/config/ of the skill: cross-review.json (tiers of models, the roster, the stages),
prompts/<name>.md, lenses/<name>.md and schemas/<name>.json. A repository overrides it with .cross-review/ at
its root: cross-review.json is merged over the defaults (objects recursively, arrays replaced whole, an unknown
key is an error); prompts/<name>.md replaces the plugin prompt of that name; lenses/<name>.md replaces a plugin
lens or adds a new one. Schemas come from the plugin only: the scripts read the answers by their shape. The
reviewed branch owns .cross-review/, so any symlink there is a configuration error, whether it leads outside the
repository or to a file inside it.

  show <repo> [--tier T | --claude M --codex M]   the resolved configuration as one JSON line
  setup <repo>                                    prepare <repo>/.cross-review/ for its owner (below)
  sides <run_dir> [--repo R]                      the models of the debate: both sides and the two moderators

A model is <model>[:<effort>], effort one of low|medium|high|xhigh|max, high when omitted. A claude model is
an Agent alias: opus|sonnet|fable|haiku. Synthesis and verify run on the claude model of the pair. A
configuration error prints {"error": ...} and exits 1.

`setup` writes what the owner tunes from: a minimal cross-review.json (`tier` only) and README.md, each only when
missing, and defaults/ — a copy of the plugin layer that no script reads — refreshed on every call, stale files
removed. Nothing the owner wrote is overwritten, and a file copied from defaults/ into prompts/ or lenses/ is the
only thing that starts to replace the plugin, so whatever the owner did not touch keeps following the plugin.

`sides` keeps the debate on the models of the review. A cross-review run has them in manifest.json (`models`,
`moderators`). An external run has only findings.json `sources.agents[]`: the claude model id becomes its Agent
alias (claude-opus-5-5 → opus), and the moderators come from tiers.<tier>.moderators of the configured tier of
--repo (default: the run's workdir, else the current directory).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SKILL = Path(__file__).resolve().parent.parent
PLUGIN = SKILL / "assets" / "config"
REPO_DIR = ".cross-review"
CONFIG_FILE = "cross-review.json"
DEFAULTS_DIR = "defaults"
SETUP_README = SKILL / "assets" / "templates" / "setup-README.md"

EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_EFFORT = "high"
CLAUDE_MODELS = ("opus", "sonnet", "fable", "haiku")
CODEX_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
# a claude model id: claude-opus-5-5, claude-3-5-sonnet-20241022, claude-fable-5-1[1m]
CLAUDE_ID = re.compile(r"claude-(?:[0-9.]+-)*(" + "|".join(CLAUDE_MODELS) + r")(?:-[A-Za-z0-9.-]*)?(?:\[[a-z0-9]+\])?")
NAME = re.compile(r"[a-z0-9][a-z0-9-]*")
GROUP_BY = ("dir", "source")
# verify answers share agents/ with the finders as verify-<group>.json, so a roster entry cannot take the name
RESERVED_NAMES = ("verify",)
KINDS = {"prompts": "prompt", "lenses": "lens", "schemas": "schema"}

# the shape of cross-review.json: "*" stands for any key, a one-item list for an array of that item
TIER = {"claude": str, "codex": str, "moderators": {"claude": str, "codex": str}}
SHAPE = {
    "tier": str,
    "tiers": {"*": TIER},
    "roster": [{"name": str, "lenses": [str]}],
    "stages": {"synthesis": bool, "verify": bool, "verify_groups": int, "verify_group_by": str},
}
TYPE_NAMES = {str: "a string", bool: "true or false", int: "an integer"}


class ConfigError(Exception):
    """A configuration the scripts cannot run with; the message names the file and the key."""


def key_path(where: str, key: object) -> str:
    return f"{where}.{key}" if where else str(key)


def check_shape(value: object, shape: Any, where: str, complete: bool) -> None:
    """Check value against SHAPE. complete=False lets a layer leave keys out; an array item is always complete,
    because an array replaces the default whole."""
    if isinstance(shape, dict):
        if not isinstance(value, dict):
            raise ConfigError(f"{where or 'the file'}: expected an object")
        if "*" in shape:
            for k, v in value.items():
                check_shape(v, shape["*"], key_path(where, k), complete)
            return
        for k, v in value.items():
            if k not in shape:
                raise ConfigError(f"{key_path(where, k)}: unknown key, expected one of {', '.join(shape)}")
            check_shape(v, shape[k], key_path(where, k), complete)
        missing = [k for k in shape if k not in value]
        if complete and missing:
            raise ConfigError(f"{where or 'the file'}: missing {', '.join(missing)}")
    elif isinstance(shape, list):
        if not isinstance(value, list):
            raise ConfigError(f"{where}: expected an array")
        for i, item in enumerate(value):
            check_shape(item, shape[0], f"{where}[{i}]", True)
    elif not isinstance(value, shape) or (shape is int and isinstance(value, bool)):
        raise ConfigError(f"{where}: expected {TYPE_NAMES[shape]}")


def merge(base: object, over: object) -> object:
    """Objects merge key by key, anything else — arrays included — is replaced by the override."""
    if isinstance(base, dict) and isinstance(over, dict):
        return {**base, **{k: merge(base[k], v) if k in base else v for k, v in over.items()}}
    return over


def parse_model(spec: str, vendor: str, where: str = "") -> dict:
    """`<model>[:<effort>]` → {model, effort}; a claude model must be an Agent alias."""
    prefix = f"{where}: " if where else ""
    model, colon, effort = spec.partition(":")
    if not colon:
        effort = DEFAULT_EFFORT
    if effort not in EFFORTS:
        raise ConfigError(f"{prefix}effort {effort!r} in {spec!r}, expected one of {'|'.join(EFFORTS)}")
    if vendor == "claude" and model not in CLAUDE_MODELS:
        raise ConfigError(f"{prefix}claude model {model!r}, expected an Agent alias: {'|'.join(CLAUDE_MODELS)}")
    if vendor == "codex" and not CODEX_MODEL.fullmatch(model):
        raise ConfigError(f"{prefix}codex model {model!r} is not a model name")
    return {"model": model, "effort": effort}


def symlink_error(path: Path) -> ConfigError:
    return ConfigError(f"{path}: a symlink in the repository's {REPO_DIR}/, remove it")


def confined(repo: Path, path: Path) -> Path:
    """path itself, unless it or a directory between it and the repository is a symlink: the reviewed branch owns
    .cross-review/, and its links must not make the scripts send a local file — .git/config, an ignored .env — to a
    model or write outside the run directory, wherever the link points."""
    root = Path(repo)
    try:
        parts = Path(path).relative_to(root).parts
    except ValueError:
        raise ConfigError(f"{path}: not inside the repository {root}") from None
    current = root
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise symlink_error(current)
    if ".." in parts or not path.resolve().is_relative_to(root.resolve()):
        raise ConfigError(f"{path}: leads outside the repository {root}")
    return path


def no_symlinks(root: Path) -> None:
    """An error on the first symlink at or below root. A prior run is handed to the models as a directory to read,
    so a link anywhere in it — the run itself, its report.md, a file under stages/ — would lead them to a file the
    link points at, wherever that is."""
    if root.is_symlink():
        raise symlink_error(root)
    for dirpath, dirnames, filenames in os.walk(root):
        for name in sorted([*dirnames, *filenames]):
            path = Path(dirpath) / name
            if path.is_symlink():
                raise symlink_error(path)


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
    if kind == "schemas":
        path = PLUGIN / kind / f"{name}.json"
    else:
        layer = repo_layer(repo)
        own = layer / kind / f"{name}.md" if layer else None
        if own is not None and own.is_file():
            return confined(repo, own)
        path = PLUGIN / kind / f"{name}.md"
    if not path.is_file():
        where = f"{REPO_DIR}/{kind}/ or the plugin" if kind != "schemas" else "the plugin"
        raise ConfigError(f"no {KINDS[kind]} {name!r} in {where}")
    return path


def layer_of(path: Path) -> str:
    return "plugin" if PLUGIN in path.parents else "repo"


def strip_front_matter(text: str) -> str:
    """A prompt or lens without its leading `---` front matter; text without one is returned as is."""
    if text.startswith("---\n"):
        end = text.find("\n---\n", 3)
        if end != -1:
            return text[end + 5:].lstrip("\n")
    return text


def body(repo: Path | None, kind: str, name: str) -> str:
    """The text of a prompt or lens as the scripts use it: resolved through both layers, front matter dropped."""
    return strip_front_matter(resolve(repo, kind, name).read_text()).strip()


PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")


def render(text: str, values: dict, where: str) -> str:
    """Replace every {{NAME}} with values[NAME] in one pass. A placeholder with no value is an error naming
    `where` and the placeholder; a value is never re-scanned, so model text quoting {{x}} stays as it is."""
    missing: list[str] = []

    def sub(m: re.Match) -> str:
        if m.group(1) not in values:
            missing.append(m.group(0))
            return m.group(0)
        return str(values[m.group(1)])

    out = PLACEHOLDER.sub(sub, text)
    if missing:
        raise ConfigError(f"{where}: unknown placeholder {missing[0]}, expected one of "
                          f"{', '.join('{{' + k + '}}' for k in values)}")
    return out


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise ConfigError(f"{path}: not valid JSON: {e}") from None


def load_config(repo: Path | None) -> dict:
    """The plugin defaults with <repo>/.cross-review/cross-review.json merged over them, validated."""
    if repo is not None and not Path(repo).is_dir():
        raise ConfigError(f"{repo}: no such directory")
    defaults_file = PLUGIN / CONFIG_FILE
    cfg = read_json(defaults_file)
    blame = defaults_file
    try:
        check_shape(cfg, SHAPE, "", True)
    except ConfigError as e:
        raise ConfigError(f"{defaults_file}: {e}") from None
    layer = repo_layer(repo)
    own_file = layer / CONFIG_FILE if layer else None
    if own_file is not None and own_file.is_file():
        own = read_json(confined(repo, own_file))
        try:
            check_shape(own, SHAPE, "", False)
        except ConfigError as e:
            raise ConfigError(f"{own_file}: {e}") from None
        cfg = merge(cfg, own)
        blame = own_file
    try:
        check_shape(cfg, SHAPE, "", True)
        validate(cfg, repo)
    except ConfigError as e:
        raise ConfigError(f"{blame}: {e}") from None
    return cfg


def validate(cfg: dict, repo: Path | None) -> None:
    """What the shape cannot say: known tier, model names, roster names, lenses that exist, stage values."""
    if cfg["tier"] not in cfg["tiers"]:
        raise ConfigError(f"tier: {cfg['tier']!r} is not one of the tiers: {', '.join(cfg['tiers'])}")
    for name, tier in cfg["tiers"].items():
        where = f"tiers.{name}"
        parse_model(tier["claude"], "claude", f"{where}.claude")
        parse_model(tier["codex"], "codex", f"{where}.codex")
        parse_model(tier["moderators"]["claude"], "claude", f"{where}.moderators.claude")
        parse_model(tier["moderators"]["codex"], "codex", f"{where}.moderators.codex")
    if not cfg["roster"]:
        raise ConfigError("roster: empty, list at least one entry")
    seen: set[str] = set()
    for i, entry in enumerate(cfg["roster"]):
        where = f"roster[{i}]"
        if not NAME.fullmatch(entry["name"]):
            raise ConfigError(f"{where}.name: {entry['name']!r}, use lowercase letters, digits and hyphens")
        if entry["name"] in RESERVED_NAMES:
            raise ConfigError(f"{where}.name: {entry['name']!r} is reserved for the verify stage, pick another name")
        if entry["name"] in seen:
            raise ConfigError(f"{where}.name: {entry['name']!r} is already in the roster")
        seen.add(entry["name"])
        if not entry["lenses"]:
            raise ConfigError(f"{where}.lenses: empty, give the entry at least one lens")
        for lens in entry["lenses"]:
            try:
                resolve(repo, "lenses", lens)
            except ConfigError as e:
                raise ConfigError(f"{where}.lenses: {e}") from None
    stages = cfg["stages"]
    if stages["verify_groups"] < 1:
        raise ConfigError(f"stages.verify_groups: {stages['verify_groups']}, expected 1 or more")
    if stages["verify_group_by"] not in GROUP_BY:
        raise ConfigError(f"stages.verify_group_by: {stages['verify_group_by']!r}, expected one of {'|'.join(GROUP_BY)}")
    layer = repo_layer(repo)
    known = {p.stem for p in (PLUGIN / "prompts").glob("*.md")}
    for own in sorted(layer.glob("prompts/*.md")) if layer else []:
        if own.stem not in known:
            raise ConfigError(f"{own}: no plugin prompt {own.stem!r} to replace, expected one of {', '.join(sorted(known))}")


def select(cfg: dict, tier: str | None = None, claude: str | None = None, codex: str | None = None) -> dict:
    """The models of this run: a tier by name (the configured one by default) or a one-off claude/codex pair.
    Moderators always come from a tier — with a pair, from the configured one."""
    pair = claude is not None or codex is not None
    if pair and (claude is None or codex is None or tier is not None):
        raise ValueError("a one-off pair needs both claude and codex and no tier")
    name = tier or cfg["tier"]
    if name not in cfg["tiers"]:
        raise ConfigError(f"no tier {name!r}, expected one of {', '.join(cfg['tiers'])}")
    spec = cfg["tiers"][name]
    if claude is not None and codex is not None:
        models = {"claude": parse_model(claude, "claude", "--claude"), "codex": parse_model(codex, "codex", "--codex")}
    else:
        models = {side: parse_model(spec[side], side) for side in ("claude", "codex")}
    return {
        "tier": name,
        "pair": pair,
        **models,
        "moderators": {side: parse_model(spec["moderators"][side], side) for side in ("claude", "codex")},
    }


def roster_agents(cfg: dict, models: dict) -> list[dict]:
    """<name>-claude and <name>-codex for every roster entry, on the same lenses."""
    return [{"name": f"{entry['name']}-{side}", "vendor": side, **models[side], "lenses": entry["lenses"]}
            for entry in cfg["roster"] for side in ("claude", "codex")]


def describe(repo: Path | None, cfg: dict, models: dict) -> dict:
    """Everything `show` prints: layers, the agents, the stage model, the moderators and where each file comes from."""
    layer = repo_layer(repo)
    own_file = layer / CONFIG_FILE if layer else None
    layers: list[dict[str, Any]] = [{"name": "plugin", "dir": str(PLUGIN)}]
    if layer:
        layers.append({"name": "repo", "dir": str(layer), "config": bool(own_file and own_file.is_file())})

    def where(kind: str, name: str) -> dict:
        path = resolve(repo, kind, name)
        return {"path": str(path), "layer": layer_of(path)}

    lenses = sorted({lens for entry in cfg["roster"] for lens in entry["lenses"]})
    return {
        "layers": layers,
        "tier": models["tier"],
        "pair": models["pair"],
        "claude": models["claude"],
        "codex": models["codex"],
        "agents": roster_agents(cfg, models),
        "stages": {**cfg["stages"], **models["claude"]},
        "moderators": models["moderators"],
        "prompts": {p.stem: where("prompts", p.stem) for p in sorted((PLUGIN / "prompts").glob("*.md"))},
        "lenses": {lens: where("lenses", lens) for lens in lenses},
        "schemas": {p.stem: str(p) for p in sorted((PLUGIN / "schemas").glob("*.json"))},
    }


def claude_alias(model: str) -> str:
    """The Agent alias of a claude model: an alias as is, a model id by its family (claude-opus-5-5 → opus)."""
    if model in CLAUDE_MODELS:
        return model
    m = CLAUDE_ID.fullmatch(model)
    if m is None:
        raise ConfigError(f"claude model {model!r} has no Agent alias, expected {'|'.join(CLAUDE_MODELS)} or claude-<alias>-…")
    return m.group(1)


def agent_model(agent: dict, vendor: str) -> dict:
    """{model, effort} one review agent ran on: the actual model first, the requested one when that is missing or
    has no alias."""
    effort = agent.get("effort") or DEFAULT_EFFORT
    if effort not in EFFORTS:
        raise ConfigError(f"agent {agent.get('name')!r}: effort {effort!r}, expected one of {'|'.join(EFFORTS)}")
    names = [m for m in (agent.get("actual_model"), agent.get("requested_model")) if isinstance(m, str) and m]
    if not names:
        raise ConfigError(f"agent {agent.get('name')!r} names no model")
    if vendor == "codex":
        if not CODEX_MODEL.fullmatch(names[0]):
            raise ConfigError(f"agent {agent.get('name')!r}: codex model {names[0]!r} is not a model name")
        return {"model": names[0], "effort": effort}
    for name in names:
        try:
            return {"model": claude_alias(name), "effort": effort}
        except ConfigError:
            continue
    raise ConfigError(f"agent {agent.get('name')!r}: claude model {names[0]!r} has no Agent alias, "
                      f"expected {'|'.join(CLAUDE_MODELS)} or claude-<alias>-…")


def round_models(agents: list, where: str) -> dict:
    """{claude, codex} of a round from its agents: the model most of a vendor's healthy agents ran on."""
    out = {}
    for vendor in ("claude", "codex"):
        mine = [a for a in agents if isinstance(a, dict) and a.get("executor") == vendor]
        if not mine:
            raise ConfigError(f"{where}: no {vendor} agent, the debate needs both sides")
        healthy = [a for a in mine if not a.get("degraded")] or mine
        counts = Counter(json.dumps(agent_model(a, vendor), sort_keys=True) for a in healthy)
        out[vendor] = json.loads(counts.most_common(1)[0][0])
    return out


def manifest_models(manifest: dict, key: str) -> dict | None:
    """{claude, codex} under manifest[key] ("models" or "moderators"), validated; None when the key is absent."""
    group = manifest.get(key)
    if group is None:
        return None
    out = {}
    for vendor in ("claude", "codex"):
        entry = group.get(vendor) if isinstance(group, dict) else None
        if not isinstance(entry, dict) or not isinstance(entry.get("model"), str) or not isinstance(entry.get("effort"), str):
            raise ConfigError(f"manifest.json {key}.{vendor}: expected {{model, effort}}")
        out[vendor] = parse_model(f"{entry['model']}:{entry['effort']}", vendor, f"manifest.json {key}.{vendor}")
    return out


def side_models(run_dir: Path, repo: Path | None = None) -> dict:
    """The models of the debate: both sides on the review's own models, and the two independent moderators.
    A cross-review run reads both from manifest.json; an external run reads the sides from findings.json
    `sources.agents[]` and the moderators from the configured tier of repo (the manifest's workdir by default)."""
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text())
    except (OSError, ValueError):
        manifest = {}
    manifest = manifest if isinstance(manifest, dict) else {}
    sides = manifest_models(manifest, "models")
    source = "manifest"
    if sides is None:
        findings_file = run_dir / "findings.json"
        if not findings_file.is_file():
            raise ConfigError(f"{run_dir}: no models in manifest.json and no findings.json, finish the review run first")
        findings = read_json(findings_file)
        agents = ((findings.get("sources") or {}).get("agents") or []) if isinstance(findings, dict) else []
        sides, source = round_models(agents, str(findings_file)), "findings"
    moderators = manifest_models(manifest, "moderators")
    moderators_source = "manifest"
    if moderators is None:
        if repo is None:
            workdir = manifest.get("workdir")
            repo = Path(workdir) if isinstance(workdir, str) and Path(workdir).is_dir() else Path.cwd()
        cfg = load_config(repo)
        moderators, moderators_source = select(cfg)["moderators"], f"config:{cfg['tier']}"
    return {"source": source, **sides, "moderators": moderators, "moderators_source": moderators_source}


def printable(models: dict) -> dict:
    """{model, effort} as the session reads it: a claude model under `alias`, a codex model under `model`."""
    claude, codex = models["claude"], models["codex"]
    return {"claude": {"alias": claude["model"], "effort": claude["effort"]},
            "codex": {"model": codex["model"], "effort": codex["effort"]}}


def setup(repo: Path) -> dict:
    """Prepare <repo>/.cross-review/: cross-review.json and README.md when missing, defaults/ refreshed from the plugin."""
    if not repo.is_dir():
        raise ConfigError(f"{repo}: no such directory")
    layer = confined(repo, repo / REPO_DIR)
    tier = json.loads((PLUGIN / CONFIG_FILE).read_text())["tier"]
    own = {CONFIG_FILE: json.dumps({"tier": tier}, indent=2) + "\n", "README.md": SETUP_README.read_text()}
    created, kept = [], []
    for name, text in own.items():
        path = confined(repo, layer / name)
        if path.exists():
            kept.append(name)
            continue
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
        created.append(name)

    defaults = confined(repo, layer / DEFAULTS_DIR)
    wanted = {CONFIG_FILE: PLUGIN / CONFIG_FILE, "LICENSE": PLUGIN / "LICENSE"}
    for kind in ("prompts", "lenses"):
        wanted.update({f"{kind}/{p.name}": p for p in sorted((PLUGIN / kind).glob("*.md"))})
    changed = []
    for kind in ("prompts", "lenses"):
        for stale in sorted((defaults / kind).glob("*.md")) if (defaults / kind).is_dir() else []:
            if f"{kind}/{stale.name}" not in wanted:
                confined(repo, stale).unlink()
                changed.append(f"-{kind}/{stale.name}")
    for rel, src in wanted.items():
        path = confined(repo, defaults / rel)
        text = src.read_text()
        if not path.is_file() or path.read_text() != text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            changed.append(rel)
    return {"dir": str(layer), "created": created, "kept": kept, "defaults": str(defaults), "defaults_changed": changed}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", metavar="{show,setup,sides}", required=True)
    show = sub.add_parser("show", help="print the resolved configuration")
    show.add_argument("repo", help="root of the reviewed repository")
    show.add_argument("--tier", help="a tier from the configuration; default: its `tier`")
    show.add_argument("--claude", metavar="MODEL", help="one-off claude model, e.g. sonnet:high; needs --codex")
    show.add_argument("--codex", metavar="MODEL", help="one-off codex model, e.g. gpt-6-sol:high; needs --claude")
    sub.add_parser("setup", help="prepare .cross-review/ for the repository's owner").add_argument(
        "repo", help="root of the reviewed repository")
    sides = sub.add_parser("sides", help="print the models of the debate sides and moderators")
    sides.add_argument("run_dir", help="a cross-review run or an external run with findings.json")
    sides.add_argument("--repo", help="the repository whose configured tier names the moderators of an external run; "
                                      "default: the run's workdir, else the current directory")
    args = ap.parse_args(argv)
    if args.command == "show" and (args.claude is None) != (args.codex is None):
        show.error("--claude and --codex go together")
    if args.command == "show" and args.claude is not None and args.tier is not None:
        show.error("--tier and a --claude/--codex pair exclude each other")

    try:
        if args.command == "sides":
            models = side_models(Path(args.run_dir).resolve(), Path(args.repo).resolve() if args.repo else None)
            print(json.dumps({"source": models["source"], **printable(models),
                              "moderators": printable(models["moderators"]),
                              "moderators_source": models["moderators_source"]}, ensure_ascii=False))
            return 0
        repo = Path(args.repo).resolve()
        if args.command == "setup":
            out = setup(repo)
        else:
            cfg = load_config(repo)
            out = describe(repo, cfg, select(cfg, args.tier, args.claude, args.codex))
    except ConfigError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
