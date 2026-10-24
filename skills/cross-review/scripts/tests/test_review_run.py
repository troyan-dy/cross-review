from __future__ import annotations

import json
import shutil
import tempfile
import types
import unittest
from datetime import timezone
from pathlib import Path

from support import SCRIPTS, git, git_init, last_json_line, pick, read_json, run, write_json

from crossreview import runs
from crossreview.prior import prior_runs
from crossreview.errors import ConfigError

RUNS = Path(".cross-review") / "runs"
SCHEMA = SCRIPTS.parent / "assets" / "config" / "schemas" / "finder.json"
BASIC = SCRIPTS / "tests" / "fixtures" / "run-basic"
SYMLINK = "a symlink in the repository's .cross-review/"
SCOPE_IN_GIT = ".cross-review/runs/demo/01-initial/input/scope.md"
DEMO = ("--task", "demo")
AGENT_FIELDS = ("name", "executor", "requested_model", "effort", "lenses")
SOL = "gpt-6.1-sol"
AGENTS = (
    ("bugs-claude", "claude", "opus", "high", ["bugs", "impl"]),
    ("bugs-codex", "codex", SOL, "high", ["bugs", "impl"]),
    ("arch-claude", "claude", "opus", "high", ["architecture", "quality"]),
    ("arch-codex", "codex", SOL, "high", ["architecture", "quality"]),
)
MANIFEST_PROMPTS = (
    "prompts/review.md",
    "lenses/architecture.md",
    "lenses/bugs.md",
    "lenses/impl.md",
    "lenses/quality.md",
    "schemas/finder.json",
)
MODELS = types.MappingProxyType({
    "claude": {"model": "opus", "effort": "high"},
    "codex": {"model": SOL, "effort": "high"},
})
CLAUDE_LAUNCH = ("agent", "vendor", "alias", "effort")
CLAUDE_LAUNCH_VALUES = ("bugs-claude", "claude", "opus", "high")
CODEX_RUN = str(SCRIPTS / "codex_run.sh")
SCHEMA_LINE = "Return ONLY a JSON object matching the schema below. No prose before or after it.\n\nSchema:\n"
PRIOR_LINE = r"\n  00-old  2026-10-20T07:00Z  4 findings \(1 critical, 2 major, 1 minor\)  sources 4/4\n"
OWN_REVIEW = "---\ndescription: свой промт\n---\nСвой обзор. Scope: {{SCOPE}}. Workdir: {{WORKDIR}}.\n"
MINUTE = "%Y-%m-%dT%H:%MZ"


def copy_basic(dest: Path) -> Path:
    """A copy of the run-basic fixture at dest."""
    shutil.copytree(BASIC, dest)
    return dest


def minute(stamp: str) -> str:
    """A timestamp the run format writes, as a UTC minute."""
    return runs.parse_time(stamp).astimezone(timezone.utc).strftime(MINUTE)


class RepoCase(unittest.TestCase):
    """A fresh git repository per test."""

    def setUp(self) -> None:
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.base = Path(workspace.name).resolve()
        self.repo = git_init(self.base / "repo")
        self.runs = self.repo / RUNS
        self.task_dir = self.runs / "demo"

    def review_run(self, *args: object, code: int = 0, cwd: Path | None = None) -> dict:
        """Run review_run.py, check its exit code and return the JSON line it prints last."""
        res = run("review_run", *args, cwd=cwd)
        self.assertEqual(res.returncode, code, res.stdout + res.stderr)
        return last_json_line(res.stdout)

    def assert_usage(self, *args: object) -> None:
        """review_run.py rejects these arguments with exit code 2 and its usage line."""
        res = run("review_run", *args)
        self.assertEqual(res.returncode, 2)
        self.assertIn("usage:", res.stderr)

    def new(self, *args: object, repo: Path | None = None) -> dict:
        return self.review_run("new", repo or self.repo, *DEMO, *args)

    def new_error(self, *args: object) -> str:
        failed = self.review_run("new", self.repo, *DEMO, *args, code=1)
        return failed["error"]

    def untracked(self, repo: Path | None = None) -> list[str]:
        """What `git status` shows under .cross-review/: the runs must never appear there."""
        out = git(repo or self.repo, "status", "--porcelain", "--untracked-files=all")
        return [line for line in out.splitlines() if ".cross-review" in line]

    def prior_run(self, name: str) -> Path:
        """A finished run of the demo task: the run-basic fixture with a manifest."""
        run_dir = copy_basic(self.task_dir / name)
        write_json(run_dir / "manifest.json", {"task": "demo", "run": name})
        return run_dir


