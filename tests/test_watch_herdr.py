"""siana-watch's other half: the loop, and everything it refuses before it starts.

`test_watch.py` covers what it reads off the queue and `test_watch_wake.py` covers
the counter it raises. This covers what it does with a report once it has one, which
is the part the captain is trusting when they walk away.

Nothing here types into SIANA's pane any more, and the first test in `Waking` is the
whole of that regression: herdr is asked about the pane and asked nothing else, ever.
What is left for herdr to decide is liveness - a watcher is the autonomy grant, so it
must never keep running after the session it grants for is gone, and must never call
a live session gone because herdr restarted underneath it.

The commands' `while True` is driven here in-process, with herdr scripted. Herdr's
answers are also the only clock a test has, so the queue moves when the loop asks
after the pane - which is exactly when it moves in life.
"""

import contextlib
import io
import json
import os
import signal
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from fake_herdr import CLOSE, FakeHerdr, HerdrError
from helpers import HomeTest, gone_pid, script

w = script("siana-watch")

SIANA = {"agent": {"agent": "pi", "agent_status": "idle"}}
BUSY = {"agent": {"agent": "pi", "agent_status": "working"}}
TAKEOVER = {"agent": {"agent": "claude", "agent_status": "idle"}}
NOBODY = {"agent": {}}
REFUSED = HerdrError("no_agent", "no agent in that pane")


def once(do, answer):
    """Make something happen the moment herdr is next asked, then answer.

    A minion's report lands while the loop is running, and in-process there is no
    other thread to land it. The tick is asking herdr about the pane at that moment,
    so that is where the queue moves."""
    def handler(_params):
        do()
        return answer
    return handler


class Watched:
    def __init__(self, refusal, out, err):
        self.refusal, self.out, self.err = refusal, out, err


class Signals:
    """What the watcher installed, and one of those signals delivered where it lands.

    Recorded rather than installed: `main` runs in this process, so a handler really
    installed here would outlive the test that installed it and turn a later signal
    to the suite into somebody else's KeyboardInterrupt."""

    def __init__(self):
        self.installed = {}

    def install(self, signum, handler):
        self.installed[signum] = handler
        return signal.SIG_DFL

    def deliver(self, signum):
        """The signal arriving mid-sleep, which is where one sent to a background
        watcher arrives.

        A signal the watcher left on its default disposition installed nothing, and
        the process it was sent to would end right here with no line of shutdown
        run - so that is what this says, rather than letting the loop tick on as if
        the signal had never been sent."""
        name = signal.Signals(signum).name
        handler = self.installed.get(signum)
        if handler is None:
            raise AssertionError(
                f"{name} was left on its default disposition, so it ends the "
                "watcher where it lands and nothing withdraws the grant")
        handler(signum, None)
        raise AssertionError(f"the {name} handler returned instead of stopping "
                             "the watcher")


def _interrupt(_seconds):
    """The captain's Ctrl-C, landing where the loop sleeps."""
    raise KeyboardInterrupt


class Clock:
    """The `time` the watcher sees, with only its own sleep replaced.

    Bound onto the module rather than onto `time.sleep`, which is shared with every
    library in this process. `subprocess.run(..., timeout=)` polls for its child
    with `time.sleep`, so patching the function delivered the scripted signal from
    inside the `ps` call in `process_command` - before the watcher had installed
    anything - and the test failed saying SIGTERM was left on its default
    disposition. On Linux only, where that child is not reaped on the first
    `waitpid`; on macOS it is, `time.sleep` is never reached, and the same suite ran
    green for as long as nobody ran it anywhere else."""

    def __init__(self, sleep):
        self.sleep = sleep

    def __getattr__(self, name):
        return getattr(time, name)


