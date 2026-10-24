"""The texts of the report page around the cards: lede, eyebrow, footer and who moderated."""

from __future__ import annotations

import html
import json
from datetime import date
from pathlib import Path

from crossreview.debate_panel import SIDE_NAME, model_name
from crossreview.jsonio import read_object
from crossreview.layers import SKILL
from crossreview.models import CLAUDE, CODEX, EFFORT, MODEL, SIDES
from crossreview.report_schema import PIPELINE

TEMPLATE = SKILL / "assets" / "templates" / "report.html"
SESSION_MODERATOR = "основная сессия Claude Code; это тоже Claude, поэтому его вывод помечен отдельно"
EXTERNAL = "external"
FEW = (2, 3, 4)
TEENS = (11, 12, 13, 14)
DECIMAL = 10
HUNDRED = 100


def agents_word(count: int) -> str:
    """«агент», «агента» or «агентов» for count."""
    last, last_two = count % DECIMAL, count % HUNDRED
    if last == 1 and last_two != TEENS[0]:
        return "агент"
    if last in FEW and last_two not in TEENS[1:]:
        return "агента"
    return "агентов"


def moderator_line(debate_dir: Path, round_dir: Path) -> str:
    """Who moderated, as the footer names it: the session, or the independent moderators whose answers
    (moderator-<vendor>.json of debate_moderators.py) lie in the debate directory, each with its model from
    the run's manifest.json `moderators` when the manifest has it — an external run has no manifest."""
    present = [vendor for vendor in SIDES if (debate_dir / f"moderator-{vendor}.json").is_file()]
    if not present:
        return SESSION_MODERATOR
    moderators = read_object(round_dir / "manifest.json").get("moderators")
    moderators = moderators if isinstance(moderators, dict) else {}
    if len(present) == len(SIDES):
        return (
            f"независимые модераторы {_moderator_name(moderators, CLAUDE)} и {_moderator_name(moderators, CODEX)}: "
            "спор решён, когда оба сошлись, серьёзность берётся строже; где они разошлись, решает человек"
        )
    missing = next(vendor for vendor in SIDES if vendor not in present)
    named = _moderator_name(moderators, present[0])
    return f"независимый модератор {named}; {SIDE_NAME[missing]} не ответил, его споры решает человек"


def page_texts(meta: dict, subject: str, moderator: str) -> dict[str, str]:
    """The lede and the footer of the page."""
    agents = meta.get("agents") or []
    pipeline = PIPELINE.get(meta.get("pipeline") or EXTERNAL, PIPELINE[EXTERNAL])
    claude, codex = _panel(agents, CLAUDE), _panel(agents, CODEX)
    return {
        "lede": (
            f"{claude} и {codex} независимо прочитали {subject}. "
            "Потом каждая сторона проверила по коду находки другой и ответила на возражения. "
            "Ниже то, что осталось после спора."
        ),
        "footer_how": (
            f"Ревью: {pipeline}. Обсуждение: сессия Codex через `codex exec` / `resume` и субагент Claude, оба только "
            f"читали код. Модератор спорных пунктов — {moderator}."
        ),
        "footer_data": f"Данные: `{meta.get('round_dir')}` и `{meta.get('debate_dir')}`.",
    }


def eyebrow(meta: dict) -> str:
    """The default line above the title: task · run · today."""
    task = meta.get("task") or ""
    run = meta.get("run") or ""
    today = date.today().isoformat()
    return f"{task} · {run} · {today}"


def write_page(out: Path, title: str, final: dict) -> None:
    """The page: the template with the title and final.json as its data."""
    page = TEMPLATE.read_text().replace("{{TITLE}}", html.escape(title))
    # no `<` inside the script element: neither `</script>` nor `<!--` can end or bend it
    payload = json.dumps(final, ensure_ascii=False).replace("<", r"\u003c")
    out.write_text(page.replace("/*__DATA__*/null", payload))


def _moderator_name(moderators: dict, vendor: str) -> str:
    entry = moderators.get(vendor)
    model = entry.get(MODEL) if isinstance(entry, dict) else None
    effort = entry.get(EFFORT) if isinstance(entry, dict) else None
    if isinstance(model, str) and isinstance(effort, str):
        return f"{SIDE_NAME[vendor]} ({model}:{effort})"
    return SIDE_NAME[vendor]


def _panel(agents: list[dict], side: str) -> str:
    mine = [agent for agent in agents if agent.get("executor") == side]
    models = ", ".join(sorted({model_name(agent) for agent in mine}))
    count = len(mine)
    return f"{count} {agents_word(count)} {SIDE_NAME[side]} ({models})"
