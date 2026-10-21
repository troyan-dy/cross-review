#!/usr/bin/env python3
"""The synthesis stage of a review run: merge every source's findings into one set.

  prepare <run_dir>            write prompts/stages/synthesis.md and print the launch plan
  apply <run_dir> [answer]     apply the rules over the model's answer and write stages/2-synthesized.json

`prepare` reads stages/1-found.json and renders the `synthesis` prompt: {{SOURCES}} is the roster as it ran,
degraded agents included, {{FINDINGS}} is every finding as JSON, and the synthesis schema follows. The stage
runs as a Claude agent on the run's stage model, without access to the code, and its answer goes to
agents/synthesis.json. With `stages.synthesis: false` in the run's manifest, or with no finding to merge,
`prepare` copies 1-found.json to 2-synthesized.json and prints `skipped`: the run goes straight to verify.

`apply` reads the answer — the JSON object of the synthesis schema, optionally wrapped in prose or a ```json
fence — and takes from the model only what needs judgement: which inputs are one problem (`merged_ids`),
which list each goes to, and the wording and anchor. The rest are the synthesis rules, applied here:
  - the id is the first merged id, `merged_ids` keeps them all, sources and lenses are their union;
  - a finding's confidence is min(99, highest input confidence + 10 * (sources - 1)); an open question or a
    pre-existing issue keeps the highest input confidence, never boosted;
  - severity is the highest input severity;
  - a finding with one source, confidence below 80, neither critical nor major is dropped unless the round is
    degraded; an input the answer left out is dropped when that rule drops it and restored as its own
    finding otherwise.
What was dropped and restored goes to events.jsonl as `dropped` and `restored` events carrying the findings;
applying again replaces them. An id the input does not hold, an output with no ids and an input claimed by
two outputs are errors.

Prints one JSON line. A run, configuration or answer error prints {"error": ...} and exits 1.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cross_config  # noqa: E402
import review_collect  # noqa: E402
import review_run  # noqa: E402
from cross_config import ConfigError  # noqa: E402

STAGE = "synthesis"
LISTS = ("findings", "open_questions", "pre_existing")
SEVERITIES = ("critical", "major", "minor")
GATING = ("critical", "major")
DROP_BELOW = 80
BOOST = 10
CAP = 99


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def rank(severity: str) -> int:
    """critical first; an unknown severity sorts last instead of failing."""
    return SEVERITIES.index(severity) if severity in SEVERITIES else len(SEVERITIES)


def read_manifest(run_dir: Path) -> dict:
    """The manifest compose wrote, with the stage settings every later stage reads."""
    try:
        manifest = json.loads((run_dir / "manifest.json").read_text())
    except (OSError, ValueError):
        raise ConfigError(f"{run_dir}: no readable manifest.json, compose the run first") from None
    if not isinstance(manifest, dict) or not isinstance(manifest.get("stages"), dict):
        raise ConfigError(f"{run_dir}/manifest.json has no stages, compose the run with review_run.py")
    return manifest


def load_run(run_dir: Path) -> tuple[Path, dict, dict]:
    """The repository, the manifest and stages/1-found.json of a collected run."""
    repo, _, _ = review_run.locate(run_dir)
    manifest = read_manifest(run_dir)
    try:
        found = json.loads((run_dir / "stages" / "1-found.json").read_text())
    except (OSError, ValueError):
        raise ConfigError(f"{run_dir}: no readable stages/1-found.json, collect the run with review_collect.py") from None
    if (run_dir / "stages" / "3-verified.json").exists():
        raise ConfigError(f"{run_dir}: verify already ran over this synthesis, start a new run")
    return repo, manifest, found


def degraded_names(found: dict) -> list[str]:
    """Every source that failed to deliver, from the explicit list and the per-agent flags together."""
    src = found.get("sources") or {}
    names = list(src.get("degraded") or [])
    names += [a["name"] for a in src.get("agents") or [] if a.get("degraded") and a["name"] not in names]
    return names


def sources_block(found: dict) -> str:
    """{{SOURCES}}: what actually ran, one line per source. A degraded source is loud here, since it is
    what tells the model corroboration was rarer than it looks."""
    agents = found["sources"]["agents"]
    degraded = degraded_names(found)
    ids: dict[str, list[str]] = {}
    for f in found["findings"]:
        for source in f["sources"]:
            ids.setdefault(source, []).append(f["id"])
    lines = []
    for agent in agents:
        entry = f"- {agent['name']} (lenses: {', '.join(agent['lenses'])})"
        own = ids.get(agent["name"], [])
        if agent["name"] in degraded:
            lines.append(entry + " DEGRADED, reported nothing")
        else:
            lines.append(entry + (f" reported {len(own)} findings: {', '.join(own)}" if own else " reported no findings"))
    head = f"{len(agents)} sources ran, {sum(a['name'] not in degraded for a in agents)} reported."
    if degraded:
        head += f" This run is DEGRADED: {', '.join(degraded)} reported nothing."
    return head + "\n" + "\n".join(lines)


def read_events(run_dir: Path) -> list[str]:
    path = run_dir / "events.jsonl"
    return path.read_text().splitlines() if path.is_file() else []


def append_events(run_dir: Path, events: list[dict]) -> None:
    with (run_dir / "events.jsonl").open("a") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")


def record_prompts(run_dir: Path, manifest: dict, repo: Path, stage: str = STAGE) -> None:
    """Add a stage's prompt and schema to the manifest's prompt sources, replacing an earlier prepare's."""
    entries = [review_run.source(repo, "prompts", stage), review_run.source(repo, "schemas", stage)]
    paths = {e["path"] for e in entries}
    manifest["prompts"] = [p for p in manifest.get("prompts") or [] if p.get("path") not in paths] + entries
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def prepare(run_dir: Path) -> dict:
    repo, manifest, found = load_run(run_dir)
    stages = manifest["stages"]
    out = run_dir / "stages" / "2-synthesized.json"
    reason = ""
    if not stages.get("synthesis", True):
        reason = "stages.synthesis is off for this run"
    elif not found["findings"]:
        reason = "no finding to merge"
    if reason:
        shutil.copyfile(run_dir / "stages" / "1-found.json", out)
        append_events(run_dir, [{"kind": "stage", "stage": STAGE, "text": f"skipped: {reason}", "at": now()}])
        return {"skipped": True, "reason": reason, "synthesized": str(out), "findings": len(found["findings"])}

    values = {"SOURCES": sources_block(found), "FINDINGS": json.dumps(found["findings"], ensure_ascii=False, indent=2)}
    text = cross_config.render(cross_config.body(repo, "prompts", STAGE), values, f"prompts/{STAGE}.md")
    schema = cross_config.resolve(repo, "schemas", STAGE)
    prompt = run_dir / "prompts" / "stages" / f"{STAGE}.md"
    prompt.parent.mkdir(parents=True, exist_ok=True)
    prompt.write_text(text + "\n\n" + review_run.OUTPUT_CONTRACT + schema.read_text().strip() + "\n")
    record_prompts(run_dir, manifest, repo)
    append_events(run_dir, [{"kind": "stage", "stage": STAGE, "at": now()}])
    return {"skipped": False, "stage": STAGE, "vendor": "claude", "alias": stages["model"], "effort": stages["effort"],
            "prompt": str(prompt), "output": str(run_dir / "agents" / f"{STAGE}.json"),
            "findings": len(found["findings"]), "degraded": degraded_names(found)}


