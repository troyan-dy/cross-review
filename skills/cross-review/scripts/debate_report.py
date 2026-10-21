#!/usr/bin/env python3
"""Render final.json as the one-page report (an HTML fragment for the Artifact tool).

Needs final.json from debate_outcome.py. Every item card carries a «было → стало» badge when the debate changed its
severity, and a «Переоценка приоритетов» block counts what was raised, lowered and dropped. The footer names who
moderated by the files in the debate directory: the session, or — when moderator-claude.json or moderator-codex.json
is there — the independent pair of debate_moderators.py, with the models of the run's manifest.json when it has
them; --moderator overrides the line. Prints one JSON line with the counts per outcome and severity and the
reprioritized counts; without final.json, or with one that is not JSON or not in the shape debate_outcome.py writes,
prints {"error": ...} and exits 1 before writing the page.
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from debate_outcome import AGENT, OUT_RANK, SEV_RANK  # noqa: E402
from debate_prompts import EXAMINE_SCHEMA, RESPOND_SCHEMA, SIDE_NAME, SIDES  # noqa: E402
from review_collect import check_schema  # noqa: E402

TEMPLATE = Path(__file__).resolve().parent.parent / "assets" / "templates" / "report.html"
SHIFTS = ("raised", "lowered", "dropped")
SESSION_MODERATOR = "основная сессия Claude Code; это тоже Claude, поэтому его вывод помечен отдельно"
PIPELINE = {
    "cross-review": "cross-review (находки, склейка, проверка по коду)",
    "external": "внешний прогон",
}

# The shape of final.json as debate_outcome.py writes it. The page builds its markup from these values, so every
# one it uses is typed here, and the page still escapes each of them: a number is a number, a severity or an
# outcome is one of the known words, a side or a status is a string.
STRING = {"type": "string"}
OPTIONAL_STRING = {"type": ["string", "null"]}
STRINGS = {"type": "array", "items": STRING}
COUNT = {"type": "integer", "minimum": 0}
SEVERITY = {"type": "string", "enum": list(SEV_RANK)}
VERDICT = EXAMINE_SCHEMA["properties"]["verdicts"]["items"]["properties"]
RESPONSE = RESPOND_SCHEMA["properties"]["responses"]["items"]["properties"]
# the thread entries by kind; a `side` is claude, codex, claude+codex or the name of a source outside the roster
ENTRY = {
    "finding": {"required": ["side", "sources"], "properties": {"side": STRING, "sources": STRINGS}},
    "pipeline": {"required": ["status"], "properties": {"status": STRING}},
    "examine": {"required": ["side", "stance", "severity", "argument", "evidence"],
                "properties": {"side": STRING, "stance": VERDICT["stance"], "severity": SEVERITY, "argument": STRING, "evidence": STRING}},
    "respond": {"required": ["side", "position", "severity", "argument", "evidence"],
                "properties": {"side": STRING, "position": RESPONSE["position"], "severity": SEVERITY, "argument": STRING, "evidence": STRING}},
    "answer": {"required": ["side", "answer", "evidence"], "properties": {"side": STRING, "answer": STRING, "evidence": STRING}},
    "moderator": {"required": ["verdict", "severity", "note"], "properties": {"verdict": STRING, "severity": SEVERITY, "note": STRING}},
}
THREAD = {"type": "array", "items": {"type": "object", "required": ["kind"], "properties": {"kind": {"type": "string", "enum": list(ENTRY)}}}}
SHIFT = {"type": "object", "required": ["from", "to", "direction", "by"],
         "properties": {"from": {"enum": [*SEV_RANK, None]}, "to": SEVERITY,
                        "direction": {"type": "string", "enum": ["raised", "lowered", "same", "dropped", "new"]},
                        "by": {"type": "string", "enum": ["side", "author", "moderator", "none"]}}}
CARD = {"id": STRING, "file": STRING, "line": {"type": "integer"}, "title": STRING, "body": STRING, "fix": OPTIONAL_STRING,
        "sources": STRINGS, "vendors": STRINGS, "status": STRING, "outcome": {"type": "string", "enum": list(OUT_RANK)},
        "final_severity": SEVERITY, "thread": THREAD}
CARD_REQUIRED = ["id", "file", "line", "title", "body", "sources", "vendors", "status", "outcome", "final_severity", "thread"]
ITEM = {"type": "object", "required": [*CARD_REQUIRED, "severity_shift", "moderated", "moderators_split"],
        "properties": {**CARD, "severity_shift": SHIFT, "moderated": {"type": "boolean"}, "moderators_split": {"type": "boolean"}}}
QUESTION = {"type": "object", "required": CARD_REQUIRED, "properties": CARD}
META = {"type": "object", "required": ["agents", "raw", "synthesized", "duration_min", "pipeline", "round_dir", "debate_dir"],
        "properties": {"agents": {"type": "array", "items": AGENT}, "raw": COUNT, "synthesized": COUNT, "duration_min": COUNT,
                       "pipeline": {"type": "string", "enum": list(PIPELINE)}, "round_dir": STRING, "debate_dir": STRING,
                       "task": OPTIONAL_STRING, "run": OPTIONAL_STRING, "started_at": OPTIONAL_STRING,
                       "verify_model": OPTIONAL_STRING, "synthesis_model": OPTIONAL_STRING, "degraded": STRINGS}}
FINAL_SCHEMA = {"type": "object", "required": ["meta", "items", "questions"],
                "properties": {"meta": META, "items": {"type": "array", "items": ITEM}, "questions": {"type": "array", "items": QUESTION}}}


def agents_word(n: int) -> str:
    """«агент», «агента» or «агентов» for n."""
    if n % 10 == 1 and n % 100 != 11:
        return "агент"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "агента"
    return "агентов"


def area_of(path: str) -> str:
    parts = [p for p in (path or "").split("/") if p]
    if len(parts) <= 1:
        return parts[0] if parts else "—"
    return "/".join(parts[:2])


def moderator_line(debate_dir: Path, round_dir: Path) -> str:
    """Who moderated, as the footer names it: the session, or the independent moderators whose answers
    (moderator-<vendor>.json of debate_moderators.py) lie in the debate directory, each with its model from
    the run's manifest.json `moderators` when the manifest has it — an external run has no manifest."""
    present = [vendor for vendor in SIDES if (debate_dir / f"moderator-{vendor}.json").is_file()]
    if not present:
        return SESSION_MODERATOR
    try:
        manifest = json.loads((round_dir / "manifest.json").read_text())
    except (OSError, ValueError):
        manifest = {}
    moderators = manifest.get("moderators") if isinstance(manifest, dict) else None
    moderators = moderators if isinstance(moderators, dict) else {}

    def name(vendor: str) -> str:
        entry = moderators.get(vendor)
        model = entry.get("model") if isinstance(entry, dict) else None
        effort = entry.get("effort") if isinstance(entry, dict) else None
        if isinstance(model, str) and isinstance(effort, str):
            return f"{SIDE_NAME[vendor]} ({model}:{effort})"
        return SIDE_NAME[vendor]

    if len(present) == len(SIDES):
        return (f"независимые модераторы {name('claude')} и {name('codex')}: спор решён, когда оба сошлись, "
                "серьёзность берётся строже; где они разошлись, решает человек")
    missing = next(vendor for vendor in SIDES if vendor not in present)
    return f"независимый модератор {name(present[0])}; {SIDE_NAME[missing]} не ответил, его споры решает человек"


