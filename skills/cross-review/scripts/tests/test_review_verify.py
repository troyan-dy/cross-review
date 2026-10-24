from __future__ import annotations

import json
import tempfile
import types
import unittest
from collections.abc import Mapping
from pathlib import Path

from support import assert_fields, composed_run, last_json_line, pick, read_json, run, single, write_json

from crossreview.verify_groups import group
from crossreview.verify_labels import label

AGENTS = ("bugs-claude", "bugs-codex", "arch-claude", "arch-codex")
RUN_KEYS = ("scope", "sources", "findings", "open_questions", "pre_existing", "immaterial", "stats")
STATS_KEYS = ("started_at", "finished_at", "duration_ms", "tokens", "stages")
ROUTED = ("findings", "pre_existing", "immaterial", "open_questions")
API = "app/api/handler.py"
APP_UNVERIFIED = ("c1", "c2", "c6")
LAUNCHED = (
    ("app", ["c1", "c2"]),
    ("app-api", ["c3", "c4"]),
    ("root+lib", ["c6", "c5"]),
)
PLAN = types.MappingProxyType({
    "skipped": False,
    "stage": "verify",
    "vendor": "claude",
    "alias": "opus",
    "effort": "high",
    "group_by": "dir",
    "findings": 6,
    "groups": 3,
})
COUNTS = types.MappingProxyType({
    "groups": 3,
    "degraded": [],
    "confirmed": 1,
    "refined": 1,
    "rejected": ["c3"],
    "immaterial": ["c4"],
    "pre_existing": ["c5"],
    "unverified": ["c6"],
    "ignored": [],
    "findings": 3,
})
ROUTES = types.MappingProxyType({
    "findings": [("c1", "confirmed"), ("c2", "refined"), ("c6", "unverified")],
    "pre_existing": [("p1", ""), ("c5", "pre_existing")],
    "immaterial": [("c4", "immaterial")],
    "open_questions": [("q1", "")],
})
DONE = types.MappingProxyType({
    "verify-app": "2 of 2 kept, 1 lowered, 1 refined",
    "verify-app-api": "1 of 2 kept",
    "verify-root+lib": "2 of 2 kept",
})
REFINED = types.MappingProxyType({
    "id": "c2",
    "severity": "minor",
    "line": 22,
    "title": "Уточнённая находка",
    "confidence": 85,
    "body": "Описание.",
    "fix": "Исправление.",
})
UNCHANGED = types.MappingProxyType({
    "verdict": "refined",
    "severity": "major",
    "confidence": 95,
    "line": 10,
    "end_line": 0,
    "fix": "Новое.",
})
STATUSES = types.MappingProxyType({
    "c1": "confirmed",
    "c2": "refined",
    "c6": "unverified",
    "c4": "immaterial",
    "p1": "pre_existing",
    "c5": "pre_existing",
    "c3": "rejected_by_verify",
})
SECTIONS = ("## Серьёзные", "## Незначительные", "## Открытые вопросы", "## Было до изменений", "## Несущественное",
            "## Источники")
REFINED_CARD = "".join((
    "### Уточнённая находка\n\n`app/store.py:22`\n\nОписание.\n\nИсправление: Исправление.\n\n",
    "_id: c2 | уверенность: 85 | источники: bugs-claude | линзы: bugs | вердикт: уточнено_",
))


def finding(fid: str, sources: tuple = ("bugs-claude",), **over: object) -> dict:
    """A synthesized finding; keyword arguments override its fields."""
    return {
        "id": fid,
        "file": "app/store.py",
        "line": 10,
        "end_line": 0,
        "severity": "major",
        "confidence": 85,
        "title": f"Находка {fid}",
        "body": "Описание.",
        "fix": "Исправление.",
        "sources": list(sources),
        "lenses": ["bugs"],
        "verdict": "",
        "merged_ids": [fid],
        **over,
    }


# groups by directory: app (c1, c2), app-api (c3, c4) and the thin root+lib (c6 in the root, c5 in lib)
SYNTH = (
    finding("c1", sources=("bugs-claude", "bugs-codex"), confidence=95),
    finding("c2", line=20, sources=("bugs-claude",)),
    finding("c3", file=API, sources=("bugs-codex",)),
    finding("c4", file=API, line=30, sources=("arch-claude",), severity="minor"),
    finding("c5", file="lib/util.py", sources=("arch-codex",)),
    finding("c6", file="README.md", sources=("arch-claude",), severity="minor"),
)
QUESTION = finding("q1", file="app/pool.py", sources=("arch-claude",))
OLD = finding("p1", file="app/cache.py", sources=("arch-codex",))


