"""The text of a prompt or lens as the scripts use it: resolved through both layers, front matter dropped,
placeholders filled in one pass."""

from __future__ import annotations

import functools
import json
import re
from pathlib import Path
from typing import Any

from crossreview.errors import ConfigError
from crossreview.layers import resolve

PLACEHOLDER = re.compile(r"\{\{([A-Za-z0-9_]+)\}\}")
FRONT_MATTER = "---\n"
FRONT_MATTER_END = "\n---\n"
_ABSENT = object()


def strip_front_matter(text: str) -> str:
    """A prompt or lens without its leading `---` front matter; text without one is returned as is."""
    if text.startswith(FRONT_MATTER):
        end = text.find(FRONT_MATTER_END, len(FRONT_MATTER) - 1)
        if end != -1:
            return text[end + len(FRONT_MATTER_END):].lstrip("\n")
    return text


def body(repo: Path | None, kind: str, name: str) -> str:
    """The text of a prompt or lens as the scripts use it: resolved through both layers, front matter dropped."""
    text = resolve(repo, kind, name).read_text()
    return strip_front_matter(text).strip()


def render(text: str, replacements: dict, where: str) -> str:
    """Replace every {{NAME}} with replacements[NAME] in one pass. A placeholder with no value is an error naming
    `where` and the placeholder; a value is never re-scanned, so model text quoting {{x}} stays as it is."""
    missing: list[str] = []
    rendered = PLACEHOLDER.sub(functools.partial(_substitute, replacements, missing), text)
    if missing:
        expected = ", ".join(f"{{{{{key}}}}}" for key in replacements)
        raise ConfigError(f"{where}: unknown placeholder {missing[0]}, expected one of {expected}")
    return rendered


def read_json(path: Path) -> Any:
    """A configuration or run file; malformed JSON is a ConfigError naming the file."""
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as error:
        raise ConfigError(f"{path}: not valid JSON: {error}") from None


def _substitute(replacements: dict, missing: list[str], placeholder: re.Match) -> str:
    found = replacements.get(placeholder.group(1), _ABSENT)
    if found is _ABSENT:
        missing.append(placeholder.group(0))
        return placeholder.group(0)
    return str(found)
