"""What happens to a repair of work that is already published.

The regression this exists for is pull requests #15 and #16 of this repository. #15
was opened from a ship branch and its CI failed. The repair was briefed correctly,
cut from that branch, validated and accepted - and then published as a second pull
request, because publishing knew only the branch the QA task named. #16 carried #15's
four commits plus the repair, merged, and GitHub marked #15 merged too because its old
head had become reachable from `main`, with its only recorded check still red. One
piece of work, two review threads, and the duplicate came from publication identity
rather than from the forge or from the fix branch.

So the fixture below is that shape end to end: an original branch with one open red
request, a repair branch descended from it, a QA verdict on the repair, and a
publication that has to reach the request that is already open. Everything in it is
real except the forge client, which is `fake_forge.py`: `origin` is a bare repository
on this machine whose URL says which forge it is, so a push is a real push and a
fast-forward is git's own answer, with no credential and no network anywhere.

The copy is real too. A repair rewrites the description on the request it lands on,
because after the push the commits under that description are the repair's, so these
drive `siana-handoff` and the real handoff documents rather than asserting on what
was printed.
"""

import hashlib
import json
import os
import shutil
import stat
import unittest

from advisory import PROPOSAL
from helpers import HomeTest, script

publish = script("siana-publish")


HANDOFF = """# Handoff

    title  {title}
    head   {head}

## Intent

{intent}

## Solution

The flow carries its own types now, so a caller that reads a field the producer
stopped writing fails where it is written rather than three hops downstream.

## Validation

The suite covers an empty flow, one record, and a record carrying every optional
field. Each case is asserted against the shape a consumer actually reads.

## Hotspots

The optional fields. A producer that omits one and a consumer that requires it are
both valid on their own, and only the pair is wrong.

## Risks and boundaries

Nothing here makes the shape stable across versions, and the untyped path is left
in place for callers that have not moved.
"""


