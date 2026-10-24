from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from support import composed_run, last_json_line, read_json, run, write_json

from crossreview.verify_groups import group
from crossreview.verify_labels import label

AGENTS = ("bugs-claude", "bugs-codex", "arch-claude", "arch-codex")
RUN_KEYS = ["scope", "sources", "findings", "open_questions", "pre_existing", "immaterial", "stats"]


def finding(fid: str, file: str = "app/store.py", sources: tuple = ("bugs-claude",), **over: object) -> dict:
    """A synthesized finding; keyword arguments override its fields."""
    return {"id": fid, "file": file, "line": 10, "end_line": 0, "severity": "major", "confidence": 85,
            "title": f"Находка {fid}", "body": "Описание.", "fix": "Исправление.", "sources": list(sources),
            "lenses": ["bugs"], "verdict": "", "merged_ids": [fid], **over}


# groups by directory: app (c1, c2), app-api (c3, c4) and the thin root+lib (c6 in the root, c5 in lib)
SYNTH = [
    finding("c1", sources=("bugs-claude", "bugs-codex"), confidence=95),
    finding("c2", line=20, sources=("bugs-claude",)),
    finding("c3", file="app/api/handler.py", sources=("bugs-codex",)),
    finding("c4", file="app/api/handler.py", line=30, sources=("arch-claude",), severity="minor"),
    finding("c5", file="lib/util.py", sources=("arch-codex",)),
    finding("c6", file="README.md", sources=("arch-claude",), severity="minor"),
]
QUESTION = finding("q1", file="app/pool.py", sources=("arch-claude",))
OLD = finding("p1", file="app/cache.py", sources=("arch-codex",))


def verdict(fid: str, word: str, **over: object) -> dict:
    return {"id": fid, "verdict": word, "reason": f"Причина для {fid}.", **over}


# one verdict of every kind; c6 gets none and stays unverified
ANSWERS = {
    "verify-app": {"verdicts": [verdict("c1", "confirmed"),
                                verdict("c2", "refined", severity="minor", line=22, title="Уточнённая находка",
                                        confidence=0, body="")]},
    "verify-app-api": {"verdicts": [verdict("c3", "rejected"), verdict("c4", "immaterial")]},
    "verify-root+lib": {"verdicts": [verdict("c5", "pre_existing")]},
}


class VerifyCase(unittest.TestCase):
    """A composed and collected run of the demo task with 2-synthesized.json written by the test."""

    config: dict | None = None

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.run_dir = composed_run(self.root, self.config)
        self.repo = self.run_dir.parents[3]
        for agent in AGENTS:
            write_json(self.run_dir / "agents" / f"{agent}.json", {"findings": []})
        res = run("review_collect", self.run_dir)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def synthesize(self, findings: list | None = None) -> None:
        found = read_json(self.run_dir / "stages" / "1-found.json")
        write_json(self.run_dir / "stages" / "2-synthesized.json",
                   {**found, "findings": SYNTH if findings is None else findings, "open_questions": [QUESTION],
                    "pre_existing": [OLD], "immaterial": []})

    def verify(self, *args: object, code: int = 0) -> dict:
        res = run("review_verify", *args)
        self.assertEqual(res.returncode, code, res.stdout + res.stderr)
        return last_json_line(res.stdout)

    def prepare(self, code: int = 0) -> dict:
        return self.verify("prepare", self.run_dir, code=code)

    def answer(self, answers: dict) -> None:
        for agent, data in answers.items():
            path = self.run_dir / "agents" / f"{agent}.json"
            path.write_text(data if isinstance(data, str) else json.dumps(data, ensure_ascii=False))

    def apply(self, answers: dict | None = None, code: int = 0) -> dict:
        self.answer(ANSWERS if answers is None else answers)
        return self.verify("apply", self.run_dir, code=code)

    def read(self, name: str) -> dict:
        return read_json(self.run_dir / name)

    def events(self, kind: str) -> list[dict]:
        lines = (self.run_dir / "events.jsonl").read_text().splitlines()
        return [e for e in map(json.loads, lines) if e["kind"] == kind]

    def report(self) -> str:
        return (self.run_dir / "report.md").read_text()


