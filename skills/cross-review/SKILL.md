---
name: cross-review
description: Use when the user wants two model vendors to review a change and then check each other's findings — «перекрёстное ревью», «пусть Claude и Codex проверят друг друга», «устрой спор / дебаты моделей», «cross-review», «debate review», «повтори обсуждение после правок» — over a branch, range, PR/MR, package, uncommitted work or an existing review run. Claude and Codex review with the same lenses, then cross-examine, answer objections and leave the disputes to a moderator. Not for a single-model review.
argument-hint: "<что ревьюить — ветка | диапазон | PR/MR | пакет | task/run> [standard | expert | <claude-модель>+<codex-модель>] [с независимыми модераторами]"
---

# Cross-review

Claude and Codex review the same change with the same lenses. Synthesis merges what they found and
verify checks it against the code. Then each side examines the other side's findings, the author
answers the objections, and a moderator settles what is still disputed. The result is a list the owner
can fix without re-checking it, with every change of priority shown, and a page with the whole exchange
for every item. It costs 15–40 minutes of review and 15–30 of debate.

Scripts: `S="${CLAUDE_SKILL_DIR}/scripts"`, run as `python3 -B "$S/<script>.py"`. Every script prints
one JSON line, and `{"error": …}` with exit 1 on failure. Python 3.9+, no third-party packages.

**Language.** Everything the user reads is Russian: the findings' title, body and fix, the sides'
arguments, the moderator's verdicts, the summaries, `report.md` and the report page. This file, the
references and the prompts stay English.

## Iron rules

1. **Never touch the code or git.** No commit, add, stash, checkout, fetch or pull — by this session
   or by any agent; `git merge-base --is-ancestor` and `git branch -r --contains` answer without the
   network. Uncommitted work is reviewed as it is: write the snapshot time into `scope.md` and warn in
   the report that edits made during the debate shift line numbers. The one write outside the run is
   `.cross-review/runs/.gitignore` (`*`) of `review_run.py new`; `ignore.ignored: false` in its line means
   git still sees the runs: say so before any agent starts.
2. **Two sides or nothing.** A side that lost agents (`degraded` of `review_collect.py`) neither
   examines nor is examined: stop, say so, offer to rerun when the quota resets. Never debate on one
   vendor. A healthy agent that found nothing is decided as in
   [references/review-stage.md](references/review-stage.md).
3. **A side debates on the model it reviewed with.** The launch plans carry the models
   (`cross_config.py sides`). Never upgrade a side quietly: the point is whether one vendor's reasoning
   survives the other's scrutiny.
4. **Scripts only from `$S`.** Never parse, merge or route answers by hand, and never edit a composed
   prompt. Prompts and lenses change only through the repository's `.cross-review/`
   ([references/config.md](references/config.md)); a run adds `--subject` and `--context`.
5. **The moderator is this session, reading the code.** Open every disputed item yourself. The report
   says «модератор тоже Claude, его вывод отделён от позиций сторон». Independent moderators run only
   when the user asks for them.
6. **Valid JSON before the next step.** An empty or broken answer means the same step again for the
   same agent or side, never a hand edit.
7. **Never repeat a finished run.** Look in `.cross-review/runs/` (its `task.md`): a finished run on
   the same subject goes straight to the debate (step 5).

## Procedure

### Review

**0. Subject and tier.** Find the subject and the diff command by
[references/task-dir.md](references/task-dir.md). Pick the tier from the user's words: nothing → no
flag; «эксперт», «лучшие модели» → `--tier expert` (several times dearer: only on explicit words, and
say so); a named pair → `--claude sonnet:high --codex gpt-6-sol:high`. Run
`"$S/cross_config.py" show <repo> <flags>` and say in one line what runs on which models. A repository
without `.cross-review/` runs on the plugin defaults; mention `/cross-review:cross-review-setup` once.

**1. New run.** `"$S/review_run.py" new <repo> --task <task>` from the repository root. Write
`input/scope.md`, `goal.md`, `profile.md`, `context/` and `task.md` at the printed paths, by
[references/task-dir.md](references/task-dir.md).

**2. Finders.** `"$S/review_run.py" compose <run_dir> <flags>` prints `launch`, one entry per agent.
Start them all at once in the background with the briefs of
[references/review-stage.md](references/review-stage.md): a Claude
entry is an `Agent` on its `alias` and `effort`; a Codex entry is its `command` through Bash with
`timeout: 3600000`, here and in the debate. Tell the
user what runs and for how long (10–30 min), wait for every notification, then
`"$S/review_collect.py" <run_dir>`. Rule 2 on `degraded` and `two_sides`.

