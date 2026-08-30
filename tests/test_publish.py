"""What `siana-publish` refuses, and what reaches the forge when it does not.

Publishing is the one thing in this fleet that leaves it. Every test here is either
a way the branch could go out without a second minion having accepted it, or a way
something internal could travel with it.

What travels is the ship minion's handoff and nothing else. The rules of that
document are `test_handoff.py`; what is here is the half only this command can say:
that the copy is bound to the head QA accepted, that a missing or stale one stops the
publish before anything is pushed, and that the brief the work was briefed with no
longer reaches a forge at all.

The last class is the other half of that: while an advisory session runs, the branch
does not leave at all, and what the captain gets instead is a record of what SIANA
would have done. With no session, nothing above it changes, and that is asserted
rather than assumed - a safety feature that quietly made a direct instruction
impossible would be a worse fleet, not a safer one.
"""

import contextlib
import hashlib
import io
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


HANDOFF = """# Handoff

    title  Print one task per line in a shape a script can read
    head   {head}

## Intent

`status` printed a table nobody could parse, so every caller that wanted one field
grew its own fragile `awk`, and each of them broke on a different day.

## Solution

`--json` prints one object per task, from the same records the table is built from,
so the two cannot disagree about what a task is.

## Validation

`just test` covers the flag against an empty queue, one task, and a task carrying
every optional field.

## Hotspots

The empty queue prints `[]` and not nothing: a caller piping this into `jq` sees a
document either way, and that is the case the old table got wrong.

## Risks and boundaries

The table is unchanged and stays the default. Nothing here makes the JSON stable
across versions.
"""


def printed(block):
    """A block of the body as `--dry-run` prints it: every line behind two spaces,
    the blank ones included. What a dry run is for is showing the exact copy, so what
    it shows is asserted exactly."""
    return "".join(f"  {line}\n" for line in block.splitlines())


class Forge(unittest.TestCase):
    def test_recognised_hosts(self):
        self.assertEqual(publish.forge_of("git@gitlab.com:apm-dev/apm-web.git"), "gitlab")
        self.assertEqual(publish.forge_of("https://github.com/o/r.git"), "github")

    def test_unknown_host_is_none(self):
        # Guessing wrong runs a CLI that does not exist, or publishes somewhere
        # nobody asked for. Neither is recoverable by re-running.
        self.assertIsNone(publish.forge_of("git@git.example.internal:o/r.git"))


REPAIR = """# Brief

## Delivery: ship

Your work lands. This branch is the deliverable:

    branch  siana/fix/repair-the-ci
    repairs make-it-typed siana/feat/make-it-typed

Commit there and nowhere else.

## The task

Repair it.
"""


