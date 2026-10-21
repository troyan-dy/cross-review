# The debate — files, vocabularies, invocations

## Layout

```text
<run_dir>/                           the review run: read-only for the debate
  input/  stages/  events.jsonl  findings.json  report.md  manifest.json
<run_dir>/debate/                    the debate, written by the scripts
  items.json                         every finding that reached any stage, with vendors and status
  consensus.json                     both sides raised it and the pipeline kept it: not debated
  for-claude.json  for-codex.json    round 1 inputs: the other side's findings and open questions
  prompt-r1-*.md  prompt-r2-*.md     the sides' prompts; prompt-r2-codex.fresh.md for a lost session
  prompt-moderator*.md               the session's, the independent Claude's and Codex's moderator
  schema-examine.json  schema-respond.json  schema-moderate.json
  claude-r1.json  codex-r1.json      round 1 verdicts
  for-claude-r2.json  for-codex-r2.json  round 2 inputs: finding and objection pairs
  claude-r2.json  codex-r2.json      round 2 responses and side findings
  codex-r*.log  codex-r*.exit        Codex transcripts and exit codes; the r1 log holds the session id
  disputes.json                      what still awaits the moderator
  moderator.json  extra.json         the decisions, and the side findings the moderator confirmed
  final.json  report.html            every item with its outcome and thread; the page
```

An external run — a directory of the same layout outside `.cross-review/runs/`, with no `manifest.json` —
is read as is up to `debate/`.

## Vocabularies

**Pipeline `status`** (`items.json`): `confirmed`, `refined`, `immaterial`, `pre_existing`,
`rejected_by_verify`, `dropped_by_synthesis`, `unverified`, `open_question`.

**Round 1 `stance`** — the examiner about the other side's finding: `real`, `real_but_different`,
`not_real`, `not_worth_fixing`. `severity` is the examiner's own: `critical`, `major`, `minor`, `none`.
An objection is any verdict other than `real` at the finding's own severity.

**Round 2 `position`** — the author answering an objection: `concede`, `defend`, `amend`. A `defend`
must bring new evidence; a restatement is no defense, and the moderator treats it as none.

**The `amend` rule.** The author's answer settles an objection only at the examiner's severity.
`concede` takes the examiner's severity; `amend` at the examiner's severity is `refined`. An `amend` at
any other severity is no agreement: the item is `disputed` and goes to the moderator with both positions.
An author never lowers a severity quietly.

## Outcomes

`final.json` gives every item one outcome:

| outcome | meaning | to fix |
| --- | --- | --- |
| `consensus` | both sides raised it, the pipeline kept it | yes |
| `confirmed` | one side raised it, the other agreed at the same severity | yes |
| `restored` | the pipeline set it aside, the other side says it is real | yes |
| `refined` | the severity or the claim changed and both sides accept it | yes, at `final_severity` |
| `disputed` | the sides disagree; `moderated: true` when the moderator decided | only once decided, at the moderator's severity |
| `side` | found during the debate, confirmed by the moderator | yes |
| `dropped` | the author conceded, or both agree on `none` | no |
| `question` | an open question with the sides' answers | no: the author decides |
| `unexamined` | no verdict arrived: a side did not finish; an item both sides examine needs both verdicts | unknown: say so |

A `pre_existing` item keeps its outcome but is never to fix: the counts, `actionable` and the page's «Чинить»
leave it out, and the summary lists it under «Было до изменения».

`moderators_split: true` marks a dispute two independent moderators split on, or one that only one of them
answered: it stays `disputed` at the pipeline severity, and the report says a human decides.

An item both sides examine is one both raised and the pipeline set aside, or one whose source is outside the
roster — `debate_build.py` keeps such a source name in `vendors`, and neither side is its author. Without both
verdicts it is `unexamined`; two `real` verdicts confirm it at the stricter severity.

## Side findings and the moderator's verdict

A side finding the moderator did not put in `extra.json` is rejected once `extra.json` exists: the JSON
line lists it under `side_findings_rejected`, not `side_findings_pending`. An `extra.json` entry whose id no side
raised in round 2 is an `{"error": …}`: rerun the moderator. The moderator only confirms a side finding and sets its
severity: `file`, `line`, `title`, `body` and `fix` in `final.json` come from the side's round-2 answer, whatever the
`extra.json` entry says. Two moderators that confirm a side finding at different severities give it the stricter one.

