#!/usr/bin/env python3
"""Turn round-1 verdicts into round-2 inputs: each side gets the other side's objections to its own findings.

claude-r1.json holds Claude's verdicts on Codex findings  -> objections go to Codex  (for-codex-r2.json)
codex-r1.json  holds Codex's verdicts on Claude findings  -> objections go to Claude (for-claude-r2.json)
An objection is any verdict other than `real` at the finding's own severity. Findings both sides raised
were examined by both in round 1 and have no single author to answer, so they skip round 2.
Prints one JSON line with the counts; a missing r1 file yields an empty list for that side. An r1 answer that is
not JSON or breaks schema-examine.json prints {"error": ...} and exits 1.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from debate_prompts import EXAMINE_SCHEMA, load_answer  # noqa: E402

PAIRS = (("codex", "claude"), ("claude", "codex"))  # (examiner, author)


def objections(items: dict[str, dict], verdicts: list[dict]) -> list[dict]:
    """The verdicts the author has to answer, each paired with the finding it is about."""
    out = []
    for v in verdicts:
        item = items.get(v["id"])
        if item is None:
            continue
        if v["stance"] == "real" and v["severity"] == item["severity"]:
            continue
        if set(item["vendors"]) == {"claude", "codex"}:
            continue
        out.append({"finding": item, "objection": v})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("debate_dir", help="the debate directory with items.json and the round-1 verdicts")
    args = ap.parse_args(argv)
    d = Path(args.debate_dir).resolve()
    path = d / "items.json"
    try:
        items = {i["id"]: i for i in json.loads(path.read_text())}
    except (OSError, ValueError, TypeError, KeyError) as e:
        reason = e.strerror if isinstance(e, OSError) and e.strerror else str(e)
        print(json.dumps({"error": f"{path}: {reason}; run debate_build.py first"}, ensure_ascii=False))
        return 1
    counts: dict[str, object] = {}
    for examiner, author in PAIRS:
        src = d / f"{examiner}-r1.json"
        try:
            verdicts = load_answer(src, EXAMINE_SCHEMA, {"verdicts": []})["verdicts"]
        except ValueError as e:
            print(json.dumps({"error": str(e)}, ensure_ascii=False))
            return 1
        found = objections(items, verdicts)
        (d / f"for-{author}-r2.json").write_text(json.dumps(found, ensure_ascii=False, indent=2) + "\n")
        counts[f"for_{author}_r2"] = len(found)
        counts[f"{examiner}_r1_present"] = src.exists()
    print(json.dumps(counts))
    return 0


if __name__ == "__main__":
    sys.exit(main())
