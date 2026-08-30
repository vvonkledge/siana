"""Run the suite so that a run which stalls says what it was doing, and so that a
suite which spends its whole time waiting does not wait one test at a time.

`python3 -m unittest` reports progress as bare dots, and the first newline it
writes is the one before the summary. Its own flushes do not help: a line-oriented
reader shows nothing until a newline arrives, and the GitHub Actions runner is one.
So a job killed by its hang guard prints exactly nothing, however far it got.

That is not hypothetical. Three CI runs on this project were killed at the
fifteen-minute guard having produced not one byte, so the only thing anybody could
say about the hang was that it existed; finding it took a Linux container and a
process list rather than a log. This file is what makes the next one readable:

- One whole newline-terminated line per test, written before the test runs. The
  last line of a killed run is the test that was running when it was killed.
- A watchdog around each test. A stall dumps every thread's stack and takes the
  process down, which turns "the job ran out of wall clock" into a diagnosable
  failure. `faulthandler` runs its timer on its own thread and writes at the file
  descriptor, so it fires while the main thread is blocked in a syscall - which is
  where a stalled test here actually sits, waiting on a child that will not answer.

And this file is also what makes the run short enough to sit through. Measured on
the captain's machine at 6b76e96, the whole suite is 856 tests in 619s using 465s
of CPU: **0.75 of one core, on a machine with eleven**. It is not computing, it is
waiting - on `just`, on `git`, and above all on `tasks` and `datafile`, which are
`uv run --script` programs costing ~180ms of startup every time one is called. A
serial run of a latency-bound suite leaves ten cores idle for ten minutes.

So tests are handed to a pool of worker processes. The unit of work is one test
class, and that is safe here for a reason worth stating rather than assuming:
**this suite has no `setUpModule`, no `setUpClass`, and no module-level mutable
fixture anywhere**. Every test builds its own throwaway home under `tempfile`, so
two tests sharing a class share no more than two tests in different files do.
Chunking by module instead - the obvious first choice - was measured and rejected:
`test_repair` alone is 195s of the 619s, so a module-sized chunk puts a floor
under the whole run that no number of workers can lift.

One worker is the control, and it is not this machinery with the pool set to one:
it is `unittest` itself, in this process, on the path below. That is the mode to
reach for when a failure looks like it might be the runner's fault, and it is what
`SIANA_TEST_WORKERS=1 just test` gives you.

Discovery, arguments and reporting stay unittest's own: `just test -k slug` and
`just test -v` reach this unchanged, and the parallel path discovers through the
same call the serial path does, so the two cannot select different tests.
"""

import faulthandler
import io
import json
import os
import random
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import traceback
import unittest

TESTS = os.path.dirname(os.path.abspath(__file__))

# The watchdog, and it is a hang guard rather than a budget. Sized against what this
# suite already calls acceptable rather than against what it usually takes: a test
# here may make two `just` calls at `tests/test_justfile.py`'s 180-second timeout
# apiece, so 360 seconds is legitimately slow and only past that is a stall. On a
# cold CI runner those really are slow - `just init` drives `tasks`, a `uv run
# --script` program resolving its dependencies from the network the first time
# anything reaches for them. A watchdog under that turns one slow-but-passing test
# into a red run, and `exit=True` means `_exit`, so it would take every `addCleanup`
# with it and strand the children they remove. Eight minutes leaves seven of CI's
# fifteen-minute guard, so the stack always beats the kill.
#
# Unchanged by the pool. A worker runs one test at a time, so what is being guarded
# is exactly what it was before: one test, on one thread of control.
#
# Overridable only so that this runner's own tests can reach the stall path at all:
# a test that proved the watchdog fires by waiting eight minutes for it would never
# be run by anybody. Nothing in this suite, this justfile or CI sets it, and the
# default is the number above rather than anything a run can shorten to look faster.
STALL_S = int(os.environ.get("SIANA_TEST_STALL_S") or 480)

# How long a worker and its descendants get between SIGTERM and SIGKILL. Long enough
# for a `just` recipe to finish the line it is on and for a test's `addCleanup` to
# remove a temporary home; short enough that an interrupted run gives the terminal
# back. Also the coordinator's own margin over `STALL_S`: past that, a worker whose
# faulthandler should have fired and did not is the coordinator's to kill.
GRACE_S = 10

# The suite's size, for the progress line. Set by the runner below, which is the
# only thing that has counted the tests before they start.
TOTAL = 0


