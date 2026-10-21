#!/usr/bin/env python3
"""Start a review run and compose the prompts of its finder agents.

  new <repo> --task T [--run R]                         create the run directory and print its input paths
  compose <run_dir> [--tier T | --claude M --codex M]   write the agents' prompts and manifest.json

A run lives in <repo>/.cross-review/runs/<task>/<run>/, in the same run format as an external run, so the debate
scripts read both. `new` creates the run with an empty input/ and the task's task.md, and writes
.cross-review/runs/.gitignore with `*` — the only write outside the run: a nested .gitignore outranks the
repository's own, so even a whitelist .gitignore (`*`, `!*.*`, `!*/`) cannot bring the runs back, and it works the
same in a worktree and a subdirectory. A symlink on the way to the run or task.md is an error, wherever it points, and so is one in a
prior run of the task — the prompts point the finders at those directories. The run name defaults to NN-initial
for the first run of a task and NN-after-fix after it.

`compose` reads input/scope.md (required), goal.md, profile.md and context/, which the session writes. For
every roster agent it writes prompts/agents/<agent>.md: the `review` prompt with paths in place of
{{SCOPE}} {{GOAL}} {{PROFILE}} {{CONTEXT}} {{WORKDIR}} (`none provided` when empty), a `## Lens: <name>`
section per lens, the task's prior runs with the instruction to re-evaluate them independently, then the
finder schema. It writes manifest.json and prints the launch plan: one entry per agent with its vendor,
model, prompt and output file. A run that already has answers is not composed again.

Prints one JSON line. A run or configuration error prints {"error": ...} and exits 1.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import cross_config  # noqa: E402
from cross_config import ConfigError  # noqa: E402

SCRIPTS = Path(__file__).resolve().parent
RUNS = Path(cross_config.REPO_DIR) / "runs"
IGNORE_FILE = ".gitignore"
NONE = "none provided"
TASK_FILE = "task.md"
RUN_NUMBER = re.compile(r"(\d+)-")

TASK_TEMPLATE = """---
# every key is optional; the scripts only report what is here and never resolve a branch or a ref.
# Uncomment what applies.
#
# description: one line naming what this task reviews
# url: https://github.com/owner/repo/pull/123
# branch: feature/TICKET-123
# base: master
---

<!-- what this task covers, in prose -->
"""

# the closing lines of the prior-run block: the inventory never goes without them
HISTORY_GUARD = """Each round holds report.md (rendered) and findings.json (machine shape). Read the rounds you judge relevant.

