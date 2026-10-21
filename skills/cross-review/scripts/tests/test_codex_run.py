from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from support import SCRIPTS, SKILL, last_json_line, read_json

SCRIPT = SCRIPTS / "codex_run.sh"
SCHEMA = SKILL / "assets" / "config" / "schemas" / "finder.json"
SESSION = "00000000-0000-7000-8000-000000000001"

# a stand-in for the codex CLI: records its arguments and stdin, prints a log header, writes the answer to -o
STUB = """#!{python}
import json, os, sys
args = sys.argv[1:]
with open(os.environ["STUB_ARGS"], "w") as f:
    json.dump(args, f)
with open(os.environ["STUB_STDIN"], "w") as f:
    f.write(sys.stdin.read())
print("OpenAI Codex v0 (stub)\\n--------\\nmodel: " + os.environ.get("STUB_MODEL", "stub-model"))
print("session id: {session}\\n--------")
print(os.environ.get("STUB_TAIL", ""))
if "STUB_ANSWER" in os.environ:
    with open(args[args.index("-o") + 1], "w") as f:
        f.write(os.environ["STUB_ANSWER"])
sys.exit(int(os.environ.get("STUB_EXIT", "0")))
"""


class CodexRunCase(unittest.TestCase):
    """A workdir, a prompt and a stub `codex` first on PATH."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name).resolve()
        self.workdir = self.tmp / "repo"
        self.workdir.mkdir()
        self.prompt = self.tmp / "prompt.md"
        self.prompt.write_text("Проверь изменение.\n")
        self.out = self.tmp / "agents" / "bugs-codex.json"
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "codex"
        stub.write_text(STUB.format(python=sys.executable, session=SESSION))
        stub.chmod(0o755)
        self.args_file, self.stdin_file = self.tmp / "args.json", self.tmp / "stdin.txt"
        self.env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                    "STUB_ARGS": str(self.args_file), "STUB_STDIN": str(self.stdin_file),
                    "STUB_ANSWER": '{"findings": []}'}
        self.env.pop("CROSS_REVIEW_CODEX_USER_CONFIG", None)

    def codex_run(self, *args: object, **env: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(SCRIPT), *map(str, args)], capture_output=True, text=True,
                              env={**self.env, **env}, cwd=self.tmp)

    def standard(self, *extra: object, **env: str) -> subprocess.CompletedProcess:
        return self.codex_run(self.workdir, self.prompt, SCHEMA, self.out, "gpt-6.1-sol", "high", *extra, **env)

    def codex_args(self) -> list[str]:
        return read_json(self.args_file)


class ArgumentsTest(CodexRunCase):
    """Usage on -h, exit 2 on anything the script cannot run with; codex is not called."""

    def test_справка_выход_0(self) -> None:
        for flag in ("-h", "--help"):
            with self.subTest(flag=flag):
                res = self.codex_run(flag)
                self.assertEqual(res.returncode, 0)
                self.assertIn("usage: codex_run.sh", res.stdout)

    def test_неверные_аргументы_выход_2(self) -> None:
        base = [self.workdir, self.prompt, SCHEMA, self.out, "gpt-6.1-sol"]
        cases = [
            ("без аргументов", []),
            ("не хватает effort", base),
            ("лишний аргумент", [*base, "high", "extra"]),
            ("не тот флаг", [*base, "high", "--fork", self.prompt]),
            ("resume без лога", [*base, "high", "--resume"]),
            ("неверный effort", [*base, "ultra"]),
            ("нет промта", [self.workdir, self.tmp / "missing.md", SCHEMA, self.out, "gpt-6.1-sol", "high"]),
            ("нет схемы", [self.workdir, self.prompt, self.tmp / "missing.json", self.out, "gpt-6.1-sol", "high"]),
            ("нет рабочего каталога", [self.tmp / "missing", self.prompt, SCHEMA, self.out, "gpt-6.1-sol", "high"]),
        ]
        for name, args in cases:
            with self.subTest(case=name):
                res = self.codex_run(*args)
                self.assertEqual(res.returncode, 2, res.stdout + res.stderr)
                self.assertEqual(res.stdout, "")
                self.assertFalse(self.args_file.exists())


class FreshRunTest(CodexRunCase):
    """A fresh run: read-only exec in the workdir, the strict schema, the answer, the log and the exit code."""

    def test_флаги_exec(self) -> None:
        res = self.standard()
        self.assertEqual(res.returncode, 0, res.stderr)
        strict = self.tmp / "agents" / "bugs-codex.schema.json"
        self.assertEqual(self.codex_args(), [
            "exec", "--ignore-user-config", "-C", str(self.workdir), "-s", "read-only", "--skip-git-repo-check",
            "-m", "gpt-6.1-sol", "-c", 'model_reasoning_effort="high"', "--output-schema", str(strict),
            "-o", str(self.out), "-"])
        self.assertEqual(self.stdin_file.read_text(), "Проверь изменение.\n")

    def test_файлы_и_строка_результата(self) -> None:
        res = self.standard(STUB_MODEL="gpt-6.1-sol-2026")
        out = last_json_line(res.stdout)
        self.assertEqual({k: out[k] for k in ("exit", "model", "session_id", "resumed", "answer")},
                         {"exit": 0, "model": "gpt-6.1-sol-2026", "session_id": SESSION, "resumed": False, "answer": True})
        self.assertNotIn("warning", out)
        self.assertEqual(self.out.read_text(), '{"findings": []}')
        self.assertEqual((self.tmp / "agents" / "bugs-codex.exit").read_text().strip(), "0")
        self.assertIn(f"session id: {SESSION}", (self.tmp / "agents" / "bugs-codex.log").read_text())
        self.assertEqual(out["log"], str(self.tmp / "agents" / "bugs-codex.log"))

    def test_строгая_копия_схемы(self) -> None:
        original = SCHEMA.read_text()
        self.standard()
        strict = read_json(self.tmp / "agents" / "bugs-codex.schema.json")
        self.assertNotIn("$schema", strict)
        self.assertEqual(strict["required"], ["findings"])
        item = strict["properties"]["findings"]["items"]
        self.assertEqual(item["required"], list(item["properties"]))
        self.assertIn("end_line", item["required"])
        self.assertIn("fix", item["required"])
        self.assertFalse(item["additionalProperties"])
        self.assertEqual(SCHEMA.read_text(), original)

    def test_относительные_пути(self) -> None:
        res = self.codex_run("repo", "prompt.md", SCHEMA, "agents/rel.json", "gpt-6.1-sol", "low")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn(str(self.workdir), self.codex_args())
        self.assertTrue((self.tmp / "agents" / "rel.json").is_file())
        self.assertIn('model_reasoning_effort="low"', self.codex_args())

    def test_пользовательский_конфиг_по_переменной(self) -> None:
        res = self.standard(CROSS_REVIEW_CODEX_USER_CONFIG="1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertNotIn("--ignore-user-config", self.codex_args())
        self.assertEqual(self.codex_args()[:3], ["exec", "-C", str(self.workdir)])

    def test_старый_ответ_удаляется_перед_запуском(self) -> None:
        self.out.parent.mkdir(parents=True)
        self.out.write_text('{"findings": ["старое"]}')
        env = {k: v for k, v in self.env.items() if k != "STUB_ANSWER"}
        res = subprocess.run(["bash", str(SCRIPT), str(self.workdir), str(self.prompt), str(SCHEMA), str(self.out),
                              "gpt-6.1-sol", "high"], capture_output=True, text=True, env=env)
        self.assertFalse(self.out.exists())
        self.assertFalse(last_json_line(res.stdout)["answer"])

    def test_ненулевой_выход_и_лимит(self) -> None:
        env = {k: v for k, v in self.env.items() if k != "STUB_ANSWER"}
        env.update(STUB_EXIT="1", STUB_TAIL="ERROR: You've hit your usage limit. Try again in 3 hours.")
        res = subprocess.run(["bash", str(SCRIPT), str(self.workdir), str(self.prompt), str(SCHEMA), str(self.out),
                              "gpt-6.1-sol", "high"], capture_output=True, text=True, env=env)
        self.assertEqual(res.returncode, 1)
        out = last_json_line(res.stdout)
        self.assertEqual((out["exit"], out["answer"]), (1, False))
        self.assertIn("usage limit", out["warning"])
        self.assertEqual((self.tmp / "agents" / "bugs-codex.exit").read_text().strip(), "1")

    def test_слово_лимит_в_удачном_запуске_не_предупреждает(self) -> None:
        res = self.standard(STUB_TAIL="rate limit exceeded: 429 too many requests")
        self.assertNotIn("warning", last_json_line(res.stdout))


class ResumeTest(CodexRunCase):
    """--resume continues the session the log names, read-only through the config."""

    def test_флаги_resume(self) -> None:
        log = self.tmp / "codex-r1.log"
        log.write_text(f"OpenAI Codex\n--------\nmodel: gpt-6.1-sol\nsession id: {SESSION}\n--------\n")
        res = self.standard("--resume", log)
        self.assertEqual(res.returncode, 0, res.stderr)
        args = self.codex_args()
        self.assertEqual(args[:6], ["exec", "--ignore-user-config", "resume", SESSION, "-c", 'sandbox_mode="read-only"'])
        self.assertNotIn("-s", args)
        self.assertNotIn("-C", args)
        self.assertIn("--output-schema", args)
        self.assertEqual(args[-3:], ["-o", str(self.out), "-"])
        self.assertTrue(last_json_line(res.stdout)["resumed"])

    def test_resume_без_session_id_выход_3(self) -> None:
        log = self.tmp / "codex-r1.log"
        log.write_text("ERROR: something broke before the session started\n")
        res = self.standard("--resume", log)
        self.assertEqual(res.returncode, 3)
        out = last_json_line(res.stdout)
        self.assertEqual((out["exit"], out["resumed"]), (3, False))
        self.assertIn("no session id", out["error"])
        self.assertFalse(self.args_file.exists())

    def test_resume_без_лога_выход_3(self) -> None:
        # сторона пропустила раунд 1: лога нет, команда r2 должна уйти в fresh_command по выходу 3
        res = self.standard("--resume", self.tmp / "codex-r1.log")
        self.assertEqual(res.returncode, 3)
        out = last_json_line(res.stdout)
        self.assertEqual((out["exit"], out["resumed"]), (3, False))
        self.assertIn("no session id", out["error"])
        self.assertFalse(self.args_file.exists())


class ScriptTest(unittest.TestCase):
    """The bash script itself: syntax and Russian comments, as the marketplace conventions ask."""

    def test_синтаксис_bash(self) -> None:
        res = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
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
