from __future__ import annotations

import json
import shlex
import shutil
import tempfile
import unittest
from pathlib import Path

from support import FIXTURES, SCRIPTS, composed_run, copy_fixture, git, git_init, last_json_line, read_json, run

import debate_prompts

SUBJECT = "MR !42 `feature/DEMO-1` (диапазон a1b2c3..d4e5f6)"
PROMPT_FILES = [
    "prompt-r1-claude.md", "prompt-r1-codex.md", "prompt-r2-claude.md", "prompt-r2-codex.md",
    "prompt-r2-codex.fresh.md", "prompt-moderator.md", "prompt-moderator-claude.md", "prompt-moderator-codex.md",
]
SCHEMA_FILES = ["schema-examine.json", "schema-respond.json", "schema-moderate.json"]
PANEL = "Four reviewers — two Claude (claude-opus-5-5) and two Codex (gpt-6.1-sol) —"
CODEX_OUTPUT = "Return JSON matching the output schema."


class PromptsCase(unittest.TestCase):
    """A cross-review run of the demo task holding the verified round of run-basic, with the debate built."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.run_dir = composed_run(self.root)
        self.repo = self.run_dir.parents[3]
        self.debate = self.run_dir / "debate"
        basic = FIXTURES / "run-basic"
        for name in ("findings.json", "events.jsonl"):
            shutil.copy(basic / name, self.run_dir / name)
        shutil.copytree(basic / "stages", self.run_dir / "stages")
        res = run("debate_build", self.run_dir)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)

    def prompts(self, *args: object, run_dir: Path | None = None) -> dict:
        res = run("debate_prompts", run_dir or self.run_dir, "--subject", SUBJECT, *args)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return last_json_line(res.stdout)

    def failing(self, *args: object) -> dict:
        res = run("debate_prompts", self.run_dir, "--subject", SUBJECT, *args)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        return last_json_line(res.stdout)

    def text(self, name: str) -> str:
        return (self.debate / name).read_text()

    def own(self, name: str, text: str) -> None:
        path = self.repo / ".cross-review" / "prompts" / f"{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


class FilesTest(PromptsCase):
    """Every prompt and schema lands in <run_dir>/debate/, with nothing left to substitute."""

    def test_генерируются_все_файлы(self) -> None:
        out = self.prompts()
        self.assertEqual(out["debate_dir"], str(self.debate))
        self.assertEqual(sorted(out["wrote"]), sorted(PROMPT_FILES + SCHEMA_FILES))
        for name in PROMPT_FILES + SCHEMA_FILES:
            with self.subTest(file=name):
                self.assertTrue((self.debate / name).is_file())
        self.assertEqual(read_json(self.debate / "schema-examine.json")["required"], ["verdicts", "open_question_answers"])
        self.assertEqual(read_json(self.debate / "schema-respond.json")["required"], ["responses", "side_findings"])
        self.assertEqual(read_json(self.debate / "schema-moderate.json")["required"], ["decisions", "extra"])

    def test_плейсхолдеров_не_остаётся(self) -> None:
        self.prompts()
        for name in PROMPT_FILES:
            with self.subTest(file=name):
                self.assertNotIn("{{", self.text(name))
                self.assertNotIn("}}", self.text(name))

    def test_каталог_спора_задаётся_явно(self) -> None:
        other = self.root / "elsewhere"
        res = run("debate_prompts", self.run_dir, other, "--subject", SUBJECT)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(last_json_line(res.stdout)["debate_dir"], str(other))
        self.assertTrue((other / "prompt-r1-claude.md").is_file())
        self.assertIn(f"`{other}/for-claude.json`", (other / "prompt-r1-claude.md").read_text())

    def test_повторный_запуск_перезаписывает(self) -> None:
        self.prompts()
        self.prompts("--context", f"{self.root}/note.md=заметка")
        self.assertIn(f"`{self.root}/note.md` — заметка", self.text("prompt-r1-codex.md"))


class SubstitutionTest(PromptsCase):
    """Subject, paths, panel, context and the language rule reach every prompt they belong in."""

    def test_предмет_пути_и_панель_подставлены(self) -> None:
        self.prompts()
        d = self.debate
        # round 2 continues the round-1 session and names neither
        for name in (n for n in PROMPT_FILES if n not in ("prompt-r2-claude.md", "prompt-r2-codex.md")):
            with self.subTest(file=name):
                self.assertIn(SUBJECT, self.text(name))
                self.assertIn(f"`{self.repo}`", self.text(name))
        cases = [
            ("prompt-r1-claude.md", f"`{d}/for-claude.json`"),
            ("prompt-r1-codex.md", f"`{d}/for-codex.json`"),
            ("prompt-r2-claude.md", f"`{d}/for-claude-r2.json`"),
            ("prompt-r2-codex.md", f"`{d}/for-codex-r2.json`"),
            ("prompt-moderator.md", f"`{d}/disputes.json`"),
            ("prompt-moderator-codex.md", f"`{d}/disputes.json`"),
        ]
        for name, expected in cases:
            with self.subTest(file=name, expected=expected):
                self.assertIn(expected, self.text(name))
        for name in ("prompt-r1-claude.md", "prompt-r1-codex.md"):
            with self.subTest(panel=name):
                self.assertIn(f"{PANEL} reviewed it independently", self.text(name))
                self.assertIn("`rejected_by_verify` — the verifier rejected it", self.text(name))

    def test_стороны_названы_по_вендору(self) -> None:
        self.prompts()
        self.assertIn("You are the **Claude side**", self.text("prompt-r1-claude.md"))
        self.assertIn("you examine what only the Codex reviewers raised", self.text("prompt-r1-claude.md"))
        self.assertIn("You are the **Codex side**", self.text("prompt-r1-codex.md"))
        self.assertIn("The Claude side has examined the findings the Codex reviewers raised", self.text("prompt-r2-codex.md"))

    def test_контекст_прогона_и_дополнительный(self) -> None:
        (self.run_dir / "input" / "goal.md").write_text("Цель изменения.\n")
        extra = self.root / "design.md"
        out = self.prompts("--context", f"{extra}=дизайн решения")
        scope = self.run_dir / "input" / "scope.md"
        self.assertEqual(out["context"], [str(scope), str(self.run_dir / "input" / "goal.md"), str(extra)])
        for name in ("prompt-r1-claude.md", "prompt-r1-codex.md", "prompt-moderator.md"):
            with self.subTest(file=name):
                self.assertIn(f"- `{scope}` — what was reviewed", self.text(name))
                self.assertIn(f"- `{extra}` — дизайн решения", self.text(name))

    def test_вывод_сторон(self) -> None:
        self.prompts()
        d = self.debate
        cases = [
            ("prompt-r1-claude.md", f"`{d}/claude-r1.json` with the Write tool"),
            ("prompt-r1-claude.md", f"matching `{d}/schema-examine.json`"),
            ("prompt-r2-claude.md", f"`{d}/claude-r2.json` with the Write tool"),
            ("prompt-r2-claude.md", f"matching `{d}/schema-respond.json`"),
            ("prompt-moderator.md", f"`{d}/moderator.json` as a JSON array"),
            ("prompt-moderator.md", f"`{d}/extra.json` as a JSON array"),
            ("prompt-moderator-claude.md", f"`{d}/moderator-claude.json` as a JSON array"),
            ("prompt-moderator-claude.md", f"`{d}/extra-claude.json` as a JSON array"),
        ]
        for name, expected in cases:
            with self.subTest(file=name, expected=expected):
                self.assertIn(expected, self.text(name))
        for name in ("prompt-r1-codex.md", "prompt-r2-codex.md", "prompt-moderator-codex.md"):
            with self.subTest(codex=name):
                self.assertTrue(self.text(name).rstrip().endswith(CODEX_OUTPUT))
                self.assertNotIn("Write tool", self.text(name))
                self.assertNotIn("moderator.json", self.text(name))

    def test_русский_язык_в_каждом_промте(self) -> None:
        self.prompts()
        cases = [
            ("prompt-r1-claude.md", "Write every `argument` and every open question's `answer` in Russian"),
            ("prompt-r2-codex.md", "and the `title`, `body` and `fix` of every side finding, in Russian"),
            ("prompt-moderator-codex.md", "Write every `verdict` and `note`"),
        ]
        for name, expected in cases:
            with self.subTest(file=name):
                self.assertIn(expected, self.text(name))

    def test_свежий_промт_r2_codex(self) -> None:
        self.prompts()
        fresh = self.text("prompt-r2-codex.fresh.md")
        r1, r2 = self.text("prompt-r1-codex.md"), self.text("prompt-r2-codex.md")
        head, sep, tail = fresh.partition("\n\n---\n\n")
        self.assertEqual(sep, "\n\n---\n\n")
        self.assertEqual(tail, r2)
        self.assertEqual(head, r1.rstrip("\n").removesuffix(CODEX_OUTPUT).rstrip("\n") + "\n\n"
                         + f"Round 1 is over: your verdicts on these findings are in `{self.debate}/codex-r1.json` "
                         "(no such file means you had nothing to examine). Do not examine them again; answer round 2 below.")
        self.assertEqual(fresh.count(CODEX_OUTPUT), 1)


class LaunchTest(PromptsCase):
    """The launch plan: every step's files, and for a cross-review run the models and codex_run.sh commands."""

    def entry(self, out: dict, step: str, vendor: str) -> dict:
        return next(e for e in out["launch"] if (e["step"], e["vendor"]) == (step, vendor))

    def command(self, prompt: str, schema: str, output: str, model: str, *tail: str) -> str:
        d = self.debate
        return shlex.join(["bash", str(SCRIPTS / "codex_run.sh"), str(self.repo), str(d / prompt), str(d / schema),
                           str(d / output), model, "high", *tail])

    def test_порядок_шагов(self) -> None:
        out = self.prompts()
        self.assertEqual([(e["step"], e["vendor"]) for e in out["launch"]], [
            ("r1", "claude"), ("r1", "codex"), ("r2", "claude"), ("r2", "codex"),
            ("moderate", "session"), ("moderate", "claude"), ("moderate", "codex"),
        ])

    def test_стороны_claude(self) -> None:
        out = self.prompts()
        d = self.debate
        self.assertEqual(self.entry(out, "r1", "claude"), {
            "step": "r1", "vendor": "claude", "prompt": str(d / "prompt-r1-claude.md"),
            "output": str(d / "claude-r1.json"), "alias": "opus", "effort": "high"})
        self.assertEqual(self.entry(out, "r2", "claude")["continues"], "r1")
        self.assertEqual(self.entry(out, "moderate", "session")["output"], [str(d / "moderator.json"), str(d / "extra.json")])
        moderator = self.entry(out, "moderate", "claude")
        self.assertEqual((moderator["alias"], moderator["effort"]), ("fable", "high"))
        self.assertEqual(moderator["output"], [str(d / "moderator-claude.json"), str(d / "extra-claude.json")])

    def test_стороны_codex_через_codex_run(self) -> None:
        out = self.prompts()
        d = self.debate
        r1 = self.entry(out, "r1", "codex")
        self.assertEqual((r1["model"], r1["effort"], r1["schema"]), ("gpt-6.1-sol", "high", str(d / "schema-examine.json")))
        self.assertEqual(r1["command"], self.command("prompt-r1-codex.md", "schema-examine.json", "codex-r1.json", "gpt-6.1-sol"))
        r2 = self.entry(out, "r2", "codex")
        self.assertEqual((r2["resume"], r2["fresh"]), (str(d / "codex-r1.log"), str(d / "prompt-r2-codex.fresh.md")))
        self.assertEqual(r2["command"], self.command("prompt-r2-codex.md", "schema-respond.json", "codex-r2.json",
                                                     "gpt-6.1-sol", "--resume", str(d / "codex-r1.log")))
        self.assertEqual(r2["fresh_command"], self.command("prompt-r2-codex.fresh.md", "schema-respond.json",
                                                           "codex-r2.json", "gpt-6.1-sol"))
        moderator = self.entry(out, "moderate", "codex")
        self.assertEqual(moderator["command"], self.command("prompt-moderator-codex.md", "schema-moderate.json",
                                                            "moderator-codex.json", "gpt-6-astra"))

    def test_модели_из_манифеста_главнее_findings(self) -> None:
        manifest = read_json(self.run_dir / "manifest.json")
        manifest["models"] = {"claude": {"model": "sonnet", "effort": "medium"}, "codex": {"model": "gpt-6-sol", "effort": "low"}}
        (self.run_dir / "manifest.json").write_text(json.dumps(manifest))
        out = self.prompts()
        r1 = self.entry(out, "r1", "claude")
        self.assertEqual((r1["alias"], r1["effort"]), ("sonnet", "medium"))
        d = self.debate
        self.assertEqual(self.entry(out, "r1", "codex")["command"], shlex.join([
            "bash", str(SCRIPTS / "codex_run.sh"), str(self.repo), str(d / "prompt-r1-codex.md"),
            str(d / "schema-examine.json"), str(d / "codex-r1.json"), "gpt-6-sol", "low"]))

    def test_без_моделей_в_манифесте_модели_из_findings(self) -> None:
        manifest = read_json(self.run_dir / "manifest.json")
        del manifest["models"], manifest["moderators"]
        (self.run_dir / "manifest.json").write_text(json.dumps(manifest))
        out = self.prompts()
        r1 = self.entry(out, "r1", "claude")
        self.assertEqual((r1["alias"], r1["effort"]), ("opus", "high"))
        self.assertEqual(self.entry(out, "r1", "codex")["command"],
                         self.command("prompt-r1-codex.md", "schema-examine.json", "codex-r1.json", "gpt-6.1-sol"))
        moderator = self.entry(out, "moderate", "claude")
        self.assertEqual((moderator["alias"], moderator["effort"]), ("fable", "high"))
        self.assertEqual(self.entry(out, "moderate", "codex")["model"], "gpt-6-astra")
        self.assertIn("resume", self.entry(out, "r2", "codex"))

    def test_неизвестная_модель_в_findings_выход_1(self) -> None:
        manifest = read_json(self.run_dir / "manifest.json")
        del manifest["models"]
        (self.run_dir / "manifest.json").write_text(json.dumps(manifest))
        findings = read_json(self.run_dir / "findings.json")
        for agent in findings["sources"]["agents"]:
            if agent["executor"] == "claude":
                agent["requested_model"] = agent["actual_model"] = "claude-mystery-1"
        (self.run_dir / "findings.json").write_text(json.dumps(findings))
        self.assertIn("claude-mystery-1", self.failing()["error"])


