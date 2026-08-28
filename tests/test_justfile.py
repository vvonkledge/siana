"""The recipes the captain actually runs: `init`, `upgrade`, `doctor`.

`init` is exercised for real, into a throwaway home with a throwaway bindir, because
the thing worth checking is that a fresh install is one the rest of the distro can
read. The two refusals under it are the ones that turn a typo into a fresh empty
fleet, or a stale contract into a traceback at `add` time.
"""

import os
import shutil
import subprocess
import tempfile
import unittest

from helpers import DISTRO, HomeTest


def has(cmd):
    return shutil.which(cmd) is not None


class Recipe(HomeTest):

    def setUp(self):
        super().setUp()
        self.bindir = self.at("bin")

    def just(self, *args, home=None, timeout=180):
        e = dict(os.environ)
        e.pop("SIANA_HOME", None)
        return subprocess.run(
            ["just", f"home={home if home is not None else self.home}",
             f"bindir={self.bindir}", *args],
            cwd=DISTRO, env=e, text=True, capture_output=True, timeout=timeout)


class Upgrade(Recipe):

    def test_upgrading_a_home_that_was_never_created_is_refused(self):
        # `init` would happily build one, so a typo in SIANA_HOME would read as a
        # successful upgrade of a fleet that has nothing in it.
        missing = self.at("never-created")
        self.assertRefused(self.just("upgrade", home=missing),
                           "no SIANA home at", "just init")
        self.assertFalse(os.path.exists(missing))


class ContractDrift(Recipe):
    """A field the CLI writes and the home's contract lacks is refused by
    `extra: forbid` as a raw traceback, so it has to be caught before `add`."""

    def test_a_project_contract_missing_a_field_is_named_as_stale(self):
        self.contract("projects")
        with open(self.at("schema-projects.yaml")) as fh:
            text = fh.read()
        # Drop `qa`, the way a home written before that field looks.
        cut = text.split("  qa:")[0] + text.split("      behind every ship task in this project\n")[1]
        with open(self.at("schema-projects.yaml"), "w") as fh:
            fh.write(cut)
        out = self.just("doctor")
        self.assertIn("stale   schema-projects.yaml is missing: qa", out.stderr)
        self.assertIn("refuses to record them", out.stderr)

    def test_a_current_project_contract_is_not_called_stale(self):
        self.contract("projects")
        self.assertNotIn("schema-projects.yaml is missing", self.just("doctor").stderr)


class Doctor(Recipe):

    def test_it_names_what_is_missing_from_an_empty_home(self):
        out = self.just("doctor")
        self.assertIn("missing AGENTS.md", out.stdout)
        self.assertIn("missing orders.md", out.stdout)

    def test_an_empty_store_is_a_zero_and_never_a_fault(self):
        # datafile writes the .jsonl on the first append, so absent-with-a-contract
        # is an empty store. Doctor must not cry wolf about it.
        self.contract("projects", "obligations")
        out = self.just("doctor").stdout
        self.assertIn("projects.jsonl (empty; written on the first project)", out)
        self.assertIn("obligations.jsonl (empty; written on the first promise)", out)

    def test_no_siana_running_is_the_ordinary_state(self):
        self.assertIn("no SIANA running", self.just("doctor").stdout)

    def test_a_session_whose_process_is_gone_is_called_stale(self):
        self.store("session", "SIANA_PID=1", "SIANA_PANE=w1:p1")
        dead = subprocess.Popen(["true"])
        dead.wait()
        with open(self.at("session"), "w") as fh:
            fh.write(f"SIANA_PID={dead.pid}\n")
        out = self.just("doctor")
        self.assertIn("stale   session claims pid", out.stderr)


@unittest.skipUnless(has("pi") and has("tasks") and has("datafile"),
                     "init needs pi, tasks and datafile")
