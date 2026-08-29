"""siana-watch's mechanics: what it reads off the queue, whose session it is for,
and whether anyone can tell it is running.

The watcher is the captain's autonomy grant, so its two failure modes are both
severe and both silent. Reading the queue wrong drops a minion's report for good,
and the report is the only thing that ever reaches SIANA. Resolving the session
wrong leaves it raising wakes for a session that is not there.

The third silence is the fleet's: a watcher that stopped looks exactly like a fleet
with nothing to report. That is what the status record answers, and every reading of
it is here, driven off written records rather than off live processes - the one
process any of these needs is the test itself.

The wake it raises is in `test_watch_wake.py`, and what the loop does with a report
once it has one is in `test_watch_herdr.py`, where herdr is scripted.
"""

import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from helpers import HomeTest, gone_pid, script

w = script("siana-watch")


class Reports(HomeTest):
    """New terminal records since a byte offset, and where to read from next."""

    def test_a_queue_with_no_file_yet_is_not_a_fault(self):
        self.assertEqual(w.reports(self.at("tasks.jsonl"), 0), ([], 0))

    def test_a_terminal_record_is_reported_with_its_id_and_status(self):
        q = self.store("tasks.jsonl", {"id": "t1", "status": "done"})
        found, offset = w.reports(q, 0)
        self.assertEqual(found, ["t1 done"])
        self.assertEqual(offset, os.path.getsize(q))

    def test_only_terminal_records_are_reported(self):
        # SIANA's own writes - add, start, dep - must not wake SIANA. Only what a
        # minion appends is a report.
        q = self.store("tasks.jsonl",
                       {"id": "t1", "status": "todo"},
                       {"id": "t1", "status": "doing"},
                       {"id": "t2", "status": "blocked"},
                       {"id": "t1", "status": "done"})
        self.assertEqual(w.reports(q, 0)[0], ["t2 blocked", "t1 done"])

    def test_nothing_new_moves_nothing(self):
        q = self.store("tasks.jsonl", {"id": "t1", "status": "done"})
        _, offset = w.reports(q, 0)
        self.assertEqual(w.reports(q, offset), ([], offset))

    def test_a_half_written_record_is_left_for_the_next_read(self):
        # The one that loses a report for good: an append larger than the writer's
        # buffer reaches disk in more than one write, so the tail of a read is
        # routinely half a record. Consuming it drops that report permanently.
        q = self.store("tasks.jsonl", {"id": "t1", "status": "done"})
        whole = os.path.getsize(q)
        with open(q, "a") as fh:
            fh.write('{"id": "t2", "sta')
        found, offset = w.reports(q, 0)
        self.assertEqual(found, ["t1 done"])
        self.assertEqual(offset, whole, "the partial line must stay unread")

    def test_the_rest_of_a_split_record_is_read_once_it_lands(self):
        q = self.store("tasks.jsonl", {"id": "t1", "status": "done"})
        with open(q, "a") as fh:
            fh.write('{"id": "t2", "sta')
        _, offset = w.reports(q, 0)
        with open(q, "a") as fh:
            fh.write('tus": "blocked"}\n')
        self.assertEqual(w.reports(q, offset)[0], ["t2 blocked"])

    def test_a_block_with_no_newline_at_all_consumes_nothing(self):
        q = self.at("tasks.jsonl")
        with open(q, "w") as fh:
            fh.write('{"id": "t1", "status": "d')
        self.assertEqual(w.reports(q, 0), ([], 0))

    def test_a_log_shorter_than_the_offset_reports_once_and_resumes_from_the_end(self):
        # A compacted or rolled store. Something certainly happened, and the wake
        # carries no content that could be wrong about what.
        q = self.store("tasks.jsonl", {"id": "t1", "status": "done"})
        found, offset = w.reports(q, os.path.getsize(q) + 500)
        self.assertEqual(found, ["the queue was rewritten"])
        self.assertEqual(offset, os.path.getsize(q))

    def test_a_whole_line_that_will_not_parse_is_skipped_and_said_out_loud(self):
        # It is not a partial write, so it is never coming back. The records behind
        # it still have to arrive, and the loss has to be audible: the line may have
        # carried the very report this process exists to deliver.
        q = self.store("tasks.jsonl", "{not json at all}",
                       {"id": "t2", "status": "done"})
        noise = io.StringIO()
        with redirect_stderr(noise):
            self.assertEqual(w.reports(q, 0)[0], ["t2 done"])
        self.assertIn("unreadable record", noise.getvalue())

    def test_a_line_that_parses_to_something_that_is_not_a_record_is_skipped(self):
        q = self.store("tasks.jsonl", "[1, 2, 3]", "null",
                       {"id": "t2", "status": "done"})
        with redirect_stderr(io.StringIO()):
            self.assertEqual(w.reports(q, 0)[0], ["t2 done"])

    def test_blank_lines_are_not_records(self):
        q = self.store("tasks.jsonl", "", {"id": "t1", "status": "done"}, "   ")
        self.assertEqual(w.reports(q, 0)[0], ["t1 done"])


