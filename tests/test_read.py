"""siana-read: the one boundary a console is allowed to learn the fleet through.

Everything here is about the difference between an answer and a silence, because
that is the whole reason this command exists. A console renders what it is given,
so a store this could not read must never arrive as a store with nothing in it, and
a herdr that never answered must never arrive as a fleet with no minions. Those two
are the tests that matter most, and they are the ones a passing adapter can fail
quietly forever.

The stores stay real. `datafile` does the fold, so a stub would only ever agree with
whatever this suite already believed about tombstones, defaults and corruption - and
the fold is the one thing here that is not this command's own. Herdr is scripted, for
the reason `tests/fake_herdr.py` gives: the answers worth testing are the ones a live
server could never be made to give on cue.
"""

import json
import os
import shutil
import sys
import unittest
from datetime import UTC, datetime

from fake_herdr import CLOSE, FakeHerdr, HerdrError
from helpers import BIN, HomeTest, gone_pid, script

OK, REFUSED, USAGE = 0, 1, 2


class Read(HomeTest):
    """A home with the contracts in it, and one way to ask this command anything."""

    def setUp(self):
        super().setUp()
        self.queue()
        self.contract("projects", "obligations", "decisions")

    def read(self, *args, env=None):
        """One run, and its document. Parsed here rather than in each test, because
        "exactly one JSON document on stdout" is the contract itself: a run that
        printed something else has already broken it, whatever it exited."""
        out = self.run_bin("siana-read", *args, env=env)
        try:
            doc = json.loads(out.stdout)
        except ValueError as e:
            self.fail(f"stdout is not one JSON document ({e}):\n"
                      f"--- stdout ---\n{out.stdout}\n--- stderr ---\n{out.stderr}")
        return out.returncode, doc

    def text(self, *parts):
        """A file in the home, as a string. Several tests here compare a store or a
        counter before and after a read, and that is the whole assertion: the bytes
        did not move."""
        with open(self.at(*parts)) as fh:
            return fh.read()

    def task(self, **fields):
        """A task written through `tasks`, so it passes the contract a real one does."""
        args = [f"{k}={v}" for k, v in fields.items()]
        out = self.run_cmd(["datafile", "-f", self.at("tasks.jsonl"),
                            "-c", self.at("schema-tasks.yaml"), "put",
                            *sum((["--set", a] for a in args), [])])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def owe(self, **fields):
        args = [f"{k}={v}" for k, v in fields.items()]
        out = self.run_cmd(["datafile", "-f", self.at("obligations.jsonl"),
                            "-c", self.at("schema-obligations.yaml"), "put",
                            *sum((["--set", a] for a in args), [])])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)


class TheFold(Read):
    """What comes back is the store's own materialised record, and nothing hand-made.

    `datafile` owns the fold - last write wins, tombstones removed, contract defaults
    filled in, corruption reported - and this command is a pass-through for it. So
    these drive a store holding every one of those at once, because the failure worth
    catching is not one of them going wrong: it is this command quietly folding the
    log itself and agreeing with the store on the easy cases only.
    """

    def test_the_live_record_is_the_last_one_written(self):
        self.task(id="one", title="first", verify="just test",
                  updated="2026-08-01T00:00:00Z")
        self.task(id="one", title="second", verify="just test",
                  updated="2026-08-02T00:00:00Z")
        code, doc = self.read("tasks")
        self.assertEqual(code, OK, doc)
        self.assertEqual([r["title"] for r in doc["records"]], ["second"])

    def test_a_tombstone_removes_its_record(self):
        self.task(id="one", title="one", verify="just test",
                  updated="2026-08-01T00:00:00Z")
        self.task(id="two", title="two", verify="just test",
                  updated="2026-08-01T00:00:00Z")
        out = self.run_cmd(["datafile", "-f", self.at("tasks.jsonl"),
                            "-c", self.at("schema-tasks.yaml"), "delete", "one"])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        code, doc = self.read("tasks")
        self.assertEqual(code, OK, doc)
        self.assertEqual([r["id"] for r in doc["records"]], ["two"])

    def test_contract_defaults_are_materialised(self):
        # Written with no `status` and no `deps`, the way a record predating a field
        # is. The console must read the value the contract gives it, not a missing
        # key it would have to know the default for.
        self.store("tasks.jsonl", {"id": "bare", "title": "bare",
                                   "verify": "just test",
                                   "updated": "2026-08-01T00:00:00Z"})
        code, doc = self.read("tasks")
        self.assertEqual(code, OK, doc)
        self.assertEqual(doc["records"][0]["status"], "todo")
        self.assertEqual(doc["records"][0]["deps"], [])
        # An absent optional lands as null rather than as no key at all, which is
        # what `bin/siana-owe:84-92` had to be written against.
        self.assertIsNone(doc["records"][0]["project"])

    def test_every_kind_of_bad_line_is_reported_beside_the_good_records(self):
        """The whole shape at once: updates, a tombstone, defaults, invalid JSON, a
        contract violation and a torn tail in one store.

        Corruption `datafile` can still read is a successful answer with the damage
        attached, never a refusal. A console has to be able to render "here is the
        queue, and three lines of it are unreadable", and it can only do that if both
        halves arrive together.
        """
        self.task(id="live", title="first", verify="just test",
                  updated="2026-08-01T00:00:00Z")
        self.task(id="live", title="second", verify="just test",
                  updated="2026-08-02T00:00:00Z")
        self.task(id="gone", title="gone", verify="just test",
                  updated="2026-08-01T00:00:00Z")
        out = self.run_cmd(["datafile", "-f", self.at("tasks.jsonl"),
                            "-c", self.at("schema-tasks.yaml"), "delete", "gone"])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.store("tasks.jsonl",
                   {"id": "bare", "title": "bare", "verify": "just test",
                    "updated": "2026-08-01T00:00:00Z"},
                   "{not json",
                   {"id": "wrong", "title": "wrong", "verify": "just test",
                    "updated": "not-a-time"})
        with open(self.at("tasks.jsonl"), "a") as fh:
            fh.write('{"id": "torn", "title": "half')

        code, doc = self.read("tasks")
        self.assertEqual(code, OK, doc)
        self.assertEqual([r["id"] for r in doc["records"]], ["live", "bare"])
        self.assertEqual([r["title"] for r in doc["records"]], ["second", "bare"])
        self.assertEqual(doc["records"][1]["status"], "todo")

        reasons = " | ".join(b["reason"] for b in doc["bad_lines"])
        self.assertEqual(len(doc["bad_lines"]), 3, reasons)
        self.assertIn("invalid json", reasons)
        self.assertIn("contract violation", reasons)
        self.assertIn("torn tail", reasons)
        # Where each one is, so the captain can go and look rather than take this
        # command's word for it.
        for bad in doc["bad_lines"]:
            self.assertIn("line", bad)
            self.assertIn("raw", bad)

    def test_a_value_arrives_whole_and_not_as_a_display_of_itself(self):
        """The proof that this reads the JSON boundary and not the human page.

        `datafile`'s table truncates a long cell and escapes a comma to survive TOON.
        A value longer than that cap, and one holding the characters that page has to
        quote, both come back byte for byte here - which they could not if this were
        reading the display and unpicking it.
        """
        long_verify = "just test " + ("x" * 2000)
        awkward = 'a title with, a comma "and quotes"'
        self.task(id="wide", title=awkward, verify=long_verify,
                  updated="2026-08-01T00:00:00Z")
        code, doc = self.read("tasks")
        self.assertEqual(code, OK, doc)
        self.assertEqual(doc["records"][0]["verify"], long_verify)
        self.assertEqual(doc["records"][0]["title"], awkward)


