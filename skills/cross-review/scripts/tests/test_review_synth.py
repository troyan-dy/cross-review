from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import composed_run, last_json_line, read_json, run, write_json

from review_synth import combine, droppable

AGENTS = ("bugs-claude", "bugs-codex", "arch-claude", "arch-codex")


def finding(title: str, **over: object) -> dict:
    """A finder finding; keyword arguments override its fields."""
    return {"file": "app/store.py", "line": 10, "end_line": 0, "severity": "minor", "confidence": 60,
            "title": title, "body": "Описание.", "fix": "Исправление.", "lenses": ["bugs"], **over}


# the round most tests merge: a pair both vendors found, weak and strong singletons, a question, an old defect
ROUND = {
    "bugs-claude": [finding("Гонка при закрытии", severity="major", confidence=70),
                    finding("Опечатка в логе", line=50),
                    finding("Повтор без паузы", line=80, confidence=90)],
    "bugs-codex": [finding("Гонка при закрытии соединения", line=11, confidence=85, lenses=["impl"])],
    "arch-claude": [finding("Зачем второй пул?", file="app/pool.py", confidence=75, lenses=["architecture"])],
    "arch-codex": [finding("Старый обход кэша", file="app/cache.py", confidence=70, lenses=["quality"])],
}


def out(ids: list[str], title: str = "Итог", **over: object) -> dict:
    """One output of a synthesis answer; its severity and confidence are what the rules must override."""
    return {"merged_ids": ids, "file": "app/store.py", "line": 10, "end_line": 0, "severity": "minor",
            "confidence": 1, "title": title, "body": "Объединённое описание.", "fix": "", **over}


def answer(findings: list | None = None, questions: list | None = None, old: list | None = None) -> dict:
    return {"findings": findings or [], "open_questions": questions or [], "pre_existing": old or []}


# merges the pair, keeps a weak singleton the drop rule removes, splits out the question and the old defect
FULL = answer([out(["bugs-claude-1", "bugs-codex-1"], "Гонка при закрытии"),
               out(["bugs-claude-2"], "Опечатка в логе", line=50),
               out(["bugs-claude-3"], "Повтор без паузы", line=80)],
              [out(["arch-claude-1"], "Зачем второй пул?", file="app/pool.py")],
              [out(["arch-codex-1"], "Старый обход кэша", file="app/cache.py")])


class SynthCase(unittest.TestCase):
    """A composed run of the demo task; tests write the finders' answers and the synthesis answer."""

    config: dict | None = None

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = composed_run(Path(tmp.name).resolve(), self.config)
        self.repo = self.run_dir.parents[3]

    def collect(self, answers: dict | None = None, missing: tuple = ()) -> None:
        """Write every finder's answer, leave out the agents in missing, and collect the run."""
        answers = ROUND if answers is None else answers
        for agent in AGENTS:
            if agent not in missing:
                write_json(self.run_dir / "agents" / f"{agent}.json", {"findings": answers.get(agent, [])})
        res = run("review_collect", self.run_dir)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def synth(self, *args: object, code: int = 0) -> dict:
        res = run("review_synth", *args)
        self.assertEqual(res.returncode, code, res.stdout + res.stderr)
        return last_json_line(res.stdout)

    def prepare(self, code: int = 0) -> dict:
        return self.synth("prepare", self.run_dir, code=code)

    def apply(self, data: object = None, text: str | None = None, code: int = 0) -> dict:
        path = self.run_dir / "agents" / "synthesis.json"
        path.write_text(text if text is not None else json.dumps(data, ensure_ascii=False))
        return self.synth("apply", self.run_dir, code=code)

    def synthesized(self) -> dict:
        return read_json(self.run_dir / "stages" / "2-synthesized.json")

    def events(self, kind: str) -> list[dict]:
        lines = (self.run_dir / "events.jsonl").read_text().splitlines()
        return [e for e in map(json.loads, lines) if e["kind"] == kind]

    def prompt(self) -> str:
        return (self.run_dir / "prompts" / "stages" / "synthesis.md").read_text()


