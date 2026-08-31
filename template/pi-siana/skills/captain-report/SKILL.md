---
name: captain-report
description: Write the captain's fleet report. Reads the live queue, registry, git and forge, watcher, herdr, obligations, cleanup runs and decision history through `siana-report`, then presents outcomes, health, risks, publication and cleanup state, open obligations, and every decision the captain has to take, each with its options, consequences, SIANA's recommendation and why. Use whenever the captain asks how the fleet is, what happened, what is stuck, or what they need to decide.
---

# The captain's report

The captain has been away. They want to know what happened, what is stuck, and what
they have to decide. Everything below is in service of the last one: a report that
tells them how the fleet is and leaves them with no decision in front of them has
described a fleet nobody is steering.

## Read the world. Never your own memory

Run this first, always:

    siana-report --json

Your session remembers what it did. It does not know what happened: a minion that
finished mid-turn, a watcher that died an hour ago, a publication that was never
opened. Those are invisible to recollection and obvious to a read. Nothing in this
report may come from what you remember doing.

`siana-report` gathers and judges nothing. Every source it returns is in one of three
states, and the difference between two of them is the difference between a true
report and a comfortable one:

- `read` - the source answered, and `data` is what it said.
- `empty` - the source answered and holds nothing. A real zero. Say so plainly.
- `unavailable` - the source could not be read. `why` says what failed.

**An unavailable source is named in the report, in its own section, every time.**
Never render it as healthy, never render it as empty, and never quietly leave it out.
"Herdr is not answering, so I cannot say what workspaces are open" is a finding the
captain needs. "No open workspaces" when herdr is down is a lie you told them.

You may read more once you have this. `siana-owe history` is the decision corpus,
`siana-clean status` is any cleanup run in flight, and a project's own repository will
answer questions this does not ask. Read what you need to answer a question you
actually have. Do not go and re-derive what `siana-report` already returned.

## What the report says

Write it as prose with headings, for a captain reading on a phone. No tables of
identifiers, no dumps of the JSON, and no paragraph that exists to show that you
looked.

**1. What happened.** Outcomes, not activity. What landed, what was accepted and what
was rejected. A minion that ran for three hours and produced nothing is not an
outcome; say that it produced nothing.

A task the queue holds as `blocked` does not belong here. It is work waiting on an
answer, so it is actionable and it goes under risk or under what the captain has to
decide - reporting it as something that already happened is how a live blocker stops
being read as one. Resolved findings are a different thing, they live in their own
store, and they are read from that store rather than reconstructed from the queue.
If that store is absent or unreadable, say so as an unavailable source exactly as you
would for any other: filling the gap from the queue would put settled work back in
front of the captain as though it were still open.

**2. Health.** Is the fleet moving. Whether the watcher is running, whether anything
is dispatched, whether anything has been `doing` far longer than its shape should
take. A dead minion appends no record, so a task `doing` with an owner nothing knows
about is the thing to look for and say.

**3. Risk.** What is about to go wrong, in the captain's terms. Work that exists in
exactly one place, a queue that is blocked behind one question, a project whose forge
cannot be reached so nothing can be published.

**4. Publication and cleanup.** What is waiting to be published, what is waiting on
QA, what worktrees and branches are left over, and what a cleanup run is doing or
stopped on. If a cleanup run has a pending question, that question belongs in section
6 and not here.

**5. What is owed.** Open obligations, oldest first, because the oldest is the one
that has been forgotten. Say what is still owed and to whom; do not restate what has
been answered.

**6. What the captain has to decide.** The section the report exists for.

## Decisions, and the line under them

For each pending decision, give all four:

- the **options**, as things the captain can actually choose between
- the **consequence** of each, including the one that is irreversible
- **what SIANA recommends**
- **why** - the reasoning, not a restatement of the recommendation

Separate the two kinds and never let them run together:

**Active blockers** are decisions something is currently waiting on. Say what is
waiting and since when.

**Superseded history** is what was decided before and has since been overtaken.
Present it as history. A resolved decision that reads like an open one puts the
captain back into a question they already answered.

**A recommendation is not authority.** Say what you would do; never write as though
it is going to happen unless they stop you. Nothing in this distro turns a
recommendation into an action, and the report must not be the first thing that reads
like it does.

## Capture the decision before and after

This is what makes the fleet's decisions a corpus rather than a memory, and it is
half of what this skill is for.

**Before** you put a decision in front of the captain, record it:

    siana-owe decision "<the question, one line>" \
        --situation "<what made this a decision>" \
        --option "<one thing they can choose>" \
        --consequence "<what follows from it>" \
        --option "<another>" \
        --consequence "<what follows from that>" \
        --recommend "<the option you would choose, exactly as written above>" \
        --because "<why>" \
        [--task <id>] [--project <handle>]

It refuses fewer than two options, a mismatched consequence list, and a
recommendation that is not one of the options. Those refusals are the point: a
decision recorded without them is not one a later reader could learn anything from.

**After** the captain answers, record what they said, in their words:

    siana-owe close <id> --answer "<what the captain decided>"

The obligation holds the answer. The reasoning record holds everything else, keyed by
the same id, so there is exactly one place the captain's answer lives.

**Later**, when the answer has been carried out and you can see how it went:

    siana-owe outcome <id> --outcome "<what actually happened>"

It refuses while the obligation is still open, because an outcome recorded before an
answer is a guess, and a guess in a learning corpus teaches the wrong thing.

`siana-owe history` reads the whole corpus back, and `--json` is the form a program
would read. It exists so that "how often did SIANA and the captain agree, and about
what" is a question with an answer. That measurement is the only ground an argument
for more autonomy could ever stand on. It is not itself a grant of any, and no
command anywhere turns a row of it into an action.

## What this report never does

- Never act on anything while writing it. It is a read.
- Never answer a captain-only question because the recommendation is obvious.
- Never present an unread source as a healthy one.
- Never say the fleet is tidy while something is refused, stuck or unpublished. Idle
  is not success.