class GroupTest(unittest.TestCase):
    """The verify grouping, called directly."""

    def names(self, findings: list[dict], by: str = "dir", limit: int = 6) -> list[tuple]:
        return [(g["name"], g["keys"], [f["id"] for f in g["findings"]]) for g in group(findings, by, limit)]

    def test_по_каталогу_тонкие_сливаются(self) -> None:
        self.assertEqual(self.names(SYNTH), [
            ("app", ["app"], ["c1", "c2"]),
            ("app-api", ["app/api"], ["c3", "c4"]),
            ("root+lib", [".", "lib"], ["c6", "c5"]),
        ])

    def test_по_источнику_без_слияния(self) -> None:
        self.assertEqual(self.names(SYNTH, "source"), [
            ("arch-claude", ["arch-claude"], ["c4", "c6"]),
            ("arch-codex", ["arch-codex"], ["c5"]),
            ("bugs-claude", ["bugs-claude"], ["c1", "c2"]),
            ("bugs-codex", ["bugs-codex"], ["c3"]),
        ])

    def test_предел_групп(self) -> None:
        cases = [
            ("сливаются две самые маленькие", 3, ["arch-claude", "arch-codex+bugs-codex", "bugs-claude"]),
            ("одна группа на всё", 1, ["arch-codex+bugs-codex+arch-claude+1-more"]),
            ("предел не достигнут", 4, ["arch-claude", "arch-codex", "bugs-claude", "bugs-codex"]),
        ]
        for name, limit, expected in cases:
            with self.subTest(name):
                groups = group(SYNTH, "source", limit)
                self.assertEqual([g["name"] for g in groups], expected)
                self.assertEqual(sorted(f["id"] for g in groups for f in g["findings"]), [f["id"] for f in SYNTH])

    def test_имена_групп(self) -> None:
        cases = [
            ("корень", ["."], "root"),
            ("разделители", ["app/api v2"], "app-api-v2"),
            ("больше трёх ключей", ["a", "b", "c", "d", "e"], "a+b+c+2-more"),
        ]
        for name, keys, expected in cases:
            with self.subTest(name):
                self.assertEqual(label(keys), expected)

    def test_совпадение_имён_получает_суффикс(self) -> None:
        findings = [finding("a1", file="app/x/a.py"), finding("a2", file="app/x/b.py"),
                    finding("b1", file="app-x/a.py"), finding("b2", file="app-x/b.py")]
        self.assertEqual([g["name"] for g in group(findings, "dir", 6)], ["app-x", "app-x-2"])

    def test_находка_без_файла_в_корне(self) -> None:
        self.assertEqual(self.names([finding("a", file=""), finding("b", file="top.py")]),
                         [("root", ["."], ["a", "b"])])


