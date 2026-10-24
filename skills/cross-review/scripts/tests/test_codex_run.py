from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import SCRIPTS, SKILL, last_json_line, pick, read_json

SCRIPT = SCRIPTS / "codex_run.sh"
SCHEMA = SKILL / "assets" / "config" / "schemas" / "finder.json"
SESSION = "00000000-0000-7000-8000-000000000001"
MODEL = "gpt-6.1-sol"

# a stand-in for the codex CLI: records its arguments and stdin, prints a log header, writes the answer to -o
STUB = r"""#!{python}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["STUB_ARGS"], "w") as f:
    json.dump(args, f)
with open(os.environ["STUB_STDIN"], "w") as f:
    f.write(sys.stdin.read())
print("OpenAI Codex v0 (stub)\n--------\nmodel: " + os.environ.get("STUB_MODEL", "stub-model"))
print("session id: {session}\n--------")
print(os.environ.get("STUB_TAIL", ""))
if "STUB_ANSWER" in os.environ:
    with open(args[args.index("-o") + 1], "w") as f:
        f.write(os.environ["STUB_ANSWER"])
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
"""


class CodexRunCase(unittest.TestCase):
    """A workdir, a prompt and a stub `codex` first on PATH."""

    def setUp(self) -> None:
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name).resolve()
        self.workdir = self.root / "repo"
        self.workdir.mkdir()
        self.prompt = self.root / "prompt.md"
        self.prompt.write_text("Проверь изменение.\n")
        self.out = self.agent_file("json")
        self.args_file = self.root / "args.json"
        self.env = self.stub_env(self.install_stub())

    def install_stub(self) -> Path:
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "codex"
        stub.write_text(STUB.format(python=sys.executable, session=SESSION))
        stub.chmod(0o755)
        return bin_dir

    def stub_env(self, bin_dir: Path) -> dict[str, str]:
        env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "STUB_ARGS": str(self.args_file),
            "STUB_STDIN": str(self.root / "stdin.txt"),
            "STUB_ANSWER": '{"findings": []}',
        }
        env.pop("CROSS_REVIEW_CODEX_USER_CONFIG", None)
        return env

    def agent_file(self, suffix: str) -> Path:
        return self.root / "agents" / f"bugs-codex.{suffix}"

    def codex_run(self, *args: object, **env: str) -> subprocess.CompletedProcess:
        argv = ["bash", str(SCRIPT), *map(str, args)]
        merged = {**self.env, **env}
        return subprocess.run(argv, capture_output=True, text=True, env=merged, cwd=self.root)

    def standard_args(self) -> list[object]:
        return [self.workdir, self.prompt, SCHEMA, self.out, MODEL, "high"]

    def standard(self, *extra: object, **env: str) -> subprocess.CompletedProcess:
        return self.codex_run(*self.standard_args(), *extra, **env)

    def without_answer(self, **env: str) -> subprocess.CompletedProcess:
        """A standard run whose stub writes no answer, outside the workspace."""
        stub_env = {**self.env, **env}
        stub_env.pop("STUB_ANSWER")
        argv = ["bash", str(SCRIPT), *map(str, self.standard_args())]
        return subprocess.run(argv, capture_output=True, text=True, env=stub_env)

    def codex_args(self) -> list[str]:
        return read_json(self.args_file)


class ArgumentsTest(CodexRunCase):
    """Usage on -h, exit 2 on anything the script cannot run with; codex is not called."""

    def test_справка_выход0(self) -> None:
        for flag in ("-h", "--help"):
            with self.subTest(flag=flag):
                res = self.codex_run(flag)
                self.assertEqual(res.returncode, 0)
                self.assertIn("usage: codex_run.sh", res.stdout)

    def test_неверные_аргументы_выход2(self) -> None:
        base = [self.workdir, self.prompt, SCHEMA, self.out, MODEL]
        tail = (self.out, MODEL, "high")
        cases = (
            ("без аргументов", []),
            ("не хватает effort", base),
            ("лишний аргумент", [*base, "high", "extra"]),
            ("не тот флаг", [*base, "high", "--fork", self.prompt]),
            ("resume без лога", [*base, "high", "--resume"]),
            ("неверный effort", [*base, "ultra"]),
            ("нет промта", [self.workdir, self.root / "missing.md", SCHEMA, *tail]),
            ("нет схемы", [self.workdir, self.prompt, self.root / "missing.json", *tail]),
            ("нет рабочего каталога", [self.root / "missing", self.prompt, SCHEMA, *tail]),
        )
        for name, args in cases:
            with self.subTest(case=name):
                self.assert_usage_error(args)

    def assert_usage_error(self, args: list[object]) -> None:
        res = self.codex_run(*args)
        self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
        self.assertEqual(res.stdout, "")
        self.assertFalse(self.args_file.exists())


