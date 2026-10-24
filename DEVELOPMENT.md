# Разработка cross-review

Заметки для тех, кто меняет плагин. Пользовательское описание — в [README](./README.md).

## Раскладка репозитория

Репозиторий — одновременно плагин Claude Code и маркетплейс из одного этого плагина:

```text
.claude-plugin/
├── plugin.json              # манифест плагина: имя, версия, описание
└── marketplace.json         # маркетплейс `cross-review`, единственный плагин — корень репозитория
skills/
├── cross-review/            # само ревью
└── cross-review-setup/      # подготовка .cross-review/ в репозитории
docs/plans/                  # планы работ с отметками о сделанном
.github/workflows/test.yml   # CI: тесты скриптов, ruff, проверка манифестов
.claude/hooks/session-start.sh  # SessionStart-хук облачной сессии Claude Code: ставит ruff
```

`cross-review-setup` зовёт `cross_config.py setup` первого скила через
`${CLAUDE_SKILL_DIR}/../cross-review/scripts`, своих скриптов у него нет.

## Раскладка скила

```text
skills/cross-review/
├── SKILL.md                 # процедура для агента: шаги 0–10, железные правила, частые ошибки
├── references/
│   ├── task-dir.md          # предмет ревью, input/ прогона, имена задач и прогонов
│   ├── review-stage.md      # брифы агентов находок, синтеза и проверки, формат findings.json
│   ├── protocol.md          # раскладка debate/, словари вердиктов, правило amend, вызовы сторон
│   ├── config.md            # схема cross-review.json, слияние слоёв, плейсхолдеры промтов
│   └── present.md           # как подать итог пользователю
├── assets/
│   ├── config/              # слой плагина — дефолты, которые перекрывает .cross-review/ репозитория
│   │   ├── cross-review.json # уровни моделей, ростер, стадии
│   │   ├── prompts/         # review, synthesis, verify; examine, respond, moderate для спора
│   │   ├── lenses/          # bugs, impl, architecture, quality, docs, tests, comments, adversarial
│   │   ├── schemas/         # finder, synthesis, verify — формат ответов агентов
│   │   └── LICENSE          # MIT: промты ревью, синтеза и проверки, линзы, схемы
│   └── templates/
│       ├── report.html      # шаблон страницы отчёта
│       └── setup-README.md  # README, который setup кладёт в .cross-review/
└── scripts/
    ├── cross_config.py      # единственный читатель обоих слоёв конфигурации: show, setup, sides
    ├── review_run.py        # new и compose: каталог прогона, промты агентов, manifest.json
    ├── review_collect.py    # ответы агентов → stages/1-found.json, деградация сторон
    ├── review_synth.py      # prepare/apply синтеза → stages/2-synthesized.json
    ├── review_verify.py     # prepare/apply проверки → stages/3-verified.json, findings.json, report.md
    ├── codex_run.sh         # один запуск codex exec только на чтение, с resume для раунда 2
    ├── debate_build.py      # раскладка находок на консенсус и на то, что проверяет каждая сторона
    ├── debate_prompts.py    # промты и схемы сторон, брифы модераторов, план запуска
    ├── debate_round2.py     # возражения раунда 1 → входы раунда 2
    ├── debate_outcome.py    # исход и итоговая severity по каждому пункту → final.json
    ├── debate_moderators.py # слияние двух независимых модераторов
    ├── debate_report.py     # страница отчёта из final.json
    ├── crossreview/         # пакет, в котором живёт логика скриптов (ниже)
    └── tests/               # unittest скриптов, фикстуры — синтетические прогоны
```

Скрипты — тонкие CLI: разбор аргументов и одна строка JSON. Их логика лежит в пакете
`scripts/crossreview/`, модули в нём сгруппированы префиксом по стадии:

| Модули | Что в них |
| --- | --- |
| `errors`, `jsonio`, `keys`, `schema`, `json_schema`, `events`, `severities`, `cli` | общее: ошибка конфигурации, вывод JSON, имена полей формата прогона, проверка по схеме, events.jsonl, аргументы CLI |
| `config_*`, `layers`, `links`, `texts`, `models`, `agent_models`, `selection`, `describe`, `setup_layer` | `cross_config.py`: слои конфигурации, модели, `show`, `setup`, `sides` |
| `runs`, `prior`, `history`, `run_new`, `compose`, `finder_prompts` | `review_run.py`: каталог прогона, прошлые прогоны, промты агентов |
| `collect`, `finder_answers`, `agent_logs` | `review_collect.py` |
| `stage_run`, `synth_*` | `review_synth.py` и общее для синтеза и проверки |
| `verify_*`, `report_md`, `report_lines` | `review_verify.py` и report.md |
| `debate_*`, `moderation` | `debate_build.py`, `debate_prompts.py`, `debate_round2.py`, `debate_moderators.py` |
| `outcome_*` | `debate_outcome.py` |
| `report_page`, `report_schema` | `debate_report.py` |

Каталог пакета не попадает в `scripts/*.py`, поэтому `tests/test_cli.py` проверяет контракт CLI только у
самих скриптов.

Агент вызывает только скрипты: ответы моделей он не разбирает и не сливает руками. Поэтому
правила стадий — дедупликация, пересчёт confidence, маршрутизация вердиктов, исходы спора — живут
в скриптах и покрыты тестами, а `SKILL.md` описывает, в каком порядке их звать и кого запускать
между вызовами.

