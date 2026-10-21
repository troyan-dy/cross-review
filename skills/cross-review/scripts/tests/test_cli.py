from __future__ import annotations

import unittest

from support import SCRIPTS, run

PY_SCRIPTS = sorted(p.stem for p in SCRIPTS.glob("*.py"))


class CliContractTest(unittest.TestCase):
    """Every script: -h/--help prints usage and exits 0, wrong arguments print usage to stderr and exit 2."""

    def test_скрипты_найдены(self) -> None:
        self.assertIn("debate_build", PY_SCRIPTS)

    def test_справка_выход_0(self) -> None:
        for script in PY_SCRIPTS:
            for flag in ("-h", "--help"):
                with self.subTest(script=script, flag=flag):
                    res = run(script, flag)
                    self.assertEqual(res.returncode, 0, res.stderr)
                    self.assertIn("usage:", res.stdout)
                    self.assertEqual(res.stderr, "")

    def test_без_аргументов_выход_2(self) -> None:
        for script in PY_SCRIPTS:
            with self.subTest(script=script):
                res = run(script)
                self.assertEqual(res.returncode, 2)
                self.assertIn("usage:", res.stderr)
                self.assertEqual(res.stdout, "")

    def test_неизвестный_флаг_выход_2(self) -> None:
        for script in PY_SCRIPTS:
            with self.subTest(script=script):
                res = run(script, "a", "b", "--no-such-flag")
                self.assertEqual(res.returncode, 2)
                self.assertIn("usage:", res.stderr)

    def test_будущие_аннотации_во_всех_скриптах(self) -> None:
        for script in PY_SCRIPTS:
            with self.subTest(script=script):
                self.assertIn("from __future__ import annotations", (SCRIPTS / f"{script}.py").read_text())


if __name__ == "__main__":
    unittest.main()
