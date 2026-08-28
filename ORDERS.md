# Project orders: siana

This project is SIANA's own distro: deterministic scripts, the instruction files an
agent reads, and the justfile that installs them. There is no application here. Every
change you make is either mechanics a script owns or judgment an instruction shapes,
and the line between those two is the project's whole design.

## What is where

- `bin/` holds the commands. Mechanics only. They stop and report when the world
  surprises them, and they never adjudicate meaning.
- `template/` is what an install copies into the captain's `$SIANA_HOME`: SIANA's
  instructions, the minion orders you are reading a copy of, the brief templates, and
  the store contracts. Changing one changes what every future SIANA and every future
  minion is told.
- `tests/` is the suite. Standard-library `unittest`, no dependencies.
- `VISION.md` says what the fleet is for. Read it before you argue with a design
  choice in here, because most of them are downstream of it.

## How your work is checked

    just test

About 35 seconds. It drives the pure mechanics in-process and drives the commands as
real processes against a real `tasks` and `datafile`, because a stubbed store would
only ever agree with the suite.

If you change a behaviour a test names, change that test in the same commit and say
why. If you add a behaviour worth having, add the test that fails without it. A test
named after a rule is how the next agent finds out the rule exists.

Every pull request into `main` runs that same command on a clean runner, from
`.github/workflows/ci.yml`. That check is required for merge-readiness: work is not
ready to land until it is green, and a run that never started is not a pass. It
installs what the suite drives - `just`, `uv`, `pi`, and pinned checkouts of `tasks`
and `datafile` - so a green there says the suite passes somewhere other than the
machine that wrote it. The check exists first on the branch that adds it, and will
not appear on `main` until that branch lands.

## Conventions

- Never the em dash. Use a plain dash.
- Wrap prose at 88 columns.
- Comments say why, not what. Every refusal in `bin/` carries the failure it exists
  to prevent, and those comments are the only record of what was already tried. Keep
  that standard: a refusal with no reason beside it will be deleted by someone.
- Minimum code that solves the problem. Nothing speculative.
- Logic that can be exact belongs in a script; work that needs understanding belongs
  in an agent. Never mix them.

## Do not

- **Never remove or retype a field in a store contract** (`template/schema-*.yaml`).
  A contract only ever grows. A field dropped from a live contract makes every record
  still carrying it unreadable, and those records are the captain's.
- **Never touch the captain's `$SIANA_HOME`.** It holds the live queue, the registry,
  and instructions SIANA has evolved for itself. Your worktree is the distro. Writing
  to the home from here would edit the fleet you are running inside.
- Never edit `CHANGELOG.md` or any file marked auto-generated.

## When a brief sends you through the gate

**This project's routine rigor is `just test`, and your task's `verify` says so.**
Run it, make it pass, and report. That is the whole of what most ship work here
needs.

`no-mistakes` is a different thing and it is not the default. It is a validation
pipeline you drive round by round, and it costs a minion tens of minutes and several
human decisions, so it runs when a brief explicitly asks for it and never because
this section exists. If your brief does not mention it, you are not driving it.

When your brief does ask for it, the rest of this section is how it behaves.
Everything below was established by driving real runs from a dispatch worktree. Where
it contradicts what the tool's own help steers you toward, the tool's help is the one
that has been observed to be wrong.

### Starting

From your own worktree, once:

    no-mistakes axi run --intent "<what this task set out to accomplish>"

`--intent` is the goal behind the change, never a description of the diff. The call
blocks and returns at a gate or at an outcome. Read every return: on a `gate:`,
respond; loop until an `outcome:`.

**Loop on the presence of a `gate:` key, not on a status string.** The status differs
between rounds - `awaiting_approval` on a first review gate, `fix_review` on the gate
after a fix round - and a check written against one of them hangs on the other.

Keep the run id from that first return. If you were redispatched onto a task whose
run is already in flight, calling `axi run` again attaches to it rather than starting
a second one, but re-attach deliberately with `axi status --run` instead of relying
on that.

### Before every `respond`, four checks

The first three cost a second. The third is the one the tool will not volunteer, and
it is the one that matters.

1. `git rev-parse --abbrev-ref HEAD` is still `siana/$SIANA_TASK_ID`. If it is not,
   you are on the wrong branch. Go back. Never read the mismatch as a dead run: the
   error `respond` gives you from the wrong branch is byte-identical to the one it
   gives when your run has really died.

2. `no-mistakes axi status --run "$RUN"`, and read `run.status`. `running` means
   alive. `cancelled`, `failed` or `completed` is how you diagnose a dead run - that
   field, never the error text from `respond`.

3. In that same output, `branch_sync.local.head` must equal
   `branch_sync.pipeline.submitted_head`. If they differ, you have moved your branch
   under a live run: do not `respond`, `block` and say so. The pipeline's fixes would
   land on a head your branch has already left behind.

   **Compare exactly those two fields.** `relation` does not separate the states: it
   reads `equal` before anything happens, `unknown` on a perfectly healthy run where
   the pipeline has committed a fix, and `ahead` when you are stranded. A check
   written on `relation` goes red on the normal path. `pipeline.current_head` is also
   the wrong thing to compare against, because it diverges from yours on every
   healthy run that fixes anything.

   `branch_sync` is only emitted while the current branch has an active run, so its
   absence is not a clean bill of health. It is check 2 telling you the run is over.

4. You have not committed, rebased, reset or checked out on this branch since `run`.
   Nothing checks this. There is only not doing it.

### Responding

    no-mistakes axi respond --action approve
    no-mistakes axi respond --action fix --findings <id>,<id> [--instructions "..."]
    no-mistakes axi respond --action skip

**Never `--yes`**, on `run` or on `respond`. It auto-resolves every subsequent gate,
and those gates are precisely the decisions that were never yours.

A finding whose `action` is `ask-user` is yours to relay and never to answer.
`block`, and give SIANA the finding's id and its text. Expect this more than once in
a run and at more than one step: the `test` step raises them too, on a step that had
no findings at all until it ran.

**Say when a finding is truncated.** Findings arrive cut off, marked
`(truncated, N chars total)`, and the full text is not reachable through `axi` at
all. Relay what you have, verbatim, and say that it is truncated and by how much, so
SIANA knows it is deciding on part of a sentence rather than all of one. Never
paraphrase an `ask-user` finding to make it fit.

### After the outcome

`outcome: passed` is not the end of your job. Read `next_action` and follow it
verbatim.

After a run that pushed, the pipeline has published the validated head and the
resolution is plain `no-mistakes axi sync`.

If `next_action.code` is `recover_custody`, the run finished holding commits it never
published, and `no-mistakes axi sync --recover` is the guarded return. It may refuse,
as `blocked_recover_diverged`, because the pipeline rebased onto the default branch
and your local head did not. **That refusal is correct behaviour**: it changes
nothing and anchors the pipeline's commits at a durable ref, and it names what it
would take to reconcile. It is a finding, not an obstacle. `block` and report it.
Do not reconcile it by hand.

**Never `--keep-local`.** It keeps your head and leaves the pipeline's own fix
commits behind while the run still reads `outcome: passed`. That is shipping the
unfixed version under a green.

Until the sync step has actually succeeded, the validated work is not on your branch.
Reaching `outcome: passed` is not the same as holding it. Call `done` after custody
has returned, not before.

### What this costs

A full run on a 25-line change took about 17 minutes and three human gates, with the
three most expensive steps skipped; a real changeset ran longer. You are parked for
all of it and can do nothing else. That is expected, and SIANA knows it. Do not
abandon a run because it is slow.
