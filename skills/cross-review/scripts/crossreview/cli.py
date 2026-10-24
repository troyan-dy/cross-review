"""Command-line pieces several scripts share: the model options of a run and the parser of a script."""

from __future__ import annotations

import argparse


def parser(doc: str | None) -> argparse.ArgumentParser:
    """The parser of a script, its module docstring as the description."""
    return argparse.ArgumentParser(description=doc, formatter_class=argparse.RawDescriptionHelpFormatter)


def add_model_options(command: argparse.ArgumentParser) -> None:
    """--tier, or a one-off --claude/--codex pair."""
    command.add_argument("--tier", help="a tier from the configuration; default: its `tier`")
    command.add_argument("--claude", metavar="MODEL", help="one-off claude model, e.g. sonnet:high; needs --codex")
    command.add_argument("--codex", metavar="MODEL", help="one-off codex model, e.g. gpt-6-sol:high; needs --claude")


def check_model_pair(command: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    """A usage error, exit 2, unless the model options make one choice."""
    if (args.claude is None) != (args.codex is None):
        command.error("--claude and --codex go together")
    if args.claude is not None and args.tier is not None:
        command.error("--tier and a --claude/--codex pair exclude each other")
