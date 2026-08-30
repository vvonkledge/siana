# pi-siana

The pi package SIANA's own session loads: fleet cleanup as a delegated agent, and the
captain's report as a skill.

`just init` in the SIANA distro installs it into the captain's home as a project-local
package, so no other pi session on the machine sees it. Nothing here is global.

## What it registers

    extensions/cleanup.ts             the siana_cleanup and siana_runbook tools
    skills/captain-report/SKILL.md    /skill:captain-report, and the model's own
                                      choice of it
    prompts/captain-report.md         /captain-report

The skill and the prompt template are the same procedure reached two ways, which is
deliberate. Pi registers a skill as `/skill:captain-report` and a prompt template as
`/captain-report`, and the captain types the second one. The prompt template is three
lines that name the skill; the procedure lives in `SKILL.md` and nowhere else, so
there is one copy of it to keep true.

## The cleanup tools

`siana_cleanup` is a shim over the `siana-clean` command, and is thin on purpose.
Every rule the protocol has - the lock, the durable question, the grant, the guard on
the child's `PATH`, the runbook, the process reaping - is in that command, where it is
exact and where the distro's test suite drives it. A second copy of any of it here
would be a second set of rules, and the copy in TypeScript would be the one nothing
tests.

    action: "start"    begin one run, under a grant
    action: "status"   what a run is doing, and what it stopped on
    action: "answer"   record an answer to the question it stopped on
    action: "resume"   carry it on from there
    action: "abort"    stop one, keeping its state

**Loading this extension starts nothing.** The factory registers two tools and
returns. No process, no watcher, no timer, no model. Pi runs extension factories in
invocations that never open a session, so a factory that started a cleaner would run
one on `pi --list-models`.

**Nothing here waits on SIANA.** A cleaner that becomes uncertain writes its question
to disk and ends; the command returns with exit 3 and the question in its output. So
`answer` and `resume` are separate calls with a process boundary between them, and
the deadlock a nested agent invites - a parent blocked on a child that is blocked on
the parent - has nowhere to happen.

**Nothing after that point runs, and it is the guard that says so.** Every shim
refuses while the question file is there, so a cleaner that ignored its instructions
and kept working is stopped by a mechanism rather than by a sentence in a prompt.

## The authority boundary

The cleaner reports to SIANA. SIANA reports to the captain. Neither of those is moved
by anything in this package.

A cleanup run carries a grant, and the grants are named after the commands they
unlock: `inventory` reads, `retire` adds `siana-retire`, `reap-report` adds
`siana-reap` in its report-only form. There is no grant that reaches `siana-reap
--yes`, and there is none that closes a herdr workspace: `siana-retire` owns worktree
removal, and a second route to the same destruction would be a second copy of a
safety judgment that must have exactly one.

A question the cleaner marks `captain` cannot be answered by SIANA on the captain's
behalf. `siana-clean answer` refuses it until an obligation id is named, so the path
runs through `siana-owe decision` and a real answer from the captain. An answer given
to a cleaner never manufactures authority; it only tells a stopped cleaner what was
already decided.

The guard that puts refusing shims on the child's `PATH` is a real boundary against
the ordinary way an agent goes wrong, which is reaching for the next obvious command.
It is not a sandbox: an agent invoking `/usr/bin/git` by absolute path would be
outside it. What actually holds is that the destructive mechanics live in commands
that fail closed on their own inputs.

## The runbook

`$SIANA_HOME/runbook.md` is what a cleaner reads at the start of every run. It is
written only by `siana-clean answer`, and only out of two strings: the question a
cleaner wrote down before it stopped, and the answer SIANA recorded. Nothing else can
reach it, so a guess cannot land there and neither can a stream fragment, a secret a
tool printed, or a private transcript. Entries are keyed by question id, so appending
one twice is a no-op and a retried answer is safe.

## Recovering from a failure

Every failure leaves the fleet untouched, because nothing here mutates anything: the
mutations are `siana-retire`'s, and it fails closed on its own inputs.

**pi is missing, or the model is unavailable.** The run fails at round one and
nothing was done. Install pi, or do the cleanup by hand with `siana-retire`.

**The child died, or was aborted.** `siana-clean status` reports it interrupted
rather than running, because the recorded pid is checked against the operating
system every time it is read. Start a new run; the runbook keeps what was learned.

**The run record or the question will not parse.** It was hand-edited or lost, since
both are written whole by an atomic rename. Move it aside. That run is not resumable,
and a new one re-derives everything from the world.

**A question is pending and nobody answered it.** Nothing after that point ran.
Answer it and resume, or abort the run.

**Two starts race.** One takes the lock and the other is refused, naming the run that
holds it and what it is doing.

## Installing it by hand

`just init` does this. By hand, from the distro:

```bash
pkg="$(cd template/pi-siana && pwd -P)"
(cd "$SIANA_HOME" && pi install -l -a "$pkg")
```

One settings entry, and exactly one. Pi identifies a local package by its resolved
absolute path, so the same package reached through two spellings - a symlink and its
target, or a moved checkout and its old location - is two packages to pi, and every
resource in it is discovered twice. `just init` reconciles the entry rather than
appending one, which is what keeps `/captain-report` from being registered twice.