class Fleet(HomeTest):
    """A published request, and a repair of it that a QA minion has accepted.

    Driven through the real commands and the real queue rather than assembled as
    store lines: what is being defended is that briefing a repair and publishing it
    agree with each other, and two fixtures agreeing with the suite's own idea of a
    brief would say nothing about that.
    """

    FORGE = "github"
    SHIP = "make-the-flow-typed"
    FIX = "repair-the-ci-failure"
    URL = "https://github.com/demo/demo/pull/15"

    def setUp(self):
        super().setUp()
        self.contract("projects")
        self.template("brief-ship.md", "brief-qa.md", "handoff.md")
        self.queue()

        # `origin` is a bare repository here, at a path naming the forge it stands
        # for. That is the whole of what `siana-publish` reads a forge out of, so
        # both clients can be exercised against a real push with no credential.
        self.origin = self.at(f"{self.FORGE}.com", "demo", "demo.git")
        os.makedirs(os.path.dirname(self.origin))
        self.run_git("init", "-q", "--bare", self.origin, cwd=self.home)

        self.repo = self.at("repo")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main", ".")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.commit("a.txt", "base", "chore: base")
        self.git("remote", "add", "origin", self.origin)
        self.git("push", "-q", "origin", "main")

        self.forge = self.at("forge")
        os.makedirs(self.forge)
        self.clients = self.fake_forge()

        self.project("demo", path=self.repo, ship="just test", qa="echo ok",
                     target="main")

        # The work that was published, and the request it was published on.
        self.add(self.SHIP, "Make the flow typed", base="main")
        self.assertAccepted(self.brief(self.SHIP, "--ship", "--type", "feat"))
        self.fill(self.SHIP)
        self.ship_branch = f"siana/feat/{self.SHIP}"
        self.git("checkout", "-q", "-b", self.ship_branch, "main")
        self.published = self.commit("b.txt", "typed", "feat: type the flow")
        self.branch_at(f"siana/qa-{self.SHIP}", self.ship_branch)
        self.git("checkout", "-q", "main")
        self.git("push", "-q", "origin", self.ship_branch)
        self.write_handoff(self.SHIP, self.published, "Carry the flow's own types",
                           "The flow was a bag of strings, so every consumer of it "
                           "guessed, and each guessed differently.")
        self.finish(self.SHIP)
        self.finish(f"qa-{self.SHIP}")

        # The repair, cut from that branch, validated and judged on its own.
        self.add(self.FIX, "Repair the CI failure", base=self.ship_branch)
        self.assertAccepted(self.brief(self.FIX, "--ship", "--type", "fix",
                                       "--repairs", self.SHIP))
        self.fill(self.FIX)
        self.fix_branch = f"siana/fix/{self.FIX}"
        self.git("checkout", "-q", "-b", self.fix_branch, self.ship_branch)
        self.accepted = self.commit("c.txt", "fixed", "fix: make CI hermetic")
        self.branch_at(f"siana/qa-{self.FIX}", self.fix_branch)
        self.git("checkout", "-q", "main")
        self.write_handoff(self.FIX, self.accepted, "Type the flow, and keep CI green",
                           "The flow was a bag of strings and the check that proved "
                           "it reached for a network it does not have.")
        self.finish(self.FIX)
        self.finish(f"qa-{self.FIX}")

        self.seed([self.request()])

    # -- fixtures ---------------------------------------------------------------

    def run_git(self, *args, cwd=None):
        out = self.run_cmd(["git", *args], cwd=cwd or self.repo)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out.stdout.strip()

    def git(self, *args):
        return self.run_git("-C", self.repo, *args)

    def commit(self, name, text, message):
        with open(os.path.join(self.repo, name), "w") as fh:
            fh.write(text + "\n")
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def branch_at(self, name, start):
        """A branch made where a dispatch would make it, without checking it out.

        A QA worktree is cut from the branch it judges, so `siana/qa-<id>` standing
        at that head is what says which head the verdict is about."""
        self.git("branch", name, start)

    def head(self, ref):
        return self.git("rev-parse", ref)

    def add(self, task_id, title, base):
        out = self.run_cmd(["tasks", "--file", self.at("tasks.jsonl"), "add", title,
                            "--project", "demo", "--verify", "true",
                            "--base", base, "--cwd", self.repo])
        self.assertAccepted(out)
        self.assertIn(f"id: {task_id}", out.stdout)

    def finish(self, task_id):
        """A task taken to `done` through the queue, verify and all.

        `SIANA_TASK_ID` is set for the verify rather than left to the environment:
        a QA verify names its own report through it, and this suite runs inside a
        minion that has one of its own."""
        reports = self.at("reports")
        os.makedirs(reports, exist_ok=True)
        with open(os.path.join(reports, f"{task_id}.md"), "w") as fh:
            fh.write("what was run, and what held up.\n")
        argv = ["tasks", "--file", self.at("tasks.jsonl")]
        self.assertAccepted(self.run_cmd(
            argv + ["start", task_id, "--owner", "claude@w1:p1", "--cwd", self.repo]))
        self.assertAccepted(self.run_cmd(
            argv + ["done", task_id, "--reason", "accepted"],
            env={"SIANA_TASK_ID": task_id}))

    def brief(self, *args):
        return self.run_bin("siana-brief", *args)

    def fill(self, task_id):
        """The brief as SIANA leaves it, with the placeholders written over.

        A brief still carrying them is a contract a minion refuses, and the branch
        and the repair record are read straight out of this file."""
        path = self.at("briefs", f"{task_id}.md")
        with open(path) as fh:
            text = fh.read()
        for marker, prose in (("{TASK}", f"Do the work of {task_id}."),
                              ("{DONE}", "The suite passes on a clean runner."),
                              ("{BACKGROUND}", "CI reads the installed distro."),
                              ("{SCOPE}", "Do not touch the workflow file.")):
            text = text.replace(marker, prose)
        with open(path, "w") as fh:
            fh.write(text)

    def write_handoff(self, task_id, head, title, intent):
        """The copy the minion that did the work wrote.

        A repair has one of its own, and it is what the request ends up carrying, so
        it is part of a well-formed repair rather than something one test adds."""
        os.makedirs(self.at("handoffs"), exist_ok=True)
        with open(self.at("handoffs", f"{task_id}.md"), "w") as fh:
            fh.write(HANDOFF.format(title=title, head=head, intent=intent))

    def fake_forge(self):
        """`gh` and `glab`, earlier on PATH than any real one."""
        bindir = self.at("fakebin")
        os.makedirs(bindir)
        fake = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fake_forge.py")
        for name in ("gh", "glab"):
            os.symlink(fake, os.path.join(bindir, name))
        return bindir

    def request(self, branch=None, head=None, state=None, url=None):
        """One request on the published branch, as the fake keeps them."""
        return {"branch": branch or self.ship_branch,
                "base": "main",
                "title": "Carry the flow's own types",
                "body": "## Intent\n\nThe flow was a bag of strings.\n",
                "url": url or self.URL,
                "state": state or "open",
                "head": self.published if head is None else head}

    def seed(self, requests):
        """What the forge already holds. Written straight into the fake's own state,
        because this is work an earlier round published and not work this test
        opened."""
        with open(os.path.join(self.forge, "prs.json"), "w") as fh:
            json.dump(requests, fh)

    def prs(self):
        try:
            with open(os.path.join(self.forge, "prs.json")) as fh:
                return json.load(fh)
        except OSError:
            return []

    def publish(self, *args, task=None, **env):
        e = {"PATH": self.distro_path(self.clients), "FAKE_FORGE": self.forge}
        e.update(env)
        return self.run_bin("siana-publish", task or f"qa-{self.FIX}", *args, env=e)

    def asked(self):
        """Every argv the forge client was called with."""
        path = os.path.join(self.forge, "calls.jsonl")
        if not os.path.isfile(path):
            return []
        with open(path) as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def remote(self, branch):
        out = self.git("ls-remote", "origin", f"refs/heads/{branch}")
        return out.split()[0] if out.strip() else None

    # -- assertions -------------------------------------------------------------

    def assertNoRequestOpened(self):
        """No second request, asserted over what reached the client and over what
        the forge holds afterwards. The client is the only way to make one at all,
        so a run that never called `create` opened nothing, and the request that was
        already there keeps its number and its target either way."""
        for argv in self.asked():
            self.assertNotEqual(argv[2:3], ["create"],
                                f"a second request was opened: {argv}")
        held = self.prs()
        self.assertEqual(len(held), 1, held)
        self.assertEqual(held[0]["url"], self.URL)
        self.assertEqual(held[0]["base"], "main")

    def assertNothingTouched(self):
        """Every call was a listing. What a refusal must leave behind: no push, and
        no word of the repair on the request either."""
        for argv in self.asked():
            self.assertEqual(argv[2:3], ["list"],
                             f"the forge was asked to do something: {argv}")

    def assertRemoteUnmoved(self):
        self.assertEqual(self.remote(self.ship_branch), self.published,
                         "the published branch was advanced anyway")

    def assertLocalUntouched(self):
        """The two branches this may never write to. The local copy of the published
        branch is where the work it repairs was left, and the repair branch is the
        head a pipeline run recorded and a QA minion read."""
        self.assertEqual(self.head(self.ship_branch), self.published)
        self.assertEqual(self.head(self.fix_branch), self.accepted)