class TheRevision(Read):
    """The token a console caches against.

    It is the store's own, passed through unchanged. `(inode, size)` and not size
    alone, because `compact` and `roll` rewrite the file in place: a console keyed on
    size would hold a stale page forever across a compaction that changed nothing
    else.
    """

    def test_the_revision_is_the_snapshot_datafile_reported(self):
        self.task(id="one", title="one", verify="just test",
                  updated="2026-08-01T00:00:00Z")
        code, doc = self.read("tasks")
        self.assertEqual(code, OK, doc)
        said = json.loads(self.run_cmd(
            ["datafile", "-f", self.at("tasks.jsonl"),
             "-c", self.at("schema-tasks.yaml"), "list", "--json"]).stdout)
        self.assertEqual(doc["revision"], said["revision"])
        stat = os.stat(self.at("tasks.jsonl"))
        self.assertEqual(doc["revision"]["inode"], stat.st_ino)
        self.assertEqual(doc["revision"]["size"], stat.st_size)

    def test_compaction_changes_the_revision_with_the_records_unchanged(self):
        self.task(id="one", title="one", verify="just test",
                  updated="2026-08-01T00:00:00Z")
        self.task(id="one", title="one", verify="just test",
                  updated="2026-08-02T00:00:00Z")
        before_code, before = self.read("tasks")
        self.assertEqual(before_code, OK, before)
        out = self.run_cmd(["datafile", "-f", self.at("tasks.jsonl"),
                            "-c", self.at("schema-tasks.yaml"), "compact"])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        after_code, after = self.read("tasks")
        self.assertEqual(after_code, OK, after)
        self.assertEqual(after["records"], before["records"])
        self.assertNotEqual(after["revision"]["inode"],
                            before["revision"]["inode"])


