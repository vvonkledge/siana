"""Where a task's branch comes from, and why it is read rather than rebuilt.

A ship task's branch carries the Conventional Commit type SIANA stated when it
briefed the work. Nothing infers that type afterwards, so the brief is the fleet's
only record of it, and six commands read that one line: dispatch to make the
worktree, the pipeline to know which branch it is validating, publish to tell a QA
verdict from a ship task, retire to find the tree, reap to know what is live, and
brief itself to say where a repair of that work will land.

Two things are defended here. That the reader answers correctly - including for work
briefed before this convention, which must stay dispatchable without being migrated.
And that the copies of it have not drifted apart, because a reader that disagreed
with its callers would put a second minion on a branch nobody named. `siana-brief`
carries its copy inside a python heredoc, and it is the writer: one that read a brief
differently from the command reading it back would record a repair against a branch
nobody publishes.
"""

import os
import re
import unittest

from helpers import BIN, HomeTest, script

d = script("siana-dispatch")

# Every command that has to know where a task's work lives. Listed rather than
# discovered: a new one that forgot to read the brief is exactly what this cannot
# see, and naming them is how the next agent finds out the set exists.
READERS = ("siana-dispatch", "siana-pipeline", "siana-publish", "siana-retire",
           "siana-reap", "siana-brief")

# The two commands either side of a repair record: `siana-brief` writes it and
# `siana-publish` reads it back at publication. Their copy of the reader for it is
# compared the same way and for the same reason.
REPAIR_READERS = ("siana-brief", "siana-publish")

SHIP = """# Brief

## Delivery: ship

Your work lands. This branch is the deliverable:

    branch  siana/docs/write-it

Commit there and nowhere else.

## The task

Write it.
"""

# What a brief looked like before types existed. Still readable, still dispatchable:
# nothing already in flight is migrated to keep working.
LEGACY = """# Brief

## Delivery: ship

Your work lands. Your branch `siana/$SIANA_TASK_ID` is the deliverable.

## The task

Build it.
"""

QA = """# Brief

## Delivery: qa

The work under review is:

    task    write-it
    branch  siana/docs/write-it

Your worktree is branched from that branch.
"""


def reader_source(name):
    """The shared reader as it stands in one command, from its first constant to the
    end of `ship_branch`. Taken as text because these are commands rather than a
    package: there is nowhere for one copy to live, so the copies are compared."""
    return between(name, "# The one line in a ship brief", "def ship_branch")


def repair_source(name):
    """The same, for the record that says where a repair lands."""
    return between(name, "# The second line a repair's ship brief records",
                   "def publication_branch")


def between(name, first, last):
    with open(os.path.join(BIN, name)) as fh:
        text = fh.read()
    start = text.index(first)
    return text[start:text.index("\n\n\n", text.index(last, start))]


class OneReader(unittest.TestCase):
    """`fold` is duplicated across these commands for the same reason, and nothing
    was comparing the copies. A rule that lives in six files drifts in one of
    them first."""

    def test_every_command_reads_a_branch_the_same_way(self):
        first = reader_source(READERS[0])
        for name in READERS[1:]:
            self.assertEqual(reader_source(name), first,
                             f"{name}'s copy of ship_branch has drifted from "
                             f"{READERS[0]}'s; they answer the same question")

    def test_the_writer_and_the_reader_of_a_repair_record_agree(self):
        # `siana-brief` writes the record and `siana-publish` acts on it. A writer
        # that read a repair target's brief differently would record a repair
        # against a branch publication does not answer, and the first thing to
        # notice would be a second merge request on the captain's forge.
        first = repair_source(REPAIR_READERS[0])
        for name in REPAIR_READERS[1:]:
            self.assertEqual(repair_source(name), first,
                             f"{name}'s copy of repair_record has drifted from "
                             f"{REPAIR_READERS[0]}'s")

    def test_the_repair_reader_is_not_matched_by_something_trivial(self):
        self.assertIn("def repair_record", repair_source(REPAIR_READERS[0]))
        self.assertIn("REPAIR_LINE", repair_source(REPAIR_READERS[0]))

    def test_the_reader_is_not_matched_by_something_trivial(self):
        # A parser that found an empty string would pass the comparison above
        # forever, saying nothing about any of the copies.
        self.assertIn("def ship_branch", reader_source(READERS[0]))
        self.assertIn("BRANCH_LINE", reader_source(READERS[0]))


