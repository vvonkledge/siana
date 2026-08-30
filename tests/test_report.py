"""siana-report: the facts a captain's report is made of, read from the live world.

One property is under test throughout, and everything else here is in service of it:
**an unavailable source is never rendered as an empty one.** A herdr that is not
running, a forge with no credentials and a project directory that has been moved all
produce nothing, and reporting nothing as "no open workspaces" is how a report becomes
a lie the captain acts on.

The stores are real and written through `datafile`, so the folding under test is the
folding a real store gets, and the commands the report asks are this distro's own,
driven as processes. Nothing here asserts that a particular source is unavailable:
herdr is installed on the machine that wrote this and is not on a clean runner, so a
test that named it would be green on exactly one of the two. What is asserted is the
rule, against whatever this machine happens to have.
"""

import json
import os
import subprocess
import unittest

from helpers import BIN, HomeTest, script

r = script("siana-report")


class Source(unittest.TestCase):
    """The three states, decided in one place so that "the store answered and holds
    nothing" cannot be spelled two ways in one report."""

    def test_a_source_that_failed_is_unavailable_and_carries_why(self):
        s = r.source("herdr", False, None, "connection refused")
        self.assertEqual(s["state"], "unavailable")
        self.assertEqual(s["why"], "connection refused")

    def test_a_source_that_answered_with_nothing_is_empty(self):
        for nothing in (None, [], {}, ""):
            self.assertEqual(r.source("x", True, nothing)["state"], "empty")

    def test_a_source_that_answered_with_something_is_read(self):
        s = r.source("x", True, [{"id": "a"}])
        self.assertEqual(s["state"], "read")
        self.assertEqual(s["data"], [{"id": "a"}])

    def test_zero_is_read_and_not_empty(self):
        # A count of nothing is a fact the source stated. Folding it into `empty`
        # would lose the difference between "it said zero" and "it said nothing".
        self.assertEqual(r.source("x", True, {"exit": 0, "text": ""})["state"],
                         "read")


