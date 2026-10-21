from __future__ import annotations

import json
import re
import tempfile
import unittest
from datetime import timezone
from pathlib import Path

from support import FIXTURES, SCRIPTS, SKILL, copy_fixture, git, git_init, last_json_line, read_json, run, write_json

import review_run
from cross_config import ConfigError

RUNS = Path(".cross-review") / "runs"
SCHEMA = SKILL / "assets" / "config" / "schemas" / "finder.json"


class RepoCase(unittest.TestCase):
    """A fresh git repository per test."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.repo = git_init(self.tmp / "repo")

    def new(self, *args: object, repo: Path | None = None) -> dict:
        res = run("review_run", "new", repo or self.repo, "--task", "demo", *args)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return last_json_line(res.stdout)

    def untracked(self, repo: Path | None = None) -> list[str]:
        """What `git status` shows under .cross-review/: the runs must never appear there."""
        out = git(repo or self.repo, "status", "--porcelain", "--untracked-files=all")
        return [line for line in out.splitlines() if ".cross-review" in line]

    def prior_run(self, name: str) -> Path:
        """A finished run of the demo task: the run-basic fixture with a manifest."""
        run_dir = copy_fixture("run-basic", self.repo / RUNS / "demo" / name)
        write_json(run_dir / "manifest.json", {"task": "demo", "run": name})
        return run_dir


class NewTest(RepoCase):
    """`new` creates the run, its input/ and task.md, and keeps runs out of git."""

    def test_создаёт_каталоги_и_task_md(self) -> None:
        out = self.new()
        run_dir = self.repo / RUNS / "demo" / "01-initial"
        self.assertEqual(out["run"], "01-initial")
        self.assertEqual(Path(out["run_dir"]), run_dir)
        self.assertTrue((run_dir / "input" / "context").is_dir())
        self.assertEqual(out["input"]["scope"], str(run_dir / "input" / "scope.md"))
        self.assertEqual(out["input"]["context"], str(run_dir / "input" / "context"))
        self.assertTrue((self.repo / RUNS / "demo" / "task.md").read_text().startswith("---\n"))
        self.assertEqual(out["previous"], [])

    def test_каталог_прогонов_по_ссылке_наружу_отклоняется(self) -> None:
        outside = self.tmp / "outside"
        outside.mkdir()
        (self.repo / ".cross-review").mkdir()
        (self.repo / RUNS).symlink_to(outside)
        res = run("review_run", "new", self.repo, "--task", "demo")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("a symlink in the repository's .cross-review/", last_json_line(res.stdout)["error"])
        self.assertEqual(list(outside.iterdir()), [])

    def test_каталог_прогонов_по_ссылке_внутрь_репозитория_отклоняется(self) -> None:
        (self.repo / "src").mkdir()
        (self.repo / ".cross-review").mkdir()
        (self.repo / RUNS).symlink_to(Path("..") / "src")
        res = run("review_run", "new", self.repo, "--task", "demo")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("a symlink in the repository's .cross-review/", last_json_line(res.stdout)["error"])
        self.assertEqual(list((self.repo / "src").iterdir()), [])

    def test_task_md_по_ссылке_наружу_отклоняется(self) -> None:
        target = self.tmp / "planted.md"
        (self.repo / RUNS / "demo").mkdir(parents=True)
        (self.repo / RUNS / "demo" / "task.md").symlink_to(target)
        res = run("review_run", "new", self.repo, "--task", "demo")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("a symlink in the repository's .cross-review/", last_json_line(res.stdout)["error"])
        self.assertFalse(target.exists())

    def test_прошлый_прогон_по_ссылке_наружу_отклоняется(self) -> None:
        # прошлый прогон другого проекта: промт велит агентам прочитать его report.md и findings.json
        outside = copy_fixture("run-basic", self.tmp / "outside-run")
        write_json(outside / "manifest.json", {"task": "other"})
        (self.repo / RUNS / "demo").mkdir(parents=True)
        (self.repo / RUNS / "demo" / "01-old").symlink_to(outside)
        res = run("review_run", "new", self.repo, "--task", "demo")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn(f"{self.repo / RUNS / 'demo' / '01-old'}: a symlink in the repository's .cross-review/",
                      last_json_line(res.stdout)["error"])
        self.assertEqual([p.name for p in (self.repo / RUNS / "demo").iterdir()], ["01-old"])

    def test_файл_прошлого_прогона_по_ссылке_отклоняется(self) -> None:
        secret = self.tmp / "secret.md"
        secret.write_text("секрет")
        prior = self.prior_run("01-initial")
        (prior / "report.md").symlink_to(secret)
        res = run("review_run", "new", self.repo, "--task", "demo")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn(f"{prior / 'report.md'}: a symlink in the repository's .cross-review/", last_json_line(res.stdout)["error"])
        self.assertFalse((self.repo / RUNS / "demo" / "02-after-fix").exists())

    def test_task_md_не_перезаписывается(self) -> None:
        self.new()
        task_file = self.repo / RUNS / "demo" / "task.md"
        task_file.write_text("своё описание\n")
        self.new()
        self.assertEqual(task_file.read_text(), "своё описание\n")

    def test_прогоны_скрыты_вложенным_gitignore(self) -> None:
        first = self.new()
        ignore = self.repo / RUNS / ".gitignore"
        self.assertEqual(first["ignore"], {"path": str(ignore), "added": True, "ignored": True})
        self.assertEqual(ignore.read_text(), "*\n")
        self.assertEqual(self.untracked(), [])
        second = self.new()
        self.assertEqual(second["ignore"], {"path": str(ignore), "added": False, "ignored": True})
        exclude = self.repo / ".git" / "info" / "exclude"
        self.assertFalse(exclude.exists() and ".cross-review" in exclude.read_text())

    def test_белый_список_в_gitignore_не_возвращает_прогоны(self) -> None:
        # как в scratch: всё игнорируется, а файлы с точкой и каталоги возвращаются; info/exclude тут бессилен
        (self.repo / ".gitignore").write_text("*\n!*.*\n!*/\n")
        out = self.new()
        self.assertTrue(out["ignore"]["ignored"])
        self.assertEqual(self.untracked(), [])
        git(self.repo, "check-ignore", "-q", ".cross-review/runs/demo/01-initial/input/scope.md")

    def test_чужое_содержимое_gitignore_прогонов_переписывается(self) -> None:
        (self.repo / RUNS).mkdir(parents=True)
        (self.repo / RUNS / ".gitignore").write_text("*.tmp\n")
        self.assertTrue(self.new()["ignore"]["added"])
        self.assertEqual((self.repo / RUNS / ".gitignore").read_text(), "*\n")
        self.assertEqual(self.untracked(), [])

    def test_gitignore_прогонов_по_ссылке_отклоняется(self) -> None:
        target = self.tmp / "planted"
        (self.repo / RUNS).mkdir(parents=True)
        (self.repo / RUNS / ".gitignore").symlink_to(target)
        res = run("review_run", "new", self.repo, "--task", "demo")
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        self.assertIn("a symlink in the repository's .cross-review/", last_json_line(res.stdout)["error"])
        self.assertFalse(target.exists())

    def test_прогоны_скрыты_в_worktree(self) -> None:
        main = git_init(self.tmp / "main", commit=True)
        worktree = self.tmp / "wt"
        git(main, "worktree", "add", "-q", str(worktree))
        out = self.new(repo=worktree)
        self.assertTrue(out["ignore"]["ignored"])
        self.assertEqual(Path(out["ignore"]["path"]), worktree / RUNS / ".gitignore")
        self.assertEqual(self.untracked(worktree), [])

    def test_прогоны_скрыты_в_подкаталоге(self) -> None:
        package = self.repo / "packages" / "lib"
        package.mkdir(parents=True)
        out = self.new(repo=package)
        self.assertTrue(out["ignore"]["ignored"])
        git(package, "check-ignore", "-q", ".cross-review/runs/demo/01-initial/input/scope.md")
        self.assertFalse(self.new(repo=package)["ignore"]["added"])

    def test_относительный_путь_репозитория(self) -> None:
        res = run("review_run", "new", ".", "--task", "demo", cwd=self.repo)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertTrue(last_json_line(res.stdout)["ignore"]["ignored"])

    def test_без_git_gitignore_не_пишется(self) -> None:
        plain = self.tmp / "plain"
        plain.mkdir()
        out = self.new(repo=plain)
        self.assertIsNone(out["ignore"])
        self.assertTrue((plain / RUNS / "demo" / "01-initial" / "input").is_dir())
        self.assertFalse((plain / RUNS / ".gitignore").exists())

    def test_нумерация_прогонов(self) -> None:
        self.assertEqual(self.new()["run"], "01-initial")
        write_json(self.repo / RUNS / "demo" / "01-initial" / "manifest.json", {"task": "demo"})
        self.assertEqual(self.new()["run"], "02-after-fix")
        self.assertEqual(self.new()["run"], "03-after-fix")

    def test_незавершённый_прогон_не_делает_следующий_after_fix(self) -> None:
        self.new()
        self.assertEqual(self.new()["run"], "02-initial")

    def test_явное_имя_прогона_и_занятое_имя(self) -> None:
        self.assertEqual(self.new("--run", "retry")["run"], "retry")
        res = run("review_run", "new", self.repo, "--task", "demo", "--run", "retry")
        self.assertEqual(res.returncode, 1)
        self.assertIn("already exists", last_json_line(res.stdout)["error"])

    def test_прошлые_прогоны_в_выводе(self) -> None:
        self.prior_run("01-initial")
        out = self.new()
        self.assertEqual(out["run"], "02-after-fix")
        [prior] = out["previous"]
        self.assertEqual(prior["run"], "01-initial")
        self.assertEqual(prior["findings"], 4)
        self.assertEqual(prior["by_severity"], {"critical": 1, "major": 2, "minor": 1})
        self.assertEqual(prior["sources"], "4/4")

    def test_неверное_имя_выход_2(self) -> None:
        for flag, value in (("--task", "../x"), ("--task", ".hidden"), ("--task", "a/b"), ("--task", ""),
                            ("--run", "task.md"), ("--run", "..")):
            with self.subTest(flag=flag, value=value):
                args = ["new", self.repo, "--task", "demo"] if flag == "--run" else ["new", self.repo]
                res = run("review_run", *args, flag, value)
                self.assertEqual(res.returncode, 2)
                self.assertIn("usage:", res.stderr)

    def test_нет_каталога_репозитория_выход_1(self) -> None:
        res = run("review_run", "new", self.tmp / "missing", "--task", "demo")
        self.assertEqual(res.returncode, 1)
        self.assertIn("no such directory", last_json_line(res.stdout)["error"])


class ComposeCase(RepoCase):
    """A new run of the demo task with a scope written."""

    def setUp(self) -> None:
        super().setUp()
        self.run_dir = Path(self.new()["run_dir"])
        self.input = self.run_dir / "input"
        (self.input / "scope.md").write_text("Ветка feature/x против master: `git diff master...feature/x`.\n")

    def compose(self, *args: object) -> dict:
        res = run("review_run", "compose", self.run_dir, *args)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return last_json_line(res.stdout)

    def compose_error(self, *args: object) -> str:
        res = run("review_run", "compose", self.run_dir, *args)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        return last_json_line(res.stdout)["error"]

    def prompt(self, agent: str) -> str:
        return (self.run_dir / "prompts" / "agents" / f"{agent}.md").read_text()

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
                text = self.prompt(agent)
                self.assertNotRegex(text.split("Schema:")[0], r"\{\{[A-Z_]+\}\}")
                self.assertIn(f"`{self.input / 'scope.md'}`", text)
                self.assertIn(f"`{self.repo}` — run every command from here", text)
                self.assertFalse(text.startswith("---"))

    def test_пустой_вход_даёт_none_provided(self) -> None:
        (self.input / "goal.md").write_text("  \n")
        self.compose()
        text = self.prompt("bugs-claude")
        for line in ("what the change is trying to achieve", "the project's own conventions",
                     "a directory of supporting material"):
            with self.subTest(line=line):
                self.assertRegex(text, r"- `none provided` — " + re.escape(line))

    def test_заполненный_вход_даёт_пути(self) -> None:
        (self.input / "goal.md").write_text("Цель.\n")
        (self.input / "profile.md").write_text("См. AGENTS.md.\n")
        (self.input / "context" / "ticket.md").write_text("Тикет.\n")
        self.compose()
        text = self.prompt("arch-codex")
        for name in ("goal.md", "profile.md", "context"):
            with self.subTest(name=name):
                self.assertIn(f"`{self.input / name}`", text)
        self.assertNotIn("- `none provided` —", text)

    def test_линзы_ростера_на_месте(self) -> None:
        self.compose()
        for agent, lenses, absent in (("bugs-claude", ("bugs", "impl"), "architecture"),
                                      ("arch-codex", ("architecture", "quality"), "bugs")):
            with self.subTest(agent=agent):
                text = self.prompt(agent)
                positions = [text.index(f"## Lens: {lens}\n") for lens in lenses]
                self.assertEqual(positions, sorted(positions))
                self.assertNotIn(f"## Lens: {absent}\n", text)
                self.assertNotIn("\ndescription:", text)

    def test_схема_finder_в_конце(self) -> None:
        self.compose()
        text = self.prompt("bugs-codex")
        head, schema = text.split("Return ONLY a JSON object matching the schema below. No prose before or after it."
                                  "\n\nSchema:\n")
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
        self.assertIn(f"Prior rounds for this task: {self.repo / RUNS / 'demo'}/\n", text)
        self.assertRegex(text, r"\n  00-old  2026-10-20T07:00Z  4 findings \(1 critical, 2 major, 1 minor\)  sources 4/4\n")
        self.assertNotIn("01-initial  ", text)
        self.assertIn("Re-evaluate everything independently.", text)
        self.assertLess(text.index("Re-evaluate"), text.index("Return ONLY"))

    def test_ссылка_в_прошлом_прогоне_останавливает_compose(self) -> None:
        prior = self.prior_run("00-old")
        (prior / "report.md").symlink_to(self.tmp / "secret.md")
        self.assertIn(f"{prior / 'report.md'}: a symlink in the repository's .cross-review/", self.compose_error())
        self.assertFalse((self.run_dir / "manifest.json").exists())
        self.assertFalse((self.run_dir / "prompts").exists())

    def test_прогон_без_findings_перечислен_без_счёта(self) -> None:
        write_json(self.repo / RUNS / "demo" / "00-broken" / "manifest.json", {"task": "demo"})
        self.compose()
        self.assertRegex(self.prompt("arch-claude"), r"\n  00-broken  unknown  counts unknown  sources unknown\n")

    def test_промт_из_cross_review_перекрывает_плагин(self) -> None:
        self.layer("prompts/review.md", "---\ndescription: свой промт\n---\nСвой обзор. Scope: {{SCOPE}}. Workdir: {{WORKDIR}}.\n")
        self.compose()
        text = self.prompt("bugs-claude")
        self.assertTrue(text.startswith(f"Свой обзор. Scope: {self.input / 'scope.md'}. Workdir: {self.repo}."))
        review = next(p for p in read_json(self.run_dir / "manifest.json")["prompts"] if p["path"] == "prompts/review.md")
        self.assertEqual(review["layer"], "repo")

    def test_новая_линза_из_репозитория(self) -> None:
        self.layer("lenses/perf.md", "## Lens: perf\n\nИщи лишние аллокации.\n")
        self.layer("cross-review.json", json.dumps({"roster": [{"name": "perf", "lenses": ["perf", "bugs"]}]}))
        out = self.compose()
        self.assertEqual([e["agent"] for e in out["launch"]], ["perf-claude", "perf-codex"])
        self.assertIn("## Lens: perf\n\nИщи лишние аллокации.", self.prompt("perf-codex"))
        self.assertEqual(sorted(p.name for p in (self.run_dir / "prompts" / "agents").iterdir()),
                         ["perf-claude.md", "perf-codex.md"])

    def test_неизвестный_плейсхолдер_выход_1(self) -> None:
        self.layer("prompts/review.md", "Обзор {{SCOPE}} и {{TICKET}}.\n")
        error = self.compose_error()
        self.assertIn("prompts/review.md", error)
        self.assertIn("{{TICKET}}", error)

    def test_манифест(self) -> None:
        self.compose()
        manifest = read_json(self.run_dir / "manifest.json")
        self.assertEqual((manifest["task"], manifest["run"], manifest["tier"]), ("demo", "01-initial", "standard"))
        self.assertEqual(manifest["scope_path"], str(self.input / "scope.md"))
        self.assertEqual(manifest["workdir"], str(self.repo))
        self.assertIsNotNone(review_run.parse_time(manifest["started_at"]))
        self.assertEqual(manifest["models"], {"claude": {"model": "opus", "effort": "high"},
                                              "codex": {"model": "gpt-6.1-sol", "effort": "high"}})
        self.assertEqual([(a["name"], a["executor"], a["requested_model"], a["effort"], a["lenses"]) for a in manifest["agents"]], [
            ("bugs-claude", "claude", "opus", "high", ["bugs", "impl"]),
            ("bugs-codex", "codex", "gpt-6.1-sol", "high", ["bugs", "impl"]),
            ("arch-claude", "claude", "opus", "high", ["architecture", "quality"]),
            ("arch-codex", "codex", "gpt-6.1-sol", "high", ["architecture", "quality"]),
        ])
        self.assertEqual([p["path"] for p in manifest["prompts"]], [
            "prompts/review.md", "lenses/architecture.md", "lenses/bugs.md", "lenses/impl.md", "lenses/quality.md",
            "schemas/finder.json"])
        for entry in manifest["prompts"] + manifest["agents"]:
            with self.subTest(entry=entry.get("path") or entry["name"]):
                self.assertRegex(entry.get("hash") or entry["prompt_hash"], r"^[0-9a-f]{64}$")

    def test_план_запуска(self) -> None:
        out = self.compose()
        self.assertEqual((out["tier"], out["pair"]), ("standard", False))
        claude, codex = out["launch"][0], out["launch"][1]
        self.assertEqual({k: claude[k] for k in ("agent", "vendor", "alias", "effort")},
                         {"agent": "bugs-claude", "vendor": "claude", "alias": "opus", "effort": "high"})
        self.assertNotIn("model", claude)
        self.assertEqual(claude["output"], str(self.run_dir / "agents" / "bugs-claude.json"))
        self.assertEqual((codex["model"], codex["schema"]), ("gpt-6.1-sol", str(SCHEMA)))
        self.assertEqual(codex["command"].split(), [
            "bash", str(SCRIPTS / "codex_run.sh"), str(self.repo), codex["prompt"], str(SCHEMA), codex["output"],
            "gpt-6.1-sol", "high"])

    def test_разовая_пара_моделей(self) -> None:
        out = self.compose("--claude", "sonnet:medium", "--codex", "gpt-6-sol:low")
        self.assertTrue(out["pair"])
        self.assertEqual((out["launch"][0]["alias"], out["launch"][0]["effort"]), ("sonnet", "medium"))
        self.assertEqual((out["launch"][1]["model"], out["launch"][1]["effort"]), ("gpt-6-sol", "low"))
        self.assertEqual(read_json(self.run_dir / "manifest.json")["stages"]["model"], "sonnet")

    def test_уровень_по_имени(self) -> None:
        out = self.compose("--tier", "expert")
        self.assertEqual(out["tier"], "expert")
        self.assertEqual((out["launch"][0]["alias"], out["launch"][0]["effort"]), ("fable", "xhigh"))

    def test_повторный_compose_до_ответов_перезаписывает(self) -> None:
        self.compose()
        (self.input / "goal.md").write_text("Цель уточнена.\n")
        self.compose()
        self.assertIn(f"`{self.input / 'goal.md'}`", self.prompt("bugs-claude"))


class ComposeErrorsTest(ComposeCase):
    """What `compose` refuses."""

    def test_без_scope_выход_1(self) -> None:
        (self.input / "scope.md").unlink()
        self.assertIn("scope.md is missing or empty", self.compose_error())

    def test_после_ответов_выход_1(self) -> None:
        self.compose()
        (self.run_dir / "agents" / "bugs-claude.json").write_text('{"findings": []}')
        self.assertIn("already has answers (bugs-claude.json)", self.compose_error())

    def test_не_каталог_прогона_выход_1(self) -> None:
        other = self.tmp / "elsewhere"
        (other / "input").mkdir(parents=True)
        res = run("review_run", "compose", other)
        self.assertEqual(res.returncode, 1)
        self.assertIn("not a run directory", last_json_line(res.stdout)["error"])

    def test_неизвестный_уровень_выход_1(self) -> None:
        self.assertIn("no tier 'ultra'", self.compose_error("--tier", "ultra"))

    def test_неверные_аргументы_выход_2(self) -> None:
        for args in (["--claude", "opus"], ["--codex", "gpt-6-sol"], ["--tier", "standard", "--claude", "opus", "--codex", "x"]):
            with self.subTest(args=args):
                res = run("review_run", "compose", self.run_dir, *args)
                self.assertEqual(res.returncode, 2)
                self.assertIn("usage:", res.stderr)


class HistoryTest(unittest.TestCase):
    """The prior-run inventory and the timestamps it reads."""

    def test_разбор_времени(self) -> None:
        cases = [
            ("2026-10-19T14:25:43.154729+03:00", "2026-10-19T11:25Z"),
            ("2026-10-19T14:25:43.154729123+03:00", "2026-10-19T11:25Z"),
            ("2026-10-19T11:04:00Z", "2026-10-19T11:04Z"),
        ]
        for value, want in cases:
            with self.subTest(value=value):
                self.assertEqual(review_run.parse_time(value).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%MZ"), want)
        for value in ("", None, "вчера"):
            with self.subTest(value=value):
                self.assertIsNone(review_run.parse_time(value))

    def test_фикстура_читается_как_прошлый_прогон(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "demo"
            copy_fixture("run-basic", task_dir / "01-initial")
            self.assertEqual(review_run.prior_runs(task_dir), [])
            write_json(task_dir / "01-initial" / "manifest.json", {"task": "demo"})
            [prior] = review_run.prior_runs(task_dir)
            self.assertEqual(prior["started_at"], read_json(FIXTURES / "run-basic" / "findings.json")["stats"]["started_at"])
            self.assertEqual(review_run.prior_runs(task_dir, current="01-initial"), [])

    def test_ссылка_среди_прогонов_или_внутри_прогона_ошибка(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            task_dir = Path(tmp) / "demo"
            copy_fixture("run-basic", task_dir / "01-initial")
            write_json(task_dir / "01-initial" / "manifest.json", {"task": "demo"})
            # висячая ссылка — не каталог и не файл, но тоже ссылка
            (task_dir / "02-old").symlink_to(Path(tmp) / "missing")
            with self.assertRaisesRegex(ConfigError, "02-old: a symlink in the repository's .cross-review/"):
                review_run.prior_runs(task_dir)
            (task_dir / "02-old").unlink()
            (task_dir / "01-initial" / "stages" / "1-found.json").unlink()
            (task_dir / "01-initial" / "stages" / "1-found.json").symlink_to(Path(tmp) / "planted.json")
            with self.assertRaisesRegex(ConfigError, "1-found.json: a symlink in the repository's .cross-review/"):
                review_run.prior_runs(task_dir)
            # текущий прогон не проверяется: его пишет сама сессия
            self.assertEqual(review_run.prior_runs(task_dir, current="01-initial"), [])


if __name__ == "__main__":
    unittest.main()
