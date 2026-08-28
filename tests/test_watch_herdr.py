"""siana-watch's other half: the pane it pokes, and the loop that decides when to.

`test_watch.py` covers what it reads off the queue. This covers what it does with
that, which is the part the captain is trusting when they walk away. The watcher is
the autonomy grant, so it has three ways to fail quietly and each is worse than
stopping: a poke typed into a pane that is no longer SIANA's, a poke that herdr
refused and nobody heard about, and a process that keeps running long after the
session it grants for has gone.

The commands' `while True` is driven here in-process, with herdr scripted. Herdr's
answers are also the only clock a test has, so the queue moves when the loop asks
after the pane - which is exactly when it moves in life.
"""

import contextlib
import io
import os
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from fake_herdr import CLOSE, FakeHerdr, HerdrError
from helpers import HomeTest, script

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


class WatchTest(HomeTest):
    """A recorded SIANA session, and a herdr that answers on cue."""

    PANE = "w1:p1"

    def setUp(self):
        super().setUp()
        self.herdr = FakeHerdr(self.at("herdr.sock")).start()
        self.addCleanup(self.herdr.stop)
        self.store("session", "SIANA_PID=4242", f"SIANA_PANE={self.PANE}")

    def client(self):
        return w.Herdr(self.at("herdr.sock"), timeout=5.0)

    def reported(self, task_id="t1", status="done"):
        """A minion's terminal record landing in the queue, as `tasks` appends it."""
        return lambda: self.store("tasks.jsonl", {"id": task_id, "status": status})

    def watch(self, grace=None, interval="0"):
        """One `siana-watch`, run until it stops.

        Every one of these ends in a refusal, because a watcher that returns is a
        watcher that stopped watching. Which refusal ends it is the test's to
        script: a pane taken over, or a herdr that never comes back."""
        out, err = io.StringIO(), io.StringIO()
        env = {"SIANA_HOME": self.home, "SIANA_TASKS_FILE": self.at("tasks.jsonl"),
               "HERDR_SOCKET_PATH": self.at("herdr.sock")}
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, env))
            stack.enter_context(mock.patch.object(
                sys, "argv", ["siana-watch", "--interval", interval]))
            if grace is not None:
                stack.enter_context(mock.patch.object(w, "DETECT_GRACE_S", grace))
            stack.enter_context(redirect_stdout(out))
            stack.enter_context(redirect_stderr(err))
            try:
                w.main()
                refusal = None
            except w.Refusal as r:
                refusal = r
            except KeyboardInterrupt:
                # The scripted herdr's backstop: this loop was never going to stop.
                self.fail(f"the watcher never stopped, after "
                          f"{len(self.herdr.calls)} calls to herdr")
        return Watched(refusal, out.getvalue(), err.getvalue())

    def pokes(self):
        return self.herdr.calls_to("agent.prompt")


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
        agent = w.confirm_alive(self.client(), self.PANE, self.home)
        self.assertEqual(agent["agent"], "pi")

    def test_a_pane_holding_something_else_names_what_herdr_sees(self):
        self.herdr.reply("agent.get", TAKEOVER)
        with self.assertRaises(w.Refusal) as cm:
            w.confirm_alive(self.client(), self.PANE, self.home)
        self.assertIn("is not running SIANA: herdr sees claude", str(cm.exception))

    def test_a_pane_herdr_has_not_detected_yet_says_wait_and_rerun(self):
        # herdr re-detects agents from the pane after a restart, so an empty answer
        # is routinely a SIANA that is very much alive.
        self.herdr.reply("agent.get", NOBODY)
        with self.assertRaises(w.Refusal) as cm:
            w.confirm_alive(self.client(), self.PANE, self.home)
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
        self.assertEqual(self.pokes(), [])

    def test_what_it_is_watching_and_who_it_will_poke_are_said_at_the_start(self):
        self.herdr.reply("agent.get", SIANA, TAKEOVER)
        result = self.watch()
        self.assertIn(f"watching {self.at('tasks.jsonl')}", result.out)
        self.assertIn(f"poking   SIANA at {self.PANE}", result.out)


