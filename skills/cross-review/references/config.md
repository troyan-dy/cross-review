# Configuration

A cross-review reads its settings from two layers. The plugin layer is `assets/config/` inside the skill and
holds every default. The reviewed repository may add `.cross-review/` at its root; whatever it holds
overrides the plugin. `scripts/cross_config.py` resolves both and is the only reader of either.

## Layout

```text
assets/config/                   # plugin layer
├── cross-review.json            # tiers of models, the roster, the stages
├── prompts/<name>.md            # review, synthesis, verify; examine, respond, moderate for the debate
├── lenses/<name>.md             # bugs, impl, architecture, quality, docs, tests, comments, adversarial
├── schemas/<name>.json          # finder, synthesis, verify
└── LICENSE                      # MIT notice of the review, synthesis and verify prompts, lenses, schemas

<repo>/.cross-review/            # repository layer, committed by the team
├── README.md                    # for the owner, written by `setup`
├── cross-review.json            # partial: only the keys it changes
├── prompts/<name>.md            # replaces the plugin prompt of that name
├── lenses/<name>.md             # replaces a plugin lens or adds a new one
├── defaults/                    # a copy of the plugin layer, refreshed by `setup`; no script reads it
└── runs/<task>/<run>/           # review runs, never committed: `review_run.py new` writes runs/.gitignore
```

## cross-review.json

```json
{
  "tier": "standard",
  "tiers": {
    "standard": {
      "claude": "opus:high",
      "codex": "gpt-6.1-sol:high",
      "moderators": { "claude": "fable:high", "codex": "gpt-6-astra:high" }
    }
  },
  "roster": [
    { "name": "bugs", "lenses": ["bugs", "impl"] },
    { "name": "arch", "lenses": ["architecture", "quality"] }
  ],
  "stages": { "synthesis": true, "verify": true, "verify_groups": 6, "verify_group_by": "dir" }
}
```

- `tier` — the tier a run uses when the user names none.
- `tiers.<name>` — the claude and codex models of the finders and of both debate sides, and the two
  independent moderators. Synthesis and verify run on the tier's claude model.
- `roster` — every entry becomes two agents, `<name>-claude` and `<name>-codex`, on the same lenses.
  The name `verify` is reserved: the verifiers' answers are `agents/verify-<group>.json`.
- `stages` — `synthesis` and `verify` switch a stage off; `verify_groups` caps the parallel verifiers;
  `verify_group_by` groups findings by `dir` or by `source`.

A model is `<model>[:<effort>]`. Effort is one of `low|medium|high|xhigh|max` and defaults to `high`. A
claude model is an `Agent` alias: `opus|sonnet|fable|haiku`.

The user's words pick the models: nothing → the configured `tier`; «эксперт», «лучшие модели» →
`expert`; a named pair («sonnet против gpt-6-sol») → a one-off `--claude`/`--codex` pair. With a pair the
moderators still come from the configured tier.

## Merge rules

- Objects merge key by key, recursively: `{"tiers": {"standard": {"codex": "gpt-6-sol"}}}` changes one
  model and keeps the rest of the tier.
- Arrays are replaced whole: a repository `roster` is the whole roster.
- An unknown key, a wrong type, an unknown tier, a bad model or effort, an empty roster, a lens neither
  layer has, and a repository prompt with no plugin prompt of that name are errors. The error names the
  file and the key, and nothing runs.

## Prompts and lenses

`resolve(repo, kind, name)` takes `<repo>/.cross-review/<kind>/<name>.md` first and the plugin file
second. A repository lens with a new name is usable in its roster. Schemas come from the plugin only:
the scripts read every answer by that shape, so a repository cannot change it.

Each prompt has a fixed set of placeholders it may use. Any other `{{NAME}}` is an error that names the
prompt and the placeholder. It surfaces when the stage renders the text, not at `show`: a debate prompt
fails only after the review is done.

| prompt | placeholders |
| --- | --- |
| `review` | `{{SCOPE}} {{GOAL}} {{PROFILE}} {{CONTEXT}} {{WORKDIR}}` — paths |
| `synthesis` | `{{SOURCES}} {{FINDINGS}}` |
| `verify` | `{{SCOPE}} {{PROFILE}} {{WORKDIR}} {{FINDINGS}}` |
| `examine`, `respond` | `{{SIDE}} {{OTHER}} {{SUBJECT}} {{WORKDIR}} {{PANEL}} {{CONTEXT}} {{TREE_NOTE}} {{DEBATE_DIR}} {{INPUT}} {{OUTPUT}} {{STATUS_LEGEND}}` |
| `moderate` | the same without `{{SIDE}} {{OTHER}}` |
| a lens | the same as `review`: a literal `{{word}}` in a lens is an error |

The finder prompt is the `review` body, then a `## Lens: <name>` section per lens, then the prior runs
of the task, then the finder schema. A lens file is that section: it starts with `## Lens: <name>`.

