"""The runner itself, driven as a real run over a suite written for the occasion.

`tests/run.py` is the one file here that every other test depends on being right,
and the two things it must never do are the two a runner is uniquely able to do
quietly: report a suite as green when part of it did not run, and leave processes
behind when it goes. So both are held here by observation rather than by argument -
a worker is really killed, a test really stalls, a child really escapes into a
session of its own, and the assertion is about what is left afterwards.

The suites under test are written into a throwaway directory per test. A fixture
rather than the real suite because a runner test has to be able to fail on purpose,
and because a test of the runner that took ten minutes would be one nobody runs.
Discovery parity is the exception: that one is asserted against the real suite,
because the claim being made is about this suite and no other.
"""

import io
import os
import re
import signal
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

import run
from helpers import DISTRO, HomeTest, until

RUNNER = os.path.join(DISTRO, "tests", "run.py")
TESTS = os.path.join(DISTRO, "tests")

# `   12/34 w3 test_x.Case.test_y`, or the same without the worker in serial mode.
ANNOUNCED = re.compile(r"^\s*(\d+)/(\d+) (?:(w\d+|--) )?(\S+)$")

MIXED = """
    import unittest

    class Passes(unittest.TestCase):
        def test_one(self): pass
        def test_two(self): pass

    class Mixed(unittest.TestCase):
        def test_errors(self): raise RuntimeError("boom")
        def test_fails(self): self.assertEqual(1, 2)
        def test_passes(self): pass

        @unittest.skip("not today")
        def test_skips(self): pass
"""


class Fixture(HomeTest):
    """A throwaway suite, and real runs of `tests/run.py` over it."""

    def suite(self, **modules):
        """A directory of test modules, and a fresh one every call.

        Fresh because a test that builds a second fixture wants a second suite, not
        the first one with more in it - and discovery would happily find both and
        run a green suite as a red one.
        """
        where = self.at(f"suite{len(os.listdir(self.home))}")
        os.makedirs(where)
        for name, body in modules.items():
            with open(os.path.join(where, f"{name}.py"), "w") as fh:
                fh.write(textwrap.dedent(body).lstrip())
        return where

    def command(self, where, args, workers):
        return [sys.executable, "-B", "-u", RUNNER, "-s", where, *args], \
            self.command_env({"SIANA_TEST_WORKERS": str(workers)})

    def go(self, where, *args, workers=3, env=None, timeout=180):
        """A finished run, with both streams together in `.stdout`.

        Together because a run reports on stderr, which is where
        `unittest.TextTestRunner` writes, and a test that read the wrong one of the
        two would pass against a runner that had stopped reporting at all.
        """
        argv, e = self.command(where, args, workers)
        e.update(env or {})
        return subprocess.run(argv, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=timeout,
                              env=e, cwd=DISTRO)

    def spawn(self, where, *args, workers=2, env=None):
        """A run left in flight, for the tests that signal one."""
        argv, e = self.command(where, args, workers)
        e.update(env or {})
        out = open(self.at("out"), "w")
        proc = subprocess.Popen(argv, stdout=out, stderr=subprocess.STDOUT,
                                text=True, env=e, cwd=DISTRO)
        self.addCleanup(self.stop, proc)
        return proc

    def stop(self, proc):
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)

    def flight(self):
        with open(self.at("out")) as fh:
            return fh.read()

    def announced(self, text):
        """The test ids a run said it was starting, in the order it said them."""
        return [m.group(4) for m in
                (ANNOUNCED.match(line) for line in text.splitlines()) if m]

    def summary(self, text):
        """The report, from the first block or rule to the end.

        Everything above it is progress, which is scheduling order and therefore
        different every parallel run by design. Everything from here down is the
        verdict, and that has to be the same text every time.
        """
        for mark in ("=" * 70, "-" * 70):
            if mark in text:
                return text[text.index(mark):]
        return text

    def sleepers(self, marker):
        out = subprocess.run(["pgrep", "-f", f"sleep {marker}"],
                             capture_output=True, text=True)
        return sorted(int(p) for p in out.stdout.split())

    def marker(self):
        """A sleep duration nothing else on this machine is using.

        The processes these tests leave in flight have to be findable by name, and
        a bare `sleep 400` would also find another minion's. Registered for cleanup
        first, so a test that fails its assertion still takes its children with it.
        """
        marker = str(40000 + os.getpid() % 20000)
        self.addCleanup(self.kill_sleepers, marker)
        return marker

    def kill_sleepers(self, marker):
        for pid in self.sleepers(marker):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    def coordinated(self, where, ids, pool=2, stream=None):
        """`run.coordinate` driven in this process, for the cases argv cannot reach.

        Two of them exist: a test id no discovery would ever produce, and a report
        stream that fails while the workers are still alive. Both need the
        coordinator called directly, which means this process lends it the two
        things it takes from its own: `sys.path`, so a worker can import the
        fixture, and the signal handlers, which are put back afterwards because
        this process is a worker of the outer run rather than a runner of its own.
        """
        for sig in (signal.SIGINT, signal.SIGTERM):
            self.addCleanup(signal.signal, sig, signal.getsignal(sig))
        broken = run.Plan.broken
        self.addCleanup(setattr, run.Plan, "broken", broken)
        run.Plan.broken = []
        sys.path.insert(0, where)
        self.addCleanup(sys.path.remove, where)
        out = stream if stream is not None else io.StringIO()
        run.coordinate(ids, pool, 1, False, out)
        return out.getvalue()