class WatchTest(HomeTest):
    """A recorded SIANA session, and a herdr that answers on cue."""

    PANE = "w1:p1"

    def setUp(self):
        super().setUp()
        self.herdr = FakeHerdr().start()
        self.addCleanup(self.herdr.stop)
        self.signals = Signals()
        self.store("session", "SIANA_PID=4242", f"SIANA_PANE={self.PANE}")
        self.wake = w.wake_dir(self.home)
        os.makedirs(self.wake, exist_ok=True)
        self.consumer()

    def consumer(self, **fields):
        """The record a live pi session leaves to say it is reading the wakes.

        This process, because it is the only one here that is certainly alive: the
        watcher asks the operating system about the recorded pid rather than
        believing the file, so a live consumer is a record that names something
        running."""
        rec = {"pid": os.getpid(), "command": w.process_command(os.getpid()),
               "started": "2026-08-29T08:00:00Z", **fields}
        with open(os.path.join(self.wake, w.CONSUMER), "w") as fh:
            json.dump(rec, fh)

    def pending(self):
        return w.read_counter(os.path.join(self.wake, w.PENDING))

    def taken(self, count):
        """SIANA's session recording that it has delivered `count` wakes."""
        path = os.path.join(self.wake, w.CONSUMED)
        with open(path, "w") as fh:
            fh.write(f"{count}\n")

    def client(self):
        return w.Herdr(self.herdr.path, timeout=5.0)

    def reported(self, task_id="t1", status="done"):
        """A minion's terminal record landing in the queue, as `tasks` appends it."""
        return lambda: self.store("tasks.jsonl", {"id": task_id, "status": status})

    def watch(self, grace=None, interval="0", interrupted=False, terminated=False):
        """One `siana-watch`, run until it stops.

        Every one of these ends in a refusal, because a watcher that returns is a
        watcher that stopped watching. Which refusal ends it is the test's to
        script: a pane taken over, or a herdr that never comes back.

        `interrupted` is the other ending, the captain stopping it. The interrupt
        lands where the loop sleeps, which is where a Ctrl-C lands in life.

        `terminated` is that same ending reached the way a background watcher is
        stopped: the signal is delivered to whatever handler this run installed for
        it, so a watcher that installed none stops the test rather than the loop."""
        out, err = io.StringIO(), io.StringIO()
        env = {"SIANA_HOME": self.home, "SIANA_TASKS_FILE": self.at("tasks.jsonl"),
               "HERDR_SOCKET_PATH": self.herdr.path}
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, env))
            stack.enter_context(mock.patch.object(
                sys, "argv", ["siana-watch", "--interval", interval]))
            if grace is not None:
                stack.enter_context(mock.patch.object(w, "DETECT_GRACE_S", grace))
            stack.enter_context(mock.patch.object(w.signal, "signal",
                                                  self.signals.install))
            if terminated:
                stack.enter_context(mock.patch.object(
                    w, "time",
                    Clock(lambda _s: self.signals.deliver(signal.SIGTERM))))
            if interrupted:
                stack.enter_context(mock.patch.object(
                    w, "time", Clock(_interrupt)))
            stack.enter_context(redirect_stdout(out))
            stack.enter_context(redirect_stderr(err))
            try:
                w.main()
                refusal = None
            except w.Refusal as r:
                refusal = r
            except KeyboardInterrupt:
                if not (interrupted or terminated):
                    # The scripted herdr's backstop: this loop was never going to
                    # stop.
                    self.fail(f"the watcher never stopped, after "
                              f"{len(self.herdr.calls)} calls to herdr")
                refusal = None
        return Watched(refusal, out.getvalue(), err.getvalue())

    def status(self):
        """What `just doctor` would say about this home, and what it would exit."""
        said = io.StringIO()
        with redirect_stdout(said), redirect_stderr(said):
            code = w.check_grant(self.home)
        return code, said.getvalue()

    def herdr_methods(self):
        """Every method this watcher called on herdr, once each and in order.

        The regression, in one list. Every herdr write - `agent.prompt`,
        `agent.send_keys`, `pane.send_text`, `pane.send_input` - lands in the same
        input editor the captain types in, so the rule is not "never prompt" but
        "never write", and asserting on the whole set is the only way to hold it."""
        seen = []
        for method, _params in self.herdr.calls:
            if method not in seen:
                seen.append(method)
        return seen


class PaneAgent(WatchTest):
    """What herdr says is in that pane, or nothing when it answers that nothing is.

    The distinction is the whole of it: `None` is herdr's answer, and an unreachable
    herdr has given no answer about this pane or any other."""

    def test_a_pane_herdr_refuses_reads_as_no_agent(self):
        self.herdr.reply("agent.get", REFUSED)
        self.assertIsNone(w.pane_agent(self.client(), self.PANE))

    def test_a_pane_herdr_says_is_empty_reads_as_an_empty_agent(self):
        self.herdr.reply("agent.get", NOBODY)
        self.assertEqual(w.pane_agent(self.client(), self.PANE), {})

    def test_a_herdr_that_says_nothing_is_not_an_answer_about_the_pane(self):
        self.herdr.reply("agent.get", CLOSE)
        with self.assertRaises(w.Unreachable):
            w.pane_agent(self.client(), self.PANE)

    def test_a_herdr_that_is_not_there_is_not_an_answer_about_the_pane(self):
        self.herdr.stop()
        with self.assertRaises(w.Unreachable):
            w.pane_agent(self.client(), self.PANE)