class PrepareTest(SynthCase):
    """prepare renders the synthesis prompt from 1-found.json and prints the launch plan."""

    def test_промт_и_план_запуска(self) -> None:
        self.collect()
        plan = self.prepare()
        self.assertFalse(plan["skipped"])
        self.assertEqual({k: plan[k] for k in ("stage", "vendor", "alias", "effort", "findings", "degraded")},
                         {"stage": "synthesis", "vendor": "claude", "alias": "opus", "effort": "high", "findings": 6,
                          "degraded": []})
        self.assertEqual(plan["prompt"], str(self.run_dir / "prompts" / "stages" / "synthesis.md"))
        self.assertEqual(plan["output"], str(self.run_dir / "agents" / "synthesis.json"))
        text = self.prompt()
        self.assertIn("4 sources ran, 4 reported.\n- bugs-claude (lenses: bugs, impl) reported 3 findings: "
                      "bugs-claude-1, bugs-claude-2, bugs-claude-3\n", text)
        self.assertIn("- arch-codex (lenses: architecture, quality) reported 1 findings: arch-codex-1", text)
        self.assertIn('"id": "bugs-codex-1"', text)
        self.assertIn("Return ONLY a JSON object matching the schema below", text)
        self.assertIn('"merged_ids"', text)
        self.assertNotIn("{{", text)
        self.assertNotIn("DEGRADED", text)
        self.assertEqual(len(self.events("stage")), 2)
        sources = {p["path"]: p["layer"] for p in read_json(self.run_dir / "manifest.json")["prompts"]}
        self.assertEqual((sources["prompts/synthesis.md"], sources["schemas/synthesis.json"]), ("plugin", "plugin"))

    def test_деградировавший_раунд_в_промте(self) -> None:
        self.collect(missing=("bugs-codex",))
        plan = self.prepare()
        self.assertEqual(plan["degraded"], ["bugs-codex"])
        text = self.prompt()
        self.assertIn("4 sources ran, 3 reported. This run is DEGRADED: bugs-codex reported nothing.", text)
        self.assertIn("- bugs-codex (lenses: bugs, impl) DEGRADED, reported nothing", text)

    def test_промт_синтеза_из_репозитория(self) -> None:
        self.collect({"bugs-claude": [finding("Одна")]})
        own = self.repo / ".cross-review" / "prompts" / "synthesis.md"
        own.parent.mkdir(parents=True)
        own.write_text("---\ndescription: свой\n---\nСвой синтез.\n\n{{SOURCES}}\n\n{{FINDINGS}}\n")
        self.prepare()
        text = self.prompt()
        self.assertTrue(text.startswith("Свой синтез.\n\n4 sources ran"))
        self.assertIn("reported no findings", text)
        sources = {p["path"]: p["layer"] for p in read_json(self.run_dir / "manifest.json")["prompts"]}
        self.assertEqual(sources["prompts/synthesis.md"], "repo")

    def test_неизвестный_плейсхолдер_ошибка(self) -> None:
        self.collect()
        own = self.repo / ".cross-review" / "prompts" / "synthesis.md"
        own.parent.mkdir(parents=True)
        own.write_text("{{SOURCES}} {{SCOPE}}\n")
        err = self.prepare(code=1)["error"]
        self.assertIn("prompts/synthesis.md", err)
        self.assertIn("{{SCOPE}}", err)

    def test_без_находок_стадия_пропущена(self) -> None:
        self.collect({})
        plan = self.prepare()
        self.assertEqual((plan["skipped"], plan["reason"], plan["findings"]), (True, "no finding to merge", 0))
        stages = self.run_dir / "stages"
        self.assertEqual((stages / "2-synthesized.json").read_bytes(), (stages / "1-found.json").read_bytes())
        self.assertFalse((self.run_dir / "prompts" / "stages" / "synthesis.md").exists())

    def test_без_сбора_ошибка(self) -> None:
        self.assertIn("review_collect.py", self.prepare(code=1)["error"])


class StageOffTest(SynthCase):
    """stages.synthesis: false passes 1-found.json through unchanged."""

    config = {"stages": {"synthesis": False}}

    def test_стадия_выключена(self) -> None:
        self.collect()
        plan = self.prepare()
        self.assertEqual((plan["skipped"], plan["reason"]), (True, "stages.synthesis is off for this run"))
        stages = self.run_dir / "stages"
        self.assertEqual((stages / "2-synthesized.json").read_bytes(), (stages / "1-found.json").read_bytes())
        self.assertIn("off for this run", self.apply(FULL, code=1)["error"])


