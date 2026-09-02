#!/usr/bin/env python3
"""A scripted stand-in for `siana-read`, put on PATH ahead of the real one.

The real one is driven for everything it can be made to say: a real store, a real
`datafile`, a real corrupted line, a scripted herdr. What it cannot be made to say
on cue is the thing that is wrong with `siana-read` itself - it printed something
that is not JSON, it printed nothing at all, it hung, it is not installed. Those
are the console's own transport failures, and they are exactly the ones that must
not arrive as an empty fleet.

Only the transport is scripted. Everything the console builds around it is real:
the argv, the subprocess, the exit code, the parsing.

Driven by one directory named in the environment:

    FAKE_READ_DIR    holds `<subcommand>.out` for the bytes to print, and
                     `<subcommand>.exit` for the code to exit. A subcommand with
                     neither is answered with an ordinary empty document, so a test
                     scripts the one source it is about and leaves the other five
                     honest.
    FAKE_READ_SLEEP  seconds to hold a subcommand before answering, as
                     `<subcommand>:<seconds>`, for the read that never returns.
"""

import os
import sys
import time

what = sys.argv[1] if len(sys.argv) > 1 else ""
where = os.environ.get("FAKE_READ_DIR", "")

for pair in os.environ.get("FAKE_READ_SLEEP", "").split(","):
    if pair and pair.split(":")[0] == what:
        time.sleep(float(pair.split(":")[1]))

out = os.path.join(where, f"{what}.out")
code = os.path.join(where, f"{what}.exit")

if os.path.isfile(out):
    with open(out) as fh:
        sys.stdout.write(fh.read())
else:
    # The shape a store answers in, with nothing in it. Not a refusal: a test that
    # scripts one source must not have to script the other five to keep them quiet.
    sys.stdout.write(
        '{"source": "%s", "revision": {"inode": 1, "size": 0}, "filter": {},'
        ' "total": 0, "matched": 0, "records": [], "bad_lines": []}\n' % what)

if os.path.isfile(code):
    with open(code) as fh:
        raise SystemExit(int(fh.read().strip()))
raise SystemExit(0)
