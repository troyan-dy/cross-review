from __future__ import annotations

import json
import shlex
import shutil
import tempfile
import types
import unittest
from pathlib import Path

from support import SCRIPTS, composed_run, git, git_init, last_json_line, pick, read_json, run

from crossreview.debate_context import tree_state
from crossreview.debate_panel import panel_sentence

SUBJECT = "MR !42 `feature/DEMO-1` (диапазон a1b2c3..d4e5f6)"
BASIC = SCRIPTS / "tests" / "fixtures" / "run-basic"
CODEX_RUN = str(SCRIPTS / "codex_run.sh")
PROMPT_FILES = (
    "prompt-r1-claude.md",
    "prompt-r1-codex.md",
    "prompt-r2-claude.md",
    "prompt-r2-codex.md",
    "prompt-r2-codex.fresh.md",
    "prompt-moderator.md",
    "prompt-moderator-claude.md",
    "prompt-moderator-codex.md",
)
SCHEMA_FILES = ("schema-examine.json", "schema-respond.json", "schema-moderate.json")
# round 2 continues the round-1 session and names neither the subject nor the repository
CONTINUED = ("prompt-r2-claude.md", "prompt-r2-codex.md")
FIRST_ROUND = ("prompt-r1-claude.md", "prompt-r1-codex.md")
PANEL = "Four reviewers — two Claude (claude-opus-5-5) and two Codex (gpt-6.1-sol) —"
CODEX_OUTPUT = "Return JSON matching the output schema."
SEPARATOR = "\n\n---\n\n"
SOL = "gpt-6.1-sol"
STEPS = (
    ("r1", "claude"),
    ("r1", "codex"),
    ("r2", "claude"),
    ("r2", "codex"),
    ("moderate", "session"),
    ("moderate", "claude"),
    ("moderate", "codex"),
)
RUN_PROMPTS = ("prompts/review.md", "prompts/examine.md", "prompts/respond.md", "prompts/moderate.md")
ALL_PLUGIN = types.MappingProxyType({"examine": "plugin", "respond": "plugin", "moderate": "plugin"})
OWN_EXAMINE = "".join((
    "---\ndescription: своя редакция\n---\nСвой бриф для стороны {{SIDE}} против {{OTHER}}.\n",
    "Читай `{{INPUT}}`.\n\n{{OUTPUT}}\n",
))
FRESH_TAIL = "".join((
    "Round 1 is over: your verdicts on these findings are in `{debate}/codex-r1.json` ",
    "(no such file means you had nothing to examine). Do not examine them again; answer round 2 below.",
))


def required(path: Path) -> list[str]:
    return read_json(path)["required"]


def codex_command(workdir: Path, files: list[str], *tail: str) -> str:
    """The codex_run.sh command line of a Codex step: workdir, the step's files, model, effort and the rest."""
    argv = ["bash", CODEX_RUN, str(workdir), *files, *tail]
    return shlex.join(argv)


def by_step(launches: list[dict]) -> dict[tuple, dict]:
    return {pick(launch, "step", "vendor"): launch for launch in launches}


def panel_agents(count: int, side: str, model: str) -> list[dict]:
    return [{"executor": side, "requested_model": model} for _ in range(count)]


class PromptsCase(unittest.TestCase):
    """A cross-review run of the demo task holding the verified round of run-basic, with the debate built."""

    def setUp(self) -> None:
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name).resolve()
        self.run_dir = composed_run(self.root)
        self.repo = self.run_dir.parents[3]
        self.debate = self.run_dir / "debate"
        self.manifest_path = self.run_dir / "manifest.json"
        for name in ("findings.json", "events.jsonl"):
            shutil.copy(BASIC / name, self.run_dir / name)
        shutil.copytree(BASIC / "stages", self.run_dir / "stages")
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

    def own_path(self, name: str) -> Path:
        return self.repo / ".cross-review" / "prompts" / f"{name}.md"

    def own(self, name: str, text: str) -> None:
        path = self.own_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def manifest(self) -> dict:
        return read_json(self.manifest_path)

    def write_manifest(self, manifest: dict) -> None:
        self.manifest_path.write_text(json.dumps(manifest))

    def assert_texts(self, cases: tuple) -> None:
        """Every (prompt file, expected text) of cases: the prompt holds the text."""
        for name, expected in cases:
            with self.subTest(file=name, expected=expected):
                self.assertIn(expected, self.text(name))


