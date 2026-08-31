"""The command `siana-fact exec` runs, instrumented, so a test can say exactly what
reached it and exactly what did not.

`exec` is the only path a credential is ever read on, and what has to be proved
about it is a negative: the value goes into the child's environment and into nothing
else. A negative needs a witness on both sides, so this child records three things
and then exits however the test asked it to.

  - its own `argv`, verbatim. That is also its process title on both platforms this
    fleet runs on, so an argv with no secret in it is a process list with none.
  - the username it was given, in the clear, because that half is nonsecret and a
    test has to check the right one arrived.
  - the **sha256** of the password, never the password. A fixture that wrote the
    value down would plant exactly what the repository-wide scan is hunting for, and
    the scan would find it and be right to.

`--exit` and `--signal` are how the child's own outcome is driven, because `exec`
promising to return that outcome is the other thing worth checking.
"""

import hashlib
import json
import os
import stat

RECORD = "child.json"

SOURCE = '''#!/usr/bin/env python3
"""A stand-in for the command an authorised minion runs. See tests/fake_child.py."""
import hashlib
import json
import os
import signal
import sys

argv = sys.argv[1:]
password = os.environ.get("SIANA_FACT_PASSWORD")
with open(os.environ["{RECORD_ENV}"], "w") as fh:
    json.dump({{
        "argv": argv,
        "username": os.environ.get("SIANA_FACT_USERNAME"),
        # The digest and never the value: a fixture that wrote the password down
        # would be the leak every scan in this suite is looking for.
        "password_sha256": (hashlib.sha256(password.encode()).hexdigest()
                            if password is not None else None),
        "saw_password_name": "SIANA_FACT_PASSWORD" in os.environ,
    }}, fh)

if "--signal" in argv:
    os.kill(os.getpid(), signal.SIGTERM)
if "--exit" in argv:
    raise SystemExit(int(argv[argv.index("--exit") + 1]))
raise SystemExit(0)
'''

RECORD_ENV = "FAKE_CHILD_RECORD"


class FakeChild:
    """One instrumented child per test, in a directory of its own."""

    def __init__(self, root: str) -> None:
        os.makedirs(root, exist_ok=True)
        self.command = os.path.join(root, "child")
        self.record = os.path.join(root, RECORD)
        with open(self.command, "w") as fh:
            fh.write(SOURCE.format(RECORD_ENV=RECORD_ENV))
        os.chmod(self.command, os.stat(self.command).st_mode | stat.S_IXUSR)

    def env(self):
        return {RECORD_ENV: self.record}

    def saw(self):
        """What the child recorded, or None when it was never run. Both matter: half
        the refusals here are only refusals if the child never started."""
        if not os.path.isfile(self.record):
            return None
        with open(self.record) as fh:
            return json.load(fh)

    @staticmethod
    def digest(secret: str) -> str:
        return hashlib.sha256(secret.encode()).hexdigest()