class NeverAnEmptyStore(Read):
    """A store this could not read is a stop, and never `records: []`.

    This is the refusal the whole command is built around. `[]` for an unreadable
    `obligations.jsonl` tells the captain SIANA owes them nothing, and that is the one
    wrong answer that store must never give. Every path that cannot produce the
    store's own records has to arrive here.
    """

    def assertRefusedRead(self, code, doc, expect=REFUSED):
        self.assertEqual(code, expect, doc)
        self.assertIn("error", doc)
        self.assertIn("code", doc)
        self.assertNotIn("records", doc)

    def test_a_store_that_cannot_be_read_refuses(self):
        self.owe(id="owed", kind="promise", body="something",
                 opened="2026-08-01T00:00:00Z")
        os.chmod(self.at("obligations.jsonl"), 0o000)
        self.addCleanup(os.chmod, self.at("obligations.jsonl"), 0o644)
        code, doc = self.read("obligations")
        self.assertRefusedRead(code, doc)

    def test_a_home_with_no_contract_refuses_rather_than_reading_as_empty(self):
        """A mistyped `$SIANA_HOME` is the case this catches.

        Every store in it is absent, so a command keyed on the log file would answer
        every question with nothing and look exactly like a fleet at rest. The
        contract is what says a store exists, so a directory holding none of them
        refuses on all four.
        """
        empty = self.at("not-a-home")
        os.makedirs(empty)
        for what in ("tasks", "projects", "obligations", "decisions"):
            code, doc = self.read(what, env={"SIANA_HOME": empty})
            self.assertRefusedRead(code, doc)
            self.assertEqual(doc["code"], "NO_CONTRACT", doc)

    def test_a_store_with_a_contract_and_no_log_yet_is_honestly_empty(self):
        """The other half of the same rule, and the reason it is keyed on the
        contract rather than on the file.

        `decisions.jsonl` does not exist until `siana-gate` writes the first one, so
        an initialised home answers this every night the captain is at the helm. It
        is empty, this knows it is empty, and the null inode says the log has never
        been written rather than that it could not be read.
        """
        code, doc = self.read("decisions")
        self.assertEqual(code, OK, doc)
        self.assertEqual(doc["records"], [])
        self.assertIsNone(doc["revision"]["inode"])

    def test_the_queue_is_read_where_the_rest_of_the_fleet_reads_it(self):
        """`SIANA_TASKS_FILE` points at the queue, and this follows it.

        That variable is how a minion is pointed at the fleet queue from outside its
        home, and `siana-dispatch` exports it into every minion's environment. A
        command that ignored it would answer about a different file than `tasks` and
        every other command in `bin/` do.
        """
        elsewhere = self.at("elsewhere")
        os.makedirs(elsewhere)
        out = self.run_cmd(["tasks", "init"], cwd=elsewhere)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        queue = os.path.join(elsewhere, "tasks.jsonl")
        self.run_cmd(["datafile", "-f", queue,
                      "-c", os.path.join(elsewhere, "schema-tasks.yaml"), "put",
                      "--set", "id=away", "--set", "title=away",
                      "--set", "verify=just test",
                      "--set", "updated=2026-08-01T00:00:00Z"])
        code, doc = self.read("tasks", env={"SIANA_TASKS_FILE": queue})
        self.assertEqual(code, OK, doc)
        self.assertEqual([r["id"] for r in doc["records"]], ["away"])

    def test_a_queue_outside_the_home_is_never_read_as_the_empty_home_one(self):
        """The quiet shape of the same bug, and the reason it is worth a test.

        An initialised home always has `schema-tasks.yaml`, so a command keyed on the
        home would pass its contract check, find no `tasks.jsonl` beside it, and
        answer `records: []` with exit 0 - a full queue rendered as an empty fleet,
        which is the one answer this command exists not to give.
        """
        elsewhere = self.at("elsewhere")
        os.makedirs(elsewhere)
        out = self.run_cmd(["tasks", "init"], cwd=elsewhere)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        queue = os.path.join(elsewhere, "tasks.jsonl")
        self.run_cmd(["datafile", "-f", queue,
                      "-c", os.path.join(elsewhere, "schema-tasks.yaml"), "put",
                      "--set", "id=away", "--set", "title=away",
                      "--set", "verify=just test",
                      "--set", "updated=2026-08-01T00:00:00Z"])
        # The home has its contract and no queue of its own, which is exactly the
        # state that made the wrong answer look like a right one - and it is the
        # state `tasks init` leaves, because the log is written on the first task.
        self.assertTrue(os.path.isfile(self.at("schema-tasks.yaml")))
        self.assertFalse(os.path.isfile(self.at("tasks.jsonl")))
        code, doc = self.read("tasks", env={"SIANA_TASKS_FILE": queue})
        self.assertEqual(code, OK, doc)
        self.assertEqual(doc["total"], 1, doc)

    def test_datafile_missing_from_the_path_refuses(self):
        # Nothing here can fold a store on its own, so a `datafile` it cannot run is
        # a stop. The failure this prevents is the tempting fallback: a hand-rolled
        # read that works until the day a tombstone or a default matters.
        #
        # Python stays on the PATH, because the command's own shebang needs it: a
        # PATH with nothing on it at all would refuse before reaching the code this
        # is about.
        bare = self.at("bare-path")
        os.makedirs(bare)
        path = os.pathsep.join([bare, os.path.dirname(sys.executable)])
        self.assertIsNone(shutil.which("datafile", path=path),
                          "this fixture is meant to hide `datafile`")
        code, doc = self.read("tasks", env={"PATH": path})
        self.assertEqual(code, REFUSED, doc)
        self.assertEqual(doc["code"], "NO_DATAFILE", doc)


