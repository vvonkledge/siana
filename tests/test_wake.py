"""`template/wake.ts`: the half of the wake path that runs inside SIANA's session.

This is where the bug was fixed, so this is where it has to stay fixed. The watcher
used to write the wake into the same input editor the captain types in, and pi
submitted the concatenation as one user message: a half-written instruction not to
ship, fused to a machine wake and sent under the captain's name, unrevisable because
it had already gone. The rule that replaces it is not "check first" - every check
before a write is a window - it is that the send and the editor read happen in one
synchronous block on pi's event loop, where no keystroke can land between them.

The second rule is that a wake only ever goes into an idle session. Pi hands a
message to a turn in flight by queueing it, and it empties that queue back into the
editor when the captain interrupts with Escape, so a queued wake is the same bug one
keystroke later. `tests/fake_pi.mjs` models both the queue and the restore, which is
why `Busy` below can drive the whole reproduction rather than assert on an argument.

So these drive the extension rather than read it. It runs on the real node event
loop, against a real filesystem, with its own directory watch and its own interval;
what is scripted is pi's side of the six calls it makes, in `tests/fake_pi.mjs`. No
model is loaded, no credentials are read, and no pane is touched: the last test here
proves that rather than asserting it, by running a delivery with `pi`, `claude` and
`herdr` replaced by stubs that would leave a mark if anything reached for them.

The extension is TypeScript because pi only auto-discovers `.ts`, and node runs it by
stripping the types. A node too old for that skips the file rather than passing, so a
green run here is one that really executed it.
"""

import json
import os
import shutil
import subprocess
import unittest

from helpers import DISTRO, TEMPLATE, HomeTest, script

w = script("siana-watch")

HARNESS = os.path.join(DISTRO, "tests", "fake_pi.mjs")
EXTENSION = os.path.join(TEMPLATE, "wake.ts")

# The one sentence a wake carries, and the whole of what it knows. Read out of the
# watcher's own docstring's sibling rather than repeated by hand would be better; it
# is written twice in the distro because the two sides ship separately, and this is
# the test that would notice them drifting apart.
WAKE = "The queue moved. Reconcile it."


def node():
    """A node that can run a `.ts` file, or None. Type stripping is on by default
    from node 22.18 and 23.6, and a `.ts` import simply fails on anything older, so
    this asks by trying rather than by parsing a version string."""
    exe = shutil.which("node")
    if not exe:
        return None
    probe = subprocess.run([exe, "-e", f"import({json.dumps(EXTENSION)})"],
                           capture_output=True, text=True, timeout=60)
    return exe if probe.returncode == 0 else None


NODE = node()