class LayerTest(PromptsCase):
    """The prompts resolve through .cross-review/ of the reviewed repository, and the run's manifest says so."""

    def test_промты_спора_в_манифесте(self) -> None:
        out = self.prompts()
        self.assertEqual({name: v["layer"] for name, v in out["prompts"].items()},
                         {"examine": "plugin", "respond": "plugin", "moderate": "plugin"})
        self.prompts()
        sources = [p for p in read_json(self.run_dir / "manifest.json")["prompts"] if p["path"].startswith("prompts/")]
        self.assertEqual([p["path"] for p in sources],
                         ["prompts/review.md", "prompts/examine.md", "prompts/respond.md", "prompts/moderate.md"])
        self.assertEqual({p["layer"] for p in sources}, {"plugin"})

    def test_override_examine_из_репозитория(self) -> None:
        self.own("examine", "---\ndescription: своя редакция\n---\nСвой бриф для стороны {{SIDE}} против {{OTHER}}.\n"
                            "Читай `{{INPUT}}`.\n\n{{OUTPUT}}\n")
        out = self.prompts()
        self.assertEqual(out["prompts"]["examine"]["layer"], "repo")
        self.assertEqual(out["prompts"]["respond"]["layer"], "plugin")
        text = self.text("prompt-r1-codex.md")
        self.assertTrue(text.startswith("Свой бриф для стороны Codex против Claude.\n"))
        self.assertIn(f"Читай `{self.debate}/for-codex.json`.", text)
        self.assertNotIn("description:", text)
        self.assertTrue(self.text("prompt-r2-codex.fresh.md").startswith("Свой бриф для стороны Codex"))
        self.assertTrue(self.text("prompt-r2-claude.md").startswith("Round 2."))
        manifest = {p["path"]: p["layer"] for p in read_json(self.run_dir / "manifest.json")["prompts"]}
        self.assertEqual((manifest["prompts/examine.md"], manifest["prompts/respond.md"]), ("repo", "plugin"))

    def test_неизвестный_плейсхолдер_ошибка(self) -> None:
        cases = [
            ("examine", "Сторона {{SIDE}}, {{NOPE}}.\n", "{{NOPE}}"),
            ("respond", "Ответ {{SIDE}} на {{INPUT}} с {{SCOPE}}.\n", "{{SCOPE}}"),
            # у модератора нет стороны
            ("moderate", "Модератор {{SIDE}}.\n", "{{SIDE}}"),
        ]
        for name, text, placeholder in cases:
            with self.subTest(prompt=name):
                self.own(name, text)
                out = self.failing()
                self.assertIn(f"prompts/{name}.md", out["error"])
                self.assertIn(f"unknown placeholder {placeholder}", out["error"])
                (self.repo / ".cross-review" / "prompts" / f"{name}.md").unlink()
        self.assertEqual(self.prompts()["prompts"]["examine"]["layer"], "plugin")

    def test_опечатка_в_имени_промта_ошибка(self) -> None:
        self.own("examin", "Бриф.\n")
        out = self.failing()
        self.assertIn("examin", out["error"])
        self.assertIn("no plugin prompt", out["error"])


