# The empty slot

One thing in this fleet is designed and not built: the validation pipeline a ship
minion drives inside its own task, before it calls `done`. Everything around it
exists. This file is what a session starting cold needs to build it, and it is
written to be deleted once that is done.

Read `VISION.md` first, then `template/AGENTS.md`, then this.

## Where the fleet stands

Every other part of the loop runs, and has run on work minions produced:

    tasks add -> siana-brief --ship -> siana-dispatch
      -> minion commits on siana/<task-id>, calls done
      -> QA task becomes ready, a second minion judges it
      -> siana-publish <qa-task-id>   pushes and opens the merge request
      -> the captain orders, SIANA merges
      -> siana-retire <task-id>       removes the worktree
      -> siana-reap <handle>          removes the branch
      -> the forge deletes the remote branch (delete_branch_on_merge)

Eighteen minions drove that across three rounds on 2026-08-28, including two
rejections and their fixes. The suite is 348 tests. CI runs `just test` on a clean
runner for every pull request into `main`.

The `siana` project is registered `ship='just test'`, `qa='just test'`,
`target='main'`, `orders='ORDERS.md'`.

## What the slot is

`ship` in the registry is usually a command that runs once at `done`. In some
projects the rigor is a process the minion drives instead, round by round, and then
the verify only reads its outcome. `template/AGENTS.md` describes how SIANA holds
such a project safely, and `template/orders.md` describes how a minion drives one.
Neither names a tool, because there is none.

That frame was written from `no-mistakes`, an external gate this project used until
2026-08-28. It was removed because the captain wants a pipeline SIANA owns. **Read
the frame as a description of the thing being replaced, not as a specification.** It
encodes that tool's defects as assumptions in at least two places, called out below.

## What is already decided

These are rulings, not suggestions. Each is load-bearing and each has a reason that
cost something to learn.

**The pipeline does not push.** Nothing leaves the machine before a QA minion has
accepted it, and a pipeline runs inside the ship task, which is before. So the
pipeline is review, test and lint, and the branch is where it ends. `siana-publish`
carries push, merge request and everything downstream, and it runs on the QA
verdict. This supersedes an earlier ruling that the gate pushes.

That deletes most of what an external gate does. No push step, no pull request step,
no CI step, no post-push custody, no recover path, no monitor that keeps watching a
merge request and rebasing it. If a design brings any of those back, it is solving a
problem this ordering removed.

**It must leave `siana/<task-id>` at exactly the head it validated**, before the
minion calls `done`. The QA worktree is cut from that branch afterwards. A pipeline
that rebases or amends and does not return the branch hands the second minion a head
nobody validated. This is the requirement that replaced the old "QA behind a gate is
unwired" problem, and it is the single hardest constraint here.

**The verify reads the outcome and never starts the pipeline.** A verify that starts
the rigor runs once, after the minion has already declared itself finished, so
anything the rigor demanded arrives too late to act on. See `template/AGENTS.md`,
"Every task carries a contract before it starts".

**A question the pipeline asks a minion is a `block`, relayed.** The minion does not
answer it. That is how a finding reaches SIANA, and through SIANA the captain.

**The brief and the project's `orders` carry the protocol.** The verify no longer
says how the work is validated, so something else must. For this project that means
`ORDERS.md` gains back a section describing how to drive whatever gets built. That
section is exactly what was deleted on 2026-08-28.

## Two contradictions to resolve first

Both are in the instruction files and both would mislead a minion today.

**`template/orders.md` still assumes the pipeline pushes.** Its "If your rigor is a
process you drive" section ends: *"You never push by hand. If your rigor has a push
step, that step is the only push there is."* `template/AGENTS.md` now says the
pipeline does not push at all. One of them has to change, and it is `orders.md`.

**The same section carries a `no-mistakes` artefact.** *"never use a flag that keeps
your head over the pipeline's - it silently drops the fixes the pipeline made while
the run still reads as passed"* describes `--keep-local`, a flag of a tool that is
gone. A pipeline SIANA owns need not have such a flag, and probably should not.

Fixing these two is a good first task: small, real, and it forces whoever does it to
read the whole frame.

## Open design questions

None of these has been decided. They are the captain's.

**Is it one process or several commands?** An external gate was one long-running
process the minion talked to round by round, which is why the frame is full of
warnings about parked runs and exit codes. A SIANA-owned pipeline could instead be a
sequence of ordinary commands the minion runs, each terminating. That would delete
most of the four rules in `template/orders.md` rather than reimplementing them.

**What does review mean when the reviewer is an agent?** Test and lint are exact and
belong in a script. Review is judgment. The project's own line is that logic which
can be exact lives in a script and work that needs understanding lives in an agent,
and never the two mixed. A review step is therefore a second agent, which starts to
look like the QA minion that already exists. Say clearly what the pipeline's review
does that QA does not, or leave review out.

**How does a finding reach the minion?** An external gate had a findings table with
per-finding actions, and a whole protocol for responding to them. If the pipeline is
commands rather than a session, a finding is just a non-zero exit and some output,
and the minion fixes it and runs again. Simpler, and it makes "reach a terminal
outcome before you call `done`" trivially true.

**Does it need a registry field?** Today `ship` is a command string. A driven
pipeline could just be a different command string. If it needs its own field, say
why; the contract only ever grows, and every existing `$SIANA_HOME` has to hand-add
any new field or `datafile put` fails.

## Two small things unrelated to the pipeline

Both were found by minions and deliberately left out of scope. Neither needs its own
round.

- `template/AGENTS.md` says "pull request" twice (lines 288 and 513) against six
  uses of "merge request".
- Nothing guards the `test` recipe's `python3 -B`, so a future edit could silently
  bring `__pycache__` litter back into every worktree. That litter is what makes
  `siana-retire` refuse, so the cost is a manual step on every task.

## How to know it works

Do not trust a green suite for this. The loop is the test:

1. Queue a small ship task on `siana` whose work the pipeline should have opinions
   about, and brief it to drive the pipeline.
2. Dispatch, and watch that the minion reaches a terminal outcome rather than
   reading an exit code as a verdict.
3. Check `siana/<task-id>` points at the head the pipeline validated, before `done`.
   This is the constraint most likely to be got wrong.
4. Let the QA minion judge it. Its worktree is cut from that branch, so if step 3 is
   wrong, this is where it shows.
5. `siana-publish`, merge, `siana-retire`, `siana-reap`.

A pipeline that cannot survive that sequence is not finished, whatever the suite
says.