class RepairRecord(HomeTest):
    """Where an accepted repair lands, read off the brief of the work being fixed.

    Everything this cannot read refuses instead of answering None. None means
    ordinary ship work, which opens a merge request of its own, so answering it
    about a brief that says `repairs` is how the duplicate the record exists to
    prevent reaches the forge.
    """

    def brief(self, task_id, text):
        os.makedirs(self.at("briefs"), exist_ok=True)
        with open(self.at("briefs", f"{task_id}.md"), "w") as fh:
            fh.write(text)

    def record(self, text):
        self.brief("repair-the-ci", text)
        return publish.repair_record(self.home, "repair-the-ci")

    def refused(self, text):
        """The refusal, and what it said. `die` writes to stderr on its way out, and
        a suite that let that through would print a wall of expected refusals."""
        said = io.StringIO()
        with contextlib.redirect_stderr(said), self.assertRaises(SystemExit):
            self.record(text)
        return said.getvalue()

    def test_a_repair_names_its_target_and_the_branch_it_lands_on(self):
        self.assertEqual(self.record(REPAIR),
                         ("make-it-typed", "siana/feat/make-it-typed"))

    def test_ordinary_ship_work_records_none(self):
        self.assertIsNone(self.record(BRIEF))

    def test_a_task_with_no_brief_records_none(self):
        self.assertIsNone(publish.repair_record(self.home, "never-briefed"))

    def test_the_record_is_read_inside_the_delivery_section_only(self):
        # A QA brief carries the branch it judges in the same shape, and prose
        # elsewhere in a brief is prose.
        outside = REPAIR.replace("    repairs make-it-typed siana/feat/make-it-typed\n",
                                 "")
        self.assertIsNone(self.record(outside + "\n    repairs a siana/feat/a\n"))

    def test_half_a_record_is_refused_and_never_read_as_none(self):
        # The failure this shape has: read as ordinary ship work, it opens a second
        # merge request for commits the other branch already carries.
        for bad in ("    repairs make-it-typed\n",
                    "    repairs\n",
                    "    repairs make-it-typed siana/feat/make-it-typed extra\n"):
            with self.subTest(bad=bad.strip()):
                text = REPAIR.replace(
                    "    repairs make-it-typed siana/feat/make-it-typed\n", bad)
                self.assertIn("does not record exactly one repair",
                              self.refused(text))

    def test_two_records_are_refused(self):
        # Which request an accepted repair lands on is not something a script may
        # choose between.
        text = REPAIR.replace("Commit there and nowhere else.",
                              "    repairs other siana/feat/other")
        self.assertIn("does not record exactly one repair", self.refused(text))

    def test_the_same_record_twice_is_still_two_records(self):
        text = REPAIR.replace("Commit there and nowhere else.",
                              "    repairs make-it-typed siana/feat/make-it-typed")
        self.assertIn("does not record exactly one repair", self.refused(text))

    def test_a_branch_this_fleet_would_never_publish_is_refused(self):
        for bad in ("main", "../elsewhere", "siana/", "origin/main"):
            with self.subTest(bad=bad):
                self.assertIn("records the repair as",
                              self.refused(REPAIR.replace("siana/feat/make-it-typed",
                                                          bad)))

    def test_a_target_that_is_not_a_task_id_is_refused(self):
        # A name wearing a space is the malformed line above, not this: it is the
        # shape the strict line cannot read at all.
        for bad in ("Make-It-Typed", "../make-it-typed", "1st-attempt"):
            with self.subTest(bad=bad):
                self.assertIn("records the repair as",
                              self.refused(REPAIR.replace("make-it-typed ",
                                                          bad + " ")))

    def test_the_branch_it_publishes_on_is_its_own_until_it_is_a_repair(self):
        self.brief("make-it-typed", BRIEF.replace(
            "Your work lands. Your branch is the deliverable.",
            "    branch  siana/feat/make-it-typed"))
        self.assertEqual(publish.publication_branch(self.home, "make-it-typed"),
                         "siana/feat/make-it-typed")
        self.brief("repair-the-ci", REPAIR)
        self.assertEqual(publish.publication_branch(self.home, "repair-the-ci"),
                         "siana/feat/make-it-typed")


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
                     ["remote", "add", "origin", self.origin()]):
            out = subprocess.run(["git", "-C", self.repo, *argv],
                                 capture_output=True, text=True)
            self.assertEqual(out.returncode, 0, out.stderr)
        self.head = subprocess.run(
            ["git", "-C", self.repo, "rev-parse", "siana/add-json"],
            capture_output=True, text=True).stdout.strip()

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
        self.handoff(HANDOFF.format(head=self.head))

    def origin(self):
        """Where `origin` points.

        A URL and not a repository, because every test that inherits this stops
        before the push. `Opened` overrides it with somewhere a push can land."""
        return "git@gitlab.com:demo/demo.git"

    def handoff(self, text):
        """The copy the ship minion wrote. Every test that publishes needs one, so
        it is part of a well-formed publish rather than something a test adds."""
        os.makedirs(self.at("handoffs"), exist_ok=True)
        with open(self.at("handoffs", "add-json.md"), "w") as fh:
            fh.write(text)


