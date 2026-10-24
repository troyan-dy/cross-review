#!/bin/bash
# SessionStart-хук облачной сессии Claude Code: готовит окружение для тестов, ruff и wemake-python-styleguide.
# Скриптам скила нужен только python3 3.9+ — ставим лишь линтеры тех же версий, что в CI.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

RUFF_VERSION="0.17.2"
WPS_VERSION="1.8.1"

python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' || {
  echo "session-start: нужен python3 3.9 или новее, а найден $(python3 --version 2>&1)" >&2
  exit 1
}

# повторный запуск ничего не ставит, если нужная версия ruff уже на месте
if [ "$(ruff --version 2>/dev/null || true)" != "ruff $RUFF_VERSION" ]; then
  if command -v uv >/dev/null 2>&1; then
    uv tool install --quiet --force "ruff==$RUFF_VERSION"
  else
    python3 -m pip install --quiet --user "ruff==$RUFF_VERSION"
  fi
fi

# wemake-python-styleguide — плагин flake8 и требует Python 3.10+: через uv он ставится на 3.14, как в CI;
# сами скрипты по-прежнему держат 3.9.
# Версию плагина flake8 печатает в --version с переносами строк, поэтому сверяем без пробелов и переводов строк.
if ! flake8 --version 2>/dev/null | tr -d ' \n' | grep -q "wemake-python-styleguide:$WPS_VERSION"; then
  if command -v uv >/dev/null 2>&1; then
    uv tool install --quiet --force --python 3.14 flake8 --with "wemake-python-styleguide==$WPS_VERSION"
  elif python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    python3 -m pip install --quiet --user "wemake-python-styleguide==$WPS_VERSION"
  else
    echo "session-start: wemake-python-styleguide нужен Python 3.10+, а найден $(python3 --version 2>&1) — flake8 не поставлен" >&2
  fi
fi

if [ -n "${CLAUDE_ENV_FILE:-}" ] && ! grep -qs 'PYTHONDONTWRITEBYTECODE' "$CLAUDE_ENV_FILE"; then
  echo 'export PYTHONDONTWRITEBYTECODE=1' >> "$CLAUDE_ENV_FILE"
fi
