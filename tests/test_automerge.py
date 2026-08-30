"""What the `automerge` grant lets SIANA arrange, and everything it still refuses.

The grant is the first thing in this fleet that can put work into a project's default
branch without the captain typing the command. So every test here is either a
condition that must hold before a merge is arranged at all, or a way the arrangement
could end up bound to work nobody accepted.

Three properties carry most of it, and they are worth naming because the rest of the
file is instances of them:

- **Cumulative, never substitutable.** A local pipeline green, an independent QA
  green at that same commit, and the forge's own required checks on that same commit
  are three separate gates. The grant is permission to act once all three hold, not
  permission to treat any one of them as the others.
- **Bound to a commit and not to a branch.** The accepted head is compared against
  the request, against the remote, and against the request again after the checks
  are read, and is then passed to the forge so it compares too. A branch that moves
  at any point in that window is work an independent minion never read.
- **Reversible, and honestly so.** What a forge holds outlives this process and the
  registry, so removing the field cannot retract it. Cancelling is its own command,
  it proves what it did, and it needs no grant to run.
"""

import json
import os
import subprocess
import unittest

from helpers import HomeTest, script
from test_repair import Fleet

publish = script("siana-publish")


HANDOFF = """# Handoff

    title  {title}
    head   {head}

## Intent

{intent}

## Solution

The flag prints one object per task, from the same records the table is built from,
so the two cannot disagree about what a task is.

## Validation

`just test` covers it against an empty queue, one task, and a task carrying every
optional field.

## Hotspots

The empty queue prints `[]` and not nothing, which is the case the old table got
wrong.

## Risks and boundaries

The table is unchanged and stays the default.
"""


PASSING = [{"name": "suite", "status": "passed"},
           {"name": "review", "status": "passed"}]


BRIEF = """# Brief

## Delivery: ship

Your work lands. This branch is the deliverable:

    branch  siana/feat/add-a-json-flag

## The task

Print one object per task from `status`, so callers stop parsing a table.

## Done when

`status --json` prints one object per task.
"""


class Contract(HomeTest):
    """The store contract, which is where a mistyped grant is refused.

    Driven through `datafile` rather than asserted about the YAML, because what
    matters is what the registry accepts from the captain, and a test reading the
    contract file would only agree with itself about that."""

    def setUp(self):
        super().setUp()
        self.contract("projects")

    def put(self, handle, **fields):
        args = [f"handle={handle}", f"path={self.home}"]
        args += [f"{k}={v}" for k, v in fields.items()]
        return self.run_cmd(["datafile", "-f", self.at("projects.jsonl"),
                             "-c", self.at("schema-projects.yaml"), "put",
                             *sum((["--set", a] for a in args), [])])

    def test_the_three_methods_are_accepted(self):
        for method in ("merge", "squash", "rebase"):
            with self.subTest(method=method):
                self.assertAccepted(self.put(f"p-{method}", automerge=method))

    def test_a_method_no_forge_has_is_refused_at_the_write(self):
        # The failure this prevents: a value nothing knows how to spell reaching the
        # publisher, which would then either guess a method or refuse every publish
        # in that project long after the typo was made.
        for bad in ("fast-forward", "Squash", "auto", "true"):
            with self.subTest(bad=bad):
                self.assertRefused(self.put("p", automerge=bad), "automerge")

    def test_absence_is_the_rule_every_project_had_before_this(self):
        self.assertAccepted(self.put("plain"))
        out = self.run_cmd(["datafile", "-f", self.at("projects.jsonl"),
                            "-c", self.at("schema-projects.yaml"), "get", "plain"])
        self.assertIn("automerge: null", self.assertAccepted(out))

    def test_the_field_does_not_erase_the_records_written_before_it(self):
        """A field added to a live contract that every existing record fails is
        growth that one `datafile compact` turns into an empty store. This one is
        optional, so the same compaction is a no-op, and that is asserted rather
        than reasoned about."""
        self.assertAccepted(self.put("one"))
        self.assertAccepted(self.put("two", automerge="rebase"))
        self.assertAccepted(self.run_cmd(
            ["datafile", "-f", self.at("projects.jsonl"),
             "-c", self.at("schema-projects.yaml"), "compact"]))
        listed = self.assertAccepted(self.run_cmd(
            ["datafile", "-f", self.at("projects.jsonl"),
             "-c", self.at("schema-projects.yaml"), "list"]))
        self.assertIn("one", listed)
        self.assertIn("two", listed)


