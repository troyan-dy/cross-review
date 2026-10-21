"""The whole pipeline without models: a run, its finders, synthesis and verify, then the debate up to the report.

Every model answer is written by the test where an agent would write it; the scripts run as the skill calls them.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from support import git, git_init, last_json_line, read_json, run, write_json

SUBJECT = "ветка `feature/DEMO-1`"


def finding(title: str, file: str, line: int, severity: str, confidence: int, lens: str) -> dict:
    return {"file": file, "line": line, "end_line": 0, "severity": severity, "confidence": confidence,
            "title": title, "body": f"{title}: описание.", "fix": f"{title}: исправление.", "lenses": [lens]}


# A is found by both vendors, B and Q by Claude only, C, D and the weak E by Codex only
FINDERS = {
    "bugs-claude": [finding("Гонка при закрытии", "app/store.py", 10, "major", 80, "bugs"),
                    finding("Повтор без паузы", "app/store.py", 30, "major", 85, "impl")],
    "bugs-codex": [finding("Гонка при закрытии соединения", "app/store.py", 11, "major", 85, "bugs"),
                   finding("Утечка дескриптора", "app/io.py", 5, "critical", 90, "bugs")],
    "arch-claude": [finding("Зачем второй пул?", "app/pool.py", 3, "minor", 75, "architecture")],
    "arch-codex": [finding("Слой знает о транспорте", "app/api.py", 20, "major", 85, "architecture"),
                   finding("Опечатка в сообщении", "app/api.py", 40, "minor", 50, "quality")],
}
A, B, C, D, E, Q = "bugs-claude-1", "bugs-claude-2", "bugs-codex-2", "arch-codex-1", "arch-codex-2", "arch-claude-1"


def merged(ids: list[str], source: dict) -> dict:
    return {"merged_ids": ids, **{k: source[k] for k in ("file", "line", "end_line", "severity", "confidence", "title",
                                                         "body", "fix")}}


def examine(fid: str, stance: str, severity: str) -> dict:
    return {"id": fid, "stance": stance, "severity": severity, "argument": "Довод проверяющего.", "evidence": "app/store.py:1"}


def respond(fid: str, position: str, severity: str) -> dict:
    return {"id": fid, "position": position, "severity": severity, "argument": "Ответ автора.", "evidence": "app/store.py:2"}


SIDE_FINDING = {"file": "app/io.py", "line": 12, "severity": "major", "title": "Дескриптор не закрыт при ошибке",
                "body": "При исключении файл остаётся открытым.", "fix": "Закрыть в finally.", "evidence": "app/io.py:12"}


class PipelineTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = git_init(Path(tmp.name).resolve() / "repo")
        (self.repo / "app").mkdir()
        for name in ("store.py", "io.py", "pool.py", "api.py"):
            (self.repo / "app" / name).write_text("x = 1\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "init")
        self.head = git(self.repo, "rev-parse", "HEAD").strip()

    def step(self, script: str, *args: object) -> dict:
        res = run(script, *args, cwd=self.repo)
        self.assertEqual(res.returncode, 0, f"{script} {args}: {res.stdout}{res.stderr}")
        return last_json_line(res.stdout)

    def answer(self, path: Path | str, data: object) -> None:
        write_json(Path(path), data)

    def test_от_находок_до_отчёта(self) -> None:
        # 1. the run and its input
        run_dir = Path(self.step("review_run", "new", self.repo, "--task", "demo")["run_dir"])
        self.assertEqual(run_dir, self.repo / ".cross-review" / "runs" / "demo" / "01-initial")
        (run_dir / "input" / "scope.md").write_text("`git diff master`\n")
        (run_dir / "input" / "goal.md").write_text("Найти дефекты закрытия соединений.\n")

        # 2. finders
        plan = self.step("review_run", "compose", run_dir)
        self.assertEqual(sorted(e["agent"] for e in plan["launch"]), sorted(FINDERS))
        for entry in plan["launch"]:
            text = Path(entry["prompt"]).read_text()
            self.assertNotIn("{{", text)
            self.assertIn("Return ONLY a JSON object matching the schema below", text)
            self.answer(run_dir / "agents" / f"{entry['agent']}.json", {"findings": FINDERS[entry["agent"]]})
        collected = self.step("review_collect", run_dir)
        self.assertEqual((collected["reported"], collected["findings"], collected["degraded"], collected["two_sides"]),
                         (4, 7, [], True))

        # 3. synthesis: A merged, Q a question, E a weak singleton the rules drop
        found = {f["id"]: f for f in read_json(run_dir / "stages" / "1-found.json")["findings"]}
        synth = self.step("review_synth", "prepare", run_dir)
        self.assertFalse(synth["skipped"])
        self.assertNotIn("{{", Path(synth["prompt"]).read_text())
        self.answer(synth["output"], {
            "findings": [merged([A, "bugs-codex-1"], found[A]), *(merged([i], found[i]) for i in (B, C, D, E))],
            "open_questions": [merged([Q], found[Q])], "pre_existing": []})
        self.step("review_synth", "apply", run_dir)
        synthesized = read_json(run_dir / "stages" / "2-synthesized.json")
        # ordered by severity, the critical C first
        self.assertEqual([f["id"] for f in synthesized["findings"]], [C, A, B, D])
        self.assertEqual(synthesized["findings"][1]["confidence"], 95)
        self.assertEqual([f["id"] for f in synthesized["open_questions"]], [Q])

        # 4. verify: B refined down to minor, D rejected
        verify = self.step("review_verify", "prepare", run_dir)
        verdicts = {A: {"verdict": "confirmed"}, B: {"verdict": "refined", "severity": "minor"},
                    C: {"verdict": "confirmed"}, D: {"verdict": "rejected"}}
        for entry in verify["launch"]:
            self.assertNotIn("{{", Path(entry["prompt"]).read_text())
            self.answer(entry["output"], {"verdicts": [{"id": i, "reason": "Проверено по коду.", **verdicts[i]}
                                                       for i in entry["findings"]]})
        applied = self.step("review_verify", "apply", run_dir)
        self.assertEqual((applied["confirmed"], applied["refined"], applied["rejected"]), (2, 1, [D]))
        findings = read_json(run_dir / "findings.json")
        self.assertEqual([(f["id"], f["severity"]) for f in findings["findings"]], [(C, "critical"), (A, "major"), (B, "minor")])
        self.assertTrue((run_dir / "report.md").is_file())

        # 5. split: A is the consensus, B goes to Codex, C, D and E to Claude, Q to Codex
        split = self.step("debate_build", run_dir)
        self.assertEqual((split["consensus"], split["for_codex"], split["for_claude"]), (1, 1, 3))
        d = run_dir / "debate"
        self.assertEqual({i["id"] for i in read_json(d / "for-claude.json")["findings"]}, {C, D, E})
        self.assertEqual([i["id"] for i in read_json(d / "for-codex.json")["open_questions"]], [Q])

        # 6. round 1: Codex raises B back to major and answers Q; Claude confirms C, restores D, rejects E
        prompts = self.step("debate_prompts", run_dir, "--subject", SUBJECT, "--ref", self.head)
        for name in prompts["wrote"]:
            if name.endswith(".md"):
                self.assertNotIn("{{", (d / name).read_text(), name)
        launch = {(e["step"], e["vendor"]): e for e in prompts["launch"]}
        self.assertIn("command", launch[("r1", "codex")])
        self.answer(launch[("r1", "codex")]["output"], {
            "verdicts": [examine(B, "real_but_different", "major")],
            "open_question_answers": [{"id": Q, "answer": "Второй пул нужен для фоновых задач.", "evidence": "app/pool.py:3"}]})
        self.answer(launch[("r1", "claude")]["output"], {
            "verdicts": [examine(C, "real", "critical"), examine(D, "real", "major"), examine(E, "not_real", "none")],
            "open_question_answers": []})

        # 7. round 2: Claude amends B to its own minor, Codex concedes E and finds one more defect
        r2 = self.step("debate_round2", d)
        self.assertEqual((r2["for_claude_r2"], r2["for_codex_r2"]), (1, 1))
        self.answer(launch[("r2", "claude")]["output"], {"responses": [respond(B, "amend", "minor")], "side_findings": []})
        self.answer(launch[("r2", "codex")]["output"], {"responses": [respond(E, "concede", "none")],
                                                       "side_findings": [SIDE_FINDING]})

        # 8. outcome: the amend of B and the side finding wait for the moderator
        pending = self.step("debate_outcome", run_dir)
        self.assertEqual((pending["needs_moderator"], pending["side_findings_pending"]), ([B], ["codex-side-1"]))
        self.assertTrue((d / "disputes.json").is_file())
        self.answer(d / "moderator.json", [{"id": B, "verdict": "прав Codex", "severity": "major",
                                            "note": "Повтор без паузы перегружает сервис."}])
        self.answer(d / "extra.json", [{"id": "codex-side-1", **{k: SIDE_FINDING[k] for k in ("file", "line", "title", "body",
                                                                                            "fix", "severity")},
                                        "sources": ["codex"], "vendors": ["codex"], "note": "Проверено по коду."}])
        summary = self.step("debate_outcome", run_dir)
        self.assertEqual((summary["needs_moderator"], summary["side_findings_pending"]), ([], []))
        self.assertEqual(summary["reprioritized"], {"raised": [D, B], "lowered": [], "dropped": []})
        final = read_json(d / "final.json")
        self.assertEqual(final["meta"]["pipeline"], "cross-review")
        items = {i["id"]: i for i in final["items"]}
        outcomes = {fid: (items[fid]["outcome"], items[fid]["final_severity"], items[fid]["severity_shift"]["by"])
                    for fid in (A, B, C, D, E, "codex-side-1")}
        self.assertEqual(outcomes, {
            A: ("consensus", "major", "none"),
            B: ("disputed", "major", "moderator"),
            C: ("confirmed", "critical", "none"),
            D: ("restored", "major", "side"),
            E: ("dropped", "none", "none"),
            "codex-side-1": ("side", "major", "moderator"),
        })
        self.assertEqual(items[B]["severity_shift"], {"from": "minor", "to": "major", "direction": "raised", "by": "moderator"})
        self.assertEqual(final["questions"][0]["answered_by"], ["codex"])

        res = run("debate_outcome", run_dir, "--markdown", cwd=self.repo)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue(res.stdout.startswith("**Итог: "))
        self.assertIn("- **Переоценка приоритетов:** повышено 2, понижено 0, снято 0.", res.stdout)
        self.assertIn(f"  - Повышено: `{B}` minor → major", res.stdout)

        # 9. the report page
        page = d / "report.html"
        report = self.step("debate_report", d, page, "--title", "Перекрёстное ревью demo", "--subject", SUBJECT,
                           "--strip-prefix", f"{self.repo}/")
        self.assertEqual(report["reprioritized"], {"raised": 2, "lowered": 0, "dropped": 0})
        html = page.read_text()
        for fid in (A, B, C, D, E, "codex-side-1"):
            self.assertIn(fid, html)
        self.assertIn("Переоценка приоритетов", html)

        # iron rule 1: the code and git stay as they were
        self.assertEqual(git(self.repo, "rev-parse", "HEAD").strip(), self.head)
        self.assertEqual(git(self.repo, "status", "--porcelain"), "")
        self.assertEqual((self.repo / ".cross-review" / "runs" / ".gitignore").read_text(), "*\n")


if __name__ == "__main__":
    unittest.main()
