"""The steps of the debate: both rounds of each side and the three moderator slots, with the output each
prompt asks for."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from crossreview.jsonio import read_object
from crossreview.layers import layer_of, resolve
from crossreview.stage_run import record_prompts

PROMPTS = ("examine", "respond", "moderate")
FRESH = "prompt-r2-codex.fresh.md"
CODEX_OUTPUT = "Return JSON matching the output schema."
# the session moderates itself, or hands the brief to a Claude agent; the other two are the independent pair
MODERATOR_SLOTS = (("", "session"), ("-claude", "claude"), ("-codex", "codex"))


class RoundStep(NamedTuple):
    """One round: its step name, the prompt it renders, the input file of a side and the reply a Claude side gives."""

    step: str
    prompt: str
    input_pattern: str
    reply: str


ROUND_STEPS = (
    RoundStep("r1", "examine", "for-{side}.json", "how many verdicts per stance"),
    RoundStep("r2", "respond", "for-{side}-r2.json", "how many responses per position"),
)


def prompt_source(repo: Path, name: str) -> dict:
    """Where a debate prompt came from: its file and its layer."""
    path = resolve(repo, "prompts", name)
    return {"path": str(path), "layer": layer_of(path)}


def record_debate_prompts(round_dir: Path, repo: Path) -> None:
    """Add the three debate prompts to a cross-review run's manifest, replacing an earlier run of the script's."""
    manifest = read_object(round_dir / "manifest.json")
    if manifest:
        record_prompts(round_dir, manifest, repo, *(("prompts", name) for name in PROMPTS))


def output_claude(path: Path, schema: Path, reply: str) -> str:
    """The output paragraph of a Claude side: write the answer with the Write tool, reply with one line."""
    return (
        f"Write the JSON, one object matching `{schema}`, to `{path}` with the Write tool — the only file you "
        f"may create — and reply with one line: {reply}."
    )


def output_moderator(debate_dir: Path, suffix: str) -> str:
    """The output paragraph of a Claude moderator: the decisions and the extras, each as a JSON array."""
    return (
        f"Write `{debate_dir}/moderator{suffix}.json` as a JSON array of the `decisions` entries and "
        f"`{debate_dir}/extra{suffix}.json` as a JSON array of the `extra` entries, each extra with "
        '`"sources": ["<side>"], "vendors": ["<side>"]` added. Those two are the only files you create. '
        "Reply with one line per dispute: id, verdict, severity."
    )