class Granted(HomeTest):
    """A project that grants automerge, and one ship task a second minion accepted.

    Everything is real: the queue, the briefs, the repository, the bare `origin` a
    push actually lands in, and the pipeline record a run would have written. Only
    the two forge clients are faked, and they are faked as commands rather than as a
    forge, so what is asserted is what left this machine.
    """

    FORGE = "github"
    SHIP = "add-a-json-flag"
    METHOD = "squash"
    URL = "https://github.com/demo/demo/pull/7"

    # The checks a request carries when a test does not say otherwise: one required,
    # still running. Pending is the state a request is in when arming is worth doing
    # at all, so it is the default rather than a special case.
    CHECKS = [{"name": "ci / test", "state": "IN_PROGRESS", "bucket": "pending",
               "required": True},
              {"name": "ci / optional", "state": "SUCCESS", "bucket": "pass",
               "required": False}]

    def setUp(self):
        super().setUp()
        self.contract("projects")

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

        self.register()

        self.branch = f"siana/feat/{self.SHIP}"
        self.git("checkout", "-q", "-b", self.branch, "main")
        self.accepted = self.commit("b.txt", "json", "feat: print json")
        self.git("checkout", "-q", "main")
        self.brief(self.SHIP, BRIEF)
        self.tasks()
        self.write_handoff(self.SHIP, self.accepted)
        self.write_run(self.SHIP, branch=self.branch, head=self.accepted)

        self.seed([self.request()])

    # -- the world --------------------------------------------------------------

    def register(self, **over):
        fields = {"path": self.repo, "ship": "just test", "qa": "echo ok",
                  "target": "main", "pipeline": "true", "automerge": self.METHOD}
        fields.update(over)
        fields = {k: v for k, v in fields.items() if v is not None}
        self.project("demo", **fields)

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

    def brief(self, task_id, text):
        """The brief SIANA wrote, as a literal.

        Written rather than scaffolded through `siana-brief`. That command and this
        publisher agreeing about a branch name is `test_repair`'s guarantee and is
        driven there end to end; what is under test here is a publisher reading a
        home, so a home built out of files is the same home and several seconds
        cheaper per test."""
        os.makedirs(self.at("briefs"), exist_ok=True)
        with open(self.at("briefs", f"{task_id}.md"), "w") as fh:
            fh.write(text)

    def tasks(self):
        """The ship task and the verdict behind it, as raw store lines.

        The queue is append-only and read by folding it, so a line written here is a
        record the publisher cannot tell from one `tasks` wrote. What it reads off
        these is a status, a base, a project and a dependency, and nothing else."""
        self.store("tasks.jsonl",
                   {"id": self.SHIP, "title": "Add a JSON flag", "status": "done",
                    "verify": "siana-pipeline check", "verify_kind": "cmd",
                    "deps": [], "context": [], "project": "demo",
                    "updated": "2026-08-30T09:00:00Z"},
                   {"id": f"qa-{self.SHIP}", "title": f"QA {self.SHIP}",
                    "status": "done", "verify": "echo ok", "verify_kind": "cmd",
                    "deps": [self.SHIP], "context": [], "project": "demo",
                    "base": self.branch, "updated": "2026-08-30T10:00:00Z"})

    def write_handoff(self, task_id, head, title="Print one task per line"):
        os.makedirs(self.at("handoffs"), exist_ok=True)
        with open(self.at("handoffs", f"{task_id}.md"), "w") as fh:
            fh.write(HANDOFF.format(
                title=title, head=head,
                intent="`status` printed a table nobody could parse, so every "
                       "caller that wanted one field grew its own fragile `awk`."))

    def write_run(self, task_id, branch, head, verdict="passed"):
        """The record `siana-pipeline run` leaves behind.

        Written here rather than earned by running the pipeline, because a real run
        starts a reviewing agent. What the grant reads out of it is three fields, and
        those three are what this seeds."""
        os.makedirs(self.at("pipeline"), exist_ok=True)
        with open(self.at("pipeline", f"{task_id}.json"), "w") as fh:
            json.dump({"task": task_id, "branch": branch, "head": head,
                       "base": "main", "at": "2026-08-30T09:00:00Z",
                       "verdict": verdict, "steps": PASSING}, fh)

    def fake_forge(self):
        bindir = self.at("fakebin")
        os.makedirs(bindir)
        fake = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fake_forge.py")
        for name in ("gh", "glab"):
            os.symlink(fake, os.path.join(bindir, name))
        return bindir

    def request(self, **over):
        """The request an earlier run of this command opened.

        Seeded with no head of its own, so the fake answers it out of the bare
        repository `origin` points at - which is what a forge would say, and what
        makes the push this publish is about visible to the arming step after it."""
        rec = {"branch": self.branch, "base": "main", "url": self.URL,
               "title": "Print one task per line",
               "body": "## Intent\n\nA table nobody could parse.\n",
               "state": "open", "checks": self.CHECKS}
        rec.update(over)
        return rec

    def seed(self, requests):
        with open(os.path.join(self.forge, "prs.json"), "w") as fh:
            json.dump(requests, fh)

    def prs(self):
        try:
            with open(os.path.join(self.forge, "prs.json")) as fh:
                return json.load(fh)
        except OSError:
            return []

    def publish(self, *args, task=None, **env):
        e = {"PATH": self.distro_path(self.clients),
             "FAKE_FORGE": self.forge, "FAKE_FORGE_ORIGIN": self.origin}
        e.update(env)
        return self.run_bin("siana-publish", task or f"qa-{self.SHIP}", *args, env=e)

    def asked(self):
        path = os.path.join(self.forge, "calls.jsonl")
        if not os.path.isfile(path):
            return []
        with open(path) as fh:
            return [json.loads(line) for line in fh if line.strip()]

    # -- assertions -------------------------------------------------------------

    def assertArmed(self, method=None):
        held = self.prs()
        self.assertEqual(len(held), 1, held)
        self.assertEqual(held[0].get("armed"), method or self.METHOD, held)

    def assertNotArmed(self):
        for pr in self.prs():
            self.assertFalse(pr.get("armed"), f"a merge was arranged: {pr}")

    def assertNothingMerged(self):
        """No call that could arm, cancel or merge anything reached a client.

        Asserted over the calls rather than over the state, because a run that made
        the call and had it refused left the same state as one that never made it,
        and those are not the same fact."""
        for argv in self.asked():
            self.assertNotIn(argv[2:3], ([\
                "merge"], ["accept"]), f"a merge call was made: {argv}")

    def merge_calls(self):
        return [argv for argv in self.asked() if argv[2:3] == ["merge"]]


