#!/usr/bin/env python3
"""Collect the finder answers of a run into stages/1-found.json.

Reads the roster from the run's manifest.json and every agent's answer from agents/<agent>.json: the JSON
object the finder schema describes, optionally wrapped in prose or a ```json fence. Each finding gets the id
<agent>-<n> and `sources: [<agent>]`; lenses the agent was not given are dropped, and a missing `fix` or
`end_line` becomes empty. A finding that breaks the schema is left out with a `finding_rejected` event.

An agent is degraded when its answer is missing, is not JSON or does not match the schema, or when its
agents/<agent>.exit holds a non-zero code; a usage limit named in the tail of its .log is added to the
reason. `{"findings": []}` is a healthy agent that found nothing. Degraded agents and what each healthy one
raised go to events.jsonl.

Writes stages/1-found.json in the run format and prints one JSON line: the counts, the degraded
agents with their reasons and how many agents of each side reported. Exit 1 when the run cannot be read.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cross_config  # noqa: E402
from cross_config import ConfigError  # noqa: E402

SIDES = ("claude", "codex")
LIMIT_MARKERS = ("usage limit", "rate limit", "quota", "429", "too many requests")
LIMIT_TAIL = 2048
COLLECT_KINDS = ("finding_rejected", "agent_degraded", "agent_done")
FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n\s*```", re.S)
# the order a finding's fields are written in, as the run format has it; id, sources and verdict are added here
FIELDS = ("file", "line", "end_line", "severity", "confidence", "title", "body", "fix")
JSON_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "number": (int, float), "null": type(None)}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def extract_json(text: str) -> object:
    """The JSON answer in an agent's text: the whole text, a ```json fence, or the outermost {...}."""
    candidates = [text.strip(), *(m.group(1) for m in FENCE.finditer(text))]
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            return json.loads(candidate)
        except ValueError:
            continue
    raise ValueError("no JSON object in the answer")


def is_type(value: object, name: str) -> bool:
    if name == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, JSON_TYPES[name])


def check_schema(value: object, schema: dict, where: str = "answer") -> list[str]:
    """Errors of value against the JSON Schema subset the plugin schemas use: type (one name or a list of them),
    enum, required, properties, additionalProperties, items, minimum and maximum. An empty list is a match."""
    kind = schema.get("type")
    kinds = [kind] if isinstance(kind, str) else kind or []
    if kinds and not any(is_type(value, k) for k in kinds):
        return [f"{where}: expected {' or '.join(kinds)}"]
    errors: list[str] = []
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} is not one of {', '.join(map(str, schema['enum']))}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{where}: {value} is below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{where}: {value} is above {schema['maximum']}")
    if isinstance(value, dict):
        props = schema.get("properties") or {}
        errors += [f"{where}.{k}: missing" for k in schema.get("required") or [] if k not in value]
        for k, v in value.items():
            if k in props:
                errors += check_schema(v, props[k], f"{where}.{k}")
            elif schema.get("additionalProperties") is False:
                errors.append(f"{where}.{k}: unknown key")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for i, item in enumerate(value):
            errors += check_schema(item, schema["items"], f"{where}[{i}]")
    return errors


def log_tail(path: Path) -> str:
    try:
        return path.read_text(errors="replace")[-LIMIT_TAIL:].lower()
    except OSError:
        return ""


def header_model(path: Path) -> str:
    """The model Codex resolved, from the `model:` line of its log header."""
    try:
        lines = path.read_text(errors="replace").splitlines()[:40]
    except OSError:
        return ""
    for line in lines:
        key, _, value = line.partition(":")
        if key.strip().lower() == "model" and value.strip():
            return value.strip()
    return ""


def exit_code(path: Path) -> int | None:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return None


def read_answer(path: Path) -> tuple[list | None, str]:
    """The raw findings of an answer file, or None and why the agent is degraded."""
    if not path.is_file():
        return None, f"no answer: {path.name} is missing"
    try:
        answer = extract_json(path.read_text(errors="replace"))
    except ValueError as e:
        return None, f"answer is not JSON: {e}"
    if not isinstance(answer, dict) or not isinstance(answer.get("findings"), list):
        return None, "answer does not match the finder schema: expected an object with a findings array"
    return answer["findings"], ""


def normalize(raw: object, item_schema: dict, agent: dict) -> tuple[dict | None, list[str]]:
    """One finding as the run keeps it, or None and the schema errors that leave it out."""
    if not isinstance(raw, dict):
        return None, ["finding: expected object"]
    props = item_schema.get("properties") or {}
    finding = {k: v for k, v in raw.items() if k in props}
    finding.setdefault("end_line", 0)
    finding.setdefault("fix", "")
    errors = check_schema(finding, item_schema, "finding")
    if errors:
        return None, errors
    lenses = [lens for lens in finding["lenses"] if lens in agent["lenses"]]
    finding["lenses"] = lenses or list(agent["lenses"])
    return finding, []