Change a prompt or a lens through `.cross-review/`, never by editing a run's composed prompt by hand.

## Debate prompts

`examine` is round 1 of the debate, `respond` round 2, `moderate` the moderator. `debate_prompts.py`
builds their values from the run:

- `{{SIDE}}`, `{{OTHER}}` — `Claude` or `Codex`: the side the prompt is for and the other one.
- `{{SUBJECT}}` — the one line `--subject` names; `{{WORKDIR}}` — the repository root the sides run in.
- `{{PANEL}}` — who reviewed, from the run's `findings.json`: «Four reviewers — two Claude (…) and two
  Codex (…) —».
- `{{CONTEXT}}` — a list of context paths, each with why to read it; `{{TREE_NOTE}}` — where the working
  tree stands against `--ref` and how to read the code when it lacks that commit.
- `{{DEBATE_DIR}}` — the debate directory; `{{INPUT}}` — the file the prompt works on: `for-<side>.json`,
  `for-<side>-r2.json` or `disputes.json`.
- `{{OUTPUT}}` — how to answer: a Claude side writes its file with the Write tool, a Codex side returns
  JSON held to the schema.
- `{{STATUS_LEGEND}}` — what each pipeline status of a finding means.

`prompt-r2-codex.fresh.md` is `examine` for Codex with a note on its round-1 verdicts in place of
`{{OUTPUT}}`, a `---` line, then `respond`: a Codex round 2 that cannot resume its session starts from it.

The debate runs on the models of the review. The launch plan of `debate_prompts.py` takes them from `sides`
(below): the sides from the run's `manifest.json`, or from `findings.json` of an external run, and the two
independent moderators from `tiers.<tier>.moderators`.

## Commands

```sh
python3 -B "${CLAUDE_SKILL_DIR}/scripts/cross_config.py" show <repo> [--tier T | --claude M --codex M]
python3 -B "${CLAUDE_SKILL_DIR}/scripts/cross_config.py" setup <repo>
python3 -B "${CLAUDE_SKILL_DIR}/scripts/cross_config.py" sides <run_dir> [--repo R]
```

- `show` prints one JSON line: the layers applied, the agents with model and effort, the stage model,
  the moderators, and the path and layer of every prompt, lens and schema. Exit 1 with `{"error": …}` on
  a configuration error, 2 on wrong arguments.
- `setup` prepares `.cross-review/` for its owner: `cross-review.json` with `tier` only and `README.md`,
  each written only when missing, and `defaults/` — the plugin's `cross-review.json`, prompts, lenses and
  `LICENSE` — refreshed on every call, files the plugin no longer has removed. It prints `created`, `kept`
  and `defaults_changed` (`-` marks a removed file). No script reads `defaults/`: only a file copied from
  it into `prompts/` or `lenses/` overrides the plugin. The `cross-review-setup` skill runs it.
- `sides` prints the models of the debate as one JSON line: `claude {alias, effort}`, `codex {model,
  effort}` and the same pair under `moderators`. A cross-review run reads them from its `manifest.json`. An
  external run (no `manifest.json`) reads the sides from `findings.json` `sources.agents[]` — the model most healthy agents of
  a vendor ran on, a claude model id turned into its `Agent` alias (`claude-opus-5-5` → `opus`) — and the
  moderators from the configured tier of `--repo` (default: the run's workdir, else the current
  directory). A model without an alias, a round without one of the vendors or a malformed manifest is
  exit 1 with `{"error": …}`.

## Example

A repository that runs a cheaper codex model, adds a performance lens and words the review its own way:

```text
.cross-review/
├── cross-review.json
├── prompts/review.md
└── lenses/perf.md
```

```json
{
  "tiers": { "standard": { "codex": "gpt-6-sol:medium" } },
  "roster": [
    { "name": "bugs", "lenses": ["bugs", "impl"] },
    { "name": "perf", "lenses": ["perf", "quality"] }
  ]
}
```

`lenses/perf.md` is a new lens: an optional front matter with its `description`, which the scripts
strip, then its section heading and what to look for and what to leave alone:

```markdown
---
description: performance regressions on hot paths
---
## Lens: perf

Look for work the change adds to a hot path: a query per item of a loop, a lock held across I/O,
an unbounded cache. Leave micro-optimisations of cold code alone.
```

`prompts/review.md` replaces the plugin's review prompt whole. Start from a copy of the plugin's
`assets/config/prompts/review.md` and keep its placeholders: one the table above does not list is an error.
`cross_config.py show <repo>` then names `repo` as the layer of `review`, `perf` and the codex model.

## License

The review, synthesis and verify prompts, the lenses and the schemas are distributed under the MIT
license; its text and copyright notice are in `assets/config/LICENSE`. The review prompt adds the language rule
for the answer; the synthesis and verify prompts add the same rule for the text they write. The debate
prompts `examine`, `respond` and `moderate` are this plugin's own.
