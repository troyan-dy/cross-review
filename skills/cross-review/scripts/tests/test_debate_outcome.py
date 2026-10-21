from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from support import copy_fixture, last_json_line, read_json, run, write_json

import debate_outcome


def verdict(fid: str, stance: str, severity: str) -> dict:
    return {"id": fid, "stance": stance, "severity": severity, "argument": "Синтетический довод.", "evidence": "app/x.py:1"}


def response(fid: str, position: str, severity: str) -> dict:
    return {"id": fid, "position": position, "severity": severity, "argument": "Синтетический ответ.", "evidence": "app/x.py:2"}


def decision(fid: str, severity: str, **extra: object) -> dict:
    return {"id": fid, "verdict": "прав Codex", "severity": severity, "note": "Синтетическое решение.", **extra}


class OutcomeCase(unittest.TestCase):
    """The debate of run-basic, built once; every test answers it on a fresh copy.

    Items: bugs-claude-1 (both sides, major, consensus), bugs-claude-2 (Claude, major), arch-claude-1 (Claude,
    refined minor), arch-claude-2 (Claude, pre_existing major), bugs-codex-2 (Codex, critical), arch-codex-1 (Codex,
    immaterial minor), bugs-codex-3 (Codex, rejected_by_verify major), arch-codex-2 (Codex, dropped_by_synthesis
    minor), arch-claude-4 (both sides, immaterial minor); open question arch-claude-3 for Codex.
    """

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.run_dir = copy_fixture("run-basic", Path(cls.tmp.name).resolve() / "run")
        res = run("debate_build", cls.run_dir)
        if res.returncode != 0:
            raise AssertionError(res.stdout + res.stderr)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.tmp.cleanup()

    def debate(self, r1: dict | None = None, r2: dict | None = None, moderator: list | None = None,
               extra: list | None = None) -> Path:
        """A fresh copy of the built debate with the given answers: r1 {examiner: verdicts}, r2 {author: responses}."""
        d = Path(tempfile.mkdtemp(dir=self.tmp.name)) / "debate"
        shutil.copytree(self.run_dir / "debate", d)
        for side, verdicts in (r1 or {}).items():
            write_json(d / f"{side}-r1.json", {"verdicts": verdicts, "open_question_answers": []})
        for side, responses in (r2 or {}).items():
            write_json(d / f"{side}-r2.json", {"responses": responses, "side_findings": []})
        if moderator is not None:
            write_json(d / "moderator.json", moderator)
        if extra is not None:
            write_json(d / "extra.json", extra)
        return d

    def outcome(self, d: Path, *args: str) -> dict:
        res = run("debate_outcome", self.run_dir, d, *args)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return last_json_line(res.stdout) if not args else {"markdown": res.stdout}

    def items(self, d: Path) -> dict:
        return {i["id"]: i for i in read_json(d / "final.json")["items"]}

    def actionable(self, d: Path) -> set[str]:
        return {i["id"] for i in self.items(d).values() if i["final_severity"] != "none"
                and i["outcome"] not in ("dropped", "question", "unexamined") and (i["outcome"] != "disputed" or i["moderated"])
                and i["status"] != "pre_existing"}


