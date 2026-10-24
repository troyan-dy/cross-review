"""`meta` of final.json: what the outcome takes from the run's findings.json and its manifest."""

from __future__ import annotations

from pathlib import Path

from crossreview.keys import AGENTS, DEGRADED, FINDINGS, NAME, SOURCES
from crossreview.outcome_files import pipeline_of

AGENT_KEYS = ("name", "executor", "actual_model", "requested_model", "effort", "raised", "degraded")
SYNTHESIZED_LISTS = (FINDINGS, "immaterial", "pre_existing")
MS_IN_MINUTE = 60000


def run_meta(findings: dict, round_dir: Path, debate_dir: Path) -> dict:
    """`meta` of final.json: the run's task, agents, counts and stage models."""
    stats = findings.get("stats") or {}
    sources = findings.get(SOURCES) or {}
    agents = sources.get(AGENTS) or []
    scope = findings.get("scope") or {}
    stages = stats.get("stages") or []
    return {
        "task": scope.get("task"),
        "run": scope.get("run"),
        "round_dir": str(round_dir),
        "debate_dir": str(debate_dir),
        "pipeline": pipeline_of(round_dir),
        AGENTS: [{key: agent.get(key) for key in AGENT_KEYS} for agent in agents],
        DEGRADED: sources.get(DEGRADED) or [],
        "raw": sum(agent.get("raised", 0) for agent in agents),
        "synthesized": sum(len(findings.get(key) or []) for key in SYNTHESIZED_LISTS),
        "duration_min": round((stats.get("duration_ms") or 0) / MS_IN_MINUTE),
        "started_at": stats.get("started_at"),
        "verify_model": _stage_model(stages, "verify"),
        "synthesis_model": _stage_model(stages, "synthesis"),
    }


def _stage_model(stages: list[dict], name: str) -> str | None:
    matching = [stage for stage in stages if stage.get(NAME) == name]
    return matching[0].get("model") if matching else None
