# Публичный выпуск cross-review

## Overview

Выпустить плагин `cross-review` отдельным публичным репозиторием
[troyan-dy/cross-review](https://github.com/troyan-dy/cross-review) и подготовить его к установке любым
пользователем Claude Code.

- Функциональность скилов `cross-review` и `cross-review-setup` переносится без изменений поведения.
- Язык всего, что читает пользователь (находки, сводки, `report.md`, страница отчёта), остаётся русским.
- Репозиторий — одновременно плагин и маркетплейс Claude Code: `.claude-plugin/plugin.json` и
  `.claude-plugin/marketplace.json` в корне, скилы в `skills/`.
- Из текстов убираются примеры, привязанные к одной компании: хосты, тикеты и названия
  сервисов. MR/PR поддерживаются для GitHub и GitLab через их CLI.
- Каждая итерация — коммит и пуш в `master`, отметки в этом плане.

## Итерации

### 1. Каркас репозитория

- [x] скопировать `skills/` без кэшей
- [x] `.claude-plugin/plugin.json` и `.claude-plugin/marketplace.json`
- [x] `LICENSE` (MIT) в корне; лицензия заимствованных промтов остаётся в `assets/config/LICENSE`
- [x] `.gitignore`: кэши Python и ruff, `.cross-review/runs/`
- [x] тесты скриптов проходят на Python 3.9 из нового корня

### 2. Очистка от внутренней специфики

- [x] `references/task-dir.md`: MR/PR через `gh pr view` / `glab mr view` вместо gitlab MCP
- [x] примеры имён задач и ссылок в скриптах и references без внутренних тикетов и хостов
- [x] тест `test_codex_run.py` не ссылается на соглашения маркетплейса
- [x] `grep` по внутренним хостам и тикетам пуст
- [x] тесты зелёные

### 3. Документация

- [x] `README.md`: что это, установка через маркетплейс, требования, настройка, как устроен прогон
- [x] `DEVELOPMENT.md`: раскладка, правила скриптов, тесты, выпуск версии
- [x] `CHANGELOG.md` с версией 0.1.0

### 4. CI и проверки

- [x] `ruff.toml` под стиль скриптов, `ruff check` чистый
- [x] GitHub Actions: unittest на Python 3.9 и 3.13 (ubuntu, macOS), ruff, `bash -n` для `codex_run.sh`, сверка манифестов и CHANGELOG
- [x] `claude plugin validate .` проходит для плагина и маркетплейса
- [ ] прогон CI на GitHub зелёный

### 5. Выпуск

- [ ] версия 0.1.0 в `plugin.json`, тег `v0.1.0`
- [ ] описание и темы репозитория на GitHub

## Решения

- **Русский язык вывода** сохраняется: проверяет `test_промты_требуют_русский_в_ответе`.
- **Только Claude Code.** Скил опирается на `Agent`, `SendMessage` и фоновые задачи Claude Code; сторона Codex
  запускается через `codex exec`, отдельный адаптер для Codex не нужен.
- **Автор коммитов** — `troyan-dy` с noreply-адресом GitHub, чтобы рабочая почта не попала в публичную историю.