**3. Synthesis.** `"$S/review_synth.py" prepare <run_dir>`; unless it prints `skipped`, run one Claude
agent without access to the code on the printed prompt, then `"$S/review_synth.py" apply <run_dir>`.

**4. Verify.** `"$S/review_verify.py" prepare <run_dir>`; unless `skipped`, run one Claude agent per
`launch` group in parallel, then `"$S/review_verify.py" apply <run_dir>`. The run now has
`findings.json` and `report.md`.

### Debate

**5. Split.** `"$S/debate_build.py" <run_dir>` writes `<run_dir>/debate/` (`D` below) and prints
`consensus`, `for_claude`, `for_codex`, `open_questions`. Nothing to examine on either side → step 8.

**6. Prompts and round 1.** `"$S/debate_prompts.py" <run_dir> --subject "<subject in one line>"
[--ref <sha>] [--context <abs path>=<why>]`; add `--workdir <repo>` for an external run. `--ref` is the
last sha of a committed range. Then run the two `r1` entries of `launch` in parallel in the background,
skipping a side with nothing to examine, by [references/protocol.md](references/protocol.md). Keep the
Claude agent's id: round 2 goes to it.

**7. Round 2.** `"$S/debate_round2.py" "$D"`. A non-empty `for-codex-r2.json` → the `r2` codex
`command` (it resumes the session; on exit 3 its `fresh_command`). A non-empty `for-claude-r2.json` →
`SendMessage` to the round-1 agent with the round-2 brief.

**8. Outcome and moderator.** `"$S/debate_outcome.py" <run_dir>`. Non-empty `needs_moderator` or
`side_findings_pending` → follow `$D/prompt-moderator.md`: open the code for every item, write
`moderator.json` and `extra.json` (rule 5). Both empty → no moderator files; the report says «споров к
модерации не осталось» and keeps the note about a Claude moderator, since synthesis and verify are
Claude too. Independent moderators: the `moderate` entries of the launch plan, then
`"$S/debate_moderators.py" "$D"`. Then `debate_outcome.py` again, and with `--markdown` for the summary.

### Report and the next run

**9. Report.** `"$S/debate_report.py" "$D" "$D/report.html" --title "Перекрёстное ревью <subject>"
--subject "<subject>" --strip-prefix <common path prefix>`. The footer names the moderator by the files
in `$D`: the session, or the independent pair with the manifest's models; `--moderator "<text>"`
overrides it. Publish the page with the Artifact tool,
privately, when the session has it; otherwise give its path. Present the result by
[references/present.md](references/present.md) and end on a question: fix, another run after the fixes,
or stop.

**10. After the fixes.** `review_run.py new` on the same task makes `NN-after-fix`; the same tier, then
steps 1–9 over the new range. `compose` adds the task's earlier runs itself.

## Common mistakes

| mistake | instead |
| --- | --- |
| Commit or revert the user's WIP «to pin the lines» | Rule 1: review the tree as it is, with the snapshot time |
| Debate on one side because Codex hit its limit | Rule 2: wait for the reset or stop |
| `claude -p` with home-made flags for a Claude agent | `Agent`; round 2 by `SendMessage` keeps the side's reading of the code |
| `codex exec` by hand, or `resume … -s read-only` | The launch plan's `command`: `codex_run.sh` knows the resume flags |
| Merge, verify or route findings yourself | The stage agent's answer, then `apply` |
| Tune a run's prompt by hand «for the task» | `--subject`/`--context`, or the repository's `.cross-review/` |
| Ask the session's opinion of a dispute without the code | Rule 5: open the code for every disputed item |
| `2>&1 > out.json` around a script or Codex | stdout is the JSON line; `codex_run.sh` keeps its own log |
| Paste earlier findings into a new `scope.md` | `compose` adds earlier runs itself; a copy anchors the agents |
| A bigger model for a side «to be sure» | Rule 3: the side's own model |

## Bundled files

- `assets/config/` — the defaults: `cross-review.json`, `prompts/`, `lenses/`, `schemas/` and
  `LICENSE` ([references/config.md](references/config.md)).
- `scripts/cross_config.py` — `show`, `setup`, `sides`; `scripts/review_run.py`,
  `scripts/review_collect.py`, `scripts/review_synth.py`, `scripts/review_verify.py` — the review run;
  `scripts/codex_run.sh` — one read-only Codex run.
- `scripts/debate_build.py`, `scripts/debate_prompts.py`, `scripts/debate_round2.py`,
  `scripts/debate_moderators.py`, `scripts/debate_outcome.py`, `scripts/debate_report.py` — the
  debate, with `assets/templates/report.html` for the page; `assets/templates/setup-README.md` is the README `setup`
  writes into `.cross-review/`.