def exported(name):
    """A constant read out of the extension itself, so a bound asserted here cannot
    drift from the one the code ships."""
    out = subprocess.run(
        [NODE, "-e", f"import({json.dumps(EXTENSION)}).then(m => "
                     f"console.log(m.{name}))"],
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


class Session:
    """One scripted pi session, driven a command at a time.

    Every reply carries the whole recorded state, so an assertion never has to ask
    for what it wants first. `settle` waits for the extension to have sent something
    and `quiet` waits long enough for it to have decided not to, which is the only
    honest way to assert that a wake was held."""

    def __init__(self, test, home, env=None):
        e = dict(os.environ)
        e["SIANA_HOME"] = home
        e.update(env or {})
        self.proc = subprocess.Popen(
            [NODE, HARNESS, EXTENSION], cwd=home, env=e, text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        test.addCleanup(self.close)
        self.test = test

    def __call__(self, cmd, **fields):
        self.proc.stdin.write(json.dumps({"cmd": cmd, **fields}) + "\n")
        self.proc.stdin.flush()
        line = self.proc.stdout.readline()
        if not line:
            self.test.fail("the scripted pi session stopped:\n"
                           + self.proc.stderr.read())
        state = json.loads(line)
        self.test.assertNotIn("error", state, state.get("error"))
        return state

    def close(self):
        if self.proc.poll() is None:
            self.proc.stdin.close()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                # It should have exited when stdin closed. Something it left armed
                # is holding the event loop open, which is a fault in the extension
                # and not a reason to leave a process behind.
                self.proc.kill()
                self.proc.wait(timeout=10)
        for pipe in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if not pipe.closed:
                pipe.close()


@unittest.skipUnless(NODE, "the wake extension needs a node that runs TypeScript")
class WakeTest(HomeTest):
    """A home with a wake directory, and a scripted pi session started in it."""

    def setUp(self):
        super().setUp()
        self.wake = w.wake_dir(self.home)
        os.makedirs(self.wake, exist_ok=True)

    def session(self, **kw):
        return Session(self, self.home, **kw)

    def raise_wake(self, count=1):
        """A wake raised the way `siana-watch` raises one: staged beside the
        counter and renamed onto it. The rename is the point - a watch on the file
        itself dies after the first one."""
        pending = w.read_pending(self.home)
        return w.raise_wake(self.home, count, pending)

    def consumed(self):
        return w.read_counter(os.path.join(self.wake, w.CONSUMED))

    def consumer(self):
        return w.read_consumer(self.home)


class Delivery(WakeTest):
    """The wake arriving, and arriving as the right kind of message."""

    def test_a_wake_raised_while_pi_was_down_is_taken_as_the_session_starts(self):
        # Restart recovery is a read and never a replay: the count is on disk, so a
        # session coming up finds it there.
        self.raise_wake()
        pi = self.session()
        pi("start")
        state = pi("settle", sent=1)
        self.assertEqual(len(state["sent"]), 1, state)
        self.assertEqual(state["sent"][0]["content"], WAKE)
        self.assertEqual(self.consumed(), 1)

    def test_a_wake_raised_during_the_session_is_taken(self):
        pi = self.session()
        pi("start")
        self.raise_wake()
        state = pi("settle", sent=1)
        self.assertEqual([m["content"] for m in state["sent"]], [WAKE])

    def test_the_wake_arrives_as_a_user_message_and_never_as_a_custom_one(self):
        # Measured, not preferred: only `sendUserMessage` fires `before_agent_start`,
        # which is where the tasks package injects the ambient queue. A custom
        # message would run SIANA's reconcile with the queue missing from it.
        self.raise_wake()
        pi = self.session()
        pi("start")
        state = pi("settle", sent=1)
        self.assertNotIn("wrongApi", state["sent"][0], state["sent"])

    def test_an_idle_session_is_woken_at_once_and_never_queued(self):
        # A turn of its own, which is the only delivery with no shared surface: a
        # queued message is one pi can later empty into the captain's editor.
        self.raise_wake()
        pi = self.session()
        pi("start")
        state = pi("settle", sent=1)
        self.assertIsNone(state["sent"][0]["options"])
        self.assertEqual(state["queued"], [])
        self.assertTrue(state["sent"][0]["idleAtSend"])

    def test_wakes_that_were_raised_together_cost_one_message(self):
        self.raise_wake(count=3)
        pi = self.session()
        pi("start")
        state = pi("settle", sent=1)
        state = pi("quiet")
        self.assertEqual(len(state["sent"]), 1, state["sent"])
        self.assertEqual(self.consumed(), 3, "the high-water mark is what was raised")

    def test_a_wake_is_taken_once_however_long_the_session_runs_after(self):
        self.raise_wake()
        pi = self.session()
        pi("start")
        pi("settle", sent=1)
        state = pi("quiet")
        self.assertEqual(len(state["sent"]), 1, state["sent"])

    def test_each_new_wake_is_taken_and_the_mark_follows_it(self):
        pi = self.session()
        pi("start")
        for n in (1, 2, 3):
            self.raise_wake()
            state = pi("settle", sent=n)
            self.assertEqual(len(state["sent"]), n, state["sent"])
            self.assertEqual(self.consumed(), n)


class Busy(WakeTest):
    """A session mid-turn, which has no collision-free delivery at all.

    Pi's one way to hand a message to a turn in flight is a queued follow-up, and
    its TUI empties that queue back into the input editor when the captain
    interrupts with Escape: the queued text joined ahead of whatever they had
    started typing. So a wake handed to a working session is machine text in the
    captain's editor one keystroke later, which is the bug this whole path exists to
    remove. It waits instead, and nothing is recorded about it until it has gone."""

    DRAFT = "do NOT ship anything to main tonight"

    def test_a_wake_that_lands_mid_turn_is_held_and_never_queued(self):
        pi = self.session()
        pi("start")
        pi("idle", value=False)
        self.raise_wake()
        state = pi("quiet")
        self.assertEqual(state["sent"], [], "a wake was handed to a turn in flight")
        self.assertEqual(state["queued"], [])

    def test_a_wake_held_mid_turn_never_advances_the_high_water_mark(self):
        # The mark is the promise that the wake was delivered, and the watcher stops
        # warning once the two counters agree. Advanced on a queued send, a wake pi
        # later tipped into the editor would be gone with nothing left saying so.
        pi = self.session()
        pi("start")
        pi("idle", value=False)
        self.raise_wake()
        pi("quiet")
        self.assertEqual(self.consumed(), 0)

    def test_the_wake_goes_out_as_a_turn_of_its_own_once_the_session_is_idle(self):
        pi = self.session()
        pi("start")
        pi("idle", value=False)
        self.raise_wake()
        pi("quiet")
        pi("idle", value=True)
        state = pi("settle", sent=1)
        self.assertEqual([m["content"] for m in state["sent"]], [WAKE])
        self.assertIsNone(state["sent"][0]["options"], "the wake was queued")
        self.assertEqual(self.consumed(), 1)

    def test_interrupting_a_turn_restores_no_wake_into_the_editor(self):
        # The reproduction, end to end. Editor empty, turn in flight, wake raised;
        # the captain then types and hits Escape. Under a queued delivery pi sets
        # the editor to the wake joined ahead of their draft, and the mark already
        # says the wake was taken - machine text in the editor and a lost wake in
        # one keystroke.
        pi = self.session()
        pi("start")
        pi("idle", value=False)
        self.raise_wake()
        pi("quiet")
        pi("editor", text=self.DRAFT)
        state = pi("interrupt")
        self.assertEqual(state["editor"], self.DRAFT)
        self.assertEqual(state["editorWrites"], [],
                         "something wrote into the editor the captain is typing in")
        self.assertEqual(self.consumed(), 0, "the held wake was recorded as taken")

    def test_the_wake_survives_the_interruption_and_arrives_after_it(self):
        pi = self.session()
        pi("start")
        pi("idle", value=False)
        self.raise_wake()
        pi("quiet")
        pi("editor", text=self.DRAFT)
        pi("interrupt")
        # The draft the captain kept still holds it, so this is the wake outliving
        # the interruption rather than racing it.
        self.assertEqual(self.consumed(), 0)
        pi("editor", text="")
        state = pi("settle", sent=1)
        self.assertEqual([m["content"] for m in state["sent"]], [WAKE])
        self.assertEqual(self.consumed(), 1)

    def test_wakes_raised_through_one_long_turn_cost_one_message(self):
        pi = self.session()
        pi("start")
        pi("idle", value=False)
        self.raise_wake(count=3)
        pi("quiet")
        pi("idle", value=True)
        pi("settle", sent=1)
        state = pi("quiet")
        self.assertEqual(len(state["sent"]), 1, state["sent"])
        self.assertEqual(self.consumed(), 3, "the high-water mark is what was raised")

    def test_a_wake_held_mid_turn_is_still_there_after_a_restart(self):
        # Nothing was recorded, so nothing was lost. A captain who quits SIANA in
        # the middle of a turn gets the held wake when the next session comes up.
        pi = self.session()
        pi("start")
        pi("idle", value=False)
        self.raise_wake()
        pi("quiet")
        pi("shutdown", reason="quit")
        again = self.session()
        again("start")
        state = again("settle", sent=1)
        self.assertEqual([m["content"] for m in state["sent"]], [WAKE])
        self.assertEqual(self.consumed(), 1)


class Refused(WakeTest):
    """A send the session would not take. The mark must not move on it.

    `consumed` is a promise that the wake was delivered, and the watcher stops
    warning once the two counters agree. Written before the send, a refusal would
    swallow the wake for good and take the only warning about it with it."""

    def test_a_send_the_session_refuses_never_advances_the_mark(self):
        pi = self.session()
        pi("start")
        pi("refuse-sends", value=True)
        self.raise_wake()
        state = pi("quiet")
        self.assertTrue(state["refused"], "the extension never tried to send")
        self.assertEqual(state["sent"], [])
        self.assertEqual(self.consumed(), 0)

    def test_the_wake_goes_out_once_the_session_takes_messages_again(self):
        # A refused send is a fact about that attempt and not about the wake, so it
        # is held and tried again rather than dropped.
        pi = self.session()
        pi("start")
        pi("refuse-sends", value=True)
        self.raise_wake()
        pi("quiet")
        pi("refuse-sends", value=False)
        state = pi("settle", sent=1)
        self.assertEqual([m["content"] for m in state["sent"]], [WAKE])
        self.assertEqual(self.consumed(), 1)


class Unwritable(WakeTest):
    """A disk that will not take the mark, which is the one failure that could turn
    one wake into an unbounded stream of them.

    `sendUserMessage` always triggers a turn, so a wake re-sent every half second is
    a paid turn every half second, and the only signal is a line on a stderr nobody
    is reading five minutes later. What makes delivery once-only has to be the send
    itself, never the record of it."""

    def test_a_mark_that_cannot_be_written_never_sends_the_wake_again(self):
        pi = self.session()
        pi("start")
        pi("refuse-writes", value=True)
        self.raise_wake()
        pi("settle", sent=1)
        state = pi("quiet")
        self.assertEqual(len(state["sent"]), 1,
                         f"the wake was re-sent {len(state['sent'])} times because "
                         "its mark could not be written")

    def test_the_mark_lands_as_soon_as_it_can_be_written(self):
        # The watcher reads that file, and until it lands it reads the wake as
        # untaken and says so - which is the right thing for it to be saying. So
        # the write is retried, and the delivery behind it is not.
        pi = self.session()
        pi("start")
        pi("refuse-writes", value=True)
        self.raise_wake()
        pi("settle", sent=1)
        pi("quiet")
        self.assertEqual(self.consumed(), 0)
        pi("refuse-writes", value=False)
        state = pi("quiet")
        self.assertEqual(self.consumed(), 1)
        self.assertEqual(len(state["sent"]), 1)

    def test_the_retry_runs_on_the_poll_and_is_never_fed_by_its_own_watch(self):
        # The write is staged beside the counter, inside the directory the extension
        # watches, so an unfiltered watch hands the failed retry its own event and
        # the retry stages the file again. On Linux inotify delivers every one of
        # those: the loop measured ~14,200 writes a second, the event loop never
        # reached stdin again, and this suite hung until CI's guard killed the job
        # at fifteen minutes. macOS coalesces the same storm to ~17 a second, which
        # is why it stayed green here while three CI runs died. So the bound is the
        # poll and not the platform, and it is asserted rather than left to whichever
        # one the suite happens to run on next.
        quiet_ms = 1500
        poll_ms = int(exported("POLL_MS"))
        pi = self.session()
        pi("start")
        pi("refuse-writes", value=True)
        self.raise_wake()
        pi("settle", sent=1)
        before = len(pi("state")["writes"])
        state = pi("quiet", ms=quiet_ms)
        retries = len(state["writes"]) - before
        self.assertLessEqual(
            retries, 2 * quiet_ms // poll_ms,
            f"the mark was retried {retries} times in {quiet_ms}ms with a "
            f"{poll_ms}ms poll: the retry is feeding its own watch")


class TheEditor(WakeTest):
    """The captain's draft, which is the whole reason this lives inside pi."""

    DRAFT = "do NOT ship anything to main tonight"

    def test_a_draft_holds_the_wake_and_is_left_byte_for_byte(self):
        # The bug, from the other side. The old path concatenated this draft with
        # the wake and submitted both; this must leave it exactly where it is and
        # send nothing at all.
        pi = self.session()
        pi("start")
        pi("editor", text=self.DRAFT)
        self.raise_wake()
        state = pi("quiet")
        self.assertEqual(state["sent"], [], "a wake went out over the captain's draft")
        self.assertEqual(state["editor"], self.DRAFT)
        self.assertEqual(state["editorWrites"], [],
                         "something wrote into the editor the captain is typing in")

    def test_a_held_wake_never_advances_the_high_water_mark(self):
        # Advancing it would swallow the wake: the extension would believe it had
        # delivered one it never sent, and the watcher would stop warning about it.
        pi = self.session()
        pi("start")
        pi("editor", text=self.DRAFT)
        self.raise_wake()
        pi("quiet")
        self.assertEqual(self.consumed(), 0)

    def test_the_wake_goes_out_as_soon_as_the_editor_empties(self):
        pi = self.session()
        pi("start")
        pi("editor", text=self.DRAFT)
        self.raise_wake()
        pi("quiet")
        pi("editor", text="")
        state = pi("settle", sent=1)
        self.assertEqual([m["content"] for m in state["sent"]], [WAKE])
        self.assertEqual(self.consumed(), 1)

    def test_whitespace_alone_is_an_empty_editor(self):
        pi = self.session()
        pi("start")
        pi("editor", text="   \n  ")
        self.raise_wake()
        state = pi("settle", sent=1)
        self.assertEqual(len(state["sent"]), 1, state["sent"])

    def test_the_editor_is_read_in_the_same_block_the_wake_is_sent_in(self):
        # The whole design. Pi's TUI is one event loop and keystrokes are callbacks
        # on it, so a read and a send with nothing between them cannot be
        # interleaved with one - and a single `await` in there would turn the
        # exclusion back into the window the watcher could never close. The harness
        # queues a microtask when the editor is read, so anything that yielded
        # before sending records this as false.
        self.raise_wake()
        pi = self.session()
        pi("start")
        state = pi("settle", sent=1)
        self.assertTrue(state["sent"][0]["sameTick"],
                        "the extension yielded between reading the editor and "
                        "sending, which is a window a keystroke can land in")
        self.assertEqual(state["sent"][0]["editorAtSend"], "")


class Watching(WakeTest):
    """How the extension notices, and why it notices two ways."""

    def test_the_counter_is_still_seen_after_the_first_atomic_rename(self):
        # The trap, and the one anybody "simplifying" the directory watch back to a
        # file watch will re-break: a watch on the file follows the replaced inode,
        # so it fires once and is dead for every rename after. The interval is
        # taken away here on purpose, so a wake that arrives arrived through the
        # watch and through nothing else.
        pi = self.session()
        pi("break-interval")
        pi("start")
        for n in (1, 2, 3):
            self.raise_wake()
            state = pi("settle", sent=n)
            self.assertEqual(len(state["sent"]), n,
                             f"rename {n} was not observed: {state['sent']}")

    def test_a_wake_still_arrives_where_there_is_no_watch_to_be_had(self):
        # A filesystem or a platform that will not give a watch is not a stop, and
        # a missed event is not a lost wake. The interval is the bound on both.
        pi = self.session()
        pi("break-watch")
        pi("start")
        self.raise_wake()
        state = pi("settle", sent=1)
        self.assertEqual(len(state["sent"]), 1, state["sent"])

    def test_the_backstop_is_bounded_well_inside_the_watchers_own_warning(self):
        # A backstop nobody bounded is a wake that arrives whenever. Half a second
        # against the watcher's two-second poll, and three orders of magnitude
        # inside the cadence it warns on.
        with open(EXTENSION) as fh:
            source = fh.read()
        self.assertLessEqual(int(exported("POLL_MS")), 2000)
        self.assertIn("POLL_MS", source)


class TheQueue(WakeTest):
    """What the extension is not allowed to know.

    Starting `siana-watch` is the captain's autonomy grant, given by starting that
    process and withdrawn by stopping it. An extension that read `tasks.jsonl`
    itself would advance the fleet whenever SIANA was running, so merely opening a
    session would confer the grant and stopping the watcher would not withdraw it."""

    def test_the_extension_never_reads_the_fleet_queue(self):
        self.store("tasks.jsonl", {"id": "t1", "status": "done"})
        self.raise_wake()
        pi = self.session()
        pi("start")
        state = pi("settle", sent=1)
        # The recorder is checked against something it must have seen, so "it read
        # nothing about the queue" cannot pass by having recorded nothing at all.
        self.assertIn(os.path.join(self.wake, w.PENDING), state["reads"])
        queue = [path for path in state["reads"] if "tasks.jsonl" in path]
        self.assertEqual(queue, [], "the extension read the queue")


class ConsumerRecord(WakeTest):
    """The handshake the watcher refuses to start without."""

    def test_a_session_starting_records_itself_where_the_watcher_looks(self):
        pi = self.session()
        state = pi("start")
        rec = self.consumer()
        self.assertEqual(rec["pid"], state["pid"])
        # The command as well as the pid, because pids are reused: without it
        # nothing could tell this session from whatever lands on its pid later.
        self.assertEqual(rec["command"], w.process_command(state["pid"]))
        self.assertEqual(w.still_running(rec), (True, ""))

    def test_the_watcher_accepts_the_record_this_writes(self):
        # The two sides ship in one distro and are read by different programs, so
        # this is the seam worth driving end to end rather than describing twice.
        pi = self.session()
        pi("start")
        self.assertIsNotNone(w.confirm_consumer(self.home, "pi"))

    def test_a_record_left_by_a_session_that_was_killed_is_replaced(self):
        # A pi killed hard leaves its record behind, and the watcher refuses on it.
        # Starting SIANA again is the whole recovery, so it has to be one.
        from helpers import gone_pid
        with open(os.path.join(self.wake, w.CONSUMER), "w") as fh:
            json.dump({"pid": gone_pid(), "command": "/usr/bin/whatever"}, fh)
        pi = self.session()
        state = pi("start")
        self.assertEqual(self.consumer()["pid"], state["pid"])

    def test_quitting_withdraws_the_record(self):
        pi = self.session()
        pi("start")
        pi("shutdown", reason="quit")
        self.assertIsNone(self.consumer())

    def test_a_reload_keeps_the_record_because_a_session_start_follows_it(self):
        # `session_shutdown` fires for reload, new, resume and fork too, and each is
        # followed by a `session_start` in the same process. Withdrawing the record
        # on those would tell the watcher there is no consumer during a gap that
        # closes milliseconds later, and its refusal is a startup refusal.
        pi = self.session()
        pi("start")
        for reason in ("reload", "new", "resume", "fork"):
            pi("shutdown", reason=reason)
            self.assertIsNotNone(self.consumer(), f"withdrawn on {reason}")
            pi("start", reason=reason)

    def test_a_reloaded_session_goes_on_taking_wakes(self):
        pi = self.session()
        pi("start")
        self.raise_wake()
        pi("settle", sent=1)
        pi("shutdown", reason="reload")
        pi("start", reason="reload")
        self.raise_wake()
        state = pi("settle", sent=2)
        self.assertEqual(len(state["sent"]), 2, state["sent"])
        self.assertEqual(self.consumed(), 2)


class Unattended(WakeTest):
    """What this test suite itself is not allowed to do.

    The captain's ruling was that routine tests are deterministic and spend nothing.
    A suite that quietly started a real pi would cost money on every run and, worse,
    could reach the live session the captain is sitting in."""

    def test_delivery_spends_no_credentials_and_reaches_for_no_agent(self):
        stubs = self.at("stub")
        os.makedirs(stubs, exist_ok=True)
        marker = self.at("reached-for-it")
        for name in ("pi", "claude", "herdr"):
            path = os.path.join(stubs, name)
            with open(path, "w") as fh:
                fh.write(f"#!/bin/sh\necho {name} >> {marker}\nexit 1\n")
            os.chmod(path, 0o755)
        self.raise_wake()
        # Credentials scrubbed as well as the commands hidden, so a delivery that
        # needed either fails here rather than passing on the captain's machine and
        # failing on a clean runner.
        env = {"PATH": stubs + os.pathsep + os.environ["PATH"],
               "ANTHROPIC_API_KEY": "", "OPENAI_API_KEY": "", "PI_API_KEY": "",
               "HERDR_SOCKET_PATH": self.at("no-such-socket")}
        pi = self.session(env=env)
        pi("start")
        state = pi("settle", sent=1)
        self.assertEqual([m["content"] for m in state["sent"]], [WAKE])
        self.assertFalse(os.path.exists(marker),
                         "the wake path started an agent: "
                         + (open(marker).read() if os.path.exists(marker) else ""))


if __name__ == "__main__":
    unittest.main()