class ConfirmAlive(WatchTest):
    """The check at startup, where an undetected agent is refused outright: the
    captain is at the keyboard and a rerun costs them a second."""

    def test_a_pane_holding_siana_is_confirmed(self):
        self.herdr.reply("agent.get", SIANA)
        agent = w.confirm_alive(self.client(), self.PANE, self.home, "pi")
        self.assertEqual(agent["agent"], "pi")

    def test_a_pane_holding_the_harness_siana_started_in_is_confirmed(self):
        # A SIANA the captain started with `--harness claude`. Refusing it because
        # herdr does not see pi would leave that fleet advancing only on the
        # captain's turns, which is the whole thing a watcher is for.
        self.herdr.reply("agent.get", TAKEOVER)
        agent = w.confirm_alive(self.client(), self.PANE, self.home, "claude")
        self.assertEqual(agent["agent"], "claude")

    def test_a_pane_holding_something_else_names_what_herdr_sees(self):
        self.herdr.reply("agent.get", TAKEOVER)
        with self.assertRaises(w.Refusal) as cm:
            w.confirm_alive(self.client(), self.PANE, self.home, "pi")
        self.assertIn("is not running SIANA: herdr sees claude", str(cm.exception))

    def test_the_other_harness_in_that_pane_is_a_takeover_and_not_a_siana(self):
        # The recorded harness is checked, never the pair of them. A claude SIANA
        # whose pane now holds pi is a pane SIANA has left, and reading "either one
        # will do" would leave the watcher raising wakes for a session that left.
        self.herdr.reply("agent.get", SIANA)
        with self.assertRaises(w.Refusal) as cm:
            w.confirm_alive(self.client(), self.PANE, self.home, "claude")
        self.assertIn("is not running SIANA: herdr sees pi", str(cm.exception))

    def test_a_pane_herdr_has_not_detected_yet_says_wait_and_rerun(self):
        # herdr re-detects agents from the pane after a restart, so an empty answer
        # is routinely a SIANA that is very much alive.
        self.herdr.reply("agent.get", NOBODY)
        with self.assertRaises(w.Refusal) as cm:
            w.confirm_alive(self.client(), self.PANE, self.home, "pi")
        self.assertIn("no agent", str(cm.exception))
        self.assertIn("wait and rerun", str(cm.exception))


class Startup(WatchTest):

    def test_a_watcher_with_no_herdr_says_so_rather_than_watching_nothing(self):
        self.herdr.stop()
        result = self.watch()
        self.assertIsInstance(result.refusal, w.Unreachable)
        self.assertIn("herdr is not reachable", str(result.refusal))

    def test_a_pane_that_is_not_sianas_is_refused_before_anything_is_watched(self):
        self.herdr.reply("agent.get", TAKEOVER)
        result = self.watch()
        self.assertIn("is not running SIANA", str(result.refusal))
        self.assertEqual(self.herdr_methods(), ["agent.get"])

    def test_what_it_is_watching_and_where_the_wake_goes_are_said_at_the_start(self):
        self.herdr.reply("agent.get", SIANA, TAKEOVER)
        result = self.watch()
        self.assertIn(f"watching {self.at('tasks.jsonl')}", result.out)
        self.assertIn(f"waking   SIANA at {self.PANE} through {self.wake}",
                      result.out)

    def test_a_watcher_with_nothing_reading_its_wakes_never_starts(self):
        # Raising a wake into a home nothing consumes always succeeds, so a watcher
        # that started anyway would count for an afternoon while looking exactly
        # like a fleet with nothing to report. There is no fallback to the terminal
        # write, because that write is the bug.
        os.unlink(os.path.join(self.wake, w.CONSUMER))
        self.herdr.reply("agent.get", SIANA)

        result = self.watch()

        self.assertIn("no pi session is reading SIANA's wakes", str(result.refusal))
        self.assertEqual(self.herdr_methods(), ["agent.get"])

    def test_a_watcher_whose_consumer_was_killed_never_starts(self):
        self.consumer(pid=gone_pid())
        self.herdr.reply("agent.get", SIANA)

        result = self.watch()

        self.assertIn("is not reading wakes", str(result.refusal))
        self.assertIn("start SIANA again", str(result.refusal))

    def test_a_claude_siana_is_refused_rather_than_served_by_the_old_write(self):
        # A claude session cannot be reached without typing into the editor the
        # captain types in. So there is no watcher for one, and the refusal says so
        # rather than quietly reinstating the collision while nobody is watching.
        self.store("session", "SIANA_HARNESS=claude")
        self.herdr.reply("agent.get", TAKEOVER)

        result = self.watch()

        self.assertIn("no collision-free wake path", str(result.refusal))
        self.assertIn("siana --harness pi", str(result.refusal))

    def test_a_refusal_before_the_grant_leaves_no_record_of_a_watcher(self):
        # Startup refuses in front of the captain, who can read it. A record here
        # would say a watcher stopped when none ever started.
        os.unlink(os.path.join(self.wake, w.CONSUMER))
        self.herdr.reply("agent.get", SIANA)

        self.watch()

        self.assertFalse(os.path.exists(self.at(w.GRANT)))


