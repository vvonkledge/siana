"""What `siana-publish` refuses, and what reaches the forge when it does not.

Publishing is the one thing in this fleet that leaves it. Every test here is either
a way the branch could go out without a second minion having accepted it, or a way
something internal could travel with it.

The last class is the other half of that: while an advisory session runs, the branch
does not leave at all, and what the captain gets instead is a record of what SIANA
would have done. With no session, nothing above it changes, and that is asserted
rather than assumed - a safety feature that quietly made a direct instruction
impossible would be a worse fleet, not a safer one.
"""

import hashlib
import json
import os
import shutil
import subprocess
import unittest

from advisory import PROPOSAL
from helpers import HomeTest, script

publish = script("siana-publish")


BRIEF = """# Brief

This is the contract for one task, written by SIANA before you were dispatched.

## Delivery: ship

Your work lands. Your branch is the deliverable.

## The task

<!-- SIANA: what to build. Concrete enough that a cold-starting minion could not
     build the wrong thing. -->
Add a --json flag to the status command.

## Done when

<!-- SIANA: the acceptance the verify command cannot express. -->
`status --json` prints one object per task.

## What you are looking at

The parser was rewritten last month and the old flag table is gone.

## Out of scope

Do not touch the config loader.
"""


class Section(unittest.TestCase):
    """The brief is written for one minion in this fleet. Only the parts that
    describe the work itself are fit to read outside it."""

    def test_takes_the_named_section_only(self):
        self.assertEqual(publish.section(BRIEF, "The task"),
                         "Add a --json flag to the status command.")

    def test_stops_at_the_next_heading(self):
        # Without the stop, "Done when" would swallow every section after it and
        # carry the minion's standing limits into a merge request.
        self.assertEqual(publish.section(BRIEF, "Done when"),
                         "`status --json` prints one object per task.")

    def test_drops_the_instructions_to_siana(self):
        for heading in ("The task", "Done when"):
            self.assertNotIn("SIANA:", publish.section(BRIEF, heading))
            self.assertNotIn("<!--", publish.section(BRIEF, heading))

    def test_absent_section_is_none(self):
        # A brief may delete "Done when" when the verify genuinely says all of it,
        # so absence is a shape to handle and never a fault.
        self.assertIsNone(publish.section("# Brief\n\n## The task\n\nx\n", "Done when"))

    def test_empty_section_is_none(self):
        self.assertIsNone(publish.section("## Done when\n\n<!-- only a note -->\n",
                                          "Done when"))


class Forge(unittest.TestCase):
    def test_recognised_hosts(self):
        self.assertEqual(publish.forge_of("git@gitlab.com:apm-dev/apm-web.git"), "gitlab")
        self.assertEqual(publish.forge_of("https://github.com/o/r.git"), "github")

    def test_unknown_host_is_none(self):
        # Guessing wrong runs a CLI that does not exist, or publishes somewhere
        # nobody asked for. Neither is recoverable by re-running.
        self.assertIsNone(publish.forge_of("git@git.example.internal:o/r.git"))


