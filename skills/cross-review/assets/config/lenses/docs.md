---
description: documentation accuracy — doc comments against the code, and the project docs the change leaves stale
---
## Lens: docs

Check what the documentation claims against what the code does. A wrong document is worse than a
missing one, so accuracy comes before completeness — that orders what to look for, not how bad the
result is. Severity comes from the bar above and nowhere else: prose executes nothing, so a wrong
claim in it is minor however wrong it is, unless a machine or an agent executes against the document
as a contract.

In the code:

- comment sparingly: a comment must state a constraint, gotcha or non-obvious reason; never narrate
  what the code already shows
- an exported item, or an interface whose contract callers depend on, with nothing explaining it
- a comment naming a parameter, return value or failure condition the code does not have
- a comment describing behavior the code no longer has, or referring to a name that no longer exists
- a precondition, side effect, concurrency requirement or cancellation behavior a caller has to know
  and cannot see from the signature
- hedging language — might, could, probably — where the code is definite

In the project's own documents:

- a new flag, command, option, endpoint or user-visible behavior nothing tells a user about
- an example that would no longer work as written
- a design or plan document the change contradicts, or completes without recording it

Skip prose taste, obvious accessors, generated files and test code. Documentation aimed at the
maintainer's own tooling is his to keep current; when the author of the change is not the maintainer,
leave those files out of the review entirely — the project profile says which case this is.
