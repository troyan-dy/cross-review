from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from support import copy_fixture, last_json_line, read_json, run, write_json

import debate_report

TITLE = "Перекрёстное ревью demo"
PAYLOAD = "<img src=x onerror=alert(1)>"

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


class ReportCase(unittest.TestCase):
    """final.json of run-basic with one raised, one lowered and one dropped item, rendered to report.html."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.run_dir = copy_fixture("run-basic", self.root / "run")
        self.debate = self.run_dir / "debate"
        self.assertEqual(run("debate_build", self.run_dir).returncode, 0)

        def v(fid: str, stance: str, severity: str) -> dict:
            return {"id": fid, "stance": stance, "severity": severity, "argument": "Довод </script>.", "evidence": "app/x.py:1"}

        def r(fid: str, position: str, severity: str) -> dict:
            return {"id": fid, "position": position, "severity": severity, "argument": "Ответ.", "evidence": "app/x.py:2"}

        write_json(self.debate / "codex-r1.json", {"verdicts": [v("arch-claude-2", "real", "minor"), v("arch-claude-1", "not_real", "none")],
                                                         "open_question_answers": []})
        write_json(self.debate / "claude-r1.json", {"verdicts": [
            v("arch-codex-2", "real_but_different", "major")], "open_question_answers": []})
        write_json(self.debate / "claude-r2.json", {"responses": [r("arch-claude-2", "concede", "minor"), r("arch-claude-1", "concede", "none")],
                                                          "side_findings": []})
        write_json(self.debate / "codex-r2.json", {"responses": [
            r("arch-codex-2", "concede", "major")], "side_findings": []})
        res = run("debate_outcome", self.run_dir)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.out = self.root / "report.html"

    def report(self, *args: str) -> dict:
        res = run("debate_report", self.debate, self.out, "--title", TITLE, "--subject", "ветку `feature/DEMO-1`", *args)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return last_json_line(res.stdout)

    def data(self) -> dict:
        """The final.json payload the page embeds."""
        html = self.out.read_text()
        m = re.search(r"const DATA = (.*);\nconst SEV = ", html)
        if m is None:
            self.fail("the payload is not where the template puts it")
        return json.loads(m.group(1))


class RenderTest(ReportCase):
    def test_html_из_final_json(self) -> None:
        out = self.report()
        html = self.out.read_text()
        self.assertEqual(out["out"], str(self.out))
        self.assertNotIn("{{TITLE}}", html)
        self.assertNotIn("/*__DATA__*/null", html)
        self.assertEqual(html.count(f"<title>{TITLE}</title>"), 1)
        self.assertNotIn("Довод </script>", html)
        items = read_json(self.debate / "final.json")["items"]
        self.assertEqual([i["id"] for i in self.data()["items"]], [i["id"] for i in items])
        for item in items:
            with self.subTest(id=item["id"]):
                self.assertIn(f'"id": "{item["id"]}"', html)

    def test_заголовок_экранирован_и_в_данных_нет_угловых_скобок(self) -> None:
        res = run("debate_report", self.debate, self.out, "--title", "MR <img src=x onerror=alert(1)>", "--subject", "x")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        html = self.out.read_text()
        self.assertNotIn("<img", html)
        self.assertEqual(html.count("MR &lt;img src=x onerror=alert(1)&gt;"), 2)
        payload = html.split("const DATA = ", 1)[1].split(";\nconst SEV = ", 1)[0]
        self.assertNotIn("<", payload)
        self.assertIn("Довод </script>.", json.dumps(self.data(), ensure_ascii=False))

    def test_страница_не_ходит_в_сеть(self) -> None:
        self.report()
        self.assertIsNone(re.search(r"https?://", self.out.read_text()))

    def test_бейдж_было_стало(self) -> None:
        self.report()
        html = self.out.read_text()
        self.assertIn('class="tag shift shift-${esc(s.direction)}"', html)
        self.assertIn("было ${esc(fromName(s.from))} → стало ${esc(sevName(s.to))}", html)
        shifts = {i["id"]: i["severity_shift"] for i in self.data()["items"]}
        self.assertEqual(shifts["arch-codex-2"], {"from": "none", "to": "major", "direction": "raised", "by": "side"})
        self.assertEqual(shifts["arch-claude-2"], {"from": "major", "to": "minor", "direction": "lowered", "by": "side"})
        self.assertEqual(shifts["arch-claude-1"], {"from": "minor", "to": "none", "direction": "dropped", "by": "side"})

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
        data = self.data()
        self.assertTrue(data["meta"]["footer_how"].startswith("Ревью: cross-review (находки, склейка, проверка по коду)."))
        self.assertTrue(data["meta"]["lede"].startswith("2 агента Claude (claude-opus-5-5) и 2 агента Codex (gpt-6.1-sol)"))
        script = self.out.read_text().split("<script>", 1)[1]
        self.assertNotIn("внешний прогон", script)

    def test_внешний_прогон_назван_в_подвале(self) -> None:
        self.report()
        self.assertTrue(self.data()["meta"]["footer_how"].startswith("Ревью: внешний прогон."))

    def test_подвал_называет_модератора_по_файлам_спора(self) -> None:
        def how(*args: str) -> str:
            self.report(*args)
            return self.data()["meta"]["footer_how"]

        with self.subTest(case="без файлов модераторов — сессия"):
            self.assertIn("Модератор спорных пунктов — основная сессия Claude Code; это тоже Claude", how())
        write_json(self.debate / "moderator-claude.json", [])
        write_json(self.debate / "moderator-codex.json", {"decisions": [], "extra": []})
        with self.subTest(case="внешний прогон без манифеста — пара без моделей"):
            text = how()
            self.assertIn("Модератор спорных пунктов — независимые модераторы Claude и Codex:", text)
            self.assertIn("где они разошлись, решает человек", text)
            self.assertNotIn("основная сессия", text)
        write_json(self.run_dir / "manifest.json", {"tier": "standard", "moderators": {
            "claude": {"model": "fable", "effort": "high"}, "codex": {"model": "gpt-6-astra", "effort": "high"}}})
        with self.subTest(case="свой прогон — модели из манифеста"):
            self.assertIn("независимые модераторы Claude (fable:high) и Codex (gpt-6-astra:high):", how())
        with self.subTest(case="манифест без моделей модераторов не ломает отчёт"):
            write_json(self.run_dir / "manifest.json", {"tier": "standard", "moderators": {"claude": "fable:high"}})
            self.assertIn("независимые модераторы Claude и Codex:", how())
            (self.run_dir / "manifest.json").write_text("{")
            self.assertIn("независимые модераторы Claude и Codex:", how())
        (self.debate / "moderator-codex.json").unlink()
        with self.subTest(case="ответил один модератор"):
            self.assertIn("Модератор спорных пунктов — независимый модератор Claude; Codex не ответил, его споры решает человек.", how())
        with self.subTest(case="--moderator переопределяет"):
            self.assertIn("Модератор спорных пунктов — человек.", how("--moderator", "человек"))

    def test_final_json_с_полями_не_того_типа_отклоняется(self) -> None:
        final = read_json(self.debate / "final.json")
        item = final["items"][0]
        agent = final["meta"]["agents"][0]
        cases = [("line строкой", {"items": [{**item, "line": PAYLOAD}]}, "items[0].line: expected integer"),
                 ("outcome вне списка", {"items": [{**item, "outcome": PAYLOAD}]}, "items[0].outcome: '<img src=x onerror=alert(1)>' is not one of"),
                 ("серьёзность вне списка", {"items": [{**item, "final_severity": "high"}]}, "items[0].final_severity: 'high' is not one of"),
                 ("vendors не строки", {"items": [{**item, "vendors": [1]}]}, "items[0].vendors[0]: expected string"),
                 ("severity_shift без by", {"items": [{**item, "severity_shift": {"from": "major", "to": "major", "direction": "same"}}]},
                  "items[0].severity_shift.by: missing"),
                 ("запись переписки без side", {"items": [{**item, "thread": [{"kind": "examine", "stance": "real", "severity": "major",
                                                                           "argument": "a", "evidence": "e"}]}]},
                  "items[0].thread[0].side: missing"),
                 ("stance вне списка", {"items": [{**item, "thread": [{"kind": "examine", "side": "codex", "stance": PAYLOAD, "severity": "major",
                                                                   "argument": "a", "evidence": "e"}]}]},
                  "items[0].thread[0].stance: '<img src=x onerror=alert(1)>' is not one of"),
                 ("неизвестный kind", {"items": [{**item, "thread": [{"kind": "note"}]}]}, "items[0].thread[0].kind: 'note' is not one of"),
                 ("executor числом", {"meta": {**final["meta"], "agents": [{**agent, "executor": 1}]}}, "meta.agents[0].executor: expected string"),
                 ("raised строкой", {"meta": {**final["meta"], "agents": [{**agent, "raised": PAYLOAD}]}}, "meta.agents[0].raised: expected integer"),
                 ("модель списком", {"meta": {**final["meta"], "agents": [{**agent, "actual_model": ["x"]}]}},
                  "meta.agents[0].actual_model: expected string or null"),
                 ("raw строкой", {"meta": {**final["meta"], "raw": "7"}}, "meta.raw: expected integer"),
                 ("pipeline вне списка", {"meta": {**final["meta"], "pipeline": PAYLOAD}}, "meta.pipeline: '<img src=x onerror=alert(1)>' is not one of"),
                 ("без questions", {"questions": None}, "final.json.questions: expected array"),
                 ("вопрос без line", {"questions": [{k: v for k, v in final["questions"][0].items() if k != "line"}]}, "questions[0].line: missing")]
        for label, change, needle in cases:
            with self.subTest(case=label):
                write_json(self.debate / "final.json", {**final, **change})
                res = run("debate_report", self.debate, self.out, "--title", TITLE, "--subject", "x")
                self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
                self.assertIn(needle, last_json_line(res.stdout)["error"])
                self.assertFalse(self.out.exists())


@unittest.skipUnless(shutil.which("node"), "страницу выполняет node")
class PageTest(ReportCase):
    """The page run under node with a stub document: what it writes into innerHTML."""

    def sinks(self) -> list[str]:
        harness = self.root / "harness.js"
        harness.write_text(HARNESS)
        res = subprocess.run(["node", str(harness), str(self.out)], capture_output=True, text=True)
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
        found = re.search(r'<div class="num">(\d+)</div><div class="lbl">Чинить</div>', "".join(self.sinks()))
        self.assertIsNotNone(found)
        return int(found.group(1)) if found else -1

    def test_дефект_до_изменения_не_в_чинить(self) -> None:
        self.report()
        before = self.to_fix()
        final = read_json(self.debate / "final.json")
        item = next(i for i in final["items"] if i["outcome"] in ("consensus", "confirmed") and i["final_severity"] != "none")
        item["status"] = "pre_existing"
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
        final = read_json(self.debate / "final.json")
        entry = lambda kind, **more: {"kind": kind, "side": PAYLOAD, **{k: PAYLOAD for k in ("argument", "evidence", "answer", "verdict", "note")}, **more}  # noqa: E731
        item = {**final["items"][0], "id": PAYLOAD, "file": f"{PAYLOAD}/x.py", "title": PAYLOAD, "body": PAYLOAD, "fix": PAYLOAD,
                "status": PAYLOAD, "sources": [PAYLOAD], "vendors": [PAYLOAD], "outcome": "disputed", "moderated": True,
                "thread": [entry("finding", sources=[PAYLOAD]), entry("pipeline", status=PAYLOAD),
                           entry("examine", stance="real", severity="major"), entry("respond", position="amend", severity="minor"),
                           entry("answer"), entry("moderator", severity="major")]}
        question = {**final["questions"][0], "id": f"q{PAYLOAD}", "title": PAYLOAD, "body": PAYLOAD, "file": PAYLOAD,
                    "thread": [entry("finding", sources=[PAYLOAD]), entry("answer")]}
        agent = {**final["meta"]["agents"][0], "name": PAYLOAD, "executor": PAYLOAD, "actual_model": PAYLOAD, "requested_model": PAYLOAD}
        meta = {**final["meta"], "agents": [agent], "round_dir": PAYLOAD, "debate_dir": PAYLOAD, "verify_model": PAYLOAD}
        write_json(self.debate / "final.json", {"items": [item], "questions": [question], "meta": meta})
        res = run("debate_report", self.debate, self.out, "--title", TITLE, "--subject", PAYLOAD)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        sinks = self.sinks()
        html = "".join(sinks)
        self.assertNotIn("<img", html)
        self.assertNotIn("onerror=alert(1)>", html)
        self.assertGreater(html.count("&lt;img src=x onerror=alert(1)&gt;"), 30)
        self.assertIn('class="side-&lt;img src=x onerror=alert(1)&gt;"', html)
        self.assertIn('class="msg &lt;img src=x onerror=alert(1)&gt;"', html)


class ErrorsTest(unittest.TestCase):
    def test_без_final_json_ошибка(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "report.html"
            res = run("debate_report", tmp, out, "--title", TITLE, "--subject", "x")
            self.assertEqual(res.returncode, 1)
            self.assertIn("no final.json", last_json_line(res.stdout)["error"])
            self.assertFalse(out.exists())

    def test_битый_final_json_ошибка(self) -> None:
        for label, text in (("не JSON", "{"), ("без meta", '{"items": []}'), ("не объект", "[]"),
                            ("пустой пункт", '{"meta": {"agents": []}, "items": [{}]}'),
                            ("meta без agents", '{"meta": {}, "items": []}'),
                            ("пункт без thread", '{"meta": {"agents": []}, "items": [{"id": "a", "outcome": "side", "final_severity": "minor"}]}'),
                            ("finding без side и sources", '{"meta": {"agents": []}, "items": [{"id": "a", "outcome": "side", '
                                                          '"final_severity": "minor", "thread": [{"kind": "finding"}]}]}'),
                            ("вопрос с finding без sources", '{"meta": {"agents": []}, "items": [], "questions": [{"id": "q", '
                                                            '"thread": [{"kind": "finding", "side": "codex"}]}]}')):
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                (Path(tmp) / "final.json").write_text(text)
                out = Path(tmp) / "report.html"
                res = run("debate_report", tmp, out, "--title", TITLE, "--subject", "x")
                self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
                self.assertIn("not the output of debate_outcome.py", last_json_line(res.stdout)["error"])
                self.assertFalse(out.exists())

    def test_без_обязательных_флагов_выход_2(self) -> None:
        res = run("debate_report", "debate", "out.html")
        self.assertEqual(res.returncode, 2)
        self.assertIn("--title", res.stderr)


class WordsTest(unittest.TestCase):
    def test_склонение_агентов(self) -> None:
        cases = [(1, "агент"), (2, "агента"), (4, "агента"), (5, "агентов"), (11, "агентов"), (12, "агентов"),
                 (21, "агент"), (22, "агента"), (0, "агентов")]
        for n, word in cases:
            with self.subTest(n=n):
                self.assertEqual(debate_report.agents_word(n), word)


if __name__ == "__main__":
    unittest.main()
