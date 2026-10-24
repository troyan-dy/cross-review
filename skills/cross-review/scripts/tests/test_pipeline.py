"""The whole pipeline without models: a run, its finders, synthesis and verify, then the debate up to the report.

Every model answer is written by the test where an agent would write it; the scripts run as the skill calls them.
"""

from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path

from support import git, git_init, last_json_line, pick, read_json, run, write_json

SUBJECT = "ветка `feature/DEMO-1`"
FIELDS = ("file", "line", "end_line", "severity", "confidence", "title", "body", "fix")


def finding(title: str, place: tuple, severity: str, confidence: int, lens: str) -> dict:
    path, line = place
    return {
        "file": path,
        "line": line,
        "end_line": 0,
        "severity": severity,
        "confidence": confidence,
        "title": title,
        "body": f"{title}: описание.",
        "fix": f"{title}: исправление.",
        "lenses": [lens],
    }


# RACE is found by both vendors, RETRY and the question POOL by Claude only, LEAK, LAYERING and the weak TYPO by
# Codex only
FINDERS = types.MappingProxyType({
    "bugs-claude": [
        finding("Гонка при закрытии", ("app/store.py", 10), "major", 80, "bugs"),
        finding("Повтор без паузы", ("app/store.py", 30), "major", 85, "impl"),
    ],
    "bugs-codex": [
        finding("Гонка при закрытии соединения", ("app/store.py", 11), "major", 85, "bugs"),
        finding("Утечка дескриптора", ("app/io.py", 5), "critical", 90, "bugs"),
    ],
    "arch-claude": [finding("Зачем второй пул?", ("app/pool.py", 3), "minor", 75, "architecture")],
    "arch-codex": [
        finding("Слой знает о транспорте", ("app/api.py", 20), "major", 85, "architecture"),
        finding("Опечатка в сообщении", ("app/api.py", 40), "minor", 50, "quality"),
    ],
})
RACE = "bugs-claude-1"
RETRY = "bugs-claude-2"
LEAK = "bugs-codex-2"
LAYERING = "arch-codex-1"
TYPO = "arch-codex-2"
POOL = "arch-claude-1"
SIDE = "codex-side-1"
SINGLES = (RETRY, LEAK, LAYERING, TYPO)
CONFIRMED_FIELDS = ("file", "line", "title", "body", "fix", "severity")


def merged(ids: list[str], source: dict) -> dict:
    return {"merged_ids": ids, **{key: source[key] for key in FIELDS}}


def examine(fid: str, stance: str, severity: str) -> dict:
    return {"id": fid, "stance": stance, "severity": severity, "argument": "Довод проверяющего.", "evidence": "app/store.py:1"}


def respond(fid: str, position: str, severity: str) -> dict:
    return {"id": fid, "position": position, "severity": severity, "argument": "Ответ автора.", "evidence": "app/store.py:2"}


SIDE_FINDING = types.MappingProxyType({
    "file": "app/io.py",
    "line": 12,
    "severity": "major",
    "title": "Дескриптор не закрыт при ошибке",
    "body": "При исключении файл остаётся открытым.",
    "fix": "Закрыть в finally.",
    "evidence": "app/io.py:12",
})
VERDICTS = types.MappingProxyType({
    RACE: {"verdict": "confirmed"},
    RETRY: {"verdict": "refined", "severity": "minor"},
    LEAK: {"verdict": "confirmed"},
    LAYERING: {"verdict": "rejected"},
})
VERIFIED = ((LEAK, "critical"), (RACE, "major"), (RETRY, "minor"))
APPLIED = (2, 1, [LAYERING])
REPRIORITIZED = types.MappingProxyType({"raised": [LAYERING, RETRY], "lowered": [], "dropped": []})
OUTCOMES = types.MappingProxyType({
    RACE: ("consensus", "major", "none"),
    RETRY: ("disputed", "major", "moderator"),
    LEAK: ("confirmed", "critical", "none"),
    LAYERING: ("restored", "major", "side"),
    TYPO: ("dropped", "none", "none"),
    SIDE: ("side", "major", "moderator"),
})


class PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.repo = git_init(Path(workspace.name).resolve() / "repo")
        (self.repo / "app").mkdir()
        for name in ("store.py", "io.py", "pool.py", "api.py"):
            (self.repo / "app" / name).write_text("x = 1\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        self.head = git(self.repo, "rev-parse", "HEAD").strip()
        self.run_dir = self.repo / ".cross-review" / "runs" / "demo" / "01-initial"
        self.debate = self.run_dir / "debate"
        self.launch: dict[tuple[str, str], dict] = {}

    def step(self, script: str, *args: object) -> dict:
        res = run(script, *args, cwd=self.repo)
        message = f"{script} {args}: {res.stdout}{res.stderr}"
        self.assertEqual(res.returncode, 0, message)
        return last_json_line(res.stdout)

    def answer(self, path: Path | str, payload: object) -> None:
        write_json(Path(path), payload)

    def test_от_находок_до_отчёта(self) -> None:
        self.review()
        self.debate_and_report()
        self.check_untouched()

    def review(self) -> None:
        self.start_run()
        self.find()
        self.synthesize()
        self.verify()

    def debate_and_report(self) -> None:
        self.split_for_debate()
        self.first_round()
        self.second_round()
        self.fold()
        self.report()

    def check_untouched(self) -> None:
        """iron rule 1: the code and git stay as they were"""
        self.assertEqual(git(self.repo, "rev-parse", "HEAD").strip(), self.head)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        ignore = self.repo / ".cross-review" / "runs" / ".gitignore"
        self.assertEqual(ignore.read_text(), "*\n")

    def start_run(self) -> None:
        """1. the run and its input"""
        created = self.step("review_run", "new", self.repo, "--task", "demo")
        run_dir = Path(created["run_dir"])
        self.assertEqual(run_dir, self.run_dir)
        (run_dir / "input" / "scope.md").write_text("`git diff master`\n")
        (run_dir / "input" / "goal.md").write_text("Найти дефекты закрытия соединений.\n")

    def find(self) -> None:
        """2. finders"""
        plan = self.step("review_run", "compose", self.run_dir)
        agents = sorted(entry["agent"] for entry in plan["launch"])
        self.assertEqual(agents, sorted(FINDERS))
        for entry in plan["launch"]:
            self.answer_finder(entry)
        collected = self.step("review_collect", self.run_dir)
        counts = pick(collected, "reported", "findings", "degraded", "two_sides")
        self.assertEqual(counts, (4, 7, [], True))

    def answer_finder(self, entry: dict) -> None:
        text = Path(entry["prompt"]).read_text()
        self.assertNotIn("{{", text)
        self.assertIn("Return ONLY a JSON object matching the schema below", text)
        agent = entry["agent"]
        answer = self.run_dir / "agents" / f"{agent}.json"
        self.answer(answer, {"findings": FINDERS[agent]})

    def synthesize(self) -> None:
        """3. synthesis: RACE merged, POOL a question, TYPO a weak singleton the rules drop"""
        raw_findings = read_json(self.run_dir / "stages" / "1-found.json")["findings"]
        found = {raw["id"]: raw for raw in raw_findings}
        synth = self.step("review_synth", "prepare", self.run_dir)
        self.assertFalse(synth["skipped"])
        self.assertNotIn("{{", Path(synth["prompt"]).read_text())
        self.answer_synthesis(synth["output"], found)
        self.step("review_synth", "apply", self.run_dir)
        synthesized = read_json(self.run_dir / "stages" / "2-synthesized.json")
        # ordered by severity, the critical LEAK first
        kept_ids = [kept["id"] for kept in synthesized["findings"]]
        self.assertEqual(kept_ids, [LEAK, RACE, RETRY, LAYERING])
        self.assertEqual(synthesized["findings"][1]["confidence"], 95)
        self.assertEqual([question["id"] for question in synthesized["open_questions"]], [POOL])

    def answer_synthesis(self, output: str, found: dict[str, dict]) -> None:
        singles = [merged([fid], found[fid]) for fid in SINGLES]
        self.answer(output, {
            "findings": [merged([RACE, "bugs-codex-1"], found[RACE]), *singles],
            "open_questions": [merged([POOL], found[POOL])],
            "pre_existing": [],
        })

    def verify(self) -> None:
        """4. verify: RETRY refined down to minor, LAYERING rejected"""
        verify = self.step("review_verify", "prepare", self.run_dir)
        for entry in verify["launch"]:
            self.answer_verifier(entry)
        applied = self.step("review_verify", "apply", self.run_dir)
        self.assertEqual(pick(applied, "confirmed", "refined", "rejected"), APPLIED)
        findings = read_json(self.run_dir / "findings.json")
        severities = [(kept["id"], kept["severity"]) for kept in findings["findings"]]
        self.assertEqual(tuple(severities), VERIFIED)
        self.assertTrue((self.run_dir / "report.md").is_file())

    def answer_verifier(self, entry: dict) -> None:
        self.assertNotIn("{{", Path(entry["prompt"]).read_text())
        given = [
            {"id": fid, "reason": "Проверено по коду.", **VERDICTS[fid]}
            for fid in entry["findings"]
        ]
        self.answer(entry["output"], {"verdicts": given})

    def split_for_debate(self) -> None:
        """5. split: RACE is the consensus, RETRY goes to Codex, LEAK, LAYERING and TYPO to Claude, POOL to Codex"""
        split = self.step("debate_build", self.run_dir)
        self.assertEqual(pick(split, "consensus", "for_codex", "for_claude"), (1, 1, 3))
        for_claude = read_json(self.debate / "for-claude.json")
        claude_ids = {card["id"] for card in for_claude["findings"]}
        self.assertEqual(claude_ids, {LEAK, LAYERING, TYPO})
        for_codex = read_json(self.debate / "for-codex.json")
        self.assertEqual([question["id"] for question in for_codex["open_questions"]], [POOL])

    def first_round(self) -> None:
        """6. round 1: Codex raises RETRY back to major and answers POOL; Claude confirms LEAK, restores LAYERING, rejects E"""
        prompts = self.step("debate_prompts", self.run_dir, "--subject", SUBJECT, "--ref", self.head)
        for name in prompts["wrote"]:
            if name.endswith(".md"):
                self.assertNotIn("{{", (self.debate / name).read_text(), name)
        self.launch = {
            (entry["step"], entry["vendor"]): entry
            for entry in prompts["launch"]
        }
        self.assertIn("command", self.launch["r1", "codex"])
        reply = {"id": POOL, "answer": "Второй пул нужен для фоновых задач.", "evidence": "app/pool.py:3"}
        self.answer(self.launch["r1", "codex"]["output"], {
            "verdicts": [examine(RETRY, "real_but_different", "major")],
            "open_question_answers": [reply],
        })
        self.answer(self.launch["r1", "claude"]["output"], {
            "verdicts": [
                examine(LEAK, "real", "critical"),
                examine(LAYERING, "real", "major"),
                examine(TYPO, "not_real", "none"),
            ],
            "open_question_answers": [],
        })

    def second_round(self) -> None:
        """7. round 2: Claude amends RETRY to its own minor, Codex concedes TYPO and finds one more defect"""
        r2 = self.step("debate_round2", self.debate)
        self.assertEqual(pick(r2, "for_claude_r2", "for_codex_r2"), (1, 1))
        self.answer(self.launch["r2", "claude"]["output"], {
            "responses": [respond(RETRY, "amend", "minor")],
            "side_findings": [],
        })
        self.answer(self.launch["r2", "codex"]["output"], {
            "responses": [respond(TYPO, "concede", "none")],
            "side_findings": [dict(SIDE_FINDING)],
        })

    def fold(self) -> None:
        """8. outcome: the amend of RETRY and the side finding wait for the moderator"""
        pending = self.step("debate_outcome", self.run_dir)
        self.assertEqual(pick(pending, "needs_moderator", "side_findings_pending"), ([RETRY], [SIDE]))
        self.assertTrue((self.debate / "disputes.json").is_file())
        self.moderate()
        summary = self.step("debate_outcome", self.run_dir)
        self.assertEqual(pick(summary, "needs_moderator", "side_findings_pending"), ([], []))
        self.assertEqual(summary["reprioritized"], dict(REPRIORITIZED))
        self.check_final()
        self.check_markdown()

    def check_final(self) -> None:
        final = read_json(self.debate / "final.json")
        self.assertEqual(final["meta"]["pipeline"], "cross-review")
        cards = {card["id"]: card for card in final["items"]}
        outcomes = {fid: self.outcome_of(cards[fid]) for fid in OUTCOMES}
        self.assertEqual(outcomes, OUTCOMES)
        shift = {"from": "minor", "to": "major", "direction": "raised", "by": "moderator"}
        self.assertEqual(cards[RETRY]["severity_shift"], shift)
        self.assertEqual(final["questions"][0]["answered_by"], ["codex"])

    def moderate(self) -> None:
        decision = {"id": RETRY, "verdict": "прав Codex", "severity": "major", "note": "Повтор без паузы перегружает сервис."}
        self.answer(self.debate / "moderator.json", [decision])
        confirmed = {key: SIDE_FINDING[key] for key in CONFIRMED_FIELDS}
        self.answer(self.debate / "extra.json", [{
            "id": SIDE,
            **confirmed,
            "sources": ["codex"],
            "vendors": ["codex"],
            "note": "Проверено по коду.",
        }])

    def outcome_of(self, card: dict) -> tuple[str, str, str]:
        return card["outcome"], card["final_severity"], card["severity_shift"]["by"]

    def check_markdown(self) -> None:
        res = run("debate_outcome", self.run_dir, "--markdown", cwd=self.repo)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(res.stdout.startswith("**Итог: "))
        self.assertIn("- **Переоценка приоритетов:** повышено 2, понижено 0, снято 0.", res.stdout)
        self.assertIn(f"  - Повышено: `{RETRY}` minor → major", res.stdout)

    def report(self) -> None:
        """9. the report page"""
        page = self.debate / "report.html"
        title = "Перекрёстное ревью demo"
        report = self.step(
            "debate_report", self.debate, page, "--title", title, "--subject", SUBJECT, "--strip-prefix", f"{self.repo}/",
        )
        self.assertEqual(report["reprioritized"], {"raised": 2, "lowered": 0, "dropped": 0})
        html = page.read_text()
        for fid in OUTCOMES:
            self.assertIn(fid, html)
        self.assertIn("Переоценка приоритетов", html)


if __name__ == "__main__":
    unittest.main()
