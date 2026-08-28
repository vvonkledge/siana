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

## Do not

- **Never remove or retype a field in a store contract** (`template/schema-*.yaml`).
  A contract only ever grows. A field dropped from a live contract makes every record
  still carrying it unreadable, and those records are the captain's.
- **Never touch the captain's `$SIANA_HOME`.** It holds the live queue, the registry,
  and instructions SIANA has evolved for itself. Your worktree is the distro. Writing
  to the home from here would edit the fleet you are running inside.
- Never edit `CHANGELOG.md` or any file marked auto-generated.