class Arming(Granted):
    """The grant doing what it was set to do."""

    def test_it_publishes_and_arms_the_merge(self):
        text = self.assertAccepted(self.publish())
        self.assertIn("armed    squash", text)
        self.assertIn(self.URL, text)
        self.assertArmed()

    def test_the_forge_is_pinned_to_the_accepted_head_and_the_chosen_method(self):
        # The whole of the binding, as it reached the client. `--match-head-commit`
        # is what makes the forge refuse a head that moved after this call, which is
        # the window nothing on this machine can watch.
        self.publish()
        call = self.merge_calls()[0]
        self.assertEqual(call[:4], ["gh", "pr", "merge", self.branch])
        self.assertIn("--auto", call)
        self.assertIn("--squash", call)
        self.assertEqual(call[call.index("--match-head-commit") + 1], self.accepted)

    def test_the_method_reaches_the_forge_as_the_captain_wrote_it(self):
        for method in ("merge", "squash", "rebase"):
            with self.subTest(method=method):
                self.register(automerge=method)
                self.seed([self.request()])
                self.assertAccepted(self.publish())
                self.assertArmed(method)
                self.assertIn(f"--{method}", self.merge_calls()[-1])

    def test_the_report_says_which_checks_are_holding_it(self):
        # Named and not counted: "1 required check" reads the same whether it is
        # green or three minutes from failing, and which one it is is the reason a
        # captain reads this line at all.
        text = self.assertAccepted(self.publish())
        self.assertIn("ci / test (pending)", text)
        self.assertNotIn("ci / optional", text)

    def test_arming_is_not_merging_and_the_report_says_so(self):
        text = self.assertAccepted(self.publish())
        self.assertIn("when every required check and review passes", text)
        self.assertEqual(self.prs()[0]["state"], "open")

    def test_a_second_run_reports_and_arms_nothing_twice(self):
        self.assertAccepted(self.publish())
        armed = len(self.merge_calls())
        text = self.assertAccepted(self.publish())
        self.assertIn("already armed", text)
        self.assertEqual(len(self.merge_calls()), armed,
                         "a second run armed it again")
        self.assertArmed()

    def test_a_run_after_the_forge_merged_it_reports_and_opens_nothing(self):
        """The state a re-run finds once the grant has done its work.

        Without this the merged request reads as a branch with no open request, and
        the next line of the publisher opens a second one for commits that have
        already landed."""
        self.assertAccepted(self.publish())
        self.seed([self.request(state="merged", head=self.accepted, armed="squash")])
        text = self.assertAccepted(self.publish())
        self.assertIn("already merged", text)
        self.assertEqual(len(self.prs()), 1)
        for argv in self.asked():
            self.assertNotEqual(argv[2:3], ["create"], f"a request was opened: {argv}")

    def test_a_request_armed_with_another_method_is_re_armed_with_this_one(self):
        # The captain changed the field. What is armed at the forge is the old
        # answer, and the registry is the only place the current one lives.
        self.seed([self.request(armed="merge")])
        self.assertAccepted(self.publish())
        self.assertArmed("squash")

    def test_the_pipeline_record_and_the_verdict_are_both_required(self):
        # The composition, from the other side: the grant does not stand in for
        # either gate, so the run that arms had to have both.
        self.assertAccepted(self.publish())
        self.assertArmed()
        self.assertTrue(os.path.isfile(self.at("pipeline", f"{self.SHIP}.json")))


class WhatTheForgeDoesWithIt(Granted):
    """Arming is a promise the forge keeps, or does not.

    The fake acts on its own between calls here, which is the only way to show the
    end the whole feature exists for - and the only way to show that arming is not
    it. Nothing in this fleet may read an armed request as proof a merge happened."""

    def publish(self, *args, **env):
        return super().publish(*args, FAKE_FORGE_SETTLE="1", **env)

    def state(self):
        return [pr["state"] for pr in self.prs()]

    def test_a_pending_check_arms_and_does_not_merge(self):
        self.assertAccepted(self.publish())
        self.assertArmed()
        self.assertEqual(self.state(), ["open"])

    def test_the_forge_merges_it_once_its_required_checks_go_green(self):
        self.assertAccepted(self.publish())
        self.seed([self.request(head=self.accepted, armed="squash", checks=[
            {"name": "ci / test", "state": "SUCCESS", "bucket": "pass",
             "required": True}])])
        text = self.assertAccepted(self.publish())
        self.assertIn("already merged", text)
        self.assertEqual(self.state(), ["merged"])

    def test_a_check_that_fails_after_arming_leaves_it_open(self):
        self.assertAccepted(self.publish())
        self.seed([self.request(head=self.accepted, armed="squash", checks=[
            {"name": "ci / test", "state": "FAILURE", "bucket": "fail",
             "required": True}])])
        out = self.publish()
        self.assertEqual(self.state(), ["open"])
        self.assertArmed()
        # Still armed, and still not merged. A re-run says the state rather than
        # arming again, and the failed check is on the line a captain reads.
        self.assertIn("already armed", self.assertAccepted(out))
        self.assertIn("ci / test (fail)", out.stdout)