class ApplyTest(SynthCase):
    """apply keeps the model's grouping and wording and applies the synthesis rules to the rest."""

    def setUp(self) -> None:
        super().setUp()
        self.collect()
        self.prepare()

    def test_правила_поверх_ответа(self) -> None:
        res = self.apply(FULL)
        self.assertEqual({k: res[k] for k in ("inputs", "findings", "open_questions", "pre_existing", "merged",
                                              "dropped", "restored", "degraded")},
                         {"inputs": 6, "findings": 2, "open_questions": 1, "pre_existing": 1, "merged": 1,
                          "dropped": ["bugs-claude-2"], "restored": [], "degraded": []})
        rep = self.synthesized()
        pair, strong = rep["findings"]
        self.assertEqual({k: pair[k] for k in ("id", "merged_ids", "sources", "lenses", "severity", "confidence", "title")},
                         {"id": "bugs-claude-1", "merged_ids": ["bugs-claude-1", "bugs-codex-1"],
                          "sources": ["bugs-claude", "bugs-codex"], "lenses": ["bugs", "impl"], "severity": "major",
                          "confidence": 95, "title": "Гонка при закрытии"})
        self.assertEqual((strong["id"], strong["confidence"], strong["line"]), ("bugs-claude-3", 90, 80))
        [question] = rep["open_questions"]
        self.assertEqual((question["id"], question["confidence"], question["file"]), ("arch-claude-1", 75, "app/pool.py"))
        self.assertEqual(rep["pre_existing"][0]["id"], "arch-codex-1")
        self.assertEqual(rep["immaterial"], [])
        found = read_json(self.run_dir / "stages" / "1-found.json")
        self.assertEqual((rep["scope"], rep["sources"]), (found["scope"], found["sources"]))
        self.assertEqual([s["name"] for s in rep["stats"]["stages"]], ["find", "synthesis"])
        self.assertEqual({k: rep["stats"]["stages"][1][k] for k in ("executor", "model", "effort")},
                         {"executor": "claude", "model": "opus", "effort": "high"})
        [dropped] = self.events("dropped")
        self.assertEqual((dropped["agent"], dropped["text"]), ("synthesis", "1 findings dropped"))
        self.assertEqual([f["id"] for f in dropped["findings"]], ["bugs-claude-2"])
        self.assertEqual(self.events("restored"), [])
        self.assertEqual(self.events("agent_done")[-1]["text"], "2 findings")

    def test_ответ_по_явному_пути(self) -> None:
        path = self.run_dir.parent / "answer.json"
        path.write_text(json.dumps(FULL, ensure_ascii=False))
        self.assertEqual(self.synth("apply", self.run_dir, path)["findings"], 2)

    def test_пропущенные_находки_отсеяны_или_восстановлены(self) -> None:
        res = self.apply(answer([out(["bugs-claude-1", "bugs-codex-1"])]))
        self.assertEqual(res["dropped"], ["arch-claude-1", "arch-codex-1", "bugs-claude-2"])
        self.assertEqual(res["restored"], ["bugs-claude-3"])
        restored = self.synthesized()["findings"][1]
        self.assertEqual({k: restored[k] for k in ("id", "title", "line", "confidence", "merged_ids")},
                         {"id": "bugs-claude-3", "title": "Повтор без паузы", "line": 80, "confidence": 90,
                          "merged_ids": ["bugs-claude-3"]})
        [event] = self.events("restored")
        self.assertEqual([f["id"] for f in event["findings"]], ["bugs-claude-3"])
        self.assertEqual([f["title"] for f in self.events("dropped")[0]["findings"]],
                         ["Зачем второй пул?", "Старый обход кэша", "Опечатка в логе"])

    def test_ответ_в_прозе_лишние_ключи_и_повтор_id(self) -> None:
        item = out(["bugs-claude-1", "bugs-claude-1", "bugs-codex-1"], sources=["выдумка"], verdict="confirmed")
        del item["fix"], item["end_line"]
        body = json.dumps(answer([item]), ensure_ascii=False, indent=2)
        self.apply(text=f"Объединил находки.\n\n```json\n{body}\n```\n")
        merged = self.synthesized()["findings"][0]
        self.assertEqual((merged["merged_ids"], merged["sources"]), (["bugs-claude-1", "bugs-codex-1"],
                                                                     ["bugs-claude", "bugs-codex"]))
        self.assertEqual((merged["fix"], merged["end_line"], merged["verdict"]), ("", 0, ""))

    def test_повторный_apply_заменяет_события(self) -> None:
        self.apply(answer([out(["bugs-claude-1", "bugs-codex-1"])]))
        res = self.apply(FULL)
        self.assertEqual(res["dropped"], ["bugs-claude-2"])
        self.assertEqual(len(self.events("dropped")), 1)
        self.assertEqual(self.events("restored"), [])
        self.assertEqual(len(self.events("agent_done")), 4 + 1)
        self.assertEqual([s["name"] for s in self.synthesized()["stats"]["stages"]], ["find", "synthesis"])

    def test_ошибки_ответа(self) -> None:
        cases = [
            ("неизвестный id", answer([out(["nope-1"])]), "unknown finding id 'nope-1'"),
            ("id в двух списках", answer([out(["bugs-claude-1"])], [out(["bugs-claude-1"])]), "more than one output"),
            ("без id", answer([out([])]), "merged no input ids"),
            ("severity вне схемы", answer([out(["bugs-claude-1"], severity="blocker")]), "does not match the synthesis schema"),
            ("нет findings", {"open_questions": []}, "findings array"),
            ("список не массив", {"findings": [], "pre_existing": {}}, "pre_existing: expected array"),
        ]
        for name, data, error in cases:
            with self.subTest(name):
                self.assertIn(error, self.apply(data, code=1)["error"])
        with self.subTest("не JSON"):
            self.assertIn("no JSON object", self.apply(text="синтез не удался", code=1)["error"])
        with self.subTest("нет файла ответа"):
            (self.run_dir / "agents" / "synthesis.json").unlink()
            self.assertIn("no synthesis answer", self.synth("apply", self.run_dir, code=1)["error"])
        self.assertFalse((self.run_dir / "stages" / "2-synthesized.json").exists())

    def test_после_верификации_повтор_запрещён(self) -> None:
        (self.run_dir / "stages" / "3-verified.json").write_text("{}")
        self.assertIn("verify already ran", self.apply(FULL, code=1)["error"])
        self.assertIn("verify already ran", self.prepare(code=1)["error"])