def verdict(fid: str, word: str, **over: object) -> dict:
    return {"id": fid, "verdict": word, "reason": f"Причина для {fid}.", **over}


def verdicts(*given: object) -> dict:
    return {"verdicts": list(given)}


def ids_of(findings: list[dict]) -> list[str]:
    return [listed["id"] for listed in findings]


def names_of(entries: list[dict]) -> list[str]:
    return [entry["name"] for entry in entries]


# one verdict of every kind; c6 gets none and stays unverified
REFINE = verdict(
    "c2",
    "refined",
    severity="minor",
    line=22,
    title="Уточнённая находка",
    confidence=0,
    body="",
)
ANSWERS = types.MappingProxyType({
    "verify-app": verdicts(verdict("c1", "confirmed"), REFINE),
    "verify-app-api": verdicts(verdict("c3", "rejected"), verdict("c4", "immaterial")),
    "verify-root+lib": verdicts(verdict("c5", "pre_existing")),
})


def other_answers() -> dict:
    """ANSWERS without the app group's."""
    return {agent: answer for agent, answer in ANSWERS.items() if agent != "verify-app"}


class VerifyCase(unittest.TestCase):
    """A composed and collected run of the demo task with 2-synthesized.json written by the test."""

    config: dict | None = None

    def setUp(self) -> None:
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name).resolve()
        self.run_dir = composed_run(self.root, self.config)
        self.repo = self.run_dir.parents[3]
        self.stages = self.run_dir / "stages"
        self.prompts = self.run_dir / "prompts" / "stages"
        for agent in AGENTS:
            write_json(self.run_dir / "agents" / f"{agent}.json", {"findings": []})
        self.run_script("review_collect", self.run_dir)

    def run_script(self, script: str, *args: object) -> dict:
        res = run(script, *args)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return last_json_line(res.stdout)

    def synthesize(self, findings: tuple | list = SYNTH) -> None:
        found = read_json(self.stages / "1-found.json")
        lists = {"findings": findings, "open_questions": [QUESTION], "pre_existing": [OLD], "immaterial": []}
        write_json(self.stages / "2-synthesized.json", {**found, **lists})

    def verify(self, *args: object, code: int = 0) -> dict:
        res = run("review_verify", *args)
        self.assertEqual(res.returncode, code, res.stdout + res.stderr)
        return last_json_line(res.stdout)

    def prepare(self, code: int = 0) -> dict:
        return self.verify("prepare", self.run_dir, code=code)

    def prepare_error(self) -> str:
        return self.prepare(code=1)["error"]

    def answer(self, answers: Mapping) -> None:
        for agent, answer in answers.items():
            text = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
            (self.run_dir / "agents" / f"{agent}.json").write_text(text)

    def apply(self, answers: Mapping = ANSWERS, code: int = 0) -> dict:
        self.answer(answers)
        return self.verify("apply", self.run_dir, code=code)

    def apply_error(self, answers: Mapping = ANSWERS) -> str:
        return self.apply(answers, code=1)["error"]

    def read(self, name: str) -> dict:
        return read_json(self.run_dir / name)

    def verified(self) -> dict:
        return read_json(self.stages / "3-verified.json")

    def events(self, kind: str) -> list[dict]:
        lines = (self.run_dir / "events.jsonl").read_text().splitlines()
        return [event for event in map(json.loads, lines) if event["kind"] == kind]

    def event_stages(self) -> list[str | None]:
        return [event.get("stage") for event in self.events("stage")]

    def verify_done(self) -> list[dict]:
        return [event for event in self.events("agent_done") if event.get("stage") == "verify"]

    def report(self) -> str:
        return (self.run_dir / "report.md").read_text()

    def prompt_layers(self) -> dict[str, str]:
        prompts = self.read("manifest.json")["prompts"]
        return {source["path"]: source["layer"] for source in prompts}

    def verify_prompts(self) -> list[str]:
        return sorted(prompt.name for prompt in self.prompts.glob("verify-*.md"))

    def own_prompt(self, text: str) -> None:
        own = self.repo / ".cross-review" / "prompts" / "verify.md"
        own.parent.mkdir(parents=True)
        own.write_text(text)