class TheGrantIsNotEnough(Granted):
    """Everything about this registry that stops a merge being arranged at all.

    All of it is local, so all of it refuses before the push. A project carrying the
    grant with a prerequisite missing publishes nothing rather than publishing and
    quietly arming nothing, because the second reads afterwards exactly like the
    grant having been honoured."""

    def assertRefusedBeforeThePush(self, out, *fragments):
        text = self.assertRefused(out, *fragments)
        self.assertNotArmed()
        self.assertEqual(self.asked(), [], "the forge was called anyway")
        self.assertEqual(
            self.run_cmd(["git", "-C", self.repo, "ls-remote", "origin",
                          f"refs/heads/{self.branch}"]).stdout.strip(), "",
            "the branch was pushed anyway")
        return text

    def test_a_project_with_no_driven_pipeline(self):
        self.register(pipeline="false")
        self.assertRefusedBeforeThePush(
            self.publish(), "not validated by a driven pipeline",
            "records no commit to bind to")

    def test_a_project_that_does_not_publish_at_all(self):
        # `target` is what turns publishing on, and there is nothing to arrange the
        # merge of where nothing is published. Refused as it always was, before the
        # grant is looked at.
        self.register(target=None)
        self.assertRefusedBeforeThePush(self.publish(), "publishing is off for demo")

    def test_a_project_with_no_independent_qa(self):
        self.register(qa=None)
        self.assertRefusedBeforeThePush(
            self.publish(), "has no `qa` command",
            "delegates arranging a merge, not deciding one")

    def test_no_pipeline_run_was_ever_recorded(self):
        os.remove(self.at("pipeline", f"{self.SHIP}.json"))
        self.assertRefusedBeforeThePush(
            self.publish(), "no passing pipeline run", "there is no run recorded at")

    def test_a_pipeline_run_that_did_not_pass(self):
        self.write_run(self.SHIP, branch=self.branch, head=self.accepted,
                       verdict="failed")
        self.assertRefusedBeforeThePush(self.publish(), "did not pass (failed)")

    def test_a_pipeline_record_nothing_can_read(self):
        with open(self.at("pipeline", f"{self.SHIP}.json"), "w") as fh:
            fh.write("{not json")
        self.assertRefusedBeforeThePush(self.publish(), "cannot be read")

    def test_a_pipeline_run_that_validated_a_commit_the_branch_moved_off(self):
        """The one the whole design turns on. A run validates one commit; a branch
        that moved after it carries work this project's own rigor never saw, and the
        grant is permission to merge what that rigor accepted."""
        self.write_run(self.SHIP, branch=self.branch, head="0" * 40)
        self.assertRefusedBeforeThePush(
            self.publish(), "validated 000000000000", "the branch moved after the run")

    def test_a_pipeline_run_recorded_against_another_branch(self):
        self.write_run(self.SHIP, branch="siana/feat/something-else",
                       head=self.accepted)
        self.assertRefusedBeforeThePush(
            self.publish(), "validated siana/feat/something-else",
            "a verdict about some other branch")

    def test_a_forge_this_fleet_arranges_no_merges_on(self):
        """The documented boundary. `glab` has no call that cancels an armed merge
        and none that says which checks a project requires, so an arming there could
        not be retracted and an empty answer could not be told from a green."""
        self.git("remote", "set-url", "origin", "git@gitlab.com:demo/demo.git")
        self.assertRefusedBeforeThePush(
            self.publish(), "does not arrange merges on gitlab",
            "cancels an armed merge", "empty answer could not be told from a green")

    def test_a_verdict_that_authorises_nothing_still_refuses_first(self):
        """The grant is a fourth gate and never a replacement for the first three.

        The verdict is put back to `doing` as a raw store line, because the queue
        will not take a task off `done` and what is under test is a publisher
        reading a queue rather than the queue's own transitions."""
        self.store("tasks.jsonl",
                   {"id": f"qa-{self.SHIP}", "title": f"QA {self.SHIP}",
                    "status": "doing", "verify": "echo ok", "verify_kind": "cmd",
                    "deps": [self.SHIP], "context": [], "project": "demo",
                    "base": self.branch, "updated": "2026-08-30T12:00:00Z"})
        self.assertRefusedBeforeThePush(self.publish(), "is doing, not done")