class Refusals(HomeTest):
    """Each of these is a way work could be published that no QA minion accepted."""

    def setUp(self):
        super().setUp()
        self.contract("projects")

    def qa_task(self, **over):
        rec = {"id": "qa-add-json", "title": "QA add-json", "status": "done",
               "verify": "true", "verify_kind": "cmd", "deps": ["add-json"],
               "context": [], "project": "demo", "base": "siana/add-json",
               "updated": "2026-08-28T10:00:00Z"}
        rec.update(over)
        return rec

    def test_unknown_task(self):
        out = self.run_bin("siana-publish", "nope")
        self.assertRefused(out, "no task nope")

    def test_a_verdict_that_has_not_come_back(self):
        self.store("tasks.jsonl", self.qa_task(status="doing"))
        out = self.run_bin("siana-publish", "qa-add-json")
        self.assertRefused(out, "is doing, not done", "not a pass")

    def test_a_blocked_verdict_is_a_finding_not_a_pass(self):
        self.store("tasks.jsonl", self.qa_task(status="blocked"))
        out = self.run_bin("siana-publish", "qa-add-json")
        self.assertRefused(out, "is blocked, not done")

    def test_a_task_that_judged_no_branch(self):
        # Ship and scout tasks reach `done` too. Neither authorises a publish, and
        # without a base there is no branch to publish anyway.
        self.store("tasks.jsonl", self.qa_task(base=None))
        out = self.run_bin("siana-publish", "qa-add-json")
        self.assertRefused(out, "names no base")

    def test_publishing_off_for_the_project(self):
        self.store("tasks.jsonl", self.qa_task())
        self.project("demo", ship="just test")
        out = self.run_bin("siana-publish", "qa-add-json")
        self.assertRefused(out, "publishing is off for demo", "target")

    def test_a_verdict_with_no_ship_task_behind_it(self):
        self.store("tasks.jsonl", self.qa_task(deps=[]))
        self.project("demo", target="main")
        out = self.run_bin("siana-publish", "qa-add-json")
        self.assertRefused(out, "depends on nothing")

    def test_a_done_ship_task_wearing_the_same_shape(self):
        """The live queue holds these: a fix task, `done`, with both a `base` and a
        `dep`, identical in shape to a verdict. Its `base` is where its minion
        started, so publishing it would ship the state before the work."""
        self.store("tasks.jsonl",
                   {"id": "add-safe-worktree", "title": "Add safe worktree",
                    "status": "done", "verify": "just test", "verify_kind": "cmd",
                    "deps": ["qa-add-herdr-facing-test"], "context": [],
                    "project": "demo", "base": "siana/add-herdr-facing-test",
                    "updated": "2026-08-28T10:00:00Z"})
        self.project("demo", target="main")
        out = self.run_bin("siana-publish", "add-safe-worktree")
        self.assertRefused(out, "is not a QA task",
                           "publishing it would ship the state before the work")

    def test_a_missing_ship_brief_says_so_and_not_something_false(self):
        """The branch a verdict judged is read out of the ship task's brief, so a
        brief that is not there makes that read answer `siana/<ship-id>` - a name
        typed work never had. Asked in that order, this refused with "is not a QA
        task", which is a false statement about the queue standing in for a true one
        about a missing file."""
        self.store("tasks.jsonl", self.qa_task(base="siana/feat/add-json"))
        self.store("tasks.jsonl",
                   {"id": "add-json", "title": "Add a --json flag", "status": "done",
                    "verify": "just test", "verify_kind": "cmd", "deps": [],
                    "context": [], "project": "demo",
                    "updated": "2026-08-28T09:00:00Z"})
        self.project("demo", target="main")
        out = self.run_bin("siana-publish", "qa-add-json")
        self.assertRefused(out, "no brief at")
        self.assertNotIn("is not a QA task", out.stdout + out.stderr)

    def test_a_project_that_is_not_a_repository(self):
        # `git rev-parse --verify` fails the same way in a non-repository as it does
        # for a branch that is genuinely gone, and the two are fixed differently.
        self.store("tasks.jsonl", self.qa_task())
        self.project("demo", target="main")
        os.makedirs(self.at("briefs"), exist_ok=True)
        with open(self.at("briefs", "add-json.md"), "w") as fh:
            fh.write(BRIEF)
        self.store("tasks.jsonl",
                   {"id": "add-json", "title": "Add a --json flag", "status": "done",
                    "verify": "just test", "verify_kind": "cmd", "deps": [],
                    "context": [], "project": "demo",
                    "updated": "2026-08-28T09:00:00Z"})
        out = self.run_bin("siana-publish", "qa-add-json")
        self.assertRefused(out, "is not a git repository")


class Publishable(HomeTest):
    """A home holding a well-formed publish: a real repository, a QA verdict, and the
    ship brief behind it.

    Split from the tests that use it because two classes need it, and a class that
    inherited the fixture by inheriting the tests would run every one of them a second
    time under conditions they were not written for."""

    def setUp(self):
        super().setUp()
        self.contract("projects")
        self.repo = os.path.join(self.home, "repo")
        os.makedirs(self.repo)
        for argv in (["init", "-b", "main"],
                     ["config", "user.email", "t@example.com"],
                     ["config", "user.name", "t"],
                     ["commit", "--allow-empty", "-m", "base"],
                     ["checkout", "-b", "siana/add-json"],
                     ["commit", "--allow-empty", "-m", "work"],
                     ["remote", "add", "origin", "git@gitlab.com:demo/demo.git"]):
            out = subprocess.run(["git", "-C", self.repo, *argv],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)

        self.project("demo", path=self.repo, ship="just test", qa="just e2e",
                     target="preproduction")
        self.store("tasks.jsonl",
                   {"id": "add-json", "title": "Add a --json flag to status",
                    "status": "done", "verify": "just test", "verify_kind": "cmd",
                    "deps": [], "context": [], "project": "demo",
                    "updated": "2026-08-28T09:00:00Z"},
                   {"id": "qa-add-json", "title": "QA add-json", "status": "done",
                    "verify": "just e2e", "verify_kind": "cmd", "deps": ["add-json"],
                    "context": [], "project": "demo", "base": "siana/add-json",
                    "updated": "2026-08-28T10:00:00Z"})
        os.makedirs(self.at("briefs"))
        with open(self.at("briefs", "add-json.md"), "w") as fh:
            fh.write(BRIEF)