# --------------------------------------------------------------------------------
# The serial path: unittest, in this process, exactly as it has always been.
# --------------------------------------------------------------------------------


class Progress(unittest.TextTestResult):
    """unittest's result, reporting in whole lines instead of in dots."""

    def __init__(self, stream, descriptions, verbosity):
        super().__init__(stream, descriptions, verbosity)
        # The base class's per-test output is the thing being replaced, so it is
        # turned off rather than printed alongside: at verbosity 1 it is the
        # newline-less dots, and at 2 it is a name written before the test and an
        # outcome after, which loses the name for exactly the test that stalls.
        self.dots = False
        self.showAll = False
        self.every = verbosity > 1

    def _line(self, text):
        self.stream.write(text + "\n")
        self.stream.flush()

    def startTest(self, test):
        super().startTest(test)
        self._line(f"{self.testsRun:4d}/{TOTAL} {test.id()}")
        faulthandler.dump_traceback_later(STALL_S, exit=True)

    def stopTest(self, test):
        faulthandler.cancel_dump_traceback_later()
        super().stopTest(test)

    # Failures are marked where they happen as well as listed at the end. The list
    # is the better report, and it is also the one a run killed later never prints.
    def addError(self, test, err):
        super().addError(test, err)
        self._line("          ERROR")

    def addFailure(self, test, err):
        super().addFailure(test, err)
        self._line("          FAIL")

    def addSkip(self, test, reason):
        super().addSkip(test, reason)
        if self.every:
            self._line(f"          skipped: {reason}")

    def addSuccess(self, test):
        super().addSuccess(test)
        if self.every:
            self._line("          ok")


class Runner(unittest.TextTestRunner):
    resultclass = Progress

    def run(self, test):
        global TOTAL
        TOTAL = test.countTestCases()
        return super().run(test)


# --------------------------------------------------------------------------------
# Discovery, done once and shared by both paths.
# --------------------------------------------------------------------------------


class Plan:
    """What unittest selected, and how it was asked to report it.

    Filled by `Collect` below, which is handed to `unittest.main` in the place a
    runner goes. Reusing `unittest.main` rather than calling `TestLoader.discover`
    directly is the whole reason `-k`, `-v`, `-f`, `-b` and `-s` keep working here
    without this file knowing what any of them mean.
    """

    ids = []
    broken = []
    verbosity = 1
    failfast = False
    buffer = False


def iterate(suite):
    """Every test case in a suite, flattened, in the order unittest holds them."""
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from iterate(item)
        else:
            yield item


class Collect(unittest.TextTestRunner):
    """A runner that runs nothing and records what was selected.

    `unittest.main` builds its runner with the options it parsed, so taking them
    off the instance is how the parallel path learns what `-v` and `-f` were
    without parsing an argv of its own.
    """

    def run(self, suite):
        tests = list(iterate(suite))
        Plan.ids = [t.id() for t in tests]
        # A module that will not import is represented by a synthetic test whose id
        # names no importable class, so a worker could never load one. They raise
        # the import error and nothing else, so the coordinator runs them itself.
        Plan.broken = [t for t in tests if t.id().startswith(BROKEN + ".")]
        Plan.verbosity = self.verbosity
        Plan.failfast = self.failfast
        Plan.buffer = self.buffer
        return unittest.TestResult()


BROKEN = "unittest.loader._FailedTest"


def discover(argv):
    """The tests unittest selects for this argv, through unittest's own front door.

    `-s TESTS` first so a bare `just test` finds the suite; anything the caller
    passed comes after and therefore wins, which is what lets the runner's own
    tests point a real run at a throwaway directory of fixtures.
    """
    unittest.main(module=None, exit=False, testRunner=Collect,
                  argv=["unittest", "discover", "-s", TESTS, *argv])
    return Plan.ids


def chunks(ids):
    """The dispatchable units: one test class each, biggest first.

    Biggest first because the pool is handed work dynamically and the last chunk
    out is the one that decides when the run ends - a 53-second class picked up
    last is 53 seconds nobody else can absorb. Ordered by test count rather than by
    duration because a count is known now and a duration would have to be recorded
    from a previous run and trusted; measured against a perfect oracle on this
    suite, counting costs 4% and keeps this file honest about what it knows.
    """
    groups = {}
    for tid in ids:
        if tid.startswith(BROKEN + "."):
            continue
        groups.setdefault(tid.rsplit(".", 1)[0], []).append(tid)
    order = sorted(groups.values(), key=len, reverse=True)
    # A seeded shuffle, for the stress run that has to prove this suite has no order
    # dependence hiding in it. Not a speed knob: it can only make the schedule worse
    # than the ordering above, and a red run under one is a real finding about the
    # suite rather than about the seed.
    seed = os.environ.get("SIANA_TEST_SHUFFLE")
    if seed:
        random.Random(seed).shuffle(order)
    return order