class ReadSession(HomeTest):
    """SIANA's pane id and the harness in it: the only durable handle back to its
    session, and the only record of which agent herdr should be seeing there."""

    def test_no_session_file_means_siana_is_not_running(self):
        with self.assertRaises(w.Refusal) as cm:
            w.read_session(self.home)
        self.assertIn("no SIANA session recorded", str(cm.exception))

    def test_a_session_with_no_pane_says_siana_is_outside_herdr(self):
        # Reporting a missing session would be wrong twice: the session is running,
        # and it is sitting in front of the captain.
        self.store("session", "SIANA_PID=4242")
        with self.assertRaises(w.Refusal) as cm:
            w.read_session(self.home)
        text = str(cm.exception)
        self.assertIn("outside herdr", text)
        self.assertIn("4242", text)
        # And it names the failure it exists to prevent, which is no longer that the
        # wake cannot be delivered - the extension reads a file and does not care
        # where its session runs. It is that nothing could ever tell this watcher
        # that session had gone.
        self.assertIn("nothing here can tell when", text)
        self.assertIn("a watcher that outlives SIANA", text)

    def test_a_recorded_pane_and_harness_are_returned(self):
        self.store("session", "SIANA_PID=4242", "SIANA_PANE=w3D:p2",
                   "SIANA_HARNESS=claude")
        self.assertEqual(w.read_session(self.home), ("w3D:p2", "claude"))

    def test_a_session_written_before_there_was_a_choice_reads_as_pi(self):
        # Not a default standing in for an unknown: the `siana` that wrote a file
        # without the field could only ever have started pi, so that is what the
        # file means rather than a guess at what it might have meant.
        self.store("session", "SIANA_PID=4242", "SIANA_PANE=w3D:p2")
        self.assertEqual(w.read_session(self.home), ("w3D:p2", "pi"))

    def test_whitespace_around_a_field_does_not_change_it(self):
        self.store("session", "SIANA_PANE = w3D:p2 ", "SIANA_HARNESS = claude ")
        self.assertEqual(w.read_session(self.home), ("w3D:p2", "claude"))


class GrantTest(HomeTest):
    """Records written by hand, and read back the way `doctor` reads them.

    The record is evidence about a process and never the grant itself, so the only
    process any of these needs is this one: a record carrying this test's own pid and
    its own command is what a live watcher looks like, and every other reading is a
    record that no longer matches anything running."""

    def written(self, **fields):
        """A record on disk, in whatever shape the test needs to read back."""
        with open(self.at(w.GRANT), "w") as fh:
            json.dump(fields, fh)
        return self.at(w.GRANT)

    def mine(self, **fields):
        """A record that names this very process, which is what a live watcher's
        record names: a pid that answers, running the command it recorded."""
        return self.written(**{"state": "running", "pid": os.getpid(),
                               "command": w.process_command(os.getpid()),
                               "pane": "w1:p1",
                               "started": "2026-08-28T08:00:00Z", **fields})

    def read(self):
        """`--status` without the process: what it printed, and what it exited."""
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = w.check_grant(self.home)
        return code, out.getvalue() + err.getvalue()