class TheForgeIsNotReady(Granted):
    """Everything at the forge that stops a merge being arranged.

    None of it can be known before the push, so each of these leaves the branch
    published and the request open, and says so. That is the safe half-done state:
    the work is where a human can read it, and merging it is the captain's until
    something arms it."""

    def assertPublishedButNotArmed(self, out, *fragments):
        """A refusal that says which half of the run stood.

        The exit code is nonzero and the publish landed, so the refusal has to say
        so: read as a publish that did not happen, every one of these would send
        somebody looking for a merge request that is sitting there open."""
        text = self.assertRefused(out, *fragments)
        self.assertIn("the publish itself stands", text)
        self.assertIn(self.URL, text)
        self.assertNotArmed()
        self.assertEqual(self.merge_calls(), [], "a merge call was made anyway")
        return text

    def test_a_branch_with_no_check_at_all(self):
        """The client's own error, and the shape a client that could not be asked
        has as well. Both refuse, and the refusal names both, because nothing here
        may read an answer that is not a list of checks as an empty list of them."""
        self.seed([self.request(checks=[])])
        self.assertPublishedButNotArmed(
            self.publish(), "nothing said which checks",
            "no checks reported", "neither is ever a green")

    def test_a_branch_whose_only_checks_are_not_required(self):
        # An empty list, which is a different answer: a protection rule that
        # requires nothing. Still never a green.
        self.seed([self.request(checks=[{"name": "lint", "state": "SUCCESS",
                                         "bucket": "pass", "required": False}])])
        self.assertPublishedButNotArmed(
            self.publish(), "has no required check",
            "a protection rule that requires nothing")

    def test_a_required_check_that_has_already_failed(self):
        self.seed([self.request(checks=[{"name": "ci / test", "state": "FAILURE",
                                         "bucket": "fail", "required": True}])])
        self.assertPublishedButNotArmed(
            self.publish(), "required check that did not pass", "ci / test")

    def test_a_required_check_that_was_cancelled(self):
        self.seed([self.request(checks=[{"name": "ci / test", "state": "CANCELLED",
                                         "bucket": "cancel", "required": True}])])
        self.assertPublishedButNotArmed(self.publish(), "did not pass")

    def test_a_forge_that_will_not_say_which_checks_are_required(self):
        self.assertPublishedButNotArmed(
            self.publish(FAKE_FORGE_FAIL="checks"),
            "nothing said which checks", "not an empty list of them")

    def test_a_forge_answering_something_that_is_not_a_list_of_checks(self):
        # A login page where a list was expected. The client exits zero on some of
        # these, which is why the exit code is not read as a verdict anywhere here.
        self.assertPublishedButNotArmed(
            self.publish(FAKE_FORGE_CHECKS="<html>login</html>"),
            "nothing said which checks")

    def test_a_draft_request(self):
        self.seed([self.request(draft=True)])
        self.assertPublishedButNotArmed(self.publish(), "is a draft")

    def test_a_request_that_conflicts_with_the_target(self):
        self.seed([self.request(mergeable="CONFLICTING")])
        self.assertPublishedButNotArmed(self.publish(), "conflicts with main")

    def test_a_request_that_targets_somewhere_else(self):
        self.seed([self.request(base="release/1.2")])
        self.assertPublishedButNotArmed(
            self.publish(), "targets release/1.2", "would merge somewhere else")

    def test_a_request_standing_at_a_head_nobody_accepted(self):
        self.seed([self.request(head="0" * 40)])
        self.assertPublishedButNotArmed(
            self.publish(), "is at 000000000000", "accepted head is")

    def test_a_branch_that_moved_while_its_checks_were_being_read(self):
        """The race the three head reads exist for.

        A forge answers one question at a time, so between "which checks does this
        branch require" and "arm this" the branch can move. The checks just read then
        describe a commit that is not the one being merged, and no single answer from
        the forge can show that."""
        self.assertPublishedButNotArmed(
            self.publish(FAKE_FORGE_MOVE="checks:" + "b" * 40),
            "moved to bbbbbbbbbbbb while its checks were being read")

    def test_a_remote_branch_somebody_pushed_over(self):
        # Between the verdict and this call. The request may not have caught up yet,
        # so the remote is asked as well as the forge.
        other = self.at("other")
        self.run_git("clone", "-q", self.origin, other, cwd=self.home)
        self.run_git("-C", other, "config", "user.email", "o@example.com",
                     cwd=self.home)
        self.run_git("-C", other, "config", "user.name", "o", cwd=self.home)
        self.run_git("-C", other, "checkout", "-qb", self.branch, cwd=self.home)
        self.run_git("-C", other, "commit", "-q", "--allow-empty", "-m",
                     "chore: someone else", cwd=self.home)
        self.run_git("-C", other, "push", "-q", "origin", self.branch, cwd=self.home)
        # The publish's own push refuses a non-fast-forward, so what is exercised is
        # that nothing was armed on a head an independent minion never read.
        out = self.publish()
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertNotArmed()
        self.assertEqual(self.merge_calls(), [])

    def test_a_forge_that_refuses_the_arming_says_what_is_still_there(self):
        # The one refusal here that does make the arming call. The request is open
        # and holds the accepted head; what failed is only the arrangement, and a
        # captain has to be able to tell that from a publish that did not happen.
        self.assertRefused(self.publish(FAKE_FORGE_FAIL="merge"),
                           "refused to arrange the merge", "nothing is lost")
        self.assertNotArmed()

    def test_a_forge_that_cannot_be_asked_what_is_open(self):
        # It never reaches the arming step: opening the request is what fails first,
        # and nothing is armed on a request this run cannot see.
        self.assertRefused(self.publish(FAKE_FORGE_FAIL="list"))
        self.assertNotArmed()
        self.assertEqual(self.merge_calls(), [])