# --------------------------------------------------------------------------------
# The pool.
# --------------------------------------------------------------------------------


# The most workers this will start whatever the machine has, and the number that
# stops the pool from being a bad neighbour. The captain's machine runs the whole
# fleet: while this was being measured, three other minions were running this same
# suite on it and the load average sat at 17 on eleven cores. A pool that took most
# of the box would make every one of those slower, which is the contention that
# muddied these measurements in the first place.
POOL_CAP = 5


def default_workers():
    """A conservative pool for the machine this is running on.

    Not the core count. The suite spends 0.75 of a core for its whole length, so a
    pool sized to the cores would run out of work to wait on long before it ran out
    of machine. Seven tenths of them, capped: five on the captain's eleven-core
    machine, three on a four-core GitHub runner, two on anything smaller - and never
    one, because a pool of one would silently become the serial path without being
    the serial path.

    The ratio is where the measurement ran out of resolution, and that is worth
    saying rather than dressing up. Interleaved full-suite runs at 4, 5, 6 and 8
    workers all landed between 190s and 250s against a 619s serial control, with the
    spread inside one setting as wide as the spread between settings. Under that much
    fleet noise the pool size is not what decides the wall clock, so the tie is broken
    on the thing that is not in doubt: how much of a shared machine this takes.
    """
    return max(2, min(POOL_CAP, round((os.cpu_count() or 2) * 0.7)))


def wanted_workers():
    """The pool size, from the environment if the caller named one.

    An environment variable rather than a flag: argv here belongs to unittest, and
    a flag of this file's own would be one `just test` could not pass through
    without this file learning to parse everything else.
    """
    raw = os.environ.get("SIANA_TEST_WORKERS", "").strip()
    if not raw:
        return default_workers()
    if not raw.lstrip("+").isdigit() or int(raw) < 1:
        sys.exit(f"SIANA_TEST_WORKERS must be a positive whole number, not {raw!r}")
    return int(raw)


def run_root(pid):
    """Where the run coordinated by `pid` keeps its workers' temporary directories.

    `/tmp` rather than `$TMPDIR` for the same reason `tests/fake_herdr.py` reaches
    for it: an `AF_UNIX` path is capped near 104 bytes and a default macOS temp
    directory already spends most of that, so a worker home nested under one leaves
    no room for the socket every herdr-facing test binds.

    Named after the coordinator's pid so that two runs on one machine - a minion's
    and the captain's, say - never share a directory one of them will delete.
    """
    root = "/tmp" if os.path.isdir("/tmp") else tempfile.gettempdir()
    return os.path.join(root, f"siana-run-{pid}")


def short_tmp():
    path = run_root(os.getpid())
    os.makedirs(path, exist_ok=True)
    return path


