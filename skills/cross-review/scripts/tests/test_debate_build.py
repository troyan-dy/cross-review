from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import FIXTURES, copy_fixture, last_json_line, read_json, run, write_json

from crossreview import debate_inputs

STATUSES = {
    "bugs-claude-1": "confirmed",
    "bugs-claude-2": "confirmed",
    "arch-claude-1": "refined",
    "bugs-codex-2": "confirmed",
    "arch-claude-4": "immaterial",
    "arch-codex-1": "immaterial",
    "arch-claude-2": "pre_existing",
    "bugs-codex-3": "rejected_by_verify",
    "arch-codex-2": "dropped_by_synthesis",
}


class DebateBuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.debate = self.root / "debate"

    def build(self, run_dir: Path) -> dict:
        res = run("debate_build", run_dir, self.debate)
        self.assertEqual(res.returncode, 0, res.stderr)
        return last_json_line(res.stdout)

    def items(self) -> dict[str, dict]:
        return {i["id"]: i for i in read_json(self.debate / "items.json")}

    def ids(self, name: str, key: str = "findings") -> set[str]:
        return {i["id"] for i in read_json(self.debate / name)[key]}

    def test_раскладка_на_консенсус_и_находки_одной_стороны(self) -> None:
        summary = self.build(FIXTURES / "run-basic")
        self.assertEqual([i["id"] for i in read_json(self.debate / "consensus.json")], ["bugs-claude-1"])
        self.assertEqual(self.ids("for-codex.json"), {"bugs-claude-2", "arch-claude-1", "arch-claude-2", "arch-claude-4"})
        self.assertEqual(self.ids("for-claude.json"),
                         {"bugs-codex-2", "bugs-codex-3", "arch-codex-1", "arch-codex-2", "arch-claude-4"})
        self.assertEqual(summary["roster"], {"claude": ["arch-claude", "bugs-claude"], "codex": ["arch-codex", "bugs-codex"]})
        self.assertEqual((summary["consensus"], summary["for_codex"], summary["for_claude"]), (1, 4, 5))
        self.assertTrue(summary["verify_ran"])

    def test_открытый_вопрос_идёт_другой_стороне(self) -> None:
        self.build(FIXTURES / "run-basic")
        self.assertEqual(self.ids("for-codex.json", "open_questions"), {"arch-claude-3"})
        self.assertEqual(self.ids("for-claude.json", "open_questions"), set())
        self.assertNotIn("arch-claude-3", self.items())

    def test_статусы_пунктов(self) -> None:
        self.build(FIXTURES / "run-basic")
        items = self.items()
        self.assertEqual(set(items), set(STATUSES))
        for fid, status in STATUSES.items():
            with self.subTest(id=fid):
                self.assertEqual(items[fid]["status"], status)

    def test_пункт_обеих_сторон_отложенный_конвейером_смотрят_обе(self) -> None:
        self.build(FIXTURES / "run-basic")
        self.assertEqual(self.items()["arch-claude-4"]["vendors"], ["claude", "codex"])
        self.assertIn("arch-claude-4", self.ids("for-codex.json"))
        self.assertIn("arch-claude-4", self.ids("for-claude.json"))

    def test_слитые_при_синтезе_находки_не_попадают_в_пункты(self) -> None:
        self.build(FIXTURES / "run-basic")
        for fid in ("bugs-codex-1", "bugs-claude-3", "arch-codex-3"):
            with self.subTest(id=fid):
                self.assertNotIn(fid, self.items())

    def test_отброшенные_берутся_из_events(self) -> None:
        run_dir = copy_fixture("run-basic", self.root / "run")
        found = {f["id"]: f for f in read_json(run_dir / "stages" / "1-found.json")["findings"]}
        # эвристика сочла бы bugs-claude-3 слитой, а arch-codex-2 — отброшенной; events.jsonl главнее
        dropped = {"kind": "dropped", "agent": "synthesis", "findings": [found["bugs-claude-3"]]}
        (run_dir / "events.jsonl").write_text('{"kind": "stage", "stage": "synthesis"}\nне JSON\n' + json.dumps(dropped) + "\n")
        summary = self.build(run_dir)
        self.assertTrue(summary["dropped_from_events"])
        self.assertEqual(self.items()["bugs-claude-3"]["status"], "dropped_by_synthesis")
        self.assertNotIn("arch-codex-2", self.items())

    def test_без_events_отброшенные_находит_эвристика(self) -> None:
        run_dir = copy_fixture("run-basic", self.root / "run")
        (run_dir / "events.jsonl").unlink()
        summary = self.build(run_dir)
        self.assertFalse(summary["dropped_from_events"])
        items = self.items()
        self.assertEqual({fid: i["status"] for fid, i in items.items()}, STATUSES)
        # открытый вопрос синтеза не возвращается отброшенной находкой
        self.assertNotIn("arch-claude-3", items)

    def test_без_верификации_находки_не_проверены(self) -> None:
        run_dir = copy_fixture("run-basic", self.root / "run")
        (run_dir / "stages" / "3-verified.json").unlink()
        synth = read_json(run_dir / "stages" / "2-synthesized.json")
        for f in synth["findings"]:
            f["verdict"] = "unverified"
        write_json(run_dir / "findings.json", synth)
        summary = self.build(run_dir)
        self.assertFalse(summary["verify_ran"])
        items = self.items()
        for f in synth["findings"]:
            with self.subTest(id=f["id"]):
                self.assertEqual(items[f["id"]]["status"], "unverified")
        self.assertEqual(items["arch-claude-2"]["status"], "pre_existing")
        self.assertNotIn("rejected_by_verify", {i["status"] for i in items.values()})
        # без проверки консенсус ещё не подтверждён — его смотрят обе стороны
        self.assertEqual(read_json(self.debate / "consensus.json"), [])
        self.assertIn("bugs-claude-1", self.ids("for-claude.json"))

    def test_один_вендор_в_ростере_выход_1(self) -> None:
        res = run("debate_build", FIXTURES / "run-one-vendor", self.debate)
        self.assertEqual(res.returncode, 1)
        out = last_json_line(res.stdout)
        self.assertIn("codex", out["error"])
        self.assertEqual(out["roster"], {"claude": ["arch-claude", "bugs-claude"], "codex": []})
        self.assertFalse(self.debate.exists())

    def test_каталог_спора_по_умолчанию_в_прогоне(self) -> None:
        run_dir = copy_fixture("run-basic", self.root / "run")
        res = run("debate_build", run_dir)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(last_json_line(res.stdout)["debate_dir"], str((run_dir / "debate").resolve()))
        for name in ("items.json", "consensus.json", "for-claude.json", "for-codex.json"):
            with self.subTest(file=name):
                self.assertTrue((run_dir / "debate" / name).is_file())
        self.assertFalse(self.debate.exists())

    def test_нет_файлов_прогона_ошибка_json(self) -> None:
        run_dir = copy_fixture("run-basic", self.root / "run")
        cases = [("нет каталога", self.root / "missing", "1-found.json"),
                 ("нет findings.json", run_dir, "findings.json"),
                 ("findings.json не JSON", run_dir, "not JSON"),
                 ("findings.json список", run_dir, "expected an object")]
        for label, round_dir, needle in cases:
            with self.subTest(case=label):
                if label == "нет findings.json":
                    (run_dir / "findings.json").unlink()
                elif label == "findings.json не JSON":
                    (run_dir / "findings.json").write_text("{")
                elif label == "findings.json список":
                    write_json(run_dir / "findings.json", [])
                res = run("debate_build", round_dir, self.debate)
                self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
                self.assertEqual(res.stderr, "")
                error = last_json_line(res.stdout)["error"]
                self.assertIn(needle, error)
                self.assertIn("review_verify.py apply", error)
                self.assertFalse(self.debate.exists())

    def test_без_аргументов_выход_2(self) -> None:
        res = run("debate_build")
        self.assertEqual(res.returncode, 2)
        self.assertIn("usage:", res.stderr)

    def test_тег_заголовка(self) -> None:
        cases = [("[DB-1] Потеря записи", "[DB-1]"), ("Без тега", ""), ("[незакрытый тег", ""), ("", "")]
        for title, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(debate_inputs.tag(title), expected)

    def test_покрытие_сырой_находки(self) -> None:
        kept = [{"id": "a-1", "file": "x.py", "line": 10, "title": "[T-1] заголовок", "sources": ["a"]}]
        cases = [
            ("major не отбрасывается синтезом", {"id": "b-1", "severity": "major", "file": "y.py", "sources": ["b"]}, True),
            ("тот же тег", {"id": "b-1", "severity": "minor", "title": "[T-1] другое", "file": "y.py", "sources": ["b"]}, True),
            ("тот же агент рядом", {"id": "a-2", "severity": "minor", "file": "x.py", "line": 15, "sources": ["a"]}, True),
            ("тот же агент далеко", {"id": "a-2", "severity": "minor", "file": "x.py", "line": 16, "sources": ["a"]}, False),
            ("другой агент рядом", {"id": "b-1", "severity": "minor", "file": "x.py", "line": 10, "sources": ["b"]}, False),
        ]
        for name, raw, expected in cases:
            with self.subTest(name):
                self.assertIs(debate_inputs.covered(raw, kept), expected)

    def test_нет_events_значит_нет_списка_отброшенных(self) -> None:
        self.assertIsNone(debate_inputs.dropped_from_events(self.root / "events.jsonl"))


if __name__ == "__main__":
    unittest.main()
