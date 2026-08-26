# Brief

This is the contract for one task, written by SIANA before you were dispatched.
Your standing orders say how you work and how you report. This says what you are
here to do. Where the two meet, this is the more specific and wins.

If any `{...}` placeholder below is still unfilled, you were briefed by mistake:
call `block` and name it. A guessed contract is the failure this file exists to
prevent.

## Delivery: ship

Your work lands. Your branch `siana/$SIANA_TASK_ID` is the deliverable, and it is
already checked out: commit there and nowhere else. Never push, never open a pull
request, never merge, and never touch the default branch. SIANA lands your branch
once your work is accepted; a branch is never thrown away while it holds work.

Your task's `verify` is this project's delivery rigor. `tasks done` runs it here in
your own worktree, so a pass is evidence about your work rather than about the tree
you were branched from. Make it pass before you call `done`, never in the hope that
it will.

## The task

<!-- SIANA: what to build. Concrete enough that a cold-starting minion could not
     build the wrong thing and still believe it complied. -->
{TASK}

## Done when

<!-- SIANA: the acceptance the verify command cannot express. Behaviour to observe,
     a case that must work, a regression that must not return. Delete this section
     only when the verify command genuinely says all of it. -->
{DONE}

## What you are looking at

<!-- SIANA: the background a cold-starting minion cannot read out of the code. Why
     this matters, what was already tried, which direction is a known dead end.
     Files to read belong on the task's `context`, not here. -->
{BACKGROUND}

## Out of scope

<!-- SIANA: what not to touch and what not to fix along the way. Delete this section
     when nothing is excluded. -->
{SCOPE}

## Not yours to decide

Product choices, destructive or irreversible actions, and anything that changes what
was asked of you belong to SIANA, not to you. Call `block` with the options you see
and stop. Work you discover along the way is reported the same way and never queued
by you.