class PrepareTest(VerifyCase):
    """prepare writes one prompt per group and prints the launch plan."""

    def test_промты_групп_и_план(self) -> None:
        self.synthesize()
        plan = self.prepare()
        self.assertEqual({k: plan[k] for k in ("skipped", "stage", "vendor", "alias", "effort", "group_by", "findings",
                                               "groups")},
                         {"skipped": False, "stage": "verify", "vendor": "claude", "alias": "opus", "effort": "high",
                          "group_by": "dir", "findings": 6, "groups": 3})
        self.assertEqual([(e["group"], e["findings"]) for e in plan["launch"]],
                         [("app", ["c1", "c2"]), ("app-api", ["c3", "c4"]), ("root+lib", ["c6", "c5"])])
        stages = self.run_dir / "prompts" / "stages"
        self.assertEqual(plan["launch"][0]["prompt"], str(stages / "verify-app.md"))
        self.assertEqual(plan["launch"][2]["output"], str(self.run_dir / "agents" / "verify-root+lib.json"))
        text = (stages / "verify-app-api.md").read_text()
        self.assertIn(f"`{self.run_dir / 'input' / 'scope.md'}`", text)
        self.assertIn("`none provided` — the project's own conventions", text)
        self.assertIn(f"`{self.repo}` — run every command from here", text)
        self.assertIn('"id": "c3"', text)
        self.assertNotIn('"id": "c1"', text)
        self.assertNotIn('"merged_ids"', text.split("Schema:")[0])
        self.assertIn("Return ONLY a JSON object matching the schema below", text)
        self.assertIn('"verdicts"', text)
        self.assertNotIn("{{", text)
        sources = {p["path"]: p["layer"] for p in self.read("manifest.json")["prompts"]}
        self.assertEqual((sources["prompts/verify.md"], sources["schemas/verify.json"]), ("plugin", "plugin"))
        self.assertEqual([e.get("stage") for e in self.events("stage")], ["find", "verify"])

    def test_по_источнику_из_настроек(self) -> None:
        manifest = self.read("manifest.json")
        manifest["stages"].update(verify_group_by="source", verify_groups=2)
        write_json(self.run_dir / "manifest.json", manifest)
        self.synthesize()
        plan = self.prepare()
        self.assertEqual([e["group"] for e in plan["launch"]], ["arch-codex+bugs-codex+arch-claude", "bugs-claude"])

    def test_повторный_prepare_убирает_старые_промты(self) -> None:
        self.synthesize()
        self.prepare()
        self.synthesize(SYNTH[:2])
        plan = self.prepare()
        self.assertEqual([e["group"] for e in plan["launch"]], ["app"])
        self.assertEqual(sorted(p.name for p in (self.run_dir / "prompts" / "stages").glob("verify-*.md")),
                         ["verify-app.md"])

    def test_промт_верификации_из_репозитория(self) -> None:
        own = self.repo / ".cross-review" / "prompts" / "verify.md"
        own.parent.mkdir(parents=True)
        own.write_text("---\ndescription: свой\n---\nСвоя проверка в {{WORKDIR}}.\n\n{{FINDINGS}}\n")
        self.synthesize()
        self.prepare()
        text = (self.run_dir / "prompts" / "stages" / "verify-app.md").read_text()
        self.assertTrue(text.startswith(f"Своя проверка в {self.repo}.\n\n["))
        sources = {p["path"]: p["layer"] for p in self.read("manifest.json")["prompts"]}
        self.assertEqual(sources["prompts/verify.md"], "repo")

    def test_неизвестный_плейсхолдер_ошибка(self) -> None:
        own = self.repo / ".cross-review" / "prompts" / "verify.md"
        own.parent.mkdir(parents=True)
        own.write_text("{{FINDINGS}} {{GOAL}}\n")
        self.synthesize()
        err = self.prepare(code=1)["error"]
        self.assertIn("prompts/verify.md", err)
        self.assertIn("{{GOAL}}", err)

    def test_без_находок_стадия_пропущена(self) -> None:
        self.synthesize([])
        plan = self.prepare()
        self.assertEqual((plan["skipped"], plan["reason"], plan["findings"]), (True, "no finding to verify", 0))
        verified = read_json(self.run_dir / "stages" / "3-verified.json")
        self.assertEqual(verified["stats"]["stages"][-1], {"name": "verify", "duration_ms": 0})
        self.assertEqual(list(self.read("findings.json")), RUN_KEYS)
        self.assertIn("Находок нет.", self.report())
        self.assertIn("no finding to verify", self.apply({}, code=1)["error"])

    def test_ошибки_прогона(self) -> None:
        with self.subTest("без синтеза"):
            self.assertIn("review_synth.py", self.prepare(code=1)["error"])
        self.synthesize()
        with self.subTest("спор уже начат"):
            (self.run_dir / "debate").mkdir()
            self.assertIn("debate already started", self.prepare(code=1)["error"])
            self.assertIn("debate already started", self.apply(code=1)["error"])


