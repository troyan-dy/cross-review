from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from typing import NamedTuple

from support import FIXTURES, last_json_line, read_json, run, single, write_json

import debate_round2


def verdict(fid: str, stance: str, severity: str) -> dict:
    return {"id": fid, "stance": stance, "severity": severity, "argument": "Синтетический довод.", "evidence": "app/x.py:1"}


class Case(NamedTuple):
    """Вердикт проверяющего и ждём ли мы по нему возражение автору."""

    examiner: str
    fid: str
    stance: str
    severity: str
    objected: bool

    def as_verdict(self) -> dict:
        return verdict(self.fid, self.stance, self.severity)


CASES = (
    Case("codex", "bugs-claude-2", "real", "major", False),
    Case("codex", "arch-claude-1", "real", "major", True),
    Case("codex", "arch-claude-2", "not_real", "none", True),
    Case("codex", "ghost-1", "not_real", "none", False),
    Case("claude", "bugs-codex-2", "real_but_different", "critical", True),
    Case("claude", "arch-codex-1", "not_worth_fixing", "minor", True),
    Case("claude", "bugs-codex-3", "real", "major", False),
    Case("claude", "arch-codex-2", "real", "major", True),
)
AUTHOR = types.MappingProxyType({"codex": "claude", "claude": "codex"})


class DebateRound2Test(unittest.TestCase):
    def setUp(self) -> None:
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.debate = Path(workspace.name) / "debate"
        res = run("debate_build", FIXTURES / "run-basic", self.debate)
        self.assertEqual(res.returncode, 0, res.stderr)

    def write_r1(self, examiner: str, verdicts: list[dict]) -> None:
        write_json(self.debate / f"{examiner}-r1.json", {"verdicts": verdicts, "open_question_answers": []})

    def round2(self) -> dict:
        res = run("debate_round2", self.debate)
        self.assertEqual(res.returncode, 0, res.stderr)
        return last_json_line(res.stdout)

    def objected(self, author: str) -> set[str]:
        entries = read_json(self.debate / f"for-{author}-r2.json")
        return {entry["finding"]["id"] for entry in entries}

    def write_cases(self) -> None:
        for examiner in ("claude", "codex"):
            own = [case for case in CASES if case.examiner == examiner]
            self.write_r1(examiner, [case.as_verdict() for case in own])

    def test_возражение_это_не_real_или_другая_severity(self) -> None:
        self.write_cases()
        counts = self.round2()
        for case in CASES:
            with self.subTest(case=case):
                objected = self.objected(AUTHOR[case.examiner])
                self.assertIs(case.fid in objected, case.objected)
        self.assertEqual(counts["for_claude_r2"], 2)
        self.assertEqual(counts["for_codex_r2"], 3)

    def test_возражение_несёт_находку_и_вердикт(self) -> None:
        self.write_r1("codex", [verdict("arch-claude-2", "not_real", "none")])
        self.round2()
        entry = single(read_json(self.debate / "for-claude-r2.json"))
        self.assertEqual(entry["finding"]["id"], "arch-claude-2")
        self.assertEqual(entry["finding"]["status"], "pre_existing")
        self.assertEqual(entry["objection"]["stance"], "not_real")

    def test_пункты_обоих_вендоров_в_раунд2_не_идут(self) -> None:
        for examiner in ("claude", "codex"):
            self.write_r1(examiner, [verdict("arch-claude-4", "not_real", "none")])
        self.round2()
        self.assertEqual(self.objected("claude"), set())
        self.assertEqual(self.objected("codex"), set())

    def test_без_r1_пустые_списки(self) -> None:
        counts = self.round2()
        self.assertEqual(read_json(self.debate / "for-claude-r2.json"), [])
        self.assertEqual(read_json(self.debate / "for-codex-r2.json"), [])
        self.assertEqual(counts, {
            "for_claude_r2": 0,
            "codex_r1_present": False,
            "for_codex_r2": 0,
            "claude_r1_present": False,
        })

    def test_ответила_одна_сторона(self) -> None:
        self.write_r1("codex", [verdict("arch-claude-2", "not_real", "none")])
        counts = self.round2()
        self.assertTrue(counts["codex_r1_present"])
        self.assertFalse(counts["claude_r1_present"])
        self.assertEqual(self.objected("claude"), {"arch-claude-2"})
        self.assertEqual(read_json(self.debate / "for-codex-r2.json"), [])

    def test_ответ_не_по_схеме_ошибка(self) -> None:
        cases = (
            ("пустой вердикт", {"verdicts": [{}], "open_question_answers": []}, "codex-r1.json.verdicts[0].id: missing"),
            ("чужой stance", {"verdicts": [verdict("bugs-claude-2", "maybe", "major")], "open_question_answers": []}, "'maybe'"),
            ("не JSON", "{не json", "not JSON"),
        )
        for label, answer, needle in cases:
            with self.subTest(case=label):
                self.assert_bad_answer(answer, needle)

    def assert_bad_answer(self, answer: object, needle: str) -> None:
        path = self.debate / "codex-r1.json"
        if isinstance(answer, str):
            path.write_text(answer)
        else:
            write_json(path, answer)
        res = run("debate_round2", self.debate)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn(needle, last_json_line(res.stdout)["error"])

    def test_без_items_json_ошибка_json(self) -> None:
        cases = (
            ("нет файла", None, "items.json"),
            ("не JSON", "{", "items.json"),
            ("не список пунктов", '{"a": 1}', "debate_build.py"),
        )
        for label, text, needle in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as workspace:
                self.assert_bad_items(Path(workspace) / "debate", text, needle)

    def assert_bad_items(self, debate: Path, text: str | None, needle: str) -> None:
        debate.mkdir()
        if text is not None:
            (debate / "items.json").write_text(text)
        res = run("debate_round2", debate)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertEqual(res.stderr, "")
        self.assertIn(needle, last_json_line(res.stdout)["error"])
        self.assertFalse((debate / "for-claude-r2.json").exists())

    def test_без_аргументов_выход2(self) -> None:
        res = run("debate_round2")
        self.assertEqual(res.returncode, 2)
        self.assertIn("usage:", res.stderr)

    def test_возражения_без_запуска_скрипта(self) -> None:
        cards = {"a-1": {"id": "a-1", "severity": "major", "vendors": ["claude"]}}
        cases = (
            ("согласие на той же severity", verdict("a-1", "real", "major"), 0),
            ("другая severity", verdict("a-1", "real", "minor"), 1),
            ("не real", verdict("a-1", "real_but_different", "major"), 1),
            ("неизвестный id", verdict("b-1", "not_real", "none"), 0),
        )
        for name, given, expected in cases:
            with self.subTest(name):
                self.assertEqual(len(debate_round2.objections(cards, [given])), expected)


if __name__ == "__main__":
    unittest.main()
