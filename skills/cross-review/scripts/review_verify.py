#!/usr/bin/env python3
"""The verify stage of a review run: judge every synthesized finding against the code, then write the run's
findings.json and report.md.

  prepare <run_dir>    write prompts/stages/verify-<group>.md and print the launch plan
  apply <run_dir>      route the verdicts and write stages/3-verified.json, findings.json and report.md

`prepare` reads stages/2-synthesized.json and groups its findings for the verifiers. By default the key is
the directory of a finding's file, and the directories holding one finding are merged into one group; with
`stages.verify_group_by: source` the key is the first agent that raised it, and nothing is merged. Past
`stages.verify_groups` groups the smallest are merged into one. Each group gets the `verify` prompt with
{{SCOPE}} {{PROFILE}} {{WORKDIR}} as paths and {{FINDINGS}} as its own findings, then the verify schema. A
group runs as a Claude agent on the run's stage model, reading the code and writing nothing, and its answer
goes to agents/verify-<group>.json.

With `stages.verify: false` in the run's manifest, `prepare` marks every finding `unverified` and writes
findings.json and report.md straight away, without 3-verified.json: the stage is off, not empty. With no
finding to verify it writes all three: the stage ran and had nothing to check.

`apply` groups the findings again and reads each group's answer: the JSON object of the verify schema,
optionally wrapped in prose or a ```json fence.
  - confirmed and refined stay in findings; a refined verdict's non-empty corrections replace the fields;
  - rejected leaves the report, and a `rejected` event keeps the finding with the verifier's reason;
  - immaterial and pre_existing move to their own lists;
  - a finding with no verdict, or with a verdict outside the enum, stays as `unverified`;
  - a group without a readable answer leaves all its findings unverified, with an `agent_degraded` event.
Open questions are never verified. Applying again replaces the events of the earlier apply. report.md is in
Russian, like everything else the user reads.

Prints one JSON line. A run, configuration or answer error prints {"error": ...} and exits 1.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cross_config  # noqa: E402
import review_collect  # noqa: E402
import review_run  # noqa: E402
import review_synth  # noqa: E402
from cross_config import ConfigError  # noqa: E402

STAGE = "verify"
VERDICTS = ("confirmed", "refined", "rejected", "immaterial", "pre_existing")
UNVERIFIED = "unverified"
MOVED = ("immaterial", "pre_existing")
CORRECTIONS = ("line", "end_line", "severity", "confidence", "title", "body", "fix")
# what a verifier sees of a finding: the fields it judges, without the synthesis bookkeeping
SHOWN = ("id", "file", "line", "end_line", "severity", "confidence", "title", "body", "fix", "sources", "lenses")
THIN_GROUP = 2
LABEL_KEYS = 3
LABEL_UNSAFE = re.compile(r"[^a-zA-Z0-9_.-]+")

SEVERITY_HEADINGS = {"critical": "Критические", "major": "Серьёзные", "minor": "Незначительные"}
VERDICT_WORDS = {"confirmed": "подтверждено", "refined": "уточнено", "rejected": "отклонено",
                 "immaterial": "несущественно", "pre_existing": "было до изменений", UNVERIFIED: "не проверено"}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def load_run(run_dir: Path) -> tuple[Path, dict, dict]:
    """The repository, the manifest and stages/2-synthesized.json of a synthesized run."""
    repo, _, _ = review_run.locate(run_dir)
    manifest = review_synth.read_manifest(run_dir)
    try:
        synthesized = json.loads((run_dir / "stages" / "2-synthesized.json").read_text())
    except (OSError, ValueError):
        raise ConfigError(f"{run_dir}: no readable stages/2-synthesized.json, run review_synth.py first") from None
    if (run_dir / "debate").exists():
        raise ConfigError(f"{run_dir}: the debate already started over this verification, start a new run")
    return repo, manifest, synthesized


def group_key(f: dict, by_source: bool) -> str:
    """The bucket of a finding: its first source, or the directory of its file; `.` for neither."""
    if by_source:
        return f["sources"][0] if f.get("sources") else "."
    return posixpath.normpath(posixpath.dirname(f["file"])) if f.get("file") else "."


def slug(key: str) -> str:
    return LABEL_UNSAFE.sub("-", key).strip("-.") or "root"


def label(keys: list[str]) -> str:
    """A filename-safe name for the keys a group covers: the first three, then how many more."""
    named = "+".join(slug(k) for k in keys[:LABEL_KEYS]) or "root"
    return named + (f"+{len(keys) - LABEL_KEYS}-more" if len(keys) > LABEL_KEYS else "")


def group(findings: list[dict], by: str, limit: int) -> list[dict]:
    """The verify groups, in order: [{name, keys, findings}] with collision-free names."""
    by_source = by == "source"
    buckets: dict[str, list[dict]] = {}
    for f in findings:
        buckets.setdefault(group_key(f, by_source), []).append(f)
    groups: list[dict] = []
    thin: dict = {"keys": [], "findings": []}
    for key in sorted(buckets):
        # one finding does not earn a verifier of its own; directory locality is what makes merging them sound
        if not by_source and len(buckets[key]) < THIN_GROUP:
            thin["keys"].append(key)
            thin["findings"] += buckets[key]
            continue
        groups.append({"keys": [key], "findings": buckets[key]})
    if thin["findings"]:
        groups.append(thin)
    if limit > 0 and len(groups) > limit:
        ordered = sorted(groups, key=lambda g: len(g["findings"]))
        cut = len(ordered) - limit + 1
        merged = {"keys": [k for g in ordered[:cut] for k in g["keys"]],
                  "findings": [f for g in ordered[:cut] for f in g["findings"]]}
        groups = sorted([merged, *ordered[cut:]], key=lambda g: label(g["keys"]))
    out, taken = [], set()
    for g in groups:
        name = base = label(g["keys"])
        n = 2
        while name in taken:
            name, n = f"{base}-{n}", n + 1
        taken.add(name)
        out.append({"name": name, **g})
    return out


def run_groups(manifest: dict, synthesized: dict) -> list[dict]:
    stages = manifest["stages"]
    return group(synthesized["findings"], stages.get("verify_group_by", "dir"), stages.get("verify_groups", 0))


def agent_name(g: dict) -> str:
    return f"{STAGE}-{g['name']}"


def own_event(line: str) -> bool:
    """An event an earlier apply wrote; prepare's stage event stays, it times the stage."""
    try:
        event = json.loads(line)
        return event.get("stage") == STAGE and event.get("kind") != "stage"
    except (ValueError, AttributeError):
        return False


