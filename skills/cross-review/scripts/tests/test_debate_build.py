from __future__ import annotations

import json
import tempfile
import types
import unittest
from pathlib import Path

from support import FIXTURES, copy_fixture, last_json_line, read_json, run, write_json

from crossreview import debate_inputs

STATUSES = types.MappingProxyType({
    "bugs-claude-1": "confirmed",
    "bugs-claude-2": "confirmed",
    "arch-claude-1": "refined",
    "bugs-codex-2": "confirmed",
    "arch-claude-4": "immaterial",
    "arch-codex-1": "immaterial",
    "arch-claude-2": "pre_existing",
    "bugs-codex-3": "rejected_by_verify",
    "arch-codex-2": "dropped_by_synthesis",
})
ROSTER = types.MappingProxyType({"claude": ["arch-claude", "bugs-claude"], "codex": ["arch-codex", "bugs-codex"]})
DEBATE_FILES = ("items.json", "consensus.json", "for-claude.json", "for-codex.json")


def raw_finding(fid: str, source: str, **fields: object) -> dict:
    """A raw finding of agent `source` in x.py, minor unless given; a None field is left out."""
    finding = {"id": fid, "severity": "minor", "file": "x.py", "sources": [source], **fields}
    return {key: field for key, field in finding.items() if field is not None}


def write_dropped(run_dir: Path, fid: str) -> None:
    """events.jsonl with a stage event, a line that is not JSON and synthesis dropping the raw finding fid."""
    raw_findings = read_json(run_dir / "stages" / "1-found.json")["findings"]
    found = {finding["id"]: finding for finding in raw_findings}
    dropped = {"kind": "dropped", "agent": "synthesis", "findings": [found[fid]]}
    lines = ('{"kind": "stage", "stage": "synthesis"}', "не JSON", json.dumps(dropped))
    (run_dir / "events.jsonl").write_text("".join(f"{line}\n" for line in lines))


class DebateBuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self.workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.debate = self.root / "debate"

    def build(self, run_dir: Path) -> dict:
        res = run("debate_build", run_dir, self.debate)
        self.assertEqual(res.returncode, 0, res.stderr)
        return last_json_line(res.stdout)

    def cards(self) -> dict[str, dict]:
        return {card["id"]: card for card in read_json(self.debate / "items.json")}

    def ids(self, name: str, key: str = "findings") -> set[str]:
        return {card["id"] for card in read_json(self.debate / name)[key]}

    def test_раскладка_на_консенсус_и_находки_одной_стороны(self) -> None:
        summary = self.build(FIXTURES / "run-basic")
        consensus = read_json(self.debate / "consensus.json")
        self.assertEqual([card["id"] for card in consensus], ["bugs-claude-1"])
        self.assertEqual(self.ids("for-codex.json"), {"bugs-claude-2", "arch-claude-1", "arch-claude-2", "arch-claude-4"})
        self.assertEqual(
            self.ids("for-claude.json"),
            {"bugs-codex-2", "bugs-codex-3", "arch-codex-1", "arch-codex-2", "arch-claude-4"},
        )
        self.assertEqual(summary["roster"], ROSTER)
        counts = (summary["consensus"], summary["for_codex"], summary["for_claude"])
        self.assertEqual(counts, (1, 4, 5))
        self.assertTrue(summary["verify_ran"])

    def test_открытый_вопрос_идёт_другой_стороне(self) -> None:
        self.build(FIXTURES / "run-basic")
        self.assertEqual(self.ids("for-codex.json", "open_questions"), {"arch-claude-3"})
        self.assertEqual(self.ids("for-claude.json", "open_questions"), set())
        self.assertNotIn("arch-claude-3", self.cards())

    def test_статусы_пунктов(self) -> None:
        self.build(FIXTURES / "run-basic")
        cards = self.cards()
        self.assertEqual(set(cards), set(STATUSES))
        for fid, status in STATUSES.items():
            with self.subTest(id=fid):
                self.assertEqual(cards[fid]["status"], status)

    def test_пункт_обеих_сторон_отложенный_конвейером_смотрят_обе(self) -> None:
        self.build(FIXTURES / "run-basic")
        self.assertEqual(self.cards()["arch-claude-4"]["vendors"], ["claude", "codex"])
        self.assertIn("arch-claude-4", self.ids("for-codex.json"))
        self.assertIn("arch-claude-4", self.ids("for-claude.json"))

    def test_слитые_при_синтезе_находки_не_попадают_в_пункты(self) -> None:
        self.build(FIXTURES / "run-basic")
        for fid in ("bugs-codex-1", "bugs-claude-3", "arch-codex-3"):
            with self.subTest(id=fid):
                self.assertNotIn(fid, self.cards())

    def test_отброшенные_берутся_из_events(self) -> None:
        run_dir = copy_fixture("run-basic", self.root / "run")
        # эвристика сочла бы bugs-claude-3 слитой, а arch-codex-2 — отброшенной; events.jsonl главнее
        write_dropped(run_dir, "bugs-claude-3")
        summary = self.build(run_dir)
        self.assertTrue(summary["dropped_from_events"])
        self.assertEqual(self.cards()["bugs-claude-3"]["status"], "dropped_by_synthesis")
        self.assertNotIn("arch-codex-2", self.cards())

    def test_без_events_отброшенные_находит_эвристика(self) -> None:
        run_dir = copy_fixture("run-basic", self.root / "run")
        (run_dir / "events.jsonl").unlink()
        self.assertFalse(self.build(run_dir)["dropped_from_events"])
        cards = self.cards()
        statuses = {fid: card["status"] for fid, card in cards.items()}
        self.assertEqual(statuses, STATUSES)
        # открытый вопрос синтеза не возвращается отброшенной находкой
        self.assertNotIn("arch-claude-3", cards)

    def test_без_верификации_находки_не_проверены(self) -> None:
        synth = self.build_unverified()
        cards = self.cards()
        for finding in synth["findings"]:
            with self.subTest(id=finding["id"]):
                self.assertEqual(cards[finding["id"]]["status"], "unverified")
        self.assertEqual(cards["arch-claude-2"]["status"], "pre_existing")
        self.assertNotIn("rejected_by_verify", {card["status"] for card in cards.values()})
        # без проверки консенсус ещё не подтверждён — его смотрят обе стороны
        self.assertEqual(read_json(self.debate / "consensus.json"), [])
        self.assertIn("bugs-claude-1", self.ids("for-claude.json"))

    def build_unverified(self) -> dict:
        """The run-basic fixture as if verify never ran; its synthesis is the result."""
        run_dir = copy_fixture("run-basic", self.root / "run")
        (run_dir / "stages" / "3-verified.json").unlink()
        synth = read_json(run_dir / "stages" / "2-synthesized.json")
        for finding in synth["findings"]:
            finding["verdict"] = "unverified"
        write_json(run_dir / "findings.json", synth)
        self.assertFalse(self.build(run_dir)["verify_ran"])
        return synth

    def test_один_вендор_в_ростере_выход1(self) -> None:
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
        expected = (run_dir / "debate").resolve()
        self.assertEqual(last_json_line(res.stdout)["debate_dir"], str(expected))
        for name in DEBATE_FILES:
            with self.subTest(file=name):
                self.assertTrue((run_dir / "debate" / name).is_file())
        self.assertFalse(self.debate.exists())

    def test_нет_файлов_прогона_ошибка_json(self) -> None:
        findings = copy_fixture("run-basic", self.root / "run") / "findings.json"
        with self.subTest(case="нет каталога"):
            self.assert_not_finished(self.root / "missing", "1-found.json")
        cases = (
            ("нет findings.json", "findings.json", findings.unlink),
            ("findings.json не JSON", "not JSON", lambda: findings.write_text("{")),
            ("findings.json список", "expected an object", lambda: write_json(findings, [])),
        )
        for label, needle, spoil in cases:
            with self.subTest(case=label):
                spoil()
                self.assert_not_finished(findings.parent, needle)

    def assert_not_finished(self, round_dir: Path, needle: str) -> None:
        res = run("debate_build", round_dir, self.debate)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertEqual(res.stderr, "")
        error = last_json_line(res.stdout)["error"]
        self.assertIn(needle, error)
        self.assertIn("review_verify.py apply", error)
        self.assertFalse(self.debate.exists())

    def test_без_аргументов_выход2(self) -> None:
        res = run("debate_build")
        self.assertEqual(res.returncode, 2)
        self.assertIn("usage:", res.stderr)

    def test_тег_заголовка(self) -> None:
        cases = (
            ("[DB-1] Потеря записи", "[DB-1]"),
            ("Без тега", ""),
            ("[незакрытый тег", ""),
            ("", ""),
        )
        for title, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(debate_inputs.tag(title), expected)

    def test_покрытие_сырой_находки(self) -> None:
        kept = [raw_finding("a-1", "a", severity=None, line=10, title="[T-1] заголовок")]
        cases = (
            ("major не отбрасывается синтезом", raw_finding("b-1", "b", severity="major", file="y.py"), True),
            ("тот же тег", raw_finding("b-1", "b", title="[T-1] другое", file="y.py"), True),
            ("тот же агент рядом", raw_finding("a-2", "a", line=15), True),
            ("тот же агент далеко", raw_finding("a-2", "a", line=16), False),
            ("другой агент рядом", raw_finding("b-1", "b", line=10), False),
        )
        for name, raw, expected in cases:
            with self.subTest(name):
                self.assertIs(debate_inputs.covered(raw, kept), expected)

    def test_нет_events_значит_нет_списка_отброшенных(self) -> None:
        self.assertIsNone(debate_inputs.dropped_from_events(self.root / "events.jsonl"))


if __name__ == "__main__":
    unittest.main()