class FilesTest(PromptsCase):
    """Every prompt and schema lands in <run_dir>/debate/, with nothing left to substitute."""

    def test_генерируются_все_файлы(self) -> None:
        out = self.prompts()
        self.assertEqual(out["debate_dir"], str(self.debate))
        written = PROMPT_FILES + SCHEMA_FILES
        self.assertEqual(sorted(out["wrote"]), sorted(written))
        for name in written:
            with self.subTest(file=name):
                self.assertTrue((self.debate / name).is_file())
        self.assertEqual(required(self.debate / "schema-examine.json"), ["verdicts", "open_question_answers"])
        self.assertEqual(required(self.debate / "schema-respond.json"), ["responses", "side_findings"])
        self.assertEqual(required(self.debate / "schema-moderate.json"), ["decisions", "extra"])

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
        prompt = other / "prompt-r1-claude.md"
        self.assertTrue(prompt.is_file())
        self.assertIn(f"`{other}/for-claude.json`", prompt.read_text())

    def test_повторный_запуск_перезаписывает(self) -> None:
        self.prompts()
        self.prompts("--context", f"{self.root}/note.md=заметка")
        self.assertIn(f"`{self.root}/note.md` — заметка", self.text("prompt-r1-codex.md"))


class SubstitutionTest(PromptsCase):
    """Subject, paths, panel, context and the language rule reach every prompt they belong in."""

    def test_предмет_пути_и_панель_подставлены(self) -> None:
        self.prompts()
        named = [name for name in PROMPT_FILES if name not in CONTINUED]
        for name in named:
            with self.subTest(file=name):
                self.assertIn(SUBJECT, self.text(name))
                self.assertIn(f"`{self.repo}`", self.text(name))
        debate = self.debate
        self.assert_texts((
            ("prompt-r1-claude.md", f"`{debate}/for-claude.json`"),
            ("prompt-r1-codex.md", f"`{debate}/for-codex.json`"),
            ("prompt-r2-claude.md", f"`{debate}/for-claude-r2.json`"),
            ("prompt-r2-codex.md", f"`{debate}/for-codex-r2.json`"),
            ("prompt-moderator.md", f"`{debate}/disputes.json`"),
            ("prompt-moderator-codex.md", f"`{debate}/disputes.json`"),
        ))
        for name in FIRST_ROUND:
            with self.subTest(panel=name):
                self.assertIn(f"{PANEL} reviewed it independently", self.text(name))
                self.assertIn("`rejected_by_verify` — the verifier rejected it", self.text(name))

    def test_стороны_названы_по_вендору(self) -> None:
        self.prompts()
        self.assertIn("You are the **Claude side**", self.text("prompt-r1-claude.md"))
        self.assertIn("you examine what only the Codex reviewers raised", self.text("prompt-r1-claude.md"))
        self.assertIn("You are the **Codex side**", self.text("prompt-r1-codex.md"))
        examined = "The Claude side has examined the findings the Codex reviewers raised"
        self.assertIn(examined, self.text("prompt-r2-codex.md"))

    def test_контекст_прогона_и_дополнительный(self) -> None:
        inputs = self.run_dir / "input"
        (inputs / "goal.md").write_text("Цель изменения.\n")
        extra = self.root / "design.md"
        out = self.prompts("--context", f"{extra}=дизайн решения")
        scope = inputs / "scope.md"
        expected = [str(path) for path in (scope, inputs / "goal.md", extra)]
        self.assertEqual(out["context"], expected)
        self.assert_texts(
            (name, line)
            for name in ("prompt-r1-claude.md", "prompt-r1-codex.md", "prompt-moderator.md")
            for line in (f"- `{scope}` — what was reviewed", f"- `{extra}` — дизайн решения")
        )

    def test_вывод_сторон(self) -> None:
        self.prompts()
        debate = self.debate
        self.assert_texts((
            ("prompt-r1-claude.md", f"`{debate}/claude-r1.json` with the Write tool"),
            ("prompt-r1-claude.md", f"matching `{debate}/schema-examine.json`"),
            ("prompt-r2-claude.md", f"`{debate}/claude-r2.json` with the Write tool"),
            ("prompt-r2-claude.md", f"matching `{debate}/schema-respond.json`"),
            ("prompt-moderator.md", f"`{debate}/moderator.json` as a JSON array"),
            ("prompt-moderator.md", f"`{debate}/extra.json` as a JSON array"),
            ("prompt-moderator-claude.md", f"`{debate}/moderator-claude.json` as a JSON array"),
            ("prompt-moderator-claude.md", f"`{debate}/extra-claude.json` as a JSON array"),
        ))
        for name in ("prompt-r1-codex.md", "prompt-r2-codex.md", "prompt-moderator-codex.md"):
            with self.subTest(codex=name):
                self.assertTrue(self.text(name).rstrip().endswith(CODEX_OUTPUT))
                self.assertNotIn("Write tool", self.text(name))
                self.assertNotIn("moderator.json", self.text(name))

    def test_русский_язык_в_каждом_промте(self) -> None:
        self.prompts()
        self.assert_texts((
            ("prompt-r1-claude.md", "Write every `argument` and every open question's `answer` in Russian"),
            ("prompt-r2-codex.md", "and the `title`, `body` and `fix` of every side finding, in Russian"),
            ("prompt-moderator-codex.md", "Write every `verdict` and `note`"),
        ))

    def test_свежий_промт_r2_codex(self) -> None:
        self.prompts()
        fresh = self.text("prompt-r2-codex.fresh.md")
        head, sep, tail = fresh.partition(SEPARATOR)
        self.assertEqual(sep, SEPARATOR)
        self.assertEqual(tail, self.text("prompt-r2-codex.md"))
        self.assertEqual(head, self.fresh_head())
        self.assertEqual(fresh.count(CODEX_OUTPUT), 1)

    def fresh_head(self) -> str:
        """The round-1 Codex prompt without its output rule, then the note that round 1 is over."""
        prompt = self.text("prompt-r1-codex.md").rstrip("\n")
        first = prompt.removesuffix(CODEX_OUTPUT).rstrip("\n")
        tail = FRESH_TAIL.format(debate=self.debate)
        return f"{first}\n\n{tail}"


