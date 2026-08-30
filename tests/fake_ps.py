#!/usr/bin/env python3
"""A `ps` that will not name one pid, put on PATH ahead of the real one.

The console draws a line the watcher does not: a claim it cannot prove stale is
never taken over. The case that separates the two is a recorded pid that is
demonstrably alive while `ps` says nothing about it - the watcher reads that as a
process that has been replaced, and the console reads it as knowing nothing.

A live process `ps` refuses to name cannot be arranged. So `ps` is scripted, and
only for the pid the test names: every other pid is answered by the real one, so
the console's own identity check still runs against the real machine.

    FAKE_PS_SILENT   the pid to answer nothing about
"""

import os
import subprocess
import sys

silent = os.environ.get("FAKE_PS_SILENT", "")
if silent and silent in sys.argv:
    raise SystemExit(1)

# The real one, found past whatever directory this was put in.
here = os.path.dirname(os.path.abspath(__file__))
real = next((p for p in (os.path.join(d, "ps")
                         for d in os.environ.get("PATH", "").split(os.pathsep))
             if os.path.dirname(os.path.abspath(p)) != here
             and os.access(p, os.X_OK)), None)
if not real:
    print("no real ps to fall through to", file=sys.stderr)
    raise SystemExit(1)
raise SystemExit(subprocess.run([real, *sys.argv[1:]]).returncode)
