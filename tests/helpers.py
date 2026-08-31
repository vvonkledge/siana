"""What every test here needs: a bin/ command loaded as a module, and a throwaway
SIANA home to point one at.

The commands have no `.py` extension, because they are commands and not a package,
so they are loaded by path rather than imported by name. Each one guards its
`main()` behind `__name__ == "__main__"`, so loading one runs nothing.

Loading is how the exact mechanics get tested: `fold`, `resolve`, `reports`, `slug`
are pure functions of their inputs, and a test that drove them through a subprocess
would be testing the CLI's plumbing instead of the rule the function encodes. The
bash commands have no such seam, so those are driven as processes, with `tasks` and
`datafile` real rather than faked: a stub would agree with whatever this suite
believed about them, which is the one thing a contract test must not do.
"""

import importlib.machinery
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

# bin/ holds commands, not a package, so a .pyc beside one is build litter from
# this suite and nothing else. Set before any load, because the loader decides
# whether to write one at compile time.
#
# `just test` runs `python3 -B`, which covers this and the test modules discovery
# compiles before this line can run. This stays for the suite driven straight
# through `python3 -m unittest`, where a load below is the one thing still writing
# into bin/.
sys.dont_write_bytecode = True

DISTRO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(DISTRO, "bin")
TEMPLATE = os.path.join(DISTRO, "template")

_loaded = {}


def script(name):
    """A bin/ command, loaded as a module. Cached: loading is a compile."""
    if name not in _loaded:
        # An explicit loader because the file has no `.py` suffix: without one,
        # `spec_from_file_location` decides it is not importable and returns None.
        path = os.path.join(BIN, name)
        spec = importlib.util.spec_from_file_location(
            name.replace("-", "_"), path,
            loader=importlib.machinery.SourceFileLoader(name.replace("-", "_"), path))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _loaded[name] = module
    return _loaded[name]


