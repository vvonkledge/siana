"""The mailbox: the counter siana-watch raises, and the record that says somebody
is reading it.

This half needs no herdr at all, which is the gain. What used to be a terminal write
into SIANA's pane - unobservable, unconditional, and the source of the bug this
replaced - is now two numbers in two files, and a test can drive both exactly.

Three rules carry the weight. The counter only ever goes up, because a watcher that
restarted it at zero would leave every wake it raised afterwards below the mark the
extension already holds, and the fleet would never wake again while the watcher
reported itself healthy. It is written whole, because the extension reads it while
this writes it. And a watcher with nobody reading refuses to start, because raising a
wake into a home nothing consumes always succeeds and looks, for an afternoon,
exactly like a fleet with nothing to report.

What the extension does with a raised wake is `test_wake.py`. What the loop does per
tick is `test_watch_herdr.py`, where herdr is scripted.
"""

import json
import os
import threading
import unittest

from helpers import HomeTest, gone_pid, script

w = script("siana-watch")


class WakeTest(HomeTest):
    """A home with a wake directory in it, which is what a started SIANA leaves."""

    def setUp(self):
        super().setUp()
        self.wake = w.wake_dir(self.home)
        os.makedirs(self.wake, exist_ok=True)

    def write(self, name, text):
        path = os.path.join(self.wake, name)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def consumer(self, **fields):
        """A consumer record naming this very process, which is what a live pi's
        record names: a pid that answers, running the command it recorded."""
        rec = {"pid": os.getpid(), "command": w.process_command(os.getpid()),
               "started": "2026-08-29T08:00:00Z", **fields}
        return self.write(w.CONSUMER, json.dumps(rec))


class Counter(WakeTest):
    """Reading a number off disk, where absent and unreadable are different answers.

    They have to be: `pending` reading as zero would throw away every wake this
    watcher raised, and `consumed` refusing would stop a watcher over a file only
    the other side writes."""

    def read(self, name):
        return w.read_counter(os.path.join(self.wake, name))

    def test_a_counter_that_was_never_written_is_the_zero(self):
        self.assertEqual(self.read("pending"), 0)

    def test_a_number_is_the_number(self):
        self.write("pending", "17\n")
        self.assertEqual(self.read("pending"), 17)

    def test_whitespace_around_it_does_not_change_it(self):
        self.write("pending", "  17  \n")
        self.assertEqual(self.read("pending"), 17)

    def test_something_that_is_not_a_number_is_not_a_zero(self):
        self.write("pending", "seventeen")
        self.assertIsNone(self.read("pending"))

    def test_an_empty_file_is_not_a_zero(self):
        # The one a half-written counter would look like, if there could be one.
        self.write("pending", "")
        self.assertIsNone(self.read("pending"))

    def test_a_negative_count_is_not_a_count(self):
        self.write("pending", "-3")
        self.assertIsNone(self.read("pending"))


class Pending(WakeTest):
    """The count of wakes raised. Monotonic, atomic, and continued across restarts."""

    def test_a_home_with_no_wake_yet_starts_the_count_at_zero(self):
        self.assertEqual(w.read_pending(self.home), 0)

    def test_a_counter_that_does_not_hold_a_number_refuses_rather_than_restarting(self):
        # Restarting at zero would put every wake this raised below the mark the
        # extension already holds, and nothing would ever wake again.
        self.write(w.PENDING, "seventeen")
        with self.assertRaises(w.Refusal) as cm:
            w.read_pending(self.home)
        self.assertIn("does not hold a number", str(cm.exception))

    def test_raising_a_wake_writes_the_new_count_where_the_extension_reads_it(self):
        self.assertEqual(w.raise_wake(self.home, 1, 0), 1)
        self.assertEqual(w.read_pending(self.home), 1)

    def test_reports_that_land_together_raise_the_count_by_all_of_them(self):
        self.assertEqual(w.raise_wake(self.home, 3, 0), 3)
        self.assertEqual(w.read_pending(self.home), 3)

    def test_a_restart_continues_the_count_and_never_decreases_it(self):
        w.raise_wake(self.home, 2, 0)
        # A second watcher, or the same one started again: it reads where the count
        # stood and carries on from there.
        pending = w.read_pending(self.home)
        self.assertEqual(w.raise_wake(self.home, 1, pending), 3)
        self.assertEqual(w.read_pending(self.home), 3)

    def test_a_reader_never_catches_the_counter_half_written(self):
        # The extension reads this file while this writes it, and a high-water mark
        # read half-written would report wakes as taken that were not. Driven rather
        # than asserted about the code: a reader really does poll it throughout.
        path = os.path.join(self.wake, w.PENDING)
        w.raise_wake(self.home, 1, 0)
        seen, stop = [], threading.Event()

        def reader():
            while not stop.is_set():
                try:
                    with open(path) as fh:
                        seen.append(fh.read())
                except FileNotFoundError:
                    seen.append("")

        thread = threading.Thread(target=reader, daemon=True)
        thread.start()
        pending = 1
        for _ in range(300):
            pending = w.raise_wake(self.home, 1, pending)
        stop.set()
        thread.join(timeout=5)
        self.assertTrue(seen, "the reader never read the counter at all")
        partial = [text for text in seen if not text.strip().isdigit()]
        self.assertEqual(partial, [], "the counter was readable half-written")

    def test_the_staged_write_leaves_nothing_behind_in_the_wake_directory(self):
        # The counter arrives by rename, and the file it was staged in is gone. A
        # leftover would sit in the directory the extension watches and wake it for
        # nothing on every raise.
        w.raise_wake(self.home, 1, 0)
        self.assertEqual(sorted(os.listdir(self.wake)), [w.PENDING])