class Filters(Read):
    """Narrowing an answer, always on the store's own records.

    Every filter here runs on what `datafile` materialised. Filtering a narrowed or
    truncated view would answer a different question than the one asked and give no
    sign of it, so each of these also says what it filtered and how much it matched.
    """

    def three_tasks(self):
        self.task(id="alpha", title="alpha", verify="just test", status="doing",
                  updated="2026-08-01T00:00:00Z")
        self.task(id="beta", title="beta", verify="just test", status="todo",
                  updated="2026-08-01T00:00:00Z")
        self.task(id="gamma", title="gamma", verify="just test", status="doing",
                  updated="2026-08-01T00:00:00Z")

    def test_status_selects_and_says_what_it_selected(self):
        self.three_tasks()
        code, doc = self.read("tasks", "--status", "doing")
        self.assertEqual(code, OK, doc)
        self.assertEqual([r["id"] for r in doc["records"]], ["alpha", "gamma"])
        self.assertEqual(doc["filter"]["status"], "doing")
        self.assertEqual(doc["total"], 3)
        self.assertEqual(doc["matched"], 2)

    def test_a_status_nothing_is_in_is_visibly_a_filter_and_not_an_empty_fleet(self):
        # The typo case. `--status dnoe` matches nothing, and a bare `[]` would read
        # as a queue with no work in it. `total` beside `matched` is what separates
        # the two without anyone having to ask a second question.
        self.three_tasks()
        code, doc = self.read("tasks", "--status", "dnoe")
        self.assertEqual(code, OK, doc)
        self.assertEqual(doc["records"], [])
        self.assertEqual(doc["matched"], 0)
        self.assertEqual(doc["total"], 3)

    def test_fields_narrow_the_answer_and_keep_their_order(self):
        self.three_tasks()
        code, doc = self.read("tasks", "--fields", "title,id")
        self.assertEqual(code, OK, doc)
        self.assertEqual([list(r) for r in doc["records"]],
                         [["title", "id"]] * 3)

    def test_a_field_the_contract_does_not_have_is_refused(self):
        # Refused by `datafile` against the contract, and relayed with its own exit
        # code: the request was wrong, not the store. A column of nulls would be this
        # command inventing a field the store has never heard of.
        self.three_tasks()
        code, doc = self.read("tasks", "--fields", "id,nope")
        self.assertEqual(code, USAGE, doc)
        self.assertIn("nope", doc["error"])

    def test_a_filter_runs_on_the_record_and_not_on_the_narrowed_view(self):
        """`--fields id` with `--status doing` still filters on `status`.

        The failure this catches is the obvious implementation: narrow first, then
        filter what is left. `status` is not in the projection, so every task would
        match nothing and the queue would read as empty.
        """
        self.three_tasks()
        code, doc = self.read("tasks", "--fields", "id", "--status", "doing")
        self.assertEqual(code, OK, doc)
        self.assertEqual([r["id"] for r in doc["records"]], ["alpha", "gamma"])
        # And the field the filter needed is not left in the answer the caller asked
        # for.
        self.assertEqual([list(r) for r in doc["records"]], [["id"], ["id"]])

    def test_limit_clips_after_filtering_and_never_silently(self):
        self.three_tasks()
        code, doc = self.read("tasks", "--status", "doing", "--limit", "1")
        self.assertEqual(code, OK, doc)
        self.assertEqual([r["id"] for r in doc["records"]], ["alpha"])
        # `matched` above the number returned is how a console knows it was clipped.
        self.assertEqual(doc["matched"], 2)
        self.assertEqual(doc["filter"]["limit"], 1)

    def test_the_default_limit_of_the_store_is_not_inherited(self):
        """More records than `datafile list` returns by default.

        Its own default is 100, and inheriting it would mean this command filtered
        the first hundred tasks and called that the queue - a silent cap on the one
        store that grows without bound.
        """
        for n in range(120):
            self.task(id=f"task-{n:03d}", title=f"task {n}", verify="just test",
                      status="todo", updated="2026-08-01T00:00:00Z")
        code, doc = self.read("tasks")
        self.assertEqual(code, OK, doc)
        self.assertEqual(doc["total"], 120)
        self.assertEqual(len(doc["records"]), 120)

    def test_a_negative_limit_is_refused(self):
        code, doc = self.read("tasks", "--limit", "-1")
        self.assertEqual(code, USAGE, doc)
        self.assertEqual(doc["code"], "BAD_LIMIT", doc)

    def test_obligations_answer_with_what_is_open(self):
        self.owe(id="still-owed", kind="promise", body="open one", status="open",
                 opened="2026-08-01T00:00:00Z")
        self.owe(id="answered", kind="promise", body="closed one", status="closed",
                 opened="2026-08-01T00:00:00Z", closed="2026-08-02T00:00:00Z",
                 answer="it landed")
        code, doc = self.read("obligations")
        self.assertEqual(code, OK, doc)
        self.assertEqual([r["id"] for r in doc["records"]], ["still-owed"])
        self.assertEqual(doc["filter"]["status"], "open")
        self.assertEqual(doc["total"], 2)

    def test_closed_obligations_are_the_other_half_and_never_both(self):
        self.owe(id="still-owed", kind="promise", body="open one", status="open",
                 opened="2026-08-01T00:00:00Z")
        self.owe(id="answered", kind="promise", body="closed one", status="closed",
                 opened="2026-08-01T00:00:00Z", closed="2026-08-02T00:00:00Z",
                 answer="it landed")
        code, doc = self.read("obligations", "--closed")
        self.assertEqual(code, OK, doc)
        self.assertEqual([r["id"] for r in doc["records"]], ["answered"])
        self.assertEqual(doc["records"][0]["answer"], "it landed")

    def decision(self, ident, at):
        self.store("decisions.jsonl",
                   {"id": ident, "at": at, "class": "publish",
                    "action": "siana-publish thing", "verdict": "proposed",
                    "evidence": ["a"], "alternatives": ["b"], "principles": ["c"]})

    def test_since_keeps_the_boundary_and_everything_after_it(self):
        self.decision("early-one", "2026-08-01T00:00:00Z")
        self.decision("on-the-hour", "2026-08-02T00:00:00Z")
        self.decision("later-one", "2026-08-03T00:00:00Z")
        code, doc = self.read("decisions", "--since", "2026-08-02T00:00:00Z")
        self.assertEqual(code, OK, doc)
        # At the bound is inside it: a decision stamped exactly then happened at that
        # moment, and an audit trail that dropped it would be missing the record the
        # captain went looking for.
        self.assertEqual([r["id"] for r in doc["records"]],
                         ["on-the-hour", "later-one"])
        self.assertEqual(doc["total"], 3)

    def test_since_reads_a_bare_date_as_utc(self):
        self.decision("early-one", "2026-08-01T00:00:00Z")
        self.decision("later-one", "2026-08-03T00:00:00Z")
        code, doc = self.read("decisions", "--since", "2026-08-02")
        self.assertEqual(code, OK, doc)
        self.assertEqual([r["id"] for r in doc["records"]], ["later-one"])

    def test_a_since_that_is_not_a_timestamp_is_refused(self):
        code, doc = self.read("decisions", "--since", "last tuesday")
        self.assertEqual(code, USAGE, doc)
        self.assertEqual(doc["code"], "BAD_TIMESTAMP", doc)

    def test_a_stamp_that_will_not_parse_is_never_silently_dropped(self):
        """What the `--since` filter does with a record it cannot date.

        Driven in-process, because the store cannot produce one: `at` is a typed
        datetime in the contract, so a line carrying `"whenever"` is a contract
        violation and arrives as a bad line rather than as a record. That leaves this
        branch reachable only if the boundary changes shape - and the answer then must
        be to keep the record, because dropping an undateable decision would delete
        history from the captain's audit trail on the strength of a guess about when
        it happened.
        """
        r = script("siana-read")
        since = datetime(2026, 8, 2, tzinfo=UTC)
        records = [{"id": "later-one", "at": "2026-08-03T00:00:00Z"},
                   {"id": "early-one", "at": "2026-08-01T00:00:00Z"},
                   {"id": "undateable", "at": "whenever"},
                   {"id": "unstamped", "at": None}]
        kept = [rec["id"] for rec in r.at_least("at", records, since)]
        self.assertEqual(kept, ["later-one", "undateable", "unstamped"])

    def test_a_naive_stamp_in_a_record_is_read_as_utc(self):
        # The stores are stamped in UTC, and comparing an aware bound against a naive
        # record raises rather than answering. A hand-edited row is the only way one
        # gets in, and a traceback is never the answer to it.
        r = script("siana-read")
        since = datetime(2026, 8, 2, tzinfo=UTC)
        records = [{"id": "naive-later", "at": "2026-08-03T00:00:00"},
                   {"id": "naive-early", "at": "2026-08-01T00:00:00"}]
        kept = [rec["id"] for rec in r.at_least("at", records, since)]
        self.assertEqual(kept, ["naive-later"])


