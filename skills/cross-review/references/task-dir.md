# The run's input — subject, names, files

Everything the finders know about the change reaches them as files this session writes into the run's
`input/`. The scripts detect nothing: they only check that `scope.md` exists and pass the paths on.

## The subject and its diff

Read the subject from the user's words. Run git only to read, never `fetch`, `pull` or `checkout`: a
ref the clone does not have is a question to the user, not a download.

| the user names | the subject | the diff command |
| --- | --- | --- |
| a branch | the branch against its base | `git diff <base>...<branch>` |
| a range, `a..b` | the commits of the range | `git diff a..b`, `git log a..b` |
| a PR or an MR (link, `#123`, `!123`) | its source branch against the target | branch and target by `gh pr view <n> --json headRefName,baseRefName` (GitHub) or `glab mr view <n>` (GitLab), else the forge's MCP or the user; then `git diff <target>...<source>` |
| uncommitted work, «мои правки» | the working tree against `HEAD` | `git diff HEAD`, plus `git status --short` for new files |
| a package or a directory | the files themselves, no diff | `git ls-files <path>` |
| `task/run`, or a finished run | the run as it is | none: go to step 5 |

- **The base of a branch:** the PR's target, else `master` or `main`, whichever exists locally.
- **A PR branch missing locally:** ask the user to fetch it. Never fetch it yourself.
- **Uncommitted work:** write the snapshot time into `scope.md`.
- **The last sha of a committed subject** is the `--ref` of `debate_prompts.py`.

Write the plainest form of every command. `git diff master...HEAD`, never `git -c core.pager=cat diff
…`: an agent's permission layer matches command prefixes, and a leading option can deny the command.
Put a pipe at the end (`git log … | cat`).

## Names

- **Task** — one per subject, kebab-case: the ticket key (`proj-123`), the branch
  (`fix-login-retry`), `pr-123`, or `wip-<date>`. Later runs on the same subject reuse the task.
- **Run** — `review_run.py new` names it: `01-initial` for the first run of a task, then
  `NN-after-fix`. `--run` overrides it only when the user names a run.
- **The debate** lives in `<run_dir>/debate/`, one per run.

## task.md

`new` writes it commented out. Fill it when the task is new: `description`, `url` (the PR link) and
`branch`, `base`. A later session finds the task by `url` and `branch`, so set them exactly. Leave an
existing `task.md` alone.

## scope.md — required, under 1500 characters

What is reviewed and how to see it, not why. Every finder reads it in full, so use bullets and command
blocks, not paragraphs:

1. **What changed** — the branch, the commits, or «uncommitted changes as of <time>»;
2. **The commands to see it** — the diff and log commands above;
3. **Scale** — files and `+/-` lines, so a reviewer trades breadth against depth;
4. **Files to read in full**, one line each on why;
5. **Exclusions** — vendored and generated code, and approaches the user has already ruled out.

## goal.md — optional, under 1000 characters

The intent in the author's terms, then «this is correct only if …» — the falsifiable lines verify
checks against — and the severity bar for this change. Omit the file for a mechanical change; never
write a placeholder.

## profile.md — optional

How this repository calibrates a review: links to its `AGENTS.md` and `CLAUDE.md` and the few
conventions the finders must not flag, in a few lines. Do not copy those files: a link is enough, the
agents read them.

## context/

Files a finder needs and the repository does not hold: the ticket text, a design note, a thread. One
file per source, plain text or markdown. The debate picks `context/` up by itself; anything else goes
through `--context <abs path>=<why>` of `debate_prompts.py`.