def thread_errors(final: dict) -> list[str]:
    """Errors of the thread entries against the shape of their kind, which the schema subset cannot pick by itself."""
    errors: list[str] = []
    for coll in ("items", "questions"):
        for n, item in enumerate(final[coll]):
            for k, entry in enumerate(item["thread"]):
                errors += check_schema(entry, {"type": "object", **ENTRY[entry["kind"]]}, f"final.json.{coll}[{n}].thread[{k}]")
    return errors


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("debate_dir", help="the debate directory with final.json")
    ap.add_argument("out", help="the HTML file to write")
    ap.add_argument("--title", required=True, help="page title")
    ap.add_argument("--subject", required=True, help="what was reviewed, as the lede names it")
    ap.add_argument("--strip-prefix", default="", help="path prefix to hide in file names")
    ap.add_argument("--eyebrow", default=None, help="the line above the title, e.g. '<repo> · <branch> · <date>'")
    ap.add_argument("--moderator", default=None,
                    help="who moderated the disputes, as the footer names it; by default the session, or the independent "
                         "moderators whose moderator-<vendor>.json lie in the debate directory, with the manifest's models")
    args = ap.parse_args(argv)
    d = Path(args.debate_dir).resolve()
    if not (d / "final.json").is_file():
        print(json.dumps({"error": f"{d}: no final.json, run debate_outcome.py first"}, ensure_ascii=False))
        return 1
    try:
        final = json.loads((d / "final.json").read_text())
        errors = check_schema(final, FINAL_SCHEMA, "final.json") or thread_errors(final)
        if errors:
            raise ValueError("; ".join(errors[:5]))
    except ValueError as e:
        print(json.dumps({"error": f"{d / 'final.json'}: not the output of debate_outcome.py ({e}), run it again"}, ensure_ascii=False))
        return 1
    meta = final["meta"]
    for coll in ("items", "questions"):
        for i in final.get(coll) or []:
            i["area"] = area_of(i.get("file") or "")
    agents = meta.get("agents") or []
    claude = [a for a in agents if a.get("executor") == "claude"]
    codex = [a for a in agents if a.get("executor") == "codex"]

    def models(xs: list[dict]) -> str:
        return ", ".join(sorted({a.get("actual_model") or a.get("requested_model") or "" for a in xs}))

    # the debate directory sits in its run: <run_dir>/debate/ for a cross-review run and an external run alike
    moderator = args.moderator or moderator_line(d, d.parent)
    meta.update({
        "title": args.title,
        "eyebrow": args.eyebrow or f"{meta.get('task') or ''} · {meta.get('run') or ''} · {date.today().isoformat()}",
        "strip_prefix": args.strip_prefix,
        "lede": (f"{len(claude)} {agents_word(len(claude))} Claude ({models(claude)}) и {len(codex)} {agents_word(len(codex))} Codex ({models(codex)}) независимо прочитали {args.subject}. "
                 "Потом каждая сторона проверила по коду находки другой и ответила на возражения. Ниже то, что осталось после спора."),
        "footer_how": (f"Ревью: {PIPELINE.get(meta.get('pipeline') or 'external', PIPELINE['external'])}. Обсуждение: сессия Codex через `codex exec` / `resume` и субагент Claude, оба только читали код. "
                       f"Модератор спорных пунктов — {moderator}."),
        "footer_data": f"Данные: `{meta.get('round_dir')}` и `{meta.get('debate_dir')}`.",
    })
    page = TEMPLATE.read_text().replace("{{TITLE}}", html.escape(args.title))
    # no `<` inside the script element: neither `</script>` nor `<!--` can end or bend it
    payload = json.dumps(final, ensure_ascii=False).replace("<", "\\u003c")
    Path(args.out).write_text(page.replace("/*__DATA__*/null", payload))
    counts: dict[str, int] = {}
    for r in final["items"]:
        key = f"{r['outcome']}:{r['final_severity']}"
        counts[key] = counts.get(key, 0) + 1
    shifts = {direction: sum(1 for r in final["items"] if (r.get("severity_shift") or {}).get("direction") == direction)
              for direction in SHIFTS}
    print(json.dumps({"out": str(Path(args.out).resolve()), "counts": counts, "reprioritized": shifts,
                      "questions": len(final.get("questions") or [])}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
