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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from crossreview import cli  # noqa: E402
from crossreview.config_load import load_config  # noqa: E402
from crossreview.describe import describe  # noqa: E402
from crossreview.errors import ConfigError  # noqa: E402
from crossreview.jsonio import emit, fail  # noqa: E402
from crossreview.models import EFFORT, MODEL  # noqa: E402
from crossreview.selection import select, side_models  # noqa: E402
from crossreview.setup_layer import setup  # noqa: E402

REPO_HELP = "root of the reviewed repository"
MODERATORS = "moderators"


def main(argv: list[str] | None = None) -> int:
    """Run one command and print its JSON line; a configuration error is {"error": ...} and exit 1."""
    root, show = _parser()
    args = root.parse_args(argv)
    if args.command == "show":
        cli.check_model_pair(show, args)
    try:
        payload = _run(args)
    except ConfigError as error:
        return fail(str(error))
    emit(payload)
    return 0


def printable(models: dict) -> dict:
    """{model, effort} as the session reads it: a claude model under `alias`, a codex model under `model`."""
    claude, codex = models["claude"], models["codex"]
    return {
        "claude": {"alias": claude[MODEL], EFFORT: claude[EFFORT]},
        "codex": {MODEL: codex[MODEL], EFFORT: codex[EFFORT]},
    }


def _parser() -> tuple[argparse.ArgumentParser, argparse.ArgumentParser]:
    root = cli.parser(__doc__)
    commands = root.add_subparsers(dest="command", metavar="{show,setup,sides}", required=True)
    show = commands.add_parser("show", help="print the resolved configuration")
    show.add_argument("repo", help=REPO_HELP)
    cli.add_model_options(show)
    commands.add_parser("setup", help="prepare .cross-review/ for the repository's owner").add_argument(
        "repo", help=REPO_HELP)
    sides = commands.add_parser("sides", help="print the models of the debate sides and moderators")
    sides.add_argument("run_dir", help="a cross-review run or an external run with findings.json")
    sides.add_argument("--repo", help="the repository whose configured tier names the moderators of an external run; "
                                      "default: the run's workdir, else the current directory")
    return root, show


def _run(args: argparse.Namespace) -> dict:
    if args.command == "sides":
        return _sides(args)
    repo = Path(args.repo).resolve()
    if args.command == "setup":
        return setup(repo)
    cfg = load_config(repo)
    return describe(repo, cfg, select(cfg, args.tier, args.claude, args.codex))


def _sides(args: argparse.Namespace) -> dict:
    repo = Path(args.repo).resolve() if args.repo else None
    models = side_models(Path(args.run_dir).resolve(), repo)
    return {
        "source": models["source"],
        **printable(models),
        MODERATORS: printable(models[MODERATORS]),
        "moderators_source": models["moderators_source"],
    }


if __name__ == "__main__":
    sys.exit(main())
