from __future__ import annotations

import shutil
import tempfile
import types
import unittest
from pathlib import Path
from typing import NamedTuple

from support import copy_fixture, last_json_line, pick, read_json, run, write_json

from crossreview.outcome_judge import severity_shift
from crossreview.outcome_markdown import plural
from crossreview.outcome_words import SET_ASIDE

NOT_REAL = ("not_real", "none")
NOT_WORTH = ("not_worth_fixing", "none")
UNSETTLED = ("dropped", "question", "unexamined")
SIDE_FIELDS = ("file", "line", "severity", "title", "body", "fix")
LEGACY = "legacy-agent-1"
NO_ANSWER = ()
SHIFT_KEYS = ("from", "to", "direction", "by")


def verdict(fid: str, stance: str, severity: str) -> dict:
    return {
        "id": fid,
        "stance": stance,
        "severity": severity,
        "argument": "Синтетический довод.",
        "evidence": "app/x.py:1",
    }


def response(fid: str, position: str, severity: str) -> dict:
    return {
        "id": fid,
        "position": position,
        "severity": severity,
        "argument": "Синтетический ответ.",
        "evidence": "app/x.py:2",
    }


def decision(fid: str, severity: str, **extra: object) -> dict:
    return {"id": fid, "verdict": "прав Codex", "severity": severity, "note": "Синтетическое решение.", **extra}


def both(fid: str, claude: tuple, codex: tuple) -> dict:
    """Round-1 verdicts of both sides on one item: claude and codex are (stance, severity)."""
    return {
        "claude": [verdict(fid, *claude)],
        "codex": [verdict(fid, *codex)],
    }


def extra_finding(fid: str, **over: object) -> dict:
    """A side finding the moderator confirmed in extra.json; keyword arguments override its fields."""
    return {
        "id": fid,
        "file": "app/api.py",
        "line": 70,
        "title": "Утечка",
        "body": "Тело.",
        "fix": "Закрыть.",
        "severity": "major",
        "note": "Проверено.",
        **over,
    }


def side_finding() -> dict:
    """A side finding of round 2, where extra_finding puts it."""
    return {
        "file": "app/api.py",
        "line": 70,
        "severity": "major",
        "title": "Утечка",
        "body": "Тело.",
        "fix": "Закрыть.",
        "evidence": "app/api.py:70",
    }


def unused_import() -> dict:
    """A side finding of round 2 that extra.json does not confirm."""
    return {
        "file": "app/db.py",
        "line": 3,
        "severity": "minor",
        "title": "Лишний импорт",
        "body": "Не нужен.",
        "fix": "Убрать.",
        "evidence": "app/db.py:3",
    }


def reported(extra: dict) -> dict:
    """The round-2 side finding a confirmed extra finding stands for."""
    return {**{key: extra[key] for key in SIDE_FIELDS}, "evidence": "app/api.py:70"}


def built_item(**over: object) -> dict:
    """An item of items.json with every field it needs; keyword arguments override them."""
    return {
        "id": "a",
        "file": "a.py",
        "line": 1,
        "severity": "major",
        "title": "T",
        "body": "B",
        "sources": ["bugs-claude"],
        "vendors": ["claude"],
        "status": "confirmed",
        **over,
    }


def by_id(entries: list[dict], fid: str) -> dict:
    return next(entry for entry in entries if entry["id"] == fid)


def to_fix(entry: dict) -> bool:
    """Whether a final item goes to fixing: kept, settled and not older than the change."""
    if entry["final_severity"] == "none" or entry["status"] == "pre_existing":
        return False
    if entry["outcome"] in UNSETTLED:
        return False
    return entry["outcome"] != "disputed" or entry["moderated"]


def shift_of(base: str | None, final: str, direction: str, by: str) -> dict:
    return dict(zip(SHIFT_KEYS, (base, final, direction, by)))


def bad_answers() -> tuple:
    """(case, answer file, answer, error) for every answer the schemas refuse."""
    unpositioned = {"id": "bugs-codex-2", "severity": "major", "argument": ".", "evidence": "."}
    return (
        ("пустой вердикт", "claude-r1.json", {"verdicts": [{}], "open_question_answers": []},
         "claude-r1.json.verdicts[0].id: missing"),
        ("ответ без position", "codex-r2.json", {"responses": [unpositioned], "side_findings": []},
         "codex-r2.json.responses[0].position: missing"),
        ("решение модератора без severity", "moderator.json", [{"id": "bugs-codex-2", "verdict": "снять", "note": "."}],
         "moderator.json[0].severity: missing"),
        ("попутная без id", "extra.json", [{"file": "a.py"}], "extra.json[0].id: missing"),
    )