class ShipBranch(HomeTest):
    """Read through `siana-dispatch`'s copy. The comparison above is what makes one
    copy answer for all of them."""

    def brief(self, task_id, text):
        os.makedirs(self.at("briefs"), exist_ok=True)
        with open(self.at("briefs", f"{task_id}.md"), "w") as fh:
            fh.write(text)

    def test_a_task_with_no_brief_at_all(self):
        # Dispatch does not require a brief, and scout and QA work never records one.
        self.assertEqual(d.ship_branch(self.home, "make-thing"), "siana/make-thing")

    def test_a_ship_brief_names_its_branch(self):
        self.brief("write-it", SHIP)
        self.assertEqual(d.ship_branch(self.home, "write-it"), "siana/docs/write-it")

    def test_a_brief_written_before_types_existed(self):
        # The bootstrap case, and every task briefed before this landed: no line to
        # read, so the name dispatch always used is the answer.
        self.brief("make-thing", LEGACY)
        self.assertEqual(d.ship_branch(self.home, "make-thing"), "siana/make-thing")

    def test_a_qa_brief_does_not_hand_over_the_branch_it_judges(self):
        # A QA brief carries the ship branch in the same shape. Read as the QA
        # task's own it would put that minion on the branch it is meant to be cut
        # from, where nothing it did could be told apart from the work.
        self.brief("qa-write-it", QA)
        self.assertEqual(d.ship_branch(self.home, "qa-write-it"),
                         "siana/qa-write-it")

    def test_a_branch_named_outside_the_delivery_section_is_not_the_record(self):
        self.brief("write-it", SHIP.replace("Write it.",
                                            "Compare against:\n\n    branch  main\n"))
        self.assertEqual(d.ship_branch(self.home, "write-it"), "siana/docs/write-it")

    def test_prose_about_a_branch_is_not_a_record_of_one(self):
        self.brief("write-it", LEGACY.replace(
            "Your branch `siana/$SIANA_TASK_ID` is the deliverable.",
            "    the branch holding this work is the deliverable"))
        self.assertEqual(d.ship_branch(self.home, "write-it"), "siana/write-it")

    def test_two_different_branches_are_refused(self):
        # Which of them holds the work is not something a script may choose.
        self.brief("write-it", SHIP.replace("Commit there and nowhere else.",
                                            "    branch  siana/feat/write-it"))
        with self.assertRaises(d.Refusal) as caught:
            d.ship_branch(self.home, "write-it")
        self.assertIn("names more than one branch", str(caught.exception))

    def test_the_same_branch_said_twice_is_one_branch(self):
        self.brief("write-it", SHIP.replace("Commit there and nowhere else.",
                                            "    branch  siana/docs/write-it"))
        self.assertEqual(d.ship_branch(self.home, "write-it"), "siana/docs/write-it")

    def test_a_branch_this_fleet_would_never_make_is_refused(self):
        # Fail closed. Falling back to `siana/<id>` here would send a worktree, a
        # review or a publish to a branch the brief does not name.
        for bad in ("main", "siana/", "../elsewhere", "siana/docs/"):
            with self.subTest(bad=bad):
                self.brief("write-it", SHIP.replace("siana/docs/write-it", bad))
                with self.assertRaises(d.Refusal) as caught:
                    d.ship_branch(self.home, "write-it")
                self.assertIn("records the branch as", str(caught.exception))


class Colliding(HomeTest):
    """git stores a ref as a path, so `siana/docs` and `siana/docs/write-it` can
    never both exist. Answered before a worktree or a claim is made, because the
    same refusal from git arrives through herdr as a lock failure with nothing in
    it that names the branch in the way."""

    def setUp(self):
        super().setUp()
        self.repo = self.at("repo")
        os.makedirs(self.repo)
        for argv in (["init", "-q", "-b", "main", "."],
                     ["config", "user.email", "t@example.com"],
                     ["config", "user.name", "t"],
                     ["commit", "-q", "--allow-empty", "-m", "base"]):
            out = self.run_cmd(["git", "-C", self.repo, *argv])
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def branch(self, name):
        self.assertEqual(
            self.run_cmd(["git", "-C", self.repo, "branch", name]).returncode, 0)

    def test_nothing_in_the_way(self):
        self.branch("siana/feat/other")
        self.assertEqual(d.colliding_refs(self.repo, "siana/docs/write-it"), [])

    def test_a_branch_standing_where_a_directory_has_to_go(self):
        # A task whose id is a commit type takes the single-segment name, and then
        # no ship branch of that type can be created at all.
        self.branch("siana/docs")
        self.assertEqual(d.colliding_refs(self.repo, "siana/docs/write-it"),
                         ["siana/docs"])

    def test_a_directory_standing_where_a_branch_has_to_go(self):
        self.branch("siana/docs/write-it")
        self.assertEqual(d.colliding_refs(self.repo, "siana/docs"),
                         ["siana/docs/write-it"])

    def test_a_place_that_is_not_a_repository_answers_nothing(self):
        # `worktree.create` refuses that, and says it better than this could.
        self.assertEqual(d.colliding_refs(self.home, "siana/docs/write-it"), [])


if __name__ == "__main__":
    unittest.main()
