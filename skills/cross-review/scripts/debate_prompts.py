#!/usr/bin/env python3
"""Write the prompts and output schemas for both sides of the debate, plus the moderator briefs, and print
the launch plan.

The texts are the prompts `examine` (round 1), `respond` (round 2) and `moderate` (the moderator), resolved
through both configuration layers: <repo>/.cross-review/prompts/<name>.md first, the plugin's assets/config/prompts/
second. The repository is the run's own for a cross-review run, the --workdir for an external run. The parts
that depend on the run are built here and substituted as values: the panel sentence (how many reviewers per
vendor, on which models, from the run's findings.json), the context list, the state of the working tree, the
status legend, and the input and output of each prompt. A placeholder left without a value is an error naming
the prompt and the placeholder.

--ref is the commit under review (the last sha of the range); the prompts then state whether the working tree
contains it and, when it does not, tell the sides to read the code through `git show <sha>:<path>`.

Writes to <debate_dir>, <run_dir>/debate/ by default:
  prompt-r1-claude.md, prompt-r1-codex.md     round 1, `examine`: what the other side's reviewers raised
  prompt-r2-claude.md, prompt-r2-codex.md     round 2, `respond`: the objections to the side's own findings
  prompt-r2-codex.fresh.md                    round 2 for a Codex session that cannot be resumed: the round-1
                                              prompt, a note that its verdicts are in codex-r1.json, then round 2
  prompt-moderator.md                         the session or a Claude agent moderates: moderator.json, extra.json
  prompt-moderator-claude.md                  an independent Claude moderator: moderator-claude.json, extra-claude.json
  prompt-moderator-codex.md                   an independent Codex moderator, answer held to schema-moderate.json
  schema-examine.json, schema-respond.json, schema-moderate.json
Paths in the prompts are absolute. Context defaults to the run's own input: scope.md, goal.md, context/*, and
the profile that calibrated the run (input/profile.md when non-empty, else prompts/input-profile.md of an external
run). --context adds more.

A Claude side runs as an Agent that writes its answer with the Write tool; round 2 continues the round-1 agent.
A Codex side runs through codex_run.sh: round 1 fresh, round 2 with --resume codex-r1.log, and on exit 3 (no
session id in the log) or without that log, fresh with prompt-r2-codex.fresh.md. The launch plan lists every
step with its prompt, schema, output, model and, for Codex, the codex_run.sh command. The models are the review's
own (`cross_config.py sides`): a cross-review run's manifest.json, or for an external run its findings.json with the
moderators of the configured tier. A cross-review run records the three prompts in its manifest.json.

Prints one JSON line. A run or configuration error prints {"error": ...} and exits 1.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cross_config  # noqa: E402
import review_collect  # noqa: E402
import review_run  # noqa: E402
from cross_config import ConfigError  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
SIDES = ("claude", "codex")
SIDE_NAME = {"claude": "Claude", "codex": "Codex"}
OTHER = {"claude": "codex", "codex": "claude"}
PROMPTS = ("examine", "respond", "moderate")
NONE = "- none provided"

EXAMINE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["verdicts", "open_question_answers"],
    "properties": {
        "verdicts": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "stance", "severity", "argument", "evidence"],
            "properties": {
                "id": {"type": "string"},
                "stance": {"type": "string", "enum": ["real", "real_but_different", "not_real", "not_worth_fixing"]},
                "severity": {"type": "string", "enum": ["critical", "major", "minor", "none"]},
                "argument": {"type": "string"},
                "evidence": {"type": "string"}}}},
        "open_question_answers": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "answer", "evidence"],
            "properties": {"id": {"type": "string"}, "answer": {"type": "string"}, "evidence": {"type": "string"}}}},
    },
}

RESPOND_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["responses", "side_findings"],
    "properties": {
        "responses": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "position", "severity", "argument", "evidence"],
            "properties": {
                "id": {"type": "string"},
                "position": {"type": "string", "enum": ["concede", "defend", "amend"]},
                "severity": {"type": "string", "enum": ["critical", "major", "minor", "none"]},
                "argument": {"type": "string"},
                "evidence": {"type": "string"}}}},
        "side_findings": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["file", "line", "severity", "title", "body", "fix", "evidence"],
            "properties": {
                "file": {"type": "string"}, "line": {"type": "integer"},
                "severity": {"type": "string", "enum": ["critical", "major", "minor"]},
                "title": {"type": "string"}, "body": {"type": "string"}, "fix": {"type": "string"},
                "evidence": {"type": "string"}}}},
    },
}

MODERATE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["decisions", "extra"],
    "properties": {
        "decisions": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["id", "verdict", "severity", "note"],
            "properties": {
                "id": {"type": "string"}, "verdict": {"type": "string"},
                "severity": {"type": "string", "enum": ["critical", "major", "minor", "none"]},
                "note": {"type": "string"}}}},
        "extra": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["id", "file", "line", "severity", "title", "body", "fix", "note"],
            "properties": {
                "id": {"type": "string"}, "file": {"type": "string"}, "line": {"type": "integer"},
                "severity": {"type": "string", "enum": ["critical", "major", "minor"]},
                "title": {"type": "string"}, "body": {"type": "string"}, "fix": {"type": "string"},
                "note": {"type": "string"}}}},
    },
}

SCHEMAS = {"examine": EXAMINE_SCHEMA, "respond": RESPOND_SCHEMA, "moderate": MODERATE_SCHEMA}


def with_props(schema: dict, **props: dict) -> dict:
    return {**schema, "properties": {**schema["properties"], **props}}


SIDE_LIST = {"type": "array", "items": {"type": "string"}}
# the arrays a Claude moderator and debate_moderators.py write: decisions with the split marks, extras with their side
DECISIONS_SCHEMA = {"type": "array", "items": with_props(MODERATE_SCHEMA["properties"]["decisions"]["items"],
                                                         split={"type": "boolean"}, missing={"type": "boolean"})}
EXTRA_SCHEMA = {"type": "array", "items": with_props(MODERATE_SCHEMA["properties"]["extra"]["items"],
                                                     sources=SIDE_LIST, vendors=SIDE_LIST)}


def load_answer(path: Path, schema: dict, default: object) -> object:
    """A side's or a moderator's answer checked against its schema, default when the file is missing.

    An answer that is not JSON or breaks the schema raises ValueError naming the file, so the step is rerun.
    """
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text())
    except ValueError as e:
        raise ValueError(f"{path}: not JSON: {e}") from None
    errors = review_collect.check_schema(value, schema, path.name)
    if errors:
        raise ValueError(f"{path}: does not match the schema, rerun the step: {'; '.join(errors[:5])}")
    return value

STATUS_LEGEND = """- `confirmed` / `refined` — the verifier accepted it (refined: with corrections)
- `immaterial` — the verifier found it accurate but not worth acting on
- `pre_existing` — the verifier judged it real but not introduced by this change; judge also whether this change made it reachable or worse
- `rejected_by_verify` — the verifier rejected it
- `dropped_by_synthesis` — a weak single-source finding the merge step dropped before anyone checked it
- `unverified` — the verify stage was skipped; nobody has checked it yet"""

# the output of the round-1 prompt inside a fresh Codex round 2: the round-2 prompt follows it
FRESH_BRIDGE = ("Round 1 is over: your verdicts on these findings are in `{verdicts}` (no such file means you had "
                "nothing to examine). Do not examine them again; answer round 2 below.")


def panel_sentence(agents: list[dict]) -> str:
    words = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight"}
    parts = []
    for side in SIDES:
        names = [a for a in agents if a.get("executor") == side]
        if not names:
            continue
        models = sorted({a.get("actual_model") or a.get("requested_model") or "" for a in names})
        parts.append(f"{words.get(len(names), len(names))} {SIDE_NAME[side]} ({', '.join(m for m in models if m)})")
    if not parts:
        return "The panel's reviewers"
    total = len(agents)
    noun = "reviewer" if total == 1 else "reviewers"
    return f"{str(words.get(total, total)).capitalize()} {noun} — {' and '.join(parts)} —"


def tree_state(workdir: str, ref: str | None) -> str:
    """One paragraph on where the working tree stands relative to the commit under review."""
    def git(*a):
        return subprocess.run(["git", "-C", workdir, *a], capture_output=True, text=True).stdout.strip()

    head = git("rev-parse", "--short", "HEAD")
    branch = git("branch", "--show-current") or "detached HEAD"
    dirty = bool(git("status", "--porcelain"))
    base = (f"The working tree is `{branch}`" + (f" at `{head}`" if head else ", with no commit yet")
            + (", with uncommitted changes" if dirty else "") + ".")
    if not ref:
        return base + " Cite lines as the working tree has them."
    short = git("rev-parse", "--short", ref)
    if not short:
        return base + f" The ref `{ref}` does not resolve here; read the code the scope's commands show."
    contained = subprocess.run(["git", "-C", workdir, "merge-base", "--is-ancestor", ref, "HEAD"],
                               capture_output=True).returncode == 0
    if contained:
        return base + f" It contains `{short}`, the commit under review, so files on disk are at or after it; cite lines of `{short}` (`git show {short}:<path>`) when the two differ."
    return (base + f" It does **not** contain `{short}`, the commit under review: files on disk are an older or unrelated version. "
            f"Read every file through `git show {short}:<path>` (and `git diff`/`git log` on the range), never from disk, and cite lines of `{short}`.")


def default_context(round_dir: Path) -> list[tuple[str, str]]:
    inp = round_dir / "input"
    ctx: list[tuple[str, str]] = []
    if (inp / "scope.md").exists():
        ctx.append((str(inp / "scope.md"), "what was reviewed and the commands that show the change"))
    if (inp / "goal.md").exists() and (inp / "goal.md").read_text().strip():
        ctx.append((str(inp / "goal.md"), "what the change is for and when it counts as correct"))
    cdir = inp / "context"
    if cdir.is_dir():
        for f in sorted(cdir.iterdir()):
            if f.is_file():
                ctx.append((str(f), f"supporting material: {f.name}"))
    profile = inp / "profile.md"
    inherited = round_dir / "prompts" / "input-profile.md"
    if profile.exists() and profile.read_text().strip():
        ctx.append((str(profile), "project conventions; where they disagree with your taste, they win"))
    elif inherited.exists() and inherited.read_text().strip():
        ctx.append((str(inherited), "project conventions; where they disagree with your taste, they win"))
    return ctx


def own_run(round_dir: Path) -> Path | None:
    """The repository of a cross-review run; None for an external run or any other directory."""
    try:
        repo, _, _ = review_run.locate(round_dir)
    except ConfigError:
        return None
    return repo


def read_manifest(round_dir: Path) -> dict:
    try:
        manifest = json.loads((round_dir / "manifest.json").read_text())
    except (OSError, ValueError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


def output_claude(path: Path, schema: Path, reply: str) -> str:
    return (f"Write the JSON, one object matching `{schema}`, to `{path}` with the Write tool — the only file you "
            f"may create — and reply with one line: {reply}.")


def output_moderator(debate_dir: Path, suffix: str) -> str:
    return (f"Write `{debate_dir}/moderator{suffix}.json` as a JSON array of the `decisions` entries and "
            f"`{debate_dir}/extra{suffix}.json` as a JSON array of the `extra` entries, each extra with "
            '`"sources": ["<side>"], "vendors": ["<side>"]` added. Those two are the only files you create. '
            "Reply with one line per dispute: id, verdict, severity.")


CODEX_OUTPUT = "Return JSON matching the output schema."


def codex_command(workdir: str, prompt: Path, schema: Path, output: Path, model: dict, resume: Path | None = None) -> str:
    argv = ["bash", str(SCRIPTS / "codex_run.sh"), workdir, str(prompt), str(schema), str(output),
            model["model"], model["effort"]]
    return shlex.join(argv + (["--resume", str(resume)] if resume else []))


def launch_entry(step: str, vendor: str, prompt: Path, output: object, model: dict | None) -> dict:
    entry: dict = {"step": step, "vendor": vendor, "prompt": str(prompt), "output": output}
    if model is not None and vendor == "claude":
        entry.update(alias=model["model"], effort=model["effort"])
    elif model is not None:
        entry.update(model=model["model"], effort=model["effort"])
    return entry


def record_prompts(round_dir: Path, repo: Path) -> None:
    """Add the three debate prompts to a cross-review run's manifest, replacing an earlier run of this script's."""
    path = round_dir / "manifest.json"
    manifest = read_manifest(round_dir)
    if not manifest:
        return
    entries = [review_run.source(repo, "prompts", name) for name in PROMPTS]
    paths = {e["path"] for e in entries}
    manifest["prompts"] = [p for p in manifest.get("prompts") or [] if p.get("path") not in paths] + entries
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")


def build(round_dir: Path, debate_dir: Path, subject: str, workdir: str | None, extra_context: list[tuple[str, str]],
          ref: str | None) -> dict:
    try:
        findings = json.loads((round_dir / "findings.json").read_text())
    except (OSError, ValueError):
        raise ConfigError(f"{round_dir}: no readable findings.json, finish the review run first") from None
    run_repo = own_run(round_dir)
    workdir = workdir or str(run_repo or Path.cwd())
    repo = run_repo or Path(workdir).resolve()
    cross_config.load_config(repo)
    templates = {name: cross_config.body(repo, "prompts", name) for name in PROMPTS}

    agents = (findings.get("sources") or {}).get("agents") or []
    context = default_context(round_dir) + extra_context
    panel = panel_sentence(agents)
    tree_note = tree_state(workdir, ref)
    common = {
        "SUBJECT": subject,
        "WORKDIR": workdir,
        "PANEL": panel,
        "CONTEXT": "\n".join(f"- `{p}` — {why}" for p, why in context) or NONE,
        "TREE_NOTE": tree_note,
        "DEBATE_DIR": str(debate_dir),
        "STATUS_LEGEND": STATUS_LEGEND,
    }
    schema = {name: debate_dir / f"schema-{name}.json" for name in PROMPTS}
    models = cross_config.side_models(round_dir, repo)
    sides = {vendor: models[vendor] for vendor in SIDES}
    moderators = models["moderators"]

    def render(name: str, values: dict) -> str:
        return cross_config.render(templates[name], {**common, **values}, f"prompts/{name}.md").rstrip("\n") + "\n"

    def side_values(side: str, inp: str) -> dict:
        return {"SIDE": SIDE_NAME[side], "OTHER": SIDE_NAME[OTHER[side]], "INPUT": str(debate_dir / inp)}

    texts: dict[str, str] = {}
    launch: list[dict] = []
    fresh = debate_dir / "prompt-r2-codex.fresh.md"
    for step, name, inp, reply in (("r1", "examine", "for-{side}.json", "how many verdicts per stance"),
                                   ("r2", "respond", "for-{side}-r2.json", "how many responses per position")):
        for side in SIDES:
            prompt, output = debate_dir / f"prompt-{step}-{side}.md", debate_dir / f"{side}-{step}.json"
            out = output_claude(output, schema[name], reply) if side == "claude" else CODEX_OUTPUT
            texts[prompt.name] = render(name, {**side_values(side, inp.format(side=side)), "OUTPUT": out})
            model = sides[side]
            entry = launch_entry(step, side, prompt, str(output), model)
            if side == "claude":
                if step == "r2":
                    entry["continues"] = "r1"
            else:
                entry["schema"] = str(schema[name])
                resume = debate_dir / "codex-r1.log" if step == "r2" else None
                if resume is not None:
                    entry.update(resume=str(resume), fresh=str(fresh))
                entry["command"] = codex_command(workdir, prompt, schema[name], output, model, resume)
                if resume is not None:
                    entry["fresh_command"] = codex_command(workdir, fresh, schema[name], output, model)
            launch.append(entry)
    # round 1 with the bridge as its output, then round 2: a fresh session sees what it examined and answered
    bridge = FRESH_BRIDGE.format(verdicts=debate_dir / "codex-r1.json")
    fresh_r1 = render("examine", {**side_values("codex", "for-codex.json"), "OUTPUT": bridge})
    texts[fresh.name] = "\n\n".join([fresh_r1.rstrip("\n"), "---", texts["prompt-r2-codex.md"]])

    # the session moderates itself, or hands the brief to a Claude agent; the other two are the independent pair
    for suffix, vendor in (("", "session"), ("-claude", "claude"), ("-codex", "codex")):
        prompt = debate_dir / f"prompt-moderator{suffix}.md"
        out = CODEX_OUTPUT if vendor == "codex" else output_moderator(debate_dir, suffix)
        texts[prompt.name] = render("moderate", {"INPUT": str(debate_dir / "disputes.json"), "OUTPUT": out})
        if vendor == "codex":
            output = debate_dir / "moderator-codex.json"
            model = moderators[vendor]
            entry = launch_entry("moderate", vendor, prompt, str(output), model)
            entry["schema"] = str(schema["moderate"])
            entry["command"] = codex_command(workdir, prompt, schema["moderate"], output, model)
        else:
            outputs = [str(debate_dir / f"moderator{suffix}.json"), str(debate_dir / f"extra{suffix}.json")]
            entry = launch_entry("moderate", vendor, prompt, outputs, moderators.get(vendor))
        launch.append(entry)

    debate_dir.mkdir(parents=True, exist_ok=True)
    for file, text in texts.items():
        (debate_dir / file).write_text(text)
    for name, path in schema.items():
        path.write_text(json.dumps(SCHEMAS[name], indent=2) + "\n")
    if run_repo is not None:
        record_prompts(round_dir, repo)
    sources = {}
    for name in PROMPTS:
        path = cross_config.resolve(repo, "prompts", name)
        sources[name] = {"path": str(path), "layer": cross_config.layer_of(path)}
    return {"debate_dir": str(debate_dir), "wrote": [*texts, *(p.name for p in schema.values())], "panel": panel,
            "tree": tree_note, "context": [p for p, _ in context], "prompts": sources, "launch": launch}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("round_dir", help="the run directory with findings.json and input/")
    ap.add_argument("debate_dir", nargs="?", default=None,
                    help="where the prompts and schemas go, created when missing; default: <round_dir>/debate")
    ap.add_argument("--subject", required=True, help="what is under review, one line, e.g. 'PR #765 `feature/PROJ-42` (range 88e879874..747aec1db)'")
    ap.add_argument("--workdir", default=None,
                    help="repository root the sides run in; default: the run's repository for a cross-review run, "
                         "the current directory for an external run")
    ap.add_argument("--context", action="append", default=[], metavar="PATH=WHY", help="extra context file, absolute path and why to read it")
    ap.add_argument("--ref", default=None, help="the commit under review (last sha of the range); the prompts then say whether the working tree contains it and how to read the code when it does not")
    args = ap.parse_args(argv)

    extra: list[tuple[str, str]] = []
    for entry in args.context:
        if "=" not in entry:
            ap.error(f"--context wants PATH=WHY, got {entry!r}")
        p, why = entry.split("=", 1)
        extra.append((str(Path(p).resolve()), why.strip()))
    round_dir = Path(args.round_dir).resolve()
    debate_dir = Path(args.debate_dir).resolve() if args.debate_dir else round_dir / "debate"
    workdir = str(Path(args.workdir).resolve()) if args.workdir else None
    try:
        out = build(round_dir, debate_dir, args.subject, workdir, extra, args.ref)
    except ConfigError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