class ApplyTest(VerifyCase):
    """apply routes every verdict and writes 3-verified.json, findings.json and report.md."""

    def setUp(self) -> None:
        super().setUp()
        self.synthesize()
        self.prepare()

    def test_каждый_вердикт(self) -> None:
        res = self.apply()
        self.assertEqual({k: res[k] for k in ("groups", "degraded", "confirmed", "refined", "rejected", "immaterial",
                                              "pre_existing", "unverified", "ignored", "findings")},
                         {"groups": 3, "degraded": [], "confirmed": 1, "refined": 1, "rejected": ["c3"],
                          "immaterial": ["c4"], "pre_existing": ["c5"], "unverified": ["c6"], "ignored": [],
                          "findings": 3})
        verified = read_json(self.run_dir / "stages" / "3-verified.json")
        routes = {name: [(f["id"], f["verdict"]) for f in verified[name]]
                  for name in ("findings", "pre_existing", "immaterial", "open_questions")}
        self.assertEqual(routes, {
            "findings": [("c1", "confirmed"), ("c2", "refined"), ("c6", "unverified")],
            "pre_existing": [("p1", ""), ("c5", "pre_existing")],
            "immaterial": [("c4", "immaterial")],
            "open_questions": [("q1", "")],
        })
        self.assertEqual([s["name"] for s in verified["stats"]["stages"]], ["find", "verify"])
        self.assertEqual({k: verified["stats"]["stages"][-1][k] for k in ("executor", "model", "effort")},
                         {"executor": "claude", "model": "opus", "effort": "high"})
        [rejected] = self.events("rejected")
        self.assertEqual((rejected["agent"], rejected["findings"][0]["id"], rejected["findings"][0]["reason"]),
                         ("verify-app-api", "c3", "Причина для c3."))
        done = {e["agent"]: e["text"] for e in self.events("agent_done") if e.get("stage") == "verify"}
        self.assertEqual(done, {"verify-app": "2 of 2 kept, 1 lowered, 1 refined", "verify-app-api": "1 of 2 kept",
                                "verify-root+lib": "2 of 2 kept"})

    def test_правки_refined(self) -> None:
        self.apply()
        refined = read_json(self.run_dir / "stages" / "3-verified.json")["findings"][1]
        self.assertEqual({k: refined[k] for k in ("id", "severity", "line", "title", "confidence", "body", "fix")},
                         {"id": "c2", "severity": "minor", "line": 22, "title": "Уточнённая находка", "confidence": 85,
                          "body": "Описание.", "fix": "Исправление."})

    def test_правки_вне_схемы_не_применяются(self) -> None:
        self.apply({"verify-app": {"verdicts": [
            verdict("c1", "refined", severity="blocker", confidence=150, line=-3, end_line=True, fix="Новое."),
            verdict("c2", "confirmed", severity="critical", title="Не меняется")]}})
        c1, c2 = read_json(self.run_dir / "stages" / "3-verified.json")["findings"][:2]
        self.assertEqual({k: c1[k] for k in ("verdict", "severity", "confidence", "line", "end_line", "fix")},
                         {"verdict": "refined", "severity": "major", "confidence": 95, "line": 10, "end_line": 0,
                          "fix": "Новое."})
        self.assertEqual((c2["verdict"], c2["severity"], c2["title"]), ("confirmed", "major", "Находка c2"))

    def test_без_вердикта_и_чужие_id(self) -> None:
        res = self.apply({"verify-app": {"verdicts": [verdict("c1", "maybe"), verdict("c3", "rejected"), "мусор"]},
                          **{k: v for k, v in ANSWERS.items() if k != "verify-app"}})
        self.assertEqual(res["unverified"], ["c1", "c2", "c6"])
        self.assertEqual(res["ignored"], ["c3", "'мусор'"])
        self.assertEqual(res["rejected"], ["c3"])

    def test_пропущенная_группа_даёт_unverified(self) -> None:
        cases = [
            ("нет ответа", None, "no answer: verify-app.json is missing"),
            ("не JSON", "проверить не удалось", "answer is not JSON"),
            ("нет verdicts", {"findings": []}, "expected an object with a verdicts array"),
        ]
        for name, data, reason in cases:
            with self.subTest(name):
                path = self.run_dir / "agents" / "verify-app.json"
                answers = {k: v for k, v in ANSWERS.items() if k != "verify-app"}
                if data is None:
                    path.unlink(missing_ok=True)
                else:
                    answers["verify-app"] = data
                res = self.apply(answers)
                [degraded] = res["degraded"]
                self.assertEqual(degraded["group"], "app")
                self.assertIn(reason, degraded["reason"])
                self.assertEqual(res["unverified"], ["c1", "c2", "c6"])
                [event] = self.events("agent_degraded")
                self.assertEqual((event["agent"], event["text"]), ("verify-app", degraded["reason"]))
                self.assertIn("Не ответили верификаторы групп app: их находки остались непроверенными.", self.report())

    def test_ответ_в_прозе(self) -> None:
        body = json.dumps(ANSWERS["verify-app"], ensure_ascii=False, indent=2)
        res = self.apply({**ANSWERS, "verify-app": f"Проверил обе находки.\n\n```json\n{body}\n```\n"})
        self.assertEqual((res["confirmed"], res["refined"]), (1, 1))

    def test_повторный_apply_заменяет_события(self) -> None:
        self.apply({})
        self.apply()
        self.assertEqual(self.events("agent_degraded"), [])
        self.assertEqual(len(self.events("rejected")), 1)
        self.assertEqual(len([e for e in self.events("agent_done") if e.get("stage") == "verify"]), 3)
        self.assertEqual([e.get("stage") for e in self.events("stage")], ["find", "verify"])

    def test_findings_json_в_формате_прогона(self) -> None:
        self.apply()
        final = self.read("findings.json")
        self.assertEqual(list(final), RUN_KEYS)
        verified = read_json(self.run_dir / "stages" / "3-verified.json")
        for name in RUN_KEYS[:-1]:
            self.assertEqual(final[name], verified[name])
        self.assertEqual(list(final["stats"]), ["started_at", "finished_at", "duration_ms", "tokens", "stages"])
        self.assertEqual(final["stats"]["started_at"], self.read("manifest.json")["started_at"])

    def test_report_md_по_русски(self) -> None:
        self.apply()
        text = self.report()
        self.assertTrue(text.startswith(f"# Ревью: demo / 01-initial\n\nПредмет: `{self.run_dir / 'input' / 'scope.md'}`"))
        self.assertIn("Верификация: подтверждено 1, уточнено 1, отклонено 1, несущественно 1, было до изменений 1, "
                      "не проверено 1.", text)
        sections = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(sections, ["## Серьёзные", "## Незначительные", "## Открытые вопросы", "## Было до изменений",
                                    "## Несущественное", "## Источники"])
        self.assertIn("### Уточнённая находка\n\n`app/store.py:22`\n\nОписание.\n\nИсправление: Исправление.\n\n"
                      "_id: c2 | уверенность: 85 | источники: bugs-claude | линзы: bugs | вердикт: уточнено_", text)
        self.assertIn("вердикт: не проверено", text)
        self.assertNotIn("Находка c3", text)
        self.assertIn("| bugs-claude | claude | opus | high | 0 | ответил, ничего не нашёл |", text)

    def test_неполный_раунд_в_отчёте(self) -> None:
        found = read_json(self.run_dir / "stages" / "1-found.json")
        found["sources"].update(reported=3, degraded=["bugs-codex"])
        write_json(self.run_dir / "stages" / "1-found.json", found)
        self.synthesize()
        self.apply()
        text = self.report()
        self.assertIn("**Раунд неполный**: ответили 3 из 4 источников, нет bugs-codex", text)
        self.assertIn("| bugs-codex | codex | gpt-6.1-sol | high | 0 | не ответил |", text)

    def test_findings_json_читает_debate_build(self) -> None:
        self.apply()
        debate = self.root / "debate"
        res = run("debate_build", self.run_dir, debate)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        out = last_json_line(res.stdout)
        self.assertTrue(out["verify_ran"])
        status = {i["id"]: i["status"] for i in read_json(debate / "items.json")}
        self.assertEqual(status, {"c1": "confirmed", "c2": "refined", "c6": "unverified", "c4": "immaterial",
                                  "p1": "pre_existing", "c5": "pre_existing", "c3": "rejected_by_verify"})
        self.assertEqual([i["id"] for i in read_json(debate / "consensus.json")], ["c1"])
        self.assertEqual(out["open_questions"], 1)


