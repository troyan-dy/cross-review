from __future__ import annotations

import re
import tempfile
import types
import unittest
from pathlib import Path

from support import SKILL, composed_run, copy_fixture, last_json_line, pick, read_json, run, write_json

from crossreview import models, selection, texts
from crossreview.config_load import load_config
from crossreview.config_shape import merge
from crossreview.errors import ConfigError
from crossreview.layers import resolve

PLUGIN = SKILL / "assets" / "config"
DEFAULTS = read_json(PLUGIN / "cross-review.json")
SYMLINK = "a symlink in the repository's .cross-review/"
LENSES = ("bugs", "impl", "architecture", "quality", "docs", "tests", "comments", "adversarial")
SOL = "gpt-6.1-sol"
OWN_FILES = ("cross-review.json", "README.md")
HIGH = "high"
DEFAULT_AGENTS = (
    ("bugs-claude", "claude", "opus", HIGH),
    ("bugs-codex", "codex", SOL, HIGH),
    ("arch-claude", "claude", "opus", HIGH),
    ("arch-codex", "codex", SOL, HIGH),
)
STANDARD_MODERATORS = types.MappingProxyType({
    "claude": {"model": "fable", "effort": HIGH},
    "codex": {"model": "gpt-6-astra", "effort": HIGH},
})
SIDES = types.MappingProxyType({
    "source": "manifest",
    "claude": {"alias": "opus", "effort": HIGH},
    "codex": {"model": SOL, "effort": HIGH},
    "moderators": {"claude": {"alias": "fable", "effort": HIGH}, "codex": {"model": "gpt-6-astra", "effort": HIGH}},
    "moderators_source": "manifest",
})
EXPERT_MODERATORS = types.MappingProxyType({
    "claude": {"alias": "opus", "effort": "xhigh"},
    "codex": {"model": SOL, "effort": "xhigh"},
})

# the placeholders each default prompt may use; the scripts substitute exactly these
PLACEHOLDERS = types.MappingProxyType({
    "review": frozenset(("SCOPE", "GOAL", "PROFILE", "CONTEXT", "WORKDIR")),
    "synthesis": frozenset(("SOURCES", "FINDINGS")),
    "verify": frozenset(("SCOPE", "PROFILE", "WORKDIR", "FINDINGS")),
    "examine": frozenset((
        "SIDE", "OTHER", "SUBJECT", "WORKDIR", "PANEL", "CONTEXT", "TREE_NOTE", "INPUT", "STATUS_LEGEND", "OUTPUT",
    )),
    "respond": frozenset(("SIDE", "OTHER", "INPUT", "OUTPUT")),
    "moderate": frozenset(("SUBJECT", "WORKDIR", "CONTEXT", "TREE_NOTE", "INPUT", "OUTPUT")),
})


def plugin_text(*parts: str) -> str:
    return PLUGIN.joinpath(*parts).read_text()


def plugin_files() -> list[str]:
    """Every file setup copies into defaults/: the config, the license, every prompt and lens."""
    copied = [
        f"{kind}/{path.name}"
        for kind in ("prompts", "lenses")
        for path in sorted((PLUGIN / kind).glob("*.md"))
    ]
    return ["cross-review.json", "LICENSE", *copied]


def layers_of(resolved: dict) -> set[str]:
    """The layers a show listing of prompts or lenses resolves from."""
    return {entry["layer"] for entry in resolved.values()}


class RepoCase(unittest.TestCase):
    """A temporary repository; .cross-review/ files are written per test."""

    def setUp(self) -> None:
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.repo = Path(workspace.name).resolve()
        self.own = self.repo / ".cross-review"

    def layer(self, rel: str, written: object) -> Path:
        path = self.own / rel
        if isinstance(written, str):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(written)
        else:
            write_json(path, written)
        return path

    def config(self) -> dict:
        return load_config(self.repo)

    def config_error(self) -> str:
        """The message load_config stops with on the repository layer."""
        try:
            load_config(self.repo)
        except ConfigError as error:
            return str(error)
        raise AssertionError("load_config accepted the repository layer")

    def cross_config(self, *args: object, code: int = 0) -> dict:
        res = run("cross_config", *args)
        self.assertEqual(res.returncode, code, res.stdout + res.stderr)
        return last_json_line(res.stdout)

    def cross_config_error(self, *args: object) -> str:
        return self.cross_config(*args, code=1)["error"]