def finish(run_dir: Path, manifest: dict, rep: dict, notes: list[str]) -> dict:
    """Write findings.json in the run format and report.md for the user."""
    stats = rep.get("stats") or {}
    started_at = stats.get("started_at") or manifest.get("started_at")
    started = review_run.parse_time(started_at)
    finished = datetime.now().astimezone()
    final = {
        "scope": rep["scope"],
        "sources": rep["sources"],
        "findings": rep["findings"],
        "open_questions": rep.get("open_questions") or [],
        "pre_existing": rep.get("pre_existing") or [],
        "immaterial": rep.get("immaterial") or [],
        "stats": {"started_at": started_at, "finished_at": finished.isoformat(timespec="seconds"),
                  "duration_ms": int((finished - started).total_seconds() * 1000) if started else 0,
                  "tokens": stats.get("tokens", 0), "stages": stats.get("stages") or []},
    }
    path = run_dir / "findings.json"
    path.write_text(json.dumps(final, ensure_ascii=False, indent=2) + "\n")
    report = run_dir / "report.md"
    report.write_text(markdown(final, notes))
    return {"findings_json": str(path), "report": str(report), "findings": len(final["findings"])}


def location(f: dict) -> str:
    if not f.get("file"):
        return "(без файла)"
    line, end = f.get("line") or 0, f.get("end_line") or 0
    if line <= 0:
        return f["file"]
    return f"{f['file']}:{line}-{end}" if end > line else f"{f['file']}:{line}"


def finding_lines(f: dict) -> list[str]:
    out = [f"### {f['title']}", "", f"`{location(f)}`", ""]
    if f.get("body"):
        out += [f["body"], ""]
    if f.get("fix"):
        out += [f"Исправление: {f['fix']}", ""]
    meta = [f"id: {f['id']}", f"уверенность: {f['confidence']}"]
    if f.get("sources"):
        meta.append(f"источники: {', '.join(f['sources'])}")
    if f.get("lenses"):
        meta.append(f"линзы: {', '.join(f['lenses'])}")
    if f.get("verdict"):
        meta.append(f"вердикт: {VERDICT_WORDS.get(f['verdict'], f['verdict'])}")
    return out + ["_" + " | ".join(meta) + "_", ""]


