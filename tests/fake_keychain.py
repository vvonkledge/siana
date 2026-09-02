"""A keychain the suite can drive, and that records everything it was told.

The real one is macOS's `security`, and there are two reasons nothing here can use
it. It stores into the captain's own login keychain, so a suite that drove it would
be writing test credentials into the machine it runs on. And CI runs on Linux, where
there is no keychain at all - which would leave every refusal in `siana-fact`'s
credential path tested on one developer's Mac and nowhere else.

So this is a `security` with the same command line, put on PATH by way of
`SIANA_KEYCHAIN`. What it reproduces is exactly the interface that matters:

  - the value is never on the command line. `add-generic-password ... -w` with
    nothing after the flag prompts for it, twice, and refuses when the two differ.
  - `find-generic-password` answers 44 for an item that is not there, and returns
    the value only when asked with `-w`. Without it the item's attributes come
    back, which is how `siana-fact status` asks whether a reference is live without
    reading what it points at.
  - a duplicate answers 45 unless `-U` says to overwrite, and a keychain that will
    not open answers 51.

One divergence, and it is deliberate. The real `security` reads its prompt from the
terminal, which nothing can write to from a test; this one reads stdin. What that
changes is where the characters come from, and not the thing under test: the value
still arrives on neither the command line nor a file, and this keychain records its
own argv so a test can prove it.

`INSTRUMENT` is the whole point of the recording. Every invocation appends its argv,
so a test asserts against what the keychain was actually told rather than against
what the command meant to tell it. The secret is never written there, because a
fixture that logged it would be the leak the tests are looking for.
"""

import json
import os
import stat

# What the fake writes beside its items, one JSON array of argv per line.
INSTRUMENT = "argv.jsonl"

ITEM_NOT_FOUND = 44
DUPLICATE_ITEM = 45
INTERACTION_NOT_ALLOWED = 51

# The environment this keychain reads. `SIANA_KEYCHAIN` points `siana-fact` at it;
# these two are the fake's own.
DIR_ENV = "FAKE_KEYCHAIN_DIR"
FAIL_ENV = "FAKE_KEYCHAIN_EXIT"

SOURCE = '''#!/usr/bin/env python3
"""A stand-in for macOS `security`. See tests/fake_keychain.py."""
import hashlib
import json
import os
import sys

root = os.environ["{DIR_ENV}"]
os.makedirs(root, exist_ok=True)
argv = sys.argv[1:]
with open(os.path.join(root, "{INSTRUMENT}"), "a") as fh:
    fh.write(json.dumps(argv) + "\\n")

forced = os.environ.get("{FAIL_ENV}")
if forced:
    print("fake keychain: refusing every request", file=sys.stderr)
    raise SystemExit(int(forced))


def flag(name):
    return argv[argv.index(name) + 1] if name in argv else None


def path(service, account):
    # Hashed, so an item name can hold anything a service or an account can and
    # never has to be a legal filename.
    return os.path.join(root, hashlib.sha256(
        json.dumps([service, account]).encode()).hexdigest())


command = argv[0] if argv else ""
item = path(flag("-s"), flag("-a"))

if command == "add-generic-password":
    if os.path.exists(item) and "-U" not in argv:
        print("The specified item already exists in the keychain.", file=sys.stderr)
        raise SystemExit({DUPLICATE_ITEM})
    # `-w` last and empty is what makes the real one prompt. Here it reads the two
    # lines a person would have typed, and refuses the same mismatch it does.
    first = sys.stdin.readline().rstrip("\\n")
    again = sys.stdin.readline().rstrip("\\n")
    if first != again:
        print("They don't match.", file=sys.stderr)
        raise SystemExit(1)
    with open(item, "w") as fh:
        fh.write(first)
    raise SystemExit(0)

if command == "find-generic-password":
    if not os.path.exists(item):
        print("The specified item could not be found in the keychain.",
              file=sys.stderr)
        raise SystemExit({ITEM_NOT_FOUND})
    if "-w" in argv:
        with open(item) as fh:
            sys.stdout.write(fh.read() + "\\n")
        raise SystemExit(0)
    # Attributes and no value, which is what a caller asking whether the item is
    # there is entitled to.
    print('keychain: "fake"')
    print('    "acct"<blob>="%s"' % flag("-a"))
    print('    "svce"<blob>="%s"' % flag("-s"))
    raise SystemExit(0)

if command == "delete-generic-password":
    if not os.path.exists(item):
        raise SystemExit({ITEM_NOT_FOUND})
    os.remove(item)
    raise SystemExit(0)

print("fake keychain: unknown command %r" % command, file=sys.stderr)
raise SystemExit(1)
'''


class FakeKeychain:
    """One keychain per test, in a directory of its own."""

    def __init__(self, root: str) -> None:
        self.root = root
        os.makedirs(root, exist_ok=True)
        self.command = os.path.join(root, "security")
        with open(self.command, "w") as fh:
            fh.write(SOURCE.format(DIR_ENV=DIR_ENV, FAIL_ENV=FAIL_ENV,
                                   INSTRUMENT=INSTRUMENT,
                                   ITEM_NOT_FOUND=ITEM_NOT_FOUND,
                                   DUPLICATE_ITEM=DUPLICATE_ITEM))
        os.chmod(self.command, os.stat(self.command).st_mode | stat.S_IXUSR)
        self.items = os.path.join(root, "items")

    def env(self, exit_code=None):
        """What a command has to be given to reach this keychain instead of one."""
        out = {"SIANA_KEYCHAIN": self.command, DIR_ENV: self.items}
        if exit_code is not None:
            out[FAIL_ENV] = str(exit_code)
        return out

    def calls(self):
        """Every argv this keychain has been given, oldest first."""
        log = os.path.join(self.items, INSTRUMENT)
        if not os.path.isfile(log):
            return []
        with open(log) as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def files(self):
        """Every path this keychain has written, for a scan that has to cover the
        one place a test secret legitimately lives."""
        out = []
        for dirpath, _, filenames in os.walk(self.root):
            out.extend(os.path.join(dirpath, name) for name in filenames)
        return out