Re-evaluate everything independently. A prior round reporting an issue is not evidence that it is real,
and a prior round missing one is not evidence that it is absent."""

OUTPUT_CONTRACT = "Return ONLY a JSON object matching the schema below. No prose before or after it.\n\nSchema:\n"


def check_name(value: str) -> str:
    """A task or run name becomes one path component: no separators, no leading dot, no parent reference."""
    if not value or "/" in value or "\\" in value or value.startswith(".") or ".." in value or value == TASK_FILE:
        raise argparse.ArgumentTypeError(f"{value!r} is not a valid name: one path component, no leading dot")
    return value


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def ensure_ignored(repo: Path, probe: Path) -> dict | None:
    """Write .cross-review/runs/.gitignore with `*` unless it is there, then ask git whether `probe` — a file of
    the run, relative to the repository — is ignored; None outside a git repository.

    The file ignores itself too, so nothing under runs/ ever shows in `git status`. `ignored: false` means git
    still sees the run, which no .gitignore pattern can cause: the session warns before any agent starts.
    """
    if git(repo, "rev-parse", "--git-dir").returncode != 0:
        return None
    path = cross_config.confined(repo, repo / RUNS / IGNORE_FILE)
    added = not path.is_file() or path.read_text().split() != ["*"]
    if added:
        path.write_text("*\n")
    ignored = git(repo, "check-ignore", "-q", probe.as_posix()).returncode == 0
    return {"path": str(path), "added": added, "ignored": ignored}


def parse_time(value: object) -> datetime | None:
    """An RFC 3339 timestamp as this skill or an external run writes it; Python 3.9 takes neither `Z` nor nanoseconds."""
    if not isinstance(value, str) or not value:
        return None
    text = re.sub(r"(\.\d{6})\d+", r"\1", value.replace("Z", "+00:00"))
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def has_run(run_dir: Path) -> bool:
    """A run that got as far as its manifest; an empty manifest is an external run still open."""
    manifest = run_dir / "manifest.json"
    return manifest.is_file() and manifest.stat().st_size > 0


def prior_runs(task_dir: Path, current: str | None = None) -> list[dict]:
    """Every run of the task but `current`, oldest first, with the counts its findings.json records. The finder
    prompts name these directories for the models to read, so a symlink in the task directory or anywhere below a
    prior run is an error, wherever it points."""
    runs = []
    for d in sorted(task_dir.iterdir()) if task_dir.is_dir() else []:
        if d.is_symlink():
            raise cross_config.symlink_error(d)
        if not d.is_dir() or d.name == current:
            continue
        cross_config.no_symlinks(d)
        if not has_run(d):
            continue
        entry: dict = {"run": d.name, "path": str(d), "started_at": None, "findings": None,
                       "by_severity": None, "sources": None, "degraded": []}
        try:
            rec = json.loads((d / "findings.json").read_text())
        except (OSError, ValueError):
            rec = None
        if isinstance(rec, dict):
            sev = [f.get("severity") for f in rec.get("findings") or [] if isinstance(f, dict)]
            src = rec.get("sources") or {}
            entry.update(
                started_at=(rec.get("stats") or {}).get("started_at"),
                findings=len(sev),
                by_severity={s: sev.count(s) for s in ("critical", "major", "minor")},
                sources=f"{src.get('reported', 0)}/{src.get('expected', 0)}",
                degraded=list(src.get("degraded") or []),
            )
        at = parse_time(entry["started_at"])
        entry["_order"] = at or datetime.fromtimestamp(d.stat().st_mtime, timezone.utc)
        runs.append(entry)
    runs.sort(key=lambda r: r.pop("_order"))
    return runs


def history(task_dir: Path, runs: list[dict]) -> str:
    """The prior-run inventory every finder prompt carries, one padded line per run; empty without runs."""
    if not runs:
        return ""
    rows = []
    for r in runs:
        at = parse_time(r["started_at"])
        if r["findings"] is None:
            counts, sources = "counts unknown", "sources unknown"
        else:
            sev = r["by_severity"]
            counts = f"{r['findings']} findings ({sev['critical']} critical, {sev['major']} major, {sev['minor']} minor)"
            sources = f"sources {r['sources']}" + (f", {', '.join(r['degraded'])} degraded" if r["degraded"] else "")
        rows.append((r["run"], at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ") if at else "unknown",
                     counts, sources))
    widths = [max(len(row[i]) for row in rows) for i in range(3)]
    lines = [f"Prior rounds for this task: {task_dir}/"]
    lines += [f"  {a:<{widths[0]}}  {b:<{widths[1]}}  {c:<{widths[2]}}  {d}" for a, b, c, d in rows]
    return "\n".join(lines)


def input_paths(run_dir: Path) -> dict:
    inp = run_dir / "input"
    return {"scope": str(inp / "scope.md"), "goal": str(inp / "goal.md"),
            "profile": str(inp / "profile.md"), "context": str(inp / "context")}


def new(repo: Path, task: str, run: str | None) -> dict:
    if not repo.is_dir():
        raise ConfigError(f"{repo}: no such directory")
    task_dir = cross_config.confined(repo, repo / RUNS / task)
    previous = prior_runs(task_dir)
    if run is None:
        taken = [int(m.group(1)) for d in (task_dir.iterdir() if task_dir.is_dir() else [])
                 if d.is_dir() and (m := RUN_NUMBER.match(d.name))]
        run = f"{max(taken, default=0) + 1:02d}-{'after-fix' if previous else 'initial'}"
    run_dir = cross_config.confined(repo, task_dir / run)
    if run_dir.exists():
        raise ConfigError(f"{run_dir} already exists, give another --run")
    task_file = cross_config.confined(repo, task_dir / TASK_FILE)
    (run_dir / "input" / "context").mkdir(parents=True)
    if not task_file.exists():
        task_file.write_text(TASK_TEMPLATE)
    return {
        "task": task,
        "run": run,
        "task_dir": str(task_dir),
        "run_dir": str(run_dir),
        "task_file": str(task_file),
        "input": input_paths(run_dir),
        "previous": previous,
        "ignore": ensure_ignored(repo, RUNS / task / TASK_FILE),
    }


def locate(run_dir: Path) -> tuple[Path, str, str]:
    """The repository, task and run of <repo>/.cross-review/runs/<task>/<run>."""
    if not (run_dir / "input").is_dir():
        raise ConfigError(f"{run_dir}: no input/ here, create the run with `review_run.py new` first")
    if len(run_dir.parents) < 4 or run_dir.parents[1].name != RUNS.name or run_dir.parents[2].name != cross_config.REPO_DIR:
        raise ConfigError(f"{run_dir}: not a run directory, expected <repo>/{RUNS.as_posix()}/<task>/<run>")
    return run_dir.parents[3], run_dir.parent.name, run_dir.name


def filled(path: Path) -> bool:
    """A file with text in it, or a directory with at least one file below it."""
    if path.is_dir():
        return any(p.is_file() for p in path.rglob("*"))
    return path.is_file() and bool(path.read_text().strip())


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source(repo: Path, kind: str, name: str) -> dict:
    """Where a prompt, lens or schema came from, for the manifest."""
    path = cross_config.resolve(repo, kind, name)
    return {"path": f"{kind}/{path.name}", "layer": cross_config.layer_of(path), "file": str(path),
            "hash": digest(path)}


def compose(run_dir: Path, tier: str | None, claude: str | None, codex: str | None) -> dict:
    repo, task, run = locate(run_dir)
    answered = sorted(p.name for p in (run_dir / "agents").glob("*") if p.is_file())
    if answered or (run_dir / "stages").exists():
        raise ConfigError(f"{run_dir} already has answers ({', '.join(answered) or 'stages/'}), start a new run")
    inp = input_paths(run_dir)
    if not filled(Path(inp["scope"])):
        raise ConfigError(f"{inp['scope']} is missing or empty: write what is under review and the diff command first")

    cfg = cross_config.load_config(repo)
    models = cross_config.select(cfg, tier, claude, codex)
    agents = cross_config.roster_agents(cfg, models)
    values = {key.upper(): path if filled(Path(path)) else NONE for key, path in inp.items()}
    values["WORKDIR"] = str(repo)

    review = cross_config.render(cross_config.body(repo, "prompts", "review"), values, "prompts/review.md")
    lenses = sorted({lens for a in agents for lens in a["lenses"]})
    lens_text = {lens: cross_config.render(cross_config.body(repo, "lenses", lens), values, f"lenses/{lens}.md")
                 for lens in lenses}
    schema = cross_config.resolve(repo, "schemas", "finder")
    task_dir = run_dir.parent
    previous = prior_runs(task_dir, run)
    tail = [history(task_dir, previous), HISTORY_GUARD] if previous else []
    tail.append(OUTPUT_CONTRACT + schema.read_text().strip() + "\n")

    prompts_dir = run_dir / "prompts" / "agents"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    for stale in prompts_dir.glob("*.md"):
        stale.unlink()
    (run_dir / "agents").mkdir(exist_ok=True)
    launch, manifest_agents = [], []
    for agent in agents:
        prompt = prompts_dir / f"{agent['name']}.md"
        prompt.write_text("\n\n".join([review, *(lens_text[lens] for lens in agent["lenses"]), *tail]))
        output = run_dir / "agents" / f"{agent['name']}.json"
        entry = {"agent": agent["name"], "vendor": agent["vendor"]}
        if agent["vendor"] == "claude":
            entry["alias"] = agent["model"]
        else:
            entry["model"] = agent["model"]
        entry.update(effort=agent["effort"], prompt=str(prompt), output=str(output))
        if agent["vendor"] == "codex":
            entry["schema"] = str(schema)
            entry["command"] = shlex.join(["bash", str(SCRIPTS / "codex_run.sh"), str(repo), str(prompt), str(schema),
                                           str(output), agent["model"], agent["effort"]])
        launch.append(entry)
        manifest_agents.append({"name": agent["name"], "lenses": agent["lenses"], "executor": agent["vendor"],
                                "requested_model": agent["model"], "effort": agent["effort"],
                                "prompt": f"prompts/agents/{prompt.name}", "prompt_hash": digest(prompt)})

    manifest = {
        "task": task,
        "run": run,
        "scope_path": inp["scope"],
        "workdir": str(repo),
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tier": models["tier"],
        "pair": models["pair"],
        "models": {"claude": models["claude"], "codex": models["codex"]},
        "moderators": models["moderators"],
        "stages": {**cfg["stages"], **models["claude"]},
        "agents": manifest_agents,
        "prior_runs": [r["run"] for r in previous],
        "prompts": [source(repo, "prompts", "review"), *(source(repo, "lenses", lens) for lens in lenses),
                    source(repo, "schemas", "finder")],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    return {"run_dir": str(run_dir), "tier": models["tier"], "pair": models["pair"],
            "previous": manifest["prior_runs"], "launch": launch}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", metavar="{new,compose}", required=True)
    new_ap = sub.add_parser("new", help="create a run directory")
    new_ap.add_argument("repo", help="root of the reviewed repository")
    new_ap.add_argument("--task", required=True, type=check_name, help="task name, e.g. proj-123 or the branch")
    new_ap.add_argument("--run", type=check_name, help="run name; default NN-initial, then NN-after-fix")
    comp = sub.add_parser("compose", help="write the finder prompts and manifest.json of a run")
    comp.add_argument("run_dir", help="the run directory `new` created")
    comp.add_argument("--tier", help="a tier from the configuration; default: its `tier`")
    comp.add_argument("--claude", metavar="MODEL", help="one-off claude model, e.g. sonnet:high; needs --codex")
    comp.add_argument("--codex", metavar="MODEL", help="one-off codex model, e.g. gpt-6-sol:high; needs --claude")
    args = ap.parse_args(argv)
    if args.command == "compose" and (args.claude is None) != (args.codex is None):
        comp.error("--claude and --codex go together")
    if args.command == "compose" and args.claude is not None and args.tier is not None:
        comp.error("--tier and a --claude/--codex pair exclude each other")

    try:
        if args.command == "new":
            out = new(Path(args.repo).resolve(), args.task, args.run)
        else:
            out = compose(Path(args.run_dir).resolve(), args.tier, args.claude, args.codex)
    except ConfigError as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        return 1
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
