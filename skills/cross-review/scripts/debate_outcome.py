#!/usr/bin/env python3
"""Fold the run and both debate rounds into one outcome per finding.

Reads items.json, for-*.json, claude-r1/codex-r1, claude-r2/codex-r2, moderator.json, extra.json (the last
four may be absent; without items.json or for-*.json, or with one not in the shape debate_build.py writes, prints
{"error": ...} and exits 1). An extra.json entry must carry the id of a round-2 side finding; the moderator only
confirms it and sets its severity — file, line, title, body and fix come from the side's round-2 answer. Writes to <debate_dir>, <run_dir>/debate/ by default:
  final.json      every item with `outcome`, `final_severity` and its `thread`, plus open questions and meta
  disputes.json   what the moderator still has to decide: disputed items with their threads, and side findings
                  nobody verified yet — the input prompt-moderator.md names
Prints one JSON line (counts, disputed ids, pending and rejected side findings); --markdown prints the Russian
summary instead. Once extra.json exists the moderator has seen every side finding: those it left out are rejected,
not pending. A side's or a moderator's answer that is not JSON or breaks its schema prints {"error": ...} and exits 1.

Outcomes: consensus (both sides raised it, the pipeline kept it) · confirmed (the other side agreed) · refined
(severity or claim changed and both sides accept it) · restored (the pipeline had set it aside, the other side says
it is real) · dropped (the author conceded, or both agree on no severity) · disputed (still open, decided by the
moderator, or left open by two independent moderators that split or where one did not answer — then a human
decides) · side (found during the debate,
verified by the moderator) · question (an open question with the sides' answers) · unexamined (no verdict arrived
for it — a side did not finish; an item both sides were asked to examine needs both verdicts).

To fix (`actionable`) is what has a severity and is settled: neither unexamined nor a dispute the moderator has not
decided — those wait for a side, the moderator or a human — nor a defect that predates the change (`pre_existing`),
which keeps its outcome but is listed apart, outside the counts.

The author's answer settles an objection only when it lands on the examiner's severity: `concede` takes it, `amend`
at the examiner's severity is `refined`. An `amend` at any other severity is no agreement — the item is `disputed`
and goes to the moderator with both positions in its thread.

Every item carries `severity_shift` {from, to, direction, by}: `from` is the severity after synthesis and verify —
`none` for an item the pipeline set aside, which stays at `none` unless the debate restores it — `to` the final
one; `direction` is raised, lowered, same, dropped (to `none`) or new (a side finding); `by` names who set
the final severity — side (the examiner's verdict), author (the author's amend), moderator, or none when it did
not change. The JSON line lists the ids under `reprioritized` {raised, lowered, dropped}; the markdown has a
«Переоценка приоритетов» section with «было → стало» and who changed it.

An item whose `vendors` names something other than claude or codex — a source outside the roster that
debate_build.py passed through — was sent to both sides, like one both found and the pipeline set aside: it needs
both verdicts. The run's findings.json feeds `meta` (the agents, the counts, the stage models) and is read by
shape: a field of the wrong type is an {"error": ...}, a missing one is left empty.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from debate_prompts import DECISIONS_SCHEMA, EXAMINE_SCHEMA, EXTRA_SCHEMA, RESPOND_SCHEMA, load_answer  # noqa: E402
from review_collect import check_schema  # noqa: E402

OTHER = {"claude": "codex", "codex": "claude"}
SEV_RANK = {"critical": 0, "major": 1, "minor": 2, "none": 3}
OUT_RANK = {"consensus": 0, "confirmed": 1, "restored": 2, "refined": 3, "disputed": 4, "side": 5, "unexamined": 6, "question": 7, "dropped": 8}
SET_ASIDE = ("dropped_by_synthesis", "rejected_by_verify", "immaterial")
SHIFTS = ("raised", "lowered", "dropped")
WITH_ID = {"type": "object", "required": ["id"], "properties": {"id": {"type": "string"}}}
STRINGS = {"type": "array", "items": {"type": "string"}}
OPTIONAL_STRING = {"type": ["string", "null"]}
COUNT = {"type": "integer", "minimum": 0}
# what debate_build.py copies from a finding and adds to it; a card without the content is no finding at all
FINDING = {"id": {"type": "string"}, "file": {"type": "string"}, "line": {"type": "integer", "minimum": 0},
           "severity": {"type": "string", "enum": ["critical", "major", "minor"]}, "title": {"type": "string"},
           "body": {"type": "string"}, "fix": {"type": "string"}, "sources": STRINGS, "vendors": STRINGS}
CONTENT = ["id", "file", "line", "title", "body", "sources", "vendors"]
ITEMS_SCHEMA = {"type": "array", "items": {
    "type": "object", "required": [*CONTENT, "severity", "status"],
    "properties": {**FINDING, "status": {"type": "string", "enum": ["confirmed", "refined", "unverified", "immaterial", "pre_existing",
                                                                    "rejected_by_verify", "dropped_by_synthesis"]}}}}
FOR_SCHEMA = {"type": "object", "required": ["findings", "open_questions"],
              "properties": {"findings": {"type": "array", "items": WITH_ID},
                             "open_questions": {"type": "array", "items": {"type": "object", "required": CONTENT, "properties": FINDING}}}}
BUILT = {"items.json": ITEMS_SCHEMA, "for-claude.json": FOR_SCHEMA, "for-codex.json": FOR_SCHEMA}
# what `meta` takes from the run's findings.json, as review_verify.py or an external run writes it; every key is optional
AGENT = {"type": "object", "required": ["name", "executor", "raised"],
         "properties": {"name": {"type": "string"}, "executor": {"type": "string"}, "actual_model": OPTIONAL_STRING,
                        "requested_model": OPTIONAL_STRING, "effort": OPTIONAL_STRING, "raised": COUNT,
                        "degraded": {"type": ["boolean", "null"]}}}
STAGE = {"type": "object", "properties": {"name": OPTIONAL_STRING, "model": OPTIONAL_STRING}}
FINDINGS_SCHEMA = {"type": "object", "properties": {
    "scope": {"type": ["object", "null"], "properties": {"task": OPTIONAL_STRING, "run": OPTIONAL_STRING}},
    "stats": {"type": ["object", "null"], "properties": {"duration_ms": {"type": ["number", "null"], "minimum": 0},
                                                         "started_at": OPTIONAL_STRING,
                                                         "stages": {"type": ["array", "null"], "items": STAGE}}},
    "sources": {"type": ["object", "null"], "properties": {"agents": {"type": ["array", "null"], "items": AGENT},
                                                           "degraded": {"type": ["array", "null"], "items": {"type": "string"}}}},
    "findings": {"type": ["array", "null"]}, "immaterial": {"type": ["array", "null"]}, "pre_existing": {"type": ["array", "null"]},
}}
RU_OUT = {
    "consensus": "Консенсус, нашли обе стороны", "confirmed": "Подтвердила другая сторона", "restored": "Восстановлены после отсева",
    "refined": "Уточнены в обсуждении", "disputed": "Спорные", "side": "Найдены в споре", "question": "Открытые вопросы",
    "unexamined": "Не проверены другой стороной", "dropped": "Сняты",
}
RU_SHIFT = {"raised": "Повышено", "lowered": "Понижено", "dropped": "Снято"}
RU_BY = {"side": "по вердикту проверяющей стороны", "author": "уточнение автора, проверяющая сторона согласна",
         "moderator": "решение модератора"}


def plural(n: int, one: str, few: str, many: str) -> str:
    """`n` with the Russian noun form for it: 1 пункт, 2 пункта, 5 пунктов, 11 пунктов, 21 пункт."""
    if n % 10 == 1 and n % 100 != 11:
        return f"{n} {one}"
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return f"{n} {few}"
    return f"{n} {many}"


def load_findings(path: Path) -> dict:
    """The run's findings.json for `meta`, checked by shape; {} when the run has none."""
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text())
    except ValueError as e:
        raise ValueError(f"{path}: not JSON: {e}") from None
    errors = check_schema(value, FINDINGS_SCHEMA, "findings.json")
    if errors:
        raise ValueError(f"{path}: not a review's findings.json: {'; '.join(errors[:5])}")
    return value