class LaunchTest(PromptsCase):
    """The launch plan: every step's files, and for a cross-review run the models and codex_run.sh commands."""

    def entry(self, out: dict, step: str, vendor: str) -> dict:
        return by_step(out["launch"])[step, vendor]

    def command(self, prompt: str, schema: str, output: str, model: str, *tail: str) -> str:
        files = self.debate_files(prompt, schema, output)
        return codex_command(self.repo, files, model, "high", *tail)

    def debate_files(self, *names: str) -> list[str]:
        return [str(self.debate / name) for name in names]

    def test_порядок_шагов(self) -> None:
        out = self.prompts()
        steps = [(launch["step"], launch["vendor"]) for launch in out["launch"]]
        self.assertEqual(tuple(steps), STEPS)

    def test_стороны_claude(self) -> None:
        out = self.prompts()
        self.assertEqual(self.entry(out, "r1", "claude"), self.claude_r1())
        self.assertEqual(self.entry(out, "r2", "claude")["continues"], "r1")
        session = self.entry(out, "moderate", "session")
        self.assertEqual(session["output"], self.debate_files("moderator.json", "extra.json"))
        moderator = self.entry(out, "moderate", "claude")
        self.assertEqual((moderator["alias"], moderator["effort"]), ("fable", "high"))
        self.assertEqual(moderator["output"], self.debate_files("moderator-claude.json", "extra-claude.json"))

    def claude_r1(self) -> dict:
        prompt, output = self.debate_files("prompt-r1-claude.md", "claude-r1.json")
        return {
            "step": "r1",
            "vendor": "claude",
            "prompt": prompt,
            "output": output,
            "alias": "opus",
            "effort": "high",
        }

    def test_стороны_codex_через_codex_run(self) -> None:
        out = self.prompts()
        self.check_codex_r1(self.entry(out, "r1", "codex"))
        self.check_codex_r2(self.entry(out, "r2", "codex"))
        moderate = ("prompt-moderator-codex.md", "schema-moderate.json", "moderator-codex.json", "gpt-6-astra")
        moderator = self.entry(out, "moderate", "codex")
        self.assertEqual(moderator["command"], self.command(*moderate))

    def check_codex_r1(self, codex_r1: dict) -> None:
        schema = str(self.debate / "schema-examine.json")
        self.assertEqual(pick(codex_r1, "model", "effort", "schema"), (SOL, "high", schema))
        examine = self.command("prompt-r1-codex.md", "schema-examine.json", "codex-r1.json", SOL)
        self.assertEqual(codex_r1["command"], examine)

    def check_codex_r2(self, codex_r2: dict) -> None:
        log, fresh = self.debate_files("codex-r1.log", "prompt-r2-codex.fresh.md")
        self.assertEqual(pick(codex_r2, "resume", "fresh"), (log, fresh))
        respond = ("schema-respond.json", "codex-r2.json", SOL)
        resumed = self.command("prompt-r2-codex.md", *respond, "--resume", log)
        self.assertEqual(codex_r2["command"], resumed)
        self.assertEqual(codex_r2["fresh_command"], self.command("prompt-r2-codex.fresh.md", *respond))

    def test_модели_из_манифеста_главнее_findings(self) -> None:
        manifest = self.manifest()
        manifest["models"] = {
            "claude": {"model": "sonnet", "effort": "medium"},
            "codex": {"model": "gpt-6-sol", "effort": "low"},
        }
        self.write_manifest(manifest)
        out = self.prompts()
        claude_r1 = self.entry(out, "r1", "claude")
        self.assertEqual((claude_r1["alias"], claude_r1["effort"]), ("sonnet", "medium"))
        files = self.debate_files("prompt-r1-codex.md", "schema-examine.json", "codex-r1.json")
        command = self.entry(out, "r1", "codex")["command"]
        self.assertEqual(command, codex_command(self.repo, files, "gpt-6-sol", "low"))

    def test_без_моделей_в_манифесте_модели_из_findings(self) -> None:
        manifest = self.manifest()
        manifest.pop("models")
        manifest.pop("moderators")
        self.write_manifest(manifest)
        out = self.prompts()
        claude_r1 = self.entry(out, "r1", "claude")
        self.assertEqual((claude_r1["alias"], claude_r1["effort"]), ("opus", "high"))
        examine = self.command("prompt-r1-codex.md", "schema-examine.json", "codex-r1.json", SOL)
        self.assertEqual(self.entry(out, "r1", "codex")["command"], examine)
        moderator = self.entry(out, "moderate", "claude")
        self.assertEqual((moderator["alias"], moderator["effort"]), ("fable", "high"))
        self.assertEqual(self.entry(out, "moderate", "codex")["model"], "gpt-6-astra")
        self.assertIn("resume", self.entry(out, "r2", "codex"))

    def test_неизвестная_модель_в_findings_выход1(self) -> None:
        manifest = self.manifest()
        manifest.pop("models")
        self.write_manifest(manifest)
        findings = read_json(self.run_dir / "findings.json")
        for agent in findings["sources"]["agents"]:
            if agent["executor"] == "claude":
                agent.update(requested_model="claude-mystery-1", actual_model="claude-mystery-1")
        (self.run_dir / "findings.json").write_text(json.dumps(findings))
        self.assertIn("claude-mystery-1", self.failing()["error"])


