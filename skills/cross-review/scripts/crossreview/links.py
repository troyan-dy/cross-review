"""Symlinks in what the reviewed branch owns: .cross-review/ and the runs under it.

The reviewed branch owns .cross-review/, so any symlink there is a configuration error, whether it leads outside
the repository or to a file inside it.
"""

from __future__ import annotations

import os
from pathlib import Path

from crossreview.errors import ConfigError

REPO_DIR = ".cross-review"


def symlink_error(path: Path) -> ConfigError:
    """The error for a symlink found where the repository layer or a run must hold plain files."""
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
        current /= part
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
    for folder, dirnames, filenames in os.walk(root):
        _refuse_links(Path(folder), sorted([*dirnames, *filenames]))


def _refuse_links(folder: Path, names: list[str]) -> None:
    links = [folder / name for name in names if (folder / name).is_symlink()]
    if links:
        raise symlink_error(links[0])
