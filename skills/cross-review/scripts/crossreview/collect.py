"""`review_collect.py`: the finder answers of a run collected into stages/1-found.json."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from crossreview.errors import ConfigError
from crossreview.events import make_event, parse_event, read_events, write_events
from crossreview.finder_answers import FinderAnswer, agent_stats, numbered
from crossreview.jsonio import now, write_json
from crossreview.keys import AGENT, AGENTS, DEGRADED, EXECUTOR, EXPECTED, FINDINGS, ITEMS_KEY, KIND
from crossreview.keys import NAME, PROPERTIES, REASON, REPORTED, SOURCES, STAGE, STAGES
from crossreview.layers import resolve
from crossreview.models import SIDES

COLLECT_KINDS = ("finding_rejected", "agent_degraded", "agent_done")
FIND = "find"


class Collection:
    """One collect of a run: every roster agent's answer read, normalized and accounted for."""

    def __init__(self, run_dir: Path) -> None:
        """Read the roster from manifest.json and the finder schema the answers are held to."""
        self._run_dir = run_dir
        self._manifest = _read_manifest(run_dir)
        schema = json.loads(resolve(None, "schemas", "finder").read_text())
        self._item_schema = schema[PROPERTIES][FINDINGS][ITEMS_KEY]
        self._agents_dir = run_dir / AGENTS
        self._events: list[dict] = []
        self._stats: list[dict] = []
        self._findings: list[dict] = []
        self._degraded: list[dict] = []
        self._rejected = 0

    def run(self) -> dict:
        """Collect every agent, write 1-found.json and events.jsonl; the counts are the result."""
        for agent in self._manifest[AGENTS]:
            self._collect(agent)
        out = self._write_found()
        sides = _sides(self._stats)
        return {
            "found": str(out),
            EXPECTED: len(self._stats),
            REPORTED: sum(not entry[DEGRADED] for entry in self._stats),
            FINDINGS: len(self._findings),
            "rejected": self._rejected,
            DEGRADED: self._degraded,
            "sides": sides,
            "two_sides": all(sides[side][REPORTED] > 0 for side in SIDES),
        }

    def _collect(self, agent: dict) -> None:
        name = agent[NAME]
        answer = FinderAnswer(self._agents_dir, agent, self._item_schema)
        self._rejected += len(answer.events)
        self._events.extend(answer.events)
        if answer.reason:
            self._degraded.append({AGENT: name, REASON: answer.reason})
            self._events.append(make_event("agent_degraded", agent=name, text=answer.reason))
        else:
            self._findings.extend(numbered(name, answer.kept))
            done = f"{len(answer.kept)} findings"
            self._events.append(make_event("agent_done", agent=name, text=done))
        self._stats.append(agent_stats(answer))

    def _write_found(self) -> Path:
        started = self._manifest.get("started_at")
        collected = now()
        out = self._run_dir / STAGES / "1-found.json"
        out.parent.mkdir(exist_ok=True)
        write_json(out, self._found(started, _duration_ms(started, collected)))
        self._rewrite_events(started or collected)
        return out

    def _rewrite_events(self, started: object) -> None:
        # a second collect after a rerun replaces the first one's events instead of adding a contradicting set
        names = {agent[NAME] for agent in self._manifest[AGENTS]}
        kept = [line for line in read_events(self._run_dir) if not own_event(line, names)]
        stage = make_event(STAGE, at=started, stage=FIND)
        write_events(self._run_dir, kept, [stage, *self._events])

    def _found(self, started: object, duration: int) -> dict:
        stats = self._stats
        sources = {
            EXPECTED: len(stats),
            REPORTED: sum(not entry[DEGRADED] for entry in stats),
            DEGRADED: [entry[AGENT] for entry in self._degraded],
            AGENTS: stats,
        }
        timing = [{NAME: FIND, "duration_ms": duration}]
        return {
            "scope": {key: self._manifest.get(key, "") for key in ("task", "run", "scope_path")},
            SOURCES: sources,
            FINDINGS: self._findings,
            "open_questions": [],
            "pre_existing": [],
            "immaterial": [],
            "stats": {"started_at": started, "tokens": 0, STAGES: timing},
        }


def collect(run_dir: Path) -> dict:
    """Collect the finder answers of run_dir into stages/1-found.json."""
    return Collection(run_dir).run()


def own_event(line: str, names: set[str]) -> bool:
    """An event an earlier collect wrote: the find stage, or an event about one of the run's finders."""
    event = parse_event(line)
    if not isinstance(event, dict):
        return False
    if event.get(STAGE) == FIND:
        return True
    return event.get(KIND) in COLLECT_KINDS and event.get(AGENT) in names


def _read_manifest(run_dir: Path) -> dict:
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text())
    except (OSError, ValueError):
        raise ConfigError(f"{run_dir}: no readable manifest.json, compose the run first") from None
    roster = manifest.get(AGENTS) if isinstance(manifest, dict) else None
    if not isinstance(roster, list):
        raise ConfigError(f"{run_dir}/manifest.json lists no agents, compose the run with review_run.py")
    return manifest


def _duration_ms(started: object, collected: str) -> int:
    try:
        elapsed = datetime.fromisoformat(collected) - datetime.fromisoformat(started)
    except (TypeError, ValueError):
        return 0
    return int(elapsed.total_seconds() * 1000)


def _sides(stats: list[dict]) -> dict:
    sides = {}
    for side in SIDES:
        mine = [entry for entry in stats if entry[EXECUTOR] == side]
        healthy = [entry for entry in mine if not entry[DEGRADED]]
        sides[side] = {EXPECTED: len(mine), REPORTED: len(healthy)}
    return sides
