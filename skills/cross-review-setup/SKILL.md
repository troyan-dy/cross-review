---
name: cross-review-setup
description: "Use when the user wants to set up or tune cross-review for a repository — «настрой перекрёстное ревью», «cross-review-setup», «добавь настройки cross-review в проект», «какие модели и промты использует cross-review», «обнови дефолты cross-review». Writes .cross-review/ with a minimal cross-review.json, a README and defaults/ — the plugin's models, prompts and lenses as a reference the owner copies from — and never overwrites the owner's files. Not for running a review, which is cross-review:cross-review."
argument-hint: "[путь к репозиторию]"
---

# Cross-review setup

Prepares a repository's `.cross-review/` so its owner can see what cross-review runs and change it. The
config script is the review skill's: `S="${CLAUDE_SKILL_DIR}/../cross-review/scripts"`, run as
`python3 -B "$S/cross_config.py" …`. It prints one JSON line, and `{"error": …}` with exit 1 on failure.

**Language.** Everything the user reads is Russian.

## What setup writes

| path | written | what it is |
| --- | --- | --- |
| `cross-review.json` | only when missing | the active config: `{"tier": "standard"}`, the rest comes from the plugin |
| `README.md` | only when missing | for the owner, in Russian: what each file does and how to override it |
| `defaults/` | on every run | the plugin's `cross-review.json`, `prompts/`, `lenses/` and `LICENSE`; no script reads it |

A file in `prompts/` or `lenses/` replaces the plugin's file of that name; an override key in
`cross-review.json` replaces that key. Everything the owner leaves alone keeps following the plugin, so
setup never copies `defaults/` into the active places by itself.

## Procedure

1. **Repository.** The argument, else `git rev-parse --show-toplevel` of the current directory. No git
   repository → ask for the path.
2. **Setup.** `"$S/cross_config.py" setup <repo>`. It reports `created`, `kept` (the owner's files left as
   they were) and `defaults_changed` (a `-` prefix is a file the plugin no longer has).
3. **Check.** `"$S/cross_config.py" show <repo>`: a configuration error here is the owner's file — show the
   error and the file, change nothing.
4. **Requested changes.** When the user named one in the same request, make only that:
   - a model or tier → the smallest key in `cross-review.json` (the shape is in `defaults/cross-review.json`;
     objects merge, arrays such as `roster` replace whole);
   - a prompt or lens → copy that one file from `defaults/` into `prompts/` or `lenses/`, then edit the
     copy, keeping every `{{…}}` placeholder;
   - then `show` again.
5. **Report** in a few lines: what was created or kept; on a repeat run, what changed in `defaults/` and
   that `git diff .cross-review/defaults/` shows it; which agents run on which models and which prompts
   and lenses come from the repository; that `.cross-review/` (without `runs/`) is meant to be committed
   by the team, and `README.md` there says how to tune it.

## Rules

- Never commit, stage or push; the owner commits `.cross-review/`.
- Never overwrite or "refresh" the owner's `cross-review.json`, `README.md`, `prompts/` or `lenses/`.
- Never copy a file out of `defaults/` the user did not ask to change: a copy stops following the plugin.
- `runs/` is the review skill's; leave it alone.