class WithoutTheGrant(Granted):
    """A project that has not been granted anything, which is every project until
    the captain writes the field.

    The regression this guards is the one a feature like this is most likely to
    cause: work merging somewhere nobody asked for it to."""

    def register(self, **over):
        super().register(automerge=None, **over)

    def test_it_publishes_exactly_as_it_always_did(self):
        text = self.assertAccepted(self.publish())
        self.assertIn("already open", text)
        self.assertIn(self.URL, text)

    def test_nothing_that_could_arm_or_merge_reaches_the_forge(self):
        self.publish()
        self.assertNothingMerged()
        self.assertNotArmed()

    def test_a_request_with_no_required_check_publishes_anyway(self):
        # Every condition the grant adds is a condition on the grant. A project
        # without one is not held to any of them.
        self.seed([self.request(checks=[])])
        self.assertAccepted(self.publish())

    def test_a_project_with_no_pipeline_and_no_qa_publishes_anyway(self):
        self.register(pipeline="false", qa=None)
        os.remove(self.at("pipeline", f"{self.SHIP}.json"))
        self.assertAccepted(self.publish())
        self.assertNothingMerged()

    def test_the_merge_is_still_said_to_be_the_captains(self):
        self.seed([])
        text = self.assertAccepted(self.publish())
        self.assertIn("merging is the captain's, and still done by hand", text)


class DryRun(Granted):
    """What a captain sees before any of it happens."""

    def dry(self, *args, **env):
        return self.assertAccepted(self.publish("--dry-run", *args, **env))

    def test_it_names_the_method_the_head_and_the_checks(self):
        text = self.dry()
        self.assertIn("automerge: squash", text)
        self.assertIn(self.accepted[:12], text)
        self.assertIn("ci / test (pending)", text)
        self.assertIn(self.URL, text)

    def test_it_says_whether_the_merge_would_be_armed(self):
        self.assertIn("would arm squash", self.dry())

    def test_it_says_why_it_would_not_be(self):
        self.seed([self.request(checks=[{"name": "lint", "state": "SUCCESS",
                                         "bucket": "pass", "required": False}])])
        text = self.dry()
        self.assertIn("would not arm: ", text)
        self.assertIn("has no required check", text)

    def test_a_request_the_forge_already_merged_claims_nothing_about_checks(self):
        """The state a dry run is most likely to be asked about, since this is where
        SIANA is pointed to look at what the grant has done.

        Nothing asked the forge about checks - the merge has happened - so a line
        saying its answer was unreadable would be a complaint about a question that
        was never put, with nothing under it to explain the claim."""
        self.seed([self.request(state="merged", head=self.accepted, armed="squash")])
        text = self.dry()
        self.assertIn("it is merged already", text)
        self.assertNotIn("checks", text.split("automerge:")[1])
        self.assertNotIn("would not arm", text)

    def test_a_check_it_could_not_read_is_not_printed_as_a_green(self):
        self.seed([self.request(checks=[])])
        text = self.dry()
        self.assertIn("checks  unreadable here", text)
        self.assertIn("would not arm: ", text)

    def test_it_says_when_something_is_armed_already(self):
        # The pending external state, surfaced where a captain is already looking.
        self.seed([self.request(armed="squash")])
        self.assertIn("armed already with squash", self.dry())

    def test_nothing_is_pushed_armed_or_merged(self):
        self.dry()
        self.assertNothingMerged()
        self.assertNotArmed()
        self.assertEqual(
            self.run_cmd(["git", "-C", self.repo, "ls-remote", "origin",
                          f"refs/heads/{self.branch}"]).stdout.strip(), "")

    def test_a_machine_with_no_client_still_describes_the_publish(self):
        text = self.assertAccepted(self.run_bin(
            "siana-publish", f"qa-{self.SHIP}", "--dry-run",
            env={"PATH": self.path_with_no_forge_client(),
                 "FAKE_FORGE": self.forge}))
        self.assertIn(f"branch:  {self.branch}", text)
        self.assertIn("automerge: squash", text)
        self.assertIn("not installed here", text)

    def test_a_registry_the_grant_cannot_be_honoured_under_still_refuses(self):
        # A dry run changes nothing, and a grant this machine could never honour is
        # a fact about the registry rather than about the run.
        self.register(pipeline="false")
        self.assertRefused(self.publish("--dry-run"),
                           "not validated by a driven pipeline")


