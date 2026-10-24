from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import types
import unittest
from pathlib import Path

from support import copy_fixture, last_json_line, read_json, run, write_json

from crossreview.report_page import agents_word

TITLE = "Перекрёстное ревью demo"
SUBJECT = "ветку `feature/DEMO-1`"
PAYLOAD = "<img src=x onerror=alert(1)>"
ESCAPED = "&lt;img src=x onerror=alert(1)&gt;"
NOT_ONE_OF = f"{PAYLOAD!r} is not one of"
EMBEDDED = re.compile(r"const DATA = (.*);\nconst SEV = ")
TO_FIX = re.compile(r'<div class="num">(\d+)</div><div class="lbl">Чинить</div>')
SHIFTS = types.MappingProxyType({
    "arch-codex-2": {"from": "none", "to": "major", "direction": "raised", "by": "side"},
    "arch-claude-2": {"from": "major", "to": "minor", "direction": "lowered", "by": "side"},
    "arch-claude-1": {"from": "minor", "to": "none", "direction": "dropped", "by": "side"},
})
TEXT_FIELDS = ("argument", "evidence", "answer", "verdict", "note")
SESSION = "Модератор спорных пунктов — основная сессия Claude Code; это тоже Claude"
PAIR = "независимые модераторы Claude и Codex:"
ONE_ANSWERED = "Модератор спорных пунктов — независимый модератор Claude; Codex не ответил, его споры решает человек."

# страница без браузера: document с заглушками, в которых копится всё, что страница пишет в innerHTML
HARNESS = """
const fs = require("fs");
const html = fs.readFileSync(process.argv[2], "utf8");
const script = html.split("<script>")[1].split("</script>")[0];
const sinks = [];
const node = () => {
  const el = { style: {}, dataset: {}, appendChild() {}, addEventListener() {}, setAttribute() {}, closest() { return null; } };
  Object.defineProperty(el, "innerHTML", { set(v) { sinks.push(String(v)); } });
  return el;
};
global.document = { getElementById: () => node(), createElement: () => node(), addEventListener() {} };
new Function(script)();
process.stdout.write(JSON.stringify(sinks));
"""


def examine(fid: str, stance: str, severity: str) -> dict:
    return {"id": fid, "stance": stance, "severity": severity, "argument": "Довод </script>.", "evidence": "app/x.py:1"}


def respond(fid: str, position: str, severity: str) -> dict:
    return {"id": fid, "position": position, "severity": severity, "argument": "Ответ.", "evidence": "app/x.py:2"}


def thread_entry(kind: str, **more: object) -> dict:
    """A thread entry whose every text field and side carry PAYLOAD."""
    return {"kind": kind, "side": PAYLOAD, **dict.fromkeys(TEXT_FIELDS, PAYLOAD), **more}


def card_changed(card: dict, **fields: object) -> dict:
    """final.json items with the one card these fields changed."""
    return {"items": [{**card, **fields}]}


def meta_changed(meta: dict, **fields: object) -> dict:
    """final.json meta with these fields changed."""
    return {"meta": {**meta, **fields}}