class Advancing(Fleet):
    """The regression itself: the repair reaches the request that is already open."""

    def test_the_open_request_receives_the_accepted_head(self):
        text = self.assertAccepted(self.publish())
        self.assertEqual(self.remote(self.ship_branch), self.accepted)
        self.assertIn("advanced", text)
        self.assertIn("pull/15", text)

    def test_no_second_request_is_opened(self):
        # What #16 was.
        self.assertAccepted(self.publish())
        self.assertNoRequestOpened()

    def test_the_request_carries_the_repairs_own_copy(self):
        # After the push the commits under that description are the repair's, so the
        # description is the repair minion's. The one it replaces described a
        # different set of commits and said nothing about the failure.
        self.assertAccepted(self.publish())
        pr = self.prs()[0]
        self.assertEqual(pr["title"], "Type the flow, and keep CI green")
        self.assertIn("reached for a network it does not have", pr["body"])
        self.assertIn("Independently reviewed and accepted by a second agent",
                      pr["body"])
        self.assertIn(f"Shipped by `{self.FIX}`, accepted by `qa-{self.FIX}`.",
                      pr["body"])

    def test_the_repair_branch_is_not_published_on_its_own(self):
        # It is the minion's and the QA minion's, and a remote copy of it is where a
        # second request would be opened from next time.
        self.assertAccepted(self.publish())
        self.assertIsNone(self.remote(self.fix_branch))

    def test_neither_local_branch_is_moved(self):
        # The pipeline recorded the repair branch's head and `check` compares the
        # two, so moving it here would turn a finished task red.
        self.assertAccepted(self.publish())
        self.assertLocalUntouched()

    def test_the_published_work_is_now_anchored_outside_the_fleet(self):
        # `siana-retire` frees a worktree once a commit exists outside `siana/*`
        # locally. The push updates the remote-tracking ref, so the repair's commits
        # are anchored by the request they landed on.
        self.assertAccepted(self.publish())
        self.assertEqual(self.head(f"refs/remotes/origin/{self.ship_branch}"),
                         self.accepted)

    def test_it_stops_before_merging_and_says_which_request_holds_the_repair(self):
        text = self.assertAccepted(self.publish())
        self.assertIn(self.URL, text)
        self.assertIn("merging is still the captain's", text)

    def test_running_it_again_changes_nothing(self):
        self.assertAccepted(self.publish())
        # The forge has caught up by then, which is the ordinary second run.
        self.seed([self.request(head=self.accepted)])
        text = self.assertAccepted(self.publish())
        self.assertIn("already advanced", text)
        self.assertEqual(self.remote(self.ship_branch), self.accepted)
        self.assertNoRequestOpened()

    def test_running_it_again_before_the_forge_has_caught_up(self):
        # The branch is already where this would push it, so the end state holds
        # whatever the forge's own record of the head has caught up to.
        self.assertAccepted(self.publish())
        text = self.assertAccepted(self.publish())
        self.assertIn("already advanced", text)

    def test_a_dry_run_describes_it_and_changes_nothing(self):
        text = self.assertAccepted(self.publish("--dry-run"))
        self.assertIn(f"repairs: {self.SHIP}", text)
        self.assertIn(self.ship_branch, text)
        self.assertIn(self.accepted[:12], text)
        self.assertIn("fast-forward", text)
        self.assertIn("Type the flow, and keep CI green", text)
        self.assertRemoteUnmoved()
        self.assertNothingTouched()

    def test_a_dry_run_after_the_advance_says_there_is_nothing_to_push(self):
        self.assertAccepted(self.publish())
        text = self.assertAccepted(self.publish("--dry-run"))
        self.assertIn("already at", text)
        self.assertIn("nothing would be pushed", text)

    def test_a_dry_run_without_the_client_still_says_what_it_would_do(self):
        # CI has neither client, and a dry run has to stay readable there.
        text = self.assertAccepted(self.publish("--dry-run", PATH="/usr/bin:/bin"))
        self.assertIn(self.ship_branch, text)
        self.assertIn("is not installed here", text)
        self.assertRemoteUnmoved()

    def test_a_second_repair_lands_on_the_same_request(self):
        """A repair of a repair. The chain is resolved when the brief is written, so
        publication still has one branch to advance and one request to find."""
        self.assertAccepted(self.publish())

        second = "repair-it-once-more"
        self.add(second, "Repair it once more", base=self.fix_branch)
        self.assertAccepted(self.brief(second, "--ship", "--type", "fix",
                                       "--repairs", self.FIX))
        self.git("checkout", "-q", "-b", f"siana/fix/{second}", self.fix_branch)
        again = self.commit("d.txt", "more", "fix: again")
        self.branch_at(f"siana/qa-{second}", f"siana/fix/{second}")
        self.git("checkout", "-q", "main")
        self.write_handoff(second, again, "Type the flow, and keep CI green twice",
                           "The first repair left one path still reaching out.")
        self.finish(second)
        self.finish(f"qa-{second}")

        self.seed([self.request(head=self.accepted)])
        text = self.assertAccepted(self.publish(task=f"qa-{second}"))
        self.assertIn("advanced", text)
        self.assertEqual(self.remote(self.ship_branch), again)
        self.assertNoRequestOpened()
        self.assertEqual(self.prs()[0]["title"],
                         "Type the flow, and keep CI green twice")