def read_answer(path: Path) -> dict:
    """The three lists of a synthesis answer, cleaned to the schema: keys the schema does not name are dropped,
    a missing `fix` or `end_line` becomes empty, and a missing open_questions or pre_existing list is empty."""
    if not path.is_file():
        raise ConfigError(f"{path}: no synthesis answer, save the agent's answer there first")
    try:
        answer = review_collect.extract_json(path.read_text(errors="replace"))
    except ValueError as e:
        raise ConfigError(f"{path}: {e}") from None
    if not isinstance(answer, dict) or not isinstance(answer.get("findings"), list):
        raise ConfigError(f"{path}: expected an object with a findings array, the synthesis schema")
    schema = json.loads(cross_config.resolve(None, "schemas", STAGE).read_text())
    clean: dict = {}
    for name in LISTS:
        props = schema["properties"][name]["items"]["properties"]
        items = answer.get(name, [])
        if isinstance(items, list):
            items = [{"end_line": 0, "fix": "", **{k: v for k, v in i.items() if k in props}} if isinstance(i, dict) else i
                     for i in items]
        clean[name] = items
    errors = review_collect.check_schema(clean, schema)
    if errors:
        more = f" and {len(errors) - 5} more" if len(errors) > 5 else ""
        raise ConfigError(f"{path}: the answer does not match the synthesis schema: {'; '.join(errors[:5])}{more}")
    return clean


def union(lists: list[list[str]]) -> list[str]:
    """Every value once, in first-seen order: one agent under two lenses is still one source."""
    out: list[str] = []
    for values in lists:
        out += [v for v in values if v not in out]
    return out


def combine(item: dict, inputs: list[dict], boost: bool) -> dict:
    """One synthesized finding: the wording and anchor of item, attribution, confidence and severity from the
    inputs it merges."""
    ids = [f["id"] for f in inputs]
    sources = union([f["sources"] for f in inputs])
    top = max(f["confidence"] for f in inputs)
    return {
        "id": ids[0],
        "file": item["file"],
        "line": item["line"],
        "end_line": item.get("end_line", 0),
        "severity": min((f["severity"] for f in inputs), key=rank),
        "confidence": min(CAP, top + BOOST * (len(sources) - 1)) if boost else top,
        "title": item["title"],
        "body": item["body"],
        "fix": item.get("fix", ""),
        "sources": sources,
        "lenses": union([f["lenses"] for f in inputs]),
        "verdict": "",
        "merged_ids": ids,
    }


