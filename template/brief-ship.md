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
{REPAIR}

Commit there and nowhere else. Never push by hand, never open a merge request, never
merge, and never touch the default branch. SIANA lands your work once it is accepted;
a branch is never thrown away while it holds work.

A `repairs` line beside that one means this work repairs work that is already
published. Your branch is still yours and still the whole deliverable; what that line
records is the branch whose open merge request receives your accepted head, once a QA
minion has accepted it. Publication does that and nothing else does, so you never
commit on that branch, never push it, and never open a second request for the same
work.

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

## The handoff

Your branch is one half of what you deliver. The other half is the copy a human
reads on the merge request, and you write that too, because you are the only agent
that will ever have understood both what was asked and what you actually built.

Write it after your last commit and before you call `done`:

    siana-handoff "$SIANA_TASK_ID" --scaffold
    siana-handoff "$SIANA_TASK_ID" --head "$(git rev-parse HEAD)"

The scaffold says what each section is for. The second command is the check: it
refuses a section left empty, a title that is a task id, and a copy that describes
a commit your branch has already moved past.

It is judged like the rest of your work. The minion that reviews this branch reads
it against the change, and publication refuses a handoff that is missing, unfilled,
malformed or stale, so a branch nobody can describe is a branch that does not land.

Write it for someone who has not read this brief, was not told what this fleet is,
and is deciding whether to merge. Nothing else about your work travels with it: not
this brief, not your task, and not the report of the minion that reviews you.

**If this is a repair**, your handoff replaces the one on the request you are landing
on, because after your push the commits under that description are yours. So write a
whole one and not a postscript: what the work was for in the first place, and what
the solution does now that yours is part of it. A reader arriving at that request has
no memory of the round that failed, and nothing tells them which half of the copy is
new.

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