class GrantRecord(GrantTest):
    """Claiming the record, leaving it, and taking one over."""

    def test_claiming_records_this_process_where_doctor_will_look(self):
        path = w.claim_grant(self.home, "w1:p1")
        self.assertEqual(path, self.at(w.GRANT))
        with open(path) as fh:
            rec = json.load(fh)
        self.assertEqual(rec["state"], "running")
        self.assertEqual(rec["pid"], os.getpid())
        self.assertEqual(rec["pane"], "w1:p1")
        self.assertEqual(rec["command"], w.process_command(os.getpid()))
        self.assertTrue(rec["started"])

    def test_a_second_watcher_is_refused_while_the_first_still_holds_it(self):
        # Two would raise two wakes for every report, and only one of them could be
        # recorded, so the captain would be told about a watcher while another ran
        # unaccounted for.
        w.claim_grant(self.home, "w1:p1")
        with self.assertRaises(w.Refusal) as cm:
            w.claim_grant(self.home, "w1:p1")
        self.assertIn("a watcher is already running", str(cm.exception))

    def test_the_record_of_a_watcher_that_stopped_is_taken_over_and_said_out_loud(self):
        # Starting a watcher is the captain deciding to move on. But the record it
        # replaces is the only account of what stopped the last one, so it is not
        # quietly overwritten.
        self.written(state="running", pid=gone_pid(), command="/usr/bin/whatever",
                     pane="w1:p1", started="2026-08-27T08:00:00Z")
        said = io.StringIO()
        with redirect_stderr(said):
            w.claim_grant(self.home, "w2:p2")
        self.assertIn("replaced a watcher record that had stopped", said.getvalue())
        self.assertEqual(self.read()[0], 0)

    def test_a_record_nobody_can_read_stops_a_watcher_from_starting(self):
        # It may be a live watcher's record. Claiming over it would put two watchers
        # on one pane, which is the one thing this file exists to prevent.
        with open(self.at(w.GRANT), "w") as fh:
            fh.write("{half a record")
        with self.assertRaises(w.Refusal) as cm:
            w.claim_grant(self.home, "w1:p1")
        self.assertIn("cannot be read", str(cm.exception))

    def test_a_grant_with_nothing_to_identify_it_by_is_refused(self):
        # Without the command, nothing could tell this process from whatever lands
        # on its pid later, and a grant nobody can verify is worse than no grant.
        with mock.patch.object(w, "process_command", return_value=""):
            with self.assertRaises(w.Refusal) as cm:
                w.claim_grant(self.home, "w1:p1")
        self.assertIn("`ps` says nothing about this process", str(cm.exception))
        self.assertFalse(os.path.exists(self.at(w.GRANT)))

    def test_a_clean_stop_withdraws_the_record_with_the_grant(self):
        # A record left behind would read as a watcher that died, and send the
        # captain looking for something that never happened.
        w.claim_grant(self.home, "w1:p1")
        w.release_grant(self.home)
        self.assertFalse(os.path.exists(self.at(w.GRANT)))
        code, said = self.read()
        self.assertEqual(code, 0)
        self.assertIn("no watcher", said)

    def test_releasing_a_record_that_is_already_gone_is_not_a_fault(self):
        w.release_grant(self.home)

    def test_a_watcher_that_stops_badly_leaves_the_reason_behind(self):
        # The refusal it printed went to a screen nobody is reading, which is the
        # whole point of the watcher.
        w.claim_grant(self.home, "w1:p1")
        w.fail_grant(self.home, "pane w1:p1 is not running SIANA\nrestart SIANA")
        with open(self.at(w.GRANT)) as fh:
            rec = json.load(fh)
        self.assertEqual(rec["state"], "failed")
        self.assertEqual(rec["pid"], os.getpid())
        self.assertIn("not running SIANA", rec["reason"])
        self.assertTrue(rec["stopped"])


