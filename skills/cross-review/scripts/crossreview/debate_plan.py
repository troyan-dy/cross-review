"""`debate_prompts.py`: the prompts and schemas of both debate sides and the moderators, and the launch plan."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from crossreview.config_load import load_config
from crossreview.debate_context import default_context, own_run, tree_state
from crossreview.debate_launch import LaunchPlan
from crossreview.debate_panel import run_panel
from crossreview.debate_schemas import write_schemas
from crossreview.debate_steps import PROMPTS, prompt_source, record_debate_prompts
from crossreview.debate_texts import DebateTexts, load_templates, write_texts
from crossreview.selection import side_models

NONE = "- none provided"
STATUS_LEGEND = """- `confirmed` / `refined` — the verifier accepted it (refined: with corrections)
- `immaterial` — the verifier found it accurate but not worth acting on
- `pre_existing` — the verifier judged it real but not introduced by this change; judge also whether this change made it reachable or worse
- `rejected_by_verify` — the verifier rejected it
- `dropped_by_synthesis` — a weak single-source finding the merge step dropped before anyone checked it
- `unverified` — the verify stage was skipped; nobody has checked it yet"""


class Request(NamedTuple):
    """What debate_prompts.py was asked for."""

    round_dir: Path
    debate_dir: Path
    subject: str
    workdir: str | None
    context: list[tuple[str, str]]
    ref: str | None


class DebatePlan:
    """The debate of one run: its repository, the prompt templates and what the prompts say about the run."""

    def __init__(self, request: Request) -> None:
        """Resolve the repository and the prompt templates, then describe the panel, the context and the tree."""
        self._request = request
        self._panel = run_panel(request.round_dir)
        self._run_repo = own_run(request.round_dir)
        self._workdir = request.workdir or str(self._run_repo or Path.cwd())
        self._repo = self._run_repo or Path(self._workdir).resolve()
        load_config(self._repo)
        self._templates = load_templates(self._repo)
        self._context = [*default_context(request.round_dir), *request.context]
        self._tree_note = tree_state(self._workdir, request.ref)

    def build(self) -> dict:
        """Write the prompts and the schemas; the launch plan is the result."""
        debate_dir = self._request.debate_dir
        schemas = _schema_paths(debate_dir)
        models = side_models(self._request.round_dir, self._repo)
        texts = DebateTexts(self._templates, self._common(), debate_dir, schemas).all()
        launch = LaunchPlan(debate_dir, schemas, self._workdir, models).entries()
        write_texts(debate_dir, texts)
        write_schemas(schemas)
        if self._run_repo is not None:
            record_debate_prompts(self._request.round_dir, self._repo)
        return self._plan(texts, schemas, launch)

    def _plan(self, texts: dict, schemas: dict, launch: list[dict]) -> dict:
        return {
            "debate_dir": str(self._request.debate_dir),
            "wrote": [*texts, *(schema.name for schema in schemas.values())],
            "panel": self._panel,
            "tree": self._tree_note,
            "context": [path for path, _ in self._context],
            "prompts": {name: prompt_source(self._repo, name) for name in PROMPTS},
            "launch": launch,
        }

    def _common(self) -> dict[str, str]:
        lines = [f"- `{path}` — {why}" for path, why in self._context]
        context = "\n".join(lines)
        return {
            "SUBJECT": self._request.subject,
            "WORKDIR": self._workdir,
            "PANEL": self._panel,
            "CONTEXT": context or NONE,
            "TREE_NOTE": self._tree_note,
            "DEBATE_DIR": str(self._request.debate_dir),
            "STATUS_LEGEND": STATUS_LEGEND,
        }


def _schema_paths(debate_dir: Path) -> dict[str, Path]:
    return {name: debate_dir / f"schema-{name}.json" for name in PROMPTS}