class Discovery(unittest.TestCase):
    """The claim that the two modes cannot select different tests, about this suite.

    Made against the real suite rather than a fixture, because "the accelerated run
    and the serial run agree" is worth nothing if they only agree about six
    invented tests.
    """

    def test_the_plan_is_exactly_what_unittest_discovers(self):
        direct = sorted(t.id() for t in
                        run.iterate(unittest.TestLoader().discover(TESTS)))
        self.assertEqual(sorted(run.discover([])), direct)
        self.assertTrue(len(direct) > 100, "this suite should be much larger")

    def test_every_discovered_test_is_dispatched_exactly_once(self):
        ids = run.discover([])
        order, solo = run.chunks(ids)
        handed = [tid for chunk in order + solo for tid in chunk]
        # A module that would not import is reported by the coordinator rather than
        # dispatched, so it is the one thing `chunks` leaves out; there are none in
        # a healthy suite, and this says so rather than assuming it.
        self.assertEqual(sorted(handed),
                         sorted(t for t in ids if not t.startswith(run.BROKEN)))
        self.assertEqual(len(handed), len(set(handed)))
        self.assertEqual(len(handed), len(ids))

    def test_the_chunks_are_whole_test_classes(self):
        # The unit of work, asserted rather than assumed: a chunk that split a class
        # would still be correct, and a chunk that spanned two would be the thing
        # this suite has never had a fixture for.
        for chunk in run.chunks(run.discover([]))[0]:
            self.assertEqual(len({tid.rsplit(".", 1)[0] for tid in chunk}), 1)


class Solo(unittest.TestCase):
    """The one lane that runs with the machine to itself.

    `run.SOLO` names the tests whose subject is the operating system's event
    latency rather than this project's code, and running them beside eight hundred
    others made one of them fail three times in roughly twenty runs. The rule they
    need is that nothing else of this runner's is in flight while they run.
    """

    def test_the_solo_names_match_tests_that_exist(self):
        # A rename would otherwise turn the lane into a no-op silently, and the
        # flake would come back with nothing to point at.
        ids = run.discover([])
        for name in run.SOLO:
            self.assertTrue([t for t in ids if t.startswith(name)],
                            f"{name} matches no test in this suite")

    def test_solo_tests_are_kept_out_of_the_ordinary_chunks(self):
        ids = run.discover([])
        order, solo = run.chunks(ids)
        self.assertTrue(solo, "nothing is in the solo lane")
        for chunk in order:
            for tid in chunk:
                self.assertFalse(tid.startswith(run.SOLO))
        self.assertEqual(sorted(t for chunk in solo for t in chunk),
                         sorted(t for t in ids if t.startswith(run.SOLO)))

    def test_the_shuffle_never_moves_solo_work_into_the_pool(self):
        # The stress seed reorders the ordinary chunks and must not be able to
        # undo the one rule that is not about ordering.
        with mock.patch.dict(os.environ, {"SIANA_TEST_SHUFFLE": "seed"}):
            order, solo = run.chunks(run.discover([]))
        self.assertTrue(solo)
        for chunk in order:
            for tid in chunk:
                self.assertFalse(tid.startswith(run.SOLO))