class DefaultsTest(unittest.TestCase):
    """The plugin layer on its own is a complete, valid configuration."""

    def test_дефолты_проходят_валидацию(self) -> None:
        self.assertEqual(load_config(None), DEFAULTS)

    def test_файлы_дефолтов_на_месте(self) -> None:
        for kind, names in (("prompts", tuple(PLACEHOLDERS)), ("lenses", LENSES)):
            for name in names:
                with self.subTest(kind=kind, name=name):
                    expected = PLUGIN / kind / f"{name}.md"
                    self.assertEqual(resolve(None, kind, name), expected)
        for schema in ("finder", "synthesis", "verify"):
            with self.subTest(schema=schema):
                self.assertIsInstance(read_json(resolve(None, "schemas", schema)), dict)
        self.assertIn("MIT License", plugin_text("LICENSE"))

    def test_плейсхолдеры_промтов_из_нашего_набора(self) -> None:
        for name, expected in PLACEHOLDERS.items():
            with self.subTest(prompt=name):
                text = plugin_text("prompts", f"{name}.md")
                self.assertEqual(set(re.findall(r"\{\{([A-Z_]+)\}\}", text)), expected)

    def test_промты_требуют_русский_в_ответе(self) -> None:
        for name in PLACEHOLDERS:
            with self.subTest(prompt=name):
                self.assertIn("in Russian", plugin_text("prompts", f"{name}.md"))


class MergeTest(RepoCase):
    """The repository layer over the defaults: objects merge recursively, arrays are replaced whole."""

    def test_слияние_объектов_и_замена_массивов(self) -> None:
        cases = (
            (
                {"a": 1, "b": {"c": 1, "d": 2}},
                {"b": {"d": 3}},
                {"a": 1, "b": {"c": 1, "d": 3}},
            ),
            ({"a": [1, 2]}, {"a": [3]}, {"a": [3]}),
            (
                {"a": {"b": 1}},
                {"c": {"d": 2}},
                {"a": {"b": 1}, "c": {"d": 2}},
            ),
            ({"a": {"b": 1}}, {}, {"a": {"b": 1}}),
        )
        for base, over, want in cases:
            with self.subTest(base=base, over=over):
                self.assertEqual(merge(base, over), want)

    def test_без_слоя_репозитория_только_дефолты(self) -> None:
        self.assertEqual(self.config(), DEFAULTS)

    def test_частичный_override_уровня(self) -> None:
        self.layer("cross-review.json", {"tiers": {"standard": {"codex": "gpt-6-sol:medium"}}})
        tiers = self.config()["tiers"]
        standard = DEFAULTS["tiers"]["standard"]
        self.assertEqual(tiers["standard"], {**standard, "codex": "gpt-6-sol:medium"})
        self.assertEqual(tiers["expert"], DEFAULTS["tiers"]["expert"])
        self.assertEqual(self.config()["roster"], DEFAULTS["roster"])

    def test_roster_заменяется_целиком(self) -> None:
        roster = [{"name": "docs", "lenses": ["docs", "comments"]}]
        self.layer("cross-review.json", {"roster": roster})
        self.assertEqual(self.config()["roster"], roster)

    def test_новый_уровень_и_выбор_по_умолчанию(self) -> None:
        cheap = {
            "claude": "haiku:low",
            "codex": "gpt-6-mini",
            "moderators": {"claude": "sonnet", "codex": "gpt-6-sol"},
        }
        self.layer("cross-review.json", {"tier": "cheap", "tiers": {"cheap": cheap}})
        cfg = self.config()
        self.assertEqual(cfg["tier"], "cheap")
        self.assertEqual(set(cfg["tiers"]), {"standard", "expert", "cheap"})