class Cancelling(Granted):
    """Taking an arrangement back.

    What a forge holds outlives this process and the registry both, so removing the
    field prevents the next arming and retracts nothing. This is the operation that
    does, and it has to work in the order a revocation actually happens in: cancel,
    check, then remove the field."""

    def armed_publish(self):
        self.assertAccepted(self.publish())
        self.assertArmed()

    def cancel(self, *args, **env):
        return self.publish("--cancel-automerge", *args, **env)

    def test_it_disarms_the_open_request(self):
        self.armed_publish()
        text = self.assertAccepted(self.cancel())
        self.assertIn("cancelled", text)
        self.assertIn(self.URL, text)
        self.assertNotArmed()

    def test_it_says_merging_is_the_captains_again(self):
        self.armed_publish()
        self.assertIn("merging it is the captain's again",
                      self.assertAccepted(self.cancel()))

    def test_it_works_after_the_grant_has_been_removed(self):
        """The load-bearing case. A captain who removed the field first would
        otherwise have no exact way to retract what is already armed, and the safe
        revocation order depends on this working with no grant in the registry."""
        self.armed_publish()
        self.register(automerge=None)
        self.assertAccepted(self.cancel())
        self.assertNotArmed()

    def test_it_works_after_the_target_has_been_removed_as_well(self):
        # Publishing is off for the project by then, and the armed request is not.
        self.armed_publish()
        self.register(automerge=None, target=None)
        self.assertAccepted(self.cancel())
        self.assertNotArmed()

    def test_running_it_twice_is_the_same_answer(self):
        self.armed_publish()
        self.assertAccepted(self.cancel())
        text = self.assertAccepted(self.cancel())
        self.assertIn("nothing armed", text)
        self.assertNotArmed()

    def test_a_request_that_was_never_armed(self):
        self.seed([self.request()])
        self.assertIn("nothing armed", self.assertAccepted(self.cancel()))

    def test_a_branch_with_no_request_at_all(self):
        self.seed([])
        self.assertIn("no request is open", self.assertAccepted(self.cancel()))

    def test_a_cancel_that_did_not_take_is_a_refusal_and_not_a_report(self):
        """The one outcome this command must never produce.

        A cancel reported as done that left the request armed sends the captain on to
        remove the field believing nothing can merge, and something still can. So it
        asks again rather than trusting the client's exit code."""
        self.armed_publish()
        out = self.cancel(FAKE_FORGE_STICKY="1")
        self.assertRefused(out, "is still armed with squash",
                           "cancel it in the forge's own interface")
        self.assertIn("--disable-auto", str(self.merge_calls()))
        self.assertArmed()

    def test_a_forge_that_cannot_be_asked_proves_nothing(self):
        self.armed_publish()
        self.assertRefused(self.cancel(FAKE_FORGE_FAIL="list"),
                           "could not say what is open", "proves nothing")

    def test_a_forge_that_refuses_the_cancel_says_it_may_still_merge(self):
        self.armed_publish()
        self.assertRefused(self.cancel(FAKE_FORGE_FAIL="merge"),
                           "refused to cancel", "may still merge")
        self.assertArmed()

    def test_a_dry_run_is_how_the_captain_asks_what_is_armed(self):
        # The only read-only question about pending external state, so it has to
        # answer both ways round rather than only when there is something to cancel.
        self.armed_publish()
        self.assertIn("armed   squash",
                      self.assertAccepted(self.cancel("--dry-run")))
        self.assertAccepted(self.cancel())
        self.assertIn("nothing armed",
                      self.assertAccepted(self.cancel("--dry-run")))

    def test_a_dry_run_says_what_it_would_disarm_and_disarms_nothing(self):
        self.armed_publish()
        before = len(self.merge_calls())
        text = self.assertAccepted(self.cancel("--dry-run"))
        self.assertIn("would cancel", text)
        self.assertArmed()
        self.assertEqual(len(self.merge_calls()), before)

    def test_a_verdict_that_authorises_nothing_cancels_nothing(self):
        # It is the same authority the publish runs on, so the same refusals hold.
        self.armed_publish()
        out = self.publish("--cancel-automerge", task="nope")
        self.assertRefused(out, "no task nope")


class UnderAnAdvisorySession(Granted):
    """A session in force, which permits nothing.

    The grant is standing authority from the captain's own registry, and a session is
    the captain saying decisions are being written down rather than made. The second
    wins: nothing is pushed, nothing is armed, nothing is cancelled, and what the
    captain reads in the morning is the proposal."""

    def setUp(self):
        super().setUp()
        self.contract("decisions")
        with open(self.at("principles.md"), "w") as fh:
            fh.write("# Principles\n\nPublish what two minions accepted.\n")
        with open(self.at("afk"), "w") as fh:
            json.dump({"state": "running", "pid": 1,
                       "command": "python3 /nowhere/bin/siana-afk",
                       "started": "2026-08-29T20:00:00Z",
                       "until": "2099-01-01T00:00:00Z",
                       "policy": self.at("principles.md"),
                       "sha256": "0" * 64, "allow": [], "projects": ["demo"]}, fh)

    def test_a_publish_arms_nothing_and_is_refused(self):
        out = self.publish()
        self.assertRefused(out, "needs --record")
        self.assertNotArmed()
        self.assertNothingMerged()

    def test_a_cancel_arms_and_disarms_nothing_and_is_refused(self):
        out = self.publish("--cancel-automerge")
        self.assertRefused(out, "needs --record")
        self.assertNothingMerged()

    def test_a_record_does_not_buy_the_arming_either(self):
        with open(self.at("record.json"), "w") as fh:
            json.dump({"action": f"siana-publish qa-{self.SHIP}",
                       "class": "publish", "reversibility": "R2",
                       "confidence": "high",
                       "evidence": [f"qa-{self.SHIP}"],
                       "alternatives": ["wait for the captain"],
                       "principles": ["Publish what two minions accepted."]}, fh)
        out = self.publish("--record", self.at("record.json"))
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertNotArmed()
        self.assertNothingMerged()


