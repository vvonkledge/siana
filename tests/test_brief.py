"""siana-brief: the one place a task's delivery kind is ever stated.

Two things are being defended. The kind is never inferred, because a scout that
starts shipping is the blur the fleet exists to prevent. And in a project that asks
for QA, the QA task is queued here or nowhere: a QA task nobody remembered to queue
is exactly the false green that registry field was set to prevent.

Driven as a process against a real `tasks` and a real `datafile`, because the
pairing's whole value is that the queue agrees it happened.
"""

import json
import os
import unittest

from helpers import HomeTest


class Brief(HomeTest):

    def setUp(self):
        super().setUp()
        self.contract("projects")
        self.template("brief-ship.md", "brief-scout.md", "brief-qa.md")
        self.queue()

    def brief(self, *args):
        return self.run_bin("siana-brief", *args)

    def add(self, title, project="proj", **flags):
        argv = ["tasks", "--file", self.at("tasks.jsonl"), "add", title,
                "--verify", "true"]
        if project:
            argv += ["--project", project]
        for k, v in flags.items():
            argv += [f"--{k}", v]
        out = self.assertAccepted(self.run_cmd(argv))
        for line in out.splitlines():
            if line.startswith("id: "):
                return line[4:].strip()
        self.fail(f"tasks add named no id:\n{out}")

    def record(self, task_id):
        """The stored record, not `tasks show`. The view is TOON, which escapes a
        quote or a colon on the way out, so a check against it would be a check on
        the rendering rather than on what a QA minion will actually run."""
        found = None
        with open(self.at("tasks.jsonl")) as fh:
            for line in fh:
                rec = json.loads(line)
                if rec.get("id") == task_id:
                    found = rec
        self.assertIsNotNone(found, f"no record for {task_id}")
        return found

    def brief_text(self, task_id):
        with open(self.at("briefs", f"{task_id}.md")) as fh:
            return fh.read()

    def show(self, task_id):
        return self.assertAccepted(
            self.run_cmd(["tasks", "--file", self.at("tasks.jsonl"), "show", task_id]))

    def ids(self):
        out = self.assertAccepted(
            self.run_cmd(["tasks", "--file", self.at("tasks.jsonl"), "list"]))
        return [line.strip().split(",")[0] for line in out.splitlines()
                if line.startswith("  ") and "," in line]


class Arguments(Brief):

    def test_no_task_is_refused(self):
        self.assertRefused(self.brief(), "which task?")

    def test_a_task_with_no_kind_is_refused_and_the_kind_is_never_guessed(self):
        # A wrong guess here is a minion shipping what was only meant to be
        # investigated.
        self.project("proj")
        tid = self.add("Do a thing")
        self.assertRefused(self.brief(tid), "delivery kind", "--ship or --scout")

    def test_both_kinds_at_once_is_refused(self):
        self.project("proj")
        tid = self.add("Do a thing")
        self.assertRefused(self.brief(tid, "--ship", "--scout"), "pick one")

    def test_an_unknown_option_is_refused_rather_than_ignored(self):
        self.assertRefused(self.brief("t1", "--shp"), "unknown option")

    def test_two_task_ids_are_refused(self):
        self.assertRefused(self.brief("t1", "t2", "--ship"), "one task at a time")

    def test_help_is_the_command_s_own_docstring(self):
        out = self.assertAccepted(self.brief("--help"))
        self.assertIn("--ship", out)
        self.assertIn("--scout", out)
        self.assertIn("--type", out)