class Interrupted(Fleet):
    """The push and the copy are two calls, so a run can end between them.

    The order is deliberate: commits arrive under a stale description rather than a
    description arriving for commits that are not there. Both halves are asserted,
    because what makes the choice safe is that the second run converges from it.
    """

    FAIL = "edit"

    def test_a_run_that_dies_after_the_push_says_what_is_still_wrong(self):
        out = self.publish(FAKE_FORGE_FAIL=self.FAIL)
        self.assertRefused(out, "refused to update the merge request",
                           "carrying copy from an earlier run")
        # The commits are there. That is the half-done state this leaves on purpose.
        self.assertEqual(self.remote(self.ship_branch), self.accepted)
        self.assertEqual(self.prs()[0]["title"], "Carry the flow's own types")

    def test_running_it_again_finishes_the_job_and_pushes_nothing(self):
        self.assertRefused(self.publish(FAKE_FORGE_FAIL=self.FAIL))
        pushes = len([a for a in self.asked() if a[2:3] == ["create"]])
        text = self.assertAccepted(self.publish())
        self.assertIn("already advanced", text)
        self.assertIn("copy   updated", text)
        self.assertEqual(self.prs()[0]["title"], "Type the flow, and keep CI green")
        self.assertEqual(self.remote(self.ship_branch), self.accepted)
        self.assertEqual(pushes, 0)
        self.assertNoRequestOpened()

    def test_a_stale_copy_on_an_already_advanced_request_is_repaired(self):
        # The same convergence reached the other way: somebody advanced the branch
        # by hand, or an earlier run did and its copy never arrived.
        self.git("push", "-q", "origin",
                 f"{self.accepted}:refs/heads/{self.ship_branch}")
        self.seed([self.request(head=self.accepted)])
        text = self.assertAccepted(self.publish())
        self.assertIn("already advanced", text)
        self.assertEqual(self.prs()[0]["title"], "Type the flow, and keep CI green")


