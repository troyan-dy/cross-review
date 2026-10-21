---
description: the code's own stated rules — doc comments and inline notes the change was supposed to obey
---
## Lens: comments

Read the comments in the files the change touches, and judge the change against what they say. This is
the project's guidance at its most local and most binding: a note sitting on the function being edited
was written by whoever last got this wrong.

What earns a finding:

- a doc comment that states a contract the change now breaks — an invariant, an ordering, a "callers
  must", a "never do X here"
- a comment explaining why the code is the way it is, where the change undoes the reason without
  addressing it
- a `TODO`, `FIXME` or "do not" the change walks straight into
- a doc comment left describing the old behaviour after the code beneath it changed. Comments describe
  the current state, so a stale one is a defect in the change rather than a pre-existing one
- a rule stated in one place and silently broken in another the same change touches

What does not:

- a comment you merely disagree with. The bar is that the change contradicts it, not that it is wrong
- a missing comment. Absence is the `docs` lens's business and usually nobody's
- a directive the code deliberately silences with the reason visible — that is a decision, not a defect
- prose style. How a comment is worded is not what this lens is for

Quote the comment and the line that contradicts it. A finding here is one the author can settle by
reading two lines side by side, so give them both — and say which one you think is wrong, because a
contract the change outgrew is fixed by rewriting the comment, not the code.
