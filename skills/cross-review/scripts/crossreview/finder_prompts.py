"""The texts every finder prompt of a run is made of: the `review` prompt, the lenses, the prior runs and the
finder schema."""

from __future__ import annotations

from pathlib import Path

from crossreview.history import HISTORY_GUARD, history
from crossreview.layers import resolve
from crossreview.prior import prior_runs
from crossreview.runs import OUTPUT_CONTRACT, input_or_none, input_paths
from crossreview.texts import body, render

LENSES = "lenses"


class FinderPrompts:
    """The rendered parts of the finder prompts; text() puts one agent's prompt together."""

    def __init__(self, repo: Path, run_dir: Path, agents: list[dict]) -> None:
        """Render the review prompt and every lens the roster uses, then read the task's prior runs."""
        inputs = input_paths(run_dir).items()
        self._replacements = {key.upper(): input_or_none(path) for key, path in inputs}
        self._replacements["WORKDIR"] = str(repo)
        self.review = render(body(repo, "prompts", "review"), self._replacements, "prompts/review.md")
        lenses = sorted({lens for agent in agents for lens in agent[LENSES]})
        self.lenses = {lens: self._lens(repo, lens) for lens in lenses}
        self.schema = resolve(repo, "schemas", "finder")
        self.previous = prior_runs(run_dir.parent, run_dir.name)
        self._tail = self._closing(run_dir.parent)

    def text(self, agent: dict) -> str:
        """The prompt of one agent: the review prompt, its own lenses, the shared tail."""
        own = [self.lenses[lens] for lens in agent[LENSES]]
        return "\n\n".join([self.review, *own, *self._tail])

    def _lens(self, repo: Path, lens: str) -> str:
        return render(body(repo, LENSES, lens), self._replacements, f"lenses/{lens}.md")

    def _closing(self, task_dir: Path) -> list[str]:
        schema_text = self.schema.read_text().strip()
        contract = f"{OUTPUT_CONTRACT}{schema_text}\n"
        if not self.previous:
            return [contract]
        return [history(task_dir, self.previous), HISTORY_GUARD, contract]