class Poking(WatchTest):
    """A terminal record is the only thing that pokes anyone, and the poke says the
    queue moved and nothing else: anything it summarised would be a second source of
    truth able to disagree with the store SIANA is about to read anyway."""

    def test_a_terminal_record_wakes_siana_with_a_poke_that_carries_no_content(self):
        self.herdr.reply("agent.get", SIANA, once(self.reported(), SIANA), TAKEOVER)

        result = self.watch()

        poke, = self.pokes()
        self.assertEqual(poke["target"], self.PANE)
        self.assertEqual(poke["text"], "The queue moved. Reconcile it.")
        self.assertIn("report   t1 done", result.out)
        self.assertIn("poked    1 report(s)", result.out)

    def test_reports_that_land_together_cost_one_poke_and_not_one_each(self):
        def two():
            self.store("tasks.jsonl", {"id": "t1", "status": "done"},
                       {"id": "t2", "status": "blocked"})
        self.herdr.reply("agent.get", SIANA, once(two, SIANA), TAKEOVER)

        result = self.watch()

        self.assertEqual(len(self.pokes()), 1)
        self.assertIn("poked    2 report(s)", result.out)

    def test_nothing_terminal_in_the_queue_pokes_nobody(self):
        # SIANA's own writes - add, start, dep - must never poke SIANA.
        started = lambda: self.store("tasks.jsonl", {"id": "t1", "status": "doing"})
        self.herdr.reply("agent.get", SIANA, once(started, SIANA), TAKEOVER)

        result = self.watch()

        self.assertEqual(self.pokes(), [])
        self.assertNotIn("poked", result.out)

    def test_a_report_that_lands_mid_turn_is_held_rather_than_typed_into_the_turn(self):
        # A prompt sent into a working agent is typed into a turn already in flight.
        self.herdr.reply("agent.get", SIANA, once(self.reported(), BUSY), TAKEOVER)

        result = self.watch()

        self.assertEqual(self.pokes(), [])
        self.assertIn("report   t1 done", result.out)
        self.assertNotIn("poked", result.out)

    def test_a_held_report_goes_out_as_soon_as_siana_settles(self):
        self.herdr.reply("agent.get", SIANA, once(self.reported(), BUSY), SIANA,
                         TAKEOVER)

        result = self.watch()

        self.assertEqual(len(self.pokes()), 1)
        self.assertIn("poked    1 report(s)", result.out)

    def test_a_report_held_a_long_time_is_said_out_loud_once(self):
        # A warning and never a deadline: the report is still held, however long
        # SIANA stays mid-turn. But held silently, a captain reading a watcher that
        # is plainly running has no way to know nothing has moved.
        self.herdr.reply("agent.get", SIANA, once(self.reported(), BUSY), BUSY, BUSY,
                         TAKEOVER)

        with mock.patch.object(w, "SETTLE_WARN_S", 0.01):
            result = self.watch(interval="0.02")

        self.assertIn("SIANA has been mid-turn", result.err)
        self.assertIn("1 report(s) held", result.err)
        self.assertEqual(result.err.count("mid-turn"), 1, "said once, not every tick")
        self.assertEqual(self.pokes(), [])

    def test_a_poke_herdr_refuses_is_said_out_loud_and_tried_again(self):
        # A herdr that answers `agent.get` and refuses `agent.prompt` passes the
        # liveness check every tick, so without this nothing would ever mention it
        # and the reports would pile up behind a process that looks fine.
        self.herdr.reply("agent.prompt", HerdrError("no_pane", "cannot type there"), {})
        self.herdr.reply("agent.get", SIANA, once(self.reported(), SIANA), SIANA,
                         TAKEOVER)

        result = self.watch()

        self.assertEqual(len(self.pokes()), 2)
        self.assertIn("held     herdr took no poke", result.err)
        self.assertIn("1 report(s) held", result.err)
        self.assertIn("poked    1 report(s)", result.out)


class Liveness(WatchTest):
    """This process is the grant, so it must never keep running once the session it
    grants for is gone - and must never call a session gone that is merely being
    re-detected after a herdr restart."""

    def test_a_pane_taken_over_by_another_agent_stops_the_watcher_at_once(self):
        # Not a gap: something else holds that pane now, so every poke from here
        # would be typed into somebody else's session. No window makes that safer.
        self.herdr.reply("agent.get", SIANA, TAKEOVER)

        result = self.watch()

        self.assertIn("is not running SIANA: herdr sees claude", str(result.refusal))
        self.assertIn("its pane was taken over", str(result.refusal))

    def test_a_pane_herdr_cannot_identify_holds_every_poke_until_it_can(self):
        # The pane this cannot identify is precisely the pane a poke must not be
        # typed into. A report waiting costs timeliness; a poke into a stranger's
        # session costs the stranger.
        self.herdr.reply("agent.get", SIANA, once(self.reported(), REFUSED), SIANA,
                         TAKEOVER)

        result = self.watch()

        self.assertIn("every poke is held", result.err)
        self.assertIn(f"detected herdr sees SIANA at {self.PANE} again", result.out)
        self.assertEqual(len(self.pokes()), 1)

    def test_a_herdr_that_never_comes_back_ends_the_grant(self):
        # A watcher parked against a herdr that is not returning is the one thing
        # this must never be: a process that reads as a live autonomy grant while it
        # can wake nobody.
        self.herdr.reply("agent.get", SIANA, once(self.reported(), CLOSE), CLOSE)

        result = self.watch(grace=0.2, interval="0.01")

        self.assertIn("without herdr", str(result.refusal))
        self.assertIn("confirming SIANA is in it", str(result.refusal))
        # Not one poke went out while the pane could not be identified.
        self.assertEqual(self.pokes(), [])

    def test_liveness_is_checked_every_tick_and_not_only_when_there_is_news(self):
        # A watcher that only looked when it had something to deliver would sit for
        # hours after SIANA exited, with the captain believing the fleet advanced.
        self.herdr.reply("agent.get", SIANA, SIANA, SIANA, TAKEOVER)

        self.watch()

        self.assertEqual(len(self.herdr.calls_to("agent.get")), 4)


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
            w.Herdr(self.at("herdr.sock"), timeout=0.05).call("agent.get")
        self.assertIn("stopped answering during agent.get", str(cm.exception))

    def test_a_refusal_is_not_an_unreachable(self):
        self.herdr.reply("agent.get", REFUSED)
        with self.assertRaises(w.Refusal) as cm:
            self.client().call("agent.get", target=self.PANE)
        self.assertNotIsInstance(cm.exception, w.Unreachable)
        self.assertIn("no agent in that pane", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