class SoloDispatch(Fixture):
    """The rule driven for real: nothing else runs while solo work does."""

    def test_nothing_else_is_in_flight_while_a_solo_test_runs(self):
        out_dir = self.at("windows")
        os.makedirs(out_dir)
        where = self.suite(test_solo=f"""
            import os, time, unittest

            OUT = {out_dir!r}

            def record(name):
                start = time.monotonic()
                time.sleep(0.5)
                with open(os.path.join(OUT, name), "w") as fh:
                    fh.write(f"{{start}} {{time.monotonic()}}")

            class Quiet(unittest.TestCase):
                def test_needs_the_machine(self): record("solo")

            class Busy(unittest.TestCase):
                def test_one(self): record("busy-one")

            class AlsoBusy(unittest.TestCase):
                def test_two(self): record("busy-two")

            class StillBusy(unittest.TestCase):
                def test_three(self): record("busy-three")
        """)
        ids = ["test_solo.Quiet.test_needs_the_machine", "test_solo.Busy.test_one",
               "test_solo.AlsoBusy.test_two", "test_solo.StillBusy.test_three"]
        with mock.patch.object(run, "SOLO", ("test_solo.Quiet.",)):
            out = self.coordinated(where, ids, pool=3)
        self.assertIn("Ran 4 tests", out)
        self.assertIn("OK", out)

        def window(name):
            with open(os.path.join(out_dir, name)) as fh:
                return tuple(float(v) for v in fh.read().split())

        solo_start, solo_end = window("solo")
        for name in ("busy-one", "busy-two", "busy-three"):
            start, end = window(name)
            self.assertTrue(end <= solo_start or start >= solo_end,
                            f"{name} overlapped the solo test")
        # And the point of the pool is still intact: the three ordinary tests did
        # run beside each other, so this is a rule about the solo test and not a
        # serial run wearing a pool's name.
        busy = [window(n) for n in ("busy-one", "busy-two", "busy-three")]
        self.assertTrue(any(a[0] < b[1] and b[0] < a[1]
                            for a in busy for b in busy if a is not b),
                        "the ordinary tests did not overlap either")

    def test_the_solo_test_still_runs_when_it_is_the_only_work(self):
        where = self.suite(test_solo="""
            import unittest

            class Quiet(unittest.TestCase):
                def test_alone(self): pass
        """)
        with mock.patch.object(run, "SOLO", ("test_solo.Quiet.",)):
            out = self.coordinated(where, ["test_solo.Quiet.test_alone"], pool=3)
        self.assertIn("Ran 1 test", out)
        self.assertIn("OK", out)


class Equivalence(Fixture):
    """One worker and several have to be the same run with a different clock."""

    def setUp(self):
        super().setUp()
        self.where = self.suite(test_mixed=MIXED)

    def test_both_modes_run_the_same_tests_exactly_once(self):
        serial = self.announced(self.go(self.where, workers=1).stdout)
        parallel = self.announced(self.go(self.where, workers=3).stdout)
        self.assertEqual(sorted(serial), sorted(parallel))
        self.assertEqual(len(parallel), len(set(parallel)))
        self.assertEqual(len(parallel), 6)

    def test_both_modes_reach_the_same_verdict(self):
        serial = self.go(self.where, workers=1)
        parallel = self.go(self.where, workers=3)
        self.assertEqual(serial.returncode, parallel.returncode)
        self.assertNotEqual(parallel.returncode, 0)
        for out in (serial, parallel):
            self.assertIn("Ran 6 tests", out.stdout)
            self.assertIn("FAILED (failures=1, errors=1, skipped=1)", out.stdout)

    def test_a_green_suite_is_green_in_both_modes(self):
        where = self.suite(test_green="""
            import unittest

            class Green(unittest.TestCase):
                def test_one(self): pass
                def test_two(self): pass
        """)
        for workers in (1, 3):
            out = self.go(where, workers=workers)
            self.assertEqual(out.returncode, 0, out.stdout)
            self.assertIn("Ran 2 tests", out.stdout)
            self.assertIn("OK", out.stdout)