class TheFleet(Read):
    """What herdr says, and what its silence does not say.

    Herdr being unreachable is a fact about herdr and about nothing else. Rendered as
    an empty fleet it becomes a claim about every pane in it, and a captain reading
    "no minions" walks away from work that is still running.
    """

    def setUp(self):
        super().setUp()
        self.herdr = FakeHerdr()
        self.herdr.start()
        self.addCleanup(self.herdr.stop)

    def fleet(self, socket_path=None):
        return self.read("fleet", env={
            "HERDR_SOCKET_PATH": socket_path or self.herdr.path})

    AGENT = {"terminal_id": "term_1", "agent": "pi", "agent_status": "working",
             "workspace_id": "w3S", "tab_id": "w3S:t1", "pane_id": "w3S:p1",
             "cwd": "/somewhere", "revision": 7}

    def test_a_live_fleet_is_the_agents_herdr_listed(self):
        self.herdr.reply("agent.list", {"agents": [self.AGENT]})
        code, doc = self.fleet()
        self.assertEqual(code, OK, doc)
        self.assertEqual(doc["state"], "ok")
        self.assertEqual(doc["agents"], [self.AGENT])
        # Timestamped, always. A live reading is worth what its age says, and a
        # console has to render "as of Ns ago" rather than as a standing fact.
        self.assertIn("at", doc)

    def test_the_machine_fields_survive_verbatim(self):
        """Nothing is summarised on the way through.

        What a pane is doing is meaning, and a console that wants to render it needs
        the fields to render from. Dropping the ones this command has no use for would
        decide that for every future consumer, and durable ids are the ones it would
        hurt most to lose: a label is not unique and a pane id is.
        """
        self.herdr.reply("agent.list", {"agents": [self.AGENT]})
        code, doc = self.fleet()
        self.assertEqual(code, OK, doc)
        self.assertEqual(doc["agents"][0], self.AGENT)
        for durable in ("pane_id", "workspace_id", "tab_id", "terminal_id"):
            self.assertIn(durable, doc["agents"][0])

    def test_an_empty_fleet_is_an_answer_and_says_so(self):
        self.herdr.reply("agent.list", {"agents": []})
        code, doc = self.fleet()
        self.assertEqual(code, OK, doc)
        self.assertEqual(doc["state"], "ok")
        self.assertEqual(doc["agents"], [])

    def test_a_herdr_that_is_not_there_is_unknown_and_never_no_minions(self):
        code, doc = self.fleet(socket_path=self.at("no-such-socket"))
        self.assertEqual(code, REFUSED, doc)
        self.assertEqual(doc["state"], "unknown")
        # `null` and not `[]`. This is the whole test: a console rendering this must
        # be unable to draw an empty fleet from it, and an empty list is exactly what
        # it would draw one from.
        self.assertIsNone(doc["agents"])
        self.assertEqual(doc["code"], "HERDR_UNREACHABLE")

    def test_a_herdr_that_stops_mid_request_is_unknown_too(self):
        # It took the connection and then said nothing, so nothing here learned
        # anything about any pane. Same answer as a socket that was never there.
        self.herdr.reply("agent.list", CLOSE)
        code, doc = self.fleet()
        self.assertEqual(code, REFUSED, doc)
        self.assertEqual(doc["state"], "unknown")
        self.assertIsNone(doc["agents"])

    def test_a_herdr_that_refuses_is_an_answer_and_a_refusal(self):
        # It answered, so this is not silence: herdr said no, and guessing past a
        # refusal is how a console would show panes nobody confirmed.
        self.herdr.reply("agent.list", HerdrError("nope", "not today"))
        code, doc = self.fleet()
        self.assertEqual(code, REFUSED, doc)
        self.assertEqual(doc["code"], "HERDR_REFUSED")
        self.assertNotIn("agents", doc)

    def test_a_reply_with_no_agents_array_refuses_rather_than_guessing(self):
        self.herdr.reply("agent.list", {"panes": []})
        code, doc = self.fleet()
        self.assertEqual(code, REFUSED, doc)
        self.assertEqual(doc["code"], "HERDR_MALFORMED")

    def test_a_partial_list_refuses_rather_than_being_a_shorter_fleet(self):
        """One entry herdr's protocol no longer describes, and the answer is refused.

        Reporting the agents it could read would be a fleet with a minion missing from
        it, and nothing in the document would say one was dropped. A shorter fleet is
        the most dangerous shape this can take, because it looks exactly like a real
        one.
        """
        self.herdr.reply("agent.list", {"agents": [self.AGENT, "w3S:p2"]})
        code, doc = self.fleet()
        self.assertEqual(code, REFUSED, doc)
        self.assertEqual(doc["code"], "HERDR_MALFORMED")
        self.assertNotIn("agents", doc)

    def test_the_fleet_is_asked_for_and_nothing_else(self):
        # A read path, and only a read path. `agent.prompt`, `agent.start` and every
        # other write herdr offers are not this command's to call, and the test that
        # none was is the transcript of what it asked.
        self.herdr.reply("agent.list", {"agents": []})
        self.fleet()
        self.assertEqual([name for name, _ in self.herdr.calls], ["agent.list"])


