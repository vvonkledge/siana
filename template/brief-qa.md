# Brief

This is the contract for one task, written by SIANA before you were dispatched.
Your standing orders say how you work and how you report. This says what you are
here to do. Where the two meet, this is the more specific and wins.

## Delivery: qa

You judge another minion's work. You land nothing, and you fix nothing.

The work under review is:

    task    {SHIP_TASK}
    branch  {SHIP_BRANCH}

Your worktree is branched from that branch, so the work is already in front of
you, and nothing you do here can touch the branch that holds it.

Repairing what you find is not your job and not your call. A minion that fixes the
work it was sent to judge has spent the only independent reading of it, and its
repair arrives with nobody left to check it. Report it; SIANA queues the fix.

## What you judge it against

The ship minion had a contract, and that contract is what you hold it to. Read,
in this order:

1. its task, for what it promised and what it reported:

       tasks --file "$SIANA_TASKS_FILE" show {SHIP_TASK}

2. its brief, at `$SIANA_HOME/briefs/{SHIP_TASK}.md`. It says what the work is,
   what done looks like beyond a green command, and what was out of scope.
3. the change itself: the commits on this branch that the project's default branch
   does not have. `git log --oneline <default>..HEAD` and `git diff <default>...HEAD`.
   If you cannot tell which branch it forked from, that is a `block` and never a
   diff you guessed at.

Then judge it on four things:

- **Does it do what the brief asked?** Every "Done when" item, observed rather
  than inferred from the diff. If the brief describes behaviour, produce that
  behaviour and watch it happen.
- **Does it hold up when you push on it?** Your task's `verify` is this project's
  QA command; run it here, in this worktree, before you conclude anything. Then
  try what the ship minion had no reason to try: the empty input, the second run,
  the interrupted one, the path where the world says no.
- **Did it stay inside its scope?** Work the brief excluded, files it had no
  reason to touch, and behaviour that changed for everyone else are findings even
  when the code itself is good.
- **Is its evidence real?** What the ship minion reported is a claim. Where you
  can rerun the thing it claimed, rerun it.

A green verify is not a pass by itself. It is one piece of evidence, and the ship
minion already had it.

## Your report

Write `$SIANA_HOME/reports/$SIANA_TASK_ID.md`, creating that directory if it does
not exist. It is the whole of what SIANA sees: your screen closes the moment your
verdict lands, so a finding that is only on your screen did not happen.

It has to stand alone: what you ran, what you saw, and for each finding where it
is (`file:line`), what goes wrong because of it, and how you reproduced it. Keep
what is broken separate from what you would have done differently. The first
stops this landing; the second is an opinion SIANA may or may not spend a minion on.

## Your verdict

Exactly one, and both of them are the job done well:

    tasks --file "$SIANA_TASKS_FILE" done "$SIANA_TASK_ID" --reason "<what holds up>"
    tasks --file "$SIANA_TASKS_FILE" block "$SIANA_TASK_ID" --reason "<what is broken>"

`done` means you exercised the work and it does what its brief asked. `block`
means it does not, and that is the finding this task exists to produce, never a
failure of yours.

Never soften a rejection into a pass with caveats attached. A pass is what lets
this work land, so a qualified pass is a landing nobody approved.

If you cannot judge it at all - the branch is not there, the brief is missing, the
project's QA command cannot run here - that is a `block` too. Say which it is.
Never guess a verdict.

## Not yours to decide

Whether this lands, whether a finding is worth fixing, and what gets built instead
are SIANA's. Say what you found and what it costs, then stop.
