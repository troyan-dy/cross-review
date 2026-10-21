from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from support import FIXTURES, last_json_line, read_json, run, write_json

import debate_round2


def verdict(fid: str, stance: str, severity: str) -> dict:
    return {"id": fid, "stance": stance, "severity": severity, "argument": "Синтетический довод.", "evidence": "app/x.py:1"}


# (проверяющий, id, stance, severity, ждём возражение)
CASES = [
    ("codex", "bugs-claude-2", "real", "major", False),
    ("codex", "arch-claude-1", "real", "major", True),
    ("codex", "arch-claude-2", "not_real", "none", True),
    ("codex", "ghost-1", "not_real", "none", False),
    ("claude", "bugs-codex-2", "real_but_different", "critical", True),
    ("claude", "arch-codex-1", "not_worth_fixing", "minor", True),
    ("claude", "bugs-codex-3", "real", "major", False),
    ("claude", "arch-codex-2", "real", "major", True),
]
AUTHOR = {"codex": "claude", "claude": "codex"}


class DebateRound2Test(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.debate = Path(tmp.name) / "debate"
        res = run("debate_build", FIXTURES / "run-basic", self.debate)
        self.assertEqual(res.returncode, 0, res.stderr)

    def write_r1(self, examiner: str, verdicts: list[dict]) -> None:
        write_json(self.debate / f"{examiner}-r1.json", {"verdicts": verdicts, "open_question_answers": []})

    def round2(self) -> dict:
        res = run("debate_round2", self.debate)
        self.assertEqual(res.returncode, 0, res.stderr)
        return last_json_line(res.stdout)

    def objected(self, author: str) -> set[str]:
        return {e["finding"]["id"] for e in read_json(self.debate / f"for-{author}-r2.json")}

    def test_возражение_это_не_real_или_другая_severity(self) -> None:
        for examiner in ("claude", "codex"):
            self.write_r1(examiner, [verdict(fid, stance, sev) for ex, fid, stance, sev, _ in CASES if ex == examiner])
        counts = self.round2()
        for examiner, fid, stance, sev, expected in CASES:
            with self.subTest(examiner=examiner, id=fid, stance=stance, severity=sev):
                self.assertIs(fid in self.objected(AUTHOR[examiner]), expected)
        self.assertEqual(counts["for_claude_r2"], 2)
        self.assertEqual(counts["for_codex_r2"], 3)

    def test_возражение_несёт_находку_и_вердикт(self) -> None:
        self.write_r1("codex", [verdict("arch-claude-2", "not_real", "none")])
        self.round2()
        [entry] = read_json(self.debate / "for-claude-r2.json")
        self.assertEqual(entry["finding"]["id"], "arch-claude-2")
        self.assertEqual(entry["finding"]["status"], "pre_existing")
        self.assertEqual(entry["objection"]["stance"], "not_real")

    def test_пункты_обоих_вендоров_в_раунд_2_не_идут(self) -> None:
        for examiner in ("claude", "codex"):
            self.write_r1(examiner, [verdict("arch-claude-4", "not_real", "none")])
        self.round2()
        self.assertEqual(self.objected("claude"), set())
        self.assertEqual(self.objected("codex"), set())

    def test_без_r1_пустые_списки(self) -> None:
        counts = self.round2()
        self.assertEqual(read_json(self.debate / "for-claude-r2.json"), [])
        self.assertEqual(read_json(self.debate / "for-codex-r2.json"), [])
        self.assertEqual(counts, {"for_claude_r2": 0, "codex_r1_present": False, "for_codex_r2": 0, "claude_r1_present": False})

    def test_ответила_одна_сторона(self) -> None:
        self.write_r1("codex", [verdict("arch-claude-2", "not_real", "none")])
        counts = self.round2()
        self.assertTrue(counts["codex_r1_present"])
        self.assertFalse(counts["claude_r1_present"])
        self.assertEqual(self.objected("claude"), {"arch-claude-2"})
        self.assertEqual(read_json(self.debate / "for-codex-r2.json"), [])

    def test_ответ_не_по_схеме_ошибка(self) -> None:
        cases = [
            ("пустой вердикт", {"verdicts": [{}], "open_question_answers": []}, "codex-r1.json.verdicts[0].id: missing"),
            ("чужой stance", {"verdicts": [verdict("bugs-claude-2", "maybe", "major")], "open_question_answers": []}, "'maybe'"),
            ("не JSON", "{не json", "not JSON"),
        ]
        for label, answer, needle in cases:
            with self.subTest(case=label):
                path = self.debate / "codex-r1.json"
                if isinstance(answer, str):
                    path.write_text(answer)
                else:
                    write_json(path, answer)
                res = run("debate_round2", self.debate)
                self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
                self.assertIn(needle, last_json_line(res.stdout)["error"])

    def test_без_items_json_ошибка_json(self) -> None:
        cases = [("нет файла", None, "items.json"), ("не JSON", "{", "items.json"), ("не список пунктов", '{"a": 1}', "debate_build.py")]
        for label, content, needle in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                d = Path(tmp) / "debate"
                d.mkdir()
                if content is not None:
                    (d / "items.json").write_text(content)
                res = run("debate_round2", d)
                self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
                self.assertEqual(res.stderr, "")
                self.assertIn(needle, last_json_line(res.stdout)["error"])
                self.assertFalse((d / "for-claude-r2.json").exists())

    def test_без_аргументов_выход_2(self) -> None:
        res = run("debate_round2")
        self.assertEqual(res.returncode, 2)
        self.assertIn("usage:", res.stderr)

    def test_возражения_без_запуска_скрипта(self) -> None:
        items = {"a-1": {"id": "a-1", "severity": "major", "vendors": ["claude"]}}
        cases = [
            ("согласие на той же severity", verdict("a-1", "real", "major"), 0),
            ("другая severity", verdict("a-1", "real", "minor"), 1),
            ("не real", verdict("a-1", "real_but_different", "major"), 1),
            ("неизвестный id", verdict("b-1", "not_real", "none"), 0),
        ]
        for name, v, expected in cases:
            with self.subTest(name):
                self.assertEqual(len(debate_round2.objections(items, [v])), expected)


if __name__ == "__main__":
    unittest.main()