class ARepair(Fleet):
    """A repair of published work, under the grant.

    Everything the grant needs is re-established over on the branch the request is
    open from, because that is where the work now is: the request was fast-forwarded
    to the accepted head one call ago, and the checks on it are that head's. A repair
    that armed on the round before it would be arming on a red request.
    """

    METHOD = "squash"
    CHECKS = [{"name": "ci / test", "state": "IN_PROGRESS", "bucket": "pending",
               "required": True}]

    def setUp(self):
        super().setUp()
        self.project("demo", path=self.repo, ship="just test", qa="echo ok",
                     target="main", pipeline="true", automerge=self.METHOD)
        self.write_run(self.FIX, self.fix_branch, self.accepted)
        # No stored head, so the fake answers it out of `origin` - which is what
        # makes the fast-forward this publish is about visible to the arming step
        # after it, exactly as a forge would see it.
        self.seed([self.request(head="", checks=self.CHECKS)])

    def write_run(self, task_id, branch, head, verdict="passed"):
        os.makedirs(self.at("pipeline"), exist_ok=True)
        with open(self.at("pipeline", f"{task_id}.json"), "w") as fh:
            json.dump({"task": task_id, "branch": branch, "head": head,
                       "base": "main", "at": "2026-08-30T09:00:00Z",
                       "verdict": verdict, "steps": PASSING}, fh)

    def publish(self, *args, task=None, **env):
        return super().publish(*args, task=task,
                               FAKE_FORGE_ORIGIN=self.origin, **env)

    def armed(self):
        """What each request the forge holds is armed with, "" for none. Read off
        the fake's own state rather than off the calls, because the question is what
        the forge would act on and not what it was asked."""
        return [pr.get("armed") or "" for pr in self.prs()]

    def merge_calls(self):
        return [argv for argv in self.asked() if argv[2:3] == ["merge"]]

    def test_it_advances_the_request_and_arms_that_head(self):
        text = self.assertAccepted(self.publish())
        self.assertIn("advanced", text)
        self.assertIn("armed    squash", text)
        self.assertEqual(self.armed(), ["squash"])
        self.assertNoRequestOpened()

    def test_the_head_it_pins_is_the_one_the_repair_was_accepted_at(self):
        # And not the head the request held before this call, which is the work that
        # was rejected.
        self.publish()
        call = self.merge_calls()[0]
        self.assertEqual(call[3], self.ship_branch)
        self.assertEqual(call[call.index("--match-head-commit") + 1], self.accepted)
        self.assertNotIn(self.published, call)

    def test_it_reads_the_repairs_own_pipeline_run_and_not_the_work_it_repairs(self):
        """The run that matters is the repair's. The one behind the published work
        validated the commit that was rejected, and a grant honoured on that would
        merge the round that failed."""
        os.remove(self.at("pipeline", f"{self.FIX}.json"))
        self.write_run(self.SHIP, f"siana/feat/{self.SHIP}", self.published)
        out = self.publish()
        self.assertRefused(out, "no passing pipeline run")
        self.assertEqual(self.armed(), [""])
        self.assertEqual(self.remote(self.ship_branch), self.published,
                         "the request was advanced anyway")

    def test_a_repair_whose_run_validated_an_earlier_commit_arms_nothing(self):
        self.write_run(self.FIX, self.fix_branch, self.published)
        out = self.publish()
        self.assertRefused(out, "the branch moved after the run")
        self.assertEqual(self.merge_calls(), [])
        self.assertEqual(self.remote(self.ship_branch), self.published)

    def test_a_run_after_the_forge_merged_it_reports_and_advances_nothing(self):
        """The end state a restart finds, on the repair path.

        Re-running is the documented recovery from a SIANA that restarted between a
        verdict and this call, and after the grant has done its work the request is
        merged. Read as a closed request it refuses, which is safe and describes a
        request that landed as a review thread nobody will read again."""
        self.assertAccepted(self.publish())
        armed = len(self.merge_calls())
        self.seed([self.request(head=self.accepted, state="merged", armed="squash")])
        text = self.assertAccepted(self.publish())
        self.assertIn("already merged", text)
        self.assertIn(self.URL, text)
        self.assertNoRequestOpened()
        self.assertEqual(len(self.merge_calls()), armed,
                         "it went back to the forge about a request that merged")

    def test_a_merged_request_without_the_grant_is_still_a_refusal(self):
        # The refusal is right where nothing was ever armed: a request the captain
        # merged by hand is not somewhere an accepted repair belongs.
        self.project("demo", path=self.repo, ship="just test", qa="echo ok",
                     target="main", pipeline="true")
        self.seed([self.request(head=self.published, state="merged")])
        self.assertRefused(self.publish(), "is not open")

    def test_running_it_again_arms_nothing_twice(self):
        self.assertAccepted(self.publish())
        armed = len(self.merge_calls())
        text = self.assertAccepted(self.publish())
        self.assertIn("already armed", text)
        self.assertEqual(len(self.merge_calls()), armed)
        self.assertNoRequestOpened()

    def test_a_red_check_on_the_repaired_head_arms_nothing(self):
        # The ordinary reason a repair exists. The request is advanced either way -
        # a reviewer has to see the fix - and nothing is arranged on top of it.
        self.seed([self.request(head="", checks=[
            {"name": "ci / test", "state": "FAILURE", "bucket": "fail",
             "required": True}])])
        out = self.publish()
        self.assertRefused(out, "required check that did not pass")
        self.assertEqual(self.armed(), [""])
        self.assertEqual(self.remote(self.ship_branch), self.accepted,
                         "the repair did not reach the request")

    def test_a_dry_run_arms_nothing_and_advances_nothing(self):
        text = self.assertAccepted(self.publish("--dry-run"))
        self.assertIn("automerge: squash", text)
        self.assertEqual(self.merge_calls(), [])
        self.assertEqual(self.remote(self.ship_branch), self.published)

    def test_cancelling_disarms_the_request_the_repair_landed_on(self):
        # And is asked for by the repair's own QA task, which is the only id SIANA
        # has for this work by then.
        self.assertAccepted(self.publish())
        self.assertEqual(self.armed(), ["squash"])
        text = self.assertAccepted(self.publish("--cancel-automerge"))
        self.assertIn("cancelled", text)
        self.assertIn(self.URL, text)
        self.assertEqual(self.armed(), [""])


if __name__ == "__main__":
    unittest.main()