class GroupTest(unittest.TestCase):
    """The verify grouping, called directly."""

    def names(self, findings: tuple | list, by: str = "dir", limit: int = 6) -> list[tuple]:
        groups = group(findings, by, limit)
        return [self.described(entry) for entry in groups]

    def described(self, entry: dict) -> tuple:
        return (entry["name"], entry["keys"], ids_of(entry["findings"]))

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
        cases = (
            ("сливаются две самые маленькие", 3, ["arch-claude", "arch-codex+bugs-codex", "bugs-claude"]),
            ("одна группа на всё", 1, ["arch-codex+bugs-codex+arch-claude+1-more"]),
            ("предел не достигнут", 4, ["arch-claude", "arch-codex", "bugs-claude", "bugs-codex"]),
        )
        for name, limit, expected in cases:
            with self.subTest(name):
                self.check_limit(limit, expected)

    def check_limit(self, limit: int, expected: list[str]) -> None:
        groups = group(SYNTH, "source", limit)
        self.assertEqual(names_of(groups), expected)
        grouped = [finding_id for entry in groups for finding_id in ids_of(entry["findings"])]
        self.assertEqual(sorted(grouped), ids_of(SYNTH))

    def test_имена_групп(self) -> None:
        cases = (
            ("корень", ["."], "root"),
            ("разделители", ["app/api v2"], "app-api-v2"),
            ("больше трёх ключей", ["a", "b", "c", "d", "e"], "a+b+c+2-more"),
        )
        for name, keys, expected in cases:
            with self.subTest(name):
                self.assertEqual(label(keys), expected)

    def test_совпадение_имён_получает_суффикс(self) -> None:
        findings = [
            finding("a1", file="app/x/a.py"),
            finding("a2", file="app/x/b.py"),
            finding("b1", file="app-x/a.py"),
            finding("b2", file="app-x/b.py"),
        ]
        self.assertEqual(names_of(group(findings, "dir", 6)), ["app-x", "app-x-2"])

    def test_находка_без_файла_в_корне(self) -> None:
        findings = [finding("a", file=""), finding("b", file="top.py")]
        root = ("root", ["."], ["a", "b"])
        self.assertEqual(self.names(findings), [root])


class PrepareTest(VerifyCase):
    """prepare writes one prompt per group and prints the launch plan."""

    def test_промты_групп_и_план(self) -> None:
        self.synthesize()
        plan = self.prepare()
        assert_fields(self, plan, **PLAN)
        launched = [pick(launch, "group", "findings") for launch in plan["launch"]]
        self.assertEqual(launched, list(LAUNCHED))
        first_prompt = self.prompts / "verify-app.md"
        self.assertEqual(plan["launch"][0]["prompt"], str(first_prompt))
        output = self.run_dir / "agents" / "verify-root+lib.json"
        self.assertEqual(plan["launch"][2]["output"], str(output))
        self.check_prompt((self.prompts / "verify-app-api.md").read_text())
        layers = self.prompt_layers()
        self.assertEqual(pick(layers, "prompts/verify.md", "schemas/verify.json"), ("plugin", "plugin"))
        self.assertEqual(self.event_stages(), ["find", "verify"])

    def check_prompt(self, text: str) -> None:
        scope = self.run_dir / "input" / "scope.md"
        self.assertIn(f"`{scope}`", text)
        self.assertIn("`none provided` — the project's own conventions", text)
        self.assertIn(f"`{self.repo}` — run every command from here", text)
        self.assertIn('"id": "c3"', text)
        self.assertNotIn('"id": "c1"', text)
        self.assertNotIn('"merged_ids"', text.split("Schema:")[0])
        self.assertIn("Return ONLY a JSON object matching the schema below", text)
        self.assertIn('"verdicts"', text)
        self.assertNotIn("{{", text)

    def test_по_источнику_из_настроек(self) -> None:
        manifest = self.read("manifest.json")
        manifest["stages"].update(verify_group_by="source", verify_groups=2)
        write_json(self.run_dir / "manifest.json", manifest)
        self.synthesize()
        plan = self.prepare()
        groups = [launch["group"] for launch in plan["launch"]]
        self.assertEqual(groups, ["arch-codex+bugs-codex+arch-claude", "bugs-claude"])

    def test_повторный_prepare_убирает_старые_промты(self) -> None:
        self.synthesize()
        self.prepare()
        self.synthesize(SYNTH[:2])
        plan = self.prepare()
        self.assertEqual([launch["group"] for launch in plan["launch"]], ["app"])
        self.assertEqual(self.verify_prompts(), ["verify-app.md"])

    def test_промт_верификации_из_репозитория(self) -> None:
        self.own_prompt("---\ndescription: свой\n---\nСвоя проверка в {{WORKDIR}}.\n\n{{FINDINGS}}\n")
        self.synthesize()
        self.prepare()
        text = (self.prompts / "verify-app.md").read_text()
        self.assertTrue(text.startswith(f"Своя проверка в {self.repo}.\n\n["))
        self.assertEqual(self.prompt_layers()["prompts/verify.md"], "repo")

    def test_неизвестный_плейсхолдер_ошибка(self) -> None:
        self.own_prompt("{{FINDINGS}} {{GOAL}}\n")
        self.synthesize()
        err = self.prepare_error()
        self.assertIn("prompts/verify.md", err)
        self.assertIn("{{GOAL}}", err)

    def test_без_находок_стадия_пропущена(self) -> None:
        self.synthesize([])
        plan = self.prepare()
        assert_fields(self, plan, skipped=True, reason="no finding to verify", findings=0)
        last_stage = self.verified()["stats"]["stages"][-1]
        self.assertEqual(last_stage, {"name": "verify", "duration_ms": 0})
        self.assertEqual(tuple(self.read("findings.json")), RUN_KEYS)
        self.assertIn("Находок нет.", self.report())
        self.assertIn("no finding to verify", self.apply_error({}))

    def test_ошибки_прогона(self) -> None:
        with self.subTest("без синтеза"):
            self.assertIn("review_synth.py", self.prepare_error())
        self.synthesize()
        with self.subTest("спор уже начат"):
            (self.run_dir / "debate").mkdir()
            self.assertIn("debate already started", self.prepare_error())
            self.assertIn("debate already started", self.apply_error())