class DryRun(Publishable):
    """A well-formed publish, stopped before it leaves the machine."""

    def dry(self):
        return self.assertAccepted(
            self.run_bin("siana-publish", "qa-add-json", "--dry-run"))

    def test_reports_what_it_would_open(self):
        text = self.dry()
        self.assertIn("branch:  siana/add-json", text)
        self.assertIn("target:  preproduction", text)
        self.assertIn("gitlab", text)

    def test_the_title_and_the_body_are_the_ones_a_human_wrote(self):
        # Exactly what would be opened, which is the whole point of a dry run: the
        # title off the handoff, and every section of it in the published order.
        text = self.dry()
        self.assertIn("title:   Print one task per line in a shape a script can read",
                      text)
        for heading in ("Intent", "Solution", "Validation", "Hotspots",
                        "Risks and boundaries"):
            self.assertIn(f"## {heading}", text)
        self.assertIn("grew its own fragile `awk`", text)

    def test_the_body_names_both_tasks(self):
        # Whoever reviews it can find the contract the work was held to, and the
        # verdict that let it out, without either being pasted in.
        text = self.dry()
        self.assertIn("Shipped by `add-json`, accepted by `qa-add-json`.", text)

    def test_the_review_a_human_never_sees_the_report_of_is_still_stated(self):
        # `Validation` is the section a reader asks the question in, and the ship
        # minion finished before any of the review happened, so this one sentence is
        # the command's to add and nobody else's.
        text = self.dry()
        self.assertIn("Independently reviewed and accepted by a second agent", text)

    def test_nothing_internal_travels(self):
        # The captain's ruling: the QA report stays inside SIANA. A brief is written
        # before the work exists, by an agent briefing a minion, so none of it is
        # review material - not the background, not the scope, and not the contract
        # itself, which used to be the whole body.
        text = self.dry()
        self.assertNotIn("Add a --json flag to the status command.", text)
        self.assertNotIn("`status --json` prints one object per task.", text)
        self.assertNotIn("Done when", text)
        self.assertNotIn("The parser was rewritten", text)
        self.assertNotIn("Do not touch the config loader", text)
        self.assertNotIn("Your work lands", text)

    def test_a_line_carrying_a_note_publishes_its_prose(self):
        """The three defects independent QA found in the parser, driven from this end.

        `test_handoff.py` holds the rules; these are the same three documents seen
        from where they would have reached a forge, because the guarantee that
        matters is about what a reviewer is sent and not about a return value."""
        self.handoff(HANDOFF.format(head=self.head).replace(
            "The empty queue prints `[]` and not nothing: a caller piping this into "
            "`jq` sees a\ndocument either way, and that is the case the old table got "
            "wrong.",
            "The empty queue prints `[]` and not nothing. <!-- a note to self -->\n"
            "A caller piping this into `jq` sees a document either way."))
        text = self.dry()
        self.assertIn(printed(
            "## Hotspots\n\nThe empty queue prints `[]` and not nothing.\n"
            "A caller piping this into `jq` sees a document either way."), text)
        self.assertNotIn("a note to self", text)

    def test_a_markdown_example_of_a_section_reaches_the_forge_as_written(self):
        self.handoff(HANDOFF.format(head=self.head).replace(
            "`--json` prints one object per task, from the same records the table is "
            "built from,\nso the two cannot disagree about what a task is.",
            "`--json` prints one object per task, as in:\n\n```markdown\n## Intent\n"
            "\nwhat was wrong.\n```\n\nand nothing else changes."))
        self.assertIn(printed(
            "## Solution\n\n`--json` prints one object per task, as in:\n\n"
            "```markdown\n## Intent\n\nwhat was wrong.\n```\n\n"
            "and nothing else changes."), self.dry())

    def test_the_captains_home_written_as_a_tilde_stops_it(self):
        self.handoff(HANDOFF.format(head=self.head).replace(
            "`just test` covers", "see ~/.siana/reports; `just test` covers"))
        out = self.run_bin("siana-publish", "qa-add-json", "--dry-run")
        self.assertRefused(out, "names ~/.siana", "nothing was pushed")

    def test_no_handoff_stops_it_before_anything_leaves(self):
        os.remove(self.at("handoffs", "add-json.md"))
        out = self.run_bin("siana-publish", "qa-add-json", "--dry-run")
        self.assertRefused(out, "no handoff for add-json", "--scaffold")

    def test_an_unfilled_handoff_is_refused(self):
        # A merge request describing the work as `{TITLE}` looks like a description
        # and is not one.
        self.handoff(HANDOFF.format(head=self.head).replace(
            "Print one task per line in a shape a script can read", "{TITLE}"))
        out = self.run_bin("siana-publish", "qa-add-json", "--dry-run")
        self.assertRefused(out, "{TITLE}")

    def test_a_handoff_the_branch_has_moved_past(self):
        """The binding between the copy and the work.

        The ship minion writes the handoff, and only then does anything move: a
        second commit, a repaired round, a rebase. A copy left behind by one of those
        describes a commit nobody is publishing, and it stops here rather than
        travelling with work it has never seen."""
        self.handoff(HANDOFF.format(head="0" * 40))
        out = self.run_bin("siana-publish", "qa-add-json", "--dry-run")
        self.assertRefused(out, "describes 000000000000",
                           f"at {self.head[:12]}")

    def test_a_missing_forge_cli_stops_a_real_run_before_it_pushes(self):
        # Discovered after the push, this leaves the branch published with no merge
        # request and nothing on the record saying why.
        out = self.run_bin("siana-publish", "qa-add-json",
                           env={"PATH": self.path_with_no_forge_client()})
        self.assertRefused(out, "glab is not installed",
                           "nowhere to open a merge request")

    def test_a_dry_run_still_describes_the_plan_without_the_cli(self):
        # A dry run changes nothing, so it has to stay readable on a machine that
        # could not carry it out - including CI, which has neither glab nor gh.
        text = self.assertAccepted(self.run_bin(
            "siana-publish", "qa-add-json", "--dry-run",
            env={"PATH": self.path_with_no_forge_client()}))
        self.assertIn("branch:  siana/add-json", text)
        self.assertIn("glab is not installed here", text)

    def test_a_branch_that_is_no_longer_there(self):
        subprocess.run(["git", "-C", self.repo, "checkout", "main"],
                       capture_output=True, text=True)
        subprocess.run(["git", "-C", self.repo, "branch", "-D", "siana/add-json"],
                       capture_output=True, text=True)
        out = self.run_bin("siana-publish", "qa-add-json", "--dry-run")
        self.assertRefused(out, "has no branch siana/add-json")


