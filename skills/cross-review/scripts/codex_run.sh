#!/usr/bin/env bash
# codex_run.sh — один запуск Codex только на чтение: агент находок, верификатор или сторона спора.
# Справка — в usage() ниже; тесты — tests/test_codex_run.py.

set -u

usage() {
    cat <<'EOF'
usage: codex_run.sh <workdir> <prompt> <schema> <out> <model> <effort> [--resume <log>]

Run `codex exec` read-only in <workdir> on the prompt file, the answer held to <schema>. Writes the answer
to <out>, everything Codex printed to <out minus .json>.log and its exit code to <out minus .json>.exit.
Blocks for the whole run, often 10-30 minutes: start it in the background and wait for the notification.

  effort      low|medium|high|xhigh|max
  --resume    continue the session whose id <log> names, with the code Codex already read; exit 3 and
              no run when <log> is missing or names no session — run it fresh with a prompt that carries
              the context

The user's ~/.codex/config.toml is ignored (plugins, MCP servers and hooks the run does not need);
CROSS_REVIEW_CODEX_USER_CONFIG=1 keeps it. Prints one JSON line: exit, model, session_id, resumed, answer
(whether <out> holds JSON) and a warning when a failed run's log names a usage limit. Exits with Codex's code.
EOF
}

case "${1:-}" in -h | --help) usage; exit 0 ;; esac
if [ $# -ne 6 ] && { [ $# -ne 8 ] || [ "$7" != --resume ]; }; then
    usage >&2; exit 2
fi
case "$6" in low | medium | high | xhigh | max) ;; *) echo "codex_run.sh: effort '$6', expected low|medium|high|xhigh|max" >&2; usage >&2; exit 2 ;; esac

# пути становятся абсолютными до cd в рабочий каталог
abs() { case "$1" in /*) printf '%s' "$1" ;; *) printf '%s/%s' "$PWD" "$1" ;; esac; }
W=$(abs "$1"); prompt=$(abs "$2"); schema=$(abs "$3"); out=$(abs "$4"); model=$5; effort=$6
resume=""; [ $# -eq 8 ] && resume=$(abs "$8")
# нет лога resume — не ошибка вызова: сторона пропустила раунд 1, это выход 3 ниже
for f in "$prompt" "$schema"; do
    [ -f "$f" ] || { echo "codex_run.sh: no such file: $f" >&2; exit 2; }
done
[ -d "$W" ] || { echo "codex_run.sh: no such directory: $W" >&2; exit 2; }
mkdir -p "$(dirname "$out")"

base=${out%.json}
log="$base.log"; exitf="$base.exit"; strict="$base.schema.json"
rm -f "$out" "$exitf"

# --output-schema у Codex строгий: каждое поле объекта должно быть в required, а $schema не принимается.
# Строгая копия лежит рядом с ответом, исходная схема плагина остаётся контрактом скриптов.
python3 -I - "$schema" "$strict" <<'PY' || exit 2
import json, sys

def strict(node):
    if isinstance(node, dict):
        node.pop("$schema", None)
        props = node.get("properties")
        if isinstance(props, dict):
            node["required"] = list(props)
            node["additionalProperties"] = False
            for sub in props.values():
                strict(sub)
        if "items" in node:
            strict(node["items"])
    elif isinstance(node, list):
        for sub in node:
            strict(sub)
    return node

with open(sys.argv[1]) as f:
    data = json.load(f)
with open(sys.argv[2], "w") as f:
    json.dump(strict(data), f, ensure_ascii=False, indent=2)
PY

# пустой массив под set -u в bash 3.2 (macOS) раскрывается только через ${cfg[@]+...}
cfg=(--ignore-user-config); [ "${CROSS_REVIEW_CODEX_USER_CONFIG:-0}" = 1 ] && cfg=()
common=(-m "$model" -c "model_reasoning_effort=\"$effort\"" --output-schema "$strict" -o "$out")
cd "$W" || exit 2

resumed=false
if [ -n "$resume" ]; then
    sid=$(grep -m1 -i 'session id' "$resume" 2>/dev/null | grep -oE '[0-9a-f]{8}-[0-9a-f-]{27}' | head -n1)
    if [ -z "$sid" ]; then
        python3 -I -c 'import json, sys; print(json.dumps({"exit": 3, "resumed": False,
    "error": f"no session id in {sys.argv[1]} (missing or empty log): run it fresh"}))' "$resume"
        exit 3
    fi
    resumed=true
    # resume не знает -s: режим песочницы задаётся только через конфиг
    codex exec ${cfg[@]+"${cfg[@]}"} resume "$sid" -c 'sandbox_mode="read-only"' --skip-git-repo-check \
        "${common[@]}" - < "$prompt" > "$log" 2>&1
else
    codex exec ${cfg[@]+"${cfg[@]}"} -C "$W" -s read-only --skip-git-repo-check \
        "${common[@]}" - < "$prompt" > "$log" 2>&1
fi
code=$?
echo "$code" > "$exitf"

python3 -I - "$out" "$log" "$code" "$resumed" "$model" <<'PY'
import json, sys

out, log, code, resumed, model = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4] == "true", sys.argv[5]
res = {"exit": code, "model": model, "session_id": None, "resumed": resumed, "answer": False,
       "output": out, "log": log}
try:
    with open(log, errors="replace") as f:
        text = f.read()
except OSError:
    text = ""
for line in text.splitlines()[:40]:
    key, _, value = line.partition(":")
    if key.strip().lower() == "model" and value.strip():
        res["model"] = value.strip()
    elif key.strip().lower() == "session id" and value.strip():
        res["session_id"] = value.strip()
try:
    with open(out) as f:
        json.load(f)
    res["answer"] = True
except (OSError, ValueError):
    pass
# тело лога цитирует прочитанный код, поэтому лимит ищем только в хвосте неудачного запуска
if code != 0 or not res["answer"]:
    tail = text[-2048:].lower()
    for marker in ("usage limit", "rate limit", "quota", "429", "too many requests"):
        if marker in tail:
            res["warning"] = f"log tail mentions {marker!r}: Codex may have hit its usage limit"
            break
print(json.dumps(res, ensure_ascii=False))
PY
exit "$code"