class RefTest(PromptsCase):
    """--ref: the tree note says whether the working tree contains the commit under review."""

    def commit(self, name: str) -> str:
        (self.repo / name).write_text(f"{name}\n")
        git(self.repo, "add", name)
        git(self.repo, "commit", "-q", "-m", name)
        return git(self.repo, "rev-parse", "--short", "HEAD").strip()

    def test_ref_вне_рабочего_дерева_читать_через_git_show(self) -> None:
        old = self.commit("a.txt")
        new = self.commit("b.txt")
        git(self.repo, "checkout", "-q", old)
        out = self.prompts("--ref", new)
        self.assertIn(f"does **not** contain `{new}`", out["tree"])
        for name in ("prompt-r1-claude.md", "prompt-r1-codex.md", "prompt-moderator.md", "prompt-r2-codex.fresh.md"):
            with self.subTest(file=name):
                self.assertIn(f"Read every file through `git show {new}:<path>`", self.text(name))

    def test_состояния_дерева(self) -> None:
        old = self.commit("a.txt")
        new = self.commit("b.txt")
        cases = [
            (None, "Cite lines as the working tree has them."),
            (new, f"It contains `{new}`, the commit under review"),
            (old, f"It contains `{old}`, the commit under review"),
            ("no-such-ref", "The ref `no-such-ref` does not resolve here"),
        ]
        for ref, expected in cases:
            with self.subTest(ref=ref):
                note = debate_prompts.tree_state(str(self.repo), ref)
                self.assertTrue(note.startswith(f"The working tree is `master` at `{new}`."), note)
                self.assertIn(expected, note)
        (self.repo / "a.txt").write_text("changed\n")
        self.assertIn(", with uncommitted changes.", debate_prompts.tree_state(str(self.repo), None))

    def test_репозиторий_без_коммитов(self) -> None:
        note = debate_prompts.tree_state(str(git_init(self.root / "empty")), None)
        self.assertTrue(note.startswith("The working tree is `"), note)
        self.assertIn(", with no commit yet", note)


