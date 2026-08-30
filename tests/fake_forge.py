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
     "url": ..., "state": "open" | "closed" | "merged", "head": <sha>,
     "draft": bool, "mergeable": ..., "armed": <method> | "",
     "checks": [{"name": ..., "state": ..., "bucket": ..., "required": bool}]}

`state` and `head` are stored once, in one spelling, and rendered per client on the
way out: github says `OPEN` and `headRefOid`, gitlab says `opened` and `sha`, and a
publish that read either one wrong would advance the branch of a request nobody is
reading again. `armed` is stored the same way and rendered as github's whole
auto-merge record or as gitlab's bool.

A request with no stored `head` takes it from `FAKE_FORGE_ORIGIN`, which names the
bare repository `origin` points at. That is what a forge actually knows, and it is
what makes a push visible here: a request opened by one run and asked about by the
next answers with the head the branch is really at, rather than with whatever the
`create` call happened to record. A stored `head` still wins, so a test that seeds one
is seeding a fact and not a starting point.

The environment scripts everything a real forge would decide on its own:

    FAKE_FORGE_FAIL    a subcommand to refuse, for the paths that handle a client
                       saying no
    FAKE_FORGE_OUT     what `list` prints instead of JSON, for the paths that handle
                       a login page or an error object where a list was expected
    FAKE_FORGE_CHECKS  what `pr checks` prints instead of JSON, for the same reason
    FAKE_FORGE_MOVE    `<verb>:<sha>`: after serving `<verb>`, put every request on
                       that head. A branch that moves while its checks are being read
                       is the one race no single answer from a forge can show, and it
                       is the race the exact-head binding exists for
    FAKE_FORGE_SETTLE  the forge acting on its own: before answering a `list`, merge
                       every armed request whose required checks have all passed. An
                       armed request is a promise the forge keeps later, and without
                       this nothing here could show it ever being kept
