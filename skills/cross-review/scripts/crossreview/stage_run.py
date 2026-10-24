"""What the synthesis and verify stages share: the run they read, the prompt they write, the manifest and
stats rows they update."""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

from crossreview.errors import ConfigError
from crossreview.jsonio import write_json
from crossreview.keys import AGENTS, DEGRADED, NAME, SOURCES, STAGES, STATS
from crossreview.layers import source
from crossreview.runs import OUTPUT_CONTRACT

MANIFEST = "manifest.json"
PROMPTS = "prompts"


class StageInput(NamedTuple):
    """The repository of a run, its manifest and the stage file the stage starts from."""

    repo: Path
    manifest: dict
    report: dict




def read_manifest(run_dir: Path) -> dict:
    """The manifest compose wrote, with the stage settings every later stage reads."""
    try:
        manifest = json.loads((run_dir / MANIFEST).read_text())
    except (OSError, ValueError):
        raise ConfigError(f"{run_dir}: no readable manifest.json, compose the run first") from None
    stages = manifest.get(STAGES) if isinstance(manifest, dict) else None
    if not isinstance(stages, dict):
        raise ConfigError(f"{run_dir}/manifest.json has no stages, compose the run with review_run.py")
    return manifest


def degraded_names(report: dict) -> list[str]:
    """Every source that failed to deliver, from the explicit list and the per-agent flags together."""
    sources = report.get(SOURCES) or {}
    names = list(sources.get(DEGRADED) or [])
    agents = sources.get(AGENTS) or []
    flagged = [agent[NAME] for agent in agents if agent.get(DEGRADED)]
    names.extend([name for name in flagged if name not in names])
    return names


def record_prompts(run_dir: Path, manifest: dict, repo: Path, *files: tuple[str, str]) -> None:
    """Add the (kind, name) files a step used to the manifest's prompt sources, replacing an earlier run's."""
    entries = [source(repo, kind, name) for kind, name in files]
    manifest[PROMPTS] = [*_other_sources(manifest, entries), *entries]
    write_json(run_dir / MANIFEST, manifest)


def write_prompt(path: Path, text: str, schema: str) -> None:
    """A stage prompt: its text, then the output contract and the schema the answer is held to."""
    path.write_text(f"{text}\n\n{OUTPUT_CONTRACT}{schema}\n")


def with_stage(report: dict, entry: dict) -> dict:
    """report with entry as the row of its stage in stats.stages, replacing an earlier one."""
    stats = dict(report.get(STATS) or {})
    earlier = stats.get(STAGES) or []
    rows = [row for row in earlier if row.get(NAME) != entry[NAME]]
    stats[STAGES] = [*rows, entry]
    return {**report, STATS: stats}


def _other_sources(manifest: dict, entries: list[dict]) -> list[dict]:
    paths = {entry["path"] for entry in entries}
    recorded = manifest.get(PROMPTS) or []
    return [entry for entry in recorded if entry.get("path") not in paths]