class TheSession(Read):
    """Whether SIANA is at the helm, asked the two ways it has to be asked.

    A pid alone is not a session. Pids are reused, so the process wearing the one in
    the record has to still be a siana, and `bin/siana:158-168` claims the home on
    exactly that pair. A record that outlived its session names a pid something else
    now holds, and reporting that as a live SIANA is how a captain is told the fleet
    is being led by a process that exited hours ago.
    """

    def session(self, **fields):
        with open(self.at("session"), "w") as fh:
            for k, v in fields.items():
                fh.write(f"{k}={v}\n")

    def health(self):
        return self.read("health")

    def test_no_session_recorded_is_the_ordinary_state_and_not_a_fault(self):
        code, doc = self.health()
        self.assertEqual(code, OK, doc)
        self.assertFalse(doc["session"]["present"])
        self.assertFalse(doc["session"]["alive"])
        self.assertIn("no SIANA session", doc["session"]["why"])

    def test_a_live_siana_is_alive(self):
        # This process is running the suite, and its command holds `siana` because
        # the suite lives in this distro. That is the same identity test `bin/siana`
        # makes, driven against a pid that really is running.
        self.session(SIANA_PID=os.getpid(), SIANA_PANE="w1:p1",
                     SIANA_HARNESS="pi")
        code, doc = self.health()
        self.assertEqual(code, OK, doc)
        self.assertEqual(doc["session"]["pid"], os.getpid())
        self.assertEqual(doc["session"]["pane"], "w1:p1")
        self.assertEqual(doc["session"]["harness"], "pi")

    def test_a_dead_pid_is_never_reported_alive(self):
        self.session(SIANA_PID=gone_pid(), SIANA_PANE="w1:p1")
        code, doc = self.health()
        self.assertEqual(code, OK, doc)
        self.assertTrue(doc["session"]["present"])
        self.assertFalse(doc["session"]["alive"])
        self.assertIn("gone", doc["session"]["why"])

    def test_a_pid_wearing_something_else_is_never_reported_alive(self):
        """The reuse case, and the reason liveness is two questions.

        Pid 1 is always running and is never a siana. A check that stopped at "the
        process is there" would call this a live session every time, which is exactly
        what a stale record left behind by a killed SIANA looks like.
        """
        self.session(SIANA_PID=1, SIANA_PANE="w1:p1")
        code, doc = self.health()
        self.assertEqual(code, OK, doc)
        self.assertFalse(doc["session"]["alive"])
        self.assertIn("not a siana", doc["session"]["why"])

    def test_a_record_with_no_pid_is_never_reported_alive(self):
        self.session(SIANA_PANE="w1:p1")
        code, doc = self.health()
        self.assertEqual(code, OK, doc)
        self.assertFalse(doc["session"]["alive"])
        self.assertIn("no pid", doc["session"]["why"])

    def test_a_record_that_cannot_be_opened_is_said_and_not_raised(self):
        """An unreadable session file still leaves a whole document behind.

        This is a diagnostic, so the part that will not open must not take the
        watcher and the counters with it: a captain looking at a fleet that has gone
        quiet needs those most on the day something else is broken. The nonzero exit
        is what stops a console reading the rest as a clean answer.
        """
        self.session(SIANA_PID=os.getpid())
        os.chmod(self.at("session"), 0o000)
        self.addCleanup(os.chmod, self.at("session"), 0o644)
        code, doc = self.health()
        self.assertEqual(code, REFUSED, doc)
        self.assertFalse(doc["session"]["alive"])
        self.assertIsNotNone(doc["session"]["error"])
        # The other two were still read.
        self.assertEqual(doc["watch"]["exit"], 0, doc)
        self.assertEqual(doc["wake"]["pending"], 0, doc)