class Waking(WatchTest):
    """A terminal record is the only thing that raises a wake, and the wake is a
    number in a file: it says the queue moved and nothing else, because anything it
    summarised would be a second source of truth able to disagree with the store
    SIANA is about to read anyway."""

    def test_a_terminal_record_raises_a_wake_and_never_writes_into_the_pane(self):
        # The regression this whole rewrite exists for. Every herdr write lands in
        # the editor the captain types in, so the assertion is that herdr was asked
        # about the pane and asked nothing else at all.
        self.herdr.reply("agent.get", SIANA, once(self.reported(), SIANA), TAKEOVER)

        result = self.watch()

        self.assertEqual(self.herdr_methods(), ["agent.get"])
        self.assertEqual(self.pending(), 1)
        self.assertIn("report   t1 done", result.out)
        self.assertIn("raised   1 wake(s); 1 in all", result.out)

    def test_one_report_raises_one_wake_however_long_the_watcher_runs_after(self):
        # The counter has to stop moving once the report is spent, and nothing else
        # here would notice if it did not: every other test scripts the pane being
        # taken over right after, so the loop dies before a second tick can happen.
        self.herdr.reply("agent.get", SIANA, once(self.reported(), SIANA),
                         SIANA, SIANA, SIANA, SIANA, TAKEOVER)

        result = self.watch()

        self.assertEqual(self.pending(), 1)
        self.assertEqual(result.out.count("raised"), 1)

    def test_reports_that_land_together_are_one_wake_carrying_all_of_them(self):
        # The wake carries no content, so a second would only spend a turn saying
        # the same thing. The count still moves by both, because the mark it hands
        # the extension is a high-water mark and not a doorbell.
        def two():
            self.store("tasks.jsonl", {"id": "t1", "status": "done"},
                       {"id": "t2", "status": "blocked"})
        self.herdr.reply("agent.get", SIANA, once(two, SIANA), TAKEOVER)

        result = self.watch()

        self.assertEqual(self.pending(), 2)
        self.assertEqual(result.out.count("raised"), 1)
        self.assertIn("raised   2 wake(s); 2 in all", result.out)

    def test_nothing_terminal_in_the_queue_raises_nothing(self):
        # SIANA's own writes - add, start, dep - must never wake SIANA.
        started = lambda: self.store("tasks.jsonl", {"id": "t1", "status": "doing"})
        self.herdr.reply("agent.get", SIANA, once(started, SIANA), TAKEOVER)

        result = self.watch()

        self.assertEqual(self.pending(), 0)
        self.assertNotIn("raised", result.out)

    def test_a_report_that_lands_mid_turn_is_raised_all_the_same(self):
        # It used to be held, because a prompt sent into a working agent was typed
        # into a turn already in flight. Nothing is typed anywhere now, and the
        # extension is what decides when a raised wake is safe to deliver - so
        # holding it here would only delay the fleet and buy nothing.
        self.herdr.reply("agent.get", SIANA, once(self.reported(), BUSY), TAKEOVER)

        result = self.watch()

        self.assertEqual(self.pending(), 1)
        self.assertIn("raised   1 wake(s)", result.out)

    def test_a_watcher_started_again_continues_the_count_rather_than_restarting(self):
        # A count restarted at zero would sit below the mark the extension already
        # holds, and every wake after it would look like one already delivered: a
        # fleet that never wakes again, with a healthy watcher running the while.
        self.herdr.reply("agent.get", SIANA, once(self.reported(), SIANA), TAKEOVER)
        self.watch()
        # The captain reading the stopped watcher's record and clearing it, which is
        # the whole of what stands between one watcher and the next.
        os.unlink(self.at(w.GRANT))
        self.herdr.reply("agent.get", SIANA,
                         once(self.reported("t2", "blocked"), SIANA), TAKEOVER)

        result = self.watch()

        self.assertEqual(self.pending(), 2)
        self.assertIn("raised   1 wake(s); 2 in all", result.out)

    def test_a_wake_nothing_takes_is_said_out_loud_on_the_settle_cadence(self):
        # A warning and never a deadline: the count is on disk, so a session that
        # comes back drains it however long it was away. But held silently, a
        # captain reading a watcher that is plainly running has no way to know
        # nothing has moved.
        self.herdr.reply("agent.get", SIANA, once(self.reported(), SIANA), SIANA,
                         SIANA, TAKEOVER)

        with mock.patch.object(w, "SETTLE_WARN_S", 0.01):
            result = self.watch(interval="0.02")

        self.assertIn("held     SIANA has taken 0 of 1 wake(s)", result.err)
        # And it says so rather than falling back to writing into the pane, which
        # is the failure this whole path was rewritten to remove.
        self.assertEqual(self.herdr_methods(), ["agent.get"])

    def test_the_held_warning_names_every_cause_and_diagnoses_none(self):
        # All this can see is that the two counters disagree. Four states look like
        # this: a session that is gone, a session mid-turn, one holding every wake
        # behind a draft the captain left in the editor, and one compacting - pi
        # refuses every message for the whole of a `/compact` while still reporting
        # itself idle. They want different things, and naming only the first would
        # send the captain to restart SIANA - which throws away the draft the
        # extension is holding the wake to protect and kills the turn or the
        # compaction the other three are waiting to finish.
        self.herdr.reply("agent.get", SIANA, once(self.reported(), SIANA), SIANA,
                         SIANA, TAKEOVER)

        with mock.patch.object(w, "SETTLE_WARN_S", 0.01):
            result = self.watch(interval="0.02")

        self.assertIn("a wake waits for an idle session with an empty editor",
                      result.err)
        self.assertIn("a long turn, a", result.err)
        self.assertIn("draft left there or a running `/compact` holds it",
                      result.err)
        self.assertIn("`just doctor` says", result.err)
        self.assertIn("whether that session is there at all", result.err)

    def test_a_wake_that_is_taken_is_said_out_loud_and_the_warning_stops(self):
        self.herdr.reply("agent.get", SIANA, once(self.reported(), SIANA),
                         once(lambda: self.taken(1), SIANA), SIANA, TAKEOVER)

        result = self.watch()

        self.assertIn("woken    SIANA has taken all 1 wake(s)", result.out)
        self.assertNotIn("held", result.err)

    def test_a_consumed_mark_nobody_can_read_warns_and_never_stops_the_watcher(self):
        # Only the extension writes that file, so this watcher must not end the
        # grant over it. Reading it as nothing taken can only ever produce the
        # warning, which is the safe direction.
        self.taken("not a number")
        self.herdr.reply("agent.get", SIANA, once(self.reported(), SIANA), SIANA,
                         SIANA, TAKEOVER)

        with mock.patch.object(w, "SETTLE_WARN_S", 0.01):
            result = self.watch(interval="0.02")

        self.assertIn("held     SIANA has taken 0 of 1 wake(s)", result.err)
        self.assertIn("is not running SIANA", str(result.refusal))