class ReportCase(unittest.TestCase):
    """final.json of run-basic with one raised, one lowered and one dropped item, rendered to report.html."""

    def setUp(self) -> None:
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name).resolve()
        self.run_dir = copy_fixture("run-basic", self.root / "run")
        self.debate = self.run_dir / "debate"
        self.assertEqual(run("debate_build", self.run_dir).returncode, 0)
        self.write_answers()
        res = run("debate_outcome", self.run_dir)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.out = self.root / "report.html"

    def write_answers(self) -> None:
        codex_verdicts = [examine("arch-claude-2", "real", "minor"), examine("arch-claude-1", "not_real", "none")]
        write_json(self.debate / "codex-r1.json", {"verdicts": codex_verdicts, "open_question_answers": []})
        claude_verdicts = [examine("arch-codex-2", "real_but_different", "major")]
        write_json(self.debate / "claude-r1.json", {"verdicts": claude_verdicts, "open_question_answers": []})
        claude_responses = [respond("arch-claude-2", "concede", "minor"), respond("arch-claude-1", "concede", "none")]
        write_json(self.debate / "claude-r2.json", {"responses": claude_responses, "side_findings": []})
        codex_responses = [respond("arch-codex-2", "concede", "major")]
        write_json(self.debate / "codex-r2.json", {"responses": codex_responses, "side_findings": []})

    def report(self, *args: str) -> dict:
        labels = ("--title", TITLE, "--subject", SUBJECT)
        res = run("debate_report", self.debate, self.out, *labels, *args)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return last_json_line(res.stdout)

    def page_data(self) -> dict:
        """The final.json payload the page embeds."""
        embedded = EMBEDDED.search(self.out.read_text())
        if embedded is None:
            self.fail("the payload is not where the template puts it")
        return json.loads(embedded.group(1))

    def footer(self, *args: str) -> str:
        self.report(*args)
        return self.page_data()["meta"]["footer_how"]


