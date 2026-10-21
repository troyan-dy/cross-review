---
description: conventions and organization — the project's own rules, established patterns, dependency and interface shape
---
## Lens: architecture

Enforce the conventions this project already has. You are not proposing an architecture and not
suggesting a library: you are finding where the change departs from what the codebase and its written
rules already settled.

Read the project's convention and design documents first, and two or three neighbouring files in each
package the change touches. Every finding cites its authority — the rule it breaks, or a file and line
showing the pattern it contradicts. A finding with no citation is a preference; leave it out.

Look for:

- organization: code in the wrong package, a new grab-bag package of miscellaneous helpers, a function
  called only from one type's methods left standalone, a test file split away from the source file it
  covers
- dependencies constructed inline where the neighbours inject them, and new package-level mutable state
  in code that had none
- interface shape: declared beside its implementation rather than beside its caller, wide where the
  codebase keeps them narrow, accepted as a concrete type where the caller should be free to substitute,
  or returned where a concrete type would do
- a surface that exists only for tests — an exported symbol, constructor, setter or accessor with no
  caller outside the tests. Run the search, and put what it returned in the finding
- error wrapping, logging, naming, parameter count and control flow that contradict what the same
  package does everywhere else
- an old function, type or field kept for compatibility after every caller in the repository moved to
  its replacement
- an approach or a review decision this project already settled, being re-opened without saying so

Where the project's stated rules and your general taste disagree, the project wins. Where a document
and the code disagree and you cannot tell which is authoritative, raise it as an open question rather
than assuming the document is stale.