class DryRun(Publishable):
    """A well-formed publish, stopped before it leaves the machine."""

    def test_reports_what_it_would_open(self):
        text = self.assertAccepted(self.run_bin("siana-publish", "qa-add-json",
                                                "--dry-run"))
        self.assertIn("branch:  siana/add-json", text)
        self.assertIn("target:  preproduction", text)
        self.assertIn("gitlab", text)
        self.assertIn("Add a --json flag to status", text)
        self.assertIn("Add a --json flag to the status command.", text)

    def test_the_body_names_both_tasks(self):
        # Whoever reviews it can find the contract the work was held to, and the
        # verdict that let it out, without either being pasted in.
        text = self.assertAccepted(self.run_bin("siana-publish", "qa-add-json",
                                                "--dry-run"))
        self.assertIn("Shipped by `add-json`, accepted by `qa-add-json`.", text)

    def test_nothing_internal_travels(self):
        # The captain's ruling: the QA report stays inside SIANA. The brief's
        # background and scope are written for one minion and are not review notes.
        text = self.assertAccepted(self.run_bin("siana-publish", "qa-add-json",
                                                "--dry-run"))
        self.assertNotIn("The parser was rewritten", text)
        self.assertNotIn("Do not touch the config loader", text)
        self.assertNotIn("Your work lands", text)

    def test_an_unfilled_brief_is_refused(self):
        # A merge request describing the work as `{TASK}` looks like a description
        # and is not one.
        with open(self.at("briefs", "add-json.md"), "w") as fh:
            fh.write("# Brief\n\n## The task\n\n{TASK}\n")
        out = self.run_bin("siana-publish", "qa-add-json", "--dry-run")
        self.assertRefused(out, "unfilled placeholder")

    def test_a_missing_forge_cli_stops_a_real_run_before_it_pushes(self):
        # Discovered after the push, this leaves the branch published with no merge
        # request and nothing on the record saying why.
        out = self.run_bin("siana-publish", "qa-add-json", env={"PATH": "/usr/bin:/bin"})
        self.assertRefused(out, "glab is not installed",
                           "nowhere to open a merge request")

    def test_a_dry_run_still_describes_the_plan_without_the_cli(self):
        # A dry run changes nothing, so it has to stay readable on a machine that
        # could not carry it out - including CI, which has neither glab nor gh.
        text = self.assertAccepted(self.run_bin("siana-publish", "qa-add-json",
                                                "--dry-run",
                                                env={"PATH": "/usr/bin:/bin"}))
        self.assertIn("branch:  siana/add-json", text)
        self.assertIn("glab is not installed here", text)

    def test_a_branch_that_is_no_longer_there(self):
        subprocess.run(["git", "-C", self.repo, "checkout", "main"],
                       capture_output=True, text=True)
        subprocess.run(["git", "-C", self.repo, "branch", "-D", "siana/add-json"],
                       capture_output=True, text=True)
        out = self.run_bin("siana-publish", "qa-add-json", "--dry-run")
        self.assertRefused(out, "has no branch siana/add-json")


