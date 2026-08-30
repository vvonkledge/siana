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
  `brief-qa.md`, `handoff.md`, copied from `template/`. **Copied only when absent.**
  SIANA evolves its own instructions, so a home copy that differs from the template
  is your work, and `init` says `kept` and leaves it alone.
- `schema-projects.yaml`, `schema-obligations.yaml`, `schema-decisions.yaml`,
  `schema-tasks.yaml`: the store contracts. Also never overwritten, for a harder
  reason. A contract only ever grows, because a field dropped from a live contract
  makes every record still carrying it unreadable.
- `principles.md`, a template for the principles an advisory session holds SIANA to.
  Written only when absent, and never touched by `just upgrade` either: a distro that
  could rewrite it could rewrite what SIANA is held to while you are asleep. It ships
  with its placeholder unfilled, and `siana-afk` refuses to start while that
  placeholder is there. See [Leave it advisory](#leave-it-advisory).
- The queue integration for each harness you have, so SIANA's session sees the
  fleet queue and no other agent session on this machine does. For `pi`, that is
  `pi-agent-tasks/`, generated from the installed `tasks`, and `.pi/settings.json`
  installing it project-locally. For `claude`, `.claude/settings.json` carrying the
  SessionStart hook and `.claude/skills/agent-tasks/`. Both are written when you
  have both. See [The queue integration](#the-queue-integration).
- `.pi/extensions/wake.ts`, when you have `pi`. It is what delivers `siana-watch`'s
  wake into SIANA's session, and the watcher refuses to start without it. See
  [Leave it running](#leave-it-running).

The stores themselves - `tasks.jsonl`, `projects.jsonl`, `obligations.jsonl`,
`decisions.jsonl` - are not written here. `datafile` creates each on its first append,
so a contract with no `.jsonl` beside it is an empty store and not a broken install.

Into the bindir, as symlinks back into this checkout: `siana`, `siana-dispatch`,
`siana-brief`, `siana-watch`, `siana-owe`, `siana-retire`, `siana-handoff`,
`siana-publish`, `siana-reap`, `siana-pipeline`, `siana-afk`, `siana-gate`. They are
links, so a `git pull` here updates the commands with no reinstall. It does not
update the home; `just upgrade` does that.

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
`siana-watch` confirms the session it is watching for is still there. Start herdr,
then start SIANA in a pane:

    herdr
    siana

`siana` opens an agent session in the home, with the project registry and SIANA's
open obligations appended to its system prompt. Talk to it there. You never talk to
a minion, and no minion talks to you.

It starts outside herdr too, and says so when it does: with no pane there is nothing
for `siana-watch` to confirm it is watching for, so it refuses and the fleet only
advances on your turns.

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

`siana` records the harness it started alongside the pane, and `siana-watch` runs
only while herdr still reports that same agent in it. So a pane that has been taken
over by the other harness stops the watcher rather than leaving it raising wakes for
a session that has gone. It also runs only for `pi`, for the reason under
[Leave it running](#leave-it-running).

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

### What the merge request says

The title and the body come from the minion that did the work, not from the task it
was given. It writes them at `~/.siana/handoffs/<task-id>.md` once its last commit is
made: the problem and why the change exists, what the implementation does and the
design choice behind it, what it was checked with, where review attention is worth
most, and what it trades away or deliberately leaves alone.

That is judgment, so it stays with an agent. What is exact is `siana-handoff`, which
validates the document and assembles it: five sections, none of them empty, one title
inside a subject line's width, and a recorded commit that has to be the head the QA
minion accepted. A handoff that is missing, still carrying its scaffolding, malformed
or left behind by a later commit refuses the publish rather than travelling with work
it does not describe, and nothing in that path asks a model. So does one that points
into `~/.siana`, in any of the ways that directory gets written: a reviewer cannot
follow a path onto the captain's machine, and what is under it was written for SIANA
and not for them.

The brief never travels. It was written before the work existed, by an agent briefing
a minion, so it can say what was asked for and not what was built, what it was
checked with, or where to look. Merge requests made out of it read as instructions to
their own implementer. Neither does the QA report: that one is written for SIANA and
stays in the home. What the merge request says about the review is one sentence,
which `siana-publish` adds because it is a fact about the queue rather than a
judgment about the work.

`siana-publish --dry-run` prints the exact title and body, and changes nothing.

### A repair of something already published

A merge request that comes back red - a failing check, a review you want answered -
is repaired the way everything else is: SIANA queues a fix task, and a minion works
it on a branch of its own with its own independent QA behind it. What it does not do
is open a second merge request holding the same commits. Tell SIANA the fix repairs
published work, and it briefs it with:

    siana-brief <fix-id> --ship --type fix --repairs <the published ship task>

The brief then records the branch that work was published from, and once QA accepts
the fix, `siana-publish` fast-forwards that branch to exactly the head QA accepted.
Your merge request keeps its number and its review, and gains the commits that fix
it. Its description is rewritten from the repair minion's own handoff, because after
that push it is describing that minion's work. Nothing merges: that is still yours,
in person.

It refuses rather than guesses. No open request from that branch, more than one, a
closed or merged one, a branch that has moved under the request, a head that is not a
fast-forward of what the request holds, or a minion still working on that branch all
stop before anything is pushed. So does a repair branch that moved after its verdict,
because the head that goes out is the one a second minion actually read.

The push and the description are two calls, so an interrupted run can leave the new
commits under the old copy. Running it again pushes nothing and puts the copy on.

### Leave it running

    siana-watch

`siana-watch` watches the queue and wakes SIANA when a minion reports, so the fleet
does not idle between your turns. Start it after SIANA is up: it finds the session
through the home.

**Running it is the autonomy grant.** While it runs, SIANA reconciles and dispatches
ready work without you in the room. The grant is the process: you give it by starting
this and withdraw it by stopping it with Ctrl-C. Nothing outlives it, so no session
can inherit an autonomy you did not choose to leave running.

**It never types into SIANA's pane.** It raises a counter under `~/.siana/wake/`, and
the `wake.ts` extension `init` installed into SIANA's pi session is what reads it and
delivers the wake. That split is the whole point: the extension can read the input
editor and send in the same breath, so a wake can never arrive in the middle of
something you are half way through typing. The watcher used to write the wake into
that editor through herdr, which concatenated your unsubmitted draft with it and
submitted both as one message under your name.

**A wake waits for an idle session.** It goes out only when SIANA is between turns
and the editor is empty, and it always arrives as a turn of its own. There is no
delivery into a turn in flight: pi's only way to hand one a message is to queue it,
and pressing Escape to interrupt empties that queue into the input editor - your
draft with the wake pasted in front of it, which is the same bug from the other end.
So a wake raised while SIANA is working is held, not queued, and goes out when the
turn ends. Nothing is lost by waiting: the count is on disk, and it is recorded as
taken only once pi has said it accepted the message and is starting the turn on it.
Handing pi a message tells you nothing on its own - the call reports no result, and
a session can report itself idle and still refuse the prompt - so the extension
waits to be told rather than assuming, and a wake pi would not take is sent again
rather than counted.

So the watcher checks that SIANA's session is reading before it starts, and refuses
with what to do about it when it is not:

- **Nothing is reading.** The session is not up yet, or the home predates the
  extension. Start SIANA first, and run `just init` in the distro if `just doctor`
  reports `.pi/extensions/wake.ts` missing.
- **The recorded session is gone.** A pi killed hard enough leaves its record
  behind. Start SIANA again; it rewrites the record as it comes up.
- **SIANA is running in Claude Code.** There is no collision-free path into a
  running claude session, so there is no watcher for one either, and no fallback to
  the old write. Restart SIANA with `siana --harness pi`, or accept that the fleet
  advances only on your turns.

A wake raised while SIANA is down is not lost: the counter is on disk, and the
session drains it as it starts.

If wakes stop being taken while the watcher runs, it says so on its stderr every
five minutes and keeps counting. It cannot say why, and it does not guess. There are
five reasons and they want different things from you:

- **SIANA is mid-turn.** A long turn holds every wake raised during it. Nothing to
  do: the wake goes out within half a second of the turn ending.
- **A draft left in SIANA's editor.** The extension holds every wake while there is
  text in the input, so that one never arrives in the middle of something you are
  half way through typing. Send that message or clear it, and the held wake goes out
  within half a second. Nothing else is needed, and restarting SIANA here would
  throw the draft away.
- **SIANA is compacting.** A `/compact` you asked for refuses every message for as
  long as it runs, while the session still reports itself idle. The extension is
  told nothing about the refusal, so it waits and sends the wake again: it goes out
  within five seconds of the compaction finishing. Nothing to do.
- **SIANA cannot start a turn on it.** Pi took the message past its input gate and
  then refused to run it: no model is selected, or the credentials have expired. The
  extension is told nothing, so it sends the wake again every two minutes and nothing
  ever accepts it. This is the only one of the five that never clears on its own, and
  the only one `just doctor` is no help with - it reports that session present and
  healthy throughout. What says so is pi's own error, printed into SIANA's transcript
  on every retry. Select a model, or run `/login`, and the held wake goes out on the
  next retry.
- **The session is gone.** `just doctor` says whether one is running. Start SIANA
  again and it drains everything counted while it was away.

Only the last of those is a reason to restart anything, which is why the warning
names all five and diagnoses none: restarting SIANA over any of the first four
discards the draft, the turn, or the compaction that the wake was waiting on, and
leaves a missing model or expired credentials exactly where they were.

### Leave it advisory

    siana-afk --until 8h --project <handle>

The watcher above lets the fleet advance while you are away. It does not move a single
decision: a gate you would have answered is still held, and you come back to a queue of
them. An advisory session is the other half, and it moves none of them either. What it
does is make SIANA write each one down.

While one runs, a decision SIANA would have brought you becomes a record in
`~/.siana/decisions.jsonl`: the exact command it would have run, what it read to
conclude that, what it rejected and why, the principle it is citing, how sure it is,
and what class of harm it believes the action is. Then it refuses, and SIANA holds
exactly as it does today. In the morning:

    siana-gate log                  every decision, newest first
    siana-gate log --full <id>      one of them, whole

**It permits nothing, by construction.** There is no allowlist and no `--allow` flag.
Nothing SIANA reads - a report, a brief, a minion's reason, a page on the web - can
turn a session into permission, because authority is never read from anything an agent
writes. The point of running one is to find out whether your principles, applied by
SIANA to real situations, produce the decisions you would have made. Principles that
read well and decide badly are the failure this catches, and nothing else does.

Before the first one, write your principles into `~/.siana/principles.md`. `just init`
leaves a template there explaining what a principle can and cannot do, with an unfilled
placeholder at the bottom; `siana-afk` refuses to start until you have replaced it,
because a night of proposals justified by a template looks like a calibration run and
is not one.

The session is bound to three things at activation:

- **Your principles, by sha256.** Every decision re-hashes that file, so editing it
  fails closed at the next decision rather than quietly changing what SIANA is held
  to.
- **An absolute deadline.** `--until` takes a time or a plain duration like `8h`, it is
  required, and it may not be set more than 12 hours out. It is read out of the record
  at every decision rather than counted down by the process, so a session that is
  wedged, paused, or stopped has already expired.
- **The projects you name.** `--project <handle>`, repeatable, resolved against your
  registry. A decision about anything else is refused.

Stop it three ways, all equivalent: `siana-afk --stop`, Ctrl-C, or
`touch ~/.siana/afk.stop`. The third exists because the first two need you to find the
right terminal. It is checked before everything else, so it beats a live session, a
valid hash, and an unexpired deadline, and it deliberately leaves the session record in
place: an emergency stop should be something you then read and clear on purpose, not
something that halted silently.

Two of those bindings are stronger than the third sounds. `siana-afk` records its own
pid and what `ps` calls it, and every decision re-asks the operating system about both,
so a session cannot be forged by writing a file. The deadline, the projects, and the
principles hash are read back out of that record, so they hold against you from a
second terminal, against a stray script, and against an accident - and not against
somebody who edits the record itself. Rewriting `principles.md` and then the `sha256`
beside it passes the hash check. That is the same guard-rail-not-a-boundary shape as
the `SIANA_TASK_ID` refusal below, for the same underlying reason: minions run with
permissions that let one write any file here, so nothing kept here is a boundary
against one. What contains it meanwhile is that nothing is permitted whatever the
record says, and that every decision names the session it was made under.

**While a session runs it applies to you too.** Telling SIANA to publish something
gets you a recorded proposal and a refusal, not a merge request, because the session is
what says decisions are being written down rather than made. If you are back at the
helm and want it published now, stop the session first. Nothing changes with no session
running: publishing is what it has always been.

`siana-afk --status` says whether one is running and how many decisions it has
recorded, and `just doctor` asks it the same way it asks the watcher. Like the
watcher's, it reports and never repairs: removing a stopped session's record would be
deciding you have read it.

Two things it will not do. A minion cannot start one: `siana-afk` refuses outright when
`SIANA_TASK_ID` is in its environment, which is how every minion runs, so an injected
report cannot talk a well-meaning minion into starting a session on your behalf. That
is a guard rail and not a boundary, because minions run with permissions that let one
unset the variable first; closing it properly means narrowing those permissions, which
is a separate change. And it never merges. Merging is permanently yours, and so is
skipping a QA pair and answering a pipeline finding marked `decide`.

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

Three or four minutes. Standard-library `unittest`, no dependencies to install. It
drives the pure mechanics in-process and drives the commands as real processes
against a real `tasks` and `datafile`, into throwaway homes, because a stubbed store
would only ever agree with the suite. Unittest arguments pass through, so `just test
-v` is verbose and `just test -k <slug>` runs one rule.

It reports a line per test as it goes, rather than unittest's dots, and puts a
watchdog around each one: a test that stalls dumps every thread's stack and takes the
run down instead of sitting there. That is `tests/run.py`, and it is there because a
run killed by a hang guard printed dots that no line-oriented reader ever showed.

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
whether an advisory session is, whether every in-flight task's minion is still alive
in the pane it was dispatched to, what SIANA owes you, and the queue itself.

Things it says that are not faults:

- `tasks.jsonl (empty; written on the first task)` and its three siblings. An empty
  store has a contract and no log yet.
- `no SIANA running`. That is the ordinary state between sessions.
- `no advisory session (every decision is the captain's)`. That is the state the
  fleet is in whenever you are at the helm.
- `note    a herdr that restarted recently reads as GONE everywhere until it has
  re-detected its agents`. Rerun before acting on it.

Things it says that are:

- `stale schema-tasks.yaml is missing: <fields>`. Your contract predates the
  installed `tasks`. Add the fields it names.
- `stale session claims pid <n>, which is gone`. A SIANA was killed before it could
  release its claim. Remove `$SIANA_HOME/session` and start again.
- `stale advisory session stopped without saying why`. The `siana-afk` process is
  gone and its record is not. Every decision has been refusing since, so nothing
  happened that you were not told about. Read the record, then `siana-afk --stop`
  clears it.
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
                standing orders every minion is started with, the brief and
                handoff templates, and the store contracts.
    tests/      the suite.
    justfile    init, upgrade, test, doctor, uninstall.
    VISION.md   what the fleet is for.
    ORDERS.md   the contract for changing this repository.