class Consumer(WakeTest):
    """The record a pi session leaves to say it is reading the wakes.

    It is a liveness handshake and never a grant. The grant is `siana-watch` itself,
    and this only answers whether raising a wake reaches anybody."""

    def test_no_record_is_no_consumer(self):
        self.assertIsNone(w.read_consumer(self.home))

    def test_a_record_nobody_can_read_refuses_rather_than_reading_as_absent(self):
        # "Nothing is reading" and "a record nobody can parse" call for opposite
        # things from the captain.
        self.write(w.CONSUMER, "{half a record")
        with self.assertRaises(w.Refusal) as cm:
            w.read_consumer(self.home)
        self.assertIn("cannot be read", str(cm.exception))

    def test_a_record_that_is_not_a_record_refuses(self):
        self.write(w.CONSUMER, "[1, 2, 3]")
        with self.assertRaises(w.Refusal) as cm:
            w.read_consumer(self.home)
        self.assertIn("not a record pi wrote", str(cm.exception))

    def test_a_live_pi_session_is_confirmed(self):
        self.consumer()
        self.assertEqual(w.confirm_consumer(self.home, "pi")["pid"], os.getpid())

    def test_nothing_reading_is_refused_with_where_the_extension_comes_from(self):
        # Raising a wake into a home nothing consumes always succeeds, so nothing
        # else in that process could ever tell the captain.
        with self.assertRaises(w.Refusal) as cm:
            w.confirm_consumer(self.home, "pi")
        said = str(cm.exception)
        self.assertIn("no pi session is reading SIANA's wakes", said)
        self.assertIn(".pi/extensions/wake.ts", said)
        self.assertIn("just init", said)

    def test_a_pi_that_was_killed_before_it_could_clean_up_is_not_a_consumer(self):
        self.consumer(pid=gone_pid())
        with self.assertRaises(w.Refusal) as cm:
            w.confirm_consumer(self.home, "pi")
        said = str(cm.exception)
        self.assertIn("is not reading wakes", said)
        self.assertIn("is gone", said)
        self.assertIn("start SIANA again", said)

    def test_a_pid_that_now_belongs_to_something_else_is_not_a_consumer(self):
        # Pids are reused. Read as live on the pid alone, any unrelated process
        # would stand in for the session that is supposed to be reading.
        self.consumer(command="/usr/bin/something-that-is-not-pi")
        with self.assertRaises(w.Refusal) as cm:
            w.confirm_consumer(self.home, "pi")
        self.assertIn("not the process that recorded it", str(cm.exception))

    def test_a_claude_siana_is_refused_and_never_served_by_the_old_write(self):
        # There is no collision-free way into a running claude session, and the
        # write this replaced is the bug. A fallback would reinstate it in exactly
        # the state nobody is watching.
        self.consumer()
        with self.assertRaises(w.Refusal) as cm:
            w.confirm_consumer(self.home, "claude")
        said = str(cm.exception)
        self.assertIn("no collision-free wake path", said)
        self.assertIn("siana --harness pi", said)

    def test_the_harness_is_refused_before_the_record_is_even_looked_for(self):
        # A claude home has no consumer record and never will, so reporting the
        # missing record would send the captain installing an extension that could
        # not help them.
        with self.assertRaises(w.Refusal) as cm:
            w.confirm_consumer(self.home, "claude")
        self.assertIn("no collision-free wake path", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
