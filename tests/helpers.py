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
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

# bin/ holds commands, not a package, so a .pyc beside one is build litter from
# this suite and nothing else. Set before any load, because the loader decides
# whether to write one at compile time.
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

    def run_cmd(self, argv, cwd=None, env=None, timeout=120):
        e = dict(os.environ)
        e["SIANA_HOME"] = self.home
        e.pop("SIANA_TASKS_FILE", None)
        e.update(env or {})
        return subprocess.run(argv, cwd=cwd or self.home, env=e, text=True,
                              capture_output=True, timeout=timeout)

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
