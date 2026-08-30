#!/usr/bin/env python3
"""A `gh` and a `glab` that answer without a network or a credential.

`siana-publish` is the one command in this fleet that leaves it, so the half of it
that matters most - what actually reaches a forge - was the half no test could see.
Everything was asserted through `--dry-run` or through a refusal, and a dry run is
the path that deliberately does not call these.

So this stands in for both CLIs, dispatching on the name it was invoked as, and
records every call. It holds merge requests in a file, which is what makes the two
questions this exists for answerable: that a second run opens no second merge
request, and that the one already open ends up carrying the copy QA accepted.

It is not a mock of the forge. It is a mock of the two commands, which is all
`siana-publish` ever touches, and it refuses an invocation it does not recognise
rather than answering one - a fake that shrugged at a command nobody meant to send
would make the suite green about a call that never worked.

`FAKE_FORGE` is where it keeps state, as one list of merge requests in the shape
below. A test seeds that file directly for work this suite did not open here - a
request the fleet published in an earlier round, which is what a repair lands on.

    {"branch": ..., "base": ..., "title": ..., "body": ...,
     "url": ..., "state": "open" | "closed" | "merged", "head": <sha>}

`state` and `head` are stored once, in one spelling, and rendered per client on the
way out: github says `OPEN` and `headRefOid`, gitlab says `opened` and `sha`, and a
publish that read either one wrong would advance the branch of a request nobody is
reading again.

`FAKE_FORGE_FAIL` names a subcommand it should refuse, which is how the paths that
handle a forge saying no are reached. `FAKE_FORGE_OUT` makes `list` print that string
instead of JSON, which is how the paths that handle a client answering something
unparseable are reached - a login page, or an error object where a list was expected.
"""

import json
import os
import sys

HOST = "https://forge.example/demo/demo"


def state_path(name):
    return os.path.join(os.environ["FAKE_FORGE"], name)


def load():
    try:
        with open(state_path("prs.json")) as fh:
            return json.load(fh)
    except OSError:
        return []


def save(prs):
    with open(state_path("prs.json"), "w") as fh:
        json.dump(prs, fh)


def record(argv):
    with open(state_path("calls.jsonl"), "a") as fh:
        fh.write(json.dumps(argv) + "\n")


def flags(argv):
    """`--name value` pairs, and the bare words before and between them.

    Both CLIs are called with fully spelled options by `siana-publish`, so this does
    not need to know either one's grammar; it needs to not silently lose an argument
    that was passed. A flag with no value is recorded as True."""
    named, bare, i = {}, [], 0
    while i < len(argv):
        if argv[i].startswith("--"):
            if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                named[argv[i][2:]] = argv[i + 1]
                i += 2
            else:
                named[argv[i][2:]] = True
                i += 1
        else:
            bare.append(argv[i])
            i += 1
    return named, bare


def state_of(pr):
    """The one spelling of a request's state. Stored open unless a test says else."""
    return (pr.get("state") or "open").lower()


# What each client calls each field. Rendered on the way out rather than stored twice,
# so a test seeding a request cannot seed one in a shape only one of the two answers.
GH_FIELDS = {"url": lambda pr: pr["url"],
             "state": lambda pr: state_of(pr).upper(),
             "headRefName": lambda pr: pr["branch"],
             "headRefOid": lambda pr: pr.get("head") or "",
             "baseRefName": lambda pr: pr.get("base") or "",
             "title": lambda pr: pr.get("title") or "",
             "body": lambda pr: pr.get("body") or ""}

GLAB_FIELDS = {"web_url": lambda pr: pr["url"],
               "state": lambda pr: state_of(pr),
               "source_branch": lambda pr: pr["branch"],
               "sha": lambda pr: pr.get("head") or "",
               "target_branch": lambda pr: pr.get("base") or "",
               "title": lambda pr: pr.get("title") or "",
               "description": lambda pr: pr.get("body") or ""}


def rendered(cli, pr, asked):
    """One request as the client prints it.

    `gh` prints exactly the fields `--json` named, and nothing else: a caller that
    forgot to ask for one gets a KeyError of its own rather than a value this fake
    volunteered. `glab` has no such flag and prints the whole object.

    An unknown field name is refused rather than dropped. `gh` itself refuses one,
    and a fake that shrugged would make this suite green about a field name the real
    client has never heard of."""
    if cli == "glab":
        return {name: read(pr) for name, read in GLAB_FIELDS.items()}
    if not asked or asked is True:
        die("`gh pr list` here is always called with --json")
    wanted = [name for name in str(asked).split(",") if name]
    unknown = [name for name in wanted if name not in GH_FIELDS]
    if unknown:
        die(f"gh knows no such field: {', '.join(unknown)}")
    return {name: GH_FIELDS[name](pr) for name in wanted}


def die(message):
    print(f"fake-forge: {message}", file=sys.stderr)
    raise SystemExit(1)


def main():
    cli = os.path.basename(sys.argv[0])
    argv = sys.argv[1:]
    record([cli, *argv])
    if len(argv) < 2:
        die(f"nothing here calls `{cli}` with {argv!r}")
    noun, verb, rest = argv[0], argv[1], argv[2:]
    if (cli, noun) not in (("gh", "pr"), ("glab", "mr")):
        die(f"nothing here calls `{cli} {noun}`")
    if os.environ.get("FAKE_FORGE_FAIL") == verb:
        die(f"refusing `{cli} {noun} {verb}` because FAKE_FORGE_FAIL says so")

    named, bare = flags(rest)
    prs = load()

    if verb == "list":
        raw = os.environ.get("FAKE_FORGE_OUT")
        if raw is not None:
            print(raw)
            return
        branch = named.get("head") or named.get("source-branch")
        # Every state, or only the open ones. Both clients default to open and both
        # have to be asked for the rest: `--state all` on one, `--all` on the other.
        # A fake that always answered with everything would let a publish that forgot
        # to ask still see a merged request, which is the case it must never miss.
        every = named.get("state") == "all" or named.get("all") is True
        found = [pr for pr in prs if pr["branch"] == branch
                 and (every or state_of(pr) == "open")]
        print(json.dumps([rendered(cli, pr, named.get("json")) for pr in found]))
        return

    if verb == "create":
        branch = named.get("head") or named.get("source-branch")
        if any(pr["branch"] == branch for pr in prs):
            die(f"a merge request for {branch} is already open")
        pr = {"branch": branch,
              "base": named.get("base") or named.get("target-branch"),
              "title": named.get("title"),
              "body": named.get("body") or named.get("description"),
              "url": f"{HOST}/-/merge_requests/{len(prs) + 1}",
              "state": "open",
              # This never sees a push, so it cannot know what the branch is at. Empty
              # is the honest answer and it is a real one: `siana-publish` treats an
              # unknown request head as nothing to compare against rather than as a
              # disagreement with the remote.
              "head": ""}
        prs.append(pr)
        save(prs)
        print(pr["url"])
        return

    # `gh pr edit <branch>` and `glab mr update <branch>`. Both take the branch as a
    # bare word rather than a flag, which is why bare words are parsed at all.
    if verb in ("edit", "update"):
        branch = bare[0] if bare else None
        for pr in prs:
            if pr["branch"] == branch:
                pr["title"] = named.get("title", pr["title"])
                pr["body"] = named.get("body") or named.get("description") or pr["body"]
                save(prs)
                print(pr["url"])
                return
        die(f"no merge request open for {branch}")

    die(f"nothing here calls `{cli} {noun} {verb}`")


if __name__ == "__main__":
    main()