class Worker:
    """One test-running process, its own session, and the pipe it reports on.

    `start_new_session` is not tidiness. A worker drives `just`, `git`, `tasks` and
    `datafile`, and those drive more; without a session of its own there is no
    handle on that tree except walking it, and a walk finds nothing once the parent
    it hung from has gone. With one, the whole tree is a single `killpg` away and
    `reap` below only has to walk for the few children a test deliberately puts in
    a session of their own.
    """

    def __init__(self, number, root, syspath):
        self.number = number
        self.outstanding = []
        self.running = None                       # (test id, monotonic start)
        self.buffer = b""
        self.finished = False
        self.tmp = os.path.join(root, str(number))
        os.makedirs(self.tmp)
        self.log_path = os.path.join(root, f"{number}.log")
        self.log = open(self.log_path, "wb")
        env = dict(os.environ)
        env["SIANA_TEST_WORKER"] = "1"
        env["SIANA_TEST_SYSPATH"] = syspath
        # A temporary directory of its own, so a worker killed mid-test leaves its
        # litter somewhere this runner can remove wholesale. Without it the homes
        # of every test in flight survive the run in the shared temp directory.
        env["TMPDIR"] = self.tmp
        read, write = os.pipe()
        self.process = subprocess.Popen(
            [sys.executable, "-B", "-u", os.path.abspath(__file__)],
            stdin=subprocess.PIPE, stdout=write, stderr=self.log,
            env=env, text=True, start_new_session=True)
        os.close(write)
        self.fd = read

    def read(self):
        """The whole events that have arrived, or None once the worker has gone.

        Read at the descriptor and split here rather than through a buffered
        `readline`, which would block the coordinator - and with it the watchdog
        below - waiting for the rest of a line that a dead worker will never send.
        """
        data = os.read(self.fd, 1 << 16)
        if not data:
            return None
        self.buffer += data
        whole, _, self.buffer = self.buffer.rpartition(b"\n")
        return [json.loads(line) for line in whole.splitlines() if line.strip()]

    def send(self, chunk):
        """Hand over a chunk of work, or an empty line meaning there is no more."""
        try:
            self.process.stdin.write((json.dumps(chunk) if chunk else "") + "\n")
            self.process.stdin.flush()
            if not chunk:
                self.process.stdin.close()
        except (BrokenPipeError, ValueError):
            # The worker has already gone. Its death is read off the event pipe,
            # which is the one place that turns it into a named failure.
            pass
        self.outstanding = list(chunk)

    def output(self):
        try:
            with open(self.log_path, encoding="utf-8", errors="replace") as fh:
                return fh.read().strip()
        except OSError:
            return ""

    def close(self):
        os.close(self.fd)
        self.log.close()


def descendants(roots):
    """Every live process descending from `roots`, and the sessions they sit in.

    Asked of `ps` and walked here rather than left to `killpg` alone, because a
    test may deliberately put a child in a session of its own -
    `tests/test_siana.py`'s `hold` does, so that a stub's `sleep` children go with
    it - and such a child is outside its worker's group while still being this
    runner's to clean up.

    The snapshot has to be taken before anything is signalled. A killed parent's
    children reparent to init within milliseconds, and after that nothing on this
    machine can say whose they were.
    """
    # `Popen` rather than `run` so the pid of `ps` itself is known. It appears in
    # its own listing as a child of whoever asked, and it is also the pid most
    # likely to have just been freed - so signalling it would be this runner's one
    # realistic chance of reaching a process it never started.
    asking = subprocess.Popen(["ps", "-Ao", "pid=,ppid=,pgid="],
                              stdout=subprocess.PIPE, text=True)
    listing = asking.communicate()[0]
    children, groups = {}, {}
    for line in listing.splitlines():
        fields = line.split()
        if len(fields) != 3 or not all(f.lstrip("-").isdigit() for f in fields):
            continue
        pid, parent, group = (int(f) for f in fields)
        children.setdefault(parent, []).append(pid)
        groups[pid] = group
    seen, stack = set(), list(roots)
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        stack.extend(children.get(pid, ()))
    seen.discard(asking.pid)
    return seen, {groups[pid] for pid in seen if pid in groups}


def terminate(roots, grace, spare=(), settle=None):
    """SIGTERM everything descending from `roots`, then SIGKILL whatever is left.

    What it must never do is reach a process this runner did not start, so the set
    it signals is rooted at pids the caller owns and nowhere else. The caller's own
    process group is subtracted for the same reason: a worker that somehow failed
    to get a session of its own would otherwise share it, and signalling that group
    would kill the caller in the middle of its own cleanup.

    The failure this exists to prevent is a measured one. Eight CPU spin loops
    started by a task on this fleet outlived it by five hours and forty-nine
    minutes, because the shell they were started from had no job control and
    `jobs -p` quietly returned nothing to `kill`. They burned 8 of 11 cores and
    corrupted every suite timing the fleet took in that window. Nothing here asks a
    shell anything.

    Returns whatever was still alive after SIGKILL. An empty list is the only
    acceptable answer, and every caller says so when it is not.
    """
    pids, groups = descendants(roots)
    pids -= set(spare)
    groups -= {os.getpgid(0), 0}
    if not pids:
        return []

    def signal_all(sig):
        for group in groups:
            try:
                os.killpg(group, sig)
            except OSError:
                pass
        for pid in pids:
            try:
                os.kill(pid, sig)
            except OSError:
                pass

    def alive():
        return [pid for pid in pids if _alive(pid)]

    signal_all(signal.SIGTERM)
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline and alive():
        # `settle` is how a caller reaps its own children while it waits. Without
        # it a worker that took SIGTERM immediately would still read as alive - a
        # zombie answers `kill(pid, 0)` exactly as a running process does - and
        # every interrupted run would sit out the whole grace period for nothing.
        if settle:
            settle()
        time.sleep(0.05)
    signal_all(signal.SIGKILL)
    return alive()