class Filters(Fixture):

    def setUp(self):
        super().setUp()
        self.where = self.suite(test_mixed=MIXED)

    def test_k_selects_the_same_tests_in_both_modes(self):
        serial = self.announced(self.go(self.where, "-k", "passes", workers=1).stdout)
        parallel = self.announced(self.go(self.where, "-k", "passes", workers=4).stdout)
        self.assertEqual(sorted(serial), sorted(parallel))
        # unittest matches a dotless `-k` against the method name alone, so the
        # class called `Passes` is not a match and `test_passes` is. Asserted as
        # unittest behaves rather than as it reads, because the claim being made
        # here is that the pool did not change it.
        self.assertEqual(sorted(parallel), ["test_mixed.Mixed.test_passes"])

    def test_k_that_matches_nothing_runs_nothing_and_says_so(self):
        out = self.go(self.where, "-k", "nothing-matches-this", workers=3)
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertIn("Ran 0 tests", out.stdout)

    def test_verbose_reports_the_outcome_of_every_test(self):
        out = self.go(self.where, "-v", workers=3)
        self.assertEqual(out.stdout.count("          ok"), 3)
        self.assertIn("          skipped: not today", out.stdout)
        self.assertIn("          FAIL", out.stdout)
        self.assertIn("          ERROR", out.stdout)

    def test_a_skip_is_counted_without_being_verbose(self):
        out = self.go(self.where, workers=3)
        self.assertIn("skipped=1", out.stdout)
        self.assertNotIn("not today", out.stdout)


class Aggregation(Fixture):
    """The report has to be the same text however the pool happened to schedule it."""

    def setUp(self):
        super().setUp()
        self.where = self.suite(test_mixed=MIXED, test_more="""
            import unittest

            class Later(unittest.TestCase):
                def test_also_fails(self): self.fail("second")
                def test_fine(self): pass
        """)

    def test_two_runs_of_the_same_suite_report_identically(self):
        first = self.summary(self.go(self.where, workers=4).stdout)
        second = self.summary(self.go(self.where, workers=2).stdout)
        self.assertEqual(re.sub(r"in [\d.]+s", "in Xs", first),
                         re.sub(r"in [\d.]+s", "in Xs", second))

    def test_a_shuffled_schedule_reports_the_same_run(self):
        # The stress that proves the suite has no order dependence in it. A seed
        # changes which worker gets what and in what order, and nothing else.
        plain = self.summary(self.go(self.where, workers=3).stdout)
        for seed in ("1", "2", "3"):
            shuffled = self.summary(
                self.go(self.where, workers=3, env={"SIANA_TEST_SHUFFLE": seed}).stdout)
            self.assertEqual(re.sub(r"in [\d.]+s", "in Xs", plain),
                             re.sub(r"in [\d.]+s", "in Xs", shuffled))

    def test_the_failures_are_listed_in_discovery_order(self):
        out = self.go(self.where, workers=4).stdout
        listed = re.findall(r"^(?:FAIL|ERROR): (\S+)$", out, re.M)
        self.assertEqual(listed, ["test_mixed.Mixed.test_errors",
                                  "test_mixed.Mixed.test_fails",
                                  "test_more.Later.test_also_fails"])


class Failfast(Fixture):

    def test_it_stops_early_without_inventing_tests_that_never_ran(self):
        where = self.suite(test_ff="""
            import unittest

            class A(unittest.TestCase):
                def test_fails(self): self.fail("stop here")

            class B(unittest.TestCase):
                def test_b1(self): pass
                def test_b2(self): pass
                def test_b3(self): pass
        """)
        out = self.go(where, "-f", workers=1)
        self.assertNotEqual(out.returncode, 0)
        parallel = self.go(where, "-f", workers=2)
        self.assertNotEqual(parallel.returncode, 0)
        # The tests failfast skipped are absent, exactly as unittest leaves them.
        # Naming them as errors would be this runner inventing a worse report than
        # the one it is standing in for.
        self.assertNotIn("never ran", parallel.stdout)
        self.assertIn("FAILED (failures=1)", parallel.stdout)


