# SIANA

SIANA is an agent distro, not an app: the instructions one agent reads, the scripts
that carry its decisions out, and the conventions its records are kept in. One
person, the captain, talks to SIANA, and SIANA runs a fleet of minions that do the
work.

`VISION.md` says what that is for. `ORDERS.md` is the contract anyone changing this
repository works under. This file is how you install it and run it.

Two things are worth separating before anything else:

- **The distro** is this repository. It is code, and you pull it.
- **The home** is a directory, `~/.siana` by default, holding one fleet's queue, the
  captain's project registry, what SIANA owes the captain, and SIANA's own
  instructions. It is state, and it is yours. Nothing in this repository is
  authoritative about it once `just init` has run.

## What you need

`just init` refuses without `tasks`, and without at least one of `pi` and `claude`
to run SIANA's own session in. The rest of this list is what running a fleet needs,
and `just doctor` reports which of it it can find. Versions are the ones this has
actually been run against; nothing older or newer has been tried, and nothing here
has been run on an operating system other than macOS.

| Tool       | Verified   | Why it is needed                                      |
| ---------- | ---------- | ----------------------------------------------------- |
| `python3`  | 3.13.13    | every `siana-*` command but `siana` and `siana-brief` |
| `bash`     | 5.3.9      | `siana`, `siana-brief`, and the recipes in `justfile` |
| `just`     | 1.58.0     | the recipes below                                     |
| `git`      | 2.50.1     | a minion works in a worktree of its project           |
| `datafile` | 0.1.1      | every record store: queue, registry, obligations      |
| `tasks`    | 0.1.0      | the fleet queue, and the agent packages for it        |
| `pi`       | 0.84.2     | one of the two harnesses SIANA's session can run in   |
| `herdr`    | 0.8.0      | the session manager minions are started in            |
| `claude`   | 2.1.231    | the harness minions run in, and the other of the two  |

Verified on macOS 26.6.2 (Darwin 25.6) on 2026-08-28.

`datafile` and `tasks` are checkouts you install yourself:

    git clone git@github.com:vvonkledge/datafile.git && (cd datafile && just install)
    git clone git@github.com:vvonkledge/tasks.git    && (cd tasks    && just install)

Install `datafile` first. `tasks` finds its storage engine relative to where it was
invoked from, so `just install` in the tasks checkout refuses if `datafile` is not
reachable, and tells you the two ways to fix it.

`pi`, `herdr` and `claude` come from Homebrew:

    brew install pi-coding-agent herdr

`claude` is Claude Code, installed however you normally install it. It is the only
minion harness `siana-dispatch` will start: any other kind is refused rather than
guessed, because a wrong flag starts an agent that silently has no orders and looks
exactly like a working dispatch. It is also what reads the diff in the review step of
a driven pipeline, for the same reason and with the same refusal.