# (случай, id, вердикты r1 {проверяющий: (stance, severity)}, ответ автора (position, severity), решение модератора,
#  ждём outcome, final_severity, direction, by)
CASES = [
    ("консенсус", "bugs-claude-1", {}, None, None, "consensus", "major", "same", "none"),
    ("подтверждено", "bugs-claude-2", {"codex": ("real", "major")}, None, None, "confirmed", "major", "same", "none"),
    ("восстановлено после отсева верификацией", "bugs-codex-3", {"claude": ("real", "major")}, None, None,
     "restored", "major", "raised", "side"),
    ("восстановлено после отсева синтезом с новой severity", "arch-codex-2", {"claude": ("real_but_different", "major")},
     ("concede", "major"), None, "restored", "major", "raised", "side"),
    ("восстановлено обеими сторонами, берётся строже", "arch-claude-4",
     {"claude": ("real", "minor"), "codex": ("real_but_different", "major")}, None, None, "restored", "major", "raised", "side"),
    ("уточнено: автор согласился с severity проверяющего", "bugs-claude-2", {"codex": ("real", "minor")},
     ("concede", "minor"), None, "refined", "minor", "lowered", "side"),
    ("уточнено: amend на severity проверяющего", "arch-claude-2", {"codex": ("real_but_different", "minor")},
     ("amend", "minor"), None, "refined", "minor", "lowered", "author"),
    ("дыра amend: major → minor без модератора — спор", "bugs-claude-2", {"codex": ("real_but_different", "minor")},
     ("amend", "major"), None, "disputed", "major", "same", "none"),
    ("дыра amend: своя третья severity — спор", "bugs-codex-2", {"claude": ("real_but_different", "minor")},
     ("amend", "major"), None, "disputed", "critical", "same", "none"),
    ("спорный amend решил модератор", "bugs-claude-2", {"codex": ("real_but_different", "minor")},
     ("amend", "major"), "minor", "disputed", "minor", "lowered", "moderator"),
    ("спор: автор настаивает, модератора нет", "bugs-codex-2", {"claude": ("not_real", "none")},
     ("defend", "critical"), None, "disputed", "critical", "same", "none"),
    ("спор: автор не ответил", "bugs-codex-2", {"claude": ("not_real", "none")}, None, None,
     "disputed", "critical", "same", "none"),
    ("спор снят модератором", "bugs-codex-2", {"claude": ("not_real", "none")}, ("defend", "critical"), "none",
     "disputed", "none", "dropped", "moderator"),
    ("спор модератор повысил", "arch-claude-1", {"codex": ("not_worth_fixing", "none")}, ("defend", "minor"), "major",
     "disputed", "major", "raised", "moderator"),
    ("спор обеих сторон по пункту, отсеянному конвейером", "arch-claude-4",
     {"claude": ("real", "minor"), "codex": ("not_real", "none")}, None, None, "disputed", "none", "same", "none"),
    ("снято: автор согласился", "arch-claude-1", {"codex": ("not_worth_fixing", "none")}, ("concede", "none"), None,
     "dropped", "none", "dropped", "side"),
    ("снято: amend на none вслед за проверяющим", "bugs-claude-2", {"codex": ("not_real", "none")}, ("amend", "none"), None,
     "dropped", "none", "dropped", "author"),
    ("отложенный остаётся отложенным: автор согласился", "arch-codex-1", {"claude": ("not_worth_fixing", "none")},
     ("concede", "none"), None, "dropped", "none", "same", "none"),
    ("отложенный остаётся отложенным: обе стороны", "arch-claude-4",
     {"claude": ("not_real", "none"), "codex": ("not_worth_fixing", "none")}, None, None, "dropped", "none", "same", "none"),
    ("отложенный спор модератор вернул", "bugs-codex-3", {"claude": ("not_real", "none")}, ("defend", "major"), "major",
     "disputed", "major", "raised", "moderator"),
    ("не проверено: вердикта нет", "bugs-claude-2", {}, None, None, "unexamined", "major", "same", "none"),
    ("не проверено обеими сторонами", "arch-claude-4", {}, None, None, "unexamined", "none", "same", "none"),
    ("общий пункт: одна сторона вернула real — не решено", "arch-claude-4", {"claude": ("real", "major")}, None, None,
     "unexamined", "none", "same", "none"),
    ("общий пункт: одна сторона сняла — не решено", "arch-claude-4", {"codex": ("not_real", "none")}, None, None,
     "unexamined", "none", "same", "none"),
    ("не проверено: отложенный не идёт к исправлению", "bugs-codex-3", {}, None, None, "unexamined", "none", "same", "none"),
]
AUTHOR = {"bugs-claude-2": "claude", "arch-claude-1": "claude", "arch-claude-2": "claude", "bugs-codex-2": "codex",
          "arch-codex-1": "codex", "bugs-codex-3": "codex", "arch-codex-2": "codex"}