class HomeTest(unittest.TestCase):
    """One throwaway SIANA home per test, holding whatever that test needs.

    Nothing is copied in by default. A missing contract and a missing template are
    both refusal paths worth testing, so a home that arrived fully furnished would
    make them unreachable.
    """

    def setUp(self):
        self.home = tempfile.mkdtemp(prefix="siana-test-")
        self.addCleanup(shutil.rmtree, self.home, ignore_errors=True)

    def at(self, *parts):
        return os.path.join(self.home, *parts)

    def contract(self, *names):
        """Contracts, from the distro's own templates. Never a copy written here:
        a test carrying its own idea of the contract would pass against a shape
        the real store rejects."""
        for name in names:
            shutil.copy(os.path.join(TEMPLATE, f"schema-{name}.yaml"),
                        self.at(f"schema-{name}.yaml"))

    def older_contract(self, name, field):
        """One field taken back out of a contract this home already has.

        Neither `init` nor `upgrade` ever rewrites a live contract, because a field
        dropped from one makes every record still carrying it unreadable. So a home
        installed before a field was added still has a contract without it after the
        documented upgrade, and that is a state the fleet has to keep working in.

        Built by removing the field from the distro's own template rather than by
        writing a contract here, so every other field stays declared exactly as the
        real store declares it and only the one under test is missing. The removal
        is asserted, because a fixture that quietly stopped removing anything would
        leave every test using it passing about nothing."""
        path = self.at(f"schema-{name}.yaml")
        with open(path) as fh:
            lines = fh.read().splitlines(True)
        keep, dropping = [], False
        for line in lines:
            if re.match(r"^  [a-z_]+:", line):
                dropping = line.startswith(f"  {field}:")
            if not dropping:
                keep.append(line)
        with open(path, "w") as fh:
            fh.writelines(keep)
        with open(path) as fh:
            self.assertNotIn(field, fh.read())

    def template(self, *names):
        for name in names:
            shutil.copy(os.path.join(TEMPLATE, name), self.at(name))

    def store(self, name, *records):
        """Raw lines into an append-only store. Raw because several tests are about
        what a reader does with a line no writer would produce: a tombstone, a blank,
        a record with no key, a half-written tail."""
        with open(self.at(name), "a") as fh:
            for rec in records:
                fh.write((rec if isinstance(rec, str) else json.dumps(rec)) + "\n")
        return self.at(name)

    def project(self, handle, path=None, **fields):
        """A project in the registry, written through `datafile` so it passes the
        same contract a real one does."""
        args = [f"handle={handle}", f"path={path if path is not None else self.home}"]
        args += [f"{k}={v}" for k, v in fields.items()]
        out = self.run_cmd(["datafile", "-f", self.at("projects.jsonl"),
                            "-c", self.at("schema-projects.yaml"), "put",
                            *sum((["--set", a] for a in args), [])])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def queue(self):
        """A real queue, initialised by `tasks` itself."""
        out = self.run_cmd(["tasks", "init"], cwd=self.home)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def distro_path(self, *first, without=()):
        """A PATH on which this checkout's `bin/` is the only SIANA there is.

        `run_bin` reaches a command by the path this suite already knows, so most
        of the suite never needs one. A verify does: `siana-pipeline check` is a
        string on a task, and the queue runs it through a shell. Both halves of
        this are what make that resolve to the code under test.

        `bin/` goes on the front, because without it nothing answers to the name at
        all. That is the exit 127 a clean runner hits.

        Every installed copy comes off, because the captain has one on their PATH
        and it would otherwise answer instead - the suite going green against a
        distro nobody changed, which is how the same failure stayed invisible here
        until CI found it. A directory holding one is mirrored without it rather
        than dropped: `~/.local/bin` holds `tasks` too, and losing that would fail
        the suite on a store it needs instead of on the SIANA it is hiding.

        `without` names further commands to hide the same way, for a test whose
        scenario is that one of them is not installed on this machine at all. See
        `path_with_no_forge_client` for why hiding by name rather than by directory
        is the only version of that which holds off this machine.
        """
        hidden = {n for n in os.listdir(BIN)
                  if os.path.isfile(os.path.join(BIN, n))}
        hidden.update(without)
        # A fresh root per call. Two PATHs built in one test hide different names,
        # and a mirror keyed by the entry's position alone would be reused across
        # both - handing the second one whatever the first had left out.
        # `test_repair.TwoPathsFromOneTest` is that test, and it is the only thing
        # that fails if this goes back to a key the two calls share.
        root = tempfile.mkdtemp(prefix="path-", dir=self.home)
        out = []
        for i, d in enumerate(os.environ["PATH"].split(os.pathsep)):
            try:
                entries = os.listdir(d)
            except OSError:
                # An unreadable or absent PATH entry finds nothing either way, so
                # it is passed through rather than made this fixture's problem.
                out.append(d)
                continue
            if not hidden.intersection(entries):
                out.append(d)
                continue
            mirror = os.path.join(root, str(i))
            os.makedirs(mirror, exist_ok=True)
            for entry in entries:
                link = os.path.join(mirror, entry)
                if entry not in hidden and not os.path.lexists(link):
                    os.symlink(os.path.join(d, entry), link)
            out.append(mirror)
        return os.pathsep.join([*first, BIN, *out])

    def path_with_no_forge_client(self, *first):
        """A PATH on which neither forge client can be found, wherever this machine
        keeps one.

        A test that hides a command by naming the directories it believes are clean
        is asserting this machine's package layout instead of building the world it
        claims. Three tests here passed `PATH=/usr/bin:/bin` to mean "no `gh`". A
        GitHub Actions runner installs `gh` in `/usr/bin`, so what they exercised
        was the real client with no `GH_TOKEN`, and its login error read as the
        refusal they were looking for on the machine that wrote them and as a
        different refusal on the one that ran them (Actions run 33301560920).

        Hiding by name leaves that nowhere to happen: every entry of the real PATH
        is still reachable, so `git`, `env` and `python3` answer as they always
        did, and the two names do not answer from anywhere.

        The names come from `siana-publish`, which is the command that shells out
        to them, so a third forge added there is hidden here without anyone having
        to remember this.
        """
        clients = {argv[0] for argv, _ in script("siana-publish").REQUESTS.values()}
        return self.distro_path(*first, without=sorted(clients))

    def command_env(self, extra=None):
        """The environment a command under test runs in.

        One place, because a command started with `Popen` has to be given the same
        hygiene as one run with `run`, and two copies of these rules would drift
        exactly where a drifted one is invisible: a suite that read the captain's
        live fleet would still be green.

        Not `env`: `test_siana.Siana` already has one of those, with a different
        signature and a different job, and a base class quietly shadowed by a
        subclass is how one rename turned 33 tests into a TypeError.
        """
        e = dict(os.environ)
        e["SIANA_HOME"] = self.home
        e.pop("SIANA_TASKS_FILE", None)
        # A suite run by a minion inherits its task id, and `siana-afk` refuses to
        # activate in a minion's environment. Left in, every activation test here
        # would pass on CI and refuse on the machine that wrote it - which is the
        # one place a suite must not disagree with itself. The test of that refusal
        # sets the variable back, deliberately.
        e.pop("SIANA_TASK_ID", None)
        # The suite's own tests run inside a worker of `tests/run.py`, which marks
        # its workers with this. Left in, a command under test that is itself a run
        # of the suite would start in worker mode and sit waiting on a coordinator
        # that is never going to talk to it. `tests/test_run.py` is where that
        # happens, and a hang there would be a hang in the runner's own tests.
        e.pop("SIANA_TEST_WORKER", None)
        e.pop("SIANA_TEST_SYSPATH", None)
        e.update(extra or {})
        return e

    def run_cmd(self, argv, cwd=None, env=None, timeout=120):
        return subprocess.run(argv, cwd=cwd or self.home, env=self.command_env(env),
                              text=True, capture_output=True, timeout=timeout)

    def run_bin(self, name, *args, **kw):
        return self.run_cmd([os.path.join(BIN, name), *args], **kw)

    def assertRefused(self, out, *fragments):
        """A refusal is a nonzero exit and a message that says what was refused.
        Both halves matter: an exit code with no explanation is the thing every
        script in this distro is written not to do."""
        text = out.stdout + out.stderr
        self.assertNotEqual(out.returncode, 0, f"expected a refusal, got:\n{text}")
        for fragment in fragments:
            self.assertIn(fragment, text)
        return text

    def assertAccepted(self, out):
        text = out.stdout + out.stderr
        self.assertEqual(out.returncode, 0, f"expected success, got:\n{text}")
        return text


def gone_pid():
    """A pid that is certainly not running, without killing anything to get one.

    A process that has already exited is the only way to be sure: an invented pid
    might belong to something, and a test that signals it would be reaching outside
    itself."""
    dead = subprocess.Popen(["true"])
    dead.wait()
    return dead.pid


def until(predicate, timeout=15.0, interval=0.05):
    """Poll rather than sleep: a fixed wait is either slow or flaky, and under load
    it is both."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False