class ExternalRunTest(unittest.TestCase):
    """An external run outside .cross-review/runs/: the layers come from --workdir and the manifest is not touched."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.round = copy_fixture("run-basic", self.root / "external" / "tasks" / "demo" / "01-initial")
        (self.round / "input").mkdir()
        (self.round / "input" / "scope.md").write_text("`git diff master`\n")
        self.workdir = git_init(self.root / "repo", commit=True)
        res = run("debate_build", self.round)
        self.assertEqual(res.returncode, 0, res.stderr)

    def launch(self) -> dict:
        res = run("debate_prompts", self.round, "--subject", SUBJECT, "--workdir", self.workdir)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        out = last_json_line(res.stdout)
        return {(e["step"], e["vendor"]): e for e in out["launch"]}

    def test_внешний_прогон_на_моделях_ревью_без_манифеста(self) -> None:
        launch = self.launch()
        debate = self.round / "debate"
        self.assertIn(f"`{self.workdir}`", (debate / "prompt-r1-claude.md").read_text())
        self.assertEqual((launch["r1", "claude"]["alias"], launch["r1", "claude"]["effort"]), ("opus", "high"))
        self.assertEqual(launch["r1", "codex"]["command"], shlex.join([
            "bash", str(SCRIPTS / "codex_run.sh"), str(self.workdir), str(debate / "prompt-r1-codex.md"),
            str(debate / "schema-examine.json"), str(debate / "codex-r1.json"), "gpt-6.1-sol", "high"]))
        self.assertEqual(launch["moderate", "claude"]["alias"], "fable")
        self.assertEqual(launch["moderate", "codex"]["model"], "gpt-6-astra")
        self.assertFalse((self.round / "manifest.json").exists())

    def test_модераторы_внешнего_прогона_из_конфига_workdir(self) -> None:
        own = self.workdir / ".cross-review" / "cross-review.json"
        own.parent.mkdir(parents=True)
        own.write_text(json.dumps({"tier": "expert"}))
        launch = self.launch()
        self.assertEqual((launch["moderate", "claude"]["alias"], launch["moderate", "claude"]["effort"]), ("opus", "xhigh"))
        self.assertEqual(launch["moderate", "codex"]["model"], "gpt-6.1-sol")
        self.assertEqual(launch["r1", "claude"]["alias"], "opus")

    def test_слой_репозитория_из_workdir(self) -> None:
        own = self.workdir / ".cross-review" / "prompts" / "respond.md"
        own.parent.mkdir(parents=True)
        own.write_text("Раунд 2 для {{SIDE}}: `{{INPUT}}`.\n\n{{OUTPUT}}\n")
        res = run("debate_prompts", self.round, "--subject", SUBJECT, "--workdir", self.workdir)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertEqual(last_json_line(res.stdout)["prompts"]["respond"]["layer"], "repo")
        self.assertTrue((self.round / "debate" / "prompt-r2-claude.md").read_text().startswith("Раунд 2 для Claude:"))

    def test_рабочий_каталог_по_умолчанию_текущий(self) -> None:
        res = run("debate_prompts", self.round, "--subject", SUBJECT, cwd=self.workdir)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn(f"(repository root: `{self.workdir}`)",
                      (self.round / "debate" / "prompt-moderator.md").read_text())


class ErrorsTest(unittest.TestCase):
    """What the script refuses: no findings.json, a malformed --context, a missing --subject."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def test_без_findings_json_выход_1(self) -> None:
        res = run("debate_prompts", self.root, "--subject", SUBJECT)
        self.assertEqual(res.returncode, 1)
        self.assertIn("findings.json", last_json_line(res.stdout)["error"])
        self.assertFalse((self.root / "debate").exists())

    def test_неверные_аргументы_выход_2(self) -> None:
        cases = [
            ("context без WHY", [self.root, "--subject", SUBJECT, "--context", "/tmp/x.md"]),
            ("без subject", [self.root]),
        ]
        for label, args in cases:
            with self.subTest(case=label):
                res = run("debate_prompts", *args)
                self.assertEqual(res.returncode, 2)
                self.assertIn("usage:", res.stderr)
                self.assertEqual(res.stdout, "")


class PanelTest(unittest.TestCase):
    """The panel sentence from the run's agents."""

    def test_фраза_панели(self) -> None:
        def agents(n: int, side: str, model: str) -> list[dict]:
            return [{"executor": side, "requested_model": model} for _ in range(n)]

        cases = [
            ("обе стороны", agents(1, "claude", "opus") + agents(1, "codex", "gpt-6.1-sol"),
             "Two reviewers — one Claude (opus) and one Codex (gpt-6.1-sol) —"),
            ("фактическая модель главнее", [{"executor": "claude", "requested_model": "opus", "actual_model": "claude-opus-5-5"}],
             "One reviewer — one Claude (claude-opus-5-5) —"),
            ("больше восьми", agents(5, "claude", "opus") + agents(5, "codex", "gpt-6.1-sol"),
             "10 reviewers — five Claude (opus) and five Codex (gpt-6.1-sol) —"),
            ("без агентов", [], "The panel's reviewers"),
        ]
        for label, given, expected in cases:
            with self.subTest(case=label):
                self.assertEqual(debate_prompts.panel_sentence(given), expected)


if __name__ == "__main__":
    unittest.main()
