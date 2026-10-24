from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from support import copy_fixture, last_json_line, pick, read_json, run, single, write_json

BOTH_NOTES = "Модератор Claude: Claude смотрел.\n\nМодератор Codex: Codex смотрел."
SIDE_NOTES = "Модератор Claude: Claude: да.\n\nМодератор Codex: Codex: да."
DISPUTED = "bugs-codex-2"


def decision(fid: str, verdict: str, severity: str, note: str = "Проверено по коду.") -> dict:
    return {"id": fid, "verdict": verdict, "severity": severity, "note": note}


def extra(eid: str, note: str, severity: str = "major") -> dict:
    return {
        "id": eid,
        "file": "app/api.py",
        "line": 70,
        "severity": severity,
        "title": "Утечка соединения",
        "body": "Соединение не закрывается.",
        "fix": "Закрыть в finally.",
        "note": note,
    }


class ModeratorsCase(unittest.TestCase):
    def setUp(self) -> None:
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name).resolve()
        self.debate = self.root / "debate"
        self.debate.mkdir()

    def moderators(
        self,
        claude: list,
        codex: list,
        extra_claude: list | None = None,
        extra_codex: list | None = None,
    ) -> dict:
        write_json(self.debate / "moderator-claude.json", claude)
        write_json(self.debate / "extra-claude.json", extra_claude or [])
        write_json(self.debate / "moderator-codex.json", {"decisions": codex, "extra": extra_codex or []})
        res = run("debate_moderators", self.debate)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return last_json_line(res.stdout)

    def merged(self) -> dict:
        return {entry["id"]: entry for entry in read_json(self.debate / "moderator.json")}


class MergeTest(ModeratorsCase):
    """Two independent moderators: agreement decides, disagreement stays a dispute."""

    def test_согласие_это_решение(self) -> None:
        # (случай, решение Claude, решение Codex, ждём severity, ждём verdict)
        cases = (
            ("одинаково", ("прав Codex", "minor"), ("прав Codex", "minor"), "minor", "прав Codex"),
            ("severity разошлась — берём строже", ("прав Claude", "minor"), ("прав Claude", "major"), "major",
             "прав Claude; серьёзность разошлась (minor / major), берём строже"),
            ("разные слова, оба за дефект", ("прав Claude", "critical"), ("оба частично правы: …", "major"), "critical",
             "оба модератора: прав Claude / оба частично правы: …; серьёзность разошлась (critical / major), берём строже"),
            ("оба снимают", ("снять", "none"), ("снять", "none"), "none", "снять"),
        )
        for label, by_claude, by_codex, *expected in cases:
            with self.subTest(case=label):
                self.assert_agreed(by_claude, by_codex, tuple(expected))

    def assert_agreed(self, by_claude: tuple, by_codex: tuple, expected: tuple) -> None:
        claude = [decision("x-1", *by_claude, note="Claude смотрел.")]
        out = self.moderators(claude, [decision("x-1", *by_codex, note="Codex смотрел.")])
        outcome = pick(out, "decided", "split", "missing")
        self.assertEqual(outcome, (["x-1"], [], []))
        merged = self.merged()["x-1"]
        self.assertEqual(pick(merged, "severity", "verdict"), expected)
        self.assertEqual(merged["note"], BOTH_NOTES)
        self.assertNotIn("split", merged)

    def test_расхождение_остаётся_спором(self) -> None:
        claude = [decision("x-1", "снять", "none")]
        out = self.moderators(claude, [decision("x-1", "прав Codex", "major")])
        self.assertEqual(pick(out, "decided", "split"), ([], ["x-1"]))
        merged = self.merged()["x-1"]
        self.assertIs(merged["split"], True)
        self.assertEqual(merged["severity"], "major")
        self.assertEqual(merged["verdict"], "модераторы разошлись: Claude — снять, Codex — прав Codex; решает человек")

    def test_ответил_один_модератор(self) -> None:
        claude = [decision("x-1", "прав Claude", "minor")]
        out = self.moderators(claude, [decision("x-2", "снять", "none")])
        self.assertEqual(pick(out, "decided", "missing"), ([], ["x-1", "x-2"]))
        merged = self.merged()
        self.assertEqual(merged["x-1"]["verdict"], "ответил только один модератор (Claude): прав Claude; решает человек")
        self.assertEqual(merged["x-2"]["verdict"], "ответил только один модератор (Codex): снять; решает человек")
        self.assertEqual(merged["x-2"]["severity"], "none")
        self.assertIs(merged["x-1"]["missing"], True)

    def test_попутная_находка_только_когда_подтвердили_оба(self) -> None:
        by_claude = [extra("codex-side-1", "Claude: да."), extra("claude-side-1", "Claude: да.")]
        out = self.moderators([], [], by_claude, [extra("codex-side-1", "Codex: да.")])
        self.assertEqual(pick(out, "extra", "extra_rejected_by_one"), (["codex-side-1"], ["claude-side-1"]))
        confirmed = single(read_json(self.debate / "extra.json"))
        self.assertEqual(confirmed["note"], SIDE_NOTES)
        self.assertEqual(pick(confirmed, "sources", "vendors"), (["codex"], ["codex"]))

    def test_попутная_находка_берёт_строже_серьёзность(self) -> None:
        mild = extra("codex-side-1", "Claude: да.", "minor")
        self.moderators([], [], [mild], [extra("codex-side-1", "Codex: да.", "critical")])
        confirmed = single(read_json(self.debate / "extra.json"))
        self.assertEqual(confirmed["severity"], "critical")
        self.assertEqual(confirmed["note"], f"{SIDE_NOTES}\n\nСерьёзность разошлась (minor / critical), берём строже.")

    def test_без_ответов_пустые_файлы(self) -> None:
        res = run("debate_moderators", self.debate)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(last_json_line(res.stdout), {
            "decided": [],
            "split": [],
            "missing": [],
            "extra": [],
            "extra_rejected_by_one": [],
        })
        self.assertEqual(read_json(self.debate / "moderator.json"), [])
        self.assertEqual(read_json(self.debate / "extra.json"), [])

    def test_ответ_модератора_не_по_схеме_ошибка(self) -> None:
        cases = (
            ("решение Claude без severity", "moderator-claude.json", [{"id": DISPUTED, "verdict": "снять", "note": "."}],
             "moderator-claude.json[0].severity: missing"),
            ("Codex без decisions", "moderator-codex.json", {"extra": []}, "moderator-codex.json.decisions: missing"),
            ("попутная Claude без file", "extra-claude.json", [{"id": "claude-side-1"}], "extra-claude.json[0].file: missing"),
        )
        for label, name, answer, needle in cases:
            with self.subTest(case=label):
                self.assert_bad_answer(name, answer, needle)

    def assert_bad_answer(self, name: str, answer: object, needle: str) -> None:
        for stale in self.debate.iterdir():
            stale.unlink()
        write_json(self.debate / name, answer)
        res = run("debate_moderators", self.debate)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn(needle, last_json_line(res.stdout)["error"])
        self.assertFalse((self.debate / "moderator.json").exists())