def load_built(path: Path) -> object:
    """What debate_build.py wrote, checked by its shape: an `{}` in place of the item list is no empty debate."""
    try:
        value = json.loads(path.read_text())
    except ValueError as e:
        raise ValueError(f"{path}: not JSON: {e}, run debate_build.py again") from None
    errors = check_schema(value, BUILT[path.name], path.name)
    if errors:
        raise ValueError(f"{path}: not the output of debate_build.py, run it again: {'; '.join(errors[:5])}")
    return value


def severity_shift(base: str | None, final: str, by: str) -> dict:
    """How the final severity differs from the one the pipeline gave; base None is a side finding."""
    if base is None:
        return {"from": None, "to": final, "direction": "new", "by": by}
    if final == base:
        return {"from": base, "to": final, "direction": "same", "by": "none"}
    if final == "none":
        direction = "dropped"
    else:
        direction = "raised" if SEV_RANK.get(final, 9) < SEV_RANK.get(base, 9) else "lowered"
    return {"from": base, "to": final, "direction": direction, "by": by}


def pipeline_of(round_dir: Path) -> str:
    """cross-review for a run this plugin composed (its manifest names a tier), external for any other run."""
    try:
        manifest = json.loads((round_dir / "manifest.json").read_text())
    except (OSError, ValueError):
        return "external"
    return "cross-review" if isinstance(manifest, dict) and "tier" in manifest else "external"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("round_dir", help="the run directory with findings.json")
    ap.add_argument("debate_dir", nargs="?", default=None,
                    help="the debate directory with items.json and the sides' answers; default: <round_dir>/debate")
    ap.add_argument("--markdown", action="store_true", help="print the Russian summary instead of the JSON line")
    args = ap.parse_args(argv)
    round_dir = Path(args.round_dir).resolve()
    d = Path(args.debate_dir).resolve() if args.debate_dir else round_dir / "debate"
    try:
        return fold(round_dir, d, args.markdown)
    except ValueError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1


