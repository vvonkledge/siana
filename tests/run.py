"""Run the suite so that a run which stalls says what it was doing.

`python3 -m unittest` reports progress as bare dots, and the first newline it
writes is the one before the summary. Its own flushes do not help: a line-oriented
reader shows nothing until a newline arrives, and the GitHub Actions runner is one.
So a job killed by its hang guard prints exactly nothing, however far it got.

That is not hypothetical. Three CI runs on this project were killed at the
fifteen-minute guard having produced not one byte, so the only thing anybody could
say about the hang was that it existed; finding it took a Linux container and a
process list rather than a log. This file is what makes the next one readable.

Two changes, and only these two:

- One whole newline-terminated line per test, written before the test runs. The
  last line of a killed run is the test that was running when it was killed.
- A watchdog around each test. A stall dumps every thread's stack and takes the
  process down, which turns "the job ran out of wall clock" into a diagnosable
  failure. `faulthandler` runs its timer on its own thread and writes at the file
  descriptor, so it fires while the main thread is blocked in a syscall - which is
  where a stalled test here actually sits, waiting on a child that will not answer.

Discovery, arguments and reporting are unittest's own: `just test -k slug` and
`just test -v` reach this unchanged.
"""

import faulthandler
import os
import sys
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
STALL_S = 480

# The suite's size, for the progress line. Set by the runner below, which is the
# only thing that has counted the tests before they start.
TOTAL = 0


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


if __name__ == "__main__":
    # Armed here as well as around each test, because discovery imports every module
    # before the first test runs and a module here does real work at import: the wake
    # tests probe for a node that runs TypeScript by running one. A stall there is
    # the one this would otherwise miss entirely, having printed nothing yet.
    faulthandler.dump_traceback_later(STALL_S, exit=True)
    unittest.main(module=None, testRunner=Runner,
                  argv=["unittest", "discover", "-s", TESTS, *sys.argv[1:]])