class FreshRunTest(CodexRunCase):
    """A fresh run: read-only exec in the workdir, the strict schema, the answer, the log and the exit code."""

    def test_флаги_exec(self) -> None:
        res = self.standard()
        self.assertEqual(res.returncode, 0, res.stderr)
        strict = self.agent_file("schema.json")
        self.assertEqual(self.codex_args(), [
            "exec", "--ignore-user-config", "-C", str(self.workdir), "-s", "read-only", "--skip-git-repo-check",
            "-m", MODEL, "-c", 'model_reasoning_effort="high"', "--output-schema", str(strict),
            "-o", str(self.out), "-"])
        self.assertEqual((self.root / "stdin.txt").read_text(), "Проверь изменение.\n")

    def test_файлы_и_строка_результата(self) -> None:
        res = self.standard(STUB_MODEL="gpt-6.1-sol-2026")
        out = last_json_line(res.stdout)
        expected = (0, "gpt-6.1-sol-2026", SESSION, False, True)
        self.assertEqual(pick(out, "exit", "model", "session_id", "resumed", "answer"), expected)
        self.assertNotIn("warning", out)
        self.assertEqual(self.out.read_text(), '{"findings": []}')
        self.assertEqual(self.agent_file("exit").read_text().strip(), "0")
        self.assertIn(f"session id: {SESSION}", self.agent_file("log").read_text())
        self.assertEqual(out["log"], str(self.agent_file("log")))

    def test_строгая_копия_схемы(self) -> None:
        original = SCHEMA.read_text()
        self.standard()
        strict = read_json(self.agent_file("schema.json"))
        self.assertNotIn("$schema", strict)
        self.assertEqual(strict["required"], ["findings"])
        finding = strict["properties"]["findings"]["items"]
        self.assertEqual(finding["required"], list(finding["properties"]))
        self.assertIn("end_line", finding["required"])
        self.assertIn("fix", finding["required"])
        self.assertFalse(finding["additionalProperties"])
        self.assertEqual(SCHEMA.read_text(), original)

    def test_относительные_пути(self) -> None:
        res = self.codex_run("repo", "prompt.md", SCHEMA, "agents/rel.json", MODEL, "low")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn(str(self.workdir), self.codex_args())
        self.assertTrue((self.root / "agents" / "rel.json").is_file())
        self.assertIn('model_reasoning_effort="low"', self.codex_args())

    def test_пользовательский_конфиг_по_переменной(self) -> None:
        res = self.standard(CROSS_REVIEW_CODEX_USER_CONFIG="1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("--ignore-user-config", self.codex_args())
        head = self.codex_args()[:3]
        self.assertEqual(head, ["exec", "-C", str(self.workdir)])

    def test_старый_ответ_удаляется_перед_запуском(self) -> None:
        self.out.parent.mkdir(parents=True)
        self.out.write_text('{"findings": ["старое"]}')
        res = self.without_answer()
        self.assertFalse(self.out.exists())
        self.assertFalse(last_json_line(res.stdout)["answer"])

    def test_ненулевой_выход_и_лимит(self) -> None:
        tail = "ERROR: You've hit your usage limit. Try again in 3 hours."
        res = self.without_answer(STUB_EXIT="1", STUB_TAIL=tail)
        self.assertEqual(res.returncode, 1)
        out = last_json_line(res.stdout)
        self.assertEqual(pick(out, "exit", "answer"), (1, False))
        self.assertIn("usage limit", out["warning"])
        self.assertEqual(self.agent_file("exit").read_text().strip(), "1")

    def test_слово_лимит_в_удачном_запуске_не_предупреждает(self) -> None:
        res = self.standard(STUB_TAIL="rate limit exceeded: 429 too many requests")
        self.assertNotIn("warning", last_json_line(res.stdout))


class ResumeTest(CodexRunCase):
    """--resume continues the session the log names, read-only through the config."""

    def test_флаги_resume(self) -> None:
        log = self.root / "codex-r1.log"
        log.write_text(f"OpenAI Codex\n--------\nmodel: {MODEL}\nsession id: {SESSION}\n--------\n")
        res = self.standard("--resume", log)
        self.assertEqual(res.returncode, 0, res.stderr)
        args = self.codex_args()
        resume = ["exec", "--ignore-user-config", "resume", SESSION, "-c", 'sandbox_mode="read-only"']
        self.assertEqual(args[:6], resume)
        self.assertNotIn("-s", args)
        self.assertNotIn("-C", args)
        self.assertIn("--output-schema", args)
        tail = args[-3:]
        self.assertEqual(tail, ["-o", str(self.out), "-"])
        self.assertTrue(last_json_line(res.stdout)["resumed"])

    def test_resume_без_session_id_выход3(self) -> None:
        log = self.root / "codex-r1.log"
        log.write_text("ERROR: something broke before the session started\n")
        self.assert_no_session(self.standard("--resume", log))

    def test_resume_без_лога_выход3(self) -> None:
        # сторона пропустила раунд 1: лога нет, команда r2 должна уйти в fresh_command по выходу 3
        self.assert_no_session(self.standard("--resume", self.root / "codex-r1.log"))

    def assert_no_session(self, res: subprocess.CompletedProcess) -> None:
        self.assertEqual(res.returncode, 3)
        out = last_json_line(res.stdout)
        self.assertEqual(pick(out, "exit", "resumed"), (3, False))
        self.assertIn("no session id", out["error"])
        self.assertFalse(self.args_file.exists())


class ScriptTest(unittest.TestCase):
    """The bash script itself: syntax and Russian comments, as DEVELOPMENT.md asks."""

    def test_синтаксис_bash(self) -> None:
        argv = ["bash", "-n", str(SCRIPT)]
        res = subprocess.run(argv, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_комментарии_по_русски(self) -> None:
        comments = [line.strip() for line in SCRIPT.read_text().splitlines()
                    if line.strip().startswith("# ") and not line.startswith("#!")]
        self.assertTrue(comments)
        for line in comments:
            with self.subTest(line=line):
                self.assertRegex(line, r"[а-яё]")


if __name__ == "__main__":
    unittest.main()
