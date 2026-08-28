"""What `siana-reap` refuses to delete.

A branch is the deliverable in this fleet, so every test here is a way work could be
deleted that had not landed, or had not finished. The one test about actually
removing something exists to prove the others are not passing by doing nothing.
"""

import os
import subprocess
import unittest

from helpers import HomeTest, script

reap = script("siana-reap")


class Reap(HomeTest):
    def setUp(self):
        super().setUp()
        self.contract("projects")
        self.repo = os.path.join(self.home, "repo")
        os.makedirs(self.repo)
        self.git("init", "-b", "main")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "t")
        self.git("commit", "--allow-empty", "-m", "base")

    def git(self, *argv, cwd=None):
        out = subprocess.run(["git", "-C", cwd or self.repo, *argv],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr or out.stdout)
        return out.stdout

    def branch(self, name, landed):
        """A siana branch, either already contained in main or not."""
        self.git("checkout", "-b", name)
        self.git("commit", "--allow-empty", "-m", f"work on {name}")
        self.git("checkout", "main")
        if landed:
            self.git("merge", "--no-ff", "-m", f"land {name}", name)

    def task(self, tid, status, base=None):
        rec = {"id": tid, "title": tid, "status": status, "verify": "true",
               "verify_kind": "cmd", "deps": [], "context": [], "project": "demo",
               "updated": "2026-08-28T10:00:00Z"}
        if base:
            rec["base"] = base
        self.store("tasks.jsonl", rec)

    def test_unlanded_work_is_never_touched(self):
        self.branch("siana/in-flight", landed=False)
        self.project("demo", path=self.repo, target="main")
        text = self.assertAccepted(self.run_bin("siana-reap", "demo"))
        self.assertIn("siana/in-flight  kept: not landed", text)
        self.assertIn("0 reapable", text)

    def test_landed_work_is_reapable_but_not_reaped_without_yes(self):
        # The default has to be inert. A reaper that deletes on its first run is one
        # nobody can safely try.
        self.branch("siana/landed", landed=True)
        self.project("demo", path=self.repo, target="main")
        text = self.assertAccepted(self.run_bin("siana-reap", "demo"))
        self.assertIn("would reap (contained)", text)
        self.assertIn("nothing was removed", text)
        self.assertIn("siana/landed", self.git("branch", "--list", "siana/landed"))

    def test_yes_removes_the_landed_branch_only(self):
        self.branch("siana/landed", landed=True)
        self.branch("siana/in-flight", landed=False)
        self.project("demo", path=self.repo, target="main")
        self.assertAccepted(self.run_bin("siana-reap", "demo", "--yes"))
        self.assertEqual(self.git("branch", "--list", "siana/landed").strip(), "")
        self.assertIn("siana/in-flight", self.git("branch", "--list", "siana/in-flight"))

    def test_a_branch_a_minion_is_working_on_survives_landing(self):
        # Landed says the commits are in main. It does not say the minion has
        # stopped, and its worktree is where uncommitted work would be.
        self.branch("siana/live", landed=True)
        self.task("live", "doing")
        self.project("demo", path=self.repo, target="main")
        text = self.assertAccepted(self.run_bin("siana-reap", "demo", "--yes"))
        self.assertIn("kept: a minion is working on it", text)
        self.assertIn("siana/live", self.git("branch", "--list", "siana/live"))

    def test_the_base_under_a_live_minion_survives_too(self):
        self.branch("siana/ship", landed=True)
        self.task("fix", "doing", base="siana/ship")
        self.project("demo", path=self.repo, target="main")
        text = self.assertAccepted(self.run_bin("siana-reap", "demo", "--yes"))
        self.assertIn("siana/ship  kept: a minion is working on it", text)

    def test_a_dirty_worktree_is_kept(self):
        self.branch("siana/dirty", landed=True)
        checkout = os.path.join(self.home, "wt")
        self.git("worktree", "add", checkout, "siana/dirty")
        with open(os.path.join(checkout, "scratch.txt"), "w") as fh:
            fh.write("work nobody committed\n")
        self.project("demo", path=self.repo, target="main")
        text = self.assertAccepted(self.run_bin("siana-reap", "demo", "--yes"))
        self.assertIn("uncommitted changes", text)
        self.assertIn("siana/dirty", self.git("branch", "--list", "siana/dirty"))

    def test_a_clean_worktree_goes_with_its_branch(self):
        self.branch("siana/clean", landed=True)
        checkout = os.path.join(self.home, "wt")
        self.git("worktree", "add", checkout, "siana/clean")
        self.project("demo", path=self.repo, target="main")
        self.assertAccepted(self.run_bin("siana-reap", "demo", "--yes"))
        self.assertFalse(os.path.isdir(checkout))
        self.assertEqual(self.git("branch", "--list", "siana/clean").strip(), "")

    def test_a_project_that_never_publishes_has_nothing_to_reap(self):
        self.branch("siana/landed", landed=True)
        self.project("demo", path=self.repo)
        out = self.run_bin("siana-reap", "demo")
        self.assertRefused(out, "has no `target`")

    def test_unknown_project(self):
        out = self.run_bin("siana-reap", "nope")
        self.assertRefused(out, "unknown project: nope")

    def test_worktrees_maps_every_checkout_to_its_branch(self):
        # `--porcelain` is the only stable shape; the human listing is columns that
        # move when a path gets longer. Built here rather than read off the distro,
        # which is on whatever branch the person running this happens to be on.
        self.branch("siana/one", landed=False)
        checkout = os.path.join(self.home, "wt")
        self.git("worktree", "add", checkout, "siana/one")
        found = reap.worktrees(self.repo)
        # realpath both sides: git reports the resolved path, and the test's own
        # temp dir sits under a symlinked /var on macOS.
        self.assertEqual(found.get("siana/one"), os.path.realpath(checkout))
        self.assertEqual(found.get("main"), os.path.realpath(self.repo))

    def test_a_machine_without_herdr_or_a_forge_cli_still_reaps(self):
        """CI found this: `subprocess.run` raises rather than returning non-zero when
        the binary is absent, so a clean runner crashed the whole sweep. Absent has
        to read as "no herdr workspace" and "could not tell", never as a traceback."""
        self.branch("siana/landed", landed=True)
        self.project("demo", path=self.repo, target="main")
        text = self.assertAccepted(self.run_bin("siana-reap", "demo", "--yes",
                                                env={"PATH": "/usr/bin:/bin"}))
        self.assertIn("reaped (contained)", text)
        self.assertEqual(self.git("branch", "--list", "siana/landed").strip(), "")

    def test_a_repo_with_no_siana_branches(self):
        self.project("demo", path=self.repo, target="main")
        text = self.assertAccepted(self.run_bin("siana-reap", "demo"))
        self.assertIn("no siana/ branches", text)


if __name__ == "__main__":
    unittest.main()