def reap(workers, root):
    """Terminate this runner's workers and everything under them, and clear up.

    Called from the end of a normal run, from a failure, from the watchdog, from
    SIGINT and SIGTERM, and from a coordinator exception - every path out.

    Only workers that are still running can be walked: a worker that has already
    exited reparented its children to init on the way out, and after that nothing
    on this machine can say whose they were. That case is answered where it
    happens instead - a worker shutting down normally clears its own tree first
    (see `work` below), and one that died is `killpg`-ed by its session before it
    is waited for.
    """
    live = [w for w in workers if w.process.poll() is None]
    survivors = terminate([w.process.pid for w in live], GRACE_S,
                          settle=lambda: [w.process.poll() for w in live])
    for worker in live:
        try:
            worker.process.wait(timeout=GRACE_S)
        except subprocess.TimeoutExpired:
            pass
    for worker in workers:
        try:
            worker.close()
        except OSError:
            pass
    shutil.rmtree(root, ignore_errors=True)
    return survivors


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# --------------------------------------------------------------------------------
# The worker process.
# --------------------------------------------------------------------------------


class Emit(unittest.TextTestResult):
    """unittest's result, reporting to the coordinator instead of to a terminal.

    A `TextTestResult` rather than a bare `TestResult` because the tracebacks a
    developer reads are the ones unittest formats - the frames of unittest's own
    machinery removed, a cleanup's error attributed to the test it came from - and
    reimplementing that here would give this suite a second, worse idea of what a
    failure looks like.
    """

    def __init__(self, events):
        # A throwaway stream, wrapped the way `TextTestRunner` wraps its own: the
        # base class writes through `writeln`, which only the decorator adds. None
        # of it is read - the report is rebuilt in the coordinator - but the base
        # class has to have somewhere to write for its formatting to work at all.
        super().__init__(unittest.runner._WritelnDecorator(io.StringIO()), True, 1)
        self.dots = False
        self.showAll = False
        self.events = events
        self.mark = ()

    def send(self, **event):
        self.events.write(json.dumps(event) + "\n")
        self.events.flush()

    def startTest(self, test):
        super().startTest(test)
        # The outcome is read off these lists in `stopTest` rather than from the
        # `add*` hook that produced it, because `stopTest` is the one call unittest
        # makes after `doCleanups` - so an error raised by a cleanup is reported
        # under the test that registered it instead of arriving after its `end`.
        self.mark = (len(self.failures), len(self.errors), len(self.skipped),
                     len(self.expectedFailures), len(self.unexpectedSuccesses))
        self.send(e="start", id=test.id())
        # `exit=False` here, where the serial path uses `exit=True`, and the
        # difference is the whole of how a stall gets cleaned up. A worker that took
        # itself down would orphan whatever the stalled test had started: a child in
        # a session of its own survives the process group, and once its parent is
        # gone nothing can say whose it was. So the stack is dumped and the worker
        # stays alive, and the coordinator - which is still holding this worker and
        # can therefore still walk its tree - is what kills it, GRACE_S later.
        faulthandler.dump_traceback_later(STALL_S, exit=False)

    def stopTest(self, test):
        faulthandler.cancel_dump_traceback_later()
        super().stopTest(test)
        failures, errors, skipped, expected, unexpected = self.mark
        if len(self.errors) > errors:
            self.send(e="end", id=test.id(), o="error", t=self.errors[-1][1])
        elif len(self.failures) > failures:
            self.send(e="end", id=test.id(), o="fail", t=self.failures[-1][1])
        elif len(self.skipped) > skipped:
            self.send(e="end", id=test.id(), o="skip", t=self.skipped[-1][1])
        elif len(self.expectedFailures) > expected:
            self.send(e="end", id=test.id(), o="xfail",
                      t=self.expectedFailures[-1][1])
        elif len(self.unexpectedSuccesses) > unexpected:
            self.send(e="end", id=test.id(), o="uxsuccess", t="")
        else:
            self.send(e="end", id=test.id(), o="ok", t="")