class RenderTest(ReportCase):
    def test_html_из_final_json(self) -> None:
        out = self.report()
        html = self.out.read_text()
        self.assertEqual(out["out"], str(self.out))
        self.assertNotIn("{{TITLE}}", html)
        self.assertNotIn("/*__DATA__*/null", html)
        self.assertEqual(html.count(f"<title>{TITLE}</title>"), 1)
        self.assertNotIn("Довод </script>", html)
        cards = read_json(self.debate / "final.json")["items"]
        embedded = [card["id"] for card in self.page_data()["items"]]
        self.assertEqual(embedded, [card["id"] for card in cards])
        for card in cards:
            with self.subTest(id=card["id"]):
                self.assertIn(f'"id": "{card["id"]}"', html)

    def test_заголовок_экранирован_и_в_данных_нет_угловых_скобок(self) -> None:
        res = run("debate_report", self.debate, self.out, "--title", f"MR {PAYLOAD}", "--subject", "x")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        html = self.out.read_text()
        self.assertNotIn("<img", html)
        self.assertEqual(html.count(f"MR {ESCAPED}"), 2)
        after = html.split("const DATA = ", 1)[1]
        payload = after.split(";\nconst SEV = ", 1)[0]
        self.assertNotIn("<", payload)
        self.assertIn("Довод </script>.", json.dumps(self.page_data(), ensure_ascii=False))

    def test_страница_не_ходит_в_сеть(self) -> None:
        self.report()
        self.assertIsNone(re.search("https?://", self.out.read_text()))

    def test_бейдж_было_стало(self) -> None:
        self.report()
        html = self.out.read_text()
        self.assertIn('class="tag shift shift-${esc(s.direction)}"', html)
        self.assertIn("было ${esc(fromName(s.from))} → стало ${esc(sevName(s.to))}", html)
        cards = self.page_data()["items"]
        shifts = {card["id"]: card["severity_shift"] for card in cards}
        self.assertEqual({fid: shifts[fid] for fid in SHIFTS}, dict(SHIFTS))

    def test_счёт_переоценки(self) -> None:
        out = self.report()
        html = self.out.read_text()
        self.assertEqual(out["reprioritized"], {"raised": 1, "lowered": 1, "dropped": 1})
        self.assertIn("<h2>Переоценка приоритетов</h2>", html)
        for label in ("Повышено", "Понижено", "Снято"):
            with self.subTest(label=label):
                self.assertIn(f'"{label}"', html)
        self.assertEqual(out["counts"]["restored:major"], 1)

    def test_подписи_по_русски_без_внешнего_прогона_для_своего(self) -> None:
        write_json(self.run_dir / "manifest.json", {"tier": "standard"})
        self.assertEqual(run("debate_outcome", self.run_dir).returncode, 0)
        self.report()
        meta = self.page_data()["meta"]
        self.assertTrue(meta["footer_how"].startswith("Ревью: cross-review (находки, склейка, проверка по коду)."))
        self.assertTrue(meta["lede"].startswith("2 агента Claude (claude-opus-5-5) и 2 агента Codex (gpt-6.1-sol)"))
        script = self.out.read_text().split("<script>", 1)[1]
        self.assertNotIn("внешний прогон", script)

    def test_внешний_прогон_назван_в_подвале(self) -> None:
        self.report()
        self.assertTrue(self.page_data()["meta"]["footer_how"].startswith("Ревью: внешний прогон."))

    def test_подвал_называет_модератора_по_файлам_спора(self) -> None:
        with self.subTest(case="без файлов модераторов — сессия"):
            self.assertIn(SESSION, self.footer())
        self.check_pair_without_models()
        self.check_manifest_models()
        (self.debate / "moderator-codex.json").unlink()
        with self.subTest(case="ответил один модератор"):
            self.assertIn(ONE_ANSWERED, self.footer())
        with self.subTest(case="--moderator переопределяет"):
            self.assertIn("Модератор спорных пунктов — человек.", self.footer("--moderator", "человек"))

    def check_pair_without_models(self) -> None:
        write_json(self.debate / "moderator-claude.json", [])
        write_json(self.debate / "moderator-codex.json", {"decisions": [], "extra": []})
        with self.subTest(case="внешний прогон без манифеста — пара без моделей"):
            text = self.footer()
            self.assertIn(f"Модератор спорных пунктов — {PAIR}", text)
            self.assertIn("где они разошлись, решает человек", text)
            self.assertNotIn("основная сессия", text)

    def check_manifest_models(self) -> None:
        moderators = {
            "claude": {"model": "fable", "effort": "high"},
            "codex": {"model": "gpt-6-astra", "effort": "high"},
        }
        write_json(self.run_dir / "manifest.json", {"tier": "standard", "moderators": moderators})
        with self.subTest(case="свой прогон — модели из манифеста"):
            self.assertIn("независимые модераторы Claude (fable:high) и Codex (gpt-6-astra:high):", self.footer())
        with self.subTest(case="манифест без моделей модераторов не ломает отчёт"):
            write_json(self.run_dir / "manifest.json", {"tier": "standard", "moderators": {"claude": "fable:high"}})
            self.assertIn(PAIR, self.footer())
            (self.run_dir / "manifest.json").write_text("{")
            self.assertIn(PAIR, self.footer())

    def test_final_json_с_полями_не_того_типа_отклоняется(self) -> None:
        final = read_json(self.debate / "final.json")
        for label, change, needle in self.broken_cards(final) + self.broken_meta(final):
            with self.subTest(case=label):
                write_json(self.debate / "final.json", {**final, **change})
                res = run("debate_report", self.debate, self.out, "--title", TITLE, "--subject", "x")
                self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
                self.assertIn(needle, last_json_line(res.stdout)["error"])
                self.assertFalse(self.out.exists())

    def broken_cards(self, final: dict) -> list[tuple[str, dict, str]]:
        card = final["items"][0]
        examined = {"kind": "examine", "stance": "real", "severity": "major", "argument": "a", "evidence": "e"}
        same = {"from": "major", "to": "major", "direction": "same"}
        return [
            ("line строкой", card_changed(card, line=PAYLOAD), "items[0].line: expected integer"),
            ("outcome вне списка", card_changed(card, outcome=PAYLOAD), f"items[0].outcome: {NOT_ONE_OF}"),
            ("серьёзность вне списка", card_changed(card, final_severity="high"),
             "items[0].final_severity: 'high' is not one of"),
            ("vendors не строки", card_changed(card, vendors=[1]), "items[0].vendors[0]: expected string"),
            ("severity_shift без by", card_changed(card, severity_shift=same), "items[0].severity_shift.by: missing"),
            ("запись переписки без side", card_changed(card, thread=[examined]), "items[0].thread[0].side: missing"),
            ("stance вне списка", card_changed(card, thread=[{**examined, "side": "codex", "stance": PAYLOAD}]),
             f"items[0].thread[0].stance: {NOT_ONE_OF}"),
            ("неизвестный kind", card_changed(card, thread=[{"kind": "note"}]),
             "items[0].thread[0].kind: 'note' is not one of"),
        ]

    def broken_meta(self, final: dict) -> list[tuple[str, dict, str]]:
        meta = final["meta"]
        agent = meta["agents"][0]
        question = dict(final["questions"][0])
        question.pop("line")
        return [
            ("executor числом", meta_changed(meta, agents=[{**agent, "executor": 1}]),
             "meta.agents[0].executor: expected string"),
            ("raised строкой", meta_changed(meta, agents=[{**agent, "raised": PAYLOAD}]),
             "meta.agents[0].raised: expected integer"),
            ("модель списком", meta_changed(meta, agents=[{**agent, "actual_model": ["x"]}]),
             "meta.agents[0].actual_model: expected string or null"),
            ("raw строкой", meta_changed(meta, raw="7"), "meta.raw: expected integer"),
            ("pipeline вне списка", meta_changed(meta, pipeline=PAYLOAD), f"meta.pipeline: {NOT_ONE_OF}"),
            ("без questions", {"questions": None}, "final.json.questions: expected array"),
            ("вопрос без line", {"questions": [question]}, "questions[0].line: missing"),
        ]