class LayerTest(PromptsCase):
    """The prompts resolve through .cross-review/ of the reviewed repository, and the run's manifest says so."""

    def manifest_layers(self) -> dict[str, str]:
        return {source["path"]: source["layer"] for source in self.manifest()["prompts"]}

    def test_промты_спора_в_манифесте(self) -> None:
        out = self.prompts()
        resolved = out["prompts"]
        layers = {name: resolved[name]["layer"] for name in resolved}
        self.assertEqual(layers, ALL_PLUGIN)
        self.prompts()
        prompts = [path for path in self.manifest_layers() if path.startswith("prompts/")]
        self.assertEqual(tuple(prompts), RUN_PROMPTS)
        self.assertEqual({self.manifest_layers()[path] for path in prompts}, {"plugin"})

    def test_override_examine_из_репозитория(self) -> None:
        self.own("examine", OWN_EXAMINE)
        out = self.prompts()
        self.assertEqual(out["prompts"]["examine"]["layer"], "repo")
        self.assertEqual(out["prompts"]["respond"]["layer"], "plugin")
        text = self.text("prompt-r1-codex.md")
        self.assertTrue(text.startswith("Свой бриф для стороны Codex против Claude.\n"))
        self.assertIn(f"Читай `{self.debate}/for-codex.json`.", text)
        self.assertNotIn("description:", text)
        self.assertTrue(self.text("prompt-r2-codex.fresh.md").startswith("Свой бриф для стороны Codex"))
        self.assertTrue(self.text("prompt-r2-claude.md").startswith("Round 2."))
        layers = self.manifest_layers()
        self.assertEqual((layers["prompts/examine.md"], layers["prompts/respond.md"]), ("repo", "plugin"))

    def test_неизвестный_плейсхолдер_ошибка(self) -> None:
        cases = (
            ("examine", "Сторона {{SIDE}}, {{NOPE}}.\n", "{{NOPE}}"),
            ("respond", "Ответ {{SIDE}} на {{INPUT}} с {{SCOPE}}.\n", "{{SCOPE}}"),
            # у модератора нет стороны
            ("moderate", "Модератор {{SIDE}}.\n", "{{SIDE}}"),
        )
        for name, text, placeholder in cases:
            with self.subTest(prompt=name):
                self.own(name, text)
                error = self.failing()["error"]
                self.assertIn(f"prompts/{name}.md", error)
                self.assertIn(f"unknown placeholder {placeholder}", error)
                self.own_path(name).unlink()
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
        for name in (*FIRST_ROUND, "prompt-moderator.md", "prompt-r2-codex.fresh.md"):
            with self.subTest(file=name):
                self.assertIn(f"Read every file through `git show {new}:<path>`", self.text(name))

    def test_состояния_дерева(self) -> None:
        old = self.commit("a.txt")
        new = self.commit("b.txt")
        cases = (
            (None, "Cite lines as the working tree has them."),
            (new, f"It contains `{new}`, the commit under review"),
            (old, f"It contains `{old}`, the commit under review"),
            ("no-such-ref", "The ref `no-such-ref` does not resolve here"),
        )
        for ref, expected in cases:
            with self.subTest(ref=ref):
                self.check_note(ref, expected, new)
        (self.repo / "a.txt").write_text("changed\n")
        self.assertIn(", with uncommitted changes.", tree_state(str(self.repo), None))

    def check_note(self, ref: str | None, expected: str, head: str) -> None:
        note = tree_state(str(self.repo), ref)
        self.assertTrue(note.startswith(f"The working tree is `master` at `{head}`."), note)
        self.assertIn(expected, note)

    def test_репозиторий_без_коммитов(self) -> None:
        note = tree_state(str(git_init(self.root / "empty")), None)
        self.assertTrue(note.startswith("The working tree is `"), note)
        self.assertIn(", with no commit yet", note)


