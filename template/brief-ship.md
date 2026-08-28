# Brief

This is the contract for one task, written by SIANA before you were dispatched.
Your standing orders say how you work and how you report. This says what you are
here to do. Where the two meet, this is the more specific and wins.

If any `{...}` placeholder below is still unfilled, you were briefed by mistake:
call `block` and name it. A guessed contract is the failure this file exists to
prevent.

## Delivery: ship

Your work lands. This branch is the deliverable, and it is already checked out:

    branch  {SHIP_BRANCH}

Commit there and nowhere else. Never push by hand, never open a merge request, never
merge, and never touch the default branch. SIANA lands your work once it is accepted;
a branch is never thrown away while it holds work.

The middle segment of that name is the Conventional Commit type your commits carry.
SIANA stated it when it briefed you, so it is not yours to reconsider: a commit whose
type disagrees with the branch it sits on is exactly the mismatch this naming exists
to remove. A branch with no such segment is work briefed before this convention, and
it is still yours to commit on unchanged.

Your task's `verify` is this project's delivery rigor. `tasks done` runs it here in
your own worktree, so a pass is evidence about your work rather than about the tree
you were branched from. Make it pass before you call `done`, never in the hope that
it will.

**If this project's rigor is a validation pipeline you drive**, your standing orders
say how to hold a run safely and this brief says how to start one. One thing changes
here: your verify no longer runs the rigor, it reads what a run recorded. A green
there is evidence that you reached a passing run and left the branch where that run
left it, so it is not something `done` can produce on its own. Your branch is still
the deliverable and it still never leaves this machine - the pipeline does not push,
and neither do you.

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

A finding your rigor marks for a human is this same line, drawn by the tool instead
of by you. Relay it and `block`. Answering it yourself is deciding something the
pipeline already said was not the minion's to decide.