@unittest.skipUnless(shutil.which("node"), "страницу выполняет node")
class PageTest(ReportCase):
    """The page run under node with a stub document: what it writes into innerHTML."""

    def sinks(self) -> list[str]:
        harness = self.root / "harness.js"
        harness.write_text(HARNESS)
        argv = ["node", str(harness), str(self.out)]
        res = subprocess.run(argv, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)
        return json.loads(res.stdout)

    def test_страница_строится_из_отчёта(self) -> None:
        self.report()
        html = "".join(self.sinks())
        for needle in ("Довод &lt;/script&gt;.", "arch-codex-2", "bugs-claude", "Чинить", "Повышено"):
            with self.subTest(needle=needle):
                self.assertIn(needle, html)

    def to_fix(self) -> int:
        """The number of the «Чинить» card of the summary."""
        found = TO_FIX.search("".join(self.sinks()))
        self.assertIsNotNone(found)
        return int(found.group(1)) if found else -1

    def test_дефект_до_изменения_не_в_чинить(self) -> None:
        self.report()
        before = self.to_fix()
        final = read_json(self.debate / "final.json")
        cards = final["items"]
        settled = [card for card in cards if card["outcome"] in {"consensus", "confirmed"}]
        card = next(card for card in settled if card["final_severity"] != "none")
        card["status"] = "pre_existing"
        write_json(self.debate / "final.json", final)
        self.report()
        self.assertEqual(self.to_fix(), before - 1)

    def test_числа_согласованы_с_существительными(self) -> None:
        self.report()
        html = "".join(self.sinks())
        self.assertIsNone(re.search(r"(?<!\d)[1-4] (пунктов|находок|возражений)", html))
        self.assertIsNone(re.search(r"(?<!\d)1 (пункта|находки|возражения) ", html))

    def test_страница_экранирует_каждое_поле(self) -> None:
        # всё, что схема оставляет строкой: текст, имена сторон и статусов, которые страница кладёт в классы
        self.write_hostile_final()
        res = run("debate_report", self.debate, self.out, "--title", TITLE, "--subject", PAYLOAD)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        html = "".join(self.sinks())
        self.assertNotIn("<img", html)
        self.assertNotIn("onerror=alert(1)>", html)
        self.assertGreater(html.count(ESCAPED), 30)
        self.assertIn(f'class="side-{ESCAPED}"', html)
        self.assertIn(f'class="msg {ESCAPED}"', html)

    def write_hostile_final(self) -> None:
        final = read_json(self.debate / "final.json")
        meta = final["meta"]
        agent = {**meta["agents"][0], "name": PAYLOAD, "executor": PAYLOAD, "actual_model": PAYLOAD,
                 "requested_model": PAYLOAD}
        hostile_meta = {**meta, "agents": [agent], "round_dir": PAYLOAD, "debate_dir": PAYLOAD, "verify_model": PAYLOAD}
        write_json(self.debate / "final.json", {
            "items": [self.hostile_card(final["items"][0])],
            "questions": [self.hostile_question(final["questions"][0])],
            "meta": hostile_meta,
        })

    def hostile_card(self, card: dict) -> dict:
        thread = [
            thread_entry("finding", sources=[PAYLOAD]),
            thread_entry("pipeline", status=PAYLOAD),
            thread_entry("examine", stance="real", severity="major"),
            thread_entry("respond", position="amend", severity="minor"),
            thread_entry("answer"),
            thread_entry("moderator", severity="major"),
        ]
        texts = dict.fromkeys(("id", "title", "body", "fix", "status"), PAYLOAD)
        return {**card, **texts, "file": f"{PAYLOAD}/x.py", "sources": [PAYLOAD], "vendors": [PAYLOAD],
                "outcome": "disputed", "moderated": True, "thread": thread}

    def hostile_question(self, question: dict) -> dict:
        thread = [thread_entry("finding", sources=[PAYLOAD]), thread_entry("answer")]
        texts = dict.fromkeys(("title", "body", "file"), PAYLOAD)
        return {**question, "id": f"q{PAYLOAD}", **texts, "thread": thread}


