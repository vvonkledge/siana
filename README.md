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
| `python3`  | 3.13.13    | every `siana-*` command that is not bash or node      |
| `bash`     | 5.3.9      | `siana`, `siana-brief`, and the recipes in `justfile` |
| `node`     | 26.7.0     | `siana-console`; no other command here needs it       |
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
  `schema-attended.yaml`, `schema-findings.yaml`, `schema-tasks.yaml`,
  `schema-facts.yaml`, `schema-grants.yaml`: the store contracts. Also never
  overwritten, for a harder reason. A contract only ever grows, because a field
  dropped from a live contract makes every record still carrying it unreadable.
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
`decisions.jsonl`, `attended.jsonl`, `findings.jsonl`, `facts.jsonl`,
`grants.jsonl` - are not written here. `datafile` creates each on its first append,
so a contract with no `.jsonl` beside it is an empty store and not a broken install.

Into the bindir, as symlinks back into this checkout: `siana`, `siana-dispatch`,
`siana-brief`, `siana-watch`, `siana-owe`, `siana-retire`, `siana-close-workspace`,
`siana-handoff`, `siana-publish`, `siana-reap`, `siana-pipeline`, `siana-afk`,
`siana-gate`, `siana-read`, `siana-clean`, `siana-report`, `siana-console`,
`siana-findings`, `siana-fact`. They are links, so a `git pull` here updates the
commands with no reinstall. It does not update the home; `just upgrade` does that.

### The distro's own pi package

Beside the queue integration, `init` installs `template/pi-siana` into the home as a
project-local pi package. It carries three things:

    siana_cleanup, siana_runbook   tools, so SIANA can delegate fleet cleanup
    captain-report                 a skill, reached as `/skill:captain-report`
    /captain-report                the same procedure, as the command you type

It is installed from this checkout rather than copied, so a `git pull` updates it the
way it updates the commands. It is also installed exactly once: pi identifies a local
package by its resolved absolute path, so the same package reached through two
spellings is two packages to pi and every resource in it is discovered twice. `init`
reconciles the settings entry rather than appending one.

Loading it starts nothing. No process, no watcher, no timer, no model. A cleanup run
begins when something calls the tool, and never before.

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

`~/.siana/schema-projects.yaml` is the full field list. Four are worth knowing about
when you ask: `qa` is an independent validation command, and setting it puts a QA
task behind every ship task in that project; `orders` names a file of extra standing
orders every minion there is started with, which is where a project's own conventions
belong; `pipeline` says work there is validated by a pipeline the minion drives
instead of by a command that runs once, and is described under "A rigor the minion
drives" below; `automerge` is standing permission for SIANA to arrange the merge of
work that has been through all of that, and is described under "Letting accepted work
merge" below.

### Facts about a project

A staging URL, the link to where the business documentation lives, the account a
test user signs in with: things that are true of one project, that every minion sent
there needs, and that you got tired of repeating. Record one and every minion
dispatched for that project is told it, in a marked data section at the end of its
orders. No other project sees it.

    siana-fact url  <project> <slug> https://staging.example.com --note 'what it is'
    siana-fact text <project> <slug> 'one line, in your words'
    siana-fact list
    siana-fact rm   <project> <slug>

Values are one line, bounded, and https only for a URL. A fact is somewhere to go or
something to know, never the thing itself, and never a secret: it is written into a
file on disk in your home and read by every minion in that project.

#### A credential is different

A test login is a fact too, and it is the one kind that must not be written down. So
`siana-fact` keeps it in the operating system keychain and records only where it is:

    siana-fact credential <project> <slug> --account <username>

That prompts for the value through the keychain's own non-echoing prompt, twice.
The value is never an argument, never reaches a file, and never reaches SIANA. There
is no command that prints it back, deliberately, and `siana-fact get` refuses a
credential rather than making one.

A credential reaches nobody until you say so, for one task, before that task starts:

    siana-fact grant  <task-id> <slug>     # only while the task is still todo
    siana-fact revoke <task-id> <slug>