class Liveness(WatchTest):
    """This process is the grant, so it must never keep running once the session it
    grants for is gone - and must never call a session gone that is merely being
    re-detected after a herdr restart."""

    def test_a_pane_taken_over_by_another_agent_stops_the_watcher_at_once(self):
        # Not a gap: SIANA has left that pane, so nothing is reading the wakes this
        # would go on raising. A watcher that cannot wake anybody is the fleet
        # quietly stopping and no one being told.
        self.herdr.reply("agent.get", SIANA, TAKEOVER)

        result = self.watch()

        self.assertIn("is not running SIANA: herdr sees claude", str(result.refusal))
        self.assertIn("its pane was taken over", str(result.refusal))

    def test_a_pane_herdr_cannot_identify_still_has_its_wakes_raised(self):
        # This used to hold every poke while nobody could say whose pane it was,
        # because a poke was typed into one. A counter in SIANA's own home cannot be
        # read by somebody else's agent, so holding it would cost timeliness and buy
        # nothing - and the grace is still what keeps a herdr restart from being
        # read as a dead session.
        self.herdr.reply("agent.get", SIANA, once(self.reported(), REFUSED), SIANA,
                         TAKEOVER)

        result = self.watch()

        self.assertIn(f"unsure   herdr sees no agent in {self.PANE}", result.err)
        self.assertIn(f"detected herdr sees SIANA at {self.PANE} again", result.out)
        self.assertEqual(self.pending(), 1)

    def test_a_herdr_that_never_comes_back_ends_the_grant(self):
        # A watcher parked against a herdr that is not returning is the one thing
        # this must never be: a process that reads as a live autonomy grant while it
        # can wake nobody.
        self.herdr.reply("agent.get", SIANA, once(self.reported(), CLOSE), CLOSE)

        result = self.watch(grace=0.2, interval="0.01")

        self.assertIn("without herdr", str(result.refusal))
        self.assertIn("confirming SIANA is in it", str(result.refusal))
        # The report was still counted. Nothing was written anywhere but the
        # counter, which is what a herdr that never answers cannot make unsafe.
        self.assertEqual(self.herdr_methods(), ["agent.get"])
        self.assertEqual(self.pending(), 1)

    def test_liveness_is_checked_every_tick_and_not_only_when_there_is_news(self):
        # A watcher that only looked when it had something to deliver would sit for
        # hours after SIANA exited, with the captain believing the fleet advanced.
        self.herdr.reply("agent.get", SIANA, SIANA, SIANA, TAKEOVER)

        self.watch()

        self.assertEqual(len(self.herdr.calls_to("agent.get")), 4)