def with_agents(findings: dict, agents: object) -> dict:
    return {**findings, "sources": {**findings["sources"], "agents": agents}}


def broken_findings(findings: dict) -> tuple:
    """(case, findings.json, error) for every findings.json the outcome refuses."""
    first, *rest = findings["sources"]["agents"]
    return [
        ("agents не список", with_agents(findings, {}), "findings.json.sources.agents: expected array or null"),
        ("raised строкой", with_agents(findings, [{**first, "raised": "3"}, *rest]),
         "findings.json.sources.agents[0].raised: expected integer"),
        ("модель числом", with_agents(findings, [{**first, "actual_model": 5}, *rest]),
         "findings.json.sources.agents[0].actual_model: expected string or null"),
        ("агент без executor", with_agents(findings, [{"name": "x", "raised": 1}]),
         "findings.json.sources.agents[0].executor: missing"),
        ("scope списком", {**findings, "scope": []}, "findings.json.scope: expected object or null"),
        ("не объект", [], "findings.json: expected object"),
    ]


class OutcomeCase(unittest.TestCase):
    """The debate of run-basic, built once; every test answers it on a fresh copy.

    Items: bugs-claude-1 (both sides, major, consensus), bugs-claude-2 (Claude, major), arch-claude-1 (Claude,
    refined minor), arch-claude-2 (Claude, pre_existing major), bugs-codex-2 (Codex, critical), arch-codex-1 (Codex,
    immaterial minor), bugs-codex-3 (Codex, rejected_by_verify major), arch-codex-2 (Codex, dropped_by_synthesis
    minor), arch-claude-4 (both sides, immaterial minor); open question arch-claude-3 for Codex.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.workspace = tempfile.TemporaryDirectory()
        root = Path(cls.workspace.name).resolve()
        cls.run_dir = copy_fixture("run-basic", root / "run")
        res = run("debate_build", cls.run_dir)
        if res.returncode != 0:
            raise AssertionError(res.stdout + res.stderr)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.workspace.cleanup()

    def fresh_dir(self) -> Path:
        return Path(tempfile.mkdtemp(dir=self.workspace.name))

    def debate(
        self,
        r1: dict | None = None,
        r2: dict | None = None,
        moderator: list | None = None,
        extra: list | None = None,
    ) -> Path:
        """A fresh copy of the built debate with the given answers: r1 {examiner: verdicts}, r2 {author: responses}."""
        debate_dir = self.fresh_dir() / "debate"
        shutil.copytree(self.run_dir / "debate", debate_dir)
        for side, verdicts in (r1 or {}).items():
            write_json(debate_dir / f"{side}-r1.json", {"verdicts": verdicts, "open_question_answers": []})
        for side, responses in (r2 or {}).items():
            write_json(debate_dir / f"{side}-r2.json", {"responses": responses, "side_findings": []})
        if moderator is not None:
            write_json(debate_dir / "moderator.json", moderator)
        if extra is not None:
            write_json(debate_dir / "extra.json", extra)
        return debate_dir

    def run_copy(self) -> Path:
        """A fresh copy of the run with its built debate, for a test that changes findings.json."""
        run_dir = self.fresh_dir() / "run"
        shutil.copytree(self.run_dir, run_dir)
        return run_dir

    def finish(self, *args: object) -> str:
        res = run("debate_outcome", *args)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return res.stdout

    def outcome(self, debate_dir: Path) -> dict:
        return last_json_line(self.finish(self.run_dir, debate_dir))

    def markdown(self, debate_dir: Path) -> str:
        return self.finish(self.run_dir, debate_dir, "--markdown")

    def refused(self, run_dir: Path, debate_dir: Path) -> str:
        """The error the outcome stops with; it writes no final.json."""
        res = run("debate_outcome", run_dir, debate_dir)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertFalse((debate_dir / "final.json").exists())
        return last_json_line(res.stdout)["error"]

    def meta(self, run_dir: Path) -> dict:
        self.finish(run_dir)
        return read_json(run_dir / "debate" / "final.json")["meta"]

    def final(self, debate_dir: Path) -> dict:
        return read_json(debate_dir / "final.json")

    def entries(self, debate_dir: Path) -> dict:
        return {entry["id"]: entry for entry in self.final(debate_dir)["items"]}

    def actionable(self, debate_dir: Path) -> set[str]:
        entries = self.entries(debate_dir)
        return {fid for fid, entry in entries.items() if to_fix(entry)}

    def write_side(self, debate_dir: Path, *found: dict) -> None:
        write_json(debate_dir / "codex-r2.json", {"responses": [], "side_findings": list(found)})


class Case(NamedTuple):
    """A row of the outcome table: the answers to one item and what the outcome makes of them."""

    label: str
    fid: str
    r1: dict  # {examiner: (stance, severity)}
    answer: tuple  # the author's (position, severity), empty when the author did not answer
    moderated: str | None  # the moderator's severity
    outcome: str
    final: str
    direction: str
    by: str

    def examined(self) -> dict:
        return {
            side: [verdict(self.fid, *given)]
            for side, given in self.r1.items()
        }

    def responded(self) -> dict | None:
        if not self.answer:
            return None
        return {AUTHOR[self.fid]: [response(self.fid, *self.answer)]}

    def moderation(self) -> list | None:
        return [decision(self.fid, self.moderated)] if self.moderated else None

    def settled(self) -> bool:
        if self.outcome in UNSETTLED:
            return False
        return self.outcome != "disputed" or self.moderated is not None

    def fixes(self, entry: dict) -> bool:
        kept = self.final != "none" and self.settled()
        return kept and entry["status"] != "pre_existing"

    def predates(self, entry: dict) -> bool:
        kept = self.final != "none" and self.outcome != "dropped"
        return kept and entry["status"] == "pre_existing"

    def shifted(self) -> tuple:
        return (self.final, self.direction, self.by)

    def pending(self) -> bool:
        return self.outcome == "disputed" and self.moderated is None


CASES = (
    Case("консенсус", "bugs-claude-1", {}, NO_ANSWER, None, "consensus", "major", "same", "none"),
    Case("подтверждено", "bugs-claude-2", {"codex": ("real", "major")}, NO_ANSWER, None,
         "confirmed", "major", "same", "none"),
    Case("восстановлено после отсева верификацией", "bugs-codex-3", {"claude": ("real", "major")}, NO_ANSWER, None,
         "restored", "major", "raised", "side"),
    Case("восстановлено после отсева синтезом с новой severity", "arch-codex-2",
         {"claude": ("real_but_different", "major")}, ("concede", "major"), None,
         "restored", "major", "raised", "side"),
    Case("восстановлено обеими сторонами, берётся строже", "arch-claude-4",
         {"claude": ("real", "minor"), "codex": ("real_but_different", "major")}, NO_ANSWER, None,
         "restored", "major", "raised", "side"),
    Case("уточнено: автор согласился с severity проверяющего", "bugs-claude-2", {"codex": ("real", "minor")},
         ("concede", "minor"), None, "refined", "minor", "lowered", "side"),
    Case("уточнено: amend на severity проверяющего", "arch-claude-2", {"codex": ("real_but_different", "minor")},
         ("amend", "minor"), None, "refined", "minor", "lowered", "author"),
    Case("дыра amend: major → minor без модератора — спор", "bugs-claude-2",
         {"codex": ("real_but_different", "minor")}, ("amend", "major"), None,
         "disputed", "major", "same", "none"),
    Case("дыра amend: своя третья severity — спор", "bugs-codex-2", {"claude": ("real_but_different", "minor")},
         ("amend", "major"), None, "disputed", "critical", "same", "none"),
    Case("спорный amend решил модератор", "bugs-claude-2", {"codex": ("real_but_different", "minor")},
         ("amend", "major"), "minor", "disputed", "minor", "lowered", "moderator"),
    Case("спор: автор настаивает, модератора нет", "bugs-codex-2", {"claude": NOT_REAL},
         ("defend", "critical"), None, "disputed", "critical", "same", "none"),
    Case("спор: автор не ответил", "bugs-codex-2", {"claude": NOT_REAL}, NO_ANSWER, None,
         "disputed", "critical", "same", "none"),
    Case("спор снят модератором", "bugs-codex-2", {"claude": NOT_REAL}, ("defend", "critical"), "none",
         "disputed", "none", "dropped", "moderator"),
    Case("спор модератор повысил", "arch-claude-1", {"codex": NOT_WORTH}, ("defend", "minor"), "major",
         "disputed", "major", "raised", "moderator"),
    Case("спор обеих сторон по пункту, отсеянному конвейером", "arch-claude-4",
         {"claude": ("real", "minor"), "codex": NOT_REAL}, NO_ANSWER, None, "disputed", "none", "same", "none"),
    Case("снято: автор согласился", "arch-claude-1", {"codex": NOT_WORTH}, ("concede", "none"), None,
         "dropped", "none", "dropped", "side"),
    Case("снято: amend на none вслед за проверяющим", "bugs-claude-2", {"codex": NOT_REAL}, ("amend", "none"), None,
         "dropped", "none", "dropped", "author"),
    Case("отложенный остаётся отложенным: автор согласился", "arch-codex-1", {"claude": NOT_WORTH},
         ("concede", "none"), None, "dropped", "none", "same", "none"),
    Case("отложенный остаётся отложенным: обе стороны", "arch-claude-4",
         {"claude": NOT_REAL, "codex": NOT_WORTH}, NO_ANSWER, None, "dropped", "none", "same", "none"),
    Case("отложенный спор модератор вернул", "bugs-codex-3", {"claude": NOT_REAL}, ("defend", "major"), "major",
         "disputed", "major", "raised", "moderator"),
    Case("не проверено: вердикта нет", "bugs-claude-2", {}, NO_ANSWER, None, "unexamined", "major", "same", "none"),
    Case("не проверено обеими сторонами", "arch-claude-4", {}, NO_ANSWER, None,
         "unexamined", "none", "same", "none"),
    Case("общий пункт: одна сторона вернула real — не решено", "arch-claude-4", {"claude": ("real", "major")},
         NO_ANSWER, None, "unexamined", "none", "same", "none"),
    Case("общий пункт: одна сторона сняла — не решено", "arch-claude-4", {"codex": NOT_REAL}, NO_ANSWER, None,
         "unexamined", "none", "same", "none"),
    Case("не проверено: отложенный не идёт к исправлению", "bugs-codex-3", {}, NO_ANSWER, None,
         "unexamined", "none", "same", "none"),
)
AUTHOR = types.MappingProxyType({
    "bugs-claude-2": "claude",
    "arch-claude-1": "claude",
    "arch-claude-2": "claude",
    "bugs-codex-2": "codex",
    "arch-codex-1": "codex",
    "bugs-codex-3": "codex",
    "arch-codex-2": "codex",
})


class OutcomesTest(OutcomeCase):
    def test_исходы_по_таблице(self) -> None:
        for case in CASES:
            with self.subTest(case=case.label):
                self.check_case(case)

    def check_case(self, case: Case) -> None:
        debate_dir = self.debate(case.examined(), case.responded(), case.moderation())
        summary = self.outcome(debate_dir)
        entry = self.entries(debate_dir)[case.fid]
        self.check_entry(case, entry)
        self.assertIs(case.fid in summary["needs_moderator"], case.pending())
        actionable = self.actionable(debate_dir)
        self.assertEqual(case.fixes(entry), case.fid in actionable)
        self.assertEqual(case.predates(entry), case.fid in summary["pre_existing"])
        self.assertEqual(summary["actionable"], len(actionable))

    def check_entry(self, case: Case, entry: dict) -> None:
        self.assertEqual(pick(entry, "outcome", "final_severity"), (case.outcome, case.final))
        kept = "none" if entry["status"] in SET_ASIDE else entry["severity"]
        self.assertEqual(entry["severity_shift"], shift_of(kept, *case.shifted()))
        self.assertIs(entry["moderated"], case.moderated is not None)

    def test_amend_с_другой_severity_уходит_модератору_с_обеими_позициями(self) -> None:
        debate_dir = self.debate(
            {"codex": [verdict("bugs-claude-2", "real_but_different", "minor")]},
            {"claude": [response("bugs-claude-2", "amend", "major")]},
        )
        summary = self.outcome(debate_dir)
        self.assertEqual(summary["needs_moderator"], ["bugs-claude-2"])
        dispute = read_json(debate_dir / "disputes.json")["disputes"][0]
        self.assertEqual(dispute["id"], "bugs-claude-2")
        self.check_thread(dispute["thread"])

    def check_thread(self, thread: list[dict]) -> None:
        kinds = [said["kind"] for said in thread]
        self.assertEqual(kinds, ["finding", "pipeline", "examine", "respond"])
        severities = [said["severity"] for said in thread[2:]]
        self.assertEqual(severities, ["minor", "major"])

    def test_найдено_в_споре(self) -> None:
        extra = extra_finding(
            "codex-side-1",
            title="Утечка соединения",
            body="Соединение не закрывается.",
            fix="Закрыть в finally.",
            sources=["codex"],
            vendors=["codex"],
            note="Проверено по коду.",
        )
        debate_dir = self.debate(extra=[extra])
        self.write_side(debate_dir, reported(extra), unused_import())
        summary = self.outcome(debate_dir)
        entry = self.entries(debate_dir)["codex-side-1"]
        self.assertEqual(pick(entry, "outcome", "final_severity", "status"), ("side", "major", "side_finding"))
        self.assertEqual(entry["severity_shift"], shift_of(None, "major", "new", "moderator"))
        # extra.json exists: the moderator saw both side findings and confirmed only the first
        side_findings = pick(summary, "side_findings_pending", "side_findings_rejected")
        self.assertEqual(side_findings, ([], ["codex-side-2"]))
        self.assertEqual(read_json(debate_dir / "disputes.json")["side_findings"], [])
        self.assertNotIn("codex-side-1", sum(summary["reprioritized"].values(), []))
        self.check_side_markdown(self.markdown(debate_dir))

    def check_side_markdown(self, text: str) -> None:
        self.assertNotIn("без проверки модератора", text)
        self.assertIn("которые модератор не подтвердил, 1:** `codex-side-2`", text)

    def test_попутная_находка_ждёт_модератора_до_extra(self) -> None:
        debate_dir = self.debate()
        self.write_side(debate_dir, unused_import())
        summary = self.outcome(debate_dir)
        side_findings = pick(summary, "side_findings_pending", "side_findings_rejected")
        self.assertEqual(side_findings, (["codex-side-1"], []))

    def test_попутная_находка_без_vendors_берёт_сторону_из_id(self) -> None:
        extra = extra_finding("codex-side-1")
        debate_dir = self.debate(extra=[extra])
        self.write_side(debate_dir, reported(extra))
        self.outcome(debate_dir)
        entry = self.entries(debate_dir)["codex-side-1"]
        self.assertEqual(pick(entry, "vendors", "sources"), (["codex"], ["codex"]))
        self.assertEqual(entry["thread"][0]["side"], "codex")

    def test_модератор_не_подменяет_попутную_находку_под_её_id(self) -> None:
        extra = extra_finding(
            "codex-side-1",
            file="app/other.py",
            line=1,
            title="Чужая находка",
            body="Другое.",
            fix="Другое.",
            severity="critical",
        )
        debate_dir = self.debate(extra=[extra])
        self.write_side(debate_dir, side_finding())
        self.outcome(debate_dir)
        entry = self.entries(debate_dir)["codex-side-1"]
        anchor = pick(entry, "file", "line", "title", "body", "fix")
        self.assertEqual(anchor, ("app/api.py", 70, "Утечка", "Тело.", "Закрыть."))
        # серьёзность назначает модератор
        self.assertEqual(entry["final_severity"], "critical")

    def test_модератор_не_придумывает_попутную_находку(self) -> None:
        debate_dir = self.debate(extra=[extra_finding("codex-side-99", severity="critical")])
        self.assertIn("codex-side-99 is no side finding of round 2", self.refused(self.run_dir, debate_dir))

    def test_без_входа_debate_build_ошибка(self) -> None:
        for name in ("items.json", "for-claude.json", "for-codex.json"):
            with self.subTest(file=name):
                debate_dir = self.debate()
                (debate_dir / name).unlink()
                self.assertIn(f"no {name}, run debate_build.py first", self.refused(self.run_dir, debate_dir))

    def test_вход_debate_build_не_той_формы_ошибка(self) -> None:
        cases = (
            ("items.json", {}, "items.json: expected array"),
            ("items.json", [{}], "items.json[0].id: missing"),
            ("items.json", [{"id": "a", "sources": [], "vendors": ["claude", "codex"]}], "items.json[0].title: missing"),
            ("items.json", [built_item(severity=["major"])], "items.json[0].severity: expected string"),
            ("items.json", [built_item(vendors="claude")], "items.json[0].vendors: expected array"),
            ("for-codex.json", {"findings": [], "open_questions": [{"id": "q", "vendors": [1]}]},
             "for-codex.json.open_questions[0].file: missing"),
            ("for-claude.json", [], "for-claude.json: expected object"),
            ("for-codex.json", {"findings": [{}], "open_questions": []}, "for-codex.json.findings[0].id: missing"),
        )
        for name, written, needle in cases:
            with self.subTest(file=name, value=written):
                debate_dir = self.debate()
                write_json(debate_dir / name, written)
                self.assertIn(needle, self.refused(self.run_dir, debate_dir))

    def legacy(self, r1: dict | None = None) -> Path:
        """The built debate plus an item a source outside the roster raised, which both sides examine."""
        debate_dir = self.debate(r1)
        built = read_json(debate_dir / "items.json")
        outsider = {
            **by_id(built, "bugs-claude-2"),
            "id": LEGACY,
            "sources": ["legacy-agent"],
            "vendors": ["legacy-agent"],
        }
        write_json(debate_dir / "items.json", [*built, outsider])
        for side in ("claude", "codex"):
            self.add_finding(debate_dir / f"for-{side}.json", outsider)
        return debate_dir

    def add_finding(self, path: Path, finding: dict) -> None:
        side_input = read_json(path)
        write_json(path, {**side_input, "findings": [*side_input["findings"], finding]})

    def test_находка_источника_вне_ростера_ждёт_обе_стороны(self) -> None:
        cases = (
            ("без вердиктов", {}, "unexamined", "major"),
            ("один вердикт", {"codex": [verdict(LEGACY, "real", "major")]}, "unexamined", "major"),
            ("оба подтвердили", both(LEGACY, ("real", "minor"), ("real", "major")), "confirmed", "major"),
            ("оба отклонили", both(LEGACY, NOT_REAL, NOT_WORTH), "dropped", "none"),
        )
        for label, r1, *expected in cases:
            with self.subTest(case=label):
                self.check_legacy(r1, tuple(expected))
        self.assertIn("`legacy-agent-1` [major]", self.markdown(self.legacy()))

    def check_legacy(self, r1: dict, expected: tuple) -> None:
        debate_dir = self.legacy(r1)
        summary = self.outcome(debate_dir)
        entry = self.entries(debate_dir)[LEGACY]
        self.assertEqual(pick(entry, "outcome", "final_severity"), expected)
        self.assertEqual(entry["thread"][0]["side"], "legacy-agent")
        self.assertEqual(summary["items"], 10)

    def test_findings_json_не_той_формы_ошибка(self) -> None:
        findings = read_json(self.run_dir / "findings.json")
        for label, broken, needle in broken_findings(findings):
            with self.subTest(case=label):
                run_dir = self.run_copy()
                write_json(run_dir / "findings.json", broken)
                self.assertIn(needle, self.refused(run_dir, run_dir / "debate"))

    def test_без_findings_json_meta_пустая(self) -> None:
        run_dir = self.run_copy()
        (run_dir / "findings.json").unlink()
        meta = self.meta(run_dir)
        blank = ([], 0, None, None)
        self.assertEqual(pick(meta, "agents", "raw", "task", "verify_model"), blank)

    def test_stages_null_в_findings_json_meta_без_моделей(self) -> None:
        run_dir = self.run_copy()
        findings = read_json(run_dir / "findings.json")
        stats = {**findings["stats"], "stages": None}
        write_json(run_dir / "findings.json", {**findings, "stats": stats})
        meta = self.meta(run_dir)
        self.assertEqual(pick(meta, "verify_model", "synthesis_model"), (None, None))

    def test_общая_непроверенная_находка_не_восстановлена(self) -> None:
        real = ("real", "minor")
        debate_dir = self.debate(both("arch-claude-4", real, real))
        built = read_json(debate_dir / "items.json")
        by_id(built, "arch-claude-4")["status"] = "unverified"
        write_json(debate_dir / "items.json", built)
        self.outcome(debate_dir)
        entry = self.entries(debate_dir)["arch-claude-4"]
        self.assertEqual(pick(entry, "outcome", "final_severity"), ("confirmed", "minor"))

    def test_открытый_вопрос(self) -> None:
        debate_dir = self.debate()
        answer = {"id": "arch-claude-3", "answer": "Да, вызывается дважды.", "evidence": "app/a.py:9"}
        write_json(debate_dir / "codex-r1.json", {"verdicts": [], "open_question_answers": [answer]})
        summary = self.outcome(debate_dir)
        question = self.final(debate_dir)["questions"][0]
        self.assertEqual(pick(question, "id", "outcome", "final_severity"), ("arch-claude-3", "question", "none"))
        self.assertEqual(pick(question, "asked", "answered_by"), (["codex"], ["codex"]))
        self.assertEqual(summary["questions"], 1)

    def test_модераторы_разошлись_остаётся_спором(self) -> None:
        debate_dir = self.debate(
            {"claude": [verdict("bugs-codex-2", *NOT_REAL)]},
            {"codex": [response("bugs-codex-2", "defend", "critical")]},
            [decision("bugs-codex-2", "critical", split=True)],
        )
        summary = self.outcome(debate_dir)
        entry = self.entries(debate_dir)["bugs-codex-2"]
        split = pick(entry, "outcome", "final_severity", "moderated", "moderators_split")
        self.assertEqual(split, ("disputed", "critical", False, True))
        self.assertEqual(entry["severity_shift"]["direction"], "same")
        self.assertEqual(entry["thread"][-1]["kind"], "moderator")
        self.assertEqual(pick(summary, "moderators_split", "needs_moderator"), (["bugs-codex-2"], []))

    def test_неразрешённое_не_идёт_к_исправлению(self) -> None:
        debate_dir = self.debate(
            {"claude": [verdict("bugs-codex-2", *NOT_REAL)]},
            {"codex": [response("bugs-codex-2", "defend", "critical")]},
        )
        summary = self.outcome(debate_dir)
        actionable = self.actionable(debate_dir)
        self.assertNotIn("bugs-codex-2", actionable)
        self.assertIn("bugs-codex-2", summary["needs_moderator"])
        self.assertIn("bugs-claude-2", summary["unexamined"])
        self.assertNotIn("bugs-claude-2", actionable)
        self.assertEqual(summary["actionable"], len(actionable))
        self.assertEqual(summary["by_severity"]["critical"], 0)

    def test_дефект_до_изменения_вне_подсчёта(self) -> None:
        # arch-claude-2 — pre_existing major; Codex согласился, автор уточнил на той же severity: refined, но не к исправлению
        debate_dir = self.debate(
            {"codex": [verdict("arch-claude-2", "real_but_different", "minor")]},
            {"claude": [response("arch-claude-2", "amend", "minor")]},
        )
        summary = self.outcome(debate_dir)
        entry = self.entries(debate_dir)["arch-claude-2"]
        self.assertEqual(pick(entry, "outcome", "final_severity"), ("refined", "minor"))
        self.assertNotIn("arch-claude-2", self.actionable(debate_dir))
        self.assertEqual(summary["pre_existing"], ["arch-claude-2"])
        self.assertEqual(summary["actionable"], len(self.actionable(debate_dir)))
        self.check_before_change(self.markdown(debate_dir))

    def check_before_change(self, text: str) -> None:
        head, _, before = text.partition("- **Было до изменения, вне подсчёта, 1:**")
        self.assertNotIn("arch-claude-2", head)
        self.assertIn("`arch-claude-2` [minor]", before)

    def test_снятый_дефект_до_изменения_не_в_списке(self) -> None:
        debate_dir = self.debate(
            {"codex": [verdict("arch-claude-2", *NOT_REAL)]},
            {"claude": [response("arch-claude-2", "concede", "none")]},
        )
        summary = self.outcome(debate_dir)
        self.assertEqual(summary["pre_existing"], [])
        self.assertNotIn("Было до изменения", self.markdown(debate_dir))

    def test_ответ_не_по_схеме_ошибка(self) -> None:
        for label, name, answer, needle in bad_answers():
            with self.subTest(case=label):
                debate_dir = self.debate()
                write_json(debate_dir / name, answer)
                self.assertIn(needle, self.refused(self.run_dir, debate_dir))

    def test_модератор_не_трогает_пункт_без_спора(self) -> None:
        confirmed = {"codex": [verdict("bugs-claude-2", "real", "major")]}
        debate_dir = self.debate(confirmed, moderator=[decision("bugs-claude-2", "none")])
        self.outcome(debate_dir)
        entry = self.entries(debate_dir)["bugs-claude-2"]
        self.assertEqual(pick(entry, "outcome", "final_severity", "moderated"), ("confirmed", "major", False))


class ReprioritizedTest(OutcomeCase):
    """severity_shift on every item and the reprioritized lists of the summary and the markdown."""

    def answered(self) -> Path:
        examined = {
            "codex": [
                verdict("bugs-claude-2", "real_but_different", "minor"),
                verdict("arch-claude-2", "real", "minor"),
                verdict("arch-claude-1", *NOT_REAL),
            ],
            "claude": [
                verdict("arch-codex-2", "real_but_different", "major"),
                verdict("arch-codex-1", *NOT_REAL),
                verdict("bugs-codex-2", "real", "critical"),
                verdict("bugs-codex-3", *NOT_REAL),
            ],
        }
        responded = {
            "claude": [
                response("bugs-claude-2", "amend", "major"),
                response("arch-claude-2", "concede", "minor"),
                response("arch-claude-1", "concede", "none"),
            ],
            "codex": [
                response("arch-codex-2", "concede", "major"),
                response("arch-codex-1", "concede", "none"),
                response("bugs-codex-3", "defend", "major"),
            ],
        }
        return self.debate(examined, responded, [decision("bugs-codex-3", "major")])

    def test_сводка_reprioritized(self) -> None:
        summary = self.outcome(self.answered())
        # arch-codex-1 was set aside and stays out: leaving it out is no reprioritization
        reprioritized = {
            "raised": ["arch-codex-2", "bugs-codex-3"],
            "lowered": ["arch-claude-2"],
            "dropped": ["arch-claude-1"],
        }
        self.assertEqual(summary["reprioritized"], reprioritized)
        self.assertEqual(summary["needs_moderator"], ["bugs-claude-2"])

    def test_у_каждого_пункта_severity_shift(self) -> None:
        debate_dir = self.answered()
        self.outcome(debate_dir)
        for fid, entry in self.entries(debate_dir).items():
            with self.subTest(id=fid):
                self.check_shift(entry)

    def check_shift(self, entry: dict) -> None:
        shift = entry["severity_shift"]
        self.assertEqual(tuple(shift), SHIFT_KEYS)
        kept = "none" if entry["status"] in SET_ASIDE else entry["severity"]
        self.assertEqual(pick(shift, "from", "to"), (kept, entry["final_severity"]))
        self.assertIn(shift["direction"], ("raised", "lowered", "same", "dropped"))
        self.assertEqual(shift["by"] == "none", shift["direction"] == "same")

    def test_markdown_переоценка_приоритетов(self) -> None:
        text = self.markdown(self.answered())
        self.assertIn("- **Переоценка приоритетов:** повышено 2, понижено 1, снято 1.", text)
        self.assertIn("  - Повышено: `arch-codex-2` отложено → major docs/guide.md:3", text)
        self.assertIn("  - Повышено: `bugs-codex-3` отложено → major app/api.py:55", text)
        self.assertIn("(по вердикту проверяющей стороны)", text)
        self.assertIn("  - Понижено: `arch-claude-2` major → minor", text)
        self.assertIn("  - Снято: `arch-claude-1` minor → снято app/models.py:5", text)
        self.assertIn("(решение модератора)", text)
        self.assertIn("`bugs-claude-2` [major]", text)
        self.assertIn("— ждёт модератора", text)

    def test_markdown_без_переоценки(self) -> None:
        text = self.markdown(self.debate())
        self.assertNotIn("Переоценка приоритетов", text)
        self.assertTrue(text.startswith("**Итог: "))


class PluralTest(unittest.TestCase):
    def test_склонение_по_числу(self) -> None:
        cases = (
            (0, "0 пунктов"),
            (1, "1 пункт"),
            (2, "2 пункта"),
            (4, "4 пункта"),
            (5, "5 пунктов"),
            (11, "11 пунктов"),
            (12, "12 пунктов"),
            (14, "14 пунктов"),
            (21, "21 пункт"),
            (22, "22 пункта"),
            (111, "111 пунктов"),
        )
        for count, want in cases:
            with self.subTest(n=count):
                self.assertEqual(plural(count, "пункт", "пункта", "пунктов"), want)


class SeverityShiftTest(unittest.TestCase):
    def test_направление(self) -> None:
        cases = (
            ("major", "major", "author", ("same", "none")),
            ("minor", "critical", "side", ("raised", "side")),
            ("critical", "minor", "moderator", ("lowered", "moderator")),
            ("minor", "none", "side", ("dropped", "side")),
            (None, "major", "moderator", ("new", "moderator")),
        )
        for base, final, by, shifted in cases:
            with self.subTest(base=base, final=final):
                self.check_shift(base, final, by, shifted)

    def check_shift(self, base: str | None, final: str, by: str, shifted: tuple) -> None:
        expected = shift_of(base, final, *shifted)
        self.assertEqual(severity_shift(base, final, by), expected)


class PipelineTest(OutcomeCase):
    def test_конвейер_прогона(self) -> None:
        debate_dir = self.debate()
        self.outcome(debate_dir)
        self.assertEqual(self.final(debate_dir)["meta"]["pipeline"], "external")
        manifest = self.run_dir / "manifest.json"
        write_json(manifest, {"tier": "standard"})
        self.addCleanup(manifest.unlink)
        self.outcome(debate_dir)
        self.assertEqual(self.final(debate_dir)["meta"]["pipeline"], "cross-review")


class DebateDirTest(unittest.TestCase):
    """The debate directory: <run_dir>/debate/ unless given."""

    def setUp(self) -> None:
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.run_dir = copy_fixture("run-basic", Path(workspace.name).resolve() / "run")

    def test_каталог_спора_по_умолчанию_в_прогоне(self) -> None:
        self.assertEqual(run("debate_build", self.run_dir).returncode, 0)
        res = run("debate_outcome", self.run_dir)
        self.assertEqual(res.returncode, 0, res.stderr)
        debate = self.run_dir / "debate"
        final = debate / "final.json"
        self.assertEqual(last_json_line(res.stdout)["final"], str(final))
        self.assertEqual(read_json(final)["meta"]["debate_dir"], str(debate))

    def test_каталог_спора_задаётся_явно(self) -> None:
        other = self.run_dir.parent / "debate"
        self.assertEqual(run("debate_build", self.run_dir, other).returncode, 0)
        res = run("debate_outcome", self.run_dir, other)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue((other / "final.json").is_file())
        self.assertFalse((self.run_dir / "debate").exists())


if __name__ == "__main__":
    unittest.main()