Каждый файл скила должен быть достижим по ссылкам из `SKILL.md` или `references/`: агент не видит
файл, на который никто не ссылается. Каталог, названный в тексте (`assets/config/`), покрывает свои
файлы. Тело `SKILL.md` держите в пределах ~3000 токенов, `description` — ~200: скил грузится в
контекст целиком, а описание видно в каждой сессии.

## Линзы и промты

### Новая линза

1. Создайте `skills/cross-review/assets/config/lenses/<имя>.md`. Формат — как у соседних линз:
   front matter с `description`, затем заголовок `## Lens: <имя>`, что искать и что не трогать.
   Скрипты срезают front matter и вставляют секцию в промт агента после тела `review`.
2. В линзе можно использовать плейсхолдеры промта `review`: `{{SCOPE}} {{GOAL}} {{PROFILE}}
   {{CONTEXT}} {{WORKDIR}}`. Любой другой — ошибка при `compose`.
3. Чтобы линза шла по умолчанию, добавьте её в `roster` файла `assets/config/cross-review.json`. Иначе
   её подключает репозиторий в своём `roster`.
4. Допишите имя в список линз `DefaultsTest.test_файлы_дефолтов_на_месте` в
   `scripts/tests/test_cross_config.py` и в раскладку `assets/config/` в `references/config.md`.

Линзу под один репозиторий в плагин не кладите: её место — `.cross-review/lenses/` того
репозитория.

### Правка промта

Промты лежат в `skills/cross-review/assets/config/prompts/`. Каждый подставляется своим скриптом:

| Промт | Скрипт |
| --- | --- |
| `review` | `review_run.py compose` |
| `synthesis` | `review_synth.py prepare` |
| `verify` | `review_verify.py prepare` |
| `examine`, `respond`, `moderate` | `debate_prompts.py` |

При правке:

- оставьте в тексте требование отвечать по-русски (`in Russian`) — его проверяет
  `test_промты_требуют_русский_в_ответе`;
- новый плейсхолдер должен получить значение в скрипте этого промта, иначе подстановка упадёт с
  его именем. Добавьте его в `PLACEHOLDERS` в `scripts/tests/test_cross_config.py` и в таблицу
  плейсхолдеров `references/config.md`: репозиторий, который переопределяет промт, опирается на
  этот набор;
- схемы `assets/config/schemas/` меняйте вместе со скриптом, который читает ответ, — слой репозитория
  схемы не переопределяет.

Новый промт — это новая стадия. Его имя нужно и скрипту, который его подставит: репозиторий не
может переопределить промт, которого нет в плагине.

## Скрипты

Скрипты скила лежат в `skills/cross-review/scripts/`. Требования к ним:

- Python 3.9 или новее и только стандартная библиотека — у пользователя не должно быть
  зависимостей, кроме `python3`;
- `from __future__ import annotations` в каждом файле, без `match` и без `X | Y` в выражениях,
  которые выполняются во время работы, — иначе скрипт упадёт на 3.9;
- аргументы разбирает `argparse`: `-h`/`--help` печатает справку и выходит с кодом 0, неверные
  аргументы — справка в stderr и код 2;
- результат — одна JSON-строка в stdout, файлы пишутся только в каталог прогона;
- комментарии в Python и bash — по-русски, отступ — 4 пробела.

`scripts/codex_run.sh` должен работать в bash 3.2 из macOS. Под `set -u` пустой массив там
раскрывается только как `${arr[@]+"${arr[@]}"}`, а ассоциативных массивов и `mapfile` нет.

## Проверки

Всё, что гоняет CI, запускается локально из корня репозитория:

```sh
python3 -B -m unittest discover -s skills/cross-review/scripts/tests
ruff check .
bash -n skills/cross-review/scripts/codex_run.sh
python3 .github/scripts/check_manifests.py
claude plugin validate .claude-plugin/plugin.json
claude plugin validate .
```

Проверьте тесты на самой старой поддерживаемой версии — 3.9. Если локальный `python3` новее,
поставьте 3.9 рядом, например `brew install python@3.9`, и запустите той же командой через
`python3.9`. CI гоняет 3.9 и 3.14 на Linux и macOS.

Как устроены тесты:

- скрипт запускается как CLI через `subprocess`, внутренние функции импортируются напрямую из пакета
  `crossreview`;
- общие помощники — в `scripts/tests/support.py`;
- фикстуры в `scripts/tests/fixtures/` — синтетические прогоны в формате прогона. Реальные данные
  ревью туда не копируются;
- имена тестов — `test_<описание по-русски>`, табличные случаи идут через `subTest`;
- модели в тестах не вызываются, их ответы подставляются из фикстур. `codex` подменяется
  заглушкой в `PATH`, которая записывает свои аргументы, — так `scripts/tests/test_codex_run.py`
  проверяет флаги `codex exec` и `codex exec resume`.

## Локальная проверка плагина

Чтобы попробовать изменения в Claude Code до выпуска, подключите рабочую копию как маркетплейс:

```sh
claude plugin marketplace add /path/to/cross-review
claude plugin install cross-review@cross-review
```

После правок — `claude plugin marketplace update cross-review` и новая сессия.

## Выпуск версии

Claude Code обновляет установленный плагин, только когда меняется `version` в
`.claude-plugin/plugin.json`. Поэтому каждый выпуск:

1. поднимите `version` по semver: исправление — patch, новая возможность — minor, несовместимая
   правка формата прогона или `cross-review.json` — major;
2. допишите раздел версии в [CHANGELOG.md](./CHANGELOG.md);
3. закоммитьте, поставьте тег `v<версия>` и запушьте его вместе с веткой:

   ```sh
   git tag v0.1.0 && git push origin master v0.1.0
   ```