def fold(round_dir: Path, d: Path, markdown: bool) -> int:
    # what debate_build.py writes is the input itself: without it an empty outcome would pass for a clean one
    missing = [name for name in BUILT if not (d / name).is_file()]
    if missing:
        raise ValueError(f"{d}: no {', '.join(missing)}, run debate_build.py first")
    items = load_built(d / "items.json")
    examined_by = {}  # id -> set of sides that were asked to examine it
    questions_for = {}
    for side in ("claude", "codex"):
        f = load_built(d / f"for-{side}.json")
        for it in f["findings"]:
            examined_by.setdefault(it["id"], set()).add(side)
        for q in f["open_questions"]:
            questions_for.setdefault(q["id"], {"q": q, "sides": set()})["sides"].add(side)
    verdicts = {}  # id -> {examiner: verdict}
    answers = {}  # question id -> {side: answer}
    for side in ("claude", "codex"):
        r1 = load_answer(d / f"{side}-r1.json", EXAMINE_SCHEMA, {})
        for v in r1.get("verdicts", []):
            verdicts.setdefault(v["id"], {})[side] = v
        for a in r1.get("open_question_answers", []):
            answers.setdefault(a["id"], {})[side] = a
    responses = {}  # id -> {author: response}
    side_findings = []
    for side in ("claude", "codex"):
        r2 = load_answer(d / f"{side}-r2.json", RESPOND_SCHEMA, {})
        for r in r2.get("responses", []):
            responses.setdefault(r["id"], {})[side] = r
        for n, sf in enumerate(r2.get("side_findings", []), 1):
            side_findings.append({"id": f"{side}-side-{n}", "sources": [side], "vendors": [side], **sf})
    moderator = {m["id"]: m for m in load_answer(d / "moderator.json", DECISIONS_SCHEMA, [])}
    moderated_side = (d / "extra.json").exists()
    extra = load_answer(d / "extra.json", EXTRA_SCHEMA, [])
    extra_ids = {e["id"] for e in extra}
    # the moderator confirms what a side raised in round 2; an id no side raised is not a side finding
    raised = {sf["id"]: sf for sf in side_findings}
    unknown = sorted(extra_ids - set(raised))
    if unknown:
        raise ValueError(f"{d / 'extra.json'}: {', '.join(unknown)} is no side finding of round 2, rerun the moderator")

    def worst(sevs):
        sevs = [s for s in sevs if s in SEV_RANK]
        return min(sevs, key=lambda s: SEV_RANK[s]) if sevs else "minor"

    out_items = []
    for item in items:
        vendors = set(item.get("vendors") or [])
        status = item.get("status") or "confirmed"
        thread = [{"kind": "finding", "side": "+".join(sorted(vendors)) or "unknown", "sources": item["sources"]},
                  {"kind": "pipeline", "status": status}]
        base = item.get("severity") or "minor"
        # what the pipeline left to fix: a set-aside item is at none until the debate restores it
        kept = "none" if status in SET_ASIDE else base
        final, by = kept, "none"
        outcome = None
        if vendors == {"claude", "codex"} and status in ("confirmed", "refined"):
            outcome = "consensus"
        else:
            vs = verdicts.get(item["id"], {})
            for examiner in sorted(vs):
                thread.append({"kind": "examine", "side": examiner, **vs[examiner]})
            author = next(iter(vendors)) if len(vendors) == 1 else None
            if author not in OTHER:
                # both sides examined it: both found it and the pipeline set it aside, nobody is named, or the
                # source is outside the roster, so neither side is its author
                stances = [v["stance"] for v in vs.values()]
                # every side that was asked has to answer: one verdict alone settles nothing
                if not vs or examined_by.get(item["id"], set(OTHER)) - set(vs):
                    outcome = "unexamined"
                elif all(s in ("real", "real_but_different") for s in stances):
                    final, by = worst(v["severity"] for v in vs.values()), "side"
                    # restored only when the pipeline had set it aside; an unverified or pre-existing one was kept
                    outcome = "restored" if status in SET_ASIDE else "confirmed" if final == base else "refined"
                elif all(s in ("not_real", "not_worth_fixing") for s in stances):
                    outcome, final, by = "dropped", "none", "side"
                else:
                    outcome = "disputed"
            else:
                v = vs.get(OTHER[author])
                if v is None:
                    outcome = "unexamined"
                elif v["stance"] == "real" and v["severity"] == item["severity"]:
                    outcome = "restored" if status in SET_ASIDE else "confirmed"
                    final, by = v["severity"], "side"
                else:
                    rr = responses.get(item["id"], {}).get(author)
                    if rr:
                        thread.append({"kind": "respond", "side": author, **rr})
                    if rr is None or rr["position"] == "defend":
                        outcome = "disputed"
                    elif rr["position"] == "concede":
                        if v["stance"] in ("not_real", "not_worth_fixing"):
                            outcome, final, by = "dropped", "none", "side"
                        else:
                            outcome, final, by = "refined", v["severity"], "side"
                    elif rr["severity"] != v["severity"]:
                        # an amend that keeps a severity the examiner did not give is still a dispute
                        outcome = "disputed"
                    else:
                        outcome, final, by = "refined", rr["severity"], "author"
                    if outcome == "refined" and status in SET_ASIDE:
                        outcome = "restored"
        if outcome in ("refined", "restored") and final == "none":
            outcome = "dropped"
        decided = split = False
        if outcome == "disputed" and item["id"] in moderator:
            m = moderator[item["id"]]
            thread.append({"kind": "moderator", **m})
            if m.get("split") or m.get("missing"):
                split = True
            else:
                final, by, decided = m["severity"], "moderator", True
        out_items.append({**{k: item.get(k) for k in ("id", "file", "line", "end_line", "title", "body", "fix", "severity", "confidence", "sources", "vendors", "status")},
                          "outcome": outcome, "final_severity": final, "severity_shift": severity_shift(kept, final, by),
                          "moderated": decided, "moderators_split": split, "thread": thread})

    for e in extra:
        # the moderator confirms what the side raised: the finding itself comes from round 2, not from the moderator,
        # so an entry that reuses a real id cannot put another finding under it
        sf = raised[e["id"]]
        side = sf["vendors"][0]
        out_items.append({"id": e["id"], **{k: sf.get(k) for k in ("file", "line", "title", "body", "fix")}, "severity": e["severity"],
                          "sources": [side], "vendors": [side],
                          "status": "side_finding", "outcome": "side", "final_severity": e["severity"],
                          "severity_shift": severity_shift(None, e["severity"], "moderator"), "moderated": True, "moderators_split": False,
                          "thread": [{"kind": "finding", "side": side, "sources": [f"{side}, раунд 2"]},
                                     {"kind": "moderator", "verdict": "подтверждаю по коду", "severity": e["severity"], "note": e.get("note", "")}]})

    out_questions = []
    for qid, entry in questions_for.items():
        q = entry["q"]
        thread = [{"kind": "finding", "side": "+".join(q.get("vendors") or []) or "unknown", "sources": q.get("sources") or []}]
        for side, a in sorted(answers.get(qid, {}).items()):
            thread.append({"kind": "answer", "side": side, **a})
        out_questions.append({**{k: q.get(k) for k in ("id", "file", "line", "title", "body", "sources", "vendors")},
                              "status": "open_question", "outcome": "question", "final_severity": "none",
                              "answered_by": sorted(answers.get(qid, {})), "asked": sorted(entry["sides"]), "thread": thread})

    out_items.sort(key=lambda r: (SEV_RANK.get(r["final_severity"], 9), OUT_RANK.get(r["outcome"], 9), r.get("file") or "", r.get("line") or 0))
    undecided_side = [sf for sf in side_findings if sf["id"] not in extra_ids]
    pending_side, rejected_side = ([], undecided_side) if moderated_side else (undecided_side, [])
    disputed_open = [i for i in out_items if i["outcome"] == "disputed" and i["id"] not in moderator]
    split_ids = [i["id"] for i in out_items if i["moderators_split"]]
    unexamined = [i["id"] for i in out_items if i["outcome"] == "unexamined"]
    reprioritized = {direction: [i["id"] for i in out_items if i["severity_shift"]["direction"] == direction]
                     for direction in SHIFTS}

    findings = load_findings(round_dir / "findings.json")
    stats = findings.get("stats") or {}
    agents = (findings.get("sources") or {}).get("agents") or []
    meta = {
        "task": (findings.get("scope") or {}).get("task"), "run": (findings.get("scope") or {}).get("run"),
        "round_dir": str(round_dir), "debate_dir": str(d), "pipeline": pipeline_of(round_dir),
        "agents": [{k: a.get(k) for k in ("name", "executor", "actual_model", "requested_model", "effort", "raised", "degraded")} for a in agents],
        "degraded": (findings.get("sources") or {}).get("degraded") or [],
        "raw": sum(a.get("raised", 0) for a in agents),
        "synthesized": len(findings.get("findings") or []) + len(findings.get("immaterial") or []) + len(findings.get("pre_existing") or []),
        "duration_min": round((stats.get("duration_ms") or 0) / 60000),
        "started_at": stats.get("started_at"),
        "verify_model": next((s.get("model") for s in (stats.get("stages") or []) if s.get("name") == "verify"), None),
        "synthesis_model": next((s.get("model") for s in (stats.get("stages") or []) if s.get("name") == "synthesis"), None),
    }
    (d / "final.json").write_text(json.dumps({"items": out_items, "questions": out_questions, "meta": meta}, ensure_ascii=False, indent=2) + "\n")
    (d / "disputes.json").write_text(json.dumps({"disputes": disputed_open, "side_findings": pending_side}, ensure_ascii=False, indent=2) + "\n")

    counts = {}
    for i in out_items:
        counts[i["outcome"]] = counts.get(i["outcome"], 0) + 1
    before = [i for i in out_items if i["status"] == "pre_existing" and i["final_severity"] != "none" and i["outcome"] != "dropped"]
    actionable = [i for i in out_items if i["final_severity"] != "none" and i["outcome"] not in ("dropped", "question", "unexamined")
                  and (i["outcome"] != "disputed" or i["moderated"]) and i["status"] != "pre_existing"]
    by_sev = {s: sum(1 for i in actionable if i["final_severity"] == s) for s in ("critical", "major", "minor")}
    summary = {"items": len(out_items), "outcomes": counts, "actionable": len(actionable), "by_severity": by_sev,
               "needs_moderator": [i["id"] for i in disputed_open], "moderators_split": split_ids,
               "reprioritized": reprioritized, "side_findings_pending": [sf["id"] for sf in pending_side],
               "side_findings_rejected": [sf["id"] for sf in rejected_side],
               "unexamined": unexamined, "questions": len(out_questions), "pre_existing": [i["id"] for i in before],
               "degraded": meta["degraded"], "final": str(d / "final.json"), "disputes": str(d / "disputes.json")}
    if not markdown:
        print(json.dumps(summary, ensure_ascii=False))
        return 0

    def loc(i):
        return f"{i.get('file') or '?'}:{i.get('line') or 0}"

    lines = [f"**Итог: {plural(len(actionable), 'пункт', 'пункта', 'пунктов')} к исправлению** — {by_sev['critical']} critical, "
             f"{by_sev['major']} major, {by_sev['minor']} minor."]
    if meta["degraded"]:
        lines.append(f"**Неполный раунд:** выпали агенты {', '.join(meta['degraded'])}.")
    for outcome in ("consensus", "confirmed", "restored", "refined", "dropped", "disputed", "side", "unexamined"):
        group = [i for i in out_items if i["outcome"] == outcome and i not in before]
        if not group:
            continue
        lines.append(f"- **{RU_OUT[outcome]}, {len(group)}:**")
        for i in group:
            extra_note = ""
            shift = i["severity_shift"]
            if shift["direction"] in ("raised", "lowered"):
                extra_note = f" (было {'отложено' if shift['from'] == 'none' else shift['from']} → {shift['to']})"
            if outcome == "disputed":
                m = moderator.get(i["id"])
                if i["moderators_split"]:
                    extra_note += f" — {m['verdict']}" if m.get("missing") else " — модераторы разошлись, решает человек"
                else:
                    extra_note += f" — модератор: {m['verdict']}, {m['severity']}" if m else " — ждёт модератора"
            lines.append(f"  - `{i['id']}` [{i['final_severity']}] {loc(i)} — {i['title']}{extra_note}")
    if before:
        lines.append(f"- **Было до изменения, вне подсчёта, {len(before)}:**")
        for i in before:
            lines.append(f"  - `{i['id']}` [{i['final_severity']}] {loc(i)} — {i['title']} ({RU_OUT[i['outcome']].lower()})")
    shifted = [i for i in out_items if i["severity_shift"]["direction"] in SHIFTS]
    if shifted:
        counts_line = ", ".join(f"{RU_SHIFT[direction].lower()} {len(reprioritized[direction])}" for direction in SHIFTS)
        lines.append(f"- **Переоценка приоритетов:** {counts_line}.")
        for direction in SHIFTS:
            for i in (i for i in shifted if i["severity_shift"]["direction"] == direction):
                shift = i["severity_shift"]
                to = "снято" if shift["to"] == "none" else shift["to"]
                was = "отложено" if shift["from"] == "none" else shift["from"]
                lines.append(f"  - {RU_SHIFT[direction]}: `{i['id']}` {was} → {to} {loc(i)} — {i['title']} ({RU_BY[shift['by']]})")
    if out_questions:
        lines.append(f"- **{RU_OUT['question']}, {len(out_questions)}:**")
        for q in out_questions:
            lines.append(f"  - `{q['id']}` {loc(q)} — {q['title']} (ответили: {', '.join(q['answered_by']) or 'никто'})")
    if pending_side:
        lines.append(f"- **Попутные находки без проверки модератора, {len(pending_side)}:** " + ", ".join(f"`{sf['id']}` {sf['file']}:{sf['line']} — {sf['title']}" for sf in pending_side))
    if rejected_side:
        lines.append(f"- **Попутные находки, которые модератор не подтвердил, {len(rejected_side)}:** " + ", ".join(f"`{sf['id']}` {sf['file']}:{sf['line']} — {sf['title']}" for sf in rejected_side))
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