class ThroughOutcomeTest(ModeratorsCase):
    """The merged decisions as debate_outcome.py folds them: a split keeps the dispute at the pipeline severity."""

    def setUp(self) -> None:
        super().setUp()
        self.run_dir = copy_fixture("run-basic", self.root / "run")
        res = run("debate_build", self.run_dir, self.debate)
        self.assertEqual(res.returncode, 0, res.stderr)
        rejected = {"id": DISPUTED, "stance": "not_real", "severity": "none", "argument": "Нет.", "evidence": "app/worker.py:77"}
        write_json(self.debate / "claude-r1.json", {"verdicts": [rejected], "open_question_answers": []})
        defended = {
            "id": DISPUTED,
            "position": "defend",
            "severity": "critical",
            "argument": "Да.",
            "evidence": "app/worker.py:80",
        }
        write_json(self.debate / "codex-r2.json", {"responses": [defended], "side_findings": []})

    def card(self) -> dict:
        res = run("debate_outcome", self.run_dir, self.debate)
        self.assertEqual(res.returncode, 0, res.stderr)
        cards = read_json(self.debate / "final.json")["items"]
        return next(card for card in cards if card["id"] == DISPUTED)

    def test_согласие_модераторов_решает_спор(self) -> None:
        claude = [decision(DISPUTED, "прав Codex", "major")]
        self.moderators(claude, [decision(DISPUTED, "прав Codex", "critical")])
        card = self.card()
        self.assertEqual(pick(card, "outcome", "moderated", "final_severity"), ("disputed", True, "critical"))
        self.assertEqual(card["severity_shift"]["by"], "none")

    def test_согласие_на_понижение(self) -> None:
        claude = [decision(DISPUTED, "оба частично правы: …", "minor")]
        self.moderators(claude, [decision(DISPUTED, "оба частично правы: …", "major")])
        card = self.card()
        self.assertEqual(card["final_severity"], "major")
        self.assertEqual(pick(card["severity_shift"], "direction", "by"), ("lowered", "moderator"))

    def test_расхождение_модераторов_ждёт_человека(self) -> None:
        claude = [decision(DISPUTED, "снять", "none")]
        self.moderators(claude, [decision(DISPUTED, "прав Codex", "critical")])
        card = self.card()
        self.assert_open(card)
        self.assertIn("решает человек", card["thread"][-1]["verdict"])

    def test_ответ_одного_модератора_не_решает_спор(self) -> None:
        self.moderators([decision(DISPUTED, "снять", "none")], [])
        card = self.card()
        self.assert_open(card)
        self.assertIn("только один модератор", card["thread"][-1]["verdict"])

    def assert_open(self, card: dict) -> None:
        expected = ("disputed", False, True, "critical")
        self.assertEqual(pick(card, "outcome", "moderated", "moderators_split", "final_severity"), expected)


if __name__ == "__main__":
    unittest.main()