class WorkerDeath(Fixture):
    """A worker that disappears is a named failure, never a shorter green run."""

    def test_a_worker_that_exits_mid_test_names_the_test_it_was_running(self):
        where = self.suite(test_die="""
            import os, unittest

            class Dies(unittest.TestCase):
                def test_a_dies(self): os._exit(7)
                def test_b_never_reached(self): pass
                def test_c_never_reached(self): pass

            class Fine(unittest.TestCase):
                def test_ok(self): pass
        """)
        out = self.go(where, workers=2)
        self.assertNotEqual(out.returncode, 0, out.stdout)
        self.assertIn("ERROR: test_die.Dies.test_a_dies", out.stdout)
        self.assertIn("died while running this test (exit 7)", out.stdout)

    def test_the_tests_that_worker_never_reached_are_named_too(self):
        where = self.suite(test_die="""
            import os, unittest

            class Dies(unittest.TestCase):
                def test_a_dies(self): os._exit(7)
                def test_b_never_reached(self): pass
                def test_c_never_reached(self): pass
        """)
        out = self.go(where, workers=2)
        self.assertIn("ERROR: test_die.Dies.test_b_never_reached", out.stdout)
        self.assertIn("ERROR: test_die.Dies.test_c_never_reached", out.stdout)
        self.assertIn("never ran", out.stdout)
        # Every discovered test still accounted for. A run that quietly dropped the
        # two it could not reach would say "Ran 1 test" and pass.
        self.assertIn("Ran 3 tests", out.stdout)

    def test_a_worker_killed_from_outside_is_still_a_failure(self):
        where = self.suite(test_killed="""
            import os, signal, unittest

            class Killed(unittest.TestCase):
                def test_is_killed(self): os.kill(os.getpid(), signal.SIGKILL)
        """)
        out = self.go(where, workers=2)
        self.assertNotEqual(out.returncode, 0, out.stdout)
        self.assertIn("ERROR: test_killed.Killed.test_is_killed", out.stdout)
        self.assertIn("Ran 1 test", out.stdout)

    def test_a_worker_that_cannot_load_what_it_was_sent_says_so(self):
        # Unreachable through discovery, which is the point: if the coordinator and
        # a worker ever disagree about what a test id names, the run has to go red
        # with that id in it rather than quietly run one test fewer. Driven straight
        # at `coordinate` because there is no way to ask discovery for a test that
        # is not there.
        where = self.suite(test_gone="""
            import unittest

            class Gone(unittest.TestCase):
                def test_here(self): pass
        """)
        out = self.coordinated(where, ["test_gone.Gone.test_here",
                                       "test_gone.Gone.test_not_here"])
        self.assertIn("ERROR: test_gone.Gone.test_not_here", out)
        self.assertIn("could not be loaded", out)
        self.assertIn("Ran 2 tests", out)
        self.assertIn("FAILED (errors=1)", out)


class Stalls(Fixture):
    """A stall has to name itself before CI's guard, not instead of it."""

    def test_a_stalled_test_names_itself_and_dumps_its_stack(self):
        where = self.suite(test_stall="""
            import time, unittest

            class Stalls(unittest.TestCase):
                def test_the_stall(self): time.sleep(300)

            class Fine(unittest.TestCase):
                def test_ok(self): pass
        """)
        out = self.go(where, workers=2, timeout=180,
                      env={"SIANA_TEST_STALL_S": "3"})
        self.assertNotEqual(out.returncode, 0, out.stdout)
        self.assertIn("ERROR: test_stall.Stalls.test_the_stall", out.stdout)
        self.assertIn(f"spent more than {3 + run.GRACE_S}s", out.stdout)
        # The stack is the whole point. Without it a stalled run says only that it
        # was killed, which is what three CI runs on this project already said.
        self.assertIn("Timeout (0:00:03)!", out.stdout)
        self.assertIn("test_the_stall", out.stdout)
        # The test that was fine is still reported. A stall must end the run, not
        # erase what the rest of the pool had already established.
        self.assertIn("Ran 2 tests", out.stdout)

    def test_the_default_watchdog_is_the_one_the_hang_guard_was_sized_against(self):
        # Nothing in this project may shorten this to make a run look faster, and
        # nothing may lengthen it past CI's fifteen-minute guard either.
        clean = {k: v for k, v in os.environ.items() if k != "SIANA_TEST_STALL_S"}
        out = subprocess.run(
            [sys.executable, "-B", "-c", "import run; print(run.STALL_S)"],
            cwd=TESTS, text=True, capture_output=True, env=clean)
        self.assertEqual(out.stdout.strip(), "480", out.stdout)


