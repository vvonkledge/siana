# Handoff

This is the merge request. Not a summary of one and not a note towards one: what
you write here is the title and the body a human reads on the forge, and nothing
else about this work travels there.

Whoever reads it has not read your brief, was not told what this fleet is, and is
deciding whether to merge. Write for them. Past tense, about what the change does
and what it costs, never about the task that produced it.

Nothing in it may point into this fleet's own home: not the resolved path, not
`$SIANA_HOME`, and not `~/.siana`. A reviewer cannot follow any of those, and what
they hold was written for SIANA rather than for a reader. Say where a thing is in
words instead.

Write it after your last commit, because the head below binds this copy to that
commit and publication refuses a copy that describes an older one. Then check it:

    siana-handoff "$SIANA_TASK_ID" --head "$(git rev-parse HEAD)"

The title is what the merge request is called: one line, an outcome rather than an
operation, and specific enough to tell a stranger what changed and why it matters.

    title  {TITLE}
    head   {HEAD}

## Intent

<!-- The problem, what it cost while it stood, and why this change exists at all.
     A reviewer who does not accept this section is not going to be persuaded by
     the diff. -->

## Solution

<!-- What the change does, and the one design choice a reviewer would otherwise
     have to reconstruct from the diff: what you chose, and what you rejected to
     get there. -->

## Validation

<!-- What you ran, what it said, and enough for someone else to run it again. The
     independent review that comes after yours is added when this is published, so
     it is not yours to write here. -->

## Hotspots

<!-- Where review attention is worth most. Name the file, behaviour, migration or
     edge case, and say why that one repays a careful look. Never the list of
     everything you touched: a list of the whole diff directs nobody. -->

## Risks and boundaries

<!-- What this trades away, what risk is left standing after it, and what it
     deliberately does not do. -->
