#!/usr/bin/env python3
"""Merge two independent moderators (Claude and Codex on third models) into moderator.json and extra.json.

Reads moderator-claude.json + extra-claude.json (written by the Claude moderator agent) and moderator-codex.json
(the Codex moderator's schema output: {"decisions": [...], "extra": [...]}). Rule: a dispute is decided when both
moderators agree on whether the finding stands (severity none vs. a real severity); the stricter severity wins and
both notes are kept. When they disagree, the entry is written with `"split": true`, verdict «модераторы разошлись:
…» and the stricter severity for reference, and the id is listed under `split`: debate_outcome.py keeps such an item
disputed at its pipeline severity, and the report says a human decides. A dispute only one moderator answered is
written with `"missing": true` and stays open the same way. A side finding goes to extra.json only when both
confirm it, at the stricter of their severities with both notes; its side comes from its id (codex-side-2) when
the moderator left out sources and vendors.
Prints one JSON line: decided, split, extra, missing. A moderator's answer that is not JSON or breaks its schema
prints {"error": ...} and exits 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from debate_prompts import DECISIONS_SCHEMA, EXTRA_SCHEMA, MODERATE_SCHEMA, load_answer  # noqa: E402

RANK = {"critical": 0, "major": 1, "minor": 2, "none": 3}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("debate_dir", help="the debate directory with both moderators' outputs")
    args = ap.parse_args(argv)
    d = Path(args.debate_dir).resolve()
    try:
        claude_out = load_answer(d / "moderator-claude.json", DECISIONS_SCHEMA, [])
        codex_out = load_answer(d / "moderator-codex.json", MODERATE_SCHEMA, {})
        extra_claude_out = load_answer(d / "extra-claude.json", EXTRA_SCHEMA, [])
    except ValueError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1
    claude = {m["id"]: m for m in claude_out}
    codex = {m["id"]: m for m in codex_out.get("decisions", [])}
    extra_claude = {e["id"]: e for e in extra_claude_out}
    extra_codex = {e["id"]: e for e in codex_out.get("extra", [])}

    merged, split, missing = [], [], []
    for fid in sorted(set(claude) | set(codex)):
        a, b = claude.get(fid), codex.get(fid)
        if a is None or b is None:
            missing.append(fid)
            m = a if a is not None else codex[fid]
            merged.append({"id": fid, "verdict": f"ответил только один модератор ({'Claude' if a else 'Codex'}): {m['verdict']}; решает человек",
                           "severity": m["severity"], "note": m["note"], "missing": True})
            continue
        stands_a, stands_b = a["severity"] != "none", b["severity"] != "none"
        stricter = min((a["severity"], b["severity"]), key=lambda s: RANK[s])
        note = f"Модератор Claude: {a['note']}\n\nМодератор Codex: {b['note']}"
        if stands_a == stands_b:
            verdict = a["verdict"] if a["verdict"] == b["verdict"] else f"оба модератора: {a['verdict']} / {b['verdict']}"
            if a["severity"] != b["severity"]:
                verdict += f"; серьёзность разошлась ({a['severity']} / {b['severity']}), берём строже"
            merged.append({"id": fid, "verdict": verdict, "severity": stricter, "note": note})
        else:
            split.append(fid)
            merged.append({"id": fid, "verdict": f"модераторы разошлись: Claude — {a['verdict']}, Codex — {b['verdict']}; решает человек",
                           "severity": stricter, "note": note, "split": True})
    extra = []
    for eid in sorted(set(extra_claude) & set(extra_codex)):
        e = dict(extra_claude[eid])
        sev_a, sev_b = extra_claude[eid]["severity"], extra_codex[eid]["severity"]
        e["severity"] = min((sev_a, sev_b), key=lambda s: RANK[s])
        e["note"] = f"Модератор Claude: {extra_claude[eid].get('note', '')}\n\nМодератор Codex: {extra_codex[eid].get('note', '')}"
        if sev_a != sev_b:
            e["note"] += f"\n\nСерьёзность разошлась ({sev_a} / {sev_b}), берём строже."
        side = (e.get("vendors") or e.get("sources") or [eid.split("-side-", 1)[0]])[0]
        e["sources"] = e.get("sources") or [side]
        e["vendors"] = e.get("vendors") or [side]
        extra.append(e)
    (d / "moderator.json").write_text(json.dumps(merged, ensure_ascii=False, indent=2) + "\n")
    (d / "extra.json").write_text(json.dumps(extra, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"decided": [m["id"] for m in merged if m["id"] not in split and m["id"] not in missing],
                      "split": split, "missing": missing, "extra": [e["id"] for e in extra],
                      "extra_rejected_by_one": sorted(set(extra_claude) ^ set(extra_codex))}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
