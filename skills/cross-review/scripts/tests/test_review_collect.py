from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import SKILL, composed_run, last_json_line, read_json, run

import review_collect
from review_collect import check_schema, extract_json

AGENTS = ("bugs-claude", "bugs-codex", "arch-claude", "arch-codex")
SCHEMA = read_json(SKILL / "assets" / "config" / "schemas" / "finder.json")


def finding(title: str = "Повтор записи теряет данные", **over: object) -> dict:
    """A finding the finder schema accepts; keyword arguments override its fields."""
    return {"file": "app/store.py", "line": 42, "end_line": 44, "severity": "major", "confidence": 85,
            "title": title, "body": "Описание.", "fix": "Исправление.", "lenses": ["bugs"], **over}


class RunCase(unittest.TestCase):
    """A composed run of the demo task in a fresh repository; tests write the agents' answers."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = composed_run(Path(tmp.name).resolve())
        self.agents = self.run_dir / "agents"

    def answer(self, agent: str, data: object = None, text: str | None = None) -> None:
        (self.agents / f"{agent}.json").write_text(text if text is not None else json.dumps(data, ensure_ascii=False))

    def answer_all(self, findings: list | None = None) -> None:
        for agent in AGENTS:
            self.answer(agent, {"findings": findings if findings is not None else []})

    def collect(self) -> dict:
        res = run("review_collect", self.run_dir)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return last_json_line(res.stdout)

    def found(self) -> dict:
        return read_json(self.run_dir / "stages" / "1-found.json")

    def events(self) -> list[dict]:
        return [json.loads(line) for line in (self.run_dir / "events.jsonl").read_text().splitlines()]

    def agent_stat(self, name: str) -> dict:
        return next(a for a in self.found()["sources"]["agents"] if a["name"] == name)


class CollectTest(RunCase):
    """Healthy answers become stages/1-found.json in the run format."""

    def test_валидные_ответы_дают_1_found(self) -> None:
        self.answer_all()
        self.answer("bugs-claude", {"findings": [finding(), finding("Гонка при закрытии", severity="critical")]})
        self.answer("arch-codex", {"findings": [finding("Слой знает о транспорте", lenses=["architecture"])]})
        out = self.collect()
        self.assertEqual((out["expected"], out["reported"], out["findings"], out["rejected"]), (4, 4, 3, 0))
        self.assertEqual(out["degraded"], [])
        self.assertTrue(out["two_sides"])
        found = self.found()
        self.assertEqual([f["id"] for f in found["findings"]], ["bugs-claude-1", "bugs-claude-2", "arch-codex-1"])
        first = found["findings"][0]
        self.assertEqual(list(first), ["id", "file", "line", "end_line", "severity", "confidence", "title", "body", "fix",
                                       "sources", "lenses", "verdict"])
        self.assertEqual((first["sources"], first["verdict"]), (["bugs-claude"], ""))
        self.assertEqual(found["sources"]["expected"], 4)
        self.assertEqual(found["sources"]["degraded"], [])
        self.assertEqual(self.agent_stat("bugs-claude")["raised"], 2)
        self.assertEqual({k: self.agent_stat("arch-codex")[k] for k in ("executor", "requested_model", "effort", "lenses")},
                         {"executor": "codex", "requested_model": "gpt-6.1-sol", "effort": "high",
                          "lenses": ["architecture", "quality"]})
        manifest = read_json(self.run_dir / "manifest.json")
        self.assertEqual(found["scope"], {"task": "demo", "run": "01-initial", "scope_path": manifest["scope_path"]})
        self.assertEqual(found["stats"]["started_at"], manifest["started_at"])
        self.assertEqual((found["open_questions"], found["pre_existing"], found["immaterial"]), ([], [], []))

    def test_пустой_ответ_здоровый_агент(self) -> None:
        self.answer_all()
        out = self.collect()
        self.assertEqual((out["reported"], out["findings"], out["degraded"]), (4, 0, []))
        self.assertFalse(self.agent_stat("bugs-codex")["degraded"])
        self.assertEqual(self.agent_stat("bugs-codex")["raised"], 0)

    def test_ответ_в_прозе_и_блоке_кода(self) -> None:
        self.answer_all()
        body = json.dumps({"findings": [finding()]}, ensure_ascii=False, indent=2)
        for agent, text in (("bugs-claude", f"Готово, вот находки:\n\n```json\n{body}\n```\n"),
                            ("arch-claude", f"Итог ревью {body} — конец.")):
            self.answer(agent, text=text)
        out = self.collect()
        self.assertEqual((out["reported"], out["findings"]), (4, 2))
        self.assertEqual([f["id"] for f in self.found()["findings"]], ["bugs-claude-1", "arch-claude-1"])

    def test_умолчания_и_лишние_ключи(self) -> None:
        self.answer_all()
        raw = finding(evidence="лишнее поле")
        del raw["fix"], raw["end_line"]
        self.answer("bugs-codex", {"findings": [raw]})
        self.collect()
        [kept] = self.found()["findings"]
        self.assertEqual((kept["fix"], kept["end_line"]), ("", 0))
        self.assertNotIn("evidence", kept)

    def test_линзы_вне_агента_отброшены(self) -> None:
        self.answer_all()
        self.answer("bugs-claude", {"findings": [finding(lenses=["bugs", "made-up"]), finding("Другая", lenses=["made-up"])]})
        self.collect()
        self.assertEqual([f["lenses"] for f in self.found()["findings"]], [["bugs"], ["bugs", "impl"]])

    def test_находка_вне_схемы_отбрасывается(self) -> None:
        self.answer_all()
        bad = [finding(severity="blocker"), finding(confidence=150), finding(line="42"), {"title": "без полей"}, "строка"]
        self.answer("bugs-claude", {"findings": [finding("Первая"), *bad, finding("Последняя")]})
        out = self.collect()
        self.assertEqual((out["findings"], out["rejected"]), (2, len(bad)))
        self.assertEqual([(f["id"], f["title"]) for f in self.found()["findings"]],
                         [("bugs-claude-1", "Первая"), ("bugs-claude-2", "Последняя")])
        self.assertFalse(self.agent_stat("bugs-claude")["degraded"])
        rejected = [e for e in self.events() if e["kind"] == "finding_rejected"]
        self.assertEqual(len(rejected), len(bad))
        self.assertIn("'blocker' is not one of", rejected[0]["text"])
        self.assertIn("finding.confidence: 150 is above 100", rejected[1]["text"])
        self.assertIn("finding.line: expected integer", rejected[2]["text"])
        self.assertIn("finding.file: missing", rejected[3]["text"])

    def test_модель_codex_из_лога(self) -> None:
        self.answer_all()
        (self.agents / "bugs-codex.log").write_text("OpenAI Codex\n--------\nmodel: gpt-6.1-sol-2026\nsession id: x\n")
        self.collect()
        self.assertEqual(self.agent_stat("bugs-codex")["actual_model"], "gpt-6.1-sol-2026")
        self.assertEqual(self.agent_stat("bugs-claude")["actual_model"], "")

    def test_события_в_events_jsonl(self) -> None:
        self.answer_all([finding()])
        (self.agents / "arch-codex.json").unlink()
        self.collect()
        kinds = [(e["kind"], e.get("agent")) for e in self.events()]
        self.assertEqual(kinds[0], ("stage", None))
        self.assertIn(("agent_done", "bugs-claude"), kinds)
        self.assertIn(("agent_degraded", "arch-codex"), kinds)
        done = next(e for e in self.events() if e["kind"] == "agent_done")
        self.assertEqual(done["text"], "1 findings")

    def test_повторный_сбор_перезаписывает_1_found(self) -> None:
        self.answer_all()
        (self.agents / "bugs-codex.json").unlink()
        self.assertEqual(self.collect()["reported"], 3)
        self.answer("bugs-codex", {"findings": [finding()]})
        self.assertEqual(self.collect()["reported"], 4)
        self.assertEqual(self.found()["sources"]["degraded"], [])
        kinds = [(e["kind"], e.get("agent")) for e in self.events()]
        self.assertEqual(kinds.count(("stage", None)), 1)
        self.assertNotIn(("agent_degraded", "bugs-codex"), kinds)
        self.assertEqual(kinds.count(("agent_done", "bugs-codex")), 1)


class DegradedTest(RunCase):
    """An agent without a valid answer, with a non-zero exit or stopped by a limit is degraded."""

    def test_битый_и_отсутствующий_ответ(self) -> None:
        cases = [
            ("нет файла", None, "no answer: bugs-codex.json is missing"),
            ("не JSON", "не json вовсе", "answer is not JSON"),
            ("пустой файл", "", "answer is not JSON"),
            ("массив", "[]", "expected an object with a findings array"),
            ("без findings", '{"items": []}', "expected an object with a findings array"),
            ("findings не массив", '{"findings": {}}', "expected an object with a findings array"),
        ]
        for name, text, reason in cases:
            with self.subTest(case=name):
                self.answer_all()
                if text is None:
                    (self.agents / "bugs-codex.json").unlink()
                else:
                    self.answer("bugs-codex", text=text)
                out = self.collect()
                self.assertEqual(out["reported"], 3)
                [degraded] = out["degraded"]
                self.assertEqual(degraded["agent"], "bugs-codex")
                self.assertIn(reason, degraded["reason"])
                self.assertTrue(self.agent_stat("bugs-codex")["degraded"])
                self.assertEqual(self.found()["sources"]["degraded"], ["bugs-codex"])

    def test_ненулевой_exit_деградирует(self) -> None:
        self.answer_all([finding()])
        (self.agents / "bugs-codex.exit").write_text("1\n")
        out = self.collect()
        self.assertEqual(out["degraded"], [{"agent": "bugs-codex", "reason": "exited 1"}])
        self.assertNotIn("bugs-codex-1", [f["id"] for f in self.found()["findings"]])
        self.assertEqual(self.agent_stat("bugs-codex")["raised"], 0)

    def test_нулевой_exit_не_мешает(self) -> None:
        self.answer_all()
        (self.agents / "arch-codex.exit").write_text("0\n")
        self.assertEqual(self.collect()["degraded"], [])

    def test_лимит_в_логе_неудачного_запуска(self) -> None:
        self.answer_all()
        (self.agents / "arch-codex.json").unlink()
        (self.agents / "arch-codex.exit").write_text("1\n")
        (self.agents / "arch-codex.log").write_text("model: gpt-6.1-sol\n" + "работа\n" * 500
                                                    + "ERROR: You've hit your usage limit. Try again later.\n")
        [degraded] = self.collect()["degraded"]
        self.assertIn("exited 1; no answer", degraded["reason"])
        self.assertIn("usage limit", degraded["reason"])

    def test_лимит_в_теле_удачного_лога_не_деградирует(self) -> None:
        self.answer_all([finding("Rate limit exceeded не обрабатывается")])
        (self.agents / "bugs-codex.exit").write_text("0\n")
        (self.agents / "bugs-codex.log").write_text("model: gpt-6.1-sol\ncodex\nrate limit exceeded, 429 too many requests\n")
        self.assertEqual(self.collect()["degraded"], [])

    def test_лимит_в_начале_длинного_лога_не_называется(self) -> None:
        self.answer_all()
        (self.agents / "arch-codex.json").unlink()
        (self.agents / "arch-codex.log").write_text("читаю rate_limiter.py: rate limit exceeded\n" + "x\n" * 5000)
        [degraded] = self.collect()["degraded"]
        self.assertNotIn("usage limit", degraded["reason"])

    def test_одна_сторона_целиком_деградировала(self) -> None:
        self.answer_all()
        for agent in ("bugs-codex", "arch-codex"):
            (self.agents / f"{agent}.json").unlink()
        out = self.collect()
        self.assertEqual(out["sides"], {"claude": {"expected": 2, "reported": 2}, "codex": {"expected": 2, "reported": 0}})
        self.assertFalse(out["two_sides"])

    def test_все_деградировали(self) -> None:
        out = self.collect()
        self.assertEqual((out["reported"], len(out["degraded"]), out["two_sides"]), (0, 4, False))
        self.assertEqual(self.found()["findings"], [])

    def test_без_manifest_выход_1(self) -> None:
        (self.run_dir / "manifest.json").unlink()
        res = run("review_collect", self.run_dir)
        self.assertEqual(res.returncode, 1)
        self.assertIn("no readable manifest.json", last_json_line(res.stdout)["error"])

    def test_manifest_без_агентов_выход_1(self) -> None:
        for manifest in ('{"task": "demo"}', "[]", ""):
            with self.subTest(manifest=manifest):
                (self.run_dir / "manifest.json").write_text(manifest)
                res = run("review_collect", self.run_dir)
                self.assertEqual(res.returncode, 1, res.stderr)
                self.assertEqual(res.stderr, "")
                self.assertIn("manifest.json", last_json_line(res.stdout)["error"])


class SchemaTest(unittest.TestCase):
    """check_schema and extract_json on their own."""

    def test_проверка_по_схеме(self) -> None:
        item = SCHEMA["properties"]["findings"]["items"]
        cases = [
            ("валидная", finding(), []),
            ("bool вместо числа", finding(line=True), ["finding.line: expected integer"]),
            ("ниже минимума", finding(line=-1), ["finding.line: -1 is below 0"]),
            ("выше максимума", finding(confidence=101), ["finding.confidence: 101 is above 100"]),
            ("вне enum", finding(severity="info"), ["finding.severity: 'info' is not one of critical, major, minor"]),
            ("неизвестный ключ", finding(extra=1), ["finding.extra: unknown key"]),
            ("элемент массива", finding(lenses=["bugs", 3]), ["finding.lenses[1]: expected string"]),
            ("не объект", [], ["finding: expected object"]),
        ]
        for name, value, want in cases:
            with self.subTest(case=name):
                self.assertEqual(check_schema(value, item, "finding"), want)
        missing = finding()
        del missing["title"]
        self.assertEqual(check_schema(missing, item, "finding"), ["finding.title: missing"])
        nullable = {"type": ["string", "null"]}
        self.assertEqual(check_schema(None, nullable, "x"), [])
        self.assertEqual(check_schema("a", nullable, "x"), [])
        self.assertEqual(check_schema(1, nullable, "x"), ["x: expected string or null"])
        self.assertEqual(check_schema(None, {"type": "null"}, "x"), [])
        self.assertEqual(check_schema({"findings": [finding()]}, SCHEMA), [])

    def test_извлечение_json(self) -> None:
        cases = [
            ("чистый", '{"findings": []}'),
            ("блок json", 'Ответ:\n```json\n{"findings": []}\n```'),
            ("блок без языка", '```\n{"findings": []}\n```\nвсё'),
            ("в прозе", 'Вот: {"findings": []} конец'),
        ]
        for name, text in cases:
            with self.subTest(case=name):
                self.assertEqual(extract_json(text), {"findings": []})
        with self.assertRaises(ValueError):
            extract_json("ничего похожего")

    def test_маркеры_лимита_как_у_codex_run(self) -> None:
        script = (review_collect.Path(review_collect.__file__).parent / "codex_run.sh").read_text()
        for marker in review_collect.LIMIT_MARKERS:
            with self.subTest(marker=marker):
                self.assertIn(f'"{marker}"', script)


if __name__ == "__main__":
    unittest.main()