def work():
    """One worker: load what the coordinator asks for, run it, report, ask again.

    The event stream gets a private descriptor and stdout is pointed at stderr, so
    a test that prints - or a library that prints on its behalf - lands in this
    worker's log rather than in the middle of a JSON line the coordinator is
    parsing.
    """
    events = os.fdopen(os.dup(1), "w", buffering=1)
    os.dup2(2, 1)
    # The coordinator's import path verbatim. Discovery inserts its top level
    # directory into `sys.path`, and a worker that rebuilt that for itself could
    # import a different module of the same name than the one that was discovered.
    sys.path[:] = json.loads(os.environ["SIANA_TEST_SYSPATH"])
    loader = unittest.TestLoader()
    result = Emit(events)
    result.failfast = os.environ.get("SIANA_TEST_FAILFAST") == "1"
    result.buffer = os.environ.get("SIANA_TEST_BUFFER") == "1"
    result.startTestRun()
    try:
        while True:
            # `readline` rather than iterating the stream, so that what this worker
            # blocks on is one line of work and never a buffer the coordinator has
            # no more lines to fill.
            line = sys.stdin.readline().strip()
            if not line:
                break
            wanted = json.loads(line)
            loaded, why = {}, ""
            try:
                where = wanted[0].rsplit(".", 1)[0]
                loaded = {t.id(): t for t in iterate(loader.loadTestsFromName(where))}
            except BaseException:
                # Every exception, because a loader failing is not one of the
                # outcomes below it: it is this worker unable to do the job it was
                # given, and the run has to say so with the ids in it rather than
                # report a suite that is quietly smaller. The reason is carried
                # into the report, because a load that failed without saying why
                # is the least diagnosable thing this file could print.
                why = traceback.format_exc()
            # Anything the coordinator discovered and this worker cannot load is a
            # named error rather than a test that quietly did not run. The two use
            # the same discovery, so this should be unreachable; it is here because
            # the alternative to reaching it is a shorter green run.
            for tid in wanted:
                if tid not in loaded:
                    result.send(e="start", id=tid)
                    result.send(e="end", id=tid, o="error",
                                t=f"{tid} was discovered but could not be loaded "
                                  f"in worker {os.getpid()}\n{why}")
            suite = unittest.TestSuite(loaded[t] for t in wanted if t in loaded)
            suite(result)
            result.send(e="idle")
            if result.failfast and not result.wasSuccessful():
                break
    finally:
        result.stopTestRun()
        events.close()
        # A worker clears its own tree before it goes, and this is the only place
        # that case can be answered from. A test may leave a child behind - one in
        # a session of its own, which is the shape `tests/test_siana.py` uses - and
        # once this process has exited that child has reparented to init, where
        # nothing can say it was ever this run's. So it is taken now, while there
        # is still a parent to name it by.
        left = terminate([os.getpid()], GRACE_S // 2, spare=(os.getpid(),))
        if left:
            sys.stderr.write(f"worker {os.getpid()} could not reap {sorted(left)}\n")


# --------------------------------------------------------------------------------
# The coordinator.
# --------------------------------------------------------------------------------


SEPARATOR1 = "=" * 70
SEPARATOR2 = "-" * 70
LABEL = {"error": "ERROR", "fail": "FAIL", "uxsuccess": "UNEXPECTED SUCCESS"}


def report(ids, outcomes, wall, verbosity, stream):
    """unittest's summary, rebuilt from what the workers said.

    Walked in discovery order rather than in the order results arrived, so the
    report of a given head is the same text every time however the pool happened to
    schedule it. That is the property that makes two runs comparable at all.
    """
    counts = {}
    for tid in ids:
        outcome, detail = outcomes[tid]
        counts[outcome] = counts.get(outcome, 0) + 1
        if outcome in LABEL:
            stream.write(f"{SEPARATOR1}\n{LABEL[outcome]}: {tid}\n{SEPARATOR2}\n")
            stream.write(f"{detail.rstrip()}\n\n" if detail else "\n")
        elif outcome == "skip" and verbosity > 1:
            stream.write(f"SKIP: {tid}: {detail}\n")
    stream.write(f"{SEPARATOR2}\nRan {len(ids)} test"
                 f"{'' if len(ids) == 1 else 's'} in {wall:.3f}s\n\n")
    bad = counts.get("fail", 0) + counts.get("error", 0) + counts.get("uxsuccess", 0)
    tally = [(name, counts.get(key, 0)) for name, key in
             (("failures", "fail"), ("errors", "error"), ("skipped", "skip"),
              ("expected failures", "xfail"), ("unexpected successes", "uxsuccess"))]
    detail = ", ".join(f"{name}={n}" for name, n in tally if n)
    stream.write(("FAILED" if bad else "OK") + (f" ({detail})" if detail else "") + "\n")
    stream.flush()
    return bad == 0


def run_local(tests, outcomes, total, stream):
    """The modules that would not import, run here rather than in a worker."""
    for number, test in enumerate(tests, 1):
        stream.write(f"{number:4d}/{total} -- {test.id()}\n")
        result = unittest.TestResult()
        test.run(result)
        detail = (result.errors + result.failures + [(None, "")])[0][1]
        outcomes[test.id()] = ("error" if result.errors else
                               "fail" if result.failures else "ok", detail)
    stream.flush()


def coordinate(ids, pool, verbosity, failfast, stream):
    """Hand `ids` to `pool` worker processes and report what comes back.

    Every exit from here goes through `reap`, including the ones nobody plans for:
    a coordinator that raised, a terminal that sent SIGINT, and a supervisor that
    sent SIGTERM. A worker outliving the run it belongs to is the failure this
    whole arrangement is written around.
    """
    outcomes = {}
    # The root is made before anything else can raise, so that every path from here
    # on - including a report stream that fails on its first line - leaves through
    # the `finally` below and takes the run's temporary state with it.
    root = short_tmp()
    workers, started = [], time.monotonic()
    survivors, stalled = [], None

    def cleanup():
        return reap(workers, root)

    def interrupted(signum, _frame):
        left = cleanup()
        stream.write(f"\ninterrupted by signal {signum}\n")
        if left:
            stream.write(f"left running after SIGKILL: {sorted(left)}\n")
        stream.flush()
        # `_exit`, because the handler may be running on top of a `finally` that
        # would otherwise reap a second time against pids already gone.
        os._exit(130 if signum == signal.SIGINT else 143)

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, interrupted)

    try:
        stream.write(f"{len(ids)} tests, {pool} workers "
                     f"(SIANA_TEST_WORKERS=1 for one worker, in this process)\n")
        stream.flush()
        run_local(Plan.broken, outcomes, len(ids), stream)
        syspath = json.dumps(sys.path)
        os.environ["SIANA_TEST_FAILFAST"] = "1" if failfast else "0"
        os.environ["SIANA_TEST_BUFFER"] = "1" if Plan.buffer else "0"
        for number in range(1, pool + 1):
            workers.append(Worker(number, root, syspath))

        pending = chunks(ids)
        selector = selectors.DefaultSelector()
        for worker in workers:
            selector.register(worker.fd, selectors.EVENT_READ, worker)
        stopping = False
        counter = len(outcomes)

        def hand_out(worker):
            nonlocal stopping
            if pending and not stopping:
                worker.send(pending.pop(0))
            else:
                worker.send([])
                worker.finished = True

        def finish(worker, reason):
            """A worker has gone. Anything it was holding is named, not forgotten.

            This is the arm that keeps a crash from reading as a shorter green run:
            a worker that dies mid-test takes its own report with it, so the report
            is written here instead, from what the coordinator last saw it doing.
            Under failfast nothing is synthesized, because there the tests that did
            not run are the point rather than a hole.
            """
            selector.unregister(worker.fd)
            if worker.process.stdin and not worker.process.stdin.closed:
                try:
                    worker.process.stdin.close()
                except (BrokenPipeError, OSError):
                    # Closing flushes, and a worker that has already gone leaves
                    # nothing at the other end to flush into. Reached whenever a
                    # worker decides to stop before the coordinator tells it to,
                    # which under failfast is every time.
                    pass
            if reason == "died":
                # Whatever it was holding, before it is waited for. A dead worker is
                # a zombie until then, and a zombie still owns its pid - so its
                # session is still nameable and `killpg` still reaches the children
                # it left in it. After the `wait` below the pid is free and this
                # would be a signal to whatever got it next.
                try:
                    os.killpg(worker.process.pid, signal.SIGKILL)
                except OSError:
                    pass
            worker.process.wait()
            if worker.running and not stopping:
                tid = worker.running[0]
                outcomes.setdefault(tid, ("error", (
                    f"worker {worker.number} {reason} while running this test "
                    f"(exit {worker.process.returncode})\n\n{worker.output()}\n")))
                worker.outstanding = [t for t in worker.outstanding if t != tid]
            if not stopping:
                for tid in worker.outstanding:
                    outcomes.setdefault(tid, ("error", (
                        f"never ran: worker {worker.number} {reason} "
                        f"(exit {worker.process.returncode}) before reaching it\n")))
            worker.running = None
            worker.outstanding = []

        for worker in workers:
            hand_out(worker)

        while selector.get_map():
            for key, _ in selector.select(timeout=1.0):
                worker = key.data
                events = worker.read()
                if events is None:
                    finish(worker, "exited" if worker.finished else "died")
                    continue
                for event in events:
                    if event["e"] == "start":
                        worker.running = (event["id"], time.monotonic())
                        counter += 1
                        stream.write(f"{counter:4d}/{len(ids)} "
                                     f"w{worker.number} {event['id']}\n")
                        stream.flush()
                    elif event["e"] == "end":
                        worker.running = None
                        worker.outstanding = [t for t in worker.outstanding
                                              if t != event["id"]]
                        if event["id"] in outcomes:
                            outcomes[event["id"]] = ("error", (
                                f"{event['id']} was reported twice; the runner "
                                f"handed it out more than once\n"))
                        else:
                            outcomes[event["id"]] = (event["o"], event["t"])
                        if event["o"] in LABEL:
                            stream.write(f"          {LABEL[event['o']]}\n")
                            if failfast:
                                stopping = True
                        elif verbosity > 1:
                            stream.write("          " + (
                                f"skipped: {event['t']}" if event["o"] == "skip"
                                else event["o"]) + "\n")
                    elif event["e"] == "idle":
                        hand_out(worker)
                stream.flush()

            # The stall, and the coordinator is what ends one. A worker dumps every
            # thread's stack at STALL_S and keeps running, because a worker that
            # took itself down there would leave the stalled test's children with
            # no parent to name them by. So the diagnosis is written in the worker
            # and the killing is done here, GRACE_S later, while this process still
            # holds the worker and can still walk the tree under it.
            now = time.monotonic()
            for worker in workers:
                if worker.running and now - worker.running[1] > STALL_S + GRACE_S:
                    stalled = worker
                    break
            if stalled:
                break

        if stalled:
            outcomes.setdefault(stalled.running[0], ("error", (
                f"worker {stalled.number} spent more than {STALL_S + GRACE_S}s on "
                f"this test and was killed\n\n{stalled.output()}\n")))
        # Anything left in flight when the loop ends. Under failfast that is
        # expected and unittest does not report it either; otherwise a test with no
        # outcome is a hole in the run and is named as one.
        for worker in workers:
            if worker.running:
                outcomes.setdefault(worker.running[0],
                                    ("error", "the run ended while this test was "
                                              "still running\n"))
    finally:
        survivors = cleanup()

    if not stopping:
        for tid in ids:
            outcomes.setdefault(tid, ("error", (
                "no worker reported this test; it was discovered and never ran\n")))
    reported = [t for t in ids if t in outcomes]
    ok = report(reported, outcomes, time.monotonic() - started, verbosity, stream)
    if survivors:
        stream.write(f"\nleft running after SIGKILL: {sorted(survivors)}\n")
    return 0 if ok and not survivors and not stalled else 1