def own_event(line: str, names: set[str]) -> bool:
    """An event an earlier collect wrote: the find stage, or an event about one of the run's finders."""
    try:
        event = json.loads(line)
        return event.get("stage") == "find" or (event.get("kind") in COLLECT_KINDS and event.get("agent") in names)
    except (ValueError, AttributeError):
        return False


def collect(run_dir: Path) -> dict:
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text())
    except (OSError, ValueError):
        raise ConfigError(f"{run_dir}: no readable manifest.json, compose the run first") from None
    if not isinstance(manifest, dict) or not isinstance(manifest.get("agents"), list):
        raise ConfigError(f"{run_dir}/manifest.json lists no agents, compose the run with review_run.py")
    schema = json.loads(cross_config.resolve(None, "schemas", "finder").read_text())
    item_schema = schema["properties"]["findings"]["items"]
    agents_dir = run_dir / "agents"
    events: list[dict] = []
    stats, findings, degraded, rejected = [], [], [], 0

    for agent in manifest["agents"]:
        name = agent["name"]
        raw, reason = read_answer(agents_dir / f"{name}.json")
        code = exit_code(agents_dir / f"{name}.exit")
        if code:
            reason = f"exited {code}" + (f"; {reason}" if reason else "")
        kept: list[dict] = []
        if not reason:
            for raw_finding in raw or []:
                finding, errors = normalize(raw_finding, item_schema, agent)
                if finding is None:
                    rejected += 1
                    events.append({"kind": "finding_rejected", "agent": name, "text": "; ".join(errors),
                                   "finding": raw_finding, "at": now()})
                    continue
                kept.append(finding)
        else:
            tail = log_tail(agents_dir / f"{name}.log")
            limit = next((m for m in LIMIT_MARKERS if m in tail), None)
            if limit:
                reason += f"; the log tail mentions {limit!r}, a usage limit is likely"
            degraded.append({"agent": name, "reason": reason})
            events.append({"kind": "agent_degraded", "agent": name, "text": reason, "at": now()})

        for n, finding in enumerate(kept, 1):
            findings.append({"id": f"{name}-{n}", **{k: finding[k] for k in FIELDS},
                             "sources": [name], "lenses": finding["lenses"], "verdict": ""})
        if not reason:
            events.append({"kind": "agent_done", "agent": name, "text": f"{len(kept)} findings", "at": now()})
        stats.append({"name": name, "lenses": agent["lenses"], "executor": agent["executor"],
                      "requested_model": agent["requested_model"],
                      "actual_model": header_model(agents_dir / f"{name}.log"),
                      "effort": agent["effort"], "tokens": 0, "raised": len(kept), "degraded": bool(reason)})

    started = manifest.get("started_at")
    collected = now()
    try:
        duration = int((datetime.fromisoformat(collected) - datetime.fromisoformat(started)).total_seconds() * 1000)
    except (TypeError, ValueError):
        duration = 0
    found = {
        "scope": {"task": manifest.get("task", ""), "run": manifest.get("run", ""),
                  "scope_path": manifest.get("scope_path", "")},
        "sources": {"expected": len(stats), "reported": sum(not s["degraded"] for s in stats),
                    "degraded": [d["agent"] for d in degraded], "agents": stats},
        "findings": findings,
        "open_questions": [],
        "pre_existing": [],
        "immaterial": [],
        "stats": {"started_at": started, "tokens": 0, "stages": [{"name": "find", "duration_ms": duration}]},
    }
    out = run_dir / "stages" / "1-found.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(found, ensure_ascii=False, indent=2) + "\n")
    # a second collect after a rerun replaces the first one's events instead of adding a contradicting set
    names = {agent["name"] for agent in manifest["agents"]}
    log = run_dir / "events.jsonl"
    kept = [line for line in (log.read_text().splitlines() if log.is_file() else []) if not own_event(line, names)]
    fresh = [{"kind": "stage", "stage": "find", "at": started or collected}, *events]
    log.write_text("".join(line + "\n" for line in [*kept, *(json.dumps(e, ensure_ascii=False) for e in fresh)]))

    sides = {side: {"expected": sum(s["executor"] == side for s in stats),
                    "reported": sum(s["executor"] == side and not s["degraded"] for s in stats)} for side in SIDES}
    return {
        "found": str(out),
        "expected": found["sources"]["expected"],
        "reported": found["sources"]["reported"],
        "findings": len(findings),
        "rejected": rejected,
        "degraded": degraded,
        "sides": sides,
        "two_sides": all(sides[side]["reported"] > 0 for side in SIDES),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run_dir", help="the run directory, after its agents answered")
    args = ap.parse_args(argv)
    try:
        out = collect(Path(args.run_dir).resolve())
    except ConfigError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