class Ownership(Fixture):
    """Every path out of a run takes this runner's children with it.

    The failure being held off is a measured one. Eight CPU spin loops started by a
    task on this fleet outlived it by five hours and forty-nine minutes, because
    the shell that started them had no job control and `jobs -p` returned nothing
    to `kill`. They burned 8 of 11 cores and corrupted every suite timing taken in
    that window. Nothing in `tests/run.py` asks a shell anything; these are the
    tests that say so.
    """

    def holding(self, marker):
        """A suite whose one test leaves two children and then waits forever.

        Two, because they are lost in different ways. The plain one goes when its
        worker's group is signalled. The one in a session of its own does not, and
        it is the shape `tests/test_siana.py` really uses - so a runner that only
        signalled process groups would leave it running exactly as that task did.
        """
        return self.suite(test_hold=f"""
            import subprocess, time, unittest

            class Hold(unittest.TestCase):
                def test_holds_two_children(self):
                    subprocess.Popen(["/bin/sleep", "{marker}"],
                                     start_new_session=True)
                    subprocess.Popen(["/bin/sleep", "{marker}"])
                    time.sleep(300)
        """)

    def held(self, marker, workers=2):
        proc = self.spawn(self.holding(marker), workers=workers)
        self.assertTrue(until(lambda: len(self.sleepers(marker)) == 2, timeout=60),
                        f"the fixture never started its children: {self.flight()}")
        return proc

    def assertReaped(self, marker, proc, expected):
        self.assertEqual(proc.wait(timeout=90), expected, self.flight())
        self.assertTrue(until(lambda: not self.sleepers(marker), timeout=30),
                        f"left running: {self.sleepers(marker)}")
        self.assertFalse(os.path.exists(run.run_root(proc.pid)),
                         "the run left its temporary root behind")

    def test_sigint_takes_both_children_and_the_temporary_root(self):
        marker = self.marker()
        proc = self.held(marker)
        proc.send_signal(signal.SIGINT)
        self.assertReaped(marker, proc, 130)

    def test_sigterm_takes_both_children_and_the_temporary_root(self):
        marker = self.marker()
        proc = self.held(marker)
        proc.terminate()
        self.assertReaped(marker, proc, 143)

    def test_a_stall_takes_both_children_with_it(self):
        marker = self.marker()
        proc = self.spawn(self.holding(marker), workers=2,
                          env={"SIANA_TEST_STALL_S": "3"})
        self.assertReaped(marker, proc, 1)

    def test_a_finished_run_leaves_nothing_behind(self):
        marker = self.marker()
        where = self.suite(test_spawns=f"""
            import subprocess, unittest

            class Spawns(unittest.TestCase):
                def test_a_child_outliving_the_test_still_goes(self):
                    subprocess.Popen(["/bin/sleep", "{marker}"],
                                     start_new_session=True)
        """)
        proc = self.spawn(where, workers=2)
        self.assertEqual(proc.wait(timeout=90), 0, self.flight())
        self.assertTrue(until(lambda: not self.sleepers(marker), timeout=30),
                        f"left running: {self.sleepers(marker)}")
        self.assertFalse(os.path.exists(run.run_root(proc.pid)))

    def test_a_coordinator_that_raises_still_reaps(self):
        """The path nobody plans for: the coordinator itself failing mid-run.

        Driven in this process rather than as a subprocess because the failure has
        to be injected somewhere real, and the report stream is the one thing the
        coordinator touches while its workers are alive. What is asserted is that
        the temporary root is gone, which only `reap` removes.
        """
        class Explodes(io.StringIO):
            """A stream that fails on the first progress line, and not before it.

            The banner would be too early: the workers do not exist yet, so a run
            that cleaned up perfectly and one that did not would look the same.
            """

            def write(self, text):
                if ANNOUNCED.match(text.rstrip("\n")):
                    raise RuntimeError("the report stream failed")
                return super().write(text)

        where = self.suite(test_slow="""
            import time, unittest

            class Slow(unittest.TestCase):
                def test_one(self): time.sleep(120)
                def test_two(self): time.sleep(120)
        """)
        with self.assertRaises(RuntimeError):
            self.coordinated(where, ["test_slow.Slow.test_one",
                                     "test_slow.Slow.test_two"],
                             stream=Explodes())
        # The root is removed by `reap` and by nothing else, so its absence is the
        # proof that the `finally` ran even though the coordinator was raising - and
        # `reap` is what would have been skipped had the workers been left running.
        self.assertFalse(os.path.exists(run.run_root(os.getpid())))