def attribute(items: list[dict], inputs: dict[str, dict], claimed: set[str], boost: bool) -> list[dict]:
    """The outputs of one list with their inputs resolved. An output listing one id twice merges it once."""
    out = []
    for item in items:
        ids = list(dict.fromkeys(item["merged_ids"]))
        if not ids:
            raise ConfigError(f"synthesized finding {item['title']!r} merged no input ids")
        for i in ids:
            if i not in inputs:
                raise ConfigError(f"synthesis merged unknown finding id {i!r}")
            if i in claimed:
                raise ConfigError(f"synthesis merged finding id {i!r} into more than one output")
            claimed.add(i)
        out.append(combine(item, [inputs[i] for i in ids], boost))
    return out


def droppable(f: dict) -> bool:
    """The drop rule: one source, confidence below 80, and neither critical nor major."""
    return len(f["sources"]) == 1 and f["confidence"] < DROP_BELOW and f["severity"] not in GATING


def stage_duration(lines: list[str], stage: str = STAGE) -> int:
    """Milliseconds since the last stage event of `stage`, the one its prepare wrote; 0 without it."""
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("kind") == "stage" and event.get("stage") == stage:
            at = review_run.parse_time(event.get("at"))
            return int((datetime.now().astimezone() - at).total_seconds() * 1000) if at else 0
    return 0


def own_event(line: str) -> bool:
    """An event an earlier apply wrote: applying again replaces them rather than adding a second set."""
    try:
        return json.loads(line).get("agent") == STAGE
    except (ValueError, AttributeError):
        return False


def apply(run_dir: Path, answer_path: Path | None) -> dict:
    _, manifest, found = load_run(run_dir)
    stages = manifest["stages"]
    if not stages.get("synthesis", True):
        raise ConfigError("stages.synthesis is off for this run: prepare already wrote 2-synthesized.json, go to verify")
    answer = read_answer(answer_path or run_dir / "agents" / f"{STAGE}.json")
    inputs = {f["id"]: f for f in found["findings"]}
    degraded = degraded_names(found)

    # one claimed set across the three lists: an input reported both as a finding and as a question is the
    # same contract violation as one reported twice in either
    claimed: set[str] = set()
    lists = {name: attribute(answer[name], inputs, claimed, name == "findings") for name in LISTS}
    findings, dropped, restored = [], [], []
    for f in lists["findings"]:
        (dropped if droppable(f) and not degraded else findings).append(f)
    for i, raw in inputs.items():
        if i in claimed:
            continue
        own = combine(raw, [raw], True)
        if droppable(own) and not degraded:
            dropped.append(raw)
        else:
            restored.append(own)
    findings = sorted(findings + restored, key=lambda f: rank(f["severity"]))
    dropped.sort(key=lambda f: f["id"])

    lines = read_events(run_dir)
    events = []
    if dropped:
        events.append({"kind": "dropped", "agent": STAGE, "text": f"{len(dropped)} findings dropped",
                       "findings": dropped, "at": now()})
    if restored:
        events.append({"kind": "restored", "agent": STAGE,
                       "text": f"{len(restored)} findings the answer left out restored, the drop rule keeps them",
                       "findings": restored, "at": now()})
    events.append({"kind": "agent_done", "agent": STAGE, "text": f"{len(findings)} findings", "at": now()})
    kept = [line for line in lines if not own_event(line)]
    (run_dir / "events.jsonl").write_text("".join(line + "\n" for line in kept))
    append_events(run_dir, events)

    stats = dict(found.get("stats") or {})
    stats["stages"] = [s for s in stats.get("stages") or [] if s.get("name") != STAGE] + [
        {"name": STAGE, "duration_ms": stage_duration(lines), "executor": "claude", "model": stages["model"],
         "effort": stages["effort"]}]
    synthesized = {**found, "findings": findings, "open_questions": lists["open_questions"],
                   "pre_existing": lists["pre_existing"], "immaterial": [], "stats": stats}
    out = run_dir / "stages" / "2-synthesized.json"
    out.write_text(json.dumps(synthesized, ensure_ascii=False, indent=2) + "\n")
    return {
        "synthesized": str(out),
        "inputs": len(inputs),
        "findings": len(findings),
        "open_questions": len(lists["open_questions"]),
        "pre_existing": len(lists["pre_existing"]),
        "merged": sum(len(f["merged_ids"]) - 1 for name in LISTS for f in lists[name]),
        "dropped": [f["id"] for f in dropped],
        "restored": [f["id"] for f in restored],
        "degraded": degraded,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", metavar="{prepare,apply}", required=True)
    sub.add_parser("prepare", help="write the synthesis prompt and print the launch plan").add_argument(
        "run_dir", help="the run directory, after review_collect.py")
    app = sub.add_parser("apply", help="apply the synthesis answer and write stages/2-synthesized.json")
    app.add_argument("run_dir", help="the run directory, after prepare")
    app.add_argument("answer", nargs="?", help="the synthesis agent's answer; default: <run_dir>/agents/synthesis.json")
    args = ap.parse_args(argv)
    run_dir = Path(args.run_dir).resolve()
    try:
        if args.command == "prepare":
            out = prepare(run_dir)
        else:
            out = apply(run_dir, Path(args.answer).resolve() if args.answer else None)
    except ConfigError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
