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
- `README.md` is the captain's install and operating guide. A change to what `init`
  writes, to what a command is called, or to what a recipe refuses, changes it too.

## How your work is checked

Every ship task here is validated by a pipeline you drive, and the last section of
this file is how you drive it. The first thing a run of it executes is the suite:

    just test

About a minute. It drives the pure mechanics in-process and drives the commands as
real processes against a real `tasks` and `datafile`, because a stubbed store would
only ever agree with the suite.

Herdr is the one exception, in `tests/fake_herdr.py`. A live server wants a terminal
and answers when it answers, so the answers that matter most - herdr slow, wrong, or
gone - are the ones it could never be made to give on cue. Its transport is scripted
and nothing else is: the commands still open a real socket and speak the real
protocol. Script herdr there; never a store.

If you change a behaviour a test names, change that test in the same commit and say
why. If you add a behaviour worth having, add the test that fails without it. A test
named after a rule is how the next agent finds out the rule exists.

Every pull request into `main` runs that same command on a clean runner, from
`.github/workflows/ci.yml`. That check is required for merge-readiness: work is not
ready to land until it is green, and a run that never started is not a pass. It
installs what the suite drives - `just`, `uv`, `pi`, and pinned checkouts of `tasks`
and `datafile` - so a green there says the suite passes somewhere other than the
machine that wrote it.

## Conventions

- Never the em dash. Use a plain dash.
- Wrap prose at 88 columns.
- Comments say why, not what. Every refusal in `bin/` carries the failure it exists
  to prevent, and those comments are the only record of what was already tried. Keep
  that standard: a refusal with no reason beside it will be deleted by someone.
- Minimum code that solves the problem. Nothing speculative.
- Logic that can be exact belongs in a script; work that needs understanding belongs
  in an agent. Never mix them.

The first two are exact, so `just test` checks them instead of trusting you to
remember (`tests/test_conventions.py`). The column limit is for prose only: fenced
and indented blocks are commands, and wrapping one changes what it does. The rest of
this list is judgment and lives only here.

## Do not

- **Never remove or retype a field in a store contract** (`template/schema-*.yaml`).
  A contract only ever grows. A field dropped from a live contract makes every record
  still carrying it unreadable, and those records are the captain's.
- **Never touch the captain's `$SIANA_HOME`.** It holds the live queue, the registry,
  and instructions SIANA has evolved for itself. Your worktree is the distro. Writing
  to the home from here would edit the fleet you are running inside.
- Never edit `CHANGELOG.md` or any file marked auto-generated.

## The pipeline

**Every ship task in this project is validated by `siana-pipeline`.** The captain's
registry record for `siana` carries `pipeline: true`, and that field is what makes a
driven pipeline the rigor here rather than something a brief opts into. SIANA queues
ship work with `verify: siana-pipeline check`, and `check` reads what a run recorded
instead of starting one. So no brief has to ask for this, and none does. If you are
shipping, you are driving a run, and work that never reached a passing one cannot
verify.

A run is two steps, in that order. First it executes this project's own `ship`
command, which is the `just test` above: exact, about a minute, and yours to get
green before you spend a run on it. Then it puts an agent on your diff, read against
your brief and against these orders. That second step is why the rigor is a pipeline
and not a command: it is judgment, it costs tokens, and every finding it raises costs
you another round.

The rest of this section is how a round behaves.

### A round

From your own worktree, with everything committed:

    siana-pipeline run

It refuses a dirty tree. A run validates one commit and records which one, so
anything you have not committed is work it would pass without having seen. It runs
`just test` first and starts the reviewer only on a green suite, so a red suite costs
you a minute and no tokens.

Then read the exit code. It is the whole protocol:

    0   passed. The record is green at this commit. Stop here.
    1   yours to fix. Fix it, commit, and run again.
    2   not yours. `block`, and relay what it printed, verbatim.

There is no third state, and there is no run to attach to: the command returns or it
does not, so nothing can be parked at a gate and there is no status to poll. An exit
code here really is a verdict, because the thing that produced it has already
finished.

### After a pass

**Do not commit again.** The run recorded the head it validated, and your verify is
`siana-pipeline check`, which compares that head against where your branch actually
is. A commit after a passing run turns a finished task into a red verify, and it does
so for a good reason: the QA minion is cut from this branch, and it would otherwise
read a head nothing validated while wearing your green.

If you do have to change something, that is fine. Change it, commit it, and run
again. What you must never do is change it and call `done`.

`check` starts nothing; it reads the record. So `done` cannot produce a green that a
run did not already earn, and there is no flag anywhere that makes it try.

### What a finding is

A finding the run lists for you to fix is yours: the reviewer read your diff against
your brief and says that part is wrong. Fix it, commit, run again.

A finding the run prints as one nobody here can settle is not yours at all. It is a
product choice, a destructive step, or a change to what you were asked for. `block`
and relay it word for word. Answering it yourself is deciding something the pipeline
already said was not the minion's to decide, and paraphrasing it decides half of it
on the way past.
