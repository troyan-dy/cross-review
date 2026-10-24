"""The configuration of a run: the plugin defaults with <repo>/.cross-review/cross-review.json merged over them."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from crossreview.config_rules import validate
from crossreview.config_shape import SHAPE, check_shape, merge
from crossreview.errors import ConfigError
from crossreview.layers import CONFIG_FILE, PLUGIN, repo_layer
from crossreview.links import confined
from crossreview.texts import read_json


def load_config(repo: Path | None) -> dict:
    """The plugin defaults with <repo>/.cross-review/cross-review.json merged over them, validated."""
    if repo is not None and not Path(repo).is_dir():
        raise ConfigError(f"{repo}: no such directory")
    defaults_file = PLUGIN / CONFIG_FILE
    cfg = read_json(defaults_file)
    _blamed(defaults_file, check_shape, cfg, SHAPE, "", complete=True)
    blame = defaults_file
    own_file = _own_config(repo)
    if own_file is not None:
        own = read_json(confined(repo, own_file))
        _blamed(own_file, check_shape, own, SHAPE, "", complete=False)
        cfg = merge(cfg, own)
        blame = own_file
    _blamed(blame, _validated, cfg, repo)
    return cfg


def _own_config(repo: Path | None) -> Path | None:
    layer = repo_layer(repo)
    own_file = layer / CONFIG_FILE if layer else None
    return own_file if own_file is not None and own_file.is_file() else None


def _validated(cfg: dict, repo: Path | None) -> None:
    check_shape(cfg, SHAPE, "", complete=True)
    validate(cfg, repo)


def _blamed(path: Path, check: Callable[..., None], *args: object, **kwargs: object) -> None:
    """Run a check; its ConfigError comes out prefixed with the file to blame."""
    try:
        check(*args, **kwargs)
    except ConfigError as error:
        raise ConfigError(f"{path}: {error}") from None