class ErrorsTest(unittest.TestCase):
    def test_без_final_json_ошибка(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            out = Path(workspace) / "report.html"
            res = run("debate_report", workspace, out, "--title", TITLE, "--subject", "x")
            self.assertEqual(res.returncode, 1)
            self.assertIn("no final.json", last_json_line(res.stdout)["error"])
            self.assertFalse(out.exists())

    def test_битый_final_json_ошибка(self) -> None:
        cases = (
            ("не JSON", "{"),
            ("без meta", '{"items": []}'),
            ("не объект", "[]"),
            ("пустой пункт", '{"meta": {"agents": []}, "items": [{}]}'),
            ("meta без agents", '{"meta": {}, "items": []}'),
            ("пункт без thread",
             '{"meta": {"agents": []}, "items": [{"id": "a", "outcome": "side", "final_severity": "minor"}]}'),
            ("finding без side и sources", '{"meta": {"agents": []}, "items": [{"id": "a", "outcome": "side", '
                                          '"final_severity": "minor", "thread": [{"kind": "finding"}]}]}'),
            ("вопрос с finding без sources", '{"meta": {"agents": []}, "items": [], "questions": [{"id": "q", '
                                            '"thread": [{"kind": "finding", "side": "codex"}]}]}'),
        )
        for label, text in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as workspace:
                self.assert_rejected(Path(workspace), text)

    def assert_rejected(self, debate: Path, text: str) -> None:
        (debate / "final.json").write_text(text)
        out = debate / "report.html"
        res = run("debate_report", debate, out, "--title", TITLE, "--subject", "x")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("not the output of debate_outcome.py", last_json_line(res.stdout)["error"])
        self.assertFalse(out.exists())

    def test_без_обязательных_флагов_выход2(self) -> None:
        res = run("debate_report", "debate", "out.html")
        self.assertEqual(res.returncode, 2)
        self.assertIn("--title", res.stderr)


class WordsTest(unittest.TestCase):
    def test_склонение_агентов(self) -> None:
        cases = (
            (1, "агент"),
            (2, "агента"),
            (4, "агента"),
            (5, "агентов"),
            (11, "агентов"),
            (12, "агентов"),
            (21, "агент"),
            (22, "агента"),
            (0, "агентов"),
        )
        for count, word in cases:
            with self.subTest(n=count):
                self.assertEqual(agents_word(count), word)


if __name__ == "__main__":
    unittest.main()
