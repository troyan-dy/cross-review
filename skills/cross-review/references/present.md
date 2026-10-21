# Presenting the result

Read this when `final.json` is complete and the page is rendered. The turn is in Russian.

**The turn is a decision, not the record.** The record exists already: `findings.json`, `report.md`,
`final.json` and the report page. Repeating it in chat buries the decision. A finding's body appears in
full only where the user acts on it, and only once.

## The skeleton

1. **Subject** — one to three sentences: what was reviewed, on which models, and the snapshot time
   for uncommitted work, with the warning that edits since then shift the lines.
2. **Completeness** — agents `expected` against `reported`; any degraded agent first; any healthy
   agent that found nothing named; `unexamined` items named with the side that did not finish.
3. **Counts** — one line by outcome: «консенсус N · подтверждено N · восстановлено N · уточнено N ·
   спор N · найдено в споре N · снято N», then by final severity.
4. **To fix** — every item with an actionable outcome, by final severity, each headed
   `[outcome, severity] Title — file:line`, then the mechanism (what triggers it and what breaks) and
   the fix. Mark a disputed item with who decided it.
5. **Reprioritization** — one line per item of `reprioritized`: «Title: было major → стало minor
   (кто: сторона / автор / модератор)». Say plainly when nothing changed.
6. **Set aside** — `dropped` items one line each with the reason; open questions with the sides'
   answers; `pre_existing` and `immaterial` one line each, outside the counts.
7. **Moderator note** — «модератор тоже Claude, его вывод отделён от позиций сторон», or which two
   independent models moderated and where they split; or «споров к модерации не осталось».
8. **The page** — the Artifact link, or the path of `report.html`.

## The question

End on the harness's question tool and put nothing after it. The question opens with the verdict in
one sentence. The recommended option goes first, marked, with its reason inside it. An option names
what it acts on, never a count: «починить подтверждение до обработки в `worker.py` и гонку в
`pool.py`», not «починить два major». The usual options:

- fix the items to fix, then another run after the fixes;
- fix only some of them, named;
- stop here.
