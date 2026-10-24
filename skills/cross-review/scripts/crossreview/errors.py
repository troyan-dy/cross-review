"""The error every script reports as one JSON line {"error": ...} with exit code 1."""

from __future__ import annotations


class ConfigError(Exception):
    """A configuration the scripts cannot run with; the message names the file and the key."""