class NewTest(RepoCase):
    """`new` creates the run, its input/ and task.md, and keeps runs out of git."""

    def test_создаёт_каталоги_и_task_md(self) -> None:
        out = self.new()
        run_dir = self.task_dir / "01-initial"
        self.assertEqual(out["run"], "01-initial")
        self.assertEqual(Path(out["run_dir"]), run_dir)
        inputs = run_dir / "input"
        self.assertTrue((inputs / "context").is_dir())
        expected = (str(inputs / "scope.md"), str(inputs / "context"))
        self.assertEqual(pick(out["input"], "scope", "context"), expected)
        task_md = (self.task_dir / "task.md").read_text()
        self.assertTrue(task_md.startswith("---\n"))
        self.assertEqual(out["previous"], [])

    def test_каталог_прогонов_по_ссылке_наружу_отклоняется(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (self.repo / ".cross-review").mkdir()
        self.runs.symlink_to(outside)
        self.assertIn(SYMLINK, self.new_error())
        self.assertEqual(list(outside.iterdir()), [])

    def test_каталог_прогонов_по_ссылке_внутрь_репозитория_отклоняется(self) -> None:
        source = self.repo / "src"
        source.mkdir()
        (self.repo / ".cross-review").mkdir()
        self.runs.symlink_to(Path("..") / "src")
        self.assertIn(SYMLINK, self.new_error())
        self.assertEqual(list(source.iterdir()), [])

    def test_task_md_по_ссылке_наружу_отклоняется(self) -> None:
        target = self.base / "planted.md"
        self.task_dir.mkdir(parents=True)
        (self.task_dir / "task.md").symlink_to(target)
        self.assertIn(SYMLINK, self.new_error())
        self.assertFalse(target.exists())

    def test_прошлый_прогон_по_ссылке_наружу_отклоняется(self) -> None:
        # прошлый прогон другого проекта: промт велит агентам прочитать его report.md и findings.json
        outside = copy_basic(self.base / "outside-run")
        write_json(outside / "manifest.json", {"task": "other"})
        self.task_dir.mkdir(parents=True)
        linked = self.task_dir / "01-old"
        linked.symlink_to(outside)
        self.assertIn(f"{linked}: {SYMLINK}", self.new_error())
        self.assertEqual([entry.name for entry in self.task_dir.iterdir()], ["01-old"])

    def test_файл_прошлого_прогона_по_ссылке_отклоняется(self) -> None:
        secret = self.base / "secret.md"
        secret.write_text("секрет")
        report = self.prior_run("01-initial") / "report.md"
        report.symlink_to(secret)
        self.assertIn(f"{report}: {SYMLINK}", self.new_error())
        self.assertFalse((self.task_dir / "02-after-fix").exists())

    def test_task_md_не_перезаписывается(self) -> None:
        self.new()
        task_file = self.task_dir / "task.md"
        task_file.write_text("своё описание\n")
        self.new()
        self.assertEqual(task_file.read_text(), "своё описание\n")

    def test_прогоны_скрыты_вложенным_gitignore(self) -> None:
        first = self.new()
        ignore = self.runs / ".gitignore"
        assert_ignore = {"path": str(ignore), "ignored": True}
        self.assertEqual(first["ignore"], {**assert_ignore, "added": True})
        self.assertEqual(ignore.read_text(), "*\n")
        self.assertEqual(self.untracked(), [])
        self.assertEqual(self.new()["ignore"], {**assert_ignore, "added": False})
        exclude = self.repo / ".git" / "info" / "exclude"
        self.assertFalse(exclude.exists() and ".cross-review" in exclude.read_text())

    def test_белый_список_в_gitignore_не_возвращает_прогоны(self) -> None:
        # как в scratch: всё игнорируется, а файлы с точкой и каталоги возвращаются; info/exclude тут бессилен
        (self.repo / ".gitignore").write_text("*\n!*.*\n!*/\n")
        out = self.new()
        self.assertTrue(out["ignore"]["ignored"])
        self.assertEqual(self.untracked(), [])
        git(self.repo, "check-ignore", "-q", SCOPE_IN_GIT)

    def test_чужое_содержимое_gitignore_прогонов_переписывается(self) -> None:
        self.runs.mkdir(parents=True)
        ignore = self.runs / ".gitignore"
        ignore.write_text("*.tmp\n")
        self.assertTrue(self.new()["ignore"]["added"])
        self.assertEqual(ignore.read_text(), "*\n")
        self.assertEqual(self.untracked(), [])

    def test_gitignore_прогонов_по_ссылке_отклоняется(self) -> None:
        target = self.base / "planted"
        self.runs.mkdir(parents=True)
        (self.runs / ".gitignore").symlink_to(target)
        self.assertIn(SYMLINK, self.new_error())
        self.assertFalse(target.exists())

    def test_прогоны_скрыты_в_worktree(self) -> None:
        main = git_init(self.base / "main", commit=True)
        worktree = self.base / "wt"
        git(main, "worktree", "add", "-q", str(worktree))
        ignore = self.new(repo=worktree)["ignore"]
        self.assertTrue(ignore["ignored"])
        self.assertEqual(Path(ignore["path"]), worktree / RUNS / ".gitignore")
        self.assertEqual(self.untracked(worktree), [])

    def test_прогоны_скрыты_в_подкаталоге(self) -> None:
        package = self.repo / "packages" / "lib"
        package.mkdir(parents=True)
        out = self.new(repo=package)
        self.assertTrue(out["ignore"]["ignored"])
        git(package, "check-ignore", "-q", SCOPE_IN_GIT)
        self.assertFalse(self.new(repo=package)["ignore"]["added"])

    def test_относительный_путь_репозитория(self) -> None:
        out = self.review_run("new", ".", *DEMO, cwd=self.repo)
        self.assertTrue(out["ignore"]["ignored"])

    def test_без_git_gitignore_не_пишется(self) -> None:
        plain = self.base / "plain"
        plain.mkdir()
        out = self.new(repo=plain)
        self.assertIsNone(out["ignore"])
        run_input = plain / RUNS / "demo" / "01-initial" / "input"
        self.assertTrue(run_input.is_dir())
        self.assertFalse((plain / RUNS / ".gitignore").exists())

    def test_нумерация_прогонов(self) -> None:
        self.assertEqual(self.new()["run"], "01-initial")
        write_json(self.task_dir / "01-initial" / "manifest.json", {"task": "demo"})
        self.assertEqual(self.new()["run"], "02-after-fix")
        self.assertEqual(self.new()["run"], "03-after-fix")

    def test_незавершённый_прогон_не_делает_следующий_after_fix(self) -> None:
        self.new()
        self.assertEqual(self.new()["run"], "02-initial")

    def test_явное_имя_прогона_и_занятое_имя(self) -> None:
        self.assertEqual(self.new("--run", "retry")["run"], "retry")
        self.assertIn("already exists", self.new_error("--run", "retry"))

    def test_прошлые_прогоны_в_выводе(self) -> None:
        self.prior_run("01-initial")
        out = self.new()
        self.assertEqual(out["run"], "02-after-fix")
        self.assertEqual(len(out["previous"]), 1)
        prior = out["previous"][0]
        self.assertEqual(pick(prior, "run", "findings", "sources"), ("01-initial", 4, "4/4"))
        self.assertEqual(prior["by_severity"], {"critical": 1, "major": 2, "minor": 1})

    def test_неверное_имя_выход2(self) -> None:
        cases = (
            ("--task", "../x"),
            ("--task", ".hidden"),
            ("--task", "a/b"),
            ("--task", ""),
            ("--run", "task.md"),
            ("--run", ".."),
        )
        for flag, given in cases:
            with self.subTest(flag=flag, value=given):
                named = ["new", self.repo]
                if flag == "--run":
                    named.extend(DEMO)
                self.assert_usage(*named, flag, given)

    def test_нет_каталога_репозитория_выход1(self) -> None:
        out = self.review_run("new", self.base / "missing", *DEMO, code=1)
        self.assertIn("no such directory", out["error"])


class ComposeCase(RepoCase):
    """A new run of the demo task with a scope written."""

    def setUp(self) -> None:
        super().setUp()
        self.run_dir = Path(self.new()["run_dir"])
        self.input = self.run_dir / "input"
        self.scope = self.input / "scope.md"
        self.scope.write_text("Ветка feature/x против master: `git diff master...feature/x`.\n")

    def compose(self, *args: object) -> dict:
        return self.review_run("compose", self.run_dir, *args)

    def compose_error(self, *args: object) -> str:
        return self.review_run("compose", self.run_dir, *args, code=1)["error"]

    def prompt(self, agent: str) -> str:
        return (self.run_dir / "prompts" / "agents" / f"{agent}.md").read_text()

    def manifest(self) -> dict:
        return read_json(self.run_dir / "manifest.json")

    def layer(self, rel: str, text: str) -> None:
        path = self.repo / ".cross-review" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


class ComposeTest(ComposeCase):
    """`compose` writes one prompt per roster agent, the manifest and the launch plan."""

    def test_плейсхолдеры_заменены_путями(self) -> None:
        self.compose()
        for agent in ("bugs-claude", "bugs-codex", "arch-claude", "arch-codex"):
            with self.subTest(agent=agent):
                self.check_prompt(self.prompt(agent))

    def check_prompt(self, text: str) -> None:
        self.assertNotRegex(text.split("Schema:")[0], r"\{\{[A-Z_]+\}\}")
        self.assertIn(f"`{self.scope}`", text)
        self.assertIn(f"`{self.repo}` — run every command from here", text)
        self.assertFalse(text.startswith("---"))

    def test_пустой_вход_даёт_none_provided(self) -> None:
        (self.input / "goal.md").write_text("  \n")
        self.compose()
        text = self.prompt("bugs-claude")
        lines = ("what the change is trying to achieve", "the project's own conventions", "a directory of supporting material")
        for line in lines:
            with self.subTest(line=line):
                self.assertIn(f"- `none provided` — {line}", text)

    def test_заполненный_вход_даёт_пути(self) -> None:
        (self.input / "goal.md").write_text("Цель.\n")
        (self.input / "profile.md").write_text("См. AGENTS.md.\n")
        (self.input / "context" / "ticket.md").write_text("Тикет.\n")
        self.compose()
        text = self.prompt("arch-codex")
        for name in ("goal.md", "profile.md", "context"):
            with self.subTest(name=name):
                path = self.input / name
                self.assertIn(f"`{path}`", text)
        self.assertNotIn("- `none provided` —", text)

    def test_линзы_ростера_на_месте(self) -> None:
        self.compose()
        cases = (
            ("bugs-claude", ("bugs", "impl"), "architecture"),
            ("arch-codex", ("architecture", "quality"), "bugs"),
        )
        for agent, lenses, absent in cases:
            with self.subTest(agent=agent):
                self.check_lenses(self.prompt(agent), lenses, absent)

    def check_lenses(self, text: str, lenses: tuple, absent: str) -> None:
        positions = [text.index(f"## Lens: {lens}\n") for lens in lenses]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn(f"## Lens: {absent}\n", text)
        self.assertNotIn("\ndescription:", text)

    def test_схема_finder_в_конце(self) -> None:
        self.compose()
        head, schema = self.prompt("bugs-codex").split(SCHEMA_LINE)
        self.assertEqual(json.loads(schema), read_json(SCHEMA))
        self.assertLess(head.index("## Lens: impl"), len(head))

    def test_без_прошлых_прогонов_блока_нет(self) -> None:
        self.compose()
        self.assertNotIn("Prior rounds", self.prompt("bugs-claude"))

    def test_прошлые_прогоны_перечислены(self) -> None:
        self.prior_run("00-old")
        out = self.compose()
        self.assertEqual(out["previous"], ["00-old"])
        text = self.prompt("bugs-claude")
        self.assertIn(f"Prior rounds for this task: {self.task_dir}/\n", text)
        self.assertRegex(text, PRIOR_LINE)
        self.assertNotIn("01-initial  ", text)
        self.assertIn("Re-evaluate everything independently.", text)
        self.assertLess(text.index("Re-evaluate"), text.index("Return ONLY"))

    def test_ссылка_в_прошлом_прогоне_останавливает_compose(self) -> None:
        report = self.prior_run("00-old") / "report.md"
        report.symlink_to(self.base / "secret.md")
        self.assertIn(f"{report}: {SYMLINK}", self.compose_error())
        self.assertFalse((self.run_dir / "manifest.json").exists())
        self.assertFalse((self.run_dir / "prompts").exists())

    def test_прогон_без_findings_перечислен_без_счёта(self) -> None:
        write_json(self.task_dir / "00-broken" / "manifest.json", {"task": "demo"})
        self.compose()
        self.assertRegex(self.prompt("arch-claude"), r"\n  00-broken  unknown  counts unknown  sources unknown\n")

    def test_промт_из_cross_review_перекрывает_плагин(self) -> None:
        self.layer("prompts/review.md", OWN_REVIEW)
        self.compose()
        text = self.prompt("bugs-claude")
        self.assertTrue(text.startswith(f"Свой обзор. Scope: {self.scope}. Workdir: {self.repo}."))
        prompts = self.manifest()["prompts"]
        layers = {source["path"]: source["layer"] for source in prompts}
        self.assertEqual(layers["prompts/review.md"], "repo")

    def test_новая_линза_из_репозитория(self) -> None:
        self.layer("lenses/perf.md", "## Lens: perf\n\nИщи лишние аллокации.\n")
        roster = [{"name": "perf", "lenses": ["perf", "bugs"]}]
        self.layer("cross-review.json", json.dumps({"roster": roster}))
        out = self.compose()
        agents = [launch["agent"] for launch in out["launch"]]
        self.assertEqual(agents, ["perf-claude", "perf-codex"])
        self.assertIn("## Lens: perf\n\nИщи лишние аллокации.", self.prompt("perf-codex"))
        agents_dir = self.run_dir / "prompts" / "agents"
        written = sorted(prompt.name for prompt in agents_dir.iterdir())
        self.assertEqual(written, ["perf-claude.md", "perf-codex.md"])

    def test_неизвестный_плейсхолдер_выход1(self) -> None:
        self.layer("prompts/review.md", "Обзор {{SCOPE}} и {{TICKET}}.\n")
        error = self.compose_error()
        self.assertIn("prompts/review.md", error)
        self.assertIn("{{TICKET}}", error)

    def test_манифест(self) -> None:
        self.compose()
        manifest = self.manifest()
        self.assertEqual(pick(manifest, "task", "run", "tier"), ("demo", "01-initial", "standard"))
        self.assertEqual(manifest["scope_path"], str(self.scope))
        self.assertEqual(manifest["workdir"], str(self.repo))
        self.assertIsNotNone(runs.parse_time(manifest["started_at"]))
        self.assertEqual(manifest["models"], MODELS)
        agents = [pick(agent, *AGENT_FIELDS) for agent in manifest["agents"]]
        self.assertEqual(tuple(agents), AGENTS)
        prompts = [prompt["path"] for prompt in manifest["prompts"]]
        self.assertEqual(tuple(prompts), MANIFEST_PROMPTS)
        for entry in manifest["prompts"] + manifest["agents"]:
            with self.subTest(entry=entry.get("path") or entry["name"]):
                self.assertRegex(entry.get("hash") or entry["prompt_hash"], "^[0-9a-f]{64}$")

    def test_план_запуска(self) -> None:
        out = self.compose()
        self.assertEqual(pick(out, "tier", "pair"), ("standard", False))
        claude, codex = out["launch"][:2]
        self.assertEqual(pick(claude, *CLAUDE_LAUNCH), CLAUDE_LAUNCH_VALUES)
        self.assertNotIn("model", claude)
        output = self.run_dir / "agents" / "bugs-claude.json"
        self.assertEqual(claude["output"], str(output))
        self.check_codex_launch(codex)

    def check_codex_launch(self, codex: dict) -> None:
        self.assertEqual(pick(codex, "model", "schema"), (SOL, str(SCHEMA)))
        files = [codex["prompt"], str(SCHEMA), codex["output"]]
        argv = ["bash", CODEX_RUN, str(self.repo), *files, SOL, "high"]
        self.assertEqual(codex["command"].split(), argv)

    def test_разовая_пара_моделей(self) -> None:
        out = self.compose("--claude", "sonnet:medium", "--codex", "gpt-6-sol:low")
        self.assertTrue(out["pair"])
        claude, codex = out["launch"][:2]
        self.assertEqual(pick(claude, "alias", "effort"), ("sonnet", "medium"))
        self.assertEqual(pick(codex, "model", "effort"), ("gpt-6-sol", "low"))
        self.assertEqual(self.manifest()["stages"]["model"], "sonnet")

    def test_уровень_по_имени(self) -> None:
        out = self.compose("--tier", "expert")
        self.assertEqual(out["tier"], "expert")
        expert = out["launch"][0]
        self.assertEqual(pick(expert, "alias", "effort"), ("fable", "xhigh"))

    def test_повторный_compose_до_ответов_перезаписывает(self) -> None:
        self.compose()
        goal = self.input / "goal.md"
        goal.write_text("Цель уточнена.\n")
        self.compose()
        self.assertIn(f"`{goal}`", self.prompt("bugs-claude"))


class ComposeErrorsTest(ComposeCase):
    """What `compose` refuses."""

    def test_без_scope_выход1(self) -> None:
        self.scope.unlink()
        self.assertIn("scope.md is missing or empty", self.compose_error())

    def test_после_ответов_выход1(self) -> None:
        self.compose()
        (self.run_dir / "agents" / "bugs-claude.json").write_text('{"findings": []}')
        self.assertIn("already has answers (bugs-claude.json)", self.compose_error())

    def test_не_каталог_прогона_выход1(self) -> None:
        other = self.base / "elsewhere"
        (other / "input").mkdir(parents=True)
        self.assertIn("not a run directory", self.review_run("compose", other, code=1)["error"])

    def test_неизвестный_уровень_выход1(self) -> None:
        self.assertIn("no tier 'ultra'", self.compose_error("--tier", "ultra"))

    def test_неверные_аргументы_выход2(self) -> None:
        cases = (
            ["--claude", "opus"],
            ["--codex", "gpt-6-sol"],
            ["--tier", "standard", "--claude", "opus", "--codex", "x"],
        )
        for args in cases:
            with self.subTest(args=args):
                self.assert_usage("compose", self.run_dir, *args)


class HistoryTest(unittest.TestCase):
    """The prior-run inventory and the timestamps it reads."""

    def setUp(self) -> None:
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.base = Path(workspace.name)
        self.task_dir = self.base / "demo"
        self.round = copy_basic(self.task_dir / "01-initial")

    def test_разбор_времени(self) -> None:
        cases = (
            ("2026-10-19T14:25:43.154729+03:00", "2026-10-19T11:25Z"),
            ("2026-10-19T14:25:43.154729123+03:00", "2026-10-19T11:25Z"),
            ("2026-10-19T11:04:00Z", "2026-10-19T11:04Z"),
        )
        for stamp, want in cases:
            with self.subTest(value=stamp):
                self.assertEqual(minute(stamp), want)
        for unparsed in ("", None, "вчера"):
            with self.subTest(value=unparsed):
                self.assertIsNone(runs.parse_time(unparsed))

    def test_фикстура_читается_как_прошлый_прогон(self) -> None:
        self.assertEqual(prior_runs(self.task_dir), [])
        write_json(self.round / "manifest.json", {"task": "demo"})
        found = prior_runs(self.task_dir)
        self.assertEqual(len(found), 1)
        started = read_json(BASIC / "findings.json")["stats"]["started_at"]
        self.assertEqual(found[0]["started_at"], started)
        self.assertEqual(prior_runs(self.task_dir, current="01-initial"), [])

    def test_ссылка_среди_прогонов_или_внутри_прогона_ошибка(self) -> None:
        write_json(self.round / "manifest.json", {"task": "demo"})
        # висячая ссылка — не каталог и не файл, но тоже ссылка
        dangling = self.task_dir / "02-old"
        dangling.symlink_to(self.base / "missing")
        with self.assertRaisesRegex(ConfigError, f"02-old: {SYMLINK}"):
            prior_runs(self.task_dir)
        dangling.unlink()
        found = self.round / "stages" / "1-found.json"
        found.unlink()
        found.symlink_to(self.base / "planted.json")
        with self.assertRaisesRegex(ConfigError, f"1-found.json: {SYMLINK}"):
            prior_runs(self.task_dir)
        # текущий прогон не проверяется: его пишет сама сессия
        self.assertEqual(prior_runs(self.task_dir, current="01-initial"), [])


if __name__ == "__main__":
    unittest.main()