class GrantStatus(GrantTest):
    """What `--status` says, which is what `just doctor` says.

    Every reading has to answer one question: is the fleet advancing unattended right
    now. A record can only ever say that a watcher was started, so nothing here reads
    healthy off the file - the process behind it is asked, every time."""

    def test_no_record_says_the_fleet_does_not_advance_unattended(self):
        # The ordinary state between sessions, and never a fault. But it is said out
        # loud, because a quiet fleet and a stopped watcher look identical.
        code, said = self.read()
        self.assertEqual(code, 0)
        self.assertIn("no watcher (the fleet does not advance unattended)", said)

    def test_a_record_whose_process_is_still_this_command_reads_as_running(self):
        self.mine()
        code, said = self.read()
        self.assertEqual(code, 0)
        self.assertIn("watcher running", said)
        self.assertIn(f"pid {os.getpid()}", said)
        self.assertIn("pane w1:p1", said)

    def test_a_record_whose_process_is_gone_is_never_read_as_running(self):
        # The crash, the kill, and the machine going down all land here. The fleet
        # stopped advancing at that moment and nothing said so.
        self.written(state="running", pid=gone_pid(), command="/usr/bin/whatever",
                     pane="w1:p1", started="2026-08-27T08:00:00Z")
        code, said = self.read()
        self.assertEqual(code, 1)
        self.assertIn("watcher stopped without saying why", said)
        self.assertIn("is gone", said)
        self.assertIn("start `siana-watch` again", said)

    def test_a_pid_that_now_belongs_to_something_else_is_not_a_grant(self):
        # Pids are reused. A record read as live on the pid alone would hand the
        # captain a grant that any unrelated process could stand in for.
        self.mine(command="/usr/bin/something-that-is-not-a-watcher")
        code, said = self.read()
        self.assertEqual(code, 1)
        self.assertIn("not the process that recorded it", said)

    def test_a_record_that_cannot_say_what_its_process_was_is_not_a_grant(self):
        self.mine(command="")
        code, said = self.read()
        self.assertEqual(code, 1)
        self.assertIn("cannot say what pid", said)

    def test_a_record_that_names_no_pid_is_not_a_grant(self):
        self.mine(pid=None)
        code, said = self.read()
        self.assertEqual(code, 1)
        self.assertIn("names no pid", said)

    def test_a_stopped_watcher_gives_its_reason_and_what_to_do_about_it(self):
        self.written(state="failed", pid=gone_pid(), command="/usr/bin/whatever",
                     pane="w1:p1", started="2026-08-27T08:00:00Z",
                     stopped="2026-08-27T09:30:00Z",
                     reason="pane w1:p1 is not running SIANA: herdr sees claude\n"
                            "that session has exited and its pane was taken over")
        code, said = self.read()
        self.assertEqual(code, 1)
        self.assertIn("watcher stopped at 2026-08-27T09:30:00Z", said)
        self.assertIn("herdr sees claude", said)
        self.assertIn("its pane was taken over", said)
        self.assertIn("start `siana-watch`", said)

    def test_a_stopped_record_is_reported_and_never_repaired(self):
        # Removing it is deciding the captain has read it, and only the captain can
        # decide that.
        path = self.written(state="failed", pid=gone_pid(), command="x",
                            reason="herdr went away")
        self.read()
        self.assertTrue(os.path.exists(path))

    def test_a_record_that_will_not_parse_is_a_fault_and_never_no_watcher(self):
        # "No watcher" and "a record nobody can read" call for opposite things from
        # the captain, and guessing the first reports an uncovered fleet that may be
        # covered.
        with open(self.at(w.GRANT), "w") as fh:
            fh.write("{half a record")
        code, said = self.read()
        self.assertEqual(code, 1)
        self.assertIn("cannot be read", said)

    def test_a_record_this_did_not_write_is_a_fault(self):
        self.written(state="watching", pid=os.getpid())
        code, said = self.read()
        self.assertEqual(code, 1)
        self.assertIn("is not a record this wrote", said)

    def test_a_fault_is_said_in_doctors_own_shape(self):
        # `just doctor` reads as one list. A line that does not carry its column is
        # a line the captain scrolls past.
        self.written(state="running", pid=gone_pid(), command="x")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            w.check_grant(self.home)
        self.assertIn("  stale   watcher", err.getvalue())
        self.assertEqual(out.getvalue(), "", "a problem belongs on the problem stream")


class Grace(unittest.TestCase):
    """The windows are policy, and the policy is what keeps a live fleet from being
    reported dead. Pinned so a change to either has to be deliberate."""

    def test_detection_grace_outlasts_a_herdr_restart(self):
        self.assertGreaterEqual(w.DETECT_GRACE_S, 60)

    def test_a_report_is_held_not_dropped_while_siana_is_mid_turn(self):
        # SETTLE_WARN_S is a warning and never a deadline: SIANA can be blocked on
        # the captain, and the captain can be away.
        self.assertGreater(w.SETTLE_WARN_S, w.DETECT_GRACE_S)

    def test_terminal_is_exactly_what_a_minion_writes(self):
        self.assertEqual(w.TERMINAL, {"done", "blocked"})


if __name__ == "__main__":
    unittest.main()