def standard(**tier: object) -> dict:
    """A repository layer that sets these keys of the standard tier."""
    return {"tiers": {"standard": tier}}


def roster(*agents: dict) -> dict:
    """A repository layer with this roster."""
    return {"roster": list(agents)}


def agent(name: str, *lenses: str) -> dict:
    return {"name": name, "lenses": list(lenses)}


def broken_layers() -> list:
    """(case, repository layer, error) for every layer load_config refuses."""
    return [
        ("неизвестный ключ", {"model": "opus"}, "model: unknown key"),
        ("неизвестный вложенный ключ", standard(runner="opus"), "tiers.standard.runner: unknown key"),
        ("неизвестный ключ стадий", {"stages": {"verify_parallel": 2}}, "stages.verify_parallel: unknown key"),
        ("claude-модель не алиас", standard(claude="claude-opus-5-5"), "tiers.standard.claude: claude model"),
        ("неверный effort", standard(codex="gpt-6-sol:ultra"), "tiers.standard.codex: effort 'ultra'"),
        ("пустой effort", {"tiers": {"expert": {"claude": "opus:"}}}, "tiers.expert.claude: effort ''"),
        ("неверная модель модератора", standard(moderators={"claude": "gpt-6"}),
         "tiers.standard.moderators.claude: claude model"),
        ("несуществующая линза", roster(agent("bugs", "bugs", "perf")), "roster[0].lenses: no lens 'perf'"),
        ("неизвестный уровень", {"tier": "ultra"}, "tier: 'ultra'"),
        ("неполный новый уровень", {"tiers": {"cheap": {"claude": "haiku", "codex": "gpt-6-mini"}}},
         "tiers.cheap: missing moderators"),
        ("неполная запись ростера", roster({"name": "bugs"}), "roster[0]: missing lenses"),
        ("пустой ростер", roster(), "roster: empty"),
        ("пустые линзы", roster(agent("bugs")), "roster[0].lenses: empty"),
        ("повтор имени в ростере", roster(agent("a", "bugs"), agent("a", "impl")), "roster[1].name: 'a' is already"),
        ("неверное имя в ростере", roster(agent("Bugs", "bugs")), "roster[0].name: 'Bugs'"),
        ("имя стадии в ростере", roster(agent("verify", "bugs")), "roster[0].name: 'verify' is reserved"),
        ("строка вместо числа", {"stages": {"verify_groups": "6"}}, "stages.verify_groups: expected an integer"),
        ("bool вместо числа", {"stages": {"verify_groups": True}}, "stages.verify_groups: expected an integer"),
        ("строка вместо bool", {"stages": {"synthesis": "no"}}, "stages.synthesis: expected true or false"),
        ("ноль групп", {"stages": {"verify_groups": 0}}, "stages.verify_groups: 0"),
        ("неверная группировка", {"stages": {"verify_group_by": "file"}}, "stages.verify_group_by: 'file'"),
        ("массив вместо объекта", [], "expected an object"),
    ]