class Descendants(unittest.TestCase):
    """The walk that finds what a process group misses, on its own."""

    def spawn_tree(self):
        """A parent, a child in its group, and a grandchild in a session of its own."""
        parent = subprocess.Popen([sys.executable, "-c", textwrap.dedent("""
            import subprocess, sys, time
            subprocess.Popen(["/bin/sleep", "300"])
            subprocess.Popen(["/bin/sleep", "300"], start_new_session=True)
            sys.stdout.write("ready\\n"); sys.stdout.flush()
            time.sleep(300)
        """)], stdout=subprocess.PIPE, text=True, start_new_session=True)
        self.addCleanup(self.reap_tree, parent)
        self.assertEqual(parent.stdout.readline().strip(), "ready")
        return parent

    def reap_tree(self, parent):
        for pid in run.descendants([parent.pid])[0]:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        parent.kill()
        parent.wait(timeout=30)

    def test_it_finds_a_child_that_left_the_process_group(self):
        parent = self.spawn_tree()
        pids, groups = run.descendants([parent.pid])
        self.assertIn(parent.pid, pids)
        # Three: the parent and both children. The one that made a session of its
        # own is outside the parent's group and would survive a bare `killpg`.
        self.assertEqual(len(pids), 3, pids)
        self.assertGreaterEqual(len(groups), 2, groups)

    def test_it_reaches_nothing_it_was_not_rooted_at(self):
        parent = self.spawn_tree()
        pids, _ = run.descendants([parent.pid])
        self.assertNotIn(os.getpid(), pids)
        self.assertNotIn(1, pids)

    def test_no_roots_finds_nothing(self):
        # The empty case, because `reap` reaches it on every run whose workers have
        # all exited normally, and a walk that invented a root there would signal
        # something. Not a pid that has merely exited: that one can be reused, and a
        # test asserting nothing descends from it would fail on whatever got it.
        self.assertEqual(run.descendants([]), (set(), set()))


class Pool(Fixture):
    """How many workers, and how a caller says so."""

    def test_one_worker_is_unittest_in_this_process(self):
        out = self.go(self.suite(test_mixed=MIXED), workers=1)
        # No worker tags and no pool banner: the control is meant to be the runner
        # standing aside, so there has to be nothing of the pool in its output.
        self.assertNotIn(" workers ", out.stdout)
        self.assertNotRegex(out.stdout, r"^\s*\d+/\d+ w\d+ ")

    def test_the_pool_says_how_big_it_is_and_how_to_change_it(self):
        out = self.go(self.suite(test_mixed=MIXED), workers=3)
        self.assertIn("6 tests, 3 workers", out.stdout)
        self.assertIn("SIANA_TEST_WORKERS=1", out.stdout)

    def test_a_worker_count_that_is_not_a_number_is_refused(self):
        # Not the empty string: unset and blank both mean "the default", which is
        # what a `just test` that never heard of this variable gets.
        for bad in ("0", "-2", "many", "3.5"):
            out = self.go(self.suite(test_mixed=MIXED), workers=bad)
            self.assertNotEqual(out.returncode, 0, f"{bad!r} was accepted")
            self.assertIn("SIANA_TEST_WORKERS", out.stdout)

    def test_the_default_is_conservative_rather_than_the_core_count(self):
        # The pool is sized to leave the machine usable rather than to fill it: the
        # captain's runs the whole fleet, and a suite that took most of it would
        # slow down every other minion on the box.
        cores = os.cpu_count() or 2
        default = run.default_workers()
        self.assertGreaterEqual(default, 2, "a pool of one is not the serial path")
        self.assertLessEqual(default, run.POOL_CAP)
        if cores > 4:
            self.assertLess(default, cores)

    def test_a_bigger_machine_never_means_an_unbounded_pool(self):
        # Asserted across machines this may never run on, because the cap is the
        # only thing standing between a 96-core runner and 96 concurrent suites.
        for cores in (1, 2, 4, 8, 11, 16, 64, 256):
            with mock.patch.object(os, "cpu_count", return_value=cores):
                self.assertIn(run.default_workers(), range(2, run.POOL_CAP + 1))