class NoForce(Fleet):
    """Nothing in the push path can force, and nothing user-controlled can make it.

    The branch it pushes at is named in a brief, which is text SIANA writes, so the
    refspec is the one place a value from outside this command reaches git's own
    argv. `siana-publish` builds it out of a full sha and a branch name the reader
    has already matched whole, and this watches the argv to say so."""

    def setUp(self):
        super().setUp()
        self.gitlog = self.at("git.log")
        self.shim = self.at("gitshim")
        os.makedirs(self.shim)
        real = shutil.which("git")
        path = os.path.join(self.shim, "git")
        with open(path, "w") as fh:
            fh.write("#!/usr/bin/env python3\n"
                     "import json, os, sys\n"
                     f"with open({self.gitlog!r}, 'a') as fh:\n"
                     "    fh.write(json.dumps(sys.argv[1:]) + '\\n')\n"
                     f"os.execv({real!r}, [{real!r}, *sys.argv[1:]])\n")
        os.chmod(path, os.stat(path).st_mode | stat.S_IXUSR)

    def git_argv(self):
        with open(self.gitlog) as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def pushes(self):
        return [argv for argv in self.git_argv() if "push" in argv]

    def test_the_push_is_one_plain_fast_forward(self):
        self.assertAccepted(self.publish(
            PATH=self.distro_path(self.shim, self.clients)))
        pushes = self.pushes()
        self.assertEqual(len(pushes), 1, pushes)
        argv = pushes[0]
        self.assertEqual(argv, ["-C", self.repo, "push", "origin",
                                f"{self.accepted}:refs/heads/{self.ship_branch}"])

    def test_no_force_reaches_git_on_any_path(self):
        # Over every git call the command makes, not the push alone: a lease or a
        # `+` refspec anywhere in a publish is the same failure.
        self.assertAccepted(self.publish(
            PATH=self.distro_path(self.shim, self.clients)))
        for argv in self.git_argv():
            for word in argv:
                self.assertNotIn("--force", word)
                self.assertNotIn("--mirror", word)
                self.assertFalse(word.startswith("+"), argv)


class UnderAnAdvisorySession(Fleet):
    """A repair while a session is in force: nothing leaves, and the ledger says why.

    `siana-publish` asks the gate above every check about this machine, so a gated
    run never reaches either publication path. That is asserted here rather than
    assumed, because a repair pushes at a branch the captain is already reading, and
    a second copy of the gate living down in the repair path is exactly the copy that
    would go stale without anything noticing.
    """

    def setUp(self):
        super().setUp()
        self.contract("decisions")
        with open(self.at("principles.md"), "wb") as fh:
            fh.write(b"# Principles\n\nPublish what two minions accepted.\n")
        with open(self.at("principles.md"), "rb") as fh:
            policy = hashlib.sha256(fh.read()).hexdigest()
        with open(self.at("afk"), "w") as fh:
            json.dump({"state": "running", "pid": 1,
                       "command": "python3 /nowhere/bin/siana-afk",
                       "started": "2026-08-29T20:00:00Z",
                       "until": "2099-01-01T00:00:00Z",
                       "policy": self.at("principles.md"), "sha256": policy,
                       "allow": [], "projects": ["demo"]}, fh)
        proposal = dict(PROPOSAL)
        proposal["action"] = f"siana-publish qa-{self.FIX}"
        self.proposal = self.at("record.json")
        with open(self.proposal, "w") as fh:
            json.dump(proposal, fh)

    def test_the_branch_is_not_advanced_and_the_proposal_is_recorded(self):
        out = self.publish("--record", self.proposal)
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertRemoteUnmoved()
        # Not one call: the forge is never asked what is open from that branch,
        # because the gate is above every question about this machine.
        self.assertEqual(self.asked(), [])
        with open(self.at("decisions.jsonl")) as fh:
            rec = json.loads(fh.read().strip().splitlines()[-1])
        self.assertEqual(rec["verdict"], "refused")
        self.assertEqual(rec["action"], f"siana-publish qa-{self.FIX}")

    def test_it_refuses_without_a_record(self):
        out = self.publish()
        self.assertRefused(out, "needs --record")
        self.assertRemoteUnmoved()
        self.assertEqual(self.asked(), [])

    def test_a_dry_run_is_exempt_and_still_describes_the_repair(self):
        # It changes nothing and records nothing, so it stays readable on a night
        # nothing may be published on.
        text = self.assertAccepted(self.publish("--dry-run"))
        self.assertIn(f"repairs: {self.SHIP}", text)
        self.assertIn("fast-forward", text)
        self.assertRemoteUnmoved()
        self.assertFalse(os.path.exists(self.at("decisions.jsonl")))


