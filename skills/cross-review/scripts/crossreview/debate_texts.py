"""The texts of the debate prompts: both rounds of each side, the fresh Codex round 2 and the moderator briefs."""

from __future__ import annotations

import types
from pathlib import Path

from crossreview.debate_panel import SIDE_NAME
from crossreview.debate_steps import CODEX_OUTPUT, FRESH, MODERATOR_SLOTS, PROMPTS, ROUND_STEPS, RoundStep
from crossreview.debate_steps import output_claude, output_moderator
from crossreview.models import CLAUDE, CODEX, SIDES
from crossreview.texts import body, render

OTHER = types.MappingProxyType({CLAUDE: CODEX, CODEX: CLAUDE})
OUTPUT = "OUTPUT"
NEWLINE = "\n"
# the output of the round-1 prompt inside a fresh Codex round 2: the round-2 prompt follows it
FRESH_BRIDGE = (
    "Round 1 is over: your verdicts on these findings are in `{verdicts}` (no such file means you had "
    "nothing to examine). Do not examine them again; answer round 2 below."
)


def load_templates(repo: Path) -> dict[str, str]:
    """The bodies of the debate prompts, resolved through both configuration layers."""
    return {name: body(repo, "prompts", name) for name in PROMPTS}


def write_texts(debate_dir: Path, texts: dict[str, str]) -> None:
    """The prompt files in the debate directory, created when missing."""
    debate_dir.mkdir(parents=True, exist_ok=True)
    for name, text in texts.items():
        (debate_dir / name).write_text(text)


class DebateTexts:
    """Every prompt file of the debate, rendered from the prompts of both configuration layers."""

    def __init__(
        self,
        templates: dict[str, str],
        common: dict[str, str],
        debate_dir: Path,
        schemas: dict[str, Path],
    ) -> None:
        """templates are the prompt bodies, common the values every prompt shares."""
        self._templates = templates
        self._common = common
        self._dir = debate_dir
        self._schemas = schemas

    def all(self) -> dict[str, str]:
        """The prompt files by name, in the order the launch plan uses them."""
        texts = {}
        for round_step in ROUND_STEPS:
            for side in SIDES:
                texts[f"prompt-{round_step.step}-{side}.md"] = self._side_text(round_step, side)
        texts[FRESH] = self._fresh(texts["prompt-r2-codex.md"])
        for suffix, vendor in MODERATOR_SLOTS:
            texts[f"prompt-moderator{suffix}.md"] = self._moderator_text(suffix, vendor)
        return texts

    def render(self, name: str, replacements: dict[str, str]) -> str:
        """A prompt with the shared values and these, ending in one newline."""
        filled = {**self._common, **replacements}
        text = render(self._templates[name], filled, f"prompts/{name}.md")
        return f"{text.rstrip(NEWLINE)}\n"

    def _side_values(self, side: str, input_name: str) -> dict[str, str]:
        return {
            "SIDE": SIDE_NAME[side],
            "OTHER": SIDE_NAME[OTHER[side]],
            "INPUT": str(self._dir / input_name),
        }

    def _side_text(self, round_step: RoundStep, side: str) -> str:
        output = self._dir / f"{side}-{round_step.step}.json"
        schema = self._schemas[round_step.prompt]
        reply = output_claude(output, schema, round_step.reply) if side == CLAUDE else CODEX_OUTPUT
        side_values = self._side_values(side, round_step.input_pattern.format(side=side))
        return self.render(round_step.prompt, {**side_values, OUTPUT: reply})

    def _fresh(self, round2: str) -> str:
        """Round 1 with the bridge as its output, then round 2: a fresh session sees what it examined and answered."""
        bridge = FRESH_BRIDGE.format(verdicts=self._dir / "codex-r1.json")
        fresh_r1 = self.render("examine", {**self._side_values(CODEX, "for-codex.json"), OUTPUT: bridge})
        return "\n\n".join([fresh_r1.rstrip("\n"), "---", round2])

    def _moderator_text(self, suffix: str, vendor: str) -> str:
        reply = CODEX_OUTPUT if vendor == CODEX else output_moderator(self._dir, suffix)
        disputes = str(self._dir / "disputes.json")
        return self.render("moderate", {"INPUT": disputes, OUTPUT: reply})