class Isolation(Fixture):
    """Two workers must not be able to reach each other's temporary state."""

    def test_a_workers_temporary_directory_is_its_own(self):
        where = self.suite(test_tmp="""
            import os, tempfile, unittest

            OUT = os.environ["FIXTURE_OUT"]

            class First(unittest.TestCase):
                def test_records_its_temp_directory(self):
                    with open(os.path.join(OUT, "first"), "w") as fh:
                        fh.write(tempfile.gettempdir())

            class Second(unittest.TestCase):
                def test_records_its_temp_directory(self):
                    with open(os.path.join(OUT, "second"), "w") as fh:
                        fh.write(tempfile.gettempdir())
        """)
        out_dir = self.at("recorded")
        os.makedirs(out_dir)
        result = self.go(where, workers=2, env={"FIXTURE_OUT": out_dir})
        self.assertEqual(result.returncode, 0, result.stdout)
        recorded = {}
        for name in ("first", "second"):
            with open(os.path.join(out_dir, name)) as fh:
                recorded[name] = fh.read()
        self.assertNotEqual(recorded["first"], recorded["second"])
        for path in recorded.values():
            self.assertIn("siana-run-", path)

    def test_a_socket_fits_under_a_workers_temporary_directory(self):
        # The whole reason a worker gets a short `$TMPDIR`: every herdr-facing test
        # in this suite binds an `AF_UNIX` socket, and one that had to fall back to
        # the shared `/tmp` would be the one thing a killed run left behind.
        where = self.suite(test_socket="""
            import os, sys, unittest

            sys.path.insert(0, os.environ["SUITE_HELPERS"])
            from fake_herdr import FakeHerdr, socket_dir

            class Socket(unittest.TestCase):
                def test_the_socket_lands_under_this_workers_tmpdir(self):
                    self.assertTrue(socket_dir().startswith(os.environ["TMPDIR"]))
                    herdr = FakeHerdr().start()
                    self.addCleanup(herdr.stop)
                    self.assertTrue(os.path.exists(herdr.path))
        """)
        out = self.go(where, workers=2, env={"SUITE_HELPERS": TESTS})
        self.assertEqual(out.returncode, 0, out.stdout)


class BrokenModule(Fixture):
    """A module that will not import is a reported error, not a smaller run."""

    def test_an_unimportable_module_fails_the_run_and_names_itself(self):
        where = self.suite(test_ok="""
            import unittest

            class Fine(unittest.TestCase):
                def test_one(self): pass
        """, test_broken="""
            import this_module_does_not_exist  # noqa: F401
        """)
        out = self.go(where, workers=2)
        self.assertNotEqual(out.returncode, 0, out.stdout)
        self.assertIn("test_broken", out.stdout)
        self.assertIn("ModuleNotFoundError", out.stdout)
        # The importable module still ran. A broken sibling must not take the rest
        # of the suite with it.
        self.assertIn("test_ok.Fine.test_one", out.stdout)
        self.assertIn("Ran 2 tests", out.stdout)


class Litter(Fixture):
    """The suite writes nothing into the tree it runs in."""

    def test_a_parallel_run_writes_no_bytecode_into_the_suite(self):
        # `-B` covers the coordinator, and a worker is started with it too. Without
        # that, every worker compiles every module it loads into a `__pycache__`
        # inside the worktree, and `siana-retire` then refuses to remove the tree.
        where = self.suite(test_ok="""
            import unittest

            class Fine(unittest.TestCase):
                def test_one(self): pass
        """)
        self.assertEqual(self.go(where, workers=2).returncode, 0)
        self.assertFalse(os.path.exists(os.path.join(where, "__pycache__")))

    def test_no_run_root_is_left_under_the_temporary_directory(self):
        root = "/tmp" if os.path.isdir("/tmp") else tempfile.gettempdir()
        where = self.suite(test_ok="""
            import unittest

            class Fine(unittest.TestCase):
                def test_one(self): pass
        """)
        proc = self.spawn(where, workers=2)
        self.assertEqual(proc.wait(timeout=90), 0, self.flight())
        self.assertNotIn(f"siana-run-{proc.pid}", os.listdir(root))


if __name__ == "__main__":
    unittest.main()