def model_of(agent: dict) -> str:
    actual, requested = agent.get("actual_model") or "", agent.get("requested_model") or ""
    if not actual:
        return requested or "-"
    if not requested or actual == requested:
        return actual
    return f"{actual} (запрошена {requested})"


def markdown(rep: dict, notes: list[str]) -> str:
    """report.md: the run's report layout, worded in Russian, with the verify summary under the header."""
    scope = rep["scope"]
    title = "# Ревью"
    if scope.get("task"):
        title += f": {scope['task']}" + (f" / {scope['run']}" if scope.get("run") else "")
    out = [title, ""]
    if scope.get("scope_path"):
        out += [f"Предмет: `{scope['scope_path']}`", ""]
    sources = rep["sources"]
    degraded = review_synth.degraded_names(rep)
    if degraded:
        out += [f"**Раунд неполный**: ответили {sources.get('reported', 0)} из {sources.get('expected', 0)} "
                f"источников, нет {', '.join(degraded)}", ""]
    for note in notes:
        out += [note, ""]
    findings = sorted(rep["findings"], key=lambda f: review_synth.rank(f["severity"]))
    if not findings:
        out += ["Находок нет.", ""]
    for i, f in enumerate(findings):
        if i == 0 or f["severity"] != findings[i - 1]["severity"]:
            out += [f"## {SEVERITY_HEADINGS.get(f['severity'], f['severity'] or 'Без severity')}", ""]
        out += finding_lines(f)
    for heading, name in (("Открытые вопросы", "open_questions"), ("Было до изменений", "pre_existing"),
                          ("Несущественное", "immaterial")):
        if rep[name]:
            out += [f"## {heading}", ""]
            for f in rep[name]:
                out += finding_lines(f)
    agents = sources.get("agents") or []
    if agents:
        out += ["## Источники", "", "| агент | исполнитель | модель | effort | находок | статус |",
                "| --- | --- | --- | --- | --- | --- |"]
        for a in agents:
            status = "ответил"
            if a.get("degraded") or a["name"] in degraded:
                status = "не ответил"
            elif not a.get("raised"):
                status = "ответил, ничего не нашёл"
            out.append(f"| {a['name']} | {a.get('executor', '')} | {model_of(a)} | {a.get('effort', '')} | "
                       f"{a.get('raised', 0)} | {status} |")
        out.append("")
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def stage_entry(stages: dict, duration: int, ran: bool) -> dict:
    """The verify row of stats.stages; a stage that dispatched nothing names no runner."""
    entry = {"name": STAGE, "duration_ms": duration}
    if ran:
        entry.update(executor="claude", model=stages["model"], effort=stages["effort"])
    return entry


def with_stage(rep: dict, entry: dict) -> dict:
    stats = dict(rep.get("stats") or {})
    stats["stages"] = [s for s in stats.get("stages") or [] if s.get("name") != STAGE] + [entry]
    return {**rep, "stats": stats}