class ApplyTest(VerifyCase):
    """apply routes every verdict and writes 3-verified.json, findings.json and report.md."""

    def setUp(self) -> None:
        super().setUp()
        self.synthesize()
        self.prepare()

    def test_каждый_вердикт(self) -> None:
        res = self.apply()
        assert_fields(self, res, **COUNTS)
        verified = self.verified()
        self.assertEqual(self.routes(verified), ROUTES)
        stages = verified["stats"]["stages"]
        self.assertEqual(names_of(stages), ["find", "verify"])
        assert_fields(self, stages[-1], executor="claude", model="opus", effort="high")
        rejected = single(self.events("rejected"))
        self.assertEqual(rejected["agent"], "verify-app-api")
        assert_fields(self, rejected["findings"][0], id="c3", reason="Причина для c3.")
        done = {event["agent"]: event["text"] for event in self.verify_done()}
        self.assertEqual(done, DONE)

    def routes(self, verified: dict) -> dict[str, list[tuple]]:
        return {name: self.verdicts_of(verified[name]) for name in ROUTED}

    def verdicts_of(self, routed: list[dict]) -> list[tuple]:
        return [pick(entry, "id", "verdict") for entry in routed]

    def test_правки_refined(self) -> None:
        self.apply()
        refined = self.verified()["findings"][1]
        assert_fields(self, refined, **REFINED)

    def test_правки_вне_схемы_не_применяются(self) -> None:
        out_of_schema = verdict(
            "c1",
            "refined",
            severity="blocker",
            confidence=150,
            line=-3,
            end_line=True,
            fix="Новое.",
        )
        unchanged = verdict("c2", "confirmed", severity="critical", title="Не меняется")
        self.apply({"verify-app": verdicts(out_of_schema, unchanged)})
        first, second = self.verified()["findings"][:2]
        assert_fields(self, first, **UNCHANGED)
        assert_fields(self, second, verdict="confirmed", severity="major", title="Находка c2")

    def test_без_вердикта_и_чужие_id(self) -> None:
        app = verdicts(verdict("c1", "maybe"), verdict("c3", "rejected"), "мусор")
        res = self.apply({"verify-app": app, **other_answers()})
        self.assertEqual(res["unverified"], list(APP_UNVERIFIED))
        self.assertEqual(res["ignored"], ["c3", "'мусор'"])
        self.assertEqual(res["rejected"], ["c3"])

    def test_пропущенная_группа_даёт_unverified(self) -> None:
        cases = (
            ("нет ответа", None, "no answer: verify-app.json is missing"),
            ("не JSON", "проверить не удалось", "answer is not JSON"),
            ("нет verdicts", {"findings": []}, "expected an object with a verdicts array"),
        )
        for name, answer, reason in cases:
            with self.subTest(name):
                self.check_missing_group(answer, reason)

    def check_missing_group(self, answer: object, reason: str) -> None:
        answers = other_answers()
        if answer is None:
            (self.run_dir / "agents" / "verify-app.json").unlink(missing_ok=True)
        else:
            answers["verify-app"] = answer
        res = self.apply(answers)
        degraded = single(res["degraded"])
        self.assertEqual(degraded["group"], "app")
        self.assertIn(reason, degraded["reason"])
        self.assertEqual(res["unverified"], list(APP_UNVERIFIED))
        event = single(self.events("agent_degraded"))
        assert_fields(self, event, agent="verify-app", text=degraded["reason"])
        self.assertIn("Не ответили верификаторы групп app: их находки остались непроверенными.", self.report())

    def test_ответ_в_прозе(self) -> None:
        body = json.dumps(ANSWERS["verify-app"], ensure_ascii=False, indent=2)
        res = self.apply({**ANSWERS, "verify-app": f"Проверил обе находки.\n\n```json\n{body}\n```\n"})
        assert_fields(self, res, confirmed=1, refined=1)

    def test_повторный_apply_заменяет_события(self) -> None:
        self.apply({})
        self.apply()
        self.assertEqual(self.events("agent_degraded"), [])
        self.assertEqual(len(self.events("rejected")), 1)
        self.assertEqual(len(self.verify_done()), 3)
        self.assertEqual(self.event_stages(), ["find", "verify"])

    def test_findings_json_в_формате_прогона(self) -> None:
        self.apply()
        final = self.read("findings.json")
        self.assertEqual(tuple(final), RUN_KEYS)
        verified = self.verified()
        for name in RUN_KEYS[:-1]:
            self.assertEqual(final[name], verified[name])
        stats = final["stats"]
        self.assertEqual(tuple(stats), STATS_KEYS)
        self.assertEqual(stats["started_at"], self.read("manifest.json")["started_at"])

    def test_report_md_по_русски(self) -> None:
        self.apply()
        text = self.report()
        scope = self.run_dir / "input" / "scope.md"
        self.assertTrue(text.startswith(f"# Ревью: demo / 01-initial\n\nПредмет: `{scope}`"))
        counts = "подтверждено 1, уточнено 1, отклонено 1, несущественно 1, было до изменений 1, не проверено 1"
        self.assertIn(f"Верификация: {counts}.", text)
        sections = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertEqual(tuple(sections), SECTIONS)
        self.assertIn(REFINED_CARD, text)
        self.assertIn("вердикт: не проверено", text)
        self.assertNotIn("Находка c3", text)
        self.assertIn("| bugs-claude | claude | opus | high | 0 | ответил, ничего не нашёл |", text)

    def test_неполный_раунд_в_отчёте(self) -> None:
        found = read_json(self.stages / "1-found.json")
        found["sources"].update(reported=3, degraded=["bugs-codex"])
        write_json(self.stages / "1-found.json", found)
        self.synthesize()
        self.apply()
        text = self.report()
        self.assertIn("**Раунд неполный**: ответили 3 из 4 источников, нет bugs-codex", text)
        self.assertIn("| bugs-codex | codex | gpt-6.1-sol | high | 0 | не ответил |", text)

    def test_findings_json_читает_debate_build(self) -> None:
        self.apply()
        debate = self.root / "debate"
        out = self.run_script("debate_build", self.run_dir, debate)
        self.assertTrue(out["verify_ran"])
        built = read_json(debate / "items.json")
        status = {entry["id"]: entry["status"] for entry in built}
        self.assertEqual(status, STATUSES)
        self.assertEqual(ids_of(read_json(debate / "consensus.json")), ["c1"])
        self.assertEqual(out["open_questions"], 1)


class StageOffTest(VerifyCase):
    """stages.verify: false marks every finding unverified and writes findings.json without 3-verified.json."""

    config = {"stages": {"verify": False}}

    def test_стадия_выключена(self) -> None:
        self.synthesize()
        plan = self.prepare()
        assert_fields(self, plan, skipped=True, reason="stages.verify is off for this run", findings=6)
        self.assertFalse((self.stages / "3-verified.json").exists())
        self.assertFalse(self.verify_prompts())
        final = self.read("findings.json")
        self.assertEqual({listed["verdict"] for listed in final["findings"]}, {"unverified"})
        self.assertEqual([question["verdict"] for question in final["open_questions"]], [""])
        self.assertIn("Верификация выключена в настройках прогона", self.report())
        self.assertIn("off for this run", self.apply_error())

    def test_debate_build_без_верификации(self) -> None:
        self.synthesize()
        self.prepare()
        out = self.run_script("debate_build", self.run_dir, self.root / "debate")
        assert_fields(self, out, verify_ran=False, by_status={"unverified": 6, "pre_existing": 1})


if __name__ == "__main__":
    unittest.main()
