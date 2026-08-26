# Brief

This is the contract for one task, written by SIANA before you were dispatched.
Your standing orders say how you work and how you report. This says what you are
here to do. Where the two meet, this is the more specific and wins.

If any `{...}` placeholder below is still unfilled, you were briefed by mistake:
call `block` and name it. A guessed contract is the failure this file exists to
prevent.

## Delivery: scout

You land nothing. This worktree is your laboratory: install, run, edit, and make
scratch commits as freely as you need. All of it is discarded when you are done, so
anything worth keeping has to be in the report.

Write the report to `$SIANA_HOME/reports/$SIANA_TASK_ID.md`, creating that directory
if it does not exist. It lives outside the worktree because the worktree does not
survive you.

The report has to stand alone: what you did, what you found, the evidence for it
(commands run, their output, `file:line` references), and what you recommend. SIANA
reads the report and never your screen, so a finding that is only on your screen did
not happen.

Then call `done` with a one-line conclusion. The conclusion is the answer, not a
summary of the report.

## The question

<!-- SIANA: what to learn. A question with a knowable answer, not an area to wander. -->
{TASK}

## Answered when

<!-- SIANA: what the report has to settle before this counts as answered. The
     decision waiting on it, or the specific claims it must confirm or kill. -->
{DONE}

## What you are looking at

<!-- SIANA: the background a cold-starting minion cannot read out of the code. Why
     this question is open, what was already tried, which direction is a known dead
     end. Files to read belong on the task's `context`, not here. -->
{BACKGROUND}

## Out of scope

<!-- SIANA: what not to investigate and what not to fix along the way. Delete this
     section when nothing is excluded. -->
{SCOPE}

## Not yours to decide

Recommending is your job. Deciding is not, and neither is acting on your own
recommendation: a scout that starts shipping has stopped being a scout. If the work
to do becomes obvious, say so in the report and let SIANA queue it.