class Grant(WatchTest):
    """The record that says whether the fleet is being watched.

    It is written by the watcher and read by `just doctor`, and the whole of its
    value is that the captain cannot get it wrong: a fleet that is quiet because
    nobody is watching has to read differently from a fleet that is quiet because
    there is nothing to report."""

    def test_a_running_watcher_is_visible_while_it_runs(self):
        # Checked from inside the loop, because after it stops the record says
        # something else. Herdr being asked is the tick's own clock.
        seen = []
        self.herdr.reply("agent.get", SIANA,
                         once(lambda: seen.append(self.status()), SIANA), TAKEOVER)

        self.watch()

        code, said = seen[0]
        self.assertEqual(code, 0, said)
        self.assertIn("watcher running", said)
        self.assertIn(f"pane {self.PANE}", said)

    def test_a_refusal_before_the_grant_exists_records_no_grant(self):
        # Startup refuses in front of the captain, who can read it. A record here
        # would say a watcher stopped when none ever started.
        self.herdr.reply("agent.get", TAKEOVER)

        self.watch()

        self.assertFalse(os.path.exists(self.at(w.GRANT)))
        self.assertIn("no watcher", self.status()[1])

    def test_a_watcher_that_loses_sianas_pane_leaves_the_reason_behind(self):
        # The one that matters: this happens while the captain is away, so the
        # refusal goes to a screen nobody is reading. The record is the same words,
        # left where someone will look when the fleet has gone quiet.
        self.herdr.reply("agent.get", SIANA, TAKEOVER)

        self.watch()

        code, said = self.status()
        self.assertEqual(code, 1)
        self.assertIn("watcher stopped at", said)
        self.assertIn("is not running SIANA: herdr sees claude", said)
        self.assertIn("its pane was taken over", said)

    def test_a_watcher_that_outlasts_herdr_leaves_the_reason_behind(self):
        self.herdr.reply("agent.get", SIANA, CLOSE)

        self.watch(grace=0.2, interval="0.01")

        code, said = self.status()
        self.assertEqual(code, 1)
        self.assertIn("without herdr", said)

    def test_the_captain_stopping_a_watcher_withdraws_the_grant_with_it(self):
        # A record left behind would read as a watcher that died, and send the
        # captain looking for a failure that never happened.
        self.herdr.reply("agent.get", SIANA)

        self.watch(interrupted=True)

        self.assertFalse(os.path.exists(self.at(w.GRANT)))
        code, said = self.status()
        self.assertEqual(code, 0)
        self.assertIn("no watcher (the fleet does not advance unattended)", said)

    def test_an_ordinary_kill_withdraws_the_grant_the_same_as_a_ctrl_c(self):
        # `kill` with no signal is SIGTERM, and it is how a watcher left running in
        # the background is stopped: the captain withdrawing the grant, never a
        # crash. On its default disposition SIGTERM ends the process before the
        # record can be withdrawn, and then every ordinary stop reads in `doctor` as
        # a watcher that died - a false alarm on the most common path, in the one
        # report this whole feature exists to make trustworthy. So the signal is
        # delivered to what the watcher installed for it, and never to an interrupt
        # of the test's own: with nothing installed, there is no shutdown to observe.
        self.herdr.reply("agent.get", SIANA)

        self.watch(terminated=True)

        self.assertFalse(os.path.exists(self.at(w.GRANT)))
        code, said = self.status()
        self.assertEqual(code, 0, said)
        self.assertIn("no watcher (the fleet does not advance unattended)", said)

    def test_where_the_grant_is_recorded_is_said_at_the_start(self):
        self.herdr.reply("agent.get", SIANA, TAKEOVER)
        self.assertIn(f"grant    {self.at(w.GRANT)}", self.watch().out)


