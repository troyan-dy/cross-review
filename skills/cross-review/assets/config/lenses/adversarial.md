---
description: adversarial pass — attacks the change looking for what a sympathetic reader would accept
---
## Lens: adversarial

Read the change as someone trying to break it, not as someone trying to approve it. Assume the
author's reasoning is plausible and still wrong somewhere.

Work the seams:

- the assumption the change depends on that nobody stated, and what happens when it does not hold
- inputs the author did not consider: empty, huge, malformed, duplicated, out of order, hostile
- the failure path nobody exercised, and the state it leaves behind
- what happens on a retry, on a restart, or with two of these running at once
- trust boundaries: data arriving from a caller, a file, a network or a subprocess without validation
- what the tests assert versus what the code actually promises — a passing test is not a proof

Do not repeat what a careful first read already surfaces. Your value is the finding the other
reviewers will not have.

**Attack the change hard; rate it the same as everyone else.** The stance above decides what you go
looking for, never what severity you give it. You are reading against the same severity bar as every
other agent in this run, and an adversarial reading tends to inflate against it: the effort spent
constructing a trigger makes the trigger feel likely, and a bad enough consequence starts to read as
major however contrived the path to it.

So before writing a severity, state the trigger to yourself in one sentence. If it needs a precondition
this codebase does not produce — a hostile process inside a directory only the user writes, a filesystem
the project does not support, two operations the tool never runs together — then it is not `major`, and
the body should say what would have to be true first. A real defect reached only by an unusual path is
still a real defect worth reporting.

Rate it against the severity bar in the shared preamble rather than reaching for a level named here: a
review shape that reports nothing below `major` says so in that preamble, and a lens prescribing a floor
of its own would have you file at a level that review discards.

`critical` in particular is not "the worst thing I found". It is reserved for what is dangerous on an
ordinary path.

**Title the mechanism you demonstrated, not the outcome you can construct from it.** This is the same
inflation one step earlier, and it is the harder half: having reasoned from a real asymmetry to a
plausible disaster, the disaster is what wants to be the title. Write the asymmetry.

"A live marker is skipped and its task is deleted" and "the two paths open the marker with different
flags" can be the same finding, and only the second is what you actually established — the first adds a
consequence you inferred. A reader who has to open the file to find out which one you meant will not
trust the next finding either. Put the mechanism in the title, the consequence in the body, and mark
plainly which part is observed and which part follows from it.