class Report(HomeTest):

    def report(self, *args):
        return self.run_cmd([os.path.join(BIN, "siana-report"), *args],
                            env={"PATH": self.distro_path()})

    def sources(self, *args):
        out = self.report("--json", *args)
        self.assertIn(out.returncode, (0, 1), out.stdout + out.stderr)
        return {s["source"]: s for s in json.loads(out.stdout)}

    def test_a_store_with_no_contract_is_unavailable_and_never_empty(self):
        # The contract is what says a store exists. Without one this is a home that
        # was never initialised for it, and "nothing owed" would be the lie.
        s = self.sources()["obligations.jsonl"]
        self.assertEqual(s["state"], "unavailable")
        self.assertIn("no contract", s["why"])

    def test_a_store_with_a_contract_and_no_records_is_empty(self):
        # `datafile` writes the `.jsonl` on the first append, so an absent file with
        # a contract beside it is a real zero.
        self.contract("obligations")
        self.assertEqual(self.sources()["obligations.jsonl"]["state"], "empty")

    def test_a_store_with_records_is_read(self):
        self.contract("obligations")
        self.store("obligations.jsonl",
                   {"id": "owe-one", "kind": "promise", "body": "A thing",
                    "status": "open", "opened": "2026-01-01T00:00:00+00:00"})
        s = self.sources()["obligations.jsonl"]
        self.assertEqual(s["state"], "read")
        self.assertEqual(s["data"][0]["body"], "A thing")

    def test_a_tombstoned_record_is_folded_away(self):
        self.contract("obligations")
        self.store("obligations.jsonl",
                   {"id": "owe-one", "kind": "promise", "body": "A thing",
                    "status": "open", "opened": "2026-01-01T00:00:00+00:00"},
                   {"id": "owe-one", "_deleted": True})
        self.assertEqual(self.sources()["obligations.jsonl"]["state"], "empty")

    def test_a_corrupt_store_is_unavailable_and_names_the_line(self):
        self.contract("obligations")
        self.store("obligations.jsonl", "{half a record")
        s = self.sources()["obligations.jsonl"]
        self.assertEqual(s["state"], "unavailable")
        self.assertIn("obligations.jsonl:1", s["why"])

    def test_the_attended_store_is_read_beside_the_obligations(self):
        # The corpus is a join of two stores, so a report that could only see one of
        # them would show a decision with no reasoning or reasoning with no question.
        self.contract("obligations", "attended")
        self.assertIn("attended.jsonl", self.sources())
        self.assertIn("obligations.jsonl", self.sources())

    def test_the_advisory_ledger_stays_a_separate_source(self):
        self.contract("decisions", "attended")
        names = self.sources()
        self.assertIn("decisions.jsonl", names)
        self.assertIn("attended.jsonl", names)

    def test_a_project_whose_directory_is_gone_is_unavailable(self):
        self.contract("projects")
        self.project("ghost", path=self.at("nowhere"))
        s = self.sources()["repo:ghost"]
        self.assertEqual(s["state"], "unavailable")
        self.assertIn("points somewhere gone", s["why"])

    def test_a_project_that_is_not_a_repository_names_what_failed(self):
        self.contract("projects")
        os.makedirs(self.at("plain"), exist_ok=True)
        self.project("plain", path=self.at("plain"))
        data = self.sources()["repo:plain"]["data"]
        self.assertIsNone(data["branches"])
        self.assertIn("not a git repository", data["branches_why"])

    def test_a_project_with_no_remote_says_so_rather_than_reporting_no_forge(self):
        self.contract("projects")
        path = self.at("repo")
        os.makedirs(path, exist_ok=True)
        subprocess.run(["git", "init", "-q"], cwd=path, check=True)
        self.project("local-only", path=path)
        data = self.sources()["repo:local-only"]["data"]
        self.assertIsNone(data["remote"])
        self.assertIsNone(data["forge"])
        self.assertEqual(data["forge_why"], "no origin remote")

    def test_a_project_handle_that_is_not_registered_is_unavailable(self):
        self.contract("projects")
        s = self.sources("--project", "nothing-like-this")["repo:nothing-like-this"]
        self.assertEqual(s["state"], "unavailable")
        self.assertIn("no project in the registry", s["why"])

    def test_naming_one_project_leaves_the_others_out(self):
        self.contract("projects")
        os.makedirs(self.at("alpha"), exist_ok=True)
        os.makedirs(self.at("beta"), exist_ok=True)
        self.project("alpha", path=self.at("alpha"))
        self.project("beta", path=self.at("beta"))
        names = self.sources("--project", "alpha")
        self.assertIn("repo:alpha", names)
        self.assertNotIn("repo:beta", names)

    def test_the_fleet_commands_are_asked_rather_than_reimplemented(self):
        # A file can say a watcher was started and never that one is running, and
        # only the command that wrote the record knows how to tell those apart.
        names = self.sources()
        for asked in ("watcher", "advisory", "dispatch", "cleanup"):
            self.assertIn(asked, names)
            self.assertIn("text", names[asked]["data"])

    def test_a_command_that_exits_nonzero_is_still_read_and_not_unavailable(self):
        # Several of these exit non-zero to mean "something is outstanding" rather
        # than "I failed". Reading that as unavailable would hide the outstanding
        # thing behind a claim that the source could not be read.
        self.contract("obligations")
        s = self.sources()["dispatch"]
        self.assertEqual(s["state"], "read")

    def test_the_exit_code_says_only_whether_the_picture_is_complete(self):
        # Never non-zero for what the sources say. A fleet with ten blockers is a
        # fleet this read correctly, and a home with no contracts is not.
        self.assertEqual(self.report("--json").returncode, 1)
        self.contract("projects", "obligations", "decisions", "attended")
        self.queue()
        out = self.report("--json")
        unavailable = [s["source"] for s in json.loads(out.stdout)
                       if s["state"] == "unavailable"]
        # Asserted against what this machine actually has rather than against a list
        # of sources: herdr is installed here and is not on CI, and a test that
        # named it would be green on exactly one of the two.
        self.assertEqual(out.returncode, 1 if unavailable else 0, unavailable)

    def test_it_never_writes_anything(self):
        self.contract("obligations")
        # The PATH fixture mirrors directories into the home, so it has to be built
        # before the snapshot or it is what this catches.
        self.distro_path()
        before = sorted(os.listdir(self.home))
        self.report("--json")
        self.assertEqual(sorted(os.listdir(self.home)), before)

    def test_no_home_at_all_is_a_refusal_and_not_an_empty_report(self):
        out = self.run_cmd([os.path.join(BIN, "siana-report")],
                           env={"PATH": self.distro_path(),
                                "SIANA_HOME": self.at("nowhere")},
                           cwd=self.home)
        self.assertRefused(out, "no SIANA home at", "just init")

    def test_the_text_form_names_every_unavailable_source_with_its_reason(self):
        out = self.report()
        self.assertIn("unavailable", out.stdout)
        self.assertIn("why", out.stdout)
        self.assertNotIn("empty", out.stdout.split("obligations.jsonl")[1][:20])


if __name__ == "__main__":
    unittest.main()