class CommitType(Brief):
    """The one place a ship task's Conventional Commit type is ever stated.

    Everything downstream reads the branch that follows from it, so a type guessed
    later - off a title, a diff, a commit - would be a statement about what the work
    turned out to be, made by something that never held the contract. There is
    nowhere else to state it, so every way of getting it wrong has to stop here.
    """

    def test_ship_work_with_no_type_is_refused(self):
        self.project("proj", qa="just qa")
        tid = self.add("Build a thing")
        self.assertRefused(self.brief(tid, "--ship"), "needs a commit type")
        # Nothing half-made: no brief to refuse to rewrite, and no QA task queued
        # against a branch no minion will ever be put on.
        self.assertFalse(os.path.exists(self.at("briefs", f"{tid}.md")))
        self.assertEqual(self.ids(), [tid])

    def test_a_type_that_is_not_a_conventional_commit_type_is_refused(self):
        # A branch named for anything else announces a commit the project's CI
        # rejects on the merge request.
        self.project("proj", qa="just qa")
        tid = self.add("Build a thing")
        out = self.brief(tid, "--ship", "--type", "wip")
        self.assertRefused(out, "wip is not a Conventional Commit type", "refactor")
        self.assertFalse(os.path.exists(self.at("briefs", f"{tid}.md")))
        self.assertEqual(self.ids(), [tid])

    def test_a_type_with_no_value_is_refused(self):
        self.project("proj")
        tid = self.add("Build a thing")
        self.assertRefused(self.brief(tid, "--ship", "--type"), "--type needs a")

    def test_two_types_are_refused(self):
        self.project("proj")
        tid = self.add("Build a thing")
        self.assertRefused(self.brief(tid, "--ship", "--type", "feat",
                                      "--type", "fix"), "one type at a time")

    def test_the_joined_spelling_is_accepted_too(self):
        # The near miss is otherwise `unknown option`, which says nothing about the
        # type, and a retype costs SIANA a whole turn.
        self.project("proj")
        tid = self.add("Build a thing")
        self.assertAccepted(self.brief(tid, "--ship", "--type=perf"))
        self.assertIn(f"siana/perf/{tid}", self.brief_text(tid))

    def test_scout_work_is_refused_a_type(self):
        # A scout lands nothing, so there is no commit for a type to describe.
        self.project("proj")
        tid = self.add("Learn a thing")
        self.assertRefused(self.brief(tid, "--scout", "--type", "docs"),
                           "--type is for ship work")
        self.assertFalse(os.path.exists(self.at("briefs", f"{tid}.md")))

    def test_the_ship_brief_records_the_branch_the_type_makes(self):
        # The brief is the fleet's only record of the type, so this line is what
        # dispatch, the pipeline, publishing, retiring and reaping all read.
        self.project("proj")
        tid = self.add("Write the guide")
        out = self.assertAccepted(self.brief(tid, "--ship", "--type", "docs"))
        self.assertIn(f"siana/docs/{tid}", out)
        text = self.brief_text(tid)
        self.assertIn(f"    branch  siana/docs/{tid}", text)
        # Filled in by the script, so it is never one of the placeholders SIANA is
        # asked to fill: a branch name typed twice is one that can disagree.
        self.assertNotIn("{SHIP_BRANCH}", text)
        self.assertNotIn("{SHIP_BRANCH}", out)

    def test_a_scout_brief_names_no_branch(self):
        # Its name is a role in this fleet and not a category of change, so there is
        # nothing to record and `siana/<task-id>` is what every command falls to.
        self.project("proj")
        tid = self.add("Learn a thing")
        self.assertAccepted(self.brief(tid, "--scout"))
        self.assertNotIn("branch  siana/", self.brief_text(tid))


class Refusals(Brief):

    def test_a_task_that_does_not_exist_is_refused(self):
        # A brief filed under a name no task carries is a brief no minion ever sees.
        self.assertRefused(self.brief("no-such-task", "--scout"), "no such task")

    def test_a_missing_template_is_refused_before_anything_is_written(self):
        self.project("proj")
        tid = self.add("Do a thing")
        os.remove(self.at("brief-scout.md"))
        self.assertRefused(self.brief(tid, "--scout"), "no scout brief template")
        self.assertFalse(os.path.exists(self.at("briefs", f"{tid}.md")))

    def test_a_brief_is_never_scaffolded_twice(self):
        # A minion may already have read this one.
        self.project("proj")
        tid = self.add("Do a thing")
        self.assertAccepted(self.brief(tid, "--scout"))
        with open(self.at("briefs", f"{tid}.md"), "a") as fh:
            fh.write("\nfilled in by SIANA\n")
        self.assertRefused(self.brief(tid, "--scout"), "already briefed")
        with open(self.at("briefs", f"{tid}.md")) as fh:
            self.assertIn("filled in by SIANA", fh.read())

    def test_a_ship_task_naming_an_unknown_project_is_refused(self):
        # Dispatch would refuse it for the same reason, so it stops here.
        tid = self.add("Do a thing", project="ghost")
        self.assertRefused(self.brief(tid, "--ship", "--type", "feat"),
                           "unknown project: ghost")


class Scaffolding(Brief):

    def test_a_scout_brief_is_copied_and_its_placeholders_are_named(self):
        # Filling them is the whole point of scaffolding it, so what is still empty
        # has to be said: the minion blocks on an unfilled one.
        self.project("proj")
        tid = self.add("Learn a thing")
        out = self.assertAccepted(self.brief(tid, "--scout"))
        self.assertIn("(scout)", out)
        with open(self.at("briefs", f"{tid}.md")) as fh:
            text = fh.read()
        self.assertIn("Delivery: scout", text)
        for marker in ("{TASK}", "{DONE}", "{BACKGROUND}", "{SCOPE}"):
            self.assertIn(marker, text)
            self.assertIn(marker, out)

    def test_a_ship_brief_is_the_ship_template(self):
        self.project("proj")
        tid = self.add("Build a thing")
        self.assertAccepted(self.brief(tid, "--ship", "--type", "feat"))
        with open(self.at("briefs", f"{tid}.md")) as fh:
            self.assertIn("Delivery: ship", fh.read())

    def test_a_template_the_captain_has_filled_in_completely_is_not_a_failure(self):
        # grep finding no placeholder is an answer, not an error: under pipefail its
        # exit 1 would kill the command after the brief was already copied.
        self.project("proj")
        tid = self.add("Learn a thing")
        with open(self.at("brief-scout.md"), "w") as fh:
            fh.write("# Brief\n\nEverything is already said.\n")
        out = self.assertAccepted(self.brief(tid, "--scout"))
        self.assertNotIn("fill", out)
        self.assertTrue(os.path.exists(self.at("briefs", f"{tid}.md")))

    def test_briefing_a_dispatched_task_says_the_brief_is_late_not_wrong(self):
        self.project("proj")
        tid = self.add("Build a thing")
        self.assertAccepted(self.run_cmd(
            ["tasks", "--file", self.at("tasks.jsonl"), "start", tid,
             "--owner", "claude@w1:p1"]))
        out = self.assertAccepted(self.brief(tid, "--ship", "--type", "fix"))
        self.assertIn("late", out)
        self.assertTrue(os.path.exists(self.at("briefs", f"{tid}.md")))
        # Its worktree was cut before this brief named a branch, so dispatch fell
        # back to `siana/<id>` and everything downstream now reads a different name.
        # The pipeline refuses that worktree for it, and this is the moment it
        # became true, so it is the moment to say so.
        self.assertIn(f"it is on siana/{tid} and", out)
        self.assertIn(f"not siana/fix/{tid}", out)