Nothing is inherited. Not from the project, not from a dependency, not from the QA
task paired with the one you granted, and not from a grant on the same fact
yesterday. The minion of that one task runs it into a child process:

    siana-fact exec <slug> -- <command>

which puts `SIANA_FACT_USERNAME` and `SIANA_FACT_PASSWORD` into that child and
returns its exit status. It refuses unless the task calling it is the task that was
granted it and is still `doing`, so a grant stops working the moment the work is
finished, whether or not you have revoked it. Revoking takes effect before the
keychain is read.

    siana-fact status

reports what is recorded and what is wrong with it: a reference whose keychain item
was removed, a grant whose task has finished, a record that was edited by hand into
a shape nothing can deliver. It reads no credential value to do it, and it repairs
nothing.

#### What this protects you from, and what it does not

SIANA, its minions and you all run as one operating system user. A process that
wanted a credential could call the keychain itself, so **this is not a sandbox and
you should not plan around it as one.**

What it does is narrower and worth having on its own. A fact recorded for one
project never reaches a minion working on another. A credential never lands in a
file, an orders document, a report, a handoff or a log, so nothing that leaves this
machine can carry one. And every use of one is something you authorised in advance,
for one task, and can withdraw.

`just init` writes both contracts and nothing else, so a home that records no facts
behaves exactly as it did before this existed, and no dispatch changes. `just
upgrade` keeps `facts.jsonl` and `grants.jsonl` untouched: a credential record is
the only pointer to the keychain item behind it, and replacing one would leave a
value nothing can find again.

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
worktree once nothing is left in it that only it holds; `siana-close-workspace`
closes the Herdr workspace that retirement left open, and only after it; `siana-reap`
removes the branch once the work has landed.

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
that push it is describing that minion's work. Nothing merges unless you granted that
project a merge, under "Letting accepted work merge" below; without the grant it is
still yours, in person.

It refuses rather than guesses. No open request from that branch, more than one, a
closed or merged one, a branch that has moved under the request, a head that is not a
fast-forward of what the request holds, or a minion still working on that branch all
stop before anything is pushed. So does a repair branch that moved after its verdict,
because the head that goes out is the one a second minion actually read.

The push and the description are two calls, so an interrupted run can leave the new
commits under the old copy. Running it again pushes nothing and puts the copy on.

### Letting accepted work merge

Everything above stops at an open merge request, and merging it is yours. If you would
rather it merged on its own once it has been through all of that, say so on the
project's registry record:

    datafile -f ~/.siana/projects.jsonl put \
        --set handle=<handle> --set path=<path> --set ship='<test command>' \
        --set qa='<qa command>' --set target=<branch> --set pipeline=true \
        --set automerge=squash

The value is `merge`, `squash` or `rebase`, and it is both the permission and the
method. There is no default and nothing infers one: how your history reads is not a
choice a script gets to make on your behalf, and the contract refuses anything else at
the write.

If that write is refused with `unknown field`, your home predates the field: see
"Upgrade" below for the one line that migrates it. Until it is migrated the field
cannot be recorded at all, which is the safe direction to fail in.

**It delegates arranging, not deciding.** What SIANA does with it is ask the forge to
merge that one request, pinned to that one commit, once every check and review your
branch protection requires has passed. Your protection rule is still the gate, and it
is the thing that actually merges. SIANA never merges anything itself, with or without
this field.

**Nothing infers it.** Not a previous merge, not the repository's settings, not the
shape of a branch, and not anything an agent read, wrote or was told. This record is
the only place it exists, and the file is yours.

**It adds a condition, it does not remove three.** A project with this field and
without `pipeline`, `qa` or `target` refuses to publish at all rather than publishing
under a weaker version of what you asked for. What has to hold before anything is
arranged: a driven pipeline run that passed at the accepted head, a QA task that came
back `done` on that same head, the branch on your remote still at it, an open
non-draft request from that branch to `target`, and at least one required check on
that head. A pending check may arm it; a failed one may not; no checks at all is never
read as a green.