**Moderator `verdict`** — short Russian: `прав Claude`, `прав Codex`, `оба частично правы: …`, `снять`.
`severity: none` drops the finding.

## severity_shift

Every item of `final.json` carries `severity_shift {from, to, direction, by}`:

- `from` — the severity after synthesis and verify: `none` for an item the pipeline set aside
  (`dropped_by_synthesis`, `rejected_by_verify`, `immaterial`), `null` for a side finding. A set-aside item
  stays at `none` until the debate restores it, so restoring it is `raised` and agreeing to leave it out is `same`;
- `to` — `final_severity`;
- `direction` — `raised`, `lowered`, `same`, `dropped` (to `none`) or `new` (a side finding);
- `by` — who set the final severity: `side` (the examiner), `author` (an `amend` the examiner shares),
  `moderator`, or `none` when it did not change.

The JSON line of `debate_outcome.py` lists the ids under `reprioritized {raised, lowered, dropped}`;
`--markdown` has the section «Переоценка приоритетов», and the page puts a «было → стало» badge on each.

## The sides

`debate_prompts.py` prints `launch`: `r1` and `r2` for each side, then the three `moderate` entries.
Each entry names its prompt, output and model; a Codex entry also its schema and `command`.

`debate_round2.py`, `debate_moderators.py` and `debate_outcome.py` check every answer against its schema
(`moderator*.json` and `extra*.json` are arrays of the `decisions` and `extra` entries). An answer that is
not JSON or breaks the schema is an `{"error": …}` naming the file: rerun that step.

## The Claude side

**Round 1** — `Agent`, `subagent_type: general-purpose`, `model:` the entry's `alias`,
`effort:` its `effort`, `run_in_background: true`. Keep the agent id the call returns:

> Read `<prompt>` and follow it exactly. You are read-only on the repository: no edits, no git state
> changes, no network, no tests. The one file you create is `<output>`, as the prompt says. Arguments
> in Russian. A second message with objections to the Claude side's findings follows later: keep your
> reading of the code in mind.

**Round 2** — `SendMessage` to that agent id:

> Round 2: read `<prompt>` (the `r2` claude entry) and follow it exactly. The objections are in
> `<debate_dir>/for-claude-r2.json`. The same read-only rules; the one file you create is `<output>`.
> Arguments in Russian.

**Joining at round 2** — when Claude skipped round 1, or its agent is gone: a fresh `Agent`:

> You are the Claude side of a two-vendor review debate and you join at round 2. Read
> `<debate_dir>/prompt-r1-claude.md` for its context section only — what is under review, what to
> read, how to read the code — and produce no round-1 verdicts. Then read `<prompt>` and follow it
> exactly: the objections are in `<debate_dir>/for-claude-r2.json`. Read-only on the repository; the
> one file you create is `<output>`. Arguments in Russian.

## The Codex side

Run the entry's `command` through Bash, `run_in_background: true`, `timeout: 3600000`: without it the
background command is stopped after 10 minutes, before Codex answers. The `r2` command resumes the
round-1 session from `codex-r1.log`. Exit 3 means the log is missing (the side skipped round 1) or
names no session: run `fresh_command`, which starts from `prompt-r2-codex.fresh.md`. A usage limit
shows as `warning` in the script's line and a missing answer: wait for the reset or stop (iron rule 2).

Check every answer with `python3 -c 'import json, sys; json.load(open(sys.argv[1]))' <file>` before the
next step. A broken one means the same step again for that side.

## The moderator

- **By default the session** follows `prompt-moderator.md` over `disputes.json`, reading the code for
  every item, and writes `moderator.json` and `extra.json`.
- **Independent moderators**, only when the user asks: the `moderate` claude entry as an `Agent` on its
  `alias` and `effort` with `prompt-moderator-claude.md`, and the `moderate` codex entry's `command`, in
  parallel. Then `debate_moderators.py <debate_dir>`: a dispute is decided when both agree whether the
  finding stands, the stricter severity wins; a split, or a dispute only one of them answered, stays a
  dispute; a side finding counts only when both confirm it. The models come from `tiers.<tier>.moderators`.

Then run `debate_outcome.py` again: the outcomes update and `final.json` is complete.
