"""siana-retire: tearing down a minion's tree without tearing down its work.

Every test here runs against a real git repository and a real queue. Nothing about
this command is decidable from a stub: what it refuses is what git would report about
a working tree that exists, and the one hazard it is written to close - `git worktree
remove` deleting ignored files without a word - is a behaviour of git itself, so a
suite that faked git would only ever confirm what it already believed.

The successes matter as much as the refusals. A command that refuses everything is
safe and useless, and the two paths SIANA actually needs, re-dispatch recovery and
retiring finished work, both end in a removal.
"""

import os
import shutil
import unittest

from helpers import HomeTest, script

r = script("siana-retire")


class SamePath(unittest.TestCase):
    """git reports a worktree by its real path while herdr recorded the path it was
    created under. On macOS anything below /tmp or /var is spelled two ways, so a
    comparison as typed reads a healthy pair as the one state this refuses on."""

    def test_a_path_equals_itself(self):
        self.assertTrue(r.same_path("/usr/bin", "/usr/bin"))

    def test_two_spellings_of_one_directory_are_the_same_place(self):
        self.assertTrue(r.same_path("/usr/bin", "/usr/bin/../bin"))

    def test_different_directories_stay_different(self):
        self.assertFalse(r.same_path("/usr/bin", "/usr/lib"))


