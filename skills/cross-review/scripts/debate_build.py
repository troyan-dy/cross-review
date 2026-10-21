#!/usr/bin/env python3
"""Split a finished review run into cross-examination inputs for the two sides.

Reads the run's stages/1-found.json, stages/2-synthesized.json, stages/3-verified.json (when verify
ran) and findings.json. Writes to <debate_dir>, <run_dir>/debate/ by default:
  items.json        every finding that reached any stage, with `vendors` and pipeline `status`
  consensus.json    found by both sides and kept by the pipeline — not debated
  for-claude.json   what Claude examines: findings only Codex raised (+ open questions Codex raised)
  for-codex.json    what Codex examines: findings only Claude raised (+ open questions Claude raised)
Prints one JSON line with the counts. Exit 1 when one side has no agent in the roster.
An external run (one this skill did not compose) has the same layout and is accepted as is.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SIDES = ("claude", "codex")
KEEP = ("id", "file", "line", "end_line", "severity", "confidence", "title", "body", "fix", "sources", "lenses", "verdict")


def load(path: Path) -> dict:
    """A run file as an object; a missing, unreadable or malformed one is a ValueError that names the path."""
    try:
        value = json.loads(path.read_text())
    except OSError as e:
        raise ValueError(f"{path}: {e.strerror or e}") from None
    except ValueError as e:
        raise ValueError(f"{path}: not JSON: {e}") from None
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected an object")
    return value


def tag(title: str) -> str:
    """A leading `[...]` tag in a title, used to match a raw finding to the synthesized one it was folded into."""
    return title.split("]", 1)[0] + "]" if title.startswith("[") and "]" in title else ""


def dropped_from_events(events: Path) -> set[str] | None:
    """Ids synthesis dropped, from the `dropped` events of events.jsonl; None when the run has no such log."""
    if not events.exists():
        return None
    ids: set[str] = set()
    for line in events.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("kind") == "dropped":
            ids |= {f["id"] for f in e.get("findings") or [] if "id" in f}
    return ids


def covered(raw: dict, synthesized: list[dict]) -> bool:
    """A raw finding is covered when synthesis could not have dropped it or when it was folded into a kept one.

    Synthesis drops only single-source minors, so a missing major/critical was merged. Otherwise: same id,
    same leading tag, or the same agent in the same file within five lines of a synthesized finding.
    """
    if raw.get("severity") != "minor":
        return True
    t = tag(raw.get("title") or "")
    if t and any(tag(i.get("title") or "") == t for i in synthesized):
        return True
    src = (raw.get("sources") or [None])[0]
    for item in synthesized:
        if raw["id"] == item["id"]:
            return True
        if src in (item.get("sources") or []) and raw.get("file") == item.get("file"):
            if abs((raw.get("line") or 0) - (item.get("line") or 0)) <= 5:
                return True
    return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("round_dir", help="the run directory: stages/, findings.json, events.jsonl")
    ap.add_argument("debate_dir", nargs="?", default=None,
                    help="where the debate files go, created when missing; default: <round_dir>/debate")
    args = ap.parse_args(argv)
    round_dir = Path(args.round_dir).resolve()
    debate_dir = Path(args.debate_dir).resolve() if args.debate_dir else round_dir / "debate"
    stages = round_dir / "stages"
    try:
        found = load(stages / "1-found.json")
        synth = load(stages / "2-synthesized.json") if (stages / "2-synthesized.json").exists() else found
        final = load(round_dir / "findings.json")
        verified = load(stages / "3-verified.json") if (stages / "3-verified.json").exists() else final
    except ValueError as e:
        print(json.dumps({"error": f"{e}; not a finished run: check round_dir, or run review_verify.py apply first"},
                         ensure_ascii=False))
        return 1
    verify_ran = (stages / "3-verified.json").exists()

    agents = (final.get("sources") or {}).get("agents") or (found.get("sources") or {}).get("agents") or []
    vendor_of = {a["name"]: a["executor"] for a in agents}
    roster = {side: sorted(n for n, v in vendor_of.items() if v == side) for side in SIDES}
    missing = [side for side in SIDES if not roster[side]]
    if missing:
        print(json.dumps({"error": f"no {' and '.join(missing)} agent in the roster; cross-examination needs both sides",
                          "roster": roster}, ensure_ascii=False))
        return 1

    def vendors(item: dict) -> list[str]:
        return sorted({vendor_of.get(s, s) for s in item.get("sources") or []})

    def slim(item: dict, status: str) -> dict:
        out = {k: item[k] for k in KEEP if k in item}
        out["vendors"] = vendors(item)
        out["status"] = status
        return out

    items: dict[str, dict] = {}
    for item in verified.get("findings") or []:
        verdict = item.get("verdict") or ""
        status = verdict if verdict in ("confirmed", "refined", "unverified") else "confirmed"
        items[item["id"]] = slim(item, status)
    for item in verified.get("immaterial") or []:
        items[item["id"]] = slim(item, "immaterial")
    for item in verified.get("pre_existing") or []:
        items[item["id"]] = slim(item, "pre_existing")
    if verify_ran:
        for item in synth.get("findings") or []:
            if item["id"] not in items:
                items[item["id"]] = slim(item, "rejected_by_verify")

    # open questions count as kept: a raw question must not come back as a dropped finding
    synthesized = [*(synth.get("findings") or []), *(synth.get("pre_existing") or []),
                   *(synth.get("immaterial") or []), *(synth.get("open_questions") or [])]
    question_ids = {q["id"] for q in [*(synth.get("open_questions") or []), *(verified.get("open_questions") or [])]}

    # synthesis records what it dropped in events.jsonl; the heuristic is only for a run without that log
    dropped_ids = dropped_from_events(round_dir / "events.jsonl")
    for raw in found.get("findings") or []:
        if raw["id"] in items or raw["id"] in question_ids:
            continue
        dropped = raw["id"] in dropped_ids if dropped_ids is not None else not covered(raw, synthesized)
        if dropped:
            items[raw["id"]] = slim(raw, "dropped_by_synthesis")

    questions = [slim(q, "open_question") for q in verified.get("open_questions") or []]

    consensus, to_codex, to_claude = [], [], []
    for item in items.values():
        v = set(item["vendors"])
        if v == {"claude", "codex"}:
            if item["status"] in ("confirmed", "refined"):
                consensus.append(item)
            else:  # both found it and the pipeline still set it aside: each side looks again
                to_codex.append(item)
                to_claude.append(item)
        elif v == {"claude"}:
            to_codex.append(item)
        elif v == {"codex"}:
            to_claude.append(item)
        else:
            to_codex.append(item)
            to_claude.append(item)

    q_codex = [q for q in questions if "claude" in q["vendors"] or not q["vendors"]]
    q_claude = [q for q in questions if "codex" in q["vendors"] or not q["vendors"]]

    debate_dir.mkdir(parents=True, exist_ok=True)

    def dump(name: str, data: object) -> None:
        (debate_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    dump("items.json", list(items.values()))
    dump("consensus.json", consensus)
    dump("for-codex.json", {"findings": to_codex, "open_questions": q_codex})
    dump("for-claude.json", {"findings": to_claude, "open_questions": q_claude})

    by_status: dict[str, int] = {}
    for item in items.values():
        by_status[item["status"]] = by_status.get(item["status"], 0) + 1
    print(json.dumps({
        "items": len(items), "by_status": by_status, "consensus": len(consensus),
        "for_codex": len(to_codex), "for_claude": len(to_claude), "open_questions": len(questions),
        "roster": roster, "verify_ran": verify_ran, "dropped_from_events": dropped_ids is not None, "debate_dir": str(debate_dir),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