class TheWakeCounters(Read):
    """The counters, read and left exactly as they were.

    `siana-watch` is the only writer of `pending` and the pi extension the only writer
    of `consumed`, which is why there is no write race in the wake path at all. A
    third process writing either of them - even to tidy one up - is how a wake gets
    counted twice or lost, so this reads and stops.
    """

    def wake(self, **files):
        os.makedirs(self.at("wake"), exist_ok=True)
        for name, text in files.items():
            with open(self.at("wake", name), "w") as fh:
                fh.write(text)

    def test_counters_are_reported(self):
        self.wake(pending="4\n", consumed="2\n")
        code, doc = self.read("health")
        self.assertEqual(code, OK, doc)
        self.assertEqual(doc["wake"]["pending"], 4)
        self.assertEqual(doc["wake"]["consumed"], 2)
        self.assertEqual(doc["wake"]["errors"], [])

    def test_reading_them_does_not_change_them(self):
        self.wake(pending="4\n", consumed="2\n")
        counters = ("pending", "consumed")
        before = {n: self.text("wake", n) for n in counters}
        self.read("health")
        self.read("health")
        after = {n: self.text("wake", n) for n in counters}
        self.assertEqual(after, before)

    def test_an_absent_counter_is_the_zero(self):
        # No wake raised yet, or none taken yet. That is a number, and the wake
        # directory not existing yet is the state a fresh home is in.
        code, doc = self.read("health")
        self.assertEqual(code, OK, doc)
        self.assertEqual(doc["wake"]["pending"], 0)
        self.assertEqual(doc["wake"]["consumed"], 0)

    def test_a_counter_that_is_not_a_number_is_null_and_never_zero(self):
        """The failure this prevents is the quiet one.

        A `pending` reported as zero when it could not be read says the fleet is fully
        caught up at the moment it has stopped waking at all. Null with the reason
        beside it is the same fact told truthfully, and the nonzero exit stops a
        console rendering it as a healthy read.
        """
        self.wake(pending="not a number\n")
        code, doc = self.read("health")
        self.assertEqual(code, REFUSED, doc)
        self.assertIsNone(doc["wake"]["pending"])
        self.assertTrue(doc["wake"]["errors"])

    def test_a_consumer_record_is_checked_the_same_two_ways(self):
        os.makedirs(self.at("wake"), exist_ok=True)
        with open(self.at("wake", "consumer"), "w") as fh:
            json.dump({"pid": gone_pid(), "command": "pi"}, fh)
        code, doc = self.read("health")
        self.assertEqual(code, OK, doc)
        self.assertFalse(doc["wake"]["consumer"]["alive"])
        self.assertIn("gone", doc["wake"]["consumer"]["why"])


class TheWatcher(Read):
    """`siana-watch --status`, in three parts.

    It writes faults to stderr and ok lines to stdout, so a reader that takes either
    stream as the verdict gets it backwards. All three are preserved and none of them
    is collapsed into a judgement here.
    """

    def test_all_three_parts_are_reported(self):
        code, doc = self.read("health")
        self.assertEqual(code, OK, doc)
        for part in ("exit", "stdout", "stderr"):
            self.assertIn(part, doc["watch"])
        self.assertEqual(doc["watch"]["exit"], 0)
        self.assertIn("no watcher", doc["watch"]["stdout"])

    def test_a_watcher_that_stopped_is_reported_with_what_it_said(self):
        """A fault from `--status` arrives whole: the exit code and the stderr text.

        Nothing here decides what to do about it. `siana-watch` already refuses to
        read healthy off its own file, and a second command re-deciding that would be
        two answers to one question.
        """
        with open(self.at("watch"), "w") as fh:
            json.dump({"state": "failed", "stopped": "2026-08-01T00:00:00Z",
                       "reason": "the machine went down"}, fh)
        code, doc = self.read("health")
        self.assertEqual(doc["watch"]["exit"], 1, doc)
        self.assertIn("the machine went down", doc["watch"]["stderr"])
        # The read succeeded. What it found is a fault about the watcher, and this
        # command reports it rather than failing on it: a diagnostic that exited
        # nonzero on every problem it was built to find would be useless the moment
        # there was one.
        self.assertEqual(code, OK, doc)

    def test_text_on_stderr_alone_does_not_make_it_a_failure(self):
        """The exit code is the verdict, and the stream is not.

        A watcher can print to stderr and still have found nothing wrong, so a reader
        that treated any stderr as a fault would report a covered fleet as uncovered
        every time. Driven through the real command, so this is `siana-watch`'s own
        behaviour and not this test's idea of it.
        """
        code, doc = self.read("health")
        self.assertEqual(doc["watch"]["exit"], 0, doc)
        self.assertEqual(code, OK, doc)


