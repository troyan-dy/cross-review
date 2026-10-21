---
description: goal fit — whether the change does what it set out to do, is wired up, and is proportionate
---
## Lens: impl

Judge the change against the goal it was given, not against your idea of a better change. Say in one
line what the code actually does, then compare that with what the goal claims it does. A divergence
between the two is a finding on its own, even when the code is internally clean.

Look for:

- a requirement the change covers only partly — one call site updated out of three, an option accepted
  and never read, an error path the goal implies and the code skips
- a fix that removes the symptom while the cause stays in place
- new code nothing reaches: a component never constructed, a branch no caller enters, an entry point
  the goal describes that is not actually connected to it
- a signature or behavior change whose callers were not all brought along
- an approach out of proportion to the problem — layers, indirection or new types where a few lines
  would serve. Name the smaller shape concretely instead of calling it too complex
- work the goal never asked for, bundled in: renames, restructuring and drive-by cleanups in files the
  goal did not require touching

Whether the change belongs in the project at all is usually a question for the author rather than a
defect: raise it as an open question, and say what already-existing feature or simpler path makes you
ask.

When the goal is absent or vague, say so and lower your confidence rather than inventing one to review
against. When a removal in the diff is there only because the branch is behind its base, it is not a
change at all — do not report it.