def prepare(run_dir: Path) -> dict:
    repo, manifest, synthesized = load_run(run_dir)
    stages = manifest["stages"]
    prompts_dir = run_dir / "prompts" / "stages"
    for stale in prompts_dir.glob(f"{STAGE}-*.md"):
        stale.unlink()

    if not stages.get("verify", True):
        rep = {**synthesized, "findings": [{**f, "verdict": UNVERIFIED} for f in synthesized["findings"]]}
        out = finish(run_dir, manifest, rep, ["Верификация выключена в настройках прогона: находки не проверялись."])
        review_synth.append_events(run_dir, [{"kind": "stage", "stage": STAGE, "text": "skipped: stages.verify is off",
                                              "at": now()}])
        return {"skipped": True, "reason": "stages.verify is off for this run", **out}

    groups = run_groups(manifest, synthesized)
    if not groups:
        verified = with_stage(synthesized, stage_entry(stages, 0, False))
        (run_dir / "stages" / "3-verified.json").write_text(json.dumps(verified, ensure_ascii=False, indent=2) + "\n")
        out = finish(run_dir, manifest, verified, [])
        review_synth.append_events(run_dir, [{"kind": "stage", "stage": STAGE, "text": "skipped: no finding to verify",
                                              "at": now()}])
        return {"skipped": True, "reason": "no finding to verify", "verified": str(run_dir / "stages" / "3-verified.json"),
                **out}

    inp = review_run.input_paths(run_dir)
    values = {key.upper(): inp[key] if review_run.filled(Path(inp[key])) else review_run.NONE
              for key in ("scope", "profile")}
    values["WORKDIR"] = str(repo)
    template = cross_config.body(repo, "prompts", STAGE)
    schema = cross_config.resolve(repo, "schemas", STAGE).read_text().strip()
    prompts_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "agents").mkdir(exist_ok=True)
    launch = []
    for g in groups:
        shown = [{k: f[k] for k in SHOWN if k in f} for f in g["findings"]]
        text = cross_config.render(template, {**values, "FINDINGS": json.dumps(shown, ensure_ascii=False, indent=2)},
                                   f"prompts/{STAGE}.md")
        prompt = prompts_dir / f"{agent_name(g)}.md"
        prompt.write_text(text + "\n\n" + review_run.OUTPUT_CONTRACT + schema + "\n")
        launch.append({"group": g["name"], "keys": g["keys"], "findings": [f["id"] for f in g["findings"]],
                       "prompt": str(prompt), "output": str(run_dir / "agents" / f"{agent_name(g)}.json")})
    review_synth.record_prompts(run_dir, manifest, repo, STAGE)
    review_synth.append_events(run_dir, [{"kind": "stage", "stage": STAGE, "at": now()}])
    return {"skipped": False, "stage": STAGE, "vendor": "claude", "alias": stages["model"], "effort": stages["effort"],
            "group_by": stages.get("verify_group_by", "dir"), "findings": len(synthesized["findings"]),
            "groups": len(groups), "launch": launch}


def read_verdicts(path: Path) -> tuple[list | None, str]:
    """The verdict entries of a group's answer, or None and why the group is degraded."""
    if not path.is_file():
        return None, f"no answer: {path.name} is missing"
    try:
        answer = review_collect.extract_json(path.read_text(errors="replace"))
    except ValueError as e:
        return None, f"answer is not JSON: {e}"
    if not isinstance(answer, dict) or not isinstance(answer.get("verdicts"), list):
        return None, "answer does not match the verify schema: expected an object with a verdicts array"
    return answer["verdicts"], ""


def corrected(f: dict, d: dict, props: dict) -> dict:
    """The finding under its verdict. Only refined carries corrections, and a field that is empty, zero or
    outside the schema leaves the original value in place."""
    out = {**f, "verdict": d["verdict"]}
    if d["verdict"] == "refined":
        for key in CORRECTIONS:
            value = d.get(key)
            if value and not review_collect.check_schema(value, props[key]):
                out[key] = value
    return out


def shifted(before: list[dict], after: list[dict]) -> str:
    """What the verifier changed beyond keeping or rejecting, worded for the agent_done event."""
    was = {f["id"]: review_synth.rank(f["severity"]) for f in before}
    lowered = sum(review_synth.rank(f["severity"]) > was[f["id"]] for f in after)
    raised = sum(review_synth.rank(f["severity"]) < was[f["id"]] for f in after)
    refined = sum(f["verdict"] == "refined" for f in after)
    parts = [f"{n} {what}" for n, what in ((lowered, "lowered"), (raised, "raised"), (refined, "refined")) if n]
    return ", " + ", ".join(parts) if parts else ""