class Transport(WatchTest):
    """Same protocol as siana-dispatch, and the same distinction to get right."""

    def test_a_reply_too_big_for_one_read_is_assembled_before_it_is_parsed(self):
        self.herdr.reply("agent.get", {"agent": {"agent": "x" * 200_000}})
        agent = self.client().call("agent.get", target=self.PANE)["agent"]
        self.assertEqual(len(agent["agent"]), 200_000)

    def test_a_herdr_that_takes_the_request_and_then_says_nothing_is_unreachable(self):
        # The one thing this process must never be is parked inside `recv` while it
        # looks like a live autonomy grant and polls nothing.
        self.herdr.reply("agent.get", lambda _p: (time.sleep(0.3), {})[1])
        with self.assertRaises(w.Unreachable) as cm:
            w.Herdr(self.herdr.path, timeout=0.05).call("agent.get")
        self.assertIn("stopped answering during agent.get", str(cm.exception))

    def test_a_socket_that_cannot_even_be_made_is_an_unreachable_herdr(self):
        # Out of descriptors is exactly the state where a traceback would end the
        # watcher without telling the captain what to do about it.
        with mock.patch.object(w.socket, "socket",
                               side_effect=OSError(24, "Too many open files")):
            with self.assertRaises(w.Unreachable) as cm:
                self.client().call("agent.get", target=self.PANE)
        self.assertIn("herdr is not reachable", str(cm.exception))
        self.assertIn("Too many open files", str(cm.exception))

    def test_a_connect_that_fails_still_closes_the_socket_it_opened(self):
        # This is the path that repeats: while herdr is away the loop reconnects
        # every tick for the whole detection grace, so a descriptor left for the
        # garbage collector here is the only place they accumulate - and running out
        # of them is how the watcher stops being able to wake anyone.
        opened = []
        real = w.socket.socket

        def spy(*a, **kw):
            opened.append(real(*a, **kw))
            return opened[-1]

        with mock.patch.object(w.socket, "socket", spy):
            with self.assertRaises(w.Unreachable):
                w.Herdr(self.at("nothing-listens-here")).call("agent.get")
        self.assertEqual([s.fileno() for s in opened], [-1], "the socket was left open")

    def test_a_refusal_is_not_an_unreachable(self):
        self.herdr.reply("agent.get", REFUSED)
        with self.assertRaises(w.Refusal) as cm:
            self.client().call("agent.get", target=self.PANE)
        self.assertNotIsInstance(cm.exception, w.Unreachable)
        self.assertIn("no agent in that pane", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
