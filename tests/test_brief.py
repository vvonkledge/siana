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

from helpers import HomeTest, script

# The branch reader, through one of the commands that carry it.
# `tests/test_ship_branch.py::OneReader` compares the copies as
# text, so what this one answers is what all of them answer.
d = script("siana-dispatch")

# The repair record, through the command that reads it back at publication. What
# `siana-brief` writes is only worth what that reader answers about it.
publish = script("siana-publish")


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

    def test_a_run_of_valid_types_is_not_a_valid_type(self):
        """The gate this replaces asked whether `" $type "` fell inside
        `" build chore ci docs feat fix perf refactor revert style test "`, so any
        adjacent run of the list matched it, bounded by spaces on both sides.
        `--type 'ci docs'` was accepted and wrote `siana/ci docs/<id>`: a name git
        refuses to create, and one the branch line cannot carry back, because it
        holds a single token. Every reader then answered the legacy `siana/<id>`
        while the QA task queued here waited on a base nobody ever made, so the
        split first showed up at a QA dispatch, long after the work was finished."""
        self.project("proj", qa="just qa")
        tid = self.add("Build a thing")
        for bad in ("ci docs", "chore ci", "revert style test",
                    "build chore ci docs feat fix perf refactor revert style test"):
            with self.subTest(bad=bad):
                self.assertRefused(self.brief(tid, "--ship", "--type", bad),
                                   "is not a Conventional Commit type")
                # Refused before the home is touched, so there is no brief to
                # rewrite and no QA task standing on an impossible base.
                self.assertFalse(os.path.exists(self.at("briefs")))
                self.assertEqual(self.ids(), [tid])

    def test_a_type_carrying_whitespace_is_refused(self):
        # The whole argument is what is compared, so a type wearing a space, a tab
        # or a newline is not that type. Any of them would reach the branch name.
        self.project("proj", qa="just qa")
        tid = self.add("Build a thing")
        for bad in (" docs", "docs ", "\tdocs", "docs\t", "\ndocs", "docs\n",
                    "do cs", "docs\ndocs"):
            with self.subTest(bad=repr(bad)):
                self.assertRefused(self.brief(tid, "--ship", "--type", bad),
                                   "is not a Conventional Commit type")
                self.assertFalse(os.path.exists(self.at("briefs")))
                self.assertEqual(self.ids(), [tid])

    def test_a_type_shaped_like_a_glob_is_refused(self):
        # Matched, never pattern-matched: a type is compared for equality, so a
        # metacharacter is a character and stands for nothing but itself.
        self.project("proj", qa="just qa")
        tid = self.add("Build a thing")
        for bad in ("*", "?", "[cf]i", "d*", "doc?", "fea*"):
            with self.subTest(bad=bad):
                self.assertRefused(self.brief(tid, "--ship", "--type", bad),
                                   "is not a Conventional Commit type")
                self.assertFalse(os.path.exists(self.at("briefs")))
                self.assertEqual(self.ids(), [tid])

    def test_a_type_that_is_part_of_a_type_is_refused(self):
        # A near miss is a miss. `doc` names no Conventional Commit category, and a
        # branch built from one announces a commit its project's CI rejects.
        self.project("proj", qa="just qa")
        tid = self.add("Build a thing")
        for bad in ("doc", "docs2", "Docs", "re", "fi", "cs fea"):
            with self.subTest(bad=bad):
                self.assertRefused(self.brief(tid, "--ship", "--type", bad),
                                   "is not a Conventional Commit type")
                self.assertFalse(os.path.exists(self.at("briefs")))
                self.assertEqual(self.ids(), [tid])

    def test_an_empty_type_is_refused_as_a_missing_one(self):
        # Both spellings of nothing. There is no type to state, and stating none is
        # the case the required flag already covers.
        self.project("proj")
        tid = self.add("Build a thing")
        self.assertRefused(self.brief(tid, "--ship", "--type", ""),
                           "needs a commit type")
        self.assertRefused(self.brief(tid, "--ship", "--type="),
                           "needs a commit type")
        self.assertFalse(os.path.exists(self.at("briefs")))

    def test_every_supported_type_is_accepted_whole_and_read_back_the_same(self):
        """The writer and the readers have to agree on one name. They disagreed for
        any type the old gate let through in more than one word, and that
        disagreement was silent: what was written was unreadable, so the readers
        answered `siana/<id>` and the QA task queued beside it named the other. Each
        of the eleven, stated whole, is accepted and comes back exactly as written -
        out of the brief, out of the reader, and off the QA task's base."""
        self.project("proj", qa="just qa")
        for type_ in ("build", "chore", "ci", "docs", "feat", "fix", "perf",
                      "refactor", "revert", "style", "test"):
            with self.subTest(type=type_):
                tid = self.add(f"Do the {type_} thing")
                out = self.assertAccepted(
                    self.brief(tid, "--ship", "--type", type_))
                branch = f"siana/{type_}/{tid}"
                self.assertIn(branch, out)
                self.assertIn(f"    branch  {branch}", self.brief_text(tid))
                self.assertEqual(d.ship_branch(self.home, tid), branch)
                self.assertEqual(self.record(f"qa-{tid}")["base"], branch)

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

    def test_a_ship_template_that_records_no_branch_is_refused(self):
        """The home's copy of this template is the captain's to evolve, and `just
        init` keeps a diverged one on purpose. One that has lost the marker writes a
        brief recording no branch, so every reader answers `siana/<id>` while the QA
        task queued behind it is cut from the typed branch. That split is invisible
        until a QA dispatch fails on a base nobody created."""
        self.project("proj", qa="just qa")
        tid = self.add("Build a thing")
        with open(self.at("brief-ship.md"), "w") as fh:
            fh.write("# Brief\n\n## Delivery: ship\n\nCommit on {SHIP_BRANCH}.\n")
        out = self.brief(tid, "--ship", "--type", "feat")
        self.assertRefused(out, "does not record the branch where every command")
        self.assertFalse(os.path.exists(self.at("briefs", f"{tid}.md")))
        self.assertEqual(self.ids(), [tid])

    def test_the_branch_line_under_another_heading_is_refused(self):
        """The readers only look inside `## Delivery: ship`, so a template keeping
        the exact line but moving it under a heading of its own is the same split
        wearing a passing check: what this verifies has to be the line they find."""
        self.project("proj", qa="just qa")
        tid = self.add("Build a thing")
        with open(self.at("brief-ship.md"), "w") as fh:
            fh.write("# Brief\n\n## Delivery: ship\n\nYour work lands.\n\n"
                     "## Your branch\n\n    branch  {SHIP_BRANCH}\n")
        out = self.brief(tid, "--ship", "--type", "feat")
        self.assertRefused(out, "where every command looks for it",
                           "under any other heading it is prose")
        self.assertFalse(os.path.exists(self.at("briefs", f"{tid}.md")))
        self.assertEqual(self.ids(), [tid])

    def test_a_ship_template_that_lost_the_marker_entirely_is_refused(self):
        self.project("proj")
        tid = self.add("Build a thing")
        with open(self.at("brief-ship.md"), "w") as fh:
            fh.write("# Brief\n\n## Delivery: ship\n\nYour work lands.\n")
        self.assertRefused(self.brief(tid, "--ship", "--type", "feat"),
                           "does not record the branch where every command")
        self.assertFalse(os.path.exists(self.at("briefs", f"{tid}.md")))

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