SIANA's own session is the one place there is a choice. It runs in `pi` or in
`claude`, `init` installs the queue for whichever of them you have, and you pick per
start. See [Choose the harness](#choose-the-harness). Only one of the two is needed
to run a fleet; installing both costs nothing and settles nothing until you start.

## Initialize

From a checkout of this repository:

    just init

That creates the home and links the commands. It is idempotent: run it again any time.

Two variables control where things go. Either set them in the environment, or pass
them to `just` under the names the recipes use:

    SIANA_HOME=/path/to/home XDG_BIN_HOME=/path/to/bin just init
    just home=/path/to/home bindir=/path/to/bin init

- `SIANA_HOME` is the home directory. Default `~/.siana`.
- `XDG_BIN_HOME` is where the commands are linked. Default `~/.local/bin`.

`init` tells you if the bindir is not on your `PATH`, and what to add to your profile.

### What `just init` writes

Into the home:

- `siana.env`, a flat env file with the resolved paths SIANA needs to find itself.
  Regenerated every run, because it is derived and never yours to edit for keeps.
- `AGENTS.md`, `orders.md`, `review.md`, `brief-ship.md`, `brief-scout.md`,
  `brief-qa.md`, copied from `template/`. **Copied only when absent.** SIANA evolves
  its own instructions, so a home copy that differs from the template is your work,
  and `init` says `kept` and leaves it alone.
- `schema-projects.yaml`, `schema-obligations.yaml`, `schema-tasks.yaml`: the store
  contracts. Also never overwritten, for a harder reason. A contract only ever grows,
  because a field dropped from a live contract makes every record still carrying it
  unreadable.
- The queue integration for each harness you have, so SIANA's session sees the
  fleet queue and no other agent session on this machine does. For `pi`, that is
  `pi-agent-tasks/`, generated from the installed `tasks`, and `.pi/settings.json`
  installing it project-locally. For `claude`, `.claude/settings.json` carrying the
  SessionStart hook and `.claude/skills/agent-tasks/`. Both are written when you
  have both. See [The queue integration](#the-queue-integration).

The stores themselves - `tasks.jsonl`, `projects.jsonl`, `obligations.jsonl` - are
not written here. `datafile` creates each on its first append, so a contract with no
`.jsonl` beside it is an empty store and not a broken install.

Into the bindir, as symlinks back into this checkout: `siana`, `siana-dispatch`,
`siana-brief`, `siana-watch`, `siana-owe`, `siana-retire`, `siana-publish`,
`siana-reap`, `siana-pipeline`. They are links, so a `git pull` here updates the
commands with no reinstall. It does not update the home; `just upgrade` does that.

### The queue integration

SIANA's session opens with the queue already in front of it, whichever harness it
opens in. `tasks` knows how to say that to both, and `init` installs it for each
harness it finds: for `pi` a generated package, and for `claude` a SessionStart hook
with the same skill beside it. Both land in the home, so no other agent session on
this machine sees fleet-wide state.

You do not have to find any of it, and there is no checkout it has to sit beside.
The pi package is regenerated on every `init`, deliberately: it bakes in the
absolute path of the `tasks` it was generated from, so a stale copy can point at a
`tasks` you have since moved or replaced.

Starting SIANA in a harness the home has no integration for is refused. That session
would come up looking exactly like a working SIANA with no fleet queue behind it, and
would answer about the queue from nothing. If you install a second harness later, run
`just init` again and it gains one.

If you keep your own copy - a tasks checkout you are editing, say - point `init` at
it and it will install that one instead of generating anything:

    SIANA_PI_PACKAGE=~/src/tasks/pi-agent-tasks just init

It has to be the package directory itself, the one holding `package.json`, and not
the checkout around it. A `SIANA_PI_PACKAGE` that is not there, or that names a
directory with no `package.json` in it, is refused before anything is written rather
than quietly falling back to a generated package. `pi` installs any directory you
give it without complaint, so the near miss would otherwise leave you a home that
`doctor` calls complete with no queue in front of SIANA. If you asked for a
particular package, getting a different one silently - or none - is worse than a
retype.

## Run SIANA

SIANA runs inside a `herdr` session, because that is how it starts minions and how
`siana-watch` wakes it. Start herdr, then start SIANA in a pane:

    herdr
    siana

`siana` opens an agent session in the home, with the project registry and SIANA's
open obligations appended to its system prompt. Talk to it there. You never talk to
a minion, and no minion talks to you.

It starts outside herdr too, and says so when it does: with no pane there is nothing
for `siana-watch` to wake, so the fleet only advances on your turns.

**One SIANA leads the fleet.** Starting a second is refused, and the refusal tells
you the pane the first is in so you can attach to it. Two would race each other for
every task in the queue, and you would have no way to tell which one you were
talking to.

To stop SIANA, exit the session. That releases the claim. If it is killed hard
enough that it cannot clean up, `just doctor` reports the session as stale and names
the file to remove.

### Choose the harness

SIANA's session runs in `pi` or in Claude Code. Both are told the same things in the
same flag, and they differ in how each is pointed at the home: `pi` is started with
`--approve`, which is your consent to the project-local files `init` wrote there, and
`claude` is started plain and picks up the home's `.claude/settings.json` as the
settings of the directory it opens in.

Neither is started with its permission checks waived. That is a call only you can
make, so pass `--dangerously-skip-permissions` yourself if you want it. A minion's is
waived by `siana-dispatch` because nobody is there to answer a prompt, and at the
helm you are.

With no argument, `siana` opens in whichever harness the home was installed for, and
in `pi` when it was installed for both. Say otherwise per start:

    siana --harness claude
    SIANA_HARNESS=claude siana

The flag wins over the environment, so a shell that exports one still leaves you able
to say otherwise for a single start. Everything after the flag is the session's:

    siana --harness claude --model opus

A name that is neither is refused rather than guessed at, because falling back to the
default would open a session you did not ask for and say nothing about it.

`siana` records the harness it started alongside the pane, and `siana-watch` pokes
that pane only while herdr still reports that same agent in it. So the watcher
follows whichever harness you chose, and a pane that has been taken over by the
other one is a stop rather than a poke typed into somebody else's session.

### Register a project

Every task belongs to a project, and SIANA speaks in handles rather than paths, so a
project has to be in the registry before work can be queued against it. Ask SIANA to
add one, giving it a handle, where the project lives, and how work there is verified.
The registry is yours, so SIANA writes it only when told to, with:

    datafile -f ~/.siana/projects.jsonl put \
        --set handle=<handle> --set path=<path> --set ship='<verify command>'

`~/.siana/schema-projects.yaml` is the full field list. Three are worth knowing about
when you ask: `qa` is an independent validation command, and setting it puts a QA
task behind every ship task in that project; `orders` names a file of extra standing
orders every minion there is started with, which is where a project's own conventions
belong; `pipeline` says work there is validated by a pipeline the minion drives
instead of by a command that runs once, and is described under "A rigor the minion
drives" below.

### Branches the fleet leaves in your repository

Every minion works in a worktree of its own, on a branch under `siana/`. That prefix
is the fleet's, and nothing else should use it: `siana-reap` judges everything in it.

Work that lands is named `siana/<type>/<task-id>`, where the type is one of the
eleven Conventional Commit types. SIANA states it when it briefs the work and never
infers it afterwards, so the branch says what its commits will say and what the merge
request will be. Work that lands nothing keeps a single segment - `siana/<task-id>`
for a scout, `siana/qa-<task-id>` for the minion that judges a ship branch - because
those are roles in the fleet rather than categories of change. Branches made before
this convention keep the names they have, and every command still finds them.

You never make one of these by hand, and nothing here pushes one. `siana-publish`
pushes the branch a QA minion accepted, and only that; `siana-retire` removes the
worktree once nothing is left in it that only it holds; `siana-reap` removes the
branch once the work has landed.

### Leave it running

    siana-watch

`siana-watch` watches the queue and pokes SIANA when a minion reports, so the fleet
does not idle between your turns. Start it after SIANA is up: it finds the session
through the home.

**Running it is the autonomy grant.** While it runs, SIANA reconciles and dispatches
ready work without you in the room. The grant is the process: you give it by starting
this and withdraw it by stopping it with Ctrl-C. Nothing outlives it, so no session
can inherit an autonomy you did not choose to leave running.

### A rigor the minion drives

Most projects are verified by one command that runs when the minion says it is
finished. That is enough when the whole of the rigor is exact. It is not enough when
part of it is judgment, because judgment spent once, after the work is declared done,
arrives too late for anyone to act on.

For those projects there is a pipeline the minion drives instead, round by round:

    datafile -f ~/.siana/projects.jsonl put \
        --set handle=<handle> --set path=<path> --set ship='<test command>' \
        --set pipeline=true

`ship` stays what it was - the exact command a run executes. What changes is that
SIANA gives ship tasks there a verify that *reads what a run recorded* rather than one
that starts anything. The minion runs `siana-pipeline run`, which runs your `ship`
command and then puts an agent on the diff against the brief; whatever comes back, the
minion fixes and runs again, until a run passes.

Two properties are worth knowing, because they are the reason it is shaped this way:

- **A run records the commit it validated, and `done` refuses if the branch has moved
  off it.** The QA minion is cut from that branch, so anything committed after a
  passing run would be reviewed wearing a green nothing gave it. This is a comparison
  the machinery makes, not a rule anybody is trusted to follow.
- **Nothing is pushed.** A run happens inside the ship task, which is before any
  independent minion has accepted the work. Review, test, lint, and the branch is
  where it ends; `siana-publish` still carries everything after the QA verdict.

A finding the reviewer says only a human can settle stops the run and comes back to
you through SIANA, as a `block`. The minion never answers one.

It costs what a second agent costs. A round is minutes, and every finding is another
round, and the minion is parked for all of it. Turn it on for a project where a
missed judgment is expensive, and leave it off where a green suite is the whole of
what you wanted.

`~/.siana/review.md` is what that reviewer is told. It is yours to edit, like every
other instruction file in the home.

## Validate a change to the distro

    just test

About 35 seconds. Standard-library `unittest`, no dependencies to install. It drives
the pure mechanics in-process and drives the commands as real processes against a
real `tasks` and `datafile`, into throwaway homes, because a stubbed store would only
ever agree with the suite. Unittest arguments pass through, so `just test -v` is
verbose and `just test -k <slug>` runs one rule.

`ORDERS.md` is the rest of the contract for changing anything here, and the parts of
it that can be checked exactly are checked by the suite.

## Upgrade

    git pull
    just upgrade

The commands are symlinks, so `git pull` alone updates those. `just upgrade` is for
the home, and it refuses to run against a home that was never created: an upgrade is
not an install, and `init` would happily build a fresh empty fleet out of a typo in
`SIANA_HOME` and call it a success.

Instructions SIANA has evolved are never merged and never silently replaced. A
diverged file is copied to `$SIANA_HOME/upgrade/<timestamp>/` with a `.diff` beside
it showing what the upgrade changed, and then the template lands. Re-apply what you
still want and delete that directory. Reconciling instructions is reading what they
mean, which is an agent's job and not a script's.

Your records are never touched: the registry, the queue, the obligations, and all
three store contracts are left exactly as they are. When the installed `tasks` grows
a field your contract does not have, `upgrade` and `doctor` say so and name the
fields, because adding one is your call and the alternative is a raw traceback the
first time SIANA writes a task.

## Diagnose

    just doctor

It changes nothing. It reports every file the home should hold, whether each required
command is on the `PATH` and where it resolves to, whether a SIANA is running,
whether every in-flight task's minion is still alive in the pane it was dispatched
to, what SIANA owes you, and the queue itself.

Things it says that are not faults:

- `tasks.jsonl (empty; written on the first task)` and its two siblings. An empty
  store has a contract and no log yet.
- `no SIANA running`. That is the ordinary state between sessions.
- `note    a herdr that restarted recently reads as GONE everywhere until it has
  re-detected its agents`. Rerun before acting on it.

Things it says that are:

- `stale schema-tasks.yaml is missing: <fields>`. Your contract predates the
  installed `tasks`. Add the fields it names.
- `stale session claims pid <n>, which is gone`. A SIANA was killed before it could
  release its claim. Remove `$SIANA_HOME/session` and start again.
- `GONE <task>: herdr has no agent in <pane>`. A minion died. `tasks reset` reclaims
  the task, and it stays manual, because that minion's worktree may hold work nobody
  has landed.

## Uninstall

    just uninstall

Removes the linked commands, and only the ones that are links into this checkout:
anything else in the bindir wearing one of those names is refused rather than
deleted. Your home is left alone, queue and all. Delete it by hand when you mean to.

## Layout

    bin/        the commands. Mechanics only: they stop and report when the
                world surprises them, and never adjudicate meaning.
    template/   what an install copies into the home: SIANA's instructions, the
                standing orders every minion is started with, the brief
                templates, and the store contracts.
    tests/      the suite.
    justfile    init, upgrade, test, doctor, uninstall.
    VISION.md   what the fleet is for.
    ORDERS.md   the contract for changing this repository.
