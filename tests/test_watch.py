"""siana-watch's mechanics: what it reads off the queue, and who it will poke.

The watcher is the captain's autonomy grant, so its two failure modes are both
severe and both silent. Reading the queue wrong drops a minion's report for good,
and the report is the only thing that ever reaches SIANA. Resolving the session
wrong types a poke into a stranger's pane.
"""

import io
import os
import unittest
from contextlib import redirect_stderr

from helpers import HomeTest, script

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
        # SIANA's own writes - add, start, dep - must not poke SIANA. Only what a
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
        # A compacted or rolled store. Something certainly happened, and the poke
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
    """SIANA's pane id, which is the only durable handle back to its session."""

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

    def test_a_recorded_pane_is_returned(self):
        self.store("session", "SIANA_PID=4242", "SIANA_PANE=w3D:p2")
        self.assertEqual(w.read_session(self.home), "w3D:p2")

    def test_whitespace_around_a_field_does_not_change_it(self):
        self.store("session", "SIANA_PANE = w3D:p2 ")
        self.assertEqual(w.read_session(self.home), "w3D:p2")


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