def apply(run_dir: Path) -> dict:
    _, manifest, synthesized = load_run(run_dir)
    stages = manifest["stages"]
    if not stages.get("verify", True):
        raise ConfigError("stages.verify is off for this run: prepare already wrote findings.json, go to the debate")
    groups = run_groups(manifest, synthesized)
    if not groups:
        raise ConfigError("no finding to verify: prepare already wrote findings.json, go to the debate")
    schema = json.loads(cross_config.resolve(None, "schemas", STAGE).read_text())
    props = schema["properties"]["verdicts"]["items"]["properties"]

    judged: dict[str, dict | None] = {}
    events, degraded, ignored = [], [], []
    for g in groups:
        agent = agent_name(g)
        verdicts, reason = read_verdicts(run_dir / "agents" / f"{agent}.json")
        if verdicts is None:
            degraded.append({"group": g["name"], "reason": reason})
            events.append({"kind": "agent_degraded", "agent": agent, "stage": STAGE, "text": reason, "at": now()})
            judged.update((f["id"], {**f, "verdict": UNVERIFIED}) for f in g["findings"])
            continue
        ids = {f["id"] for f in g["findings"]}
        by_id: dict[str, dict] = {}
        for d in verdicts:
            if isinstance(d, dict) and d.get("id") in ids:
                by_id[d["id"]] = d
            else:
                ignored.append(str(d.get("id")) if isinstance(d, dict) else repr(d))
        kept, rejected = [], []
        for f in g["findings"]:
            d = by_id.get(f["id"])
            if d is None or d.get("verdict") not in VERDICTS:
                judged[f["id"]] = {**f, "verdict": UNVERIFIED}
            elif d["verdict"] == "rejected":
                judged[f["id"]] = None
                rejected.append({**f, "verdict": "rejected", "reason": str(d.get("reason") or "")})
                continue
            else:
                judged[f["id"]] = corrected(f, d, props)
            kept.append(judged[f["id"]])
        if rejected:
            events.append({"kind": "rejected", "agent": agent, "stage": STAGE,
                           "text": f"{len(rejected)} findings rejected", "findings": rejected, "at": now()})
        events.append({"kind": "agent_done", "agent": agent, "stage": STAGE,
                       "text": f"{len(kept)} of {len(g['findings'])} kept{shifted(g['findings'], kept)}",
                       "verdicts": [{"id": i, "verdict": d.get("verdict"), "reason": d.get("reason", "")}
                                    for i, d in by_id.items()], "at": now()})

    outcome = [f for f in (judged[raw["id"]] for raw in synthesized["findings"]) if f is not None]
    lists = {name: [f for f in outcome if f["verdict"] == name] for name in MOVED}
    findings = [f for f in outcome if f["verdict"] not in MOVED]
    lines = review_synth.read_events(run_dir)
    kept_lines = [line for line in lines if not own_event(line)]
    (run_dir / "events.jsonl").write_text("".join(line + "\n" for line in kept_lines))
    review_synth.append_events(run_dir, events)

    verified = with_stage({**synthesized, "findings": findings,
                           "pre_existing": (synthesized.get("pre_existing") or []) + lists["pre_existing"],
                           "immaterial": (synthesized.get("immaterial") or []) + lists["immaterial"]},
                          stage_entry(stages, review_synth.stage_duration(lines, STAGE), True))
    path = run_dir / "stages" / "3-verified.json"
    path.write_text(json.dumps(verified, ensure_ascii=False, indent=2) + "\n")

    count = {v: [i for i, f in judged.items() if (f["verdict"] if f else "rejected") == v]
             for v in (*VERDICTS, UNVERIFIED)}
    notes = ["Верификация: " + ", ".join(f"{VERDICT_WORDS[v]} {len(count[v])}"
                                         for v in (*VERDICTS, UNVERIFIED)) + "."]
    if degraded:
        notes.append("Не ответили верификаторы групп " + ", ".join(d["group"] for d in degraded)
                     + ": их находки остались непроверенными.")
    out = finish(run_dir, manifest, verified, notes)
    return {"verified": str(path), **out, "groups": len(groups), "degraded": degraded,
            "confirmed": len(count["confirmed"]), "refined": len(count["refined"]), "rejected": count["rejected"],
            "immaterial": count["immaterial"], "pre_existing": count["pre_existing"], "unverified": count[UNVERIFIED],
            "ignored": ignored}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", metavar="{prepare,apply}", required=True)
    sub.add_parser("prepare", help="write the verify prompts and print the launch plan").add_argument(
        "run_dir", help="the run directory, after review_synth.py")
    sub.add_parser("apply", help="apply the verdicts and write 3-verified.json, findings.json and report.md").add_argument(
        "run_dir", help="the run directory, after the verifiers answered")
    args = ap.parse_args(argv)
    run_dir = Path(args.run_dir).resolve()
    try:
        out = prepare(run_dir) if args.command == "prepare" else apply(run_dir)
    except ConfigError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
