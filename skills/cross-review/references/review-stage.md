# The review stages — finders, synthesis, verify

A review run is a roster of finders on both vendors, synthesis that merges what they found, verify that
judges each finding against the code. Every stage is a script that writes the prompts and prints a launch
plan, the agents this session starts, and a script that reads their answers.
The session never reads an answer into a stage itself.

## Finders

`review_run.py compose` prints `launch`: one entry per roster agent, `<name>-claude` and `<name>-codex`
on the same lenses. Start every entry in one message, all in the background.

A **Claude entry** is an `Agent` call: `subagent_type: general-purpose`, `model:` its `alias`,
`effort:` its `effort`, `run_in_background: true`, and this brief with the entry's paths:

> Read `<prompt>` and follow it exactly. You are read-only on the repository: no edits, no git state
> changes, no network, no tests. Write your answer — the JSON object the prompt's schema describes,
> nothing else — to `<output>` with the Write tool. That is the only file you create. Reply with one
> line: how many findings.

A **Codex entry** carries its `command`: run it through Bash with `run_in_background: true` and
`timeout: 3600000` — the default stops a background command after 10 minutes, and Codex often runs
longer. It is
`codex_run.sh`, which keeps Codex read-only and writes `<output>`, its `.log` and its `.exit` beside it.
Never wrap it in a redirect.

Tell the user in three lines what runs on which models, where the logs are (`<run_dir>/agents/`), and
that it takes 10–30 minutes. Wait for every notification; never poll the files.

## Collect and degradation

`review_collect.py <run_dir>` reads every answer into `stages/1-found.json` and prints `expected`,
`reported`, `degraded` with a reason per agent, and `two_sides`.

- **An agent is degraded** when its answer is missing, is not JSON, breaks the schema, or its `.exit`
  is non-zero. A usage limit in its log is named in the reason.
- **Rerun the same entry once** when the reason is not a usage limit, then collect again.
- **A usage limit, or `two_sides: false`,** stops the review: iron rule 2.
- **`{"findings": []}` is a healthy answer.** Decide whether the agent looked:
  - a Codex agent's `agents/<agent>.log` holds what Codex printed — commands run and files read there
    mean it worked and honestly found nothing;
  - a Claude agent's notification shows its tool calls and duration — a few seconds without tool calls
    means it never looked: rerun it.

## Synthesis

`review_synth.py prepare <run_dir>` prints `skipped` when the stage is off or nothing needs merging.
Otherwise it prints `prompt`, `output`, `alias` and `effort`: start one `Agent` on them with this brief:

> Read `<prompt>` and follow it exactly. Work only from the findings in that prompt: do not open the
> repository or any other file. Write your answer — the JSON object of the prompt's schema, nothing else —
> to `<output>` with the Write tool, the only file you create. Reply with one line: how many findings.

Then `review_synth.py apply <run_dir>`. The model decides which findings are one problem and how to word
it; the script applies the rules — boosted confidence, the highest severity, weak singletons dropped
unless the round is degraded — and logs `dropped` and `restored` events in `events.jsonl`.

## Verify

`review_verify.py prepare <run_dir>` groups the findings by directory (or by source) and prints `alias`,
`effort` and `launch`, one entry per group. Start one `Agent` per entry in one message, in the background:

> Read `<prompt>` and follow it exactly. You are read-only on the repository: read the code, change
> nothing, no git state changes, no network, no tests. Write your answer — the JSON object of the
> prompt's schema, nothing else — to `<output>` with the Write tool, the only file you create. Reply
> with one line: how many verdicts of each kind.

Then `review_verify.py apply <run_dir>`. `confirmed` and `refined` stay, `rejected` leaves the report,
`immaterial` and `pre_existing` move to their lists, a finding without a verdict stays `unverified`. A
group without a readable answer is degraded: rerun that group's agent once, then apply again.

## findings.json

The run's result, in the run format, so the debate also reads an external run of the same layout:

- `scope` — what the run reviewed;
- `sources` — `expected`, `reported`, `degraded` and `agents[]`: name, lenses, executor, models, effort,
  `raised`, `degraded`;
- `findings[]` — `id`, `file`, `line`, `end_line`, `severity`, `confidence`, `title`, `body`, `fix`,
  `sources`, `lenses`, and `merged_ids` when synthesis merged several;
- `open_questions`, `pre_existing`, `immaterial` — their own lists, outside the counts;
- `stats` — start, finish, duration and the stages that ran.

`report.md` beside it is the same in Russian for a reader. The stage files `stages/1-found.json`,
`2-synthesized.json` and `3-verified.json` keep each step for the debate's `items.json`.