def main():
    if os.environ.get("SIANA_TEST_WORKER") == "1":
        work()
        return 0
    pool = wanted_workers()
    # Armed around discovery as well as around each test, because discovery imports
    # every module before the first test runs and a module here does real work at
    # import: the wake tests probe for a node that runs TypeScript by running one. A
    # stall there is the one this would otherwise miss entirely, having printed
    # nothing yet.
    faulthandler.dump_traceback_later(STALL_S, exit=True)
    if pool == 1:
        # The control, and deliberately not this file's pool with one worker in it:
        # a mode meant for deciding whether a failure is the runner's fault has to
        # be a path the runner is not on. So discovery is not run twice here - this
        # is `unittest.main` doing its own, exactly as it did before there was a
        # pool at all.
        outcome = unittest.main(module=None, exit=False, testRunner=Runner,
                                argv=["unittest", "discover", "-s", TESTS,
                                      *sys.argv[1:]])
        faulthandler.cancel_dump_traceback_later()
        return 0 if outcome.result.wasSuccessful() else 1
    ids = discover(sys.argv[1:])
    faulthandler.cancel_dump_traceback_later()
    # `sys.stderr`, because that is where `unittest.TextTestRunner` writes and the
    # two modes have to be the same run with a different clock. A pool that reported
    # on stdout would make `just test 2>/dev/null` mean one thing serially and
    # another in parallel, which is the kind of difference nobody notices until a
    # log is missing the half that mattered.
    return coordinate(ids, pool, Plan.verbosity, Plan.failfast, sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
