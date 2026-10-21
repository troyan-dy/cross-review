from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from support import copy_fixture, last_json_line, read_json, run, write_json


def decision(fid: str, verdict: str, severity: str, note: str = "Проверено по коду.") -> dict:
    return {"id": fid, "verdict": verdict, "severity": severity, "note": note}


def extra(eid: str, note: str) -> dict:
    return {"id": eid, "file": "app/api.py", "line": 70, "severity": "major", "title": "Утечка соединения",
            "body": "Соединение не закрывается.", "fix": "Закрыть в finally.", "note": note}


class ModeratorsCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.debate = self.root / "debate"
        self.debate.mkdir()

    def moderators(self, claude: list, codex: list, extra_claude: list | None = None, extra_codex: list | None = None) -> dict:
        write_json(self.debate / "moderator-claude.json", claude)
        write_json(self.debate / "extra-claude.json", extra_claude or [])
        write_json(self.debate / "moderator-codex.json", {"decisions": codex, "extra": extra_codex or []})
        res = run("debate_moderators", self.debate)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return last_json_line(res.stdout)

    def merged(self) -> dict:
        return {m["id"]: m for m in read_json(self.debate / "moderator.json")}


class MergeTest(ModeratorsCase):
    """Two independent moderators: agreement decides, disagreement stays a dispute."""

    def test_согласие_это_решение(self) -> None:
        # (случай, решение Claude, решение Codex, ждём severity, ждём verdict)
        cases = [
            ("одинаково", ("прав Codex", "minor"), ("прав Codex", "minor"), "minor", "прав Codex"),
            ("severity разошлась — берём строже", ("прав Claude", "minor"), ("прав Claude", "major"), "major",
             "прав Claude; серьёзность разошлась (minor / major), берём строже"),
            ("разные слова, оба за дефект", ("прав Claude", "critical"), ("оба частично правы: …", "major"), "critical",
             "оба модератора: прав Claude / оба частично правы: …; серьёзность разошлась (critical / major), берём строже"),
            ("оба снимают", ("снять", "none"), ("снять", "none"), "none", "снять"),
        ]
        for label, a, b, severity, verdict in cases:
            with self.subTest(case=label):
                out = self.moderators([decision("x-1", *a, note="Claude смотрел.")], [decision("x-1", *b, note="Codex смотрел.")])
                self.assertEqual((out["decided"], out["split"], out["missing"]), (["x-1"], [], []))
                m = self.merged()["x-1"]
                self.assertEqual((m["severity"], m["verdict"]), (severity, verdict))
                self.assertEqual(m["note"], "Модератор Claude: Claude смотрел.\n\nМодератор Codex: Codex смотрел.")
                self.assertNotIn("split", m)

    def test_расхождение_остаётся_спором(self) -> None:
        out = self.moderators([decision("x-1", "снять", "none")], [decision("x-1", "прав Codex", "major")])
        self.assertEqual((out["decided"], out["split"]), ([], ["x-1"]))
        m = self.merged()["x-1"]
        self.assertIs(m["split"], True)
        self.assertEqual(m["severity"], "major")
        self.assertEqual(m["verdict"], "модераторы разошлись: Claude — снять, Codex — прав Codex; решает человек")

    def test_ответил_один_модератор(self) -> None:
        out = self.moderators([decision("x-1", "прав Claude", "minor")], [decision("x-2", "снять", "none")])
        self.assertEqual((out["decided"], out["missing"]), ([], ["x-1", "x-2"]))
        merged = self.merged()
        self.assertEqual(merged["x-1"]["verdict"], "ответил только один модератор (Claude): прав Claude; решает человек")
        self.assertEqual(merged["x-2"]["verdict"], "ответил только один модератор (Codex): снять; решает человек")
        self.assertEqual(merged["x-2"]["severity"], "none")
        self.assertIs(merged["x-1"]["missing"], True)

    def test_попутная_находка_только_когда_подтвердили_оба(self) -> None:
        out = self.moderators([], [], [extra("codex-side-1", "Claude: да."), extra("claude-side-1", "Claude: да.")],
                              [extra("codex-side-1", "Codex: да.")])
        self.assertEqual((out["extra"], out["extra_rejected_by_one"]), (["codex-side-1"], ["claude-side-1"]))
        [e] = read_json(self.debate / "extra.json")
        self.assertEqual(e["note"], "Модератор Claude: Claude: да.\n\nМодератор Codex: Codex: да.")
        self.assertEqual((e["sources"], e["vendors"]), (["codex"], ["codex"]))

    def test_попутная_находка_берёт_строже_серьёзность(self) -> None:
        mild = extra("codex-side-1", "Claude: да.") | {"severity": "minor"}
        self.moderators([], [], [mild], [extra("codex-side-1", "Codex: да.") | {"severity": "critical"}])
        [e] = read_json(self.debate / "extra.json")
        self.assertEqual(e["severity"], "critical")
        self.assertEqual(e["note"], "Модератор Claude: Claude: да.\n\nМодератор Codex: Codex: да.\n\n"
                                    "Серьёзность разошлась (minor / critical), берём строже.")

    def test_без_ответов_пустые_файлы(self) -> None:
        res = run("debate_moderators", self.debate)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(last_json_line(res.stdout), {"decided": [], "split": [], "missing": [], "extra": [],
                                                      "extra_rejected_by_one": []})
        self.assertEqual((read_json(self.debate / "moderator.json"), read_json(self.debate / "extra.json")), ([], []))


    def test_ответ_модератора_не_по_схеме_ошибка(self) -> None:
        cases = [
            ("решение Claude без severity", "moderator-claude.json", [{"id": "bugs-codex-2", "verdict": "снять", "note": "."}],
             "moderator-claude.json[0].severity: missing"),
            ("Codex без decisions", "moderator-codex.json", {"extra": []}, "moderator-codex.json.decisions: missing"),
            ("попутная Claude без file", "extra-claude.json", [{"id": "claude-side-1"}], "extra-claude.json[0].file: missing"),
        ]
        for label, name, answer, needle in cases:
            with self.subTest(case=label):
                for f in self.debate.iterdir():
                    f.unlink()
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
        write_json(self.debate / "claude-r1.json", {"verdicts": [
            {"id": "bugs-codex-2", "stance": "not_real", "severity": "none", "argument": "Нет.", "evidence": "app/worker.py:77"}],
            "open_question_answers": []})
        write_json(self.debate / "codex-r2.json", {"responses": [
            {"id": "bugs-codex-2", "position": "defend", "severity": "critical", "argument": "Да.", "evidence": "app/worker.py:80"}],
            "side_findings": []})

    def item(self) -> dict:
        res = run("debate_outcome", self.run_dir, self.debate)
        self.assertEqual(res.returncode, 0, res.stderr)
        return next(i for i in read_json(self.debate / "final.json")["items"] if i["id"] == "bugs-codex-2")

    def test_согласие_модераторов_решает_спор(self) -> None:
        self.moderators([decision("bugs-codex-2", "прав Codex", "major")], [decision("bugs-codex-2", "прав Codex", "critical")])
        item = self.item()
        self.assertEqual((item["outcome"], item["moderated"], item["final_severity"]), ("disputed", True, "critical"))
        self.assertEqual(item["severity_shift"]["by"], "none")

    def test_согласие_на_понижение(self) -> None:
        self.moderators([decision("bugs-codex-2", "оба частично правы: …", "minor")],
                        [decision("bugs-codex-2", "оба частично правы: …", "major")])
        item = self.item()
        self.assertEqual((item["final_severity"], item["severity_shift"]["direction"], item["severity_shift"]["by"]),
                         ("major", "lowered", "moderator"))

    def test_расхождение_модераторов_ждёт_человека(self) -> None:
        self.moderators([decision("bugs-codex-2", "снять", "none")], [decision("bugs-codex-2", "прав Codex", "critical")])
        item = self.item()
        self.assertEqual((item["outcome"], item["moderated"], item["moderators_split"], item["final_severity"]),
                         ("disputed", False, True, "critical"))
        self.assertIn("решает человек", item["thread"][-1]["verdict"])

    def test_ответ_одного_модератора_не_решает_спор(self) -> None:
        self.moderators([decision("bugs-codex-2", "снять", "none")], [])
        item = self.item()
        self.assertEqual((item["outcome"], item["moderated"], item["moderators_split"], item["final_severity"]),
                         ("disputed", False, True, "critical"))
        self.assertIn("только один модератор", item["thread"][-1]["verdict"])


if __name__ == "__main__":
    unittest.main()