class Gitlab(Fleet):
    """The same publication, driven through the other forge's client.

    Not the whole of `Advancing` again. What differs between the two forges is the
    argv this builds and the field names it reads back, and `fake_forge.py` answers
    each client in its own vocabulary, so one advance and one state that is spelt
    differently on each side are what those cost to defend. Everything else in a
    publish is git and the queue, which are the same either way.
    """

    FORGE = "gitlab"
    URL = "https://gitlab.com/demo/demo/-/merge_requests/15"

    def test_the_open_request_receives_the_accepted_head(self):
        text = self.assertAccepted(self.publish())
        self.assertEqual(self.remote(self.ship_branch), self.accepted)
        self.assertIn("merge_requests/15", text)
        self.assertNoRequestOpened()

    def test_the_client_is_asked_about_one_branch_and_every_state(self):
        # The argv itself, because a listing that forgot `--all` would read a merged
        # request as no request, and one that forgot the source branch would answer
        # about somebody else's.
        self.assertAccepted(self.publish())
        listed = [a for a in self.asked() if a[2:3] == ["list"]]
        self.assertEqual(len(listed), 1, self.asked())
        self.assertEqual(listed[0], ["glab", "mr", "list", "--source-branch",
                                     self.ship_branch, "--all", "--output", "json"])

    def test_the_copy_reaches_it_in_the_other_clients_words(self):
        self.assertAccepted(self.publish())
        updated = [a for a in self.asked() if a[2:3] == ["update"]]
        self.assertEqual(len(updated), 1, self.asked())
        self.assertIn("--description", updated[0])
        self.assertEqual(self.prs()[0]["title"], "Type the flow, and keep CI green")

    def test_a_merged_request_is_refused(self):
        # gitlab says `merged` where github says MERGED, and reading either wrong
        # advances the branch of a request nobody will read again.
        self.seed([self.request(state="merged")])
        out = self.publish()
        self.assertRefused(out, "is not open")
        self.assertRemoteUnmoved()


class GitlabInterrupted(Interrupted):
    """The half-done state and its recovery on the other client, whose update verb
    is spelt differently and would otherwise never be exercised failing."""

    FORGE = "gitlab"
    URL = "https://gitlab.com/demo/demo/-/merge_requests/15"
    FAIL = "update"


class Github(Fleet):
    """What the github client is actually asked, with no credential anywhere."""

    def test_the_client_is_asked_about_one_branch_and_every_state(self):
        self.assertAccepted(self.publish())
        listed = [a for a in self.asked() if a[1:2] == ["pr"] and a[2:3] == ["list"]]
        self.assertEqual(len(listed), 1, self.asked())
        self.assertEqual(listed[0],
                         ["gh", "pr", "list", "--head", self.ship_branch,
                          "--state", "all", "--json",
                          "url,state,headRefName,headRefOid"])

    def test_the_copy_reaches_it_as_an_edit_of_the_published_branch(self):
        self.assertAccepted(self.publish())
        edited = [a for a in self.asked() if a[2:3] == ["edit"]]
        self.assertEqual(len(edited), 1, self.asked())
        self.assertEqual(edited[0][:4], ["gh", "pr", "edit", self.ship_branch])