class ExternalRunTest(unittest.TestCase):
    """An external run outside .cross-review/runs/: the layers come from --workdir and the manifest is not touched."""

    def setUp(self) -> None:
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name).resolve()
        task = self.root / "external" / "tasks" / "demo"
        self.round = task / "01-initial"
        shutil.copytree(BASIC, self.round)
        self.debate = self.round / "debate"
        (self.round / "input").mkdir()
        (self.round / "input" / "scope.md").write_text("`git diff master`\n")
        self.workdir = git_init(self.root / "repo", commit=True)
        res = run("debate_build", self.round)
        self.assertEqual(res.returncode, 0, res.stderr)

    def prompts(self, *args: object, cwd: Path | None = None) -> dict:
        res = run("debate_prompts", self.round, "--subject", SUBJECT, *args, cwd=cwd)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return last_json_line(res.stdout)

    def launch(self) -> dict:
        out = self.prompts("--workdir", self.workdir)
        return by_step(out["launch"])

    def text(self, name: str) -> str:
        return (self.debate / name).read_text()

    def own(self, name: str, text: str) -> None:
        own = self.workdir / ".cross-review" / name
        own.parent.mkdir(parents=True)
        own.write_text(text)

    def test_внешний_прогон_на_моделях_ревью_без_манифеста(self) -> None:
        launch = self.launch()
        self.assertIn(f"`{self.workdir}`", self.text("prompt-r1-claude.md"))
        claude_r1 = launch["r1", "claude"]
        self.assertEqual((claude_r1["alias"], claude_r1["effort"]), ("opus", "high"))
        files = [str(self.debate / name) for name in ("prompt-r1-codex.md", "schema-examine.json", "codex-r1.json")]
        command = codex_command(self.workdir, files, SOL, "high")
        self.assertEqual(launch["r1", "codex"]["command"], command)
        self.assertEqual(launch["moderate", "claude"]["alias"], "fable")
        self.assertEqual(launch["moderate", "codex"]["model"], "gpt-6-astra")
        self.assertFalse((self.round / "manifest.json").exists())

    def test_модераторы_внешнего_прогона_из_конфига_workdir(self) -> None:
        self.own("cross-review.json", json.dumps({"tier": "expert"}))
        launch = self.launch()
        moderator = launch["moderate", "claude"]
        self.assertEqual((moderator["alias"], moderator["effort"]), ("opus", "xhigh"))
        self.assertEqual(launch["moderate", "codex"]["model"], SOL)
        self.assertEqual(launch["r1", "claude"]["alias"], "opus")

    def test_слой_репозитория_из_workdir(self) -> None:
        self.own("prompts/respond.md", "Раунд 2 для {{SIDE}}: `{{INPUT}}`.\n\n{{OUTPUT}}\n")
        out = self.prompts("--workdir", self.workdir)
        self.assertEqual(out["prompts"]["respond"]["layer"], "repo")
        self.assertTrue(self.text("prompt-r2-claude.md").startswith("Раунд 2 для Claude:"))

    def test_рабочий_каталог_по_умолчанию_текущий(self) -> None:
        self.prompts(cwd=self.workdir)
        self.assertIn(f"(repository root: `{self.workdir}`)", self.text("prompt-moderator.md"))