class QaPairing(Brief):
    """A project carrying `qa` is the captain saying ship work there is not accepted
    on the word of the minion that did it."""

    def test_a_project_without_qa_queues_nothing_extra(self):
        self.project("proj")
        tid = self.add("Build a thing")
        before = self.ids()
        self.assertAccepted(self.brief(tid, "--ship", "--type", "feat"))
        self.assertEqual(self.ids(), before)

    def test_a_project_with_qa_queues_the_task_that_will_judge_the_work(self):
        self.project("proj", qa="just qa")
        tid = self.add("Build a thing")
        out = self.assertAccepted(self.brief(tid, "--ship", "--type", "docs"))
        self.assertIn("paired", out)
        self.assertIn("just qa", out)
        new = [i for i in self.ids() if i != tid]
        self.assertEqual(len(new), 1, self.ids())
        qa = self.record(new[0])
        self.assertEqual(qa["deps"], [tid])          # ready the moment the work is back
        # Cut from the branch it judges, type and all.
        self.assertEqual(qa["base"], f"siana/docs/{tid}")
        self.assertEqual(qa["project"], "proj")
        self.assertIn("just qa", qa["verify"])       # runs the project's own command
        self.assertIn("reports/$SIANA_TASK_ID.md", qa["verify"])  # report must exist
        self.assertEqual(qa["status"], "todo")

    def test_the_qa_brief_is_filled_in_and_needs_nothing_from_siana(self):
        # What makes QA cost nothing per task, and therefore get used.
        self.project("proj", qa="just qa")
        tid = self.add("Build a thing")
        self.assertAccepted(self.brief(tid, "--ship", "--type", "refactor"))
        qa_id = [i for i in self.ids() if i != tid][0]
        with open(self.at("briefs", f"{qa_id}.md")) as fh:
            text = fh.read()
        self.assertIn(tid, text)
        self.assertIn(f"siana/refactor/{tid}", text)
        self.assertNotIn("{SHIP_TASK}", text)
        self.assertNotIn("{SHIP_BRANCH}", text)

    def test_a_scout_task_is_never_paired_with_qa(self):
        self.project("proj", qa="just qa")
        tid = self.add("Learn a thing")
        before = self.ids()
        self.assertAccepted(self.brief(tid, "--scout"))
        self.assertEqual(self.ids(), before)

    def test_qa_on_a_project_git_cannot_branch_is_refused_before_briefing(self):
        # A QA minion judges the ship branch from a worktree of its own, and this
        # project is recorded as having neither.
        self.project("proj", qa="just qa", worktree="false")
        tid = self.add("Build a thing")
        self.assertRefused(self.brief(tid, "--ship", "--type", "feat"),
                           "git cannot branch it")
        self.assertFalse(os.path.exists(self.at("briefs", f"{tid}.md")))

    def test_a_missing_qa_template_is_refused_before_briefing(self):
        os.remove(self.at("brief-qa.md"))
        self.project("proj", qa="just qa")
        tid = self.add("Build a thing")
        self.assertRefused(self.brief(tid, "--ship", "--type", "feat"),
                           "no qa brief template")
        self.assertFalse(os.path.exists(self.at("briefs", f"{tid}.md")))

    def test_a_qa_command_spanning_two_lines_is_refused(self):
        # Both the path and the command travel back a line at a time, so a newline
        # would leave a QA task verifying something nobody wrote.
        self.project("proj", qa="just qa\necho hi")
        tid = self.add("Build a thing")
        self.assertRefused(self.brief(tid, "--ship", "--type", "feat"),
                           "more than one line")

    def test_a_qa_command_carrying_a_colon_survives_intact(self):
        # Read from the registry rather than parsed out of `tasks show`, because
        # TOON escapes a value like this on the way out.
        self.project("proj", qa='pytest -k "a: b"')
        tid = self.add("Build a thing")
        self.assertAccepted(self.brief(tid, "--ship", "--type", "feat"))
        qa_id = [i for i in self.ids() if i != tid][0]
        self.assertIn('pytest -k "a: b"', self.record(qa_id)["verify"])


if __name__ == "__main__":
    unittest.main()