class StageOffTest(VerifyCase):
    """stages.verify: false marks every finding unverified and writes findings.json without 3-verified.json."""

    config = {"stages": {"verify": False}}

    def test_стадия_выключена(self) -> None:
        self.synthesize()
        plan = self.prepare()
        self.assertEqual((plan["skipped"], plan["reason"], plan["findings"]), (True, "stages.verify is off for this run", 6))
        self.assertFalse((self.run_dir / "stages" / "3-verified.json").exists())
        self.assertFalse(list((self.run_dir / "prompts" / "stages").glob("verify-*.md")))
        final = self.read("findings.json")
        self.assertEqual({f["verdict"] for f in final["findings"]}, {"unverified"})
        self.assertEqual([q["verdict"] for q in final["open_questions"]], [""])
        self.assertIn("Верификация выключена в настройках прогона", self.report())
        self.assertIn("off for this run", self.apply(code=1)["error"])

    def test_debate_build_без_верификации(self) -> None:
        self.synthesize()
        self.prepare()
        res = run("debate_build", self.run_dir, self.root / "debate")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        out = last_json_line(res.stdout)
        self.assertEqual((out["verify_ran"], out["by_status"]), (False, {"unverified": 6, "pre_existing": 1}))


if __name__ == "__main__":
    unittest.main()
