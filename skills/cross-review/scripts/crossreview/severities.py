"""The severities of a finding, most urgent first."""

from __future__ import annotations

SEVERITIES = ("critical", "major", "minor")


def rank(severity: str) -> int:
    """critical first; an unknown severity sorts last instead of failing."""
    return SEVERITIES.index(severity) if severity in SEVERITIES else len(SEVERITIES)