**Arming is not merging, and it is not a promise.** All you have afterwards is a
request the forge will merge if its own gate goes green. If a check fails, it sits
there open exactly as it would have.

`siana-publish --dry-run` prints the method, the accepted head, the required checks
and whether it would arm, and changes nothing.

**Turning it off has an order.** What is armed lives at the forge, so it outlives this
fleet: taking the field off the record stops the next one being armed and retracts
nothing already armed. Read what is armed, cancel it, check, and only then remove the
field:

    siana-publish --armed <project>                        # what is armed, and what
                                                           # each would merge
    siana-publish --armed <project> --cancel-automerge     # disarm all of it
    siana-publish --armed <project>                        # `nothing armed`
    ... and only then take `automerge` off the record

The first form reads and cannot arm anything: it asks your forge for every open
request it is holding a merge for and prints the source branch, the target, the
method and the exact commit of each. That is the list to check a revocation against,
and it is what stops the whole thing depending on your remembering which QA tasks
armed what. With `--cancel-automerge` it disarms every one of them and then asks the
forge again to prove it, and it reports the ones it could not rather than stopping at
the first, so one stuck request never puts the others out of reach.

`siana-publish <qa-task-id> --cancel-automerge` is the same operation for one
request, when you know which. Either form needs neither the field nor a `target`,
precisely so it still works once you have removed either, and running one twice says
`nothing armed` rather than doing anything a second time. A cancel the forge accepted
but did not apply is a refusal that tells you to go and look, never a report that it
worked.