class ErrorsTest(RepoCase):
    """Every broken repository layer is an error naming the file and the key."""

    def test_ошибки_конфигурации(self) -> None:
        for name, layer, message in broken_layers():
            with self.subTest(name):
                path = self.layer("cross-review.json", layer)
                error = self.config_error()
                self.assertIn(str(path), error)
                self.assertIn(message, error)

    def test_битый_json(self) -> None:
        path = self.layer("cross-review.json", '{"tier": ')
        with self.assertRaisesRegex(ConfigError, "not valid JSON"):
            self.config()
        self.assertTrue(path.exists())

    def test_промт_без_пары_в_плагине(self) -> None:
        self.layer("prompts/reveiw.md", "typo in the name")
        with self.assertRaisesRegex(ConfigError, "no plugin prompt 'reveiw'"):
            self.config()

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
        plugin = PLUGIN / "prompts" / "verify.md"
        self.assertEqual(resolve(self.repo, "prompts", "verify"), plugin)

    def test_override_линзы(self) -> None:
        own = self.layer("lenses/bugs.md", "## Lens: bugs\n\nOurs.\n")
        self.assertEqual(resolve(self.repo, "lenses", "bugs"), own)
        plugin = PLUGIN / "lenses" / "impl.md"
        self.assertEqual(resolve(self.repo, "lenses", "impl"), plugin)

    def test_новая_линза_только_в_репозитории(self) -> None:
        own = self.layer("lenses/perf.md", "## Lens: perf\n\nHot paths.\n")
        self.layer("cross-review.json", roster(agent("perf", "perf", "bugs")))
        first = self.config()["roster"][0]
        self.assertEqual(first["lenses"], ["perf", "bugs"])
        self.assertEqual(resolve(self.repo, "lenses", "perf"), own)
        with self.assertRaisesRegex(ConfigError, "no lens 'perf'"):
            resolve(None, "lenses", "perf")

    def test_схемы_только_из_плагина(self) -> None:
        self.layer("schemas/finder.json", {"type": "object"})
        plugin = PLUGIN / "schemas" / "finder.json"
        self.assertEqual(resolve(self.repo, "schemas", "finder"), plugin)
        with self.assertRaisesRegex(ConfigError, "no schema 'nope'"):
            resolve(self.repo, "schemas", "nope")

    def test_неизвестный_вид_файла(self) -> None:
        with self.assertRaises(ValueError):
            resolve(self.repo, "templates", "report")

    def test_ссылка_за_пределы_репозитория_отклоняется(self) -> None:
        name = self.repo.name
        outside = self.repo.parent / f"{name}-secret.md"
        outside.write_text("секрет")
        self.addCleanup(outside.unlink)
        (self.own / "prompts").mkdir(parents=True)
        (self.own / "prompts" / "review.md").symlink_to(outside)
        with self.assertRaisesRegex(ConfigError, SYMLINK):
            resolve(self.repo, "prompts", "review")

    def test_ссылка_на_каталог_слоя_снаружи_отклоняется(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            write_json(Path(other) / "cross-review.json", {"tier": "expert"})
            self.own.symlink_to(other)
            with self.assertRaisesRegex(ConfigError, SYMLINK):
                self.config()

    def test_ссылка_внутри_репозитория_тоже_отклоняется(self) -> None:
        # .git/config или игнорируемый .env лежат внутри репозитория, но в промт модели попасть не должны
        (self.repo / ".env").write_text("TOKEN=секрет")
        prompts = self.own / "prompts"
        prompts.mkdir(parents=True)
        (prompts / "review.md").symlink_to(Path("..") / ".." / ".env")
        with self.assertRaisesRegex(ConfigError, SYMLINK):
            resolve(self.repo, "prompts", "review")


class ShowTest(RepoCase):
    """`show`: the agents of the roster, the stage model, the moderators and where every file comes from."""

    def show(self, *args: str) -> dict:
        return self.cross_config("show", self.repo, *args)

    def test_уровень_по_умолчанию(self) -> None:
        out = self.show()
        self.assertEqual(out["tier"], "standard")
        self.assertFalse(out["pair"])
        self.check_agents(out["agents"])
        stages = out["stages"]
        self.assertEqual((stages["model"], stages["effort"]), ("opus", HIGH))
        self.assertTrue(stages["verify"])
        self.assertEqual(out["moderators"], STANDARD_MODERATORS)
        self.check_files(out)

    def check_agents(self, agents: list[dict]) -> None:
        described = [pick(entry, "name", "vendor", "model", "effort") for entry in agents]
        self.assertEqual(tuple(described), DEFAULT_AGENTS)
        self.assertEqual(agents[0]["lenses"], ["bugs", "impl"])
        self.assertEqual(agents[3]["lenses"], ["architecture", "quality"])

    def check_files(self, out: dict) -> None:
        self.assertEqual([layer["name"] for layer in out["layers"]], ["plugin"])
        self.assertEqual(set(out["prompts"]), set(PLACEHOLDERS))
        self.assertEqual(layers_of(out["prompts"]), {"plugin"})
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
        self.assertEqual(out["claude"], {"model": "sonnet", "effort": HIGH})
        self.assertEqual(out["codex"], {"model": "gpt-6-sol", "effort": "medium"})
        used = {entry["model"] for entry in out["agents"]}
        self.assertEqual(used, {"sonnet", "gpt-6-sol"})
        self.assertEqual(out["stages"]["model"], "sonnet")
        # модераторы с разовой парой — из уровня конфигурации
        self.assertEqual(out["tier"], "standard")
        self.assertEqual(out["moderators"]["codex"], {"model": "gpt-6-astra", "effort": HIGH})

    def test_слой_репозитория_виден_в_show(self) -> None:
        layer = {**standard(codex="gpt-6-sol"), **roster(agent("perf", "perf"))}
        self.layer("cross-review.json", layer)
        self.layer("prompts/review.md", "Ours {{SCOPE}}")
        self.layer("lenses/perf.md", "## Lens: perf\n")
        out = self.show()
        repo_layer = {"name": "repo", "dir": str(self.own), "config": True}
        self.assertEqual(out["layers"][1], repo_layer)
        names = [entry["name"] for entry in out["agents"]]
        self.assertEqual(names, ["perf-claude", "perf-codex"])
        self.assertEqual(out["codex"]["model"], "gpt-6-sol")
        self.assertEqual(out["prompts"]["review"]["layer"], "repo")
        self.assertEqual(out["prompts"]["synthesis"]["layer"], "plugin")
        perf = {"path": str(self.own / "lenses" / "perf.md"), "layer": "repo"}
        self.assertEqual(out["lenses"], {"perf": perf})

    def test_неверные_аргументы_show(self) -> None:
        cases = (
            ["--claude", "sonnet"],
            ["--codex", "gpt-6-sol"],
            ["--tier", "expert", "--claude", "opus", "--codex", "x"],
        )
        for args in cases:
            with self.subTest(args=args):
                res = run("cross_config", "show", self.repo, *args)
                self.assertEqual(res.returncode, 2)
                self.assertIn("usage:", res.stderr)

    def test_ошибки_show_выход1(self) -> None:
        cases = (
            (["--tier", "ultra"], "no tier 'ultra'"),
            (["--claude", "gpt-6", "--codex", "gpt-6-sol"], "--claude: claude model 'gpt-6'"),
            (["--claude", "opus:turbo", "--codex", "gpt-6-sol"], "--claude: effort 'turbo'"),
        )
        for args, message in cases:
            with self.subTest(args=args):
                self.assertIn(message, self.cross_config_error("show", self.repo, *args))

    def test_ошибка_конфигурации_репозитория(self) -> None:
        self.layer("cross-review.json", roster(agent("bugs", "nope")))
        self.assertIn("no lens 'nope'", self.cross_config_error("show", self.repo))


class SetupTest(RepoCase):
    """`setup` writes what the owner tunes from, never overwrites it, and refreshes defaults/ only."""

    def setup_repo(self) -> dict:
        return self.cross_config("setup", self.repo)

    def test_создаёт_минимальный_конфиг_readme_и_образец(self) -> None:
        out = self.setup_repo()
        self.assertEqual(out["created"], list(OWN_FILES))
        self.assertEqual(out["kept"], [])
        minimal = {"tier": DEFAULTS["tier"]}
        self.assertEqual(read_json(self.own / "cross-review.json"), minimal)
        self.assertIn("/cross-review:cross-review-setup", (self.own / "README.md").read_text())
        self.assertEqual(sorted(out["defaults_changed"]), sorted(plugin_files()))
        for rel in plugin_files():
            with self.subTest(rel=rel):
                copied = (self.own / "defaults" / rel).read_text()
                self.assertEqual(copied, plugin_text(rel))
        self.assertEqual(self.config(), DEFAULTS)

    def test_повтор_не_трогает_своё_и_обновляет_образец(self) -> None:
        self.setup_repo()
        defaults = self.own / "defaults"
        write_json(self.own / "cross-review.json", {"tier": "expert"})
        (self.own / "README.md").write_text("своё\n")
        (defaults / "prompts" / "review.md").write_text("старая версия\n")
        (defaults / "lenses" / "gone.md").write_text("линза, которой в плагине больше нет\n")
        out = self.setup_repo()
        self.assertEqual(pick(out, "created", "kept"), ([], list(OWN_FILES)))
        self.assertEqual(out["defaults_changed"], ["-lenses/gone.md", "prompts/review.md"])
        self.check_kept(defaults)
        self.assertEqual(self.setup_repo()["defaults_changed"], [])

    def check_kept(self, defaults: Path) -> None:
        """The owner's files untouched, defaults/ back to the plugin's."""
        self.assertEqual(read_json(self.own / "cross-review.json"), {"tier": "expert"})
        self.assertEqual((self.own / "README.md").read_text(), "своё\n")
        refreshed = (defaults / "prompts" / "review.md").read_text()
        self.assertEqual(refreshed, plugin_text("prompts", "review.md"))
        self.assertFalse((defaults / "lenses" / "gone.md").exists())

    def test_образец_не_влияет_на_конфигурацию(self) -> None:
        self.setup_repo()
        defaults = self.own / "defaults"
        (defaults / "prompts" / "review.md").write_text("{{NOPE}}\n")
        write_json(defaults / "cross-review.json", {"unknown_key": 1})
        out = self.cross_config("show", self.repo)
        self.assertEqual(layers_of(out["prompts"]), {"plugin"})
        self.assertEqual(layers_of(out["lenses"]), {"plugin"})

    def test_копия_из_образца_перекрывает_плагин(self) -> None:
        self.setup_repo()
        (self.own / "prompts").mkdir()
        sample = (self.own / "defaults" / "prompts" / "review.md").read_text()
        (self.own / "prompts" / "review.md").write_text(sample)
        prompts = self.cross_config("show", self.repo)["prompts"]
        self.assertEqual(prompts["review"]["layer"], "repo")
        self.assertEqual(prompts["synthesis"]["layer"], "plugin")

    def test_не_пишет_через_ссылку_на_каталог_снаружи(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            self.own.symlink_to(other)
            self.assertIn(SYMLINK, self.cross_config_error("setup", self.repo))
            self.assertEqual(list(Path(other).iterdir()), [])

    def test_не_пишет_через_висячую_ссылку_наружу(self) -> None:
        name = self.repo.name
        outside = self.repo.parent / f"{name}-config.json"
        self.own.mkdir()
        (self.own / "cross-review.json").symlink_to(outside)
        self.assertIn(SYMLINK, self.cross_config_error("setup", self.repo))
        self.assertFalse(outside.exists())

    def test_без_каталога(self) -> None:
        missing = self.repo / "missing"
        self.assertIn("no such directory", self.cross_config_error("setup", missing))
        self.assertFalse(missing.exists())


class SelectTest(unittest.TestCase):
    """select() guards its own contract for the scripts that call it directly."""

    def test_пара_без_второй_модели(self) -> None:
        with self.assertRaises(ValueError):
            selection.select(DEFAULTS, claude="opus")
        with self.assertRaises(ValueError):
            selection.select(DEFAULTS, tier="expert", claude="opus", codex="gpt-6-sol")


class TextTest(RepoCase):
    """The prompt text the scripts work with: front matter dropped, placeholders filled in one pass."""

    def test_front_matter_снимается(self) -> None:
        cases = (
            ("---\ndescription: x\n---\n\nТело\n", "Тело\n"),
            ("Тело без шапки\n", "Тело без шапки\n"),
            ("---\nне закрыта\nТело\n", "---\nне закрыта\nТело\n"),
            ("Тело\n---\na: b\n---\n", "Тело\n---\na: b\n---\n"),
        )
        for text, want in cases:
            with self.subTest(text=text):
                self.assertEqual(texts.strip_front_matter(text), want)

    def test_body_берёт_слой_репозитория(self) -> None:
        self.assertTrue(texts.body(self.repo, "lenses", "bugs").startswith("## Lens: bugs\n"))
        self.layer("lenses/bugs.md", "---\ndescription: своя\n---\n## Lens: bugs\n\nСвоя линза.\n")
        self.assertEqual(texts.body(self.repo, "lenses", "bugs"), "## Lens: bugs\n\nСвоя линза.")

    def test_подстановка(self) -> None:
        filled = {"SCOPE": "/run/input/scope.md", "GOAL": "none provided"}
        rendered = texts.render("{{SCOPE}} и {{GOAL}}, {{SCOPE}}", filled, "review")
        self.assertEqual(rendered, "/run/input/scope.md и none provided, /run/input/scope.md")
        nested = texts.render("{{SCOPE}}", {"SCOPE": "текст с {{message}}"}, "review")
        self.assertEqual(nested, "текст с {{message}}")
        self.assertEqual(texts.render("{{ SCOPE }} и {scope}", filled, "review"), "{{ SCOPE }} и {scope}")

    def test_неизвестный_плейсхолдер_ошибка(self) -> None:
        error = self.render_error("{{SCOPE}} {{TICKET}}", {"SCOPE": "x"})
        self.assertIn("prompts/review.md: unknown placeholder {{TICKET}}", error)
        self.assertIn("{{SCOPE}}", error)

    def render_error(self, text: str, filled: dict) -> str:
        try:
            texts.render(text, filled, "prompts/review.md")
        except ConfigError as error:
            return str(error)
        raise AssertionError("render accepted an unknown placeholder")


class SidesTest(RepoCase):
    """`sides`: the debate runs on the review's own models; the moderators come from the run or the tier."""

    def sides(self, *args: object) -> dict:
        return self.cross_config("sides", *args)

    def failing(self, *args: object) -> str:
        return self.cross_config_error("sides", *args)

    def external_run(self, claude: dict | None = None, root: str = "external") -> Path:
        """run-basic as an external run under root: no manifest, the models only in findings.json; claude
        overrides its agents."""
        round_dir = self.repo / root / "tasks" / "demo" / "01-initial"
        copy_fixture("run-basic", round_dir)
        if claude is not None:
            self.override_claude(round_dir / "findings.json", claude)
        return round_dir

    def override_claude(self, path: Path, claude: dict) -> None:
        findings = read_json(path)
        for entry in findings["sources"]["agents"]:
            if entry["executor"] == "claude":
                entry.update(claude)
        write_json(path, findings)

    def external_sides(self, round_dir: Path) -> dict:
        return self.sides(round_dir, "--repo", self.repo)

    def test_свой_прогон_из_манифеста(self) -> None:
        run_dir = composed_run(self.repo)
        self.assertEqual(self.sides(run_dir), SIDES)

    def test_свой_прогон_с_разовой_парой(self) -> None:
        run_dir = composed_run(self.repo)
        res = run("review_run", "compose", run_dir, "--claude", "sonnet:medium", "--codex", "gpt-6-sol:low")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        out = self.sides(run_dir)
        self.assertEqual(out["claude"], {"alias": "sonnet", "effort": "medium"})
        self.assertEqual(out["codex"], {"model": "gpt-6-sol", "effort": "low"})
        self.assertEqual(out["moderators"]["claude"], {"alias": "fable", "effort": HIGH})

    def test_внешний_прогон_из_findings(self) -> None:
        out = self.external_sides(self.external_run())
        self.assertEqual(out["source"], "findings")
        self.assertEqual(out["claude"], SIDES["claude"])
        self.assertEqual(out["codex"], SIDES["codex"])
        self.assertEqual(out["moderators"], SIDES["moderators"])
        self.assertEqual(out["moderators_source"], "config:standard")

    def test_claude_модель_становится_алиасом(self) -> None:
        cases = (
            ("opus", {"actual_model": "claude-opus-5-5", "requested_model": "claude-opus-5-5"}, "opus"),
            ("fable", {"actual_model": "claude-fable-5-1", "requested_model": "fable"}, "fable"),
            ("sonnet", {"actual_model": "claude-sonnet-5-5", "requested_model": "sonnet", "effort": "medium"}, "sonnet"),
            ("только запрошенная", {"actual_model": "", "requested_model": "haiku"}, "haiku"),
            ("фактическая без алиаса", {"actual_model": "claude-preview", "requested_model": "opus"}, "opus"),
        )
        for label, claude, alias in cases:
            with self.subTest(case=label):
                self.check_alias(claude, alias, f"external-{label}")

    def check_alias(self, claude: dict, alias: str, root: str) -> None:
        out = self.external_sides(self.external_run(claude, root))
        expected = {"alias": alias, "effort": claude.get("effort", HIGH)}
        self.assertEqual(out["claude"], expected)

    def test_модераторы_внешнего_прогона_из_уровня_репозитория(self) -> None:
        self.layer("cross-review.json", {"tier": "expert"})
        out = self.external_sides(self.external_run())
        self.assertEqual(out["moderators"], EXPERT_MODERATORS)
        self.assertEqual(out["moderators_source"], "config:expert")
        self.assertEqual(out["claude"], {"alias": "opus", "effort": HIGH})

    def test_модель_большинства_здоровых_агентов(self) -> None:
        round_dir = self.external_run()
        findings = read_json(round_dir / "findings.json")
        findings["sources"]["agents"][0].update(actual_model="claude-sonnet-5-5", degraded=True)
        write_json(round_dir / "findings.json", findings)
        self.assertEqual(self.external_sides(round_dir)["claude"]["alias"], "opus")

    def test_ошибки_sides(self) -> None:
        cases = (
            ("неизвестная claude-модель", {"actual_model": "claude-mystery-1", "requested_model": "mystery"},
             "has no Agent alias"),
            ("неверный effort", {"effort": "turbo"}, "effort 'turbo'"),
            ("без модели", {"actual_model": "", "requested_model": ""}, "names no model"),
        )
        for label, claude, message in cases:
            with self.subTest(case=label):
                round_dir = self.external_run(claude, f"external-{label}")
                self.assertIn(message, self.failing(round_dir, "--repo", self.repo))

    def test_раунд_без_codex(self) -> None:
        round_dir = copy_fixture("run-one-vendor", self.repo / "run")
        self.assertIn("no codex agent", self.failing(round_dir, "--repo", self.repo))

    def test_без_findings_и_манифеста(self) -> None:
        self.assertIn("no models in manifest.json and no findings.json", self.failing(self.repo))

    def test_битый_манифест(self) -> None:
        run_dir = composed_run(self.repo)
        manifest = read_json(run_dir / "manifest.json")
        manifest["models"]["claude"] = {"model": "claude-opus-5-5", "effort": HIGH}
        write_json(run_dir / "manifest.json", manifest)
        self.assertIn("manifest.json models.claude", self.failing(run_dir))

    def test_алиас_claude(self) -> None:
        cases = (
            ("opus", "opus"),
            ("claude-opus-5-5", "opus"),
            ("claude-3-5-sonnet-20241022", "sonnet"),
            ("claude-fable-5-1[1m]", "fable"),
            ("claude-haiku-5-5", "haiku"),
        )
        for model, alias in cases:
            with self.subTest(model=model):
                self.assertEqual(models.claude_alias(model), alias)
        for model in ("gpt-6.1-sol", "claude-mystery-1", "opus-5", "claude-"):
            with self.subTest(model=model), self.assertRaises(ConfigError):
                models.claude_alias(model)


if __name__ == "__main__":
    unittest.main()