class UnderAnAdvisorySession(Publishable):
    """The same well-formed publish, with a session in force.

    Driven with a session record rather than a live `siana-afk`, deliberately: what is
    under test here is that this command asks at all and stops on the answer, and a
    dead session is an answer. That the session itself cannot be forged into a
    permission is `test_afk.py`, where it is driven against a real process."""

    def setUp(self):
        super().setUp()
        self.contract("decisions")
        with open(self.at("principles.md"), "wb") as fh:
            fh.write(b"# Principles\n\nPublish what two minions accepted.\n")
        with open(self.at("principles.md"), "rb") as fh:
            self.policy = hashlib.sha256(fh.read()).hexdigest()
        with open(self.at("afk"), "w") as fh:
            json.dump({"state": "running", "pid": 1,
                       "command": "python3 /nowhere/bin/siana-afk",
                       "started": "2026-08-29T20:00:00Z",
                       "until": "2099-01-01T00:00:00Z",
                       "policy": self.at("principles.md"), "sha256": self.policy,
                       "allow": [], "projects": ["demo"]}, fh)

    def record(self, **over):
        record = dict(PROPOSAL)
        record.update(over)
        with open(self.at("record.json"), "w") as fh:
            json.dump(record, fh)
        return self.at("record.json")

    def assertProposalRecorded(self, out):
        """The publish stopped, and the decision SIANA reached is in the ledger.

        Both halves. A publish that merely refuses has done half the job: what an
        advisory night produces is the record, and a decision missing from it reads
        in the morning as a quiet night."""
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        with open(self.at("decisions.jsonl")) as fh:
            rec = json.loads(fh.read().strip().splitlines()[-1])
        self.assertEqual(rec["verdict"], "refused")
        self.assertEqual(rec["action"], PROPOSAL["action"])
        self.assertEqual(rec["grant"], "2026-08-29T20:00:00Z")
        return rec

    def test_it_refuses_without_a_record_before_anything_else(self):
        # The record is what the captain reads in the morning instead of a merge
        # request that appeared while they were asleep. Refused up front, so the
        # message is about the missing record and not about the first world check
        # that happened to fail.
        out = self.run_bin("siana-publish", "qa-add-json")
        self.assertRefused(out, "needs --record", "siana-afk --stop")

    def test_the_push_does_not_happen_and_the_proposal_is_recorded(self):
        # The whole of what an advisory night produces. No PATH is restricted here:
        # the gate is asked before the check for a forge CLI, so this is the same
        # answer on a machine that has one and on CI, which has neither.
        out = self.run_bin("siana-publish", "qa-add-json",
                           "--record", self.record())
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("nothing was pushed", out.stderr)
        with open(self.at("decisions.jsonl")) as fh:
            rec = json.loads(fh.read().strip().splitlines()[-1])
        # Every field the captain reads in the morning instead of a merge request,
        # asserted here rather than trusted to the gate's own tests: this is the one
        # path that turns a publish into a record, and a field lost on the way would
        # be lost silently.
        self.assertEqual(rec["verdict"], "refused")
        self.assertEqual(rec["action"], PROPOSAL["action"])
        self.assertEqual(rec["task"], "qa-add-json")
        self.assertEqual(rec["project"], "demo")
        self.assertEqual(rec["evidence"], PROPOSAL["evidence"])
        self.assertEqual(rec["alternatives"], PROPOSAL["alternatives"])
        self.assertEqual(rec["principles"], PROPOSAL["principles"])
        self.assertEqual(rec["confidence"], "high")
        self.assertEqual(rec["reversibility"], "R2")
        self.assertEqual(rec["grant"], "2026-08-29T20:00:00Z")
        self.assertEqual(rec["policy"], self.policy)
        # Nothing was pushed, so the branch has no upstream.
        upstream = subprocess.run(
            ["git", "-C", self.repo, "rev-parse", "--abbrev-ref",
             "siana/add-json@{upstream}"], capture_output=True, text=True)
        self.assertNotEqual(upstream.returncode, 0, upstream.stdout)

    def test_a_dry_run_records_nothing_and_still_describes_the_plan(self):
        # A dry run changes nothing, and the ledger is something. Recording a
        # proposal for a command that was never going to run would put a decision in
        # front of the captain that SIANA did not make.
        text = self.assertAccepted(
            self.run_bin("siana-publish", "qa-add-json", "--dry-run"))
        self.assertIn("branch:  siana/add-json", text)
        self.assertFalse(os.path.exists(self.at("decisions.jsonl")))

    def test_a_proposal_the_gate_refuses_on_shape_still_stops_the_publish(self):
        out = self.run_bin("siana-publish", "qa-add-json",
                           "--record", self.record(principles=[]))
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("quotes no principle", out.stdout)

    def test_a_forge_nobody_recognises_does_not_discard_the_proposal(self):
        # Which forge the remote is on is a question about this machine, and an
        # advisory night never needs it answered: the proposal is refused at the
        # allowlist whatever the answer would have been. Asked first, it threw the
        # decision away instead.
        subprocess.run(["git", "-C", self.repo, "remote", "set-url", "origin",
                        "https://bitbucket.org/example/demo.git"],
                       capture_output=True, text=True)
        out = self.run_bin("siana-publish", "qa-add-json", "--record", self.record())
        self.assertProposalRecorded(out)
        self.assertNotIn("cannot tell which forge", out.stdout + out.stderr)

    def test_a_repository_with_no_origin_does_not_discard_the_proposal(self):
        subprocess.run(["git", "-C", self.repo, "remote", "remove", "origin"],
                       capture_output=True, text=True)
        out = self.run_bin("siana-publish", "qa-add-json", "--record", self.record())
        self.assertProposalRecorded(out)
        self.assertNotIn("no `origin` remote", out.stdout + out.stderr)

    def test_a_registry_path_that_has_moved_does_not_discard_the_proposal(self):
        # The registry entry is stale and the work is somewhere else. Still a
        # decision SIANA made, and still the captain's to read.
        shutil.move(self.repo, self.repo + "-moved")
        self.addCleanup(shutil.move, self.repo + "-moved", self.repo)
        out = self.run_bin("siana-publish", "qa-add-json", "--record", self.record())
        self.assertProposalRecorded(out)
        self.assertNotIn("which is not a directory", out.stdout + out.stderr)

    def test_a_branch_that_is_gone_does_not_discard_the_proposal(self):
        subprocess.run(["git", "-C", self.repo, "checkout", "main"],
                       capture_output=True, text=True)
        subprocess.run(["git", "-C", self.repo, "branch", "-D", "siana/add-json"],
                       capture_output=True, text=True)
        out = self.run_bin("siana-publish", "qa-add-json", "--record", self.record())
        self.assertProposalRecorded(out)
        self.assertNotIn("has no branch", out.stdout + out.stderr)

    def test_a_verdict_that_authorises_nothing_is_still_refused_first(self):
        # The gate is asked last of the queue's refusals, not instead of them. A
        # session must not turn a publish nobody accepted into a recorded proposal.
        self.store("tasks.jsonl",
                   {"id": "qa-add-json", "title": "QA add-json", "status": "doing",
                    "verify": "just e2e", "verify_kind": "cmd", "deps": ["add-json"],
                    "context": [], "project": "demo", "base": "siana/add-json",
                    "updated": "2026-08-29T11:00:00Z"})
        out = self.run_bin("siana-publish", "qa-add-json", "--record", self.record())
        self.assertRefused(out, "is doing, not done")
        self.assertFalse(os.path.exists(self.at("decisions.jsonl")))