class Init(Recipe):
    """A real install into a throwaway home."""

    def test_a_fresh_home_is_one_the_rest_of_the_distro_can_read(self):
        out = self.just("init")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        for f in ("siana.env", "AGENTS.md", "orders.md", "brief-ship.md",
                  "brief-scout.md", "brief-qa.md", "schema-projects.yaml",
                  "schema-obligations.yaml", "schema-tasks.yaml"):
            self.assertTrue(os.path.exists(self.at(f)), f"init left out {f}")
        for c in ("siana", "siana-dispatch", "siana-brief", "siana-watch", "siana-owe",
                  "siana-publish", "siana-reap"):
            link = os.path.join(self.bindir, c)
            self.assertTrue(os.path.islink(link), f"{c} was not linked")
            # realpath both sides: what matters is that the link lands on this
            # distro's command, not how the path to it happens to be spelled. A
            # checkout under a symlinked prefix spells it two ways.
            self.assertEqual(os.path.realpath(link),
                             os.path.realpath(os.path.join(DISTRO, "bin", c)))
        doctor = self.just("doctor")
        self.assertNotIn("stale", doctor.stderr)
        # The ambient queue is the one part of an install that depends on something
        # outside the distro: `init` writes `.pi/settings.json` only when the tasks
        # pi package sits beside the checkout, and says so when it does not. From a
        # linked worktree that sibling is absent, so a blanket "nothing is missing"
        # asserts on where this suite was checked out rather than on the install -
        # green in the main tree and red in every minion's. Demanded when the package
        # is there, tolerated by name when it is not, and nothing else ever tolerated.
        missing = [line.strip() for line in doctor.stdout.splitlines()
                   if "missing " in line]
        if os.path.isdir(os.path.join(DISTRO, os.pardir, "tasks", "pi-agent-tasks")):
            self.assertEqual(missing, [])
        else:
            self.assertEqual(missing, ["missing .pi/settings.json"])

    def test_it_is_idempotent_and_never_clobbers_an_evolved_file(self):
        # SIANA can evolve its own instructions, so a diverged home copy is the
        # captain's work and not drift to be cleaned up.
        self.assertEqual(self.just("init").returncode, 0)
        with open(self.at("AGENTS.md"), "a") as fh:
            fh.write("\nThe captain prefers reports at noon.\n")
        again = self.just("init")
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertIn("kept     ", again.stdout)
        with open(self.at("AGENTS.md")) as fh:
            self.assertIn("reports at noon", fh.read())

    def test_uninstall_removes_the_links_and_leaves_the_home_alone(self):
        self.assertEqual(self.just("init").returncode, 0)
        out = self.just("uninstall")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.bindir, "siana")))
        self.assertTrue(os.path.exists(self.at("tasks.jsonl")) or
                        os.path.exists(self.at("schema-tasks.yaml")))
        self.assertIn("kept    ", out.stdout)

    def test_uninstall_refuses_to_remove_something_it_did_not_install(self):
        os.makedirs(self.bindir, exist_ok=True)
        with open(os.path.join(self.bindir, "siana"), "w") as fh:
            fh.write("#!/bin/sh\n")
        self.assertRefused(self.just("uninstall"), "refusing to remove")
        self.assertTrue(os.path.exists(os.path.join(self.bindir, "siana")))

    def test_upgrade_preserves_an_evolved_file_with_the_diff_beside_it(self):
        self.assertEqual(self.just("init").returncode, 0)
        with open(self.at("orders.md"), "w") as fh:
            fh.write("# My own orders\n")
        out = self.just("upgrade")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("upgraded ", out.stdout)
        backups = []
        for root, _, files in os.walk(self.at("upgrade")):
            backups += [os.path.join(root, f) for f in files]
        kept = [b for b in backups if b.endswith("orders.md")]
        diffs = [b for b in backups if b.endswith("orders.md.diff")]
        self.assertTrue(kept, f"the captain's copy was not preserved: {backups}")
        self.assertTrue(diffs, f"no diff was left beside it: {backups}")
        with open(kept[0]) as fh:
            self.assertIn("My own orders", fh.read())
        with open(self.at("orders.md")) as fh:
            self.assertIn("Standing orders", fh.read())


if __name__ == "__main__":
    unittest.main()
