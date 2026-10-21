from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from support import SKILL, composed_run, copy_fixture, last_json_line, read_json, run, write_json

import cross_config
from cross_config import ConfigError, load_config, merge, resolve

PLUGIN = SKILL / "assets" / "config"
DEFAULTS = read_json(PLUGIN / "cross-review.json")

# the placeholders each default prompt may use; the scripts substitute exactly these
PLACEHOLDERS = {
    "review": {"SCOPE", "GOAL", "PROFILE", "CONTEXT", "WORKDIR"},
    "synthesis": {"SOURCES", "FINDINGS"},
    "verify": {"SCOPE", "PROFILE", "WORKDIR", "FINDINGS"},
    "examine": {"SIDE", "OTHER", "SUBJECT", "WORKDIR", "PANEL", "CONTEXT", "TREE_NOTE", "INPUT", "STATUS_LEGEND", "OUTPUT"},
    "respond": {"SIDE", "OTHER", "INPUT", "OUTPUT"},
    "moderate": {"SUBJECT", "WORKDIR", "CONTEXT", "TREE_NOTE", "INPUT", "OUTPUT"},
}


class RepoCase(unittest.TestCase):
    """A temporary repository; .cross-review/ files are written per test."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name).resolve()

    def layer(self, rel: str, content: object) -> Path:
        path = self.repo / ".cross-review" / rel
        if isinstance(content, str):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        else:
            write_json(path, content)
        return path


class DefaultsTest(unittest.TestCase):
    """The plugin layer on its own is a complete, valid configuration."""

    def test_дефолты_проходят_валидацию(self) -> None:
        self.assertEqual(load_config(None), DEFAULTS)

    def test_файлы_дефолтов_на_месте(self) -> None:
        for kind, names in (("prompts", PLACEHOLDERS),
                            ("lenses", ("bugs", "impl", "architecture", "quality", "docs", "tests", "comments", "adversarial"))):
            for name in names:
                with self.subTest(kind=kind, name=name):
                    self.assertEqual(resolve(None, kind, name), PLUGIN / kind / f"{name}.md")
        for name in ("finder", "synthesis", "verify"):
            with self.subTest(schema=name):
                self.assertIsInstance(read_json(resolve(None, "schemas", name)), dict)
        self.assertIn("MIT License", (PLUGIN / "LICENSE").read_text())

    def test_плейсхолдеры_промтов_из_нашего_набора(self) -> None:
        for name, expected in PLACEHOLDERS.items():
            with self.subTest(prompt=name):
                text = (PLUGIN / "prompts" / f"{name}.md").read_text()
                self.assertEqual(set(re.findall(r"\{\{([A-Z_]+)\}\}", text)), expected)

    def test_промты_требуют_русский_в_ответе(self) -> None:
        for name in PLACEHOLDERS:
            with self.subTest(prompt=name):
                self.assertIn("in Russian", (PLUGIN / "prompts" / f"{name}.md").read_text())


class MergeTest(RepoCase):
    """The repository layer over the defaults: objects merge recursively, arrays are replaced whole."""

    def test_слияние_объектов_и_замена_массивов(self) -> None:
        cases = [
            ({"a": 1, "b": {"c": 1, "d": 2}}, {"b": {"d": 3}}, {"a": 1, "b": {"c": 1, "d": 3}}),
            ({"a": [1, 2]}, {"a": [3]}, {"a": [3]}),
            ({"a": {"b": 1}}, {"c": {"d": 2}}, {"a": {"b": 1}, "c": {"d": 2}}),
            ({"a": {"b": 1}}, {}, {"a": {"b": 1}}),
        ]
        for base, over, want in cases:
            with self.subTest(base=base, over=over):
                self.assertEqual(merge(base, over), want)

    def test_без_слоя_репозитория_только_дефолты(self) -> None:
        self.assertEqual(load_config(self.repo), DEFAULTS)

    def test_частичный_override_уровня(self) -> None:
        self.layer("cross-review.json", {"tiers": {"standard": {"codex": "gpt-6-sol:medium"}}})
        cfg = load_config(self.repo)
        self.assertEqual(cfg["tiers"]["standard"]["codex"], "gpt-6-sol:medium")
        self.assertEqual(cfg["tiers"]["standard"]["claude"], DEFAULTS["tiers"]["standard"]["claude"])
        self.assertEqual(cfg["tiers"]["standard"]["moderators"], DEFAULTS["tiers"]["standard"]["moderators"])
        self.assertEqual(cfg["tiers"]["expert"], DEFAULTS["tiers"]["expert"])
        self.assertEqual(cfg["roster"], DEFAULTS["roster"])

    def test_roster_заменяется_целиком(self) -> None:
        roster = [{"name": "docs", "lenses": ["docs", "comments"]}]
        self.layer("cross-review.json", {"roster": roster})
        self.assertEqual(load_config(self.repo)["roster"], roster)

    def test_новый_уровень_и_выбор_по_умолчанию(self) -> None:
        cheap = {"claude": "haiku:low", "codex": "gpt-6-mini", "moderators": {"claude": "sonnet", "codex": "gpt-6-sol"}}
        self.layer("cross-review.json", {"tier": "cheap", "tiers": {"cheap": cheap}})
        cfg = load_config(self.repo)
        self.assertEqual(cfg["tier"], "cheap")
        self.assertEqual(set(cfg["tiers"]), {"standard", "expert", "cheap"})


class ErrorsTest(RepoCase):
    """Every broken repository layer is an error naming the file and the key."""

    def test_ошибки_конфигурации(self) -> None:
        cases = [
            ("неизвестный ключ", {"model": "opus"}, "model: unknown key"),
            ("неизвестный вложенный ключ", {"tiers": {"standard": {"runner": "opus"}}}, "tiers.standard.runner: unknown key"),
            ("неизвестный ключ стадий", {"stages": {"verify_parallel": 2}}, "stages.verify_parallel: unknown key"),
            ("claude-модель не алиас", {"tiers": {"standard": {"claude": "claude-opus-5-5"}}}, "tiers.standard.claude: claude model"),
            ("неверный effort", {"tiers": {"standard": {"codex": "gpt-6-sol:ultra"}}}, "tiers.standard.codex: effort 'ultra'"),
            ("пустой effort", {"tiers": {"expert": {"claude": "opus:"}}}, "tiers.expert.claude: effort ''"),
            ("неверная модель модератора", {"tiers": {"standard": {"moderators": {"claude": "gpt-6"}}}},
             "tiers.standard.moderators.claude: claude model"),
            ("несуществующая линза", {"roster": [{"name": "bugs", "lenses": ["bugs", "perf"]}]}, "roster[0].lenses: no lens 'perf'"),
            ("неизвестный уровень", {"tier": "ultra"}, "tier: 'ultra'"),
            ("неполный новый уровень", {"tiers": {"cheap": {"claude": "haiku", "codex": "gpt-6-mini"}}},
             "tiers.cheap: missing moderators"),
            ("неполная запись ростера", {"roster": [{"name": "bugs"}]}, "roster[0]: missing lenses"),
            ("пустой ростер", {"roster": []}, "roster: empty"),
            ("пустые линзы", {"roster": [{"name": "bugs", "lenses": []}]}, "roster[0].lenses: empty"),
            ("повтор имени в ростере", {"roster": [{"name": "a", "lenses": ["bugs"]}, {"name": "a", "lenses": ["impl"]}]},
             "roster[1].name: 'a' is already"),
            ("неверное имя в ростере", {"roster": [{"name": "Bugs", "lenses": ["bugs"]}]}, "roster[0].name: 'Bugs'"),
            ("имя стадии в ростере", {"roster": [{"name": "verify", "lenses": ["bugs"]}]}, "roster[0].name: 'verify' is reserved"),
            ("строка вместо числа", {"stages": {"verify_groups": "6"}}, "stages.verify_groups: expected an integer"),
            ("bool вместо числа", {"stages": {"verify_groups": True}}, "stages.verify_groups: expected an integer"),
            ("строка вместо bool", {"stages": {"synthesis": "no"}}, "stages.synthesis: expected true or false"),
            ("ноль групп", {"stages": {"verify_groups": 0}}, "stages.verify_groups: 0"),
            ("неверная группировка", {"stages": {"verify_group_by": "file"}}, "stages.verify_group_by: 'file'"),
            ("массив вместо объекта", [], "expected an object"),
        ]
        for name, layer, message in cases:
            with self.subTest(name):
                path = self.layer("cross-review.json", layer)
                with self.assertRaises(ConfigError) as ctx:
                    load_config(self.repo)
                self.assertIn(str(path), str(ctx.exception))
                self.assertIn(message, str(ctx.exception))

    def test_битый_json(self) -> None:
        path = self.layer("cross-review.json", "{\"tier\": ")
        with self.assertRaisesRegex(ConfigError, "not valid JSON"):
            load_config(self.repo)
        self.assertTrue(path.exists())

    def test_промт_без_пары_в_плагине(self) -> None:
        self.layer("prompts/reveiw.md", "typo in the name")
        with self.assertRaisesRegex(ConfigError, "no plugin prompt 'reveiw'"):
            load_config(self.repo)

    def test_нет_каталога_репозитория(self) -> None:
        with self.assertRaisesRegex(ConfigError, "no such directory"):
            load_config(self.repo / "missing")

    def test_имя_с_путём_отклоняется(self) -> None:
        for name in ("../review", "Review", ""):
            with self.subTest(name=name), self.assertRaisesRegex(ConfigError, "not a valid name"):
                resolve(self.repo, "prompts", name)


class ResolveTest(RepoCase):
    """Prompts and lenses: the repository first, then the plugin; schemas from the plugin only."""

    def test_override_промта(self) -> None:
        own = self.layer("prompts/review.md", "Our review body {{SCOPE}}")
        self.assertEqual(resolve(self.repo, "prompts", "review"), own)
        self.assertEqual(resolve(self.repo, "prompts", "verify"), PLUGIN / "prompts" / "verify.md")

    def test_override_линзы(self) -> None:
        own = self.layer("lenses/bugs.md", "## Lens: bugs\n\nOurs.\n")
        self.assertEqual(resolve(self.repo, "lenses", "bugs"), own)
        self.assertEqual(resolve(self.repo, "lenses", "impl"), PLUGIN / "lenses" / "impl.md")

    def test_новая_линза_только_в_репозитории(self) -> None:
        own = self.layer("lenses/perf.md", "## Lens: perf\n\nHot paths.\n")
        self.layer("cross-review.json", {"roster": [{"name": "perf", "lenses": ["perf", "bugs"]}]})
        self.assertEqual(load_config(self.repo)["roster"][0]["lenses"], ["perf", "bugs"])
        self.assertEqual(resolve(self.repo, "lenses", "perf"), own)
        with self.assertRaisesRegex(ConfigError, "no lens 'perf'"):
            resolve(None, "lenses", "perf")

    def test_схемы_только_из_плагина(self) -> None:
        self.layer("schemas/finder.json", {"type": "object"})
        self.assertEqual(resolve(self.repo, "schemas", "finder"), PLUGIN / "schemas" / "finder.json")
        with self.assertRaisesRegex(ConfigError, "no schema 'nope'"):
            resolve(self.repo, "schemas", "nope")

    def test_неизвестный_вид_файла(self) -> None:
        with self.assertRaises(ValueError):
            resolve(self.repo, "templates", "report")

    def test_ссылка_за_пределы_репозитория_отклоняется(self) -> None:
        outside = self.repo.parent / f"{self.repo.name}-secret.md"
        outside.write_text("секрет")
        self.addCleanup(outside.unlink)
        (self.repo / ".cross-review" / "prompts").mkdir(parents=True)
        (self.repo / ".cross-review" / "prompts" / "review.md").symlink_to(outside)
        with self.assertRaisesRegex(ConfigError, "a symlink in the repository's .cross-review/"):
            resolve(self.repo, "prompts", "review")

    def test_ссылка_на_каталог_слоя_снаружи_отклоняется(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            write_json(Path(other) / "cross-review.json", {"tier": "expert"})
            (self.repo / ".cross-review").symlink_to(other)
            with self.assertRaisesRegex(ConfigError, "a symlink in the repository's .cross-review/"):
                load_config(self.repo)

    def test_ссылка_внутри_репозитория_тоже_отклоняется(self) -> None:
        # .git/config или игнорируемый .env лежат внутри репозитория, но в промт модели попасть не должны
        (self.repo / ".env").write_text("TOKEN=секрет")
        (self.repo / ".cross-review" / "prompts").mkdir(parents=True)
        (self.repo / ".cross-review" / "prompts" / "review.md").symlink_to(Path("..") / ".." / ".env")
        with self.assertRaisesRegex(ConfigError, "a symlink in the repository's .cross-review/"):
            resolve(self.repo, "prompts", "review")


class ShowTest(RepoCase):
    """`show`: the agents of the roster, the stage model, the moderators and where every file comes from."""

    def show(self, *args: str) -> dict:
        res = run("cross_config", "show", self.repo, *args)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return last_json_line(res.stdout)

    def test_уровень_по_умолчанию(self) -> None:
        out = self.show()
        self.assertEqual(out["tier"], "standard")
        self.assertFalse(out["pair"])
        self.assertEqual([(a["name"], a["vendor"], a["model"], a["effort"]) for a in out["agents"]], [
            ("bugs-claude", "claude", "opus", "high"), ("bugs-codex", "codex", "gpt-6.1-sol", "high"),
            ("arch-claude", "claude", "opus", "high"), ("arch-codex", "codex", "gpt-6.1-sol", "high"),
        ])
        self.assertEqual(out["agents"][0]["lenses"], ["bugs", "impl"])
        self.assertEqual(out["agents"][3]["lenses"], ["architecture", "quality"])
        self.assertEqual((out["stages"]["model"], out["stages"]["effort"]), ("opus", "high"))
        self.assertTrue(out["stages"]["verify"])
        self.assertEqual(out["moderators"], {"claude": {"model": "fable", "effort": "high"},
                                             "codex": {"model": "gpt-6-astra", "effort": "high"}})
        self.assertEqual([layer["name"] for layer in out["layers"]], ["plugin"])
        self.assertEqual(set(out["prompts"]), set(PLACEHOLDERS))
        self.assertEqual({v["layer"] for v in out["prompts"].values()}, {"plugin"})
        self.assertEqual(set(out["lenses"]), {"bugs", "impl", "architecture", "quality"})
        self.assertEqual(set(out["schemas"]), {"finder", "synthesis", "verify"})

    def test_уровень_expert(self) -> None:
        out = self.show("--tier", "expert")
        self.assertEqual(out["claude"], {"model": "fable", "effort": "xhigh"})
        self.assertEqual(out["codex"], {"model": "gpt-6-astra", "effort": "xhigh"})
        self.assertEqual(out["moderators"]["claude"], {"model": "opus", "effort": "xhigh"})

    def test_разовая_пара_моделей(self) -> None:
        out = self.show("--claude", "sonnet", "--codex", "gpt-6-sol:medium")
        self.assertTrue(out["pair"])
        self.assertEqual(out["claude"], {"model": "sonnet", "effort": "high"})
        self.assertEqual(out["codex"], {"model": "gpt-6-sol", "effort": "medium"})
        self.assertEqual({a["model"] for a in out["agents"]}, {"sonnet", "gpt-6-sol"})
        self.assertEqual(out["stages"]["model"], "sonnet")
        # модераторы с разовой парой — из уровня конфигурации
        self.assertEqual(out["tier"], "standard")
        self.assertEqual(out["moderators"]["codex"], {"model": "gpt-6-astra", "effort": "high"})

    def test_слой_репозитория_виден_в_show(self) -> None:
        self.layer("cross-review.json", {"tiers": {"standard": {"codex": "gpt-6-sol"}},
                                         "roster": [{"name": "perf", "lenses": ["perf"]}]})
        self.layer("prompts/review.md", "Ours {{SCOPE}}")
        self.layer("lenses/perf.md", "## Lens: perf\n")
        out = self.show()
        self.assertEqual(out["layers"][1], {"name": "repo", "dir": str(self.repo / ".cross-review"), "config": True})
        self.assertEqual([a["name"] for a in out["agents"]], ["perf-claude", "perf-codex"])
        self.assertEqual(out["codex"]["model"], "gpt-6-sol")
        self.assertEqual(out["prompts"]["review"]["layer"], "repo")
        self.assertEqual(out["prompts"]["synthesis"]["layer"], "plugin")
        self.assertEqual(out["lenses"], {"perf": {"path": str(self.repo / ".cross-review/lenses/perf.md"), "layer": "repo"}})

    def test_неверные_аргументы_show(self) -> None:
        for args in (["--claude", "sonnet"], ["--codex", "gpt-6-sol"], ["--tier", "expert", "--claude", "opus", "--codex", "x"]):
            with self.subTest(args=args):
                res = run("cross_config", "show", self.repo, *args)
                self.assertEqual(res.returncode, 2)
                self.assertIn("usage:", res.stderr)

    def test_ошибки_show_выход_1(self) -> None:
        cases = [
            (["--tier", "ultra"], "no tier 'ultra'"),
            (["--claude", "gpt-6", "--codex", "gpt-6-sol"], "--claude: claude model 'gpt-6'"),
            (["--claude", "opus:turbo", "--codex", "gpt-6-sol"], "--claude: effort 'turbo'"),
        ]
        for args, message in cases:
            with self.subTest(args=args):
                res = run("cross_config", "show", self.repo, *args)
                self.assertEqual(res.returncode, 1)
                self.assertIn(message, last_json_line(res.stdout)["error"])

    def test_ошибка_конфигурации_репозитория(self) -> None:
        self.layer("cross-review.json", {"roster": [{"name": "bugs", "lenses": ["nope"]}]})
        res = run("cross_config", "show", self.repo)
        self.assertEqual(res.returncode, 1)
        self.assertIn("no lens 'nope'", last_json_line(res.stdout)["error"])


class SetupTest(RepoCase):
    """`setup` writes what the owner tunes from, never overwrites it, and refreshes defaults/ only."""

    def setup_repo(self) -> dict:
        res = run("cross_config", "setup", self.repo)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return last_json_line(res.stdout)

    def plugin_files(self) -> list[str]:
        files = ["cross-review.json", "LICENSE"]
        for kind in ("prompts", "lenses"):
            files += [f"{kind}/{p.name}" for p in sorted((PLUGIN / kind).glob("*.md"))]
        return files

    def test_создаёт_минимальный_конфиг_readme_и_образец(self) -> None:
        out = self.setup_repo()
        layer = self.repo / ".cross-review"
        self.assertEqual((out["created"], out["kept"]), (["cross-review.json", "README.md"], []))
        self.assertEqual(read_json(layer / "cross-review.json"), {"tier": DEFAULTS["tier"]})
        self.assertIn("/cross-review:cross-review-setup", (layer / "README.md").read_text())
        self.assertEqual(sorted(out["defaults_changed"]), sorted(self.plugin_files()))
        for rel in self.plugin_files():
            with self.subTest(rel=rel):
                self.assertEqual((layer / "defaults" / rel).read_text(), (PLUGIN / rel).read_text())
        self.assertEqual(load_config(self.repo), DEFAULTS)

    def test_повтор_не_трогает_своё_и_обновляет_образец(self) -> None:
        self.setup_repo()
        layer = self.repo / ".cross-review"
        write_json(layer / "cross-review.json", {"tier": "expert"})
        (layer / "README.md").write_text("своё\n")
        (layer / "defaults" / "prompts" / "review.md").write_text("старая версия\n")
        (layer / "defaults" / "lenses" / "gone.md").write_text("линза, которой в плагине больше нет\n")
        out = self.setup_repo()
        self.assertEqual((out["created"], out["kept"]), ([], ["cross-review.json", "README.md"]))
        self.assertEqual(out["defaults_changed"], ["-lenses/gone.md", "prompts/review.md"])
        self.assertEqual(read_json(layer / "cross-review.json"), {"tier": "expert"})
        self.assertEqual((layer / "README.md").read_text(), "своё\n")
        self.assertEqual((layer / "defaults" / "prompts" / "review.md").read_text(), (PLUGIN / "prompts" / "review.md").read_text())
        self.assertFalse((layer / "defaults" / "lenses" / "gone.md").exists())
        self.assertEqual(self.setup_repo()["defaults_changed"], [])

    def test_образец_не_влияет_на_конфигурацию(self) -> None:
        self.setup_repo()
        defaults = self.repo / ".cross-review" / "defaults"
        (defaults / "prompts" / "review.md").write_text("{{NOPE}}\n")
        write_json(defaults / "cross-review.json", {"unknown_key": 1})
        res = run("cross_config", "show", self.repo)
        self.assertEqual(res.returncode, 0, res.stdout)
        out = last_json_line(res.stdout)
        self.assertEqual({v["layer"] for v in out["prompts"].values()}, {"plugin"})
        self.assertEqual({v["layer"] for v in out["lenses"].values()}, {"plugin"})

    def test_копия_из_образца_перекрывает_плагин(self) -> None:
        self.setup_repo()
        layer = self.repo / ".cross-review"
        (layer / "prompts").mkdir()
        (layer / "prompts" / "review.md").write_text((layer / "defaults" / "prompts" / "review.md").read_text())
        res = run("cross_config", "show", self.repo)
        self.assertEqual(res.returncode, 0, res.stdout)
        prompts = last_json_line(res.stdout)["prompts"]
        self.assertEqual(prompts["review"]["layer"], "repo")
        self.assertEqual(prompts["synthesis"]["layer"], "plugin")

    def test_не_пишет_через_ссылку_на_каталог_снаружи(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            (self.repo / ".cross-review").symlink_to(other)
            res = run("cross_config", "setup", self.repo)
            self.assertEqual(res.returncode, 1)
            self.assertIn("a symlink in the repository's .cross-review/", last_json_line(res.stdout)["error"])
            self.assertEqual(list(Path(other).iterdir()), [])

    def test_не_пишет_через_висячую_ссылку_наружу(self) -> None:
        outside = self.repo.parent / f"{self.repo.name}-config.json"
        (self.repo / ".cross-review").mkdir()
        (self.repo / ".cross-review" / "cross-review.json").symlink_to(outside)
        res = run("cross_config", "setup", self.repo)
        self.assertEqual(res.returncode, 1)
        self.assertIn("a symlink in the repository's .cross-review/", last_json_line(res.stdout)["error"])
        self.assertFalse(outside.exists())

    def test_без_каталога(self) -> None:
        res = run("cross_config", "setup", self.repo / "missing")
        self.assertEqual(res.returncode, 1)
        self.assertIn("no such directory", last_json_line(res.stdout)["error"])
        self.assertFalse((self.repo / "missing").exists())


class SelectTest(unittest.TestCase):
    """select() guards its own contract for the scripts that call it directly."""

    def test_пара_без_второй_модели(self) -> None:
        with self.assertRaises(ValueError):
            cross_config.select(DEFAULTS, claude="opus")
        with self.assertRaises(ValueError):
            cross_config.select(DEFAULTS, tier="expert", claude="opus", codex="gpt-6-sol")



class TextTest(RepoCase):
    """The prompt text the scripts work with: front matter dropped, placeholders filled in one pass."""

    def test_front_matter_снимается(self) -> None:
        cases = [
            ("---\ndescription: x\n---\n\nТело\n", "Тело\n"),
            ("Тело без шапки\n", "Тело без шапки\n"),
            ("---\nне закрыта\nТело\n", "---\nне закрыта\nТело\n"),
            ("Тело\n---\na: b\n---\n", "Тело\n---\na: b\n---\n"),
        ]
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(cross_config.strip_front_matter(text), want)

    def test_body_берёт_слой_репозитория(self) -> None:
        self.assertTrue(cross_config.body(self.repo, "lenses", "bugs").startswith("## Lens: bugs\n"))
        self.layer("lenses/bugs.md", "---\ndescription: своя\n---\n## Lens: bugs\n\nСвоя линза.\n")
        self.assertEqual(cross_config.body(self.repo, "lenses", "bugs"), "## Lens: bugs\n\nСвоя линза.")

    def test_подстановка(self) -> None:
        values = {"SCOPE": "/run/input/scope.md", "GOAL": "none provided"}
        self.assertEqual(cross_config.render("{{SCOPE}} и {{GOAL}}, {{SCOPE}}", values, "review"),
                         "/run/input/scope.md и none provided, /run/input/scope.md")
        self.assertEqual(cross_config.render("{{SCOPE}}", {"SCOPE": "текст с {{message}}"}, "review"),
                         "текст с {{message}}")
        self.assertEqual(cross_config.render("{{ SCOPE }} и {scope}", values, "review"), "{{ SCOPE }} и {scope}")

    def test_неизвестный_плейсхолдер_ошибка(self) -> None:
        with self.assertRaises(ConfigError) as caught:
            cross_config.render("{{SCOPE}} {{TICKET}}", {"SCOPE": "x"}, "prompts/review.md")
        self.assertIn("prompts/review.md: unknown placeholder {{TICKET}}", str(caught.exception))
        self.assertIn("{{SCOPE}}", str(caught.exception))


class SidesTest(RepoCase):
    """`sides`: the debate runs on the review's own models; the moderators come from the run or the tier."""

    def sides(self, *args: object) -> dict:
        res = run("cross_config", "sides", *args)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        return last_json_line(res.stdout)

    def failing(self, *args: object) -> str:
        res = run("cross_config", "sides", *args)
        self.assertEqual(res.returncode, 1, res.stdout + res.stderr)
        return last_json_line(res.stdout)["error"]

    def external_run(self, claude: dict | None = None) -> Path:
        """run-basic as an external run: no manifest, the models only in findings.json; claude overrides its agents."""
        round_dir = copy_fixture("run-basic", self.repo / "external" / "tasks" / "demo" / "01-initial")
        if claude is not None:
            findings = read_json(round_dir / "findings.json")
            for agent in findings["sources"]["agents"]:
                if agent["executor"] == "claude":
                    agent.update(claude)
            write_json(round_dir / "findings.json", findings)
        return round_dir

    def test_свой_прогон_из_манифеста(self) -> None:
        run_dir = composed_run(self.repo)
        self.assertEqual(self.sides(run_dir), {
            "source": "manifest",
            "claude": {"alias": "opus", "effort": "high"},
            "codex": {"model": "gpt-6.1-sol", "effort": "high"},
            "moderators": {"claude": {"alias": "fable", "effort": "high"}, "codex": {"model": "gpt-6-astra", "effort": "high"}},
            "moderators_source": "manifest",
        })

    def test_свой_прогон_с_разовой_парой(self) -> None:
        run_dir = composed_run(self.repo)
        res = run("review_run", "compose", run_dir, "--claude", "sonnet:medium", "--codex", "gpt-6-sol:low")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        out = self.sides(run_dir)
        self.assertEqual((out["claude"], out["codex"]),
                         ({"alias": "sonnet", "effort": "medium"}, {"model": "gpt-6-sol", "effort": "low"}))
        self.assertEqual(out["moderators"]["claude"], {"alias": "fable", "effort": "high"})

    def test_внешний_прогон_из_findings(self) -> None:
        out = self.sides(self.external_run(), "--repo", self.repo)
        self.assertEqual((out["source"], out["claude"], out["codex"]),
                         ("findings", {"alias": "opus", "effort": "high"}, {"model": "gpt-6.1-sol", "effort": "high"}))
        self.assertEqual(out["moderators"], {"claude": {"alias": "fable", "effort": "high"},
                                             "codex": {"model": "gpt-6-astra", "effort": "high"}})
        self.assertEqual(out["moderators_source"], "config:standard")

    def test_claude_модель_становится_алиасом(self) -> None:
        cases = [
            ("opus", {"actual_model": "claude-opus-5-5", "requested_model": "claude-opus-5-5"}, "opus"),
            ("fable", {"actual_model": "claude-fable-5-1", "requested_model": "fable"}, "fable"),
            ("sonnet", {"actual_model": "claude-sonnet-5-5", "requested_model": "sonnet", "effort": "medium"}, "sonnet"),
            ("только запрошенная", {"actual_model": "", "requested_model": "haiku"}, "haiku"),
            ("фактическая без алиаса", {"actual_model": "claude-preview", "requested_model": "opus"}, "opus"),
        ]
        for label, agent, alias in cases:
            with self.subTest(case=label):
                round_dir = self.external_run(agent)
                try:
                    out = self.sides(round_dir, "--repo", self.repo)
                    self.assertEqual(out["claude"], {"alias": alias, "effort": agent.get("effort", "high")})
                finally:
                    (self.repo / "external").rename(self.repo / f"external-{label}")

    def test_модераторы_внешнего_прогона_из_уровня_репозитория(self) -> None:
        self.layer("cross-review.json", {"tier": "expert"})
        out = self.sides(self.external_run(), "--repo", self.repo)
        self.assertEqual(out["moderators"], {"claude": {"alias": "opus", "effort": "xhigh"},
                                             "codex": {"model": "gpt-6.1-sol", "effort": "xhigh"}})
        self.assertEqual(out["moderators_source"], "config:expert")
        self.assertEqual(out["claude"], {"alias": "opus", "effort": "high"})

    def test_модель_большинства_здоровых_агентов(self) -> None:
        round_dir = self.external_run()
        findings = read_json(round_dir / "findings.json")
        agents = findings["sources"]["agents"]
        agents[0].update(actual_model="claude-sonnet-5-5", degraded=True)
        write_json(round_dir / "findings.json", findings)
        self.assertEqual(self.sides(round_dir, "--repo", self.repo)["claude"]["alias"], "opus")

    def test_ошибки_sides(self) -> None:
        cases = [
            ("неизвестная claude-модель", {"actual_model": "claude-mystery-1", "requested_model": "mystery"},
             "has no Agent alias"),
            ("неверный effort", {"effort": "turbo"}, "effort 'turbo'"),
            ("без модели", {"actual_model": "", "requested_model": ""}, "names no model"),
        ]
        for label, agent, message in cases:
            with self.subTest(case=label):
                round_dir = self.external_run(agent)
                try:
                    self.assertIn(message, self.failing(round_dir, "--repo", self.repo))
                finally:
                    (self.repo / "external").rename(self.repo / f"external-{label}")

    def test_раунд_без_codex(self) -> None:
        round_dir = copy_fixture("run-one-vendor", self.repo / "run")
        self.assertIn("no codex agent", self.failing(round_dir, "--repo", self.repo))

    def test_без_findings_и_манифеста(self) -> None:
        self.assertIn("no models in manifest.json and no findings.json", self.failing(self.repo))

    def test_битый_манифест(self) -> None:
        run_dir = composed_run(self.repo)
        manifest = read_json(run_dir / "manifest.json")
        manifest["models"]["claude"] = {"model": "claude-opus-5-5", "effort": "high"}
        write_json(run_dir / "manifest.json", manifest)
        self.assertIn("manifest.json models.claude", self.failing(run_dir))

    def test_алиас_claude(self) -> None:
        cases = [("opus", "opus"), ("claude-opus-5-5", "opus"), ("claude-3-5-sonnet-20241022", "sonnet"),
                 ("claude-fable-5-1[1m]", "fable"), ("claude-haiku-5-5", "haiku")]
        for model, alias in cases:
            with self.subTest(model=model):
                self.assertEqual(cross_config.claude_alias(model), alias)
        for model in ("gpt-6.1-sol", "claude-mystery-1", "opus-5", "claude-"):
            with self.subTest(model=model), self.assertRaises(ConfigError):
                cross_config.claude_alias(model)


if __name__ == "__main__":
    unittest.main()