class NotOnThisSurface(Read):
    """Project facts are not part of what a console may read, and neither are the
    grants beside them.

    `siana-read` is the boundary a phone learns the fleet through, and it is
    deliberately narrower than the home is. A credential reference and the list of
    which task may spend one are local operational context, and putting them behind
    a read surface would be a decision to widen that boundary rather than a feature
    somebody forgot to finish. This is here so that adding them later is a change
    that turns this suite red and has to be argued for.
    """

    def setUp(self):
        super().setUp()
        self.contract("facts", "grants")
        self.store("facts.jsonl",
                   {"id": "demo/test-user", "project": "demo", "slug": "test-user",
                    "kind": "credential", "account": "qa@example.test",
                    "service": "siana/demo/test-user",
                    "recorded": "2026-08-31T00:00:00Z"})

    def test_neither_store_is_a_command(self):
        for what in ("facts", "grants"):
            with self.subTest(what):
                code, doc = self.read(what)
                self.assertEqual(code, USAGE, doc)
                self.assertEqual(doc["code"], "USAGE_ERROR", doc)

    def test_no_command_answers_with_a_credential_reference(self):
        for what in TheContract.COMMANDS:
            with self.subTest(what):
                _, doc = self.read(what, env={
                    "HERDR_SOCKET_PATH": self.at("no-such-socket")})
                self.assertNotIn("test-user", json.dumps(doc))


class TheContract(Read):
    """What every command promises, whatever happened.

    One JSON document on stdout and an exit code that means something. A console
    parses one thing and never has to guess whether this run was the kind that
    printed prose instead.
    """

    COMMANDS = ("tasks", "projects", "obligations", "decisions", "fleet", "health")

    def test_every_command_answers_with_one_document_naming_its_source(self):
        for what in self.COMMANDS:
            with self.subTest(what):
                _, doc = self.read(what, env={
                    "HERDR_SOCKET_PATH": self.at("no-such-socket")})
                self.assertEqual(doc["source"], what)

    def test_every_store_answers_with_the_same_keys(self):
        for what in ("tasks", "projects", "obligations", "decisions"):
            with self.subTest(what):
                code, doc = self.read(what)
                self.assertEqual(code, OK, doc)
                for key in ("source", "revision", "filter", "total", "matched",
                            "records", "bad_lines"):
                    self.assertIn(key, doc)
                for key in ("inode", "size", "mtime_ns"):
                    self.assertIn(key, doc["revision"])

    def test_a_request_this_command_cannot_parse_is_a_document_too(self):
        """The failure a console is most likely to cause itself.

        A subcommand or a flag this version does not have is argparse's to refuse,
        and argparse writes prose to stderr and leaves stdout empty. That is the
        single-document contract broken exactly where a consumer most needs
        something to parse, so these paths answer like every other refusal here.
        """
        for args in ((), ("nope",), ("tasks", "--bogus"),
                     ("tasks", "--limit", "abc"), ("tasks", "--fields")):
            with self.subTest(args):
                code, doc = self.read(*args)
                self.assertEqual(code, USAGE, doc)
                self.assertEqual(doc["code"], "USAGE_ERROR", doc)
                self.assertIn("error", doc)

    def test_help_is_the_one_exit_that_is_neither(self):
        # A person asking to read the usage, and the only run here that is neither an
        # answer nor a refusal. Named so that its absence from the contract above is
        # a decision on the record rather than a gap nobody noticed.
        out = self.run_bin("siana-read", "--help")
        self.assertEqual(out.returncode, OK, out.stdout + out.stderr)
        self.assertIn("siana-read", out.stdout)

    def test_a_refusal_is_a_document_too(self):
        empty = self.at("not-a-home")
        os.makedirs(empty)
        code, doc = self.read("tasks", env={"SIANA_HOME": empty})
        self.assertNotEqual(code, OK)
        self.assertIn("error", doc)
        self.assertIn("code", doc)

    def test_nothing_is_written_to_the_stores_by_reading_them(self):
        """Read, and only read.

        A `datafile` read may rewrite the `.idx` cache beside a store, which is why
        this checks the stores themselves rather than the whole directory. What must
        never move is an authoritative record, and the log is where those live.
        """
        self.task(id="one", title="one", verify="just test",
                  updated="2026-08-01T00:00:00Z")
        self.owe(id="owed", kind="promise", body="something",
                 opened="2026-08-01T00:00:00Z")
        logs = ("tasks.jsonl", "obligations.jsonl", "projects.jsonl")
        before = {n: self.text(n) for n in logs if os.path.isfile(self.at(n))}
        for what in self.COMMANDS:
            self.read(what, env={"HERDR_SOCKET_PATH": self.at("no-such-socket")})
        after = {n: self.text(n) for n in logs if os.path.isfile(self.at(n))}
        self.assertEqual(after, before)

    def test_it_binds_no_socket_and_reaches_no_network(self):
        """The slice boundary, as a test.

        This is the read adapter and nothing else. Every later layer - a listener, a
        browser, authentication, a tunnel - is a separate slice that can be stopped
        without this one existing, and the way that stays true is that this command
        never learns to listen. A `socket` import is here for herdr's unix socket, so
        the check is on what the source does with it.
        """
        with open(os.path.join(BIN, "siana-read")) as fh:
            source = fh.read()
        for forbidden in ("bind(", "listen(", "AF_INET", "http", "urllib",
                          "socketserver"):
            self.assertNotIn(forbidden, source,
                             f"siana-read must not reach for {forbidden!r}")


if __name__ == "__main__":
    unittest.main()