class Refusals(Fleet):
    """Every way this could push at something nobody accepted, or nobody reads.

    All of them are asserted against the remote ref as well as the exit code: a
    refusal that has already pushed is not a refusal.
    """

    def test_no_request_has_ever_been_opened(self):
        self.seed([])
        out = self.publish()
        self.assertRefused(out, "no merge request has ever been opened")
        self.assertRemoteUnmoved()
        self.assertNothingTouched()

    def test_a_request_from_another_branch_is_not_this_one(self):
        # The client is asked about one branch, and what comes back is filtered on
        # the same branch here: a client that widened its own matching would hand
        # back somebody else's request with nothing to notice.
        self.seed([self.request(branch="siana/feat/something-else")])
        out = self.publish()
        self.assertRefused(out, "no merge request has ever been opened")
        self.assertRemoteUnmoved()

    def test_more_than_one_open_request(self):
        self.seed([self.request(), self.request(url="https://elsewhere/2")])
        out = self.publish()
        self.assertRefused(out, "more than one open request")
        self.assertRemoteUnmoved()
        self.assertNothingTouched()

    def test_a_closed_request(self):
        self.seed([self.request(state="closed")])
        out = self.publish()
        self.assertRefused(out, "is not open")
        self.assertRemoteUnmoved()

    def test_a_merged_request(self):
        # The one that started this: #15 was marked merged with a red check on it.
        # Advancing a merged request's branch pushes work nobody will read again.
        self.seed([self.request(state="merged")])
        out = self.publish()
        self.assertRefused(out, "is not open")
        self.assertRemoteUnmoved()

    def test_a_client_that_cannot_answer(self):
        out = self.publish(FAKE_FORGE_FAIL="list")
        self.assertRefused(out, "could not say what is open", "FAKE_FORGE_FAIL")
        self.assertRemoteUnmoved()

    def test_a_client_answering_something_that_is_not_json(self):
        out = self.publish(FAKE_FORGE_OUT="<html>login</html>")
        self.assertRefused(out, "not JSON")
        self.assertRemoteUnmoved()

    def test_a_client_answering_json_that_is_not_a_list_of_requests(self):
        out = self.publish(FAKE_FORGE_OUT='{"message": "Not Found"}')
        self.assertRefused(out, "not a list of requests")
        self.assertRemoteUnmoved()

    def test_the_client_is_not_installed(self):
        out = self.publish(PATH="/usr/bin:/bin")
        self.assertRefused(out, "is not installed")
        self.assertRemoteUnmoved()

    def test_the_remote_source_branch_is_gone(self):
        self.git("push", "-q", "origin", "--delete", self.ship_branch)
        out = self.publish()
        self.assertRefused(out, "origin has no", self.ship_branch)
        self.assertIsNone(self.remote(self.ship_branch))

    def test_the_branch_moved_under_the_request(self):
        # Somebody pushed to it. What QA accepted was judged against a branch that
        # is not the one this would advance.
        moved = self.commit_on(self.ship_branch, "e.txt", "theirs", "fix: theirs")
        self.git("push", "-q", "origin", f"{moved}:refs/heads/{self.ship_branch}")
        out = self.publish()
        self.assertRefused(out, "has moved since")
        self.assertEqual(self.remote(self.ship_branch), moved)

    def test_a_head_that_is_not_a_fast_forward(self):
        # The remote holds a commit the accepted head does not, so this would drop
        # work that is on the request already.
        moved = self.commit_on(self.ship_branch, "e.txt", "theirs", "fix: theirs")
        self.git("push", "-q", "origin", f"{moved}:refs/heads/{self.ship_branch}")
        self.seed([self.request(head=moved)])
        out = self.publish()
        self.assertRefused(out, "is not a fast-forward")
        self.assertEqual(self.remote(self.ship_branch), moved)

    def test_a_repair_branch_that_moved_after_the_verdict(self):
        """`siana/qa-<id>` stands where the branch was when QA was cut from it, and
        QA lands nothing, so a difference is the repair branch having moved.

        Two refusals stand between that and the request, and the handoff's is the
        first: a commit made after the verdict is also a commit the copy was written
        before. Rewriting the copy is the obvious next move and it is not a way
        through - the verdict is about a head no minion has read, and that is what
        the second refusal says. Both are driven here, in the order an operator
        meets them, because a test that only proved the first would pass just as
        well with the second one deleted."""
        self.git("checkout", "-q", self.fix_branch)
        moved = self.commit("f.txt", "after", "fix: after the verdict")
        self.git("checkout", "-q", "main")

        stale = self.publish()
        self.assertRefused(stale, "describes", "and the branch is at")
        self.assertRemoteUnmoved()

        self.write_handoff(self.FIX, moved, "Type the flow, and keep CI green",
                           "Rewritten against the commit that came after.")
        out = self.publish()
        self.assertRefused(out, "judged", "no independent minion has read")
        self.assertRemoteUnmoved()
        self.assertNothingTouched()

    def test_a_verdict_whose_branch_is_no_longer_there(self):
        self.git("branch", "-D", f"siana/qa-{self.FIX}")
        out = self.publish()
        self.assertRefused(out, f"has no branch siana/qa-{self.FIX}")
        self.assertRemoteUnmoved()

    def test_a_handoff_the_repair_branch_has_moved_past(self):
        # The copy this would put on the captain's own request describes a commit
        # that is not the one being pushed, so it stops before either happens.
        self.write_handoff(self.FIX, "0" * 40, "Type the flow, and keep CI green",
                           "Written against an earlier commit.")
        out = self.publish()
        self.assertRefused(out, "describes 000000000000")
        self.assertRemoteUnmoved()
        self.assertNothingTouched()

    def test_no_handoff_at_all(self):
        os.remove(self.at("handoffs", f"{self.FIX}.md"))
        out = self.publish()
        self.assertRefused(out, "no handoff for")
        self.assertRemoteUnmoved()
        self.assertNothingTouched()

    def test_a_minion_still_sitting_on_the_branch_this_would_advance(self):
        # A push at a branch somebody is committing to leaves that minion diverged
        # from the request it is working on, and it finds out at its own publish.
        worktree = self.at("wt", "live")
        self.git("worktree", "add", "-q", worktree, self.ship_branch)
        self.add("hold-the-branch", "Hold the branch", base="main")
        self.assertAccepted(self.run_cmd(
            ["tasks", "--file", self.at("tasks.jsonl"), "start", "hold-the-branch",
             "--owner", "claude@w9:p9", "--cwd", worktree]))
        out = self.publish()
        self.assertRefused(out, "is checked out by hold-the-branch")
        self.assertRemoteUnmoved()
        self.assertNothingTouched()

    def test_a_minion_on_another_branch_is_not_in_the_way(self):
        worktree = self.at("wt", "elsewhere")
        self.git("worktree", "add", "-q", "-b", "siana/feat/elsewhere", worktree,
                 "main")
        self.add("work-elsewhere", "Work elsewhere", base="main")
        self.assertAccepted(self.run_cmd(
            ["tasks", "--file", self.at("tasks.jsonl"), "start", "work-elsewhere",
             "--owner", "claude@w9:p9", "--cwd", worktree]))
        self.assertAccepted(self.publish())
        self.assertEqual(self.remote(self.ship_branch), self.accepted)

    def commit_on(self, branch, name, text, message):
        """A commit somebody else made on a branch, without disturbing this one."""
        self.git("checkout", "-q", branch)
        head = self.commit(name, text, message)
        self.git("checkout", "-q", "main")
        self.git("branch", "-f", branch, f"{head}~1")
        return head


class Ordinary(Fleet):
    """Publication without a repair record, which is every other piece of work."""

    def test_it_opens_a_request_of_its_own(self):
        # The original ship task's own verdict. Its brief records no repair, so this
        # is the path that pushes the branch and opens a request from it.
        self.assertIsNone(publish.repair_record(self.home, self.SHIP))
        self.git("push", "-q", "origin", "--delete", self.ship_branch)
        self.seed([])
        text = self.assertAccepted(self.publish(task=f"qa-{self.SHIP}"))
        self.assertIn("opened", text)
        self.assertEqual(self.remote(self.ship_branch), self.published)
        created = [argv for argv in self.asked() if argv[2:3] == ["create"]]
        self.assertEqual(len(created), 1, self.asked())
        self.assertEqual(self.prs()[0]["title"], "Carry the flow's own types")

    def test_an_existing_request_is_reported_and_not_opened_twice(self):
        text = self.assertAccepted(self.publish(task=f"qa-{self.SHIP}"))
        self.assertIn("already open", text)
        self.assertNoRequestOpened()


if __name__ == "__main__":
    unittest.main()