class Repairs(Brief):
    """A ship task that repairs work already published.

    The record it writes is the whole of what keeps a second merge request from being
    opened for one piece of work, and nothing downstream infers it: a fix is cut from
    the branch it repairs, so ancestry, a title and a commit message all describe an
    ordinary fix exactly as well as they describe a repair.
    """

    def published(self, title="Ship the thing", type_="feat", qa=None):
        """Work that has been briefed and shipped, as a repair target."""
        self.project("proj", **({"qa": qa} if qa else {}))
        target = self.add(title)
        self.assertAccepted(self.brief(target, "--ship", "--type", type_))
        return target, f"siana/{type_}/{target}"

    def repair(self, target, base, title="Repair the thing", type_="fix"):
        tid = self.add(title, base=base)
        return tid, self.brief(tid, "--ship", "--type", type_, "--repairs", target)

    def test_a_repair_records_its_own_branch_and_the_one_it_lands_on(self):
        target, branch = self.published()
        tid, out = self.repair(target, branch)
        said = self.assertAccepted(out)
        text = self.brief_text(tid)
        # Its own branch, unchanged: the minion and its QA stay isolated on it.
        self.assertIn(f"    branch  siana/fix/{tid}", text)
        self.assertIn(f"    repairs {target} {branch}", text)
        self.assertEqual(d.ship_branch(self.home, tid), f"siana/fix/{tid}")
        self.assertIn(f"repairs {target}", said)

    def test_the_record_is_what_publication_reads(self):
        target, branch = self.published()
        tid, out = self.repair(target, branch)
        self.assertAccepted(out)
        self.assertEqual(publish.repair_record(self.home, tid), (target, branch))
        self.assertEqual(publish.publication_branch(self.home, tid), branch)

    def test_ordinary_ship_work_records_no_repair_at_all(self):
        # The compatibility that matters most: everything not briefed this way keeps
        # opening a merge request of its own.
        target, branch = self.published()
        self.assertIsNone(publish.repair_record(self.home, target))
        self.assertEqual(publish.publication_branch(self.home, target), branch)

    def test_a_repair_of_a_repair_lands_where_the_first_one_did(self):
        # The chain is resolved here, while the briefs are in front of us, so
        # publication has one branch to advance however many repairs deep this is.
        target, branch = self.published()
        first, out = self.repair(target, branch)
        self.assertAccepted(out)
        second = self.add("Repair it again", base=f"siana/fix/{first}")
        self.assertAccepted(self.brief(second, "--ship", "--type", "fix",
                                       "--repairs", first))
        self.assertEqual(publish.repair_record(self.home, second), (first, branch))

    def test_a_repair_keeps_a_qa_task_of_its_own_on_its_own_branch(self):
        # The isolation the record does not change: a repair is judged by a second
        # minion before anything of it reaches the request it repairs.
        target, branch = self.published(qa="just qa")
        tid, out = self.repair(target, branch)
        self.assertAccepted(out)
        self.assertEqual(self.record(f"qa-{tid}")["base"], f"siana/fix/{tid}")

    def test_scout_work_is_refused_a_repair(self):
        self.project("proj")
        tid = self.add("Learn a thing")
        self.assertRefused(self.brief(tid, "--scout", "--repairs", "something"),
                           "--repairs is for ship work")
        self.assertFalse(os.path.exists(self.at("briefs", f"{tid}.md")))

    def test_a_repair_briefed_as_anything_but_a_fix_is_refused(self):
        # The branch it lands on already carries whatever the work it repairs is,
        # and a commit type that disagrees with its branch is what the naming
        # convention exists to remove.
        target, branch = self.published()
        for type_ in ("feat", "chore", "refactor"):
            with self.subTest(type=type_):
                tid, out = self.repair(target, branch, title=f"Repair by {type_}",
                                       type_=type_)
                self.assertRefused(out, "repairs are briefed --type fix")
                self.assertFalse(os.path.exists(self.at("briefs", f"{tid}.md")))

    def test_a_repair_of_a_task_the_queue_does_not_have(self):
        self.project("proj")
        tid = self.add("Repair the thing", base="siana/feat/ghost")
        self.assertRefused(self.brief(tid, "--ship", "--type", "fix",
                                      "--repairs", "ghost"),
                           "no such task to repair: ghost")
        self.assertFalse(os.path.exists(self.at("briefs", f"{tid}.md")))

    def test_a_repair_of_a_task_that_was_never_briefed(self):
        # Its brief is where the branch its work was published from is recorded, and
        # the fallback for a task without one is a name only legacy work carries.
        self.project("proj")
        target = self.add("Ship the thing")
        tid = self.add("Repair the thing", base=f"siana/{target}")
        self.assertRefused(self.brief(tid, "--ship", "--type", "fix",
                                      "--repairs", target), "no brief at")
        self.assertFalse(os.path.exists(self.at("briefs", f"{tid}.md")))

    def test_a_base_that_is_not_the_branch_it_repairs(self):
        # A minion cut from anywhere else starts without the commits it is meant to
        # be fixing, and its accepted head would not be a fast-forward of the branch
        # this records - which publication refuses, after the work is done.
        target, branch = self.published()
        tid, out = self.repair(target, "main")
        self.assertRefused(out, "was cut from main", f"built {branch}")
        self.assertFalse(os.path.exists(self.at("briefs", f"{tid}.md")))

    def test_a_repair_queued_with_no_base_at_all(self):
        target, branch = self.published()
        tid = self.add("Repair the thing")
        self.assertRefused(self.brief(tid, "--ship", "--type", "fix",
                                      "--repairs", target), "was cut from nothing")
        self.assertFalse(os.path.exists(self.at("briefs", f"{tid}.md")))

    def test_a_task_cannot_repair_itself(self):
        self.project("proj")
        tid = self.add("Repair the thing")
        self.assertRefused(self.brief(tid, "--ship", "--type", "fix",
                                      "--repairs", tid), "cannot repair itself")

    def test_two_repair_targets_are_refused(self):
        self.project("proj")
        tid = self.add("Repair the thing")
        self.assertRefused(self.brief(tid, "--ship", "--type", "fix",
                                      "--repairs", "a", "--repairs", "b"),
                           "one repair target at a time")

    def test_a_repair_target_with_no_value_is_refused(self):
        self.project("proj")
        tid = self.add("Repair the thing")
        self.assertRefused(self.brief(tid, "--ship", "--type", "fix", "--repairs"),
                           "--repairs needs the ship task")

    def test_the_joined_spelling_is_accepted_too(self):
        target, branch = self.published()
        tid = self.add("Repair the thing", base=branch)
        self.assertAccepted(self.brief(tid, "--ship", "--type", "fix",
                                       f"--repairs={target}"))
        self.assertEqual(publish.repair_record(self.home, tid), (target, branch))

    def test_a_template_that_does_not_keep_the_record_is_refused(self):
        """The captain's copy of this template is theirs to evolve, and `just init`
        keeps a diverged one on purpose. One that has lost the marker writes a brief
        publication reads as ordinary ship work - which opens the second merge
        request this whole record exists to prevent, on the captain's forge rather
        than here."""
        target, branch = self.published(qa="just qa")
        with open(self.at("brief-ship.md"), "w") as fh:
            fh.write("# Brief\n\n## Delivery: ship\n\n    branch  {SHIP_BRANCH}\n")
        before = self.ids()
        tid = self.add("Repair the thing", base=branch)
        out = self.brief(tid, "--ship", "--type", "fix", "--repairs", target)
        self.assertRefused(out, "does not record this repair where publication looks")
        self.assertFalse(os.path.exists(self.at("briefs", f"{tid}.md")))
        self.assertEqual(self.ids(), before + [tid])

    def test_a_template_recording_it_twice_is_refused(self):
        # Which of two records an accepted repair lands on is not a thing a script
        # may choose between, and `siana-publish` refuses a brief it cannot read one
        # out of.
        target, branch = self.published()
        with open(self.at("brief-ship.md"), "w") as fh:
            fh.write("# Brief\n\n## Delivery: ship\n\n    branch  {SHIP_BRANCH}\n"
                     "{REPAIR}\n{REPAIR}\n")
        tid = self.add("Repair the thing", base=branch)
        out = self.brief(tid, "--ship", "--type", "fix", "--repairs", target)
        self.assertRefused(out, "does not record this repair where publication looks")
        self.assertFalse(os.path.exists(self.at("briefs", f"{tid}.md")))

    def test_the_marker_under_another_heading_is_refused(self):
        # The readers only look inside `## Delivery: ship`, so a template keeping the
        # marker but moving it elsewhere writes prose no command reads.
        target, branch = self.published()
        with open(self.at("brief-ship.md"), "w") as fh:
            fh.write("# Brief\n\n## Delivery: ship\n\n    branch  {SHIP_BRANCH}\n"
                     "\n## What this repairs\n\n{REPAIR}\n")
        tid = self.add("Repair the thing", base=branch)
        out = self.brief(tid, "--ship", "--type", "fix", "--repairs", target)
        self.assertRefused(out, "under no other heading")
        self.assertFalse(os.path.exists(self.at("briefs", f"{tid}.md")))

    def test_a_template_written_before_repairs_existed_still_briefs_ship_work(self):
        # Nothing already in flight is migrated to keep working: a template with no
        # marker at all is only refused for the one thing it cannot record.
        self.project("proj")
        with open(self.at("brief-ship.md"), "w") as fh:
            fh.write("# Brief\n\n## Delivery: ship\n\n    branch  {SHIP_BRANCH}\n")
        tid = self.add("Ship the thing")
        self.assertAccepted(self.brief(tid, "--ship", "--type", "feat"))
        self.assertEqual(d.ship_branch(self.home, tid), f"siana/feat/{tid}")
        self.assertIsNone(publish.repair_record(self.home, tid))


if __name__ == "__main__":
    unittest.main()