class WithNoSession(Publishable):
    """Ordinary attended publication, with the decision ledger installed and no
    session in force.

    The regression this guards is the one a safety feature is most likely to cause:
    the captain says publish this, and the fleet cannot, because a mechanism built for
    the night they were away has quietly become the rule for the day they are here."""

    def setUp(self):
        super().setUp()
        self.contract("decisions")

    def test_no_record_is_required(self):
        # And it gets no further than the check that was already there.
        out = self.run_bin("siana-publish", "qa-add-json",
                           env={"PATH": "/usr/bin:/bin"})
        self.assertRefused(out, "glab is not installed")
        self.assertNotIn("--record", out.stderr)

    def test_nothing_is_recorded_in_the_ledger(self):
        # The captain typed this, or told SIANA to, and that is the authority it has
        # always run on. There is no decision to write down.
        self.run_bin("siana-publish", "qa-add-json", env={"PATH": "/usr/bin:/bin"})
        self.assertFalse(os.path.exists(self.at("decisions.jsonl")))

    def test_a_record_passed_with_no_session_is_still_gated(self):
        # The case this exists for: a session whose deadline passes at 06:00 releases
        # its record, and a SIANA woken at 06:01 has already written its proposal.
        # Ignoring the flag would push the branch and open the merge request with
        # nothing recorded anywhere, which is the one outcome an advisory night is
        # supposed to make impossible.
        with open(self.at("record.json"), "w") as fh:
            json.dump(PROPOSAL, fh)
        out = self.run_bin("siana-publish", "qa-add-json",
                           "--record", self.at("record.json"))
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("nothing was pushed", out.stderr)
        with open(self.at("decisions.jsonl")) as fh:
            rec = json.loads(fh.read().strip().splitlines()[-1])
        # `proposed` and not `refused`: no session was in force, and the ledger says
        # so rather than naming one that had already ended.
        self.assertEqual(rec["verdict"], "proposed")
        self.assertIsNone(rec["grant"])
        upstream = subprocess.run(
            ["git", "-C", self.repo, "rev-parse", "--abbrev-ref",
             "siana/add-json@{upstream}"], capture_output=True, text=True)
        self.assertNotEqual(upstream.returncode, 0, upstream.stdout)

    def test_the_dry_run_is_unchanged(self):
        text = self.assertAccepted(
            self.run_bin("siana-publish", "qa-add-json", "--dry-run"))
        self.assertIn("branch:  siana/add-json", text)
        self.assertIn("target:  preproduction", text)


if __name__ == "__main__":
    unittest.main()