class TheMergeRequestsThisReplaces(Publishable):
    """The two that landed before this, as fixtures for the failure it prevents.

    Both were made the old way: the ship task's title became the merge request's
    title, and two sections of the brief became its body. `Rebase the advisory AFK
    branch onto merged main` named a branch operation, and said nothing about the
    change recording what SIANA would decide while granting it no authority. `Use
    target merge base safely` did not tell a cold reviewer that the pipeline had been
    reviewing an unrelated oversized diff. Neither was a bad contract. Both were the
    contract, published, which is the thing a contract cannot do.

    Nothing here is about those repositories. What is asserted is that a title and a
    body of that kind can no longer reach a forge from this command, because neither
    of the two places they came from is read any more.
    """

    def setUp(self):
        super().setUp()
        self.store("tasks.jsonl",
                   {"id": "add-json",
                    "title": "Rebase the advisory AFK branch onto merged main",
                    "status": "done", "verify": "just test", "verify_kind": "cmd",
                    "deps": [], "context": [], "project": "demo",
                    "updated": "2026-08-28T09:00:00Z"})
        with open(self.at("briefs", "add-json.md"), "w") as fh:
            fh.write("""# Brief

## Delivery: ship

Your work lands.

## The task

Rebase this branch onto the merged main and rerun the pipeline. Only the commits
this task added are yours; everything else is already on main.

## Done when

`just test` passes, then this branch earns a passing `siana-pipeline run` at its
final head.
""")
        self.handoff(HANDOFF.format(head=self.head).replace(
            "Print one task per line in a shape a script can read",
            "Record what SIANA would decide, and grant it nothing"))

    def published(self):
        return self.assertAccepted(
            self.run_bin("siana-publish", "qa-add-json", "--dry-run"))

    def test_the_task_title_is_not_the_merge_request_title(self):
        text = self.published()
        self.assertIn("title:   Record what SIANA would decide, and grant it nothing",
                      text)
        self.assertNotIn("Rebase the advisory AFK branch onto merged main", text)

    def test_instructions_to_the_implementer_do_not_travel(self):
        # "Only this commit is yours" is a sentence about which minion owns which
        # commit. On a forge it reads as an instruction to the reviewer.
        text = self.published()
        self.assertNotIn("Only the commits", text)
        self.assertNotIn("rerun the pipeline", text)

    def test_the_acceptance_a_minion_was_held_to_does_not_travel(self):
        # Future tense, about a task: it says what would have to happen for the work
        # to be accepted, and by the time anyone reads this it already has.
        text = self.published()
        self.assertNotIn("siana-pipeline run", text)
        self.assertNotIn("earns a passing", text)


