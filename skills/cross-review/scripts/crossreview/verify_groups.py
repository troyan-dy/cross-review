"""The verify groups of a run: findings bucketed by directory or by source, thin buckets merged, capped."""

from __future__ import annotations

import itertools

from crossreview.keys import FINDINGS, NAME, STAGES
from crossreview.verify_labels import group_key, label

VERIFY = "verify"
KEYS = "keys"
THIN_GROUP = 2


def group(findings: list[dict], by: str, limit: int) -> list[dict]:
    """The verify groups, in order: [{name, keys, findings}] with collision-free names."""
    by_source = by == "source"
    buckets: dict[str, list[dict]] = {}
    for finding in findings:
        buckets.setdefault(group_key(finding, by_source), []).append(finding)
    groups = _bucket_groups(buckets, by_source)
    if 0 < limit < len(groups):
        groups = _capped(groups, limit)
    return _named(groups)


def run_groups(manifest: dict, synthesized: dict) -> list[dict]:
    """The groups of a run, by the stage settings of its manifest."""
    stages = manifest[STAGES]
    by = stages.get("verify_group_by", "dir")
    return group(synthesized[FINDINGS], by, stages.get("verify_groups", 0))


def agent_name(verify_group: dict) -> str:
    """The verifier of a group: verify-<group>, the name of its prompt and answer."""
    return f"{VERIFY}-{verify_group[NAME]}"


def _bucket_groups(buckets: dict[str, list[dict]], by_source: bool) -> list[dict]:
    groups = []
    thin: dict = {KEYS: [], FINDINGS: []}
    for key in sorted(buckets):
        bucket = buckets[key]
        # one finding does not earn a verifier of its own; directory locality is what makes merging them sound
        if by_source or len(bucket) >= THIN_GROUP:
            groups.append({KEYS: [key], FINDINGS: bucket})
        else:
            thin[KEYS].append(key)
            thin[FINDINGS].extend(bucket)
    if thin[FINDINGS]:
        groups.append(thin)
    return groups


def _capped(groups: list[dict], limit: int) -> list[dict]:
    ordered = sorted(groups, key=lambda entry: len(entry[FINDINGS]))
    cut = len(ordered) - limit + 1
    smallest = ordered[:cut]
    merged = {
        KEYS: [key for entry in smallest for key in entry[KEYS]],
        FINDINGS: [finding for entry in smallest for finding in entry[FINDINGS]],
    }
    rest = [merged, *ordered[cut:]]
    return sorted(rest, key=lambda entry: label(entry[KEYS]))


def _named(groups: list[dict]) -> list[dict]:
    named = []
    taken: set[str] = set()
    for entry in groups:
        name = _free_name(label(entry[KEYS]), taken)
        taken.add(name)
        named.append({NAME: name, **entry})
    return named


def _free_name(base: str, taken: set[str]) -> str:
    numbered = (f"{base}-{number}" for number in itertools.count(2))
    candidates = itertools.chain([base], numbered)
    return next(name for name in candidates if name not in taken)
