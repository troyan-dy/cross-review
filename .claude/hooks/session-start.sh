#!/bin/bash
# SessionStart-хук облачной сессии Claude Code: готовит окружение для тестов и ruff.
# Скриптам скила нужен только python3 3.9+ — ставим лишь ruff той же версии, что в CI.
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

RUFF_VERSION="0.17.2"

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

if [ -n "${CLAUDE_ENV_FILE:-}" ] && ! grep -qs 'PYTHONDONTWRITEBYTECODE' "$CLAUDE_ENV_FILE"; then
  echo 'export PYTHONDONTWRITEBYTECODE=1' >> "$CLAUDE_ENV_FILE"
fi