class Retire(HomeTest):
    """A project, a repository, and a task dispatched into its own worktree."""

    def setUp(self):
        super().setUp()
        self.contract("projects")
        self.queue()
        self.repo = self.at("repo")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main", ".")
        self.git("config", "user.email", "minion@example.com")
        self.git("config", "user.name", "minion")
        # `litter/` is ignored from the base commit, so an ignored path in a worktree
        # is a real ignore rule rather than something a test wrote into .git/info.
        self.write(self.repo, ".gitignore", "litter/\n")
        self.write(self.repo, "a.txt", "base\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.project("proj", path=self.repo)

    # -- fixtures ---------------------------------------------------------------

    def git(self, *args, cwd=None):
        out = self.run_cmd(["git", "-C", cwd or self.repo, *args])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out.stdout

    def write(self, directory, name, text):
        path = os.path.join(directory, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def tasks(self, *args):
        return self.assertAccepted(
            self.run_cmd(["tasks", "--file", self.at("tasks.jsonl"), *args]))

    def dispatched(self, task_id="make-thing", project="proj", cwd=None):
        """A task in the state a real dispatch leaves it in: `doing`, its own
        worktree on `siana/<id>`, and the queue pointing at that tree."""
        self.tasks("add", task_id.replace("-", " "), "--verify", "true",
                   *(["--project", project] if project else []))
        worktree = self.at("wt", task_id)
        self.git("worktree", "add", "-q", "-b", f"siana/{task_id}", worktree)
        self.tasks("start", task_id, "--owner", "claude@w1:p1",
                   "--cwd", cwd or worktree)
        return worktree

    def commit_in(self, worktree, name="b.txt", text="work\n"):
        self.write(worktree, name, text)
        self.git("add", "-A", cwd=worktree)
        self.git("commit", "-qm", "work", cwd=worktree)
        return self.git("rev-parse", "HEAD", cwd=worktree).strip()

    def finished(self, task_id="make-thing", **kw):
        """A task whose minion has come back. The status gate is the first thing
        `siana-retire` checks, so every test that is about something else has to
        get past it first."""
        worktree = self.dispatched(task_id, **kw)
        self.tasks("done", task_id, "--reason", "done")
        return worktree

    def retire(self, task_id="make-thing"):
        return self.run_bin("siana-retire", task_id)

    def assertBranch(self, task_id="make-thing"):
        """The branch survived. Every removal path asserts this: a worktree torn
        down is recoverable, a branch deleted with it is not."""
        out = self.run_cmd(["git", "-C", self.repo, "rev-parse", "--verify",
                            f"refs/heads/siana/{task_id}"])
        self.assertEqual(out.returncode, 0, f"siana/{task_id} is gone:\n{out.stderr}")
        return out.stdout.strip()


class Resolving(Retire):
    """Where the worktree is comes from the task and the registry, never a path."""

    def test_no_task_store_is_a_stop(self):
        # An empty queue has no tasks.jsonl at all, so this is also what a retire
        # against a home that was never used looks like.
        self.assertRefused(self.retire(), "no task store", "just init")

    def test_an_unknown_task_points_at_the_queue(self):
        self.finished()
        self.assertRefused(self.retire("ghost"), "no such task: ghost", "list")

    def test_a_task_with_no_project_has_nowhere_to_look(self):
        self.finished(project=None)
        self.assertRefused(self.retire(), "carries no project", "nothing to remove")

    def test_an_unknown_handle_names_the_ones_that_exist(self):
        self.finished(project="other")
        self.assertRefused(self.retire(), "unknown project: other", "proj")

    def test_no_registry_is_a_stop_and_not_an_empty_one(self):
        self.finished()
        os.remove(self.at("schema-projects.yaml"))
        self.assertRefused(self.retire(), "no project registry", "just init")

    def test_a_project_path_that_is_gone_is_refused(self):
        self.finished()
        shutil.rmtree(self.repo)
        self.assertRefused(self.retire(), "not a directory")

    def test_a_project_without_isolation_never_had_a_worktree(self):
        # Its minion worked in the captain's own checkout, and nothing in this
        # fleet tears that down.
        self.finished()
        self.project("proj", path=self.repo, worktree=False)
        self.assertRefused(self.retire(), "worktree isolation off",
                           "the captain's", "never torn down")

    def test_a_project_that_is_not_a_git_repository_is_refused(self):
        self.finished()
        shutil.rmtree(os.path.join(self.repo, ".git"))
        self.assertRefused(self.retire(), "not a git repository")


class Preconditions(Retire):
    """The states where there is nothing to remove, or nothing this can read."""

    def test_a_task_still_held_by_a_minion_is_refused(self):
        # The tree is somebody's desk. Reclaiming it is `tasks reset`, and that
        # stays separate because it is the moment SIANA is meant to look.
        worktree = self.dispatched()
        text = self.assertRefused(self.retire(), "still held by", "claude@w1:p1",
                                  "reset make-thing")
        self.assertIn("--check", text)
        self.assertTrue(os.path.isdir(worktree))

    def test_a_task_with_no_branch_says_so_rather_than_hunting_for_a_tree(self):
        self.tasks("add", "make thing", "--verify", "true", "--project", "proj")
        self.tasks("start", "make-thing", "--owner", "claude@w1:p1", "--cwd", self.repo)
        self.tasks("done", "make-thing", "--reason", "done")
        self.assertRefused(self.retire(), "has no branch siana/make-thing",
                           "landed and deleted")

    def test_a_branch_with_no_worktree_reads_as_already_retired(self):
        worktree = self.finished()
        self.git("worktree", "remove", worktree)
        self.assertRefused(self.retire(), "no worktree is checked out",
                           "already been retired")
        self.assertBranch()

    def test_a_detached_worktree_is_found_rather_than_called_retired(self):
        # Any minion that checks out a commit to read it leaves the tree like this.
        # The lookup is by branch, so it came back empty and the command answered
        # "it has already been retired" about a tree still on disk with work in it
        # (QA, 2026-08-28). Acting on that walks the next dispatch into its own
        # "a worktree already exists" refusal with no idea why.
        worktree = self.finished()
        loose = self.write(worktree, "notes.md", "what I found\n")
        self.git("checkout", "-q", "--detach", cwd=worktree)
        text = self.assertRefused(self.retire(), "detached at",
                                  "nothing has been removed")
        self.assertIn(worktree, text)
        self.assertNotIn("already been retired", text)
        self.assertTrue(os.path.isfile(loose))
        self.assertBranch()

    def test_a_worktree_moved_onto_another_branch_is_reported_as_it_is(self):
        # The same blind spot, reached the other way. Neither is this command's to
        # undo: moving a head back is a decision, and it is not mechanics.
        worktree = self.finished()
        self.git("checkout", "-q", "-b", "sidetrack", cwd=worktree)
        text = self.assertRefused(self.retire(),
                                  "checked out on refs/heads/sidetrack")
        self.assertNotIn("already been retired", text)
        self.assertTrue(os.path.isdir(worktree))
        self.assertBranch()

    def test_the_projects_own_checkout_is_never_removed(self):
        # A minion branch checked out in the captain's tree is a state to report.
        # Removing the main worktree is not a cleanup, it is deleting the project.
        worktree = self.finished()
        self.git("worktree", "remove", worktree)
        self.git("checkout", "-q", "siana/make-thing")
        self.assertRefused(self.retire(), "is checked out in", "captain's own")
        self.assertTrue(os.path.isdir(self.repo))

    def test_a_registration_whose_tree_is_gone_asks_for_a_prune(self):
        # Removing the directory by hand leaves git still listing it. Pruning
        # touches no working tree, so it is the safe repair to name.
        worktree = self.finished()
        shutil.rmtree(worktree)
        self.assertRefused(self.retire(), "which is not there", "worktree prune")

    def test_a_queue_and_a_git_that_disagree_is_ambiguous_and_never_reconciled(self):
        # One of the two describes a tree this command was not asked about, and
        # from here there is no way to say which.
        self.finished(cwd=self.repo)
        text = self.assertRefused(self.retire(), "records its work in",
                                  "look at both before anything is removed")
        self.assertIn(self.repo, text)
        self.assertTrue(os.path.isdir(self.at("wt", "make-thing")))

    def test_a_worktree_recorded_through_a_symlink_is_the_same_tree(self):
        # git answers with the real path and herdr recorded the one it created the
        # tree under. Reading that difference as ambiguity would refuse every
        # healthy retirement on a machine where /tmp is a link.
        worktree = self.dispatched()
        link = self.at("linked-tree")
        os.symlink(worktree, link)
        # Retarget the queue at the same tree, spelled the other way.
        self.tasks("reset", "make-thing", "--reason", "respell")
        self.tasks("start", "make-thing", "--owner", "claude@w1:p1", "--cwd", link)
        self.tasks("done", "make-thing", "--reason", "done")
        self.assertAccepted(self.retire())


class WorkThatWouldBeLost(Retire):
    """Everything in that tree with no second copy."""

    def test_a_modified_tracked_file_is_refused_and_named(self):
        worktree = self.finished()
        self.write(worktree, "a.txt", "edited\n")
        text = self.assertRefused(self.retire(), "still holds work",
                                  "tracked file(s) modified", "a.txt")
        self.assertIn("nothing has been removed", text)
        self.assertTrue(os.path.isdir(worktree))

    def test_an_untracked_file_is_refused_and_named(self):
        worktree = self.finished()
        self.write(worktree, "notes.md", "what I found\n")
        self.assertRefused(self.retire(), "untracked file(s)", "notes.md")
        self.assertTrue(os.path.isdir(worktree))

    def test_an_ignored_path_is_refused_and_named(self):
        # This is the whole reason the check is here rather than left to git:
        # `git worktree remove` deletes ignored paths and exits 0 (verified
        # 2026-08-28), and a .env and a build directory look identical to git.
        worktree = self.finished()
        self.write(worktree, "litter/secrets.env", "TOKEN=1\n")
        text = self.assertRefused(self.retire(), "ignored path(s)", "litter/")
        self.assertIn("without saying so", text)
        self.assertTrue(os.path.isdir(worktree))
        self.assertTrue(os.path.isfile(os.path.join(worktree, "litter/secrets.env")))

    def test_a_repository_setting_cannot_blind_the_check(self):
        # `status.showUntrackedFiles=no` is an ordinary large-repo performance
        # setting, and `git status` honours it by returning an empty untracked *and*
        # an empty ignored bucket. Under it this check saw nothing, removed the tree
        # and printed a success (QA, 2026-08-28), which is worse than not existing:
        # `git worktree remove` is blinded by the same setting, so the command was
        # offering an assurance it no longer had any way to make.
        worktree = self.finished()
        self.git("config", "status.showUntrackedFiles", "no")
        secret = self.write(worktree, "notes-nobody-committed.md", "SECRET=1\n")
        self.write(worktree, "litter/secrets.env", "TOKEN=1\n")
        self.assertRefused(self.retire(), "untracked file(s)",
                           "notes-nobody-committed.md", "ignored path(s)")
        self.assertTrue(os.path.isfile(secret))
        self.assertTrue(os.path.isdir(worktree))

    def test_a_users_own_gitconfig_cannot_blind_the_check_either(self):
        # The same setting one level up. A safety check that depends on how the
        # captain configured git is a check that is off on somebody else's machine,
        # so the flag has to override every config source rather than the repository
        # being kept clean of it.
        worktree = self.finished()
        secret = self.write(worktree, "notes-nobody-committed.md", "SECRET=1\n")
        config = self.at("global.gitconfig")
        with open(config, "w") as fh:
            fh.write("[status]\n\tshowUntrackedFiles = no\n")
        self.assertRefused(
            self.run_bin("siana-retire", "make-thing",
                         env={"GIT_CONFIG_GLOBAL": config}),
            "untracked file(s)", "notes-nobody-committed.md")
        self.assertTrue(os.path.isfile(secret))
        self.assertTrue(os.path.isdir(worktree))

    def test_every_kind_of_loss_is_reported_at_once(self):
        # Stopping at the first would make emptying the tree a guessing game.
        worktree = self.finished()
        self.write(worktree, "a.txt", "edited\n")
        self.write(worktree, "notes.md", "what I found\n")
        self.write(worktree, "litter/secrets.env", "TOKEN=1\n")
        self.assertRefused(self.retire(), "tracked file(s) modified",
                           "untracked file(s)", "ignored path(s)")

    def test_a_clean_tree_is_removed(self):
        worktree = self.finished()
        text = self.assertAccepted(self.retire())
        self.assertIn(f"retired  {os.path.realpath(worktree)}", text)
        self.assertFalse(os.path.exists(worktree))
        self.assertBranch()


class TheExternalAnchorRule(Retire):
    """A `done` task's worktree goes only when something outside the fleet's own
    branches holds a copy of what it built. Every `siana/*` branch is one `git
    branch -D` from gone, so none of them is somewhere work has been left."""

    def test_finished_work_anchored_nowhere_is_refused(self):
        worktree = self.dispatched()
        self.commit_in(worktree)
        self.tasks("done", "make-thing", "--reason", "built it")
        text = self.assertRefused(self.retire(),
                                  "reachable from no ref outside the fleet")
        self.assertIn("land it or publish it", text)
        self.assertTrue(os.path.isdir(worktree))

    def test_the_qa_branch_cut_from_the_ship_branch_is_not_an_anchor(self):
        # The live false positive, reproduced exactly (QA, 2026-08-28). SIANA
        # dispatches QA with the ship branch as its base, so `siana/qa-<id>` is
        # created at the ship head. Counting any `refs/heads` ref made every ship
        # branch read as landed from the moment its own review started - which is
        # to say from before it had landed anywhere at all.
        worktree = self.dispatched()
        self.commit_in(worktree)
        self.tasks("done", "make-thing", "--reason", "built it")
        self.git("worktree", "add", "-q", "-b", "siana/qa-make-thing",
                 self.at("wt", "qa-make-thing"), "siana/make-thing")
        self.assertRefused(self.retire(), "reachable from no ref outside the fleet",
                           "QA is cut from the ship")
        self.assertTrue(os.path.isdir(worktree))

    def test_a_branch_outside_the_fleet_namespace_is_an_anchor(self):
        # What is excluded is `refs/heads/siana/*` and not `refs/heads`. A branch
        # cut deliberately to hold this work is a second copy, and the rule has to
        # be able to say so, or a merge would be the only thing that ever satisfied
        # it and the captain would have no way to keep work without landing it.
        worktree = self.dispatched()
        head = self.commit_in(worktree)
        self.tasks("done", "make-thing", "--reason", "built it")
        self.git("branch", "keep/make-thing", "siana/make-thing")
        self.assertAccepted(self.retire())
        self.assertFalse(os.path.exists(worktree))
        self.assertEqual(self.assertBranch(), head)

    def test_work_merged_into_the_default_branch_may_be_retired(self):
        worktree = self.dispatched()
        head = self.commit_in(worktree)
        self.tasks("done", "make-thing", "--reason", "built it")
        self.git("merge", "-q", "--no-ff", "-m", "land it", "siana/make-thing")
        self.assertAccepted(self.retire())
        self.assertFalse(os.path.exists(worktree))
        self.assertEqual(self.assertBranch(), head)

    def test_work_pushed_to_a_remote_may_be_retired_under_its_own_name(self):
        # Merged is one way this is met, never the only one: a gated ship task comes
        # back published and unmerged, and the copy on the remote is a copy. The
        # exclusion is of *local* `siana/*` branches only, so a remote-tracking ref
        # spelled with the same name is still an anchor, which is the whole
        # difference between "landed" and "exists somewhere else".
        worktree = self.dispatched()
        head = self.commit_in(worktree)
        self.tasks("done", "make-thing", "--reason", "built it")
        self.git("update-ref", "refs/remotes/origin/siana/make-thing", head)
        self.assertAccepted(self.retire())
        self.assertFalse(os.path.exists(worktree))

    def test_a_stash_is_not_an_anchor(self):
        # A stash entry is parented on the head it was taken from, so counting
        # `refs/stash` as another ref would read every stashed branch as anchored on
        # the strength of something its own minion was in the middle of.
        worktree = self.dispatched()
        self.commit_in(worktree)
        self.write(worktree, "b.txt", "second thoughts\n")
        self.git("stash", "push", "-q", "-m", "mid-thought", cwd=worktree)
        self.tasks("done", "make-thing", "--reason", "built it")
        self.assertRefused(self.retire(), "reachable from no ref outside the fleet")
        self.assertTrue(os.path.isdir(worktree))

    def test_work_that_lands_nothing_is_retired_without_argument(self):
        # Scout and QA branches sit at whatever they were cut from, so the base
        # anchors them and neither needs landing to be tidied away.
        worktree = self.finished()
        self.assertAccepted(self.retire())
        self.assertFalse(os.path.exists(worktree))
        self.assertBranch()

    def test_the_rule_does_not_apply_to_work_going_back_out(self):
        # For a task returning to the queue the branch is not a last copy, it is
        # the delivery mechanism: the next minion's worktree is cut from it and
        # starts at its head. Refusing here would make re-dispatch impossible for
        # exactly the minions that got furthest.
        worktree = self.dispatched()
        head = self.commit_in(worktree)
        self.tasks("block", "make-thing", "--reason", "needs a decision")
        self.assertAccepted(self.retire())
        self.assertFalse(os.path.exists(worktree))
        self.assertEqual(self.assertBranch(), head)

    def test_a_reset_task_is_retired_with_its_commits_in_front_of_the_next(self):
        # The whole reset-and-re-dispatch path, end to end: what the last minion
        # committed is on the branch a new worktree would be cut from.
        worktree = self.dispatched()
        head = self.commit_in(worktree, "recovered.txt", "half the fix\n")
        self.tasks("reset", "make-thing", "--reason", "its minion is gone")
        self.assertAccepted(self.retire())
        self.assertFalse(os.path.exists(worktree))
        self.assertEqual(self.assertBranch(), head)
        again = self.at("wt", "again")
        self.git("worktree", "add", "-q", again, "siana/make-thing")
        self.assertTrue(os.path.isfile(os.path.join(again, "recovered.txt")))

    def test_a_dirty_tree_is_still_refused_on_the_re_dispatch_path(self):
        # The branch carries commits, never uncommitted work, so the one thing
        # reset does not make safe is the thing that was never committed.
        worktree = self.dispatched()
        self.write(worktree, "notes.md", "half a thought\n")
        self.tasks("reset", "make-thing", "--reason", "its minion is gone")
        self.assertRefused(self.retire(), "untracked file(s)", "notes.md")
        self.assertTrue(os.path.isdir(worktree))


class WhatItSays(Retire):
    """A removal reports what it kept, because what it kept is the reassurance."""

    def test_it_names_the_branch_it_left_behind(self):
        self.finished()
        text = self.assertAccepted(self.retire())
        self.assertIn("kept   siana/make-thing", text)

    def test_it_names_the_herdr_workspace_it_did_not_close(self):
        # Closing it kills that agent, which is judgment. Saying nothing would
        # leave a pane whose directory has gone with no sign of why.
        self.finished()
        text = self.assertAccepted(self.retire())
        self.assertIn("claude@w1:p1", text)
        self.assertIn("yours to decide", text)


if __name__ == "__main__":
    unittest.main()