class ErrorsTest(unittest.TestCase):
    """What the script refuses: no findings.json, a malformed --context, a missing --subject."""

    def setUp(self) -> None:
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name).resolve()

    def test_без_findings_json_выход1(self) -> None:
        res = run("debate_prompts", self.root, "--subject", SUBJECT)
        self.assertEqual(res.returncode, 1)
        self.assertIn("findings.json", last_json_line(res.stdout)["error"])
        self.assertFalse((self.root / "debate").exists())

    def test_неверные_аргументы_выход2(self) -> None:
        cases = (
            ("context без WHY", [self.root, "--subject", SUBJECT, "--context", "/tmp/x.md"]),
            ("без subject", [self.root]),
        )
        for label, args in cases:
            with self.subTest(case=label):
                res = run("debate_prompts", *args)
                self.assertEqual(res.returncode, 2)
                self.assertIn("usage:", res.stderr)
                self.assertEqual(res.stdout, "")


class PanelTest(unittest.TestCase):
    """The panel sentence from the run's agents."""

    def test_фраза_панели(self) -> None:
        actual = {"executor": "claude", "requested_model": "opus", "actual_model": "claude-opus-5-5"}
        cases = (
            ("обе стороны", panel_agents(1, "claude", "opus") + panel_agents(1, "codex", SOL),
             "Two reviewers — one Claude (opus) and one Codex (gpt-6.1-sol) —"),
            ("фактическая модель главнее", [actual], "One reviewer — one Claude (claude-opus-5-5) —"),
            ("больше восьми", panel_agents(5, "claude", "opus") + panel_agents(5, "codex", SOL),
             "10 reviewers — five Claude (opus) and five Codex (gpt-6.1-sol) —"),
            ("без агентов", [], "The panel's reviewers"),
        )
        for label, given, expected in cases:
            with self.subTest(case=label):
                self.assertEqual(panel_sentence(given), expected)


if __name__ == "__main__":
    unittest.main()