class Opened(Publishable):
    """A publish that reaches a forge, with the forge faked and nothing pushed off
    this machine.

    Everything above stops before the push, so the half of this command that actually
    opens a merge request had no test at all: what reached `gh` was asserted only
    through the dry run, which is the one path that deliberately never calls it.

    `origin` is a bare repository beside the home, named so the host check reads it
    as a forge. No credential, no network, and the push is real."""

    def origin(self):
        bare = self.at("github.git")
        out = subprocess.run(["git", "init", "--bare", "-b", "main", bare],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        return bare

    def setUp(self):
        super().setUp()
        self.forge = self.at("forge")
        os.makedirs(self.forge)
        cli = os.path.join(self.home, "forge-bin")
        os.makedirs(cli)
        fake = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fake_forge.py")
        for name in ("gh", "glab"):
            os.symlink(fake, os.path.join(cli, name))
        self.forge_env = {"PATH": self.distro_path(cli), "FAKE_FORGE": self.forge}

    def publish(self, **env):
        return self.run_bin("siana-publish", "qa-add-json",
                            env={**self.forge_env, **env})

    def opened(self):
        with open(os.path.join(self.forge, "prs.json")) as fh:
            return json.load(fh)

    def test_it_pushes_the_branch_and_opens_one_merge_request(self):
        text = self.assertAccepted(self.publish())
        self.assertIn("opened", text)
        upstream = subprocess.run(
            ["git", "-C", self.repo, "rev-parse", "--abbrev-ref",
             "siana/add-json@{upstream}"], capture_output=True, text=True)
        self.assertEqual(upstream.stdout.strip(), "origin/siana/add-json")
        prs = self.opened()
        self.assertEqual(len(prs), 1)
        self.assertEqual(prs[0]["base"], "preproduction")

    def test_what_the_forge_receives_is_the_copy_a_human_wrote(self):
        self.publish()
        pr = self.opened()[0]
        self.assertEqual(pr["title"],
                         "Print one task per line in a shape a script can read")
        self.assertIn("## Hotspots", pr["body"])
        self.assertIn("Independently reviewed and accepted by a second agent",
                      pr["body"])
        self.assertIn("Shipped by `add-json`, accepted by `qa-add-json`.", pr["body"])
        self.assertNotIn("Add a --json flag to the status command.", pr["body"])

    def test_a_second_run_opens_no_second_merge_request(self):
        # Re-running is the intended recovery from an interrupted publish, so it has
        # to be safe rather than merely discouraged.
        self.publish()
        text = self.assertAccepted(self.publish())
        self.assertIn("already open", text)
        self.assertEqual(len(self.opened()), 1)

    def test_a_second_run_carries_the_copy_that_was_accepted(self):
        """The copy can move between two runs: a round is repaired, the handoff is
        rewritten, and the second run is the one carrying what QA actually accepted.
        Reporting the open merge request and leaving it as it was would make that the
        one run whose copy never arrived."""
        self.publish()
        self.handoff(HANDOFF.format(head=self.head).replace(
            "Print one task per line in a shape a script can read",
            "Print one task per line, so callers stop parsing a table"))
        text = self.assertAccepted(self.publish())
        self.assertIn("copy   updated", text)
        prs = self.opened()
        self.assertEqual(len(prs), 1)
        self.assertEqual(prs[0]["title"],
                         "Print one task per line, so callers stop parsing a table")

    def test_a_forge_that_refuses_the_update_says_what_is_still_there(self):
        # The merge request is open and the branch is pushed, so the failure is that
        # it is carrying copy from an earlier run - not that nothing happened.
        self.publish()
        out = self.publish(FAKE_FORGE_FAIL="edit")
        self.assertRefused(out, "refused to update the merge request",
                           "carrying copy from an earlier run")

    def test_a_handoff_the_branch_has_moved_past_pushes_nothing(self):
        self.handoff(HANDOFF.format(head="0" * 40))
        out = self.publish()
        self.assertRefused(out, "describes 000000000000")
        self.assertFalse(os.path.exists(os.path.join(self.forge, "prs.json")))
        upstream = subprocess.run(
            ["git", "-C", self.repo, "rev-parse", "--abbrev-ref",
             "siana/add-json@{upstream}"], capture_output=True, text=True)
        self.assertNotEqual(upstream.returncode, 0, upstream.stdout)


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
                           env={"PATH": self.path_with_no_forge_client()})
        self.assertRefused(out, "glab is not installed")
        self.assertNotIn("--record", out.stderr)

    def test_nothing_is_recorded_in_the_ledger(self):
        # The captain typed this, or told SIANA to, and that is the authority it has
        # always run on. There is no decision to write down.
        self.run_bin("siana-publish", "qa-add-json",
                     env={"PATH": self.path_with_no_forge_client()})
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