"""

import json
import os
import subprocess
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


def head_of(pr):
    """What the branch of this request is at.

    A stored head is a fact a test asserted and wins outright. Without one, the bare
    repository `origin` points at is asked, because that is what a real forge knows
    and it is the only way a push made between two runs of `siana-publish` is visible
    to the second one. With neither, the honest answer is that this does not know."""
    if pr.get("head"):
        return pr["head"]
    origin = os.environ.get("FAKE_FORGE_ORIGIN")
    if not origin:
        return ""
    out = subprocess.run(["git", "-C", origin, "rev-parse", "--verify",
                          f"refs/heads/{pr['branch']}"],
                         capture_output=True, text=True)
    return out.stdout.strip() if out.returncode == 0 else ""


def armed_of(pr):
    """The merge method this request is armed with, or "" when it is not armed."""
    return (pr.get("armed") or "").lower()


def checks_of(pr, required_only):
    """The checks reported on this request. A test that seeds none has a request
    with none, which is a state a real branch is in far more often than it is in
    any other."""
    found = pr.get("checks") or []
    return [c for c in found if c.get("required")] if required_only else found


# What each client calls each field. Rendered on the way out rather than stored twice,
# so a test seeding a request cannot seed one in a shape only one of the two answers.
GH_FIELDS = {"url": lambda pr: pr["url"],
             "state": lambda pr: state_of(pr).upper(),
             "headRefName": lambda pr: pr["branch"],
             "headRefOid": head_of,
             "baseRefName": lambda pr: pr.get("base") or "",
             "isDraft": lambda pr: bool(pr.get("draft")),
             "mergeable": lambda pr: pr.get("mergeable") or "MERGEABLE",
             # github answers with the whole auto-merge record or with null, and
             # `siana-publish` reads the method out of it. A bool here would let it
             # pass a check it only passes because this fake agreed with it.
             "autoMergeRequest": lambda pr: (
                 {"enabledAt": "2026-08-30T00:00:00Z",
                  "mergeMethod": armed_of(pr).upper()} if armed_of(pr) else None),
             "title": lambda pr: pr.get("title") or "",
             "body": lambda pr: pr.get("body") or ""}

GLAB_FIELDS = {"web_url": lambda pr: pr["url"],
               "state": lambda pr: state_of(pr),
               "source_branch": lambda pr: pr["branch"],
               "sha": head_of,
               "target_branch": lambda pr: pr.get("base") or "",
               "draft": lambda pr: bool(pr.get("draft")),
               "detailed_merge_status": lambda pr: pr.get("mergeable") or "mergeable",
               "auto_merge_enabled": lambda pr: bool(armed_of(pr)),
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


def settle(prs):
    """The forge keeping an armed promise, between one call and the next.

    Every required check green and the request armed is exactly the condition the
    forge merges on, so this merges it - which is the state a re-run of the publish
    has to report rather than open a second request for. A failed or still-running
    check leaves it open and armed, which is the other half: arming is not merging,
    and nothing here may read it as proof that a merge happened."""
    changed = False
    for pr in prs:
        if not armed_of(pr) or state_of(pr) != "open":
            continue
        required = checks_of(pr, True)
        if required and all(str(c.get("bucket") or "").lower() == "pass"
                            for c in required):
            pr["state"] = "merged"
            changed = True
    if changed:
        save(prs)


def moved(verb):
    """A branch that moves after this verb was served, if a test asked for one.

    Written after the answer rather than before it, so the run that asked gets the
    truth and the next one gets the race. Nothing else can produce a head that
    changes between two reads inside one command."""
    scripted = os.environ.get("FAKE_FORGE_MOVE")
    if not scripted or ":" not in scripted:
        return
    when, _, head = scripted.partition(":")
    if when != verb:
        return
    prs = load()
    for pr in prs:
        pr["head"] = head
    save(prs)


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
    if os.environ.get("FAKE_FORGE_SETTLE") and verb == "list":
        settle(prs)

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
              # Nothing is recorded here, so `head_of` answers this from the bare
              # repository `origin` points at, which is what a real forge would say
              # and what makes a later push visible. With no `FAKE_FORGE_ORIGIN` it
              # stays empty, which is also a real answer: `siana-publish` treats a
              # head it cannot read as nothing to compare against rather than as a
              # disagreement with the remote.
              "head": ""}
        prs.append(pr)
        save(prs)
        print(pr["url"])
        return

    # `gh pr checks <branch> --required --json name,state,bucket`. The real client
    # exits nonzero both when a required check has failed and when the branch has no
    # check at all, and prints a list only in the first case, so both halves of that
    # are modelled: an exit code a caller could read as a verdict, and a stdout that
    # is the only thing separating the two.
    if verb == "checks":
        raw = os.environ.get("FAKE_FORGE_CHECKS")
        if raw is not None:
            print(raw)
            raise SystemExit(1)
        branch = bare[0] if bare else None
        pr = next((pr for pr in prs if pr["branch"] == branch), None)
        if pr is None:
            die(f"no pull requests found for branch \"{branch}\"")
        # A branch with no check at all is the client's own error, and a branch
        # whose checks are all optional is an empty list. Two different answers, and
        # `siana-publish` has to keep them apart: one is a protection rule that
        # requires nothing, the other is indistinguishable from a client that could
        # not be asked.
        if not checks_of(pr, False):
            die(f"no checks reported on the '{branch}' branch")
        found = checks_of(pr, named.get("required") is True)
        print(json.dumps([{name: c.get(name) for name in
                           str(named.get("json") or "").split(",") if name}
                          for c in found]))
        # 8 is what `gh` exits while a check is still running, and 1 when one has
        # failed. Both are answers with a list in them, so a caller that read the
        # exit code as a verdict would be wrong about both.
        buckets = {str(c.get("bucket") or "").lower() for c in found}
        if buckets & {"fail", "cancel"}:
            raise SystemExit(1)
        if "pending" in buckets:
            raise SystemExit(8)
        return

    # `gh pr merge <branch> --auto --squash --match-head-commit <sha>`, and the
    # `--disable-auto` that takes it back. The head is compared rather than recorded,
    # because refusing a head that has moved is the whole of what the real flag is
    # for and a fake that stored it would make the suite green about a binding the
    # forge never enforced.
    if verb == "merge":
        branch = bare[0] if bare else None
        pr = next((pr for pr in prs if pr["branch"] == branch
                   and state_of(pr) == "open"), None)
        if pr is None:
            die(f"no open pull request found for branch \"{branch}\"")
        if named.get("disable-auto") is True:
            # `FAKE_FORGE_STICKY` accepts the call and changes nothing, which is what
            # a write the forge lost - or one something re-armed underneath - looks
            # like from here. A cancel that trusted the exit code would report this
            # as done and send the captain on to remove the grant.
            if not os.environ.get("FAKE_FORGE_STICKY"):
                pr["armed"] = ""
                save(prs)
            print(f"Auto-merge disabled for pull request {pr['url']}")
            return
        if named.get("auto") is not True:
            die("nothing here calls `pr merge` without --auto or --disable-auto")
        wanted = named.get("match-head-commit")
        if not wanted:
            die("nothing here calls `pr merge --auto` without --match-head-commit")
        if wanted != head_of(pr):
            die(f"Pull request is not mergeable: head is {head_of(pr)}, not {wanted}")
        methods = [m for m in ("merge", "squash", "rebase") if named.get(m) is True]
        if len(methods) != 1:
            die(f"one merge method is required, got {methods}")
        pr["armed"] = methods[0]
        save(prs)
        print(f"Auto-merge enabled for pull request {pr['url']}")
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
    try:
        main()
    finally:
        # After the answer, whatever the answer was. A refusal is a served call too,
        # and a race scripted onto one has to happen for the same reason.
        if len(sys.argv) > 2:
            moved(sys.argv[2])