class DegradedTest(SynthCase):
    """A degraded round drops nothing: a would-be drop stays, an input the answer left out comes back."""

    def test_деградировавший_раунд_ничего_не_отсеивает(self) -> None:
        self.collect(missing=("bugs-codex",))
        self.prepare()
        res = self.apply(answer([out(["bugs-claude-2"], "Опечатка в логе", line=50)]))
        self.assertEqual(res["dropped"], [])
        self.assertEqual(res["degraded"], ["bugs-codex"])
        self.assertEqual(res["restored"], ["bugs-claude-1", "bugs-claude-3", "arch-claude-1", "arch-codex-1"])
        ids = [f["id"] for f in self.synthesized()["findings"]]
        self.assertEqual(ids, ["bugs-claude-1", "bugs-claude-2", "bugs-claude-3", "arch-claude-1", "arch-codex-1"])
        self.assertEqual(self.events("dropped"), [])


def raw(source: str, confidence: int, severity: str = "minor", lenses: tuple = ("bugs",)) -> dict:
    return {"id": f"{source}-{confidence}", "severity": severity, "confidence": confidence, "sources": [source],
            "lenses": list(lenses)}


ITEM = {"file": "a.py", "line": 1, "title": "Т", "body": "Б"}


class RulesTest(unittest.TestCase):
    """The deterministic rules, called directly."""

    def test_формула_confidence(self) -> None:
        cases = [
            ("один источник", [raw("a", 70)], True, 70),
            ("потолок 99", [raw("a", 100)], True, 99),
            ("два источника", [raw("a", 85), raw("b", 60)], True, 95),
            ("два источника до потолка", [raw("a", 95), raw("b", 90)], True, 99),
            ("три источника", [raw("a", 50), raw("b", 40), raw("c", 30)], True, 70),
            ("один агент под двумя линзами", [raw("a", 70), raw("a", 60, lenses=("impl",))], True, 70),
            ("вопрос без надбавки", [raw("a", 70), raw("b", 60)], False, 70),
        ]
        for name, inputs, boost, expected in cases:
            with self.subTest(name):
                self.assertEqual(combine(ITEM, inputs, boost)["confidence"], expected)

    def test_максимальная_severity(self) -> None:
        cases = [
            (["minor"], "minor"),
            (["minor", "major"], "major"),
            (["major", "critical", "minor"], "critical"),
        ]
        for severities, expected in cases:
            with self.subTest(severities=severities):
                inputs = [raw(f"s{i}", 50, s) for i, s in enumerate(severities)]
                self.assertEqual(combine(ITEM, inputs, True)["severity"], expected)

    def test_атрибуция_по_merged_ids(self) -> None:
        merged = combine(ITEM, [raw("a", 70), raw("b", 60, lenses=("impl", "bugs")), raw("a", 50)], True)
        self.assertEqual((merged["id"], merged["merged_ids"]), ("a-70", ["a-70", "b-60", "a-50"]))
        self.assertEqual((merged["sources"], merged["lenses"]), (["a", "b"], ["bugs", "impl"]))

    def test_правило_отсева(self) -> None:
        cases = [
            ("слабая одиночная minor", ["a"], 79, "minor", True),
            ("порог 80", ["a"], 80, "minor", False),
            ("одиночная major", ["a"], 50, "major", False),
            ("одиночная critical", ["a"], 50, "critical", False),
            ("два источника", ["a", "b"], 60, "minor", False),
        ]
        for name, sources, confidence, severity, expected in cases:
            with self.subTest(name):
                self.assertEqual(droppable({"sources": sources, "confidence": confidence, "severity": severity}), expected)


if __name__ == "__main__":
    unittest.main()
