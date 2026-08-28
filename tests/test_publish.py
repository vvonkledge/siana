"""What `siana-publish` refuses, and what reaches the forge when it does not.

Publishing is the one thing in this fleet that leaves it. Every test here is either
a way the branch could go out without a second minion having accepted it, or a way
something internal could travel with it.
"""

import os
import subprocess
import unittest

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


class DryRun(HomeTest):
    """A well-formed publish, stopped before it leaves the machine."""

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

    def test_a_missing_forge_cli_is_caught_before_the_push(self):
        # Discovered after the push, this leaves the branch published with no merge
        # request and nothing on the record saying why.
        out = self.run_bin("siana-publish", "qa-add-json", "--dry-run",
                           env={"PATH": "/usr/bin:/bin"})
        self.assertRefused(out, "glab is not installed", "nowhere to open a merge request")

    def test_a_branch_that_is_no_longer_there(self):
        subprocess.run(["git", "-C", self.repo, "checkout", "main"],
                       capture_output=True, text=True)
        subprocess.run(["git", "-C", self.repo, "branch", "-D", "siana/add-json"],
                       capture_output=True, text=True)
        out = self.run_bin("siana-publish", "qa-add-json", "--dry-run")
        self.assertRefused(out, "has no branch siana/add-json")


if __name__ == "__main__":
    unittest.main()
