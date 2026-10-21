# Changelog

Формат — [Keep a Changelog](https://keepachangelog.com/ru/1.1.0/), версии — [semver](https://semver.org/lang/ru/).

## [0.1.0] — 2026-10-21

Первый публичный выпуск.

### Добавлено

- Скил `cross-review`: находки Claude и Codex по одному ростеру линз, синтез, проверка по коду, спор
  сторон в два раунда, модератор — сессия Claude или пара независимых моделей, итог с пересчитанными
  приоритетами, `report.md` и HTML-страница отчёта.
- Скил `cross-review-setup`: `.cross-review/` в репозитории с минимальным `cross-review.json`, README
  для владельца и копией дефолтов плагина.
- Двухслойная конфигурация: дефолты плагина и `.cross-review/` репозитория — уровни моделей, ростер,
  стадии, промты и линзы.
- PR GitHub и MR GitLab как предмет ревью через `gh` и `glab`.
- Маркетплейс Claude Code в том же репозитории: `claude plugin marketplace add troyan-dy/cross-review`.

[0.1.0]: https://github.com/troyan-dy/cross-review/releases/tag/v0.1.0
