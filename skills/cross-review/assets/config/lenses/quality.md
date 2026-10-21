---
description: style, over-engineering, error handling and accidental duplication in code that already works
---
## Lens: quality

Report code that runs correctly and could be written better. Judge it bottom-up, on what is in front of
you, rather than against the change's goal.

Look for:

- constructs the language treats as non-idiomatic: a pointer to something already a reference, an
  untyped catch-all where a concrete type fits, a boolean argument nobody can read at the call site
- structure that fights the reader: a function past what one screen holds, nesting deeper than three
  levels where an early return would flatten it, a branch after a return that could not be taken
- abstraction with nothing behind it: a wrapper that only forwards, a factory serving one
  implementation, a layer that passes its argument through untouched, a settings object for two options
- generality for a case that does not exist — an extension point nothing extends, a fallback that
  cannot trigger, a second implementation kept beside the one actually used
- errors discarded into a blank identifier with no reason given, an empty failure branch, a default
  value returned after a failure without a word about it
- an error returned bare where the caller cannot tell which operation failed, or wrapped so the
  original can no longer be inspected
- a failure raised inside a goroutine that nothing outside it can observe
- duplication the change introduces: a block pasted with one value changed, or logic the repository
  already provides. Cite the existing copy so the finding is actionable

Duplication is often deliberate and right. Two occurrences are not yet a pattern, tests repeat their
setup to stay readable, and repetition that keeps two modules from depending on each other is buying
something real. Flag it only when you can name the shared form and show it costs nothing.

Leave alone: an intentional discard that says why, a stateless type grouping methods, generated files,
and test code written plainly on purpose.
