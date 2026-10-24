"""The keys and file-safe names of the verify groups."""

from __future__ import annotations

import posixpath
import re

from crossreview.keys import FILE_KEY, SOURCES

LABEL_KEYS = 3
LABEL_UNSAFE = re.compile(r"[^a-zA-Z0-9_.-]+")
ROOT = "root"


def group_key(finding: dict, by_source: bool) -> str:
    """The bucket of a finding: its first source, or the directory of its file; `.` for neither."""
    if by_source:
        return finding[SOURCES][0] if finding.get(SOURCES) else "."
    if not finding.get(FILE_KEY):
        return "."
    return posixpath.normpath(posixpath.dirname(finding[FILE_KEY]))


def slug(key: str) -> str:
    """A group key as a piece of a file name."""
    return LABEL_UNSAFE.sub("-", key).strip("-.") or ROOT


def label(keys: list[str]) -> str:
    """A filename-safe name for the keys a group covers: the first three, then how many more."""
    slugs = [slug(key) for key in keys[:LABEL_KEYS]]
    named = "+".join(slugs) or ROOT
    extra = len(keys) - LABEL_KEYS
    return f"{named}+{extra}-more" if extra > 0 else named