During an advisory session the reading form still answers and the cancelling forms
are refused, so a merge armed before the session still happens when its checks pass.
Stopping that one is your forge's own interface. See [Leave it
advisory](#leave-it-advisory).

**Only GitHub.** GitLab is refused under this field, and publishes to it exactly as it
did before. `glab` has no command that cancels an armed merge and none that says which
checks a project requires - a merge request there has one pipeline, and whether it
must be green is a project setting the client does not report. So on GitLab an arming
could not be retracted, and "this branch requires nothing" and "I could not ask"
arrive as the same empty answer. Neither is a thing to build a merge on.


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
is a separate change. And it never merges, whatever a project's registry record says:
a standing grant is permission for the ordinary attended path, and a session in force
is you saying decisions are being written down rather than made. Skipping a QA pair
and answering a pipeline finding marked `decide` are permanently yours either way.

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

### Delegate the cleanup

Retiring worktrees and reaping branches is long, repetitive and almost entirely
mechanical. Done inside SIANA's session it is thirty rounds of tool output crowding
out the fleet. So it happens somewhere else, and SIANA sees only what it has to
answer:

    siana-clean start --grant retire       one run, in its own context
    siana-clean status                     what it is doing, or stopped on
    siana-clean answer <run> --text ...    your answer to its question
    siana-clean resume <run>               carry it on from there

At the helm you never type these: SIANA calls the `siana_cleanup` tool the pi package
registers, and it is the same command underneath.

A run carries a grant, and the grants are named after the commands they unlock.
`inventory` reads and is always in force. `retire` adds `siana-retire`. `reap-report`
adds `siana-reap` in its report-only form. `close-workspace` adds
`siana-close-workspace`. There is no grant that reaches `siana-reap --yes`, because a
wrong reap is the one mistake in this fleet that loses work.

`close-workspace` is the narrow authority you granted to close the Herdr workspace a
finished minion leaves behind, and it is a grant of its own rather than part of
`retire` because closing a workspace kills the agent in it. Start a run with both and
it retires each tree and then closes the workspace that retirement left open, which
is where the idle agent and the open workspace per retirement used to accumulate.

The narrowness is in the command rather than in what the cleaner was told.
`siana-close-workspace` takes a task id, and resolves the workspace from that task's
own recorded owner pane - never from a label, a workspace number, an agent name or
what is focused, each of which finds *a* workspace rather than *that task's*. It
closes only a `done` task's workspace, only when Herdr says the workspace is a
linked-worktree one open on exactly the tree the queue recorded, in exactly the
repository the registry gives that project, unfocused, named by no other task in the
queue, and with its agent in one of the states a finished minion actually leaves -
idle, done or unknown. That last one is an allowlist, so an agent mid-turn, an agent
stopped at a dialog waiting for you, and a state a later Herdr grows all refuse. And
it closes only after the retirement has actually happened: the tree gone from disk and
gone from git's own list of worktrees, read from the world rather than taken from an
exit code.

That ordering is load-bearing twice over. A workspace closed before its worktree is
removed strands the worktree, and a project's *source* workspace closes every
linked-worktree workspace under it, so a workspace Herdr does not mark
`is_linked_worktree` is refused outright. Raw `herdr` closing stays refused to a
cleanup run whatever grants it holds.

The cleaner does no safety thinking of its own. It enumerates and delegates, and
every refusal it meets is one of your existing commands refusing on its own terms.

What keeps it to that is a directory of refusing shims put on the front of its `PATH`
for the run. A command outside the grant fails with a message naming what to do
instead, so the ordinary way an agent goes wrong - reaching for the next obvious
command - is stopped by a mechanism rather than by a sentence in a prompt. It is not
a sandbox, and it is worth knowing which: it intercepts command names, so a binary
invoked by absolute path is outside it. What actually holds is that the destructive
work lives in commands that fail closed on their own inputs.

What the cleaner does instead of guessing is stop:

    $ siana-clean start --grant retire
    run      clean-20260830-0730
      round  1
      asks   design-three-part's tree holds 44 untracked files; are they yours?
      kind   siana
      answer siana-clean answer clean-20260830-0730 --text <your answer>

The question is on disk before anybody reads it, and nothing after that point runs
until an answer is recorded. Answering and resuming are two operations with a process
boundary between them, so nothing is ever waiting on anything else and a restart
loses at most a round.

A question the cleaner marks `captain` is one SIANA cannot answer for you. It becomes
an ordinary recorded decision, you answer it, and only then can the run be unblocked.

Every answer lands in `$SIANA_HOME/runbook.md`, which the next cleaner reads first.
Entries are built out of the question a cleaner wrote down and the answer SIANA
recorded, and nothing else can be *recorded* there, so a guess, a secret or a stray
piece of transcript cannot get in that way.

That is a property of the command and not a wall around the file. A cleaner runs with
a shell, and a shell can write any file its user can, so the rule against editing the
runbook by hand is carried in the cleaner's instructions the way the rest of its
scope is. The child is started without the harness's file-writing tools, which
narrows it. Read it the way you read the shim guard above: a real boundary against
the ordinary mistake, and not a wall.

If anything goes wrong, nothing was half-done. The mutations belong to commands that
fail closed on their own inputs, so a killed cleaner, an unavailable model or a
corrupt run record all leave the fleet exactly as it was. `siana-clean status` says
which, and the package's README lists the recoveries.

### Ask for the report

    /captain-report

SIANA reads the live queue, registry, repositories, forge, watcher, herdr,
obligations, cleanup runs and decision history, and writes you an overview: what
happened, whether the fleet is moving, what is about to go wrong, what is waiting to
be published or cleaned up, what is still owed, and every decision you have to take.

A source it could not read is named as unreadable. It is never rendered as healthy
and never as empty, because "no open workspaces" when herdr is down is a lie you
would act on.

Each pending decision arrives with its options, what each one costs, what SIANA
recommends, and why. A recommendation is not authority and nothing in this distro
turns one into an action.

### What the fleet learns from what you decide

Every decision SIANA puts in front of you is recorded twice, and the split is the
point. The obligation holds the question, whether it is still open, and - once you
answer - your answer, in your words. Beside it, keyed by the same id,
`attended.jsonl` holds what the obligation has no room for: the situation, the
options, what each one cost, which SIANA would have chosen, and why.

    siana-owe history            every decision, joined
    siana-owe history --json     the same, for a program

Your answer lives in exactly one place and is read out of it every time, so there is
nothing here to go stale. Later, once an answer has been carried out:

    siana-owe outcome <id> --outcome "what actually happened"

It refuses while the obligation is still open, because an outcome recorded before an
answer is a guess.

This is a learning corpus and a reporting foundation, and that is all it is. It
exists so that "how often did SIANA and I agree, and about what" is a question with
an answer, which is the only ground an argument for giving the fleet more autonomy
could ever stand on. Nothing reads a recommendation as permission today, and nothing
in this distro turns a row of this store into an action.

It is not `decisions.jsonl`. That one is the advisory ledger: what SIANA would have
done while you were away, where nobody was asked and no answer exists.

### What the fleet found, after it was fixed

The queue holds work someone can still act on. A review finding whose repair has been
independently accepted is not that: nobody will do anything about it, and leaving it
in `blocked` makes the fleet report blockers when the actionable number is zero. It
is also the most expensive thing this fleet produces, and it is read again every time
similar work is briefed. So it moves to the findings ledger.

    siana-findings                  every finding, newest case first
    siana-findings show <id>        one finding whole, with its evidence resolved
    siana-findings case <case>      one rejection chain, every round in order
    siana-findings verify [<case>]  re-run every mechanical check
    siana-findings blob <sha256>    print one archived evidence file

A rejection chain is archived whole, as a case, and only once its last round ends in
an acceptance that is `done`. Nothing auto-archives: every record enters through a
plan SIANA wrote, and `siana-findings archive --plan <file>` is the only thing that
writes this store.

Two things it will not do, and they are the design. It never decides that a successor
resolved a finding: `resolution` is a sentence SIANA writes, and no ancestry, `deps`,
`base` or merge commit reaches it. And it never answers a finding your pipeline marked
for you: a case carrying one is refused unless a closed obligation of yours is named
in its evidence.

What it does check, on demand and not only once, is everything mechanical: that the
lineage resolves, that the case is a complete chain, that every archived report is
still byte-identical to the copy in the blob store, that every rejected head is still
pinned, and that the log has never been rewritten. `siana-findings verify` prints all
of that beside the words `not checked here` against the judgment, so a green run
never reads as agreement with the sentence.

Evidence is copied rather than referenced, into
`$SIANA_HOME/findings/blobs/<aa>/<rest>`, keyed by sha256. Your home is not a git
repository and the documents in it get rewritten; a digest of a file that changed
reads as tampering and a digest of a file that was deleted leaves nothing at all.
Rejected heads are pinned as `refs/siana/findings/<id>` in the project checkout,
which is what stops `git gc` collecting a commit whose branch is gone.

## Validate a change to the distro

    just test

Standard-library `unittest`, no dependencies to install. It drives the pure
mechanics in-process and drives the commands as real processes against a real
`tasks` and `datafile`, into throwaway homes, because a stubbed store would only
ever agree with the suite. Unittest arguments pass through, so `just test -v` is
verbose and `just test -k <slug>` runs one rule.

It reports a line per test as it goes, rather than unittest's dots, and puts a
watchdog around each one: a test that stalls dumps every thread's stack and the run
is taken down instead of sitting there. That is `tests/run.py`, and it is there
because a run killed by a hang guard printed dots that no line-oriented reader ever
showed.

Driving real commands is also what makes the suite slow, and slow in a particular
way: it waits rather than computes. A serial run spends about three quarters of one
core for its whole length, with the rest of the machine idle. So `tests/run.py`
hands whole test classes to a pool of worker processes, and the first line of a run
says how many workers it got.

Measured by an independent reviewer at `0773dde`, the commit this section sits on
top of, on an eleven-core M3 Pro: 912 tests, four runs green, one warm worktree,
no cache cleared and no two of them overlapping.

    one worker (control)    635.3s    461.4s CPU    0.73 cores    168 MB
    pool, default (5)       240.6s    763.3s CPU    3.17 cores    168 MB
    pool, shuffled          231.6s    737.1s CPU    3.18 cores    167 MB
    pool, buffered (-b)     192.3s    668.0s CPU    3.47 cores    168 MB

The median pool run against that control is a 63.5% cut: 635.3s to 231.6s, ten and
a half minutes to under four. That is the figure to quote, because it is the only
like-for-like one here - same head, same machine, same warm caches - and it errs
low rather than high. Load was not sampled at every start; what was observed put
the control on the quieter side of the pool runs, 3.2 to 4.3 around it against 6.6
rising to 15.7 during a pool run, with another agent running this same suite on the
box for part of the window.

Those four runs are that head's, and that head had 912 tests. This tree has 1495, so
expect longer on the one you are standing on: measured here, about six minutes from the
default pool on a quiet eleven-core box, about eight and a half with other work running
alongside it, and about nine and a half at the three workers a four-core runner gets.
Two single runs on this tree came in under those, 274s from the default pool and 487s
at three workers, both at loads between five and seven; one run each is an observation
and not a claim, and the estimates above are still the ones to plan against.
One worker is slower again in proportion, and stays a control to reach for rather than a
way to run the suite. All of them stretch under fleet load, and this machine's own
variation is wider than the gap between any two pool sizes. Measured separately by the
author of the pool at `6906b6a`, interleaving the two modes: one control of 1115s taken
at load 11.8, against pool runs of 199s, 296s and 551s taken at loads 14.3, 19.7 and
26.4. Across the whole of that work, at heads and loads that were not held constant, the
one-worker control measured anywhere from 703s to 1115s. Those are observations of what
load does, not a second speed claim - they are not comparable with the four runs above
and are not combined with them. Re-measure rather than trusting any single number here.

    SIANA_TEST_WORKERS=1 just test      one worker: unittest, in this process
    SIANA_TEST_WORKERS=8 just test      or any number you like

The pool is sized from the machine and capped, deliberately below the core count:
this machine also runs the fleet, and a suite that took most of it would slow down
everything else on the box. One worker is the control. It is the mode to reach for
when a failure looks like it might be the pool's fault rather than the code's, and
it is what every timing here was measured against.

Each worker gets a temporary directory of its own, and the run removes the lot when
it ends - on success, on failure, on a stall, and on Ctrl-C. Nothing it started
outlives it.

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

Your records are never touched: the registry, the queue, the obligations, the
project facts, and every store contract are left exactly as they are. When the
installed `tasks` grows a field your contract does not have, `upgrade` and `doctor`
say so and name the fields, because adding one is your call and the alternative is a
raw traceback the first time SIANA writes a task.

That holds for the project contract too, and one field is worth knowing about by
name. A home installed before `automerge` existed keeps a `schema-projects.yaml`
without it, and `upgrade` says so rather than editing the file. Nothing breaks: the
fleet still starts, `siana` says the contract is older than the field and prints the
line above, and every project reads as granting no merge, which is what every project
was before the field existed. What you cannot do until you copy the field across is
grant one, and the store refuses the write rather than recording something it cannot
validate. Copy `automerge` out of `template/schema-projects.yaml` in this repository
into your own when you want that.

## Diagnose

    just doctor

It changes nothing. It reports every file the home should hold, whether each required
command is on the `PATH` and where it resolves to, whether the distro's own pi
package is installed in the home's harness settings, whether a SIANA is running,
whether an advisory session is, whether every in-flight task's minion is still alive
in the pane it was dispatched to, what SIANA owes you, and the queue itself.

What is inside the fact stores is a separate report, because answering it means
asking the keychain about every credential reference:

    siana-fact status

Almost all of that is a line for you to read and act on, and `doctor` exits zero
having said so. Two things make it exit nonzero. The first it always had: the home's
queue does not read at all, which includes there being no home there yet - the
ordinary state before `just init`. The second is new: on a machine with `pi`, the
distro's own `pi-siana` package is not installed into the home, whether because the
settings file does not name it, names it twice, names it somewhere it is not, or is
not there at all. That one fails rather than reports because you cannot see it from
inside a session. A home missing that package has no `siana_cleanup` and no
`siana_runbook` tool, no `captain-report` skill and no `/captain-report` command, and
none of them fails: none of them is there to be called.

Things it says that are not faults:

- `tasks.jsonl (empty; written on the first task)` and its seven siblings. An empty
  store has a contract and no log yet.
- `no SIANA running`. That is the ordinary state between sessions.
- `no advisory session (every decision is the captain's)`. That is the state the
  fleet is in whenever you are at the helm.
- `note    a herdr that restarted recently reads as GONE everywhere until it has
  re-detected its agents`. Rerun before acting on it.

Things it says that are:

- `stale schema-tasks.yaml is missing: <fields>`. Your contract predates the
  installed `tasks`. Add the fields it names.
- `stale schema-projects.yaml is missing: <fields>`. Your project contract predates
  this distro. The fleet runs without them and the registry refuses to record them,
  so this is a setting you cannot make rather than something that is broken. Copy
  the fields it names out of `template/schema-projects.yaml`.
- `stale session claims pid <n>, which is gone`. A SIANA was killed before it could
  release its claim. Remove `$SIANA_HOME/session` and start again.
- `stale advisory session stopped without saying why`. The `siana-afk` process is
  gone and its record is not. Every decision has been refusing since, so nothing
  happened that you were not told about. Read the record, then `siana-afk --stop`
  clears it.
- `GONE <task>: herdr has no agent in <pane>`. A minion died. `tasks reset` reclaims
  the task, and it stays manual, because that minion's worktree may hold work nobody
  has landed.
- `missing pi-siana package: <why>`, and `missing .pi/settings.json` on a machine
  with `pi`. The distro's own package is not installed in this home: the settings
  file does not name it, names it twice, names it somewhere it is not, or is not
  there at all. `just init` installs it and collapses a duplicate back to one. This
  and an unreadable queue are what make `doctor` exit nonzero.

## Read the fleet as JSON

    siana-read tasks       [--fields a,b,c] [--status S] [--limit N]
    siana-read projects
    siana-read obligations [--closed]
    siana-read decisions   [--since ISO]
    siana-read fleet
    siana-read health

Everything else here prints for a person at a terminal. This prints for a program,
so something other than a terminal can show you the fleet. It reads and only reads:
no listener, no port, nothing served, and no path in it that writes to a store.

Project facts and credential references are deliberately not on it. They are local
operational context rather than fleet state, and a phone is not where you want a
list of which task may spend which credential.

Every run writes exactly one JSON document to standard output, whether it answered
or refused, and the exit code is the verdict:

    0   the document answers the question
    1   the source could not be read, or could not be trusted
    2   the request was wrong: an unknown subcommand, flag, field or timestamp

`--help` is the one exception, and it is the one that is neither an answer nor a
refusal: it prints the usage for a person to read.

The four stores answer in one shape:

    {"source": "tasks",
     "revision": {"inode": 3876394, "size": 488283, "mtime_ns": 1788090878060132363},
     "filter": {"status": "doing"},
     "total": 123, "matched": 4,
     "records": [...],
     "bad_lines": []}

- `revision` is the store's own snapshot, for a reader caching against it. Compare
  `inode` as well as `size`: `datafile compact` and `roll` rewrite the file in
  place, so a size that has not moved is not a store that has not moved.
- `total` is the live records before filtering and `matched` is after it, so an
  empty `records` is never ambiguous. `matched` above the number returned means
  your `--limit` clipped the answer.
- `bad_lines` is every line of the store `datafile` could not read, with its line
  number and the text. A damaged store is still a successful read, and the damage
  arrives with it rather than being smoothed over.

`fleet` asks herdr what is running and answers `state: "ok"` with the agent records
verbatim. When herdr cannot be reached it answers `state: "unknown"` with
`agents: null` and exits nonzero. It never answers an unreachable herdr with an
empty list: that would be a claim about every pane, and "no minions" is not
something a silent herdr said.

`health` reports three things and judges none of them: the session record with
whether that pid is still a `siana`, the wake counters, and the exit code, stdout
and stderr of `siana-watch --status` kept apart. `no SIANA running` is the ordinary
state between sessions, so nothing here calls it a fault. What it will not do is
report a process it could not verify: a dead pid and a pid now wearing something
else both read `alive: false` with `why` saying which.

Two refusals are worth knowing about, because they look like answers:

- A store that cannot be read is a refusal, never `records: []`. An unreadable
  `obligations.jsonl` reported as empty would tell you SIANA owes you nothing.
- The contract is what says a store exists, not the log. `decisions.jsonl` does not
  exist until the first decision is written, and that is an honest empty answer with
  a null `inode`. A directory holding no contracts is refused on all four stores,
  which is what a mistyped `SIANA_HOME` gets instead of a fleet with nothing in it.

Three of the stores live in the home. The queue is read through `SIANA_TASKS_FILE`
when that is set, the same way every other command here resolves it, so a minion
pointed at a queue outside its home reads the one the rest of the fleet is using.
Each store's contract is looked for beside it, named `schema-<store>.yaml`.

It is not literally read-only on disk, and it is worth saying so plainly before
anything is built on top of it: a `datafile` read may rewrite the `.idx` cache
beside a store. That write is atomic and it is a cache. No authoritative record is
ever changed.

## Read the fleet over loopback HTTP

    SIANA_CONSOLE_PORT=8787 siana-console

`siana-read` answers one question and exits, which a phone cannot run. This is the
smallest process that puts those same documents on a socket, and it is the whole of
what it does. You start it yourself, and you stop it with Ctrl-C or `kill`. Nothing
in the fleet starts it, nothing in the fleet depends on it, and stopping it leaves
SIANA, the watcher, every minion and every store exactly as they were.

It is the one command here that runs on `node`, so `just doctor` reports whether
there is one to run it on.

Read what it is before you run it:

- **Local only.** It binds `127.0.0.1` and there is no flag, variable or fallback
  that moves it. Nothing else on your network can reach it.
- **Unauthenticated.** Anything already running on this machine as you can read it.
  That is the same reach that anything running as you already has over the home
  itself, so it adds nothing locally and would add everything remotely, which is why
  the address is not configurable.
- **Read-only where it matters.** It has no write endpoint. Every request it serves
  reaches the fleet through `siana-read` and through nothing else, so no request can
  change an authoritative record. It writes one file of its own: the claim below.
  `siana-read` and `datafile` disclose the rest, which is a store's `.idx` cache.

`SIANA_CONSOLE_PORT` has no default, and the console refuses to start without it. A
port every machine used would be a port something else is one day holding.

It serves two routes and refuses everything else, every other method included:

    GET /api/state?rev=<opaque>    every source, in one document
    GET /api/stream               server-sent events announcing a new revision

`/api/state` runs all six `siana-read` commands and returns what each of them said:
the whole document, its exit code, and when it was asked. Nothing is folded, and no
field of a source document is read, because `siana-read`'s refusals do not share one
shape. A source that could not be read arrives as a source that could not be read,
so a silent herdr is `state: "unknown"` here exactly as it is there.

`rev` is a cache validator. Pass back the `revision` you last saw and an unchanged
fleet answers `204` with no body. It is opaque, it changes when any source's answer
changes, and it is the same for the same answers, so a console restarted against
untouched stores hands back the revision you already have.

`/api/stream` says a new revision exists so that a client can refetch `/api/state`.
It carries no state and no event id: there is nothing to replay, a reconnecting
client is told the current revision, and `/api/state` is complete on its own when
the stream is disconnected.

One console runs per home. It claims `$SIANA_HOME/inbox/console` before it binds,
recording the pid, the `ps` command and the port it owns, and a second one refuses
and names the first. A claim whose process is gone, or whose pid is now something
else, is taken over and says so. A claim it cannot read, or cannot prove has
stopped, is refused rather than taken: the console that wrote it may still be
serving that port. Nothing is ever killed to recover one. Stopping releases the
claim; a `kill -9` leaves it behind, and the next start proves it stale and takes
it.

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
                handoff templates, the store contracts, and `pi-siana/`, the
                distro's own pi package.
    tests/      the suite.
    justfile    init, upgrade, test, doctor, uninstall.
    VISION.md   what the fleet is for.
    ORDERS.md   the contract for changing this repository.