class OutcomesTest(OutcomeCase):
    def test_исходы_по_таблице(self) -> None:
        for label, fid, r1, answer, moderated, outcome, final, direction, by in CASES:
            with self.subTest(case=label):
                r2 = {AUTHOR[fid]: [response(fid, *answer)]} if answer else None
                moderator = [decision(fid, moderated)] if moderated else None
                d = self.debate({side: [verdict(fid, *v)] for side, v in r1.items()}, r2, moderator)
                summary = self.outcome(d)
                item = self.items(d)[fid]
                self.assertEqual((item["outcome"], item["final_severity"]), (outcome, final))
                kept = "none" if item["status"] in debate_outcome.SET_ASIDE else item["severity"]
                self.assertEqual(item["severity_shift"], {"from": kept, "to": final, "direction": direction, "by": by})
                self.assertIs(item["moderated"], moderated is not None)
                self.assertIs(fid in summary["needs_moderator"], outcome == "disputed" and moderated is None)
                settled = outcome not in ("dropped", "question", "unexamined") and (outcome != "disputed" or moderated is not None)
                to_fix = final != "none" and settled and item["status"] != "pre_existing"
                self.assertEqual(to_fix, fid in self.actionable(d))
                self.assertEqual(final != "none" and outcome != "dropped" and item["status"] == "pre_existing",
                                 fid in summary["pre_existing"])
                self.assertEqual(summary["actionable"], len(self.actionable(d)))

    def test_amend_с_другой_severity_уходит_модератору_с_обеими_позициями(self) -> None:
        d = self.debate({"codex": [verdict("bugs-claude-2", "real_but_different", "minor")]},
                        {"claude": [response("bugs-claude-2", "amend", "major")]})
        summary = self.outcome(d)
        self.assertEqual(summary["needs_moderator"], ["bugs-claude-2"])
        dispute = read_json(d / "disputes.json")["disputes"][0]
        self.assertEqual(dispute["id"], "bugs-claude-2")
        self.assertEqual([t["kind"] for t in dispute["thread"]], ["finding", "pipeline", "examine", "respond"])
        self.assertEqual((dispute["thread"][2]["severity"], dispute["thread"][3]["severity"]), ("minor", "major"))

    def test_найдено_в_споре(self) -> None:
        extra = [{"id": "codex-side-1", "file": "app/api.py", "line": 70, "title": "Утечка соединения",
                  "body": "Соединение не закрывается.", "fix": "Закрыть в finally.", "severity": "major",
                  "sources": ["codex"], "vendors": ["codex"], "note": "Проверено по коду."}]
        d = self.debate(extra=extra)
        write_json(d / "codex-r2.json", {"responses": [], "side_findings": [
            {k: extra[0][k] for k in ("file", "line", "severity", "title", "body", "fix")} | {"evidence": "app/api.py:70"},
            {"file": "app/db.py", "line": 3, "severity": "minor", "title": "Лишний импорт", "body": "Не нужен.",
             "fix": "Убрать.", "evidence": "app/db.py:3"},
        ]})
        summary = self.outcome(d)
        item = self.items(d)["codex-side-1"]
        self.assertEqual((item["outcome"], item["final_severity"], item["status"]), ("side", "major", "side_finding"))
        self.assertEqual(item["severity_shift"], {"from": None, "to": "major", "direction": "new", "by": "moderator"})
        # extra.json exists: the moderator saw both side findings and confirmed only the first
        self.assertEqual((summary["side_findings_pending"], summary["side_findings_rejected"]), ([], ["codex-side-2"]))
        self.assertEqual(read_json(d / "disputes.json")["side_findings"], [])
        self.assertNotIn("codex-side-1", sum(summary["reprioritized"].values(), []))
        text = self.outcome(d, "--markdown")["markdown"]
        self.assertNotIn("без проверки модератора", text)
        self.assertIn("которые модератор не подтвердил, 1:** `codex-side-2`", text)

    def test_попутная_находка_ждёт_модератора_до_extra(self) -> None:
        d = self.debate()
        write_json(d / "codex-r2.json", {"responses": [], "side_findings": [
            {"file": "app/db.py", "line": 3, "severity": "minor", "title": "Лишний импорт", "body": "Не нужен.",
             "fix": "Убрать.", "evidence": "app/db.py:3"}]})
        summary = self.outcome(d)
        self.assertEqual((summary["side_findings_pending"], summary["side_findings_rejected"]), (["codex-side-1"], []))

    def test_попутная_находка_без_vendors_берёт_сторону_из_id(self) -> None:
        extra = [{"id": "codex-side-1", "file": "app/api.py", "line": 70, "title": "Утечка", "body": "Тело.",
                  "fix": "Закрыть.", "severity": "major", "note": "Проверено."}]
        d = self.debate(extra=extra)
        write_json(d / "codex-r2.json", {"responses": [], "side_findings": [
            {k: extra[0][k] for k in ("file", "line", "severity", "title", "body", "fix")} | {"evidence": "app/api.py:70"}]})
        self.outcome(d)
        item = self.items(d)["codex-side-1"]
        self.assertEqual((item["vendors"], item["sources"], item["thread"][0]["side"]), (["codex"], ["codex"], "codex"))

    def test_модератор_не_подменяет_попутную_находку_под_её_id(self) -> None:
        extra = [{"id": "codex-side-1", "file": "app/other.py", "line": 1, "title": "Чужая находка", "body": "Другое.",
                  "fix": "Другое.", "severity": "critical", "note": "Проверено."}]
        d = self.debate(extra=extra)
        write_json(d / "codex-r2.json", {"responses": [], "side_findings": [
            {"file": "app/api.py", "line": 70, "severity": "major", "title": "Утечка", "body": "Тело.", "fix": "Закрыть.",
             "evidence": "app/api.py:70"}]})
        self.outcome(d)
        item = self.items(d)["codex-side-1"]
        self.assertEqual({k: item[k] for k in ("file", "line", "title", "body", "fix")},
                         {"file": "app/api.py", "line": 70, "title": "Утечка", "body": "Тело.", "fix": "Закрыть."})
        # серьёзность назначает модератор
        self.assertEqual(item["final_severity"], "critical")

    def test_модератор_не_придумывает_попутную_находку(self) -> None:
        extra = [{"id": "codex-side-99", "file": "app/api.py", "line": 70, "title": "Утечка", "body": "Тело.",
                  "fix": "Закрыть.", "severity": "critical", "note": "Проверено."}]
        d = self.debate(extra=extra)
        res = run("debate_outcome", self.run_dir, d)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("codex-side-99 is no side finding of round 2", last_json_line(res.stdout)["error"])
        self.assertFalse((d / "final.json").exists())

    def test_без_входа_debate_build_ошибка(self) -> None:
        for name in ("items.json", "for-claude.json", "for-codex.json"):
            with self.subTest(file=name):
                d = self.debate()
                (d / name).unlink()
                res = run("debate_outcome", self.run_dir, d)
                self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
                self.assertIn(f"no {name}, run debate_build.py first", last_json_line(res.stdout)["error"])
                self.assertFalse((d / "final.json").exists())

    def test_вход_debate_build_не_той_формы_ошибка(self) -> None:
        cases = [("items.json", {}, "items.json: expected array"),
                 ("items.json", [{}], "items.json[0].id: missing"),
                 ("items.json", [{"id": "a", "sources": [], "vendors": ["claude", "codex"]}], "items.json[0].title: missing"),
                 ("items.json", [{"id": "a", "file": "a.py", "line": 1, "severity": ["major"], "title": "T", "body": "B",
                                  "sources": ["bugs-claude"], "vendors": ["claude"], "status": "confirmed"}], "items.json[0].severity: expected string"),
                 ("items.json", [{"id": "a", "file": "a.py", "line": 1, "severity": "major", "title": "T", "body": "B",
                                  "sources": ["bugs-claude"], "vendors": "claude", "status": "confirmed"}], "items.json[0].vendors: expected array"),
                 ("for-codex.json", {"findings": [], "open_questions": [{"id": "q", "vendors": [1]}]},
                  "for-codex.json.open_questions[0].file: missing"),
                 ("for-claude.json", [], "for-claude.json: expected object"),
                 ("for-codex.json", {"findings": [{}], "open_questions": []}, "for-codex.json.findings[0].id: missing")]
        for name, value, needle in cases:
            with self.subTest(file=name, value=value):
                d = self.debate()
                write_json(d / name, value)
                res = run("debate_outcome", self.run_dir, d)
                self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
                self.assertIn(needle, last_json_line(res.stdout)["error"])
                self.assertFalse((d / "final.json").exists())

    def legacy(self, r1: dict | None = None) -> Path:
        """The built debate plus an item a source outside the roster raised, which both sides examine."""
        d = self.debate(r1)
        items = read_json(d / "items.json")
        item = {**next(i for i in items if i["id"] == "bugs-claude-2"), "id": "legacy-agent-1",
                "sources": ["legacy-agent"], "vendors": ["legacy-agent"]}
        write_json(d / "items.json", [*items, item])
        for side in ("claude", "codex"):
            f = read_json(d / f"for-{side}.json")
            write_json(d / f"for-{side}.json", {**f, "findings": [*f["findings"], item]})
        return d

    def test_находка_источника_вне_ростера_ждёт_обе_стороны(self) -> None:
        cases = [("без вердиктов", {}, "unexamined", "major"),
                 ("один вердикт", {"codex": [verdict("legacy-agent-1", "real", "major")]}, "unexamined", "major"),
                 ("оба подтвердили", {"claude": [verdict("legacy-agent-1", "real", "minor")],
                                      "codex": [verdict("legacy-agent-1", "real", "major")]}, "confirmed", "major"),
                 ("оба отклонили", {"claude": [verdict("legacy-agent-1", "not_real", "none")],
                                    "codex": [verdict("legacy-agent-1", "not_worth_fixing", "none")]}, "dropped", "none")]
        for label, r1, outcome, severity in cases:
            with self.subTest(case=label):
                d = self.legacy(r1)
                summary = self.outcome(d)
                item = self.items(d)["legacy-agent-1"]
                self.assertEqual((item["outcome"], item["final_severity"]), (outcome, severity))
                self.assertEqual(item["thread"][0]["side"], "legacy-agent")
                self.assertEqual(summary["items"], 10)
        self.assertIn("`legacy-agent-1` [major]", self.outcome(self.legacy(), "--markdown")["markdown"])

    def test_findings_json_не_той_формы_ошибка(self) -> None:
        findings = read_json(self.run_dir / "findings.json")
        agents = findings["sources"]["agents"]
        cases = [("agents не список", {**findings, "sources": {**findings["sources"], "agents": {}}},
                  "findings.json.sources.agents: expected array or null"),
                 ("raised строкой", {**findings, "sources": {**findings["sources"], "agents": [{**agents[0], "raised": "3"}, *agents[1:]]}},
                  "findings.json.sources.agents[0].raised: expected integer"),
                 ("модель числом", {**findings, "sources": {**findings["sources"], "agents": [{**agents[0], "actual_model": 5}, *agents[1:]]}},
                  "findings.json.sources.agents[0].actual_model: expected string or null"),
                 ("агент без executor", {**findings, "sources": {**findings["sources"], "agents": [{"name": "x", "raised": 1}]}},
                  "findings.json.sources.agents[0].executor: missing"),
                 ("scope списком", {**findings, "scope": []}, "findings.json.scope: expected object or null"),
                 ("не объект", [], "findings.json: expected object")]
        for label, value, needle in cases:
            with self.subTest(case=label), tempfile.TemporaryDirectory() as tmp:
                run_dir = Path(tmp) / "run"
                shutil.copytree(self.run_dir, run_dir)
                write_json(run_dir / "findings.json", value)
                d = run_dir / "debate"
                res = run("debate_outcome", run_dir, d)
                self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
                self.assertIn(needle, last_json_line(res.stdout)["error"])
                self.assertFalse((d / "final.json").exists())

    def test_без_findings_json_meta_пустая(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            shutil.copytree(self.run_dir, run_dir)
            (run_dir / "findings.json").unlink()
            res = run("debate_outcome", run_dir)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            meta = read_json(run_dir / "debate" / "final.json")["meta"]
            self.assertEqual((meta["agents"], meta["raw"], meta["task"], meta["verify_model"]), ([], 0, None, None))

    def test_stages_null_в_findings_json_meta_без_моделей(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            shutil.copytree(self.run_dir, run_dir)
            findings = read_json(run_dir / "findings.json")
            write_json(run_dir / "findings.json", {**findings, "stats": {**findings["stats"], "stages": None}})
            res = run("debate_outcome", run_dir)
            self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
            meta = read_json(run_dir / "debate" / "final.json")["meta"]
            self.assertEqual((meta["verify_model"], meta["synthesis_model"]), (None, None))

    def test_общая_непроверенная_находка_не_восстановлена(self) -> None:
        d = self.debate({"claude": [verdict("arch-claude-4", "real", "minor")], "codex": [verdict("arch-claude-4", "real", "minor")]})
        items = read_json(d / "items.json")
        next(i for i in items if i["id"] == "arch-claude-4")["status"] = "unverified"
        write_json(d / "items.json", items)
        self.outcome(d)
        item = self.items(d)["arch-claude-4"]
        self.assertEqual((item["outcome"], item["final_severity"]), ("confirmed", "minor"))

    def test_открытый_вопрос(self) -> None:
        d = self.debate()
        write_json(d / "codex-r1.json", {"verdicts": [], "open_question_answers": [
            {"id": "arch-claude-3", "answer": "Да, вызывается дважды.", "evidence": "app/a.py:9"}]})
        summary = self.outcome(d)
        question = read_json(d / "final.json")["questions"][0]
        self.assertEqual((question["id"], question["outcome"], question["final_severity"]), ("arch-claude-3", "question", "none"))
        self.assertEqual((question["asked"], question["answered_by"]), (["codex"], ["codex"]))
        self.assertEqual(summary["questions"], 1)

    def test_модераторы_разошлись_остаётся_спором(self) -> None:
        d = self.debate({"claude": [verdict("bugs-codex-2", "not_real", "none")]},
                        {"codex": [response("bugs-codex-2", "defend", "critical")]},
                        [decision("bugs-codex-2", "critical", split=True)])
        summary = self.outcome(d)
        item = self.items(d)["bugs-codex-2"]
        self.assertEqual((item["outcome"], item["final_severity"], item["moderated"], item["moderators_split"]),
                         ("disputed", "critical", False, True))
        self.assertEqual(item["severity_shift"]["direction"], "same")
        self.assertEqual(item["thread"][-1]["kind"], "moderator")
        self.assertEqual((summary["moderators_split"], summary["needs_moderator"]), (["bugs-codex-2"], []))

    def test_неразрешённое_не_идёт_к_исправлению(self) -> None:
        d = self.debate({"claude": [verdict("bugs-codex-2", "not_real", "none")]},
                        {"codex": [response("bugs-codex-2", "defend", "critical")]})
        summary = self.outcome(d)
        self.assertNotIn("bugs-codex-2", self.actionable(d))
        self.assertIn("bugs-codex-2", summary["needs_moderator"])
        self.assertIn("bugs-claude-2", summary["unexamined"])
        self.assertNotIn("bugs-claude-2", self.actionable(d))
        self.assertEqual(summary["actionable"], len(self.actionable(d)))
        self.assertEqual(summary["by_severity"]["critical"], 0)

    def test_дефект_до_изменения_вне_подсчёта(self) -> None:
        # arch-claude-2 — pre_existing major; Codex согласился, автор уточнил на той же severity: refined, но не к исправлению
        d = self.debate({"codex": [verdict("arch-claude-2", "real_but_different", "minor")]},
                        {"claude": [response("arch-claude-2", "amend", "minor")]})
        summary = self.outcome(d)
        item = self.items(d)["arch-claude-2"]
        self.assertEqual((item["outcome"], item["final_severity"]), ("refined", "minor"))
        self.assertNotIn("arch-claude-2", self.actionable(d))
        self.assertEqual(summary["pre_existing"], ["arch-claude-2"])
        self.assertEqual(summary["actionable"], len(self.actionable(d)))
        text = self.outcome(d, "--markdown")["markdown"]
        head, _, before = text.partition("- **Было до изменения, вне подсчёта, 1:**")
        self.assertNotIn("arch-claude-2", head)
        self.assertIn("`arch-claude-2` [minor]", before)

    def test_снятый_дефект_до_изменения_не_в_списке(self) -> None:
        d = self.debate({"codex": [verdict("arch-claude-2", "not_real", "none")]},
                        {"claude": [response("arch-claude-2", "concede", "none")]})
        summary = self.outcome(d)
        self.assertEqual(summary["pre_existing"], [])
        self.assertNotIn("Было до изменения", self.outcome(d, "--markdown")["markdown"])

    def test_ответ_не_по_схеме_ошибка(self) -> None:
        cases = [
            ("пустой вердикт", "claude-r1.json", {"verdicts": [{}], "open_question_answers": []}, "claude-r1.json.verdicts[0].id: missing"),
            ("ответ без position", "codex-r2.json", {"responses": [{"id": "bugs-codex-2", "severity": "major", "argument": ".", "evidence": "."}],
                                                     "side_findings": []}, "codex-r2.json.responses[0].position: missing"),
            ("решение модератора без severity", "moderator.json", [{"id": "bugs-codex-2", "verdict": "снять", "note": "."}],
             "moderator.json[0].severity: missing"),
            ("попутная без id", "extra.json", [{"file": "a.py"}], "extra.json[0].id: missing"),
        ]
        for label, name, answer, needle in cases:
            with self.subTest(case=label):
                d = self.debate()
                write_json(d / name, answer)
                res = run("debate_outcome", self.run_dir, d)
                self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
                self.assertIn(needle, last_json_line(res.stdout)["error"])
                self.assertFalse((d / "final.json").exists())

    def test_модератор_не_трогает_пункт_без_спора(self) -> None:
        d = self.debate({"codex": [verdict("bugs-claude-2", "real", "major")]}, moderator=[decision("bugs-claude-2", "none")])
        self.outcome(d)
        item = self.items(d)["bugs-claude-2"]
        self.assertEqual((item["outcome"], item["final_severity"], item["moderated"]), ("confirmed", "major", False))


class ReprioritizedTest(OutcomeCase):
    """severity_shift on every item and the reprioritized lists of the summary and the markdown."""

    def answered(self) -> Path:
        return self.debate(
            {"codex": [verdict("bugs-claude-2", "real_but_different", "minor"), verdict("arch-claude-2", "real", "minor"),
                       verdict("arch-claude-1", "not_real", "none")],
             "claude": [verdict("arch-codex-2", "real_but_different", "major"), verdict("arch-codex-1", "not_real", "none"),
                        verdict("bugs-codex-2", "real", "critical"), verdict("bugs-codex-3", "not_real", "none")]},
            {"claude": [response("bugs-claude-2", "amend", "major"), response("arch-claude-2", "concede", "minor"),
                        response("arch-claude-1", "concede", "none")],
             "codex": [response("arch-codex-2", "concede", "major"), response("arch-codex-1", "concede", "none"),
                       response("bugs-codex-3", "defend", "major")]},
            [decision("bugs-codex-3", "major")])

    def test_сводка_reprioritized(self) -> None:
        summary = self.outcome(self.answered())
        # arch-codex-1 was set aside and stays out: leaving it out is no reprioritization
        self.assertEqual(summary["reprioritized"], {"raised": ["arch-codex-2", "bugs-codex-3"], "lowered": ["arch-claude-2"],
                                                    "dropped": ["arch-claude-1"]})
        self.assertEqual(summary["needs_moderator"], ["bugs-claude-2"])

    def test_у_каждого_пункта_severity_shift(self) -> None:
        d = self.answered()
        self.outcome(d)
        for fid, item in self.items(d).items():
            with self.subTest(id=fid):
                shift = item["severity_shift"]
                self.assertEqual(set(shift), {"from", "to", "direction", "by"})
                kept = "none" if item["status"] in debate_outcome.SET_ASIDE else item["severity"]
                self.assertEqual((shift["from"], shift["to"]), (kept, item["final_severity"]))
                self.assertIn(shift["direction"], ("raised", "lowered", "same", "dropped"))
                self.assertEqual(shift["by"] == "none", shift["direction"] == "same")

    def test_markdown_переоценка_приоритетов(self) -> None:
        text = self.outcome(self.answered(), "--markdown")["markdown"]
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
        text = self.outcome(self.debate(), "--markdown")["markdown"]
        self.assertNotIn("Переоценка приоритетов", text)
        self.assertTrue(text.startswith("**Итог: "))


class PluralTest(unittest.TestCase):
    def test_склонение_по_числу(self) -> None:
        cases = [(0, "0 пунктов"), (1, "1 пункт"), (2, "2 пункта"), (4, "4 пункта"), (5, "5 пунктов"), (11, "11 пунктов"),
                 (12, "12 пунктов"), (14, "14 пунктов"), (21, "21 пункт"), (22, "22 пункта"), (111, "111 пунктов")]
        for n, want in cases:
            with self.subTest(n=n):
                self.assertEqual(debate_outcome.plural(n, "пункт", "пункта", "пунктов"), want)


class SeverityShiftTest(unittest.TestCase):
    def test_направление(self) -> None:
        cases = [
            ("major", "major", "author", {"from": "major", "to": "major", "direction": "same", "by": "none"}),
            ("minor", "critical", "side", {"from": "minor", "to": "critical", "direction": "raised", "by": "side"}),
            ("critical", "minor", "moderator", {"from": "critical", "to": "minor", "direction": "lowered", "by": "moderator"}),
            ("minor", "none", "side", {"from": "minor", "to": "none", "direction": "dropped", "by": "side"}),
            (None, "major", "moderator", {"from": None, "to": "major", "direction": "new", "by": "moderator"}),
        ]
        for base, final, by, want in cases:
            with self.subTest(base=base, final=final):
                self.assertEqual(debate_outcome.severity_shift(base, final, by), want)


class PipelineTest(OutcomeCase):
    def test_конвейер_прогона(self) -> None:
        d = self.debate()
        self.outcome(d)
        self.assertEqual(read_json(d / "final.json")["meta"]["pipeline"], "external")
        write_json(self.run_dir / "manifest.json", {"tier": "standard"})
        self.addCleanup((self.run_dir / "manifest.json").unlink)
        self.outcome(d)
        self.assertEqual(read_json(d / "final.json")["meta"]["pipeline"], "cross-review")


class DebateDirTest(unittest.TestCase):
    """The debate directory: <run_dir>/debate/ unless given."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = copy_fixture("run-basic", Path(tmp.name).resolve() / "run")

    def test_каталог_спора_по_умолчанию_в_прогоне(self) -> None:
        self.assertEqual(run("debate_build", self.run_dir).returncode, 0)
        res = run("debate_outcome", self.run_dir)
        self.assertEqual(res.returncode, 0, res.stderr)
        debate = self.run_dir / "debate"
        self.assertEqual(last_json_line(res.stdout)["final"], str(debate / "final.json"))
        self.assertEqual(read_json(debate / "final.json")["meta"]["debate_dir"], str(debate))

    def test_каталог_спора_задаётся_явно(self) -> None:
        other = self.run_dir.parent / "debate"
        self.assertEqual(run("debate_build", self.run_dir, other).returncode, 0)
        res = run("debate_outcome", self.run_dir, other)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue((other / "final.json").is_file())
        self.assertFalse((self.run_dir / "debate").exists())


if __name__ == "__main__":
    unittest.main()
