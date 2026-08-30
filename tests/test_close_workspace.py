"""siana-close-workspace: closing one workspace, and refusing every other one.

This is the most destructive command in the fleet by consequence rather than by
scale. Closing a workspace kills the agent in it, and closing the wrong one - a
project's source workspace - closes every linked-worktree workspace under it at once,
which is that project's whole fleet. So most of what is here is refusals, and the
ones that matter most are the ones where the command has been handed something that
looks right.

Herdr is scripted, for the reason `tests/fake_herdr.py` gives: the answers that decide
this command are herdr being wrong, herdr being silent, and herdr describing a
workspace that is not the one asked after, and a live server cannot be made to give
those on cue. Everything else is real. The repository is a real git with real
worktrees, the queue is a real `tasks`, the registry a real `datafile`, and the
retirement the ordering depends on is a real `siana-retire`, because the whole claim
of the ordering is that the two commands compose.
"""

import json
import os
import shutil
import unittest

from fake_herdr import CLOSE, FakeHerdr, HerdrError
from helpers import HomeTest, script

c = script("siana-close-workspace")

# herdr's own refusal for a workspace id nobody holds. It is an answer about that
# workspace, unlike silence, and the command has to tell the two apart.
NO_WORKSPACE = HerdrError("workspace_not_found", "workspace w9 not found")


def listing(*ws):
    return {"type": "workspace_list", "workspaces": list(ws)}


class Owner(unittest.TestCase):
    """The one place a workspace id is derived. Every other way of finding a
    workspace - a label, a number, an agent name, focus - finds one that exists and
    is not this task's, so the derivation is a pure function under a direct test."""

    def test_a_dispatched_owner_names_its_workspace(self):
        self.assertEqual(c.workspace_of("claude@w8P:p2"), "w8P")

    def test_the_pane_half_is_not_the_workspace(self):
        # `w8P:p2` and `w8P:p9` are two panes of one workspace, and closing is a
        # workspace operation: both have to resolve to the same thing.
        self.assertEqual(c.workspace_of("claude@w8P:p9"), "w8P")

    def test_an_owner_that_names_no_pane_resolves_to_nothing(self):
        for owner in ("", "claude", "claude@", "@w1:p1", "claude@w1",
                      "claude@:p1", "claude@w1:", "claude@w1:p1:extra",
                      "claude@w 1:p1", "claude@w1/p1"):
            self.assertIsNone(c.workspace_of(owner), owner)

    def test_a_label_is_never_an_owner(self):
        # Herdr does not enforce unique workspace labels, so a label reaching this
        # would resolve to whichever workspace was listed first.
        self.assertIsNone(c.workspace_of("make-thing"))

    def test_none_is_not_a_workspace(self):
        self.assertIsNone(c.workspace_of(None))


class SamePath(unittest.TestCase):
    """The recorded tree is gone by the time anything gets this far, so the
    comparison that decides identity is one between a path herdr recorded and a path
    that no longer exists."""

    def test_a_missing_path_still_compares_as_itself(self):
        self.assertTrue(c.same_path("/nowhere/at/all", "/nowhere/./at/all"))

    def test_two_missing_paths_stay_different(self):
        self.assertFalse(c.same_path("/nowhere/a", "/nowhere/b"))


class Close(HomeTest):
    """A project, a repository, a queue, and a herdr that answers on cue."""

    WS = "w9"
    OWNER = "claude@w9:p2"

    def setUp(self):
        super().setUp()
        self.herdr = FakeHerdr().start()
        self.addCleanup(self.herdr.stop)
        self.contract("projects")
        self.queue()
        self.repo = self.at("repo")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main", ".")
        self.git("config", "user.email", "minion@example.com")
        self.git("config", "user.name", "minion")
        self.write(self.repo, "a.txt", "base\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.project("proj", path=self.repo)
        self.git_dir = self.git("rev-parse", "--path-format=absolute",
                                "--git-common-dir").strip()

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

    def dispatched(self, task_id="make-thing", project="proj", owner=None,
                   status="done"):
        """A task in the state a real dispatch left it in, taken to `status`.

        The worktree really exists at this point. Every test that is about a closed
        workspace has to retire it first, because that is the ordering under test.
        """
        self.tasks("add", task_id.replace("-", " "), "--verify", "true",
                   *(["--project", project] if project else []))
        worktree = self.at("wt", task_id)
        self.git("worktree", "add", "-q", "-b", f"siana/{task_id}", worktree)
        self.tasks("start", task_id, "--owner", owner or self.OWNER,
                   "--cwd", worktree)
        if status == "done":
            self.tasks("done", task_id, "--reason", "built it")
        elif status == "blocked":
            self.tasks("block", task_id, "--reason", "stuck")
        elif status == "todo":
            self.tasks("reset", task_id, "--reason", "reclaimed")
        return worktree

    def retired(self, task_id="make-thing", **kw):
        """A finished task whose worktree has been retired by the real command.

        Through `siana-retire` rather than `git worktree remove`, because what is
        being set up is the exact state the ordering depends on, and a fixture that
        produced it another way would be asserting against a state the fleet does
        not make.
        """
        worktree = self.dispatched(task_id, **kw)
        self.assertAccepted(self.run_bin("siana-retire", task_id))
        self.assertFalse(os.path.exists(worktree))
        return worktree

    def workspace(self, checkout, *, wid=None, linked=True, root=None, key=None,
                  status="idle", focused=False, worktree=True, label="make-thing"):
        """One entry of `workspace.list`, in the shape herdr really answers with
        (verified 2026-08-30 against herdr 0.8.0, protocol 19)."""
        w = {"workspace_id": wid or self.WS, "number": 3, "label": label,
             "focused": focused, "pane_count": 1, "tab_count": 1,
             "active_tab_id": f"{wid or self.WS}:t1", "agent_status": status}
        if worktree:
            w["worktree"] = {"repo_key": key if key is not None else self.git_dir,
                             "repo_name": "repo",
                             "repo_root": root if root is not None else self.repo,
                             "checkout_path": checkout,
                             "is_linked_worktree": linked}
            if linked is None:
                del w["worktree"]["is_linked_worktree"]
        return w

    def answers(self, *listings):
        self.herdr.reply("workspace.list", *listings)

    def close(self, task_id="make-thing", socket=None):
        return self.run_bin("siana-close-workspace", task_id,
                            env={"HERDR_SOCKET_PATH": socket or self.herdr.path})

    def assertClosedNothing(self):
        self.assertEqual(self.herdr.calls_to("workspace.close"), [])
        # The neighbouring destructive calls, which this command has no business
        # making at all: a refusal that reached for one of them instead would be
        # invisible to an assertion about `workspace.close` alone.
        for method in ("pane.close", "worktree.remove", "workspace.create"):
            self.assertEqual(self.herdr.calls_to(method), [], method)


class Resolving(Close):
    """Which workspace, decided by records and never by the caller."""

    def test_no_task_store_is_a_stop(self):
        # A queue with no writes yet has a contract and no store file, which is also
        # what a home that was initialised and never used looks like.
        self.assertFalse(os.path.exists(self.at("tasks.jsonl")))
        self.assertRefused(self.close(), "no task store", "just init")
        self.assertClosedNothing()

    def test_an_unknown_task_points_at_the_queue(self):
        self.retired()
        self.assertRefused(self.close("ghost"), "no such task: ghost", "list")
        self.assertClosedNothing()

    def test_a_task_with_no_project_has_no_repository_to_check_against(self):
        # Appended raw, because retiring the tree first needs the project the test
        # is about removing. `tasks` will not write this shape and nothing in the
        # fleet does; a hand-edited record is exactly where it comes from.
        self.retired()
        self.store("tasks.jsonl", {**self.task(), "project": None})
        self.assertRefused(self.close(), "carries no project")
        self.assertClosedNothing()

    def test_an_unknown_handle_names_the_ones_that_exist(self):
        self.retired()
        self.store("tasks.jsonl",
                   {**self.task(), "project": "other"})
        self.assertRefused(self.close(), "unknown project: other", "proj")
        self.assertClosedNothing()

    def test_no_registry_is_a_stop_and_not_an_empty_one(self):
        self.retired()
        os.remove(self.at("schema-projects.yaml"))
        self.assertRefused(self.close(), "no project registry", "just init")
        self.assertClosedNothing()

    def test_a_project_without_isolation_never_had_a_worktree_workspace(self):
        self.retired()
        self.project("proj", path=self.repo, worktree=False)
        self.assertRefused(self.close(), "worktree isolation off", "the captain's")
        self.assertClosedNothing()

    def test_an_owner_naming_no_pane_is_refused_rather_than_searched_for(self):
        # The whole design: with no pane there is no workspace, and the alternatives
        # - the label, the number, the agent name - are exactly what must not be
        # reached for instead.
        self.retired(owner="claude@nothing")
        text = self.assertRefused(self.close(), "no owner naming a pane")
        for never in ("label", "number", "agent name", "focused"):
            self.assertIn(never, text)
        self.assertClosedNothing()

    def test_a_task_with_no_recorded_worktree_has_nothing_to_match(self):
        self.retired()
        self.store("tasks.jsonl", {**self.task(), "cwd": None})
        self.assertRefused(self.close(), "records no worktree")
        self.assertClosedNothing()

    def task(self, task_id="make-thing"):
        """The task record as the queue currently holds it, for tests that need to
        append a line the queue's own writers would refuse to write."""
        with open(self.at("tasks.jsonl")) as fh:
            rec = {}
            for line in fh:
                if line.strip():
                    entry = json.loads(line)
                    if entry.get("id") == task_id:
                        rec.update(entry)
        return rec


class Status(Close):
    """Only a terminal task's workspace, and `done` is the only terminal status."""

    def test_a_task_still_held_by_a_minion_is_refused(self):
        self.dispatched(status="doing")
        text = self.assertRefused(self.close(), "not done")
        self.assertIn("reset", text)
        self.assertClosedNothing()

    def test_a_blocked_task_is_going_back_out_to_a_minion(self):
        self.dispatched(status="blocked")
        self.assertRefused(self.close(), "not done", "going back out")
        self.assertClosedNothing()

    def test_a_reclaimed_task_is_refused(self):
        self.dispatched(status="todo")
        self.assertRefused(self.close(), "not done")
        self.assertClosedNothing()

    def test_the_status_is_read_before_herdr_is_asked(self):
        # A refusal this side of herdr costs nothing and says the same thing whether
        # or not herdr is up. It is also the one that stops a live minion's
        # workspace being enumerated in the first place.
        self.dispatched(status="doing")
        self.close()
        self.assertEqual(self.herdr.calls, [])


class Ordering(Close):
    """Retire first, and never the other way round. Both halves of the retirement's
    postcondition are read from the world rather than taken from an exit code, so a
    retirement that refused refuses this too without being told."""

    def test_a_tree_that_is_still_there_has_not_been_retired(self):
        self.dispatched()
        text = self.assertRefused(self.close(), "has not been retired")
        self.assertIn("strand", text)
        self.assertIn("siana-retire make-thing", text)
        self.assertClosedNothing()

    def test_nothing_is_asked_of_herdr_before_the_tree_is_gone(self):
        self.dispatched()
        self.close()
        self.assertEqual(self.herdr.calls, [])

    def test_a_retirement_that_refused_refuses_the_close_too(self):
        # The composition that matters, driven through both real commands. An
        # ignored file is what `siana-retire` refuses on and what `git worktree
        # remove` would have deleted without a word.
        worktree = self.dispatched()
        self.write(worktree, ".env", "SECRET=1\n")
        self.assertRefused(self.run_bin("siana-retire", "make-thing"),
                           "still holds work")
        self.assertTrue(os.path.exists(worktree))
        self.assertRefused(self.close(), "has not been retired")
        self.assertClosedNothing()

    def test_a_tree_deleted_by_hand_is_not_a_retirement(self):
        # The directory is gone and git's registration is not, which is exactly what
        # `rm -rf` on a worktree leaves and what `git worktree repair` brings back.
        # Closing on the strength of the missing directory alone would close a
        # workspace whose worktree git still believes in.
        worktree = self.dispatched()
        shutil.rmtree(worktree)
        self.answers(listing(self.workspace(worktree)))
        text = self.assertRefused(self.close(), "still registers")
        self.assertIn("worktree prune", text)
        self.assertClosedNothing()

    def test_retire_then_close_is_the_whole_sequence(self):
        worktree = self.dispatched()
        retired = self.assertAccepted(self.run_bin("siana-retire", "make-thing"))
        # Retire names the next step rather than taking it, which is what keeps the
        # two decisions apart.
        self.assertIn("siana-close-workspace make-thing", retired)
        self.assertEqual(self.herdr.calls, [], "siana-retire spoke to herdr")
        self.answers(listing(self.workspace(worktree)), listing())
        self.assertAccepted(self.close())
        self.assertEqual(self.herdr.calls_to("workspace.close"),
                         [{"workspace_id": self.WS}])


class Identity(Close):
    """Everything herdr has to agree about before a workspace is this task's."""

    def test_no_branch_and_no_worktree_is_touched(self):
        # The grant is one workspace and nothing beside it. A branch is
        # `siana-reap`'s on a higher bar, and a worktree is `siana-retire`'s.
        worktree = self.retired()
        head = self.git("rev-parse", "refs/heads/siana/make-thing").strip()
        self.answers(listing(self.workspace(worktree)), listing())
        self.assertAccepted(self.close())
        self.assertEqual(self.git("rev-parse",
                                  "refs/heads/siana/make-thing").strip(), head)
        self.assertEqual(self.herdr.calls_to("worktree.remove"), [])

    def test_the_recorded_workspace_is_the_one_that_closes(self):
        worktree = self.retired()
        self.answers(listing(self.workspace(worktree)), listing())
        out = self.assertAccepted(self.close())
        self.assertIn(f"closed   {self.WS}", out)
        self.assertEqual(self.herdr.calls_to("workspace.close"),
                         [{"workspace_id": self.WS}])
        for method in ("pane.close", "worktree.remove"):
            self.assertEqual(self.herdr.calls_to(method), [], method)

    def test_a_decoy_wearing_the_label_and_the_tree_is_never_closed(self):
        # Every mutable discriminator herdr offers, on workspaces that are not this
        # task's: the same label (herdr does not enforce unique ones), the same
        # number (a position in a list, which shifts every time another workspace
        # closes), and focus. Only the recorded pane's id says which one is this
        # task's, and it is the only one closed.
        worktree = self.retired()
        others = (self.workspace(worktree, wid="w1"),
                  self.workspace(worktree, wid="w2", focused=True))
        self.answers(listing(others[0], self.workspace(worktree), others[1]),
                     listing(*others))
        self.assertAccepted(self.close())
        self.assertEqual(self.herdr.calls_to("workspace.close"),
                         [{"workspace_id": self.WS}])

    def test_no_agent_is_ever_looked_up_by_name(self):
        # A minion's herdr agent name is its task id, and herdr frees that name the
        # moment the agent exits and gives it to the next minion on the same task.
        # So resolving through it would find whichever agent holds the name now.
        # The whole of what this command asks herdr is two reads and one close.
        worktree = self.retired()
        self.answers(listing(self.workspace(worktree)), listing())
        self.assertAccepted(self.close())
        self.assertEqual(sorted({m for m, _ in self.herdr.calls}),
                         ["workspace.close", "workspace.list"])

    def test_nothing_is_focused_to_find_out_where_it_is(self):
        # Focus is the captain's attention, not a handle. Reading it would be one
        # thing; moving it to establish identity would be another, and neither
        # happens here.
        worktree = self.retired()
        self.answers(listing(self.workspace(worktree)), listing())
        self.assertAccepted(self.close())
        for method in ("workspace.focus", "agent.focus", "pane.focus"):
            self.assertEqual(self.herdr.calls_to(method), [], method)

    def test_a_label_that_matches_while_the_id_does_not_closes_nothing(self):
        # The same listing with this task's own workspace id absent. Every other
        # discriminator agrees, and the answer is still "already closed".
        worktree = self.retired()
        self.answers(listing(self.workspace(worktree, wid="w1"),
                             self.workspace(worktree, wid="w2")))
        out = self.assertAccepted(self.close())
        self.assertIn("already", out)
        self.assertClosedNothing()

    def test_a_source_workspace_is_never_closed(self):
        # The project's own checkout. Closing it closes every linked worktree
        # workspace under it, which is that project's whole fleet in one call.
        self.retired()
        self.answers(listing(self.workspace(self.repo, linked=False,
                                            label="proj")))
        text = self.assertRefused(self.close(), "not a linked-worktree workspace")
        self.assertIn("every linked worktree workspace", text)
        self.assertClosedNothing()

    def test_a_missing_linked_flag_is_a_refusal_and_not_a_default(self):
        # A plain `workspace.create` workspace carries no `is_linked_worktree` at
        # all, so reading its absence as permission is the direction that cascades.
        worktree = self.retired()
        self.answers(listing(self.workspace(worktree, linked=None)))
        self.assertRefused(self.close(), "not a linked-worktree workspace")
        self.assertClosedNothing()

    def test_a_workspace_with_no_worktree_metadata_is_refused(self):
        self.retired()
        self.answers(listing(self.workspace(None, worktree=False)))
        self.assertRefused(self.close(), "no worktree behind")
        self.assertClosedNothing()

    def test_a_workspace_open_on_another_tree_is_another_tasks(self):
        self.retired()
        self.answers(listing(self.workspace(self.at("wt", "somebody-else"))))
        self.assertRefused(self.close(), "not the same tree")
        self.assertClosedNothing()

    def test_a_workspace_naming_no_checkout_is_refused(self):
        self.retired()
        self.answers(listing(self.workspace(None)))
        self.assertRefused(self.close(), "nothing herdr names")
        self.assertClosedNothing()

    def test_a_workspace_in_another_repository_is_another_projects(self):
        worktree = self.retired()
        self.answers(listing(self.workspace(worktree, root=self.at("elsewhere"))))
        self.assertRefused(self.close(), "belongs to", "proj")
        self.assertClosedNothing()

    def test_a_repository_key_that_disagrees_with_git_is_refused(self):
        # The root and the key are two statements about one repository, and a
        # workspace this closes has to make both of them.
        worktree = self.retired()
        self.answers(listing(self.workspace(worktree,
                                            key=self.at("elsewhere", ".git"))))
        self.assertRefused(self.close(), "names")
        self.assertClosedNothing()

    def test_the_checkout_is_matched_through_both_spellings_of_one_path(self):
        # git reports a worktree by its real path and herdr recorded the one it was
        # created under; on macOS anything below /tmp or /var is spelled two ways.
        worktree = self.retired()
        doubled = worktree.replace(os.sep, os.sep + "." + os.sep, 1)
        self.answers(listing(self.workspace(doubled)), listing())
        self.assertAccepted(self.close())
        self.assertEqual(self.herdr.calls_to("workspace.close"),
                         [{"workspace_id": self.WS}])


class InUse(Close):
    """Three ways a workspace is somebody's, and the third is invisible to herdr."""

    def test_a_working_workspace_is_never_closed(self):
        worktree = self.retired()
        self.answers(listing(self.workspace(worktree, status="working")))
        self.assertRefused(self.close(), "w9 is working", "mid-turn")
        self.assertClosedNothing()

    def test_a_blocked_agent_is_a_live_one_waiting_on_a_person(self):
        # The state a denylist naming `working` alone read as closable. A blocked
        # agent is stopped at a modal dialog - `siana-dispatch` refuses to prompt
        # into that one for the same reason - so closing it kills an agent
        # mid-question, which is the exact harm this grant is drawn around.
        worktree = self.retired()
        self.answers(listing(self.workspace(worktree, status="blocked")))
        self.assertRefused(self.close(), "w9 is blocked", "waiting for a person")
        self.assertClosedNothing()

    def test_a_status_this_command_does_not_know_is_never_a_finished_one(self):
        # The list is the states a finished minion leaves, not the states to avoid,
        # so a sixth one a later herdr adds arrives as a refusal rather than as a
        # close. Herdr's schema has five today (protocol 19).
        worktree = self.retired()
        self.answers(listing(self.workspace(worktree, status="reviewing")))
        self.assertRefused(self.close(), "w9 is reviewing",
                           "one this command does not know")
        self.assertClosedNothing()

    def test_the_states_it_closes_are_the_ones_a_finished_minion_leaves(self):
        # Read off the command, so the allowlist and the tests cannot drift apart
        # into agreeing with each other.
        self.assertEqual(set(c.FINISHED), {"idle", "done", "unknown"})

    def test_a_focused_workspace_is_never_closed(self):
        worktree = self.retired()
        self.answers(listing(self.workspace(worktree, focused=True)))
        text = self.assertRefused(self.close(), "the captain is looking at")
        self.assertIn("focus is never identity", text)
        self.assertClosedNothing()

    def test_a_workspace_herdr_will_not_say_the_focus_of_is_refused(self):
        # `False` and absent are two different answers and `.get` gives a falsy
        # value for both, which is the way this one would have failed open.
        worktree = self.retired()
        w = self.workspace(worktree)
        del w["focused"]
        self.answers(listing(w))
        self.assertRefused(self.close(), "does not say whether")
        self.assertClosedNothing()

    def test_a_workspace_whose_state_herdr_will_not_say_is_refused(self):
        worktree = self.retired()
        w = self.workspace(worktree)
        del w["agent_status"]
        self.answers(listing(w))
        self.assertRefused(self.close(), "says nothing about what is running")
        self.assertClosedNothing()

    def test_an_idle_or_finished_agent_is_closable(self):
        # The states a retired minion's workspace actually reads as, `unknown`
        # included: herdr answers that for a pane holding no agent it has
        # identified, which is exactly what a finished minion leaves. Refusing it
        # would leave the grant unable to close the ordinary case.
        for n, status in enumerate(("idle", "done", "unknown")):
            with self.subTest(status=status):
                task_id, wid = f"task-{n}", f"w{n}0"
                worktree = self.retired(task_id, owner=f"claude@{wid}:p2")
                self.answers(listing(self.workspace(worktree, wid=wid,
                                                    status=status)),
                             listing())
                self.assertAccepted(self.close(task_id))
                self.assertIn({"workspace_id": wid},
                              self.herdr.calls_to("workspace.close"))

    def test_a_live_minion_in_the_same_workspace_refuses_the_close(self):
        # `workspace.list` reports one agent status for a whole workspace however
        # many panes are in it, so a second minion split into this one is invisible
        # to herdr and visible only in the queue.
        worktree = self.retired()
        self.dispatched("other-task", owner="claude@w9:p4", status="doing")
        self.answers(listing(self.workspace(worktree)))
        text = self.assertRefused(self.close(), "other task(s) in the queue")
        self.assertIn("other-task", text)
        self.assertIn("doing", text)
        self.assertClosedNothing()

    def test_a_finished_task_sharing_the_workspace_refuses_it_too(self):
        # Shared custody is never inferred away by reading the other record as
        # stale. Whether it is stale is SIANA's to say.
        worktree = self.retired()
        self.dispatched("other-task", owner="claude@w9:p4", status="done")
        self.answers(listing(self.workspace(worktree)))
        self.assertRefused(self.close(), "other task(s) in the queue", "other-task")
        self.assertClosedNothing()

    def test_a_task_in_another_workspace_is_not_in_the_way(self):
        worktree = self.retired()
        self.dispatched("other-task", owner="claude@w7:p1", status="doing")
        self.answers(listing(self.workspace(worktree)), listing())
        self.assertAccepted(self.close())

    def test_a_task_whose_owner_names_no_pane_is_not_in_the_way(self):
        worktree = self.retired()
        self.dispatched("other-task", owner="claude@handmade", status="done")
        self.answers(listing(self.workspace(worktree)), listing())
        self.assertAccepted(self.close())


class WhatHerdrSays(Close):
    """A herdr that cannot answer has said nothing about any workspace, and a
    workspace it does not list is one already closed. Reading either as the other is
    the whole failure this separation exists to prevent."""

    def test_a_workspace_herdr_does_not_list_is_an_idempotent_no_op(self):
        self.retired()
        self.answers(listing())
        out = self.assertAccepted(self.close())
        self.assertIn("already", out)
        self.assertIn("absence", out)
        self.assertClosedNothing()

    def test_a_second_close_after_a_successful_one_is_a_no_op(self):
        worktree = self.retired()
        self.answers(listing(self.workspace(worktree)), listing())
        self.assertAccepted(self.close())
        self.assertAccepted(self.close())
        self.assertEqual(len(self.herdr.calls_to("workspace.close")), 1)

    def test_a_herdr_that_cannot_be_reached_is_never_an_absence(self):
        self.retired()
        out = self.close(socket=self.at("no-such.sock"))
        self.assertRefused(out, "cannot reach herdr")
        self.assertNotIn("already", out.stdout)
        self.assertClosedNothing()

    def test_a_herdr_that_hangs_up_mid_request_is_never_an_absence(self):
        self.retired()
        self.answers(CLOSE)
        out = self.close()
        self.assertRefused(out, "closed without a response")
        self.assertNotIn("already", out.stdout)
        self.assertClosedNothing()

    def test_a_herdr_that_refuses_the_listing_is_never_an_absence(self):
        self.retired()
        self.answers(NO_WORKSPACE)
        out = self.close()
        self.assertRefused(out, "workspace_not_found")
        self.assertNotIn("already", out.stdout)
        self.assertClosedNothing()

    def test_a_listing_that_is_not_a_list_is_refused(self):
        self.retired()
        self.answers({"type": "workspace_list", "workspaces": "w9"})
        self.assertRefused(self.close(), "did not answer with a list")
        self.assertClosedNothing()

    def test_a_listing_with_no_workspaces_key_is_refused(self):
        self.retired()
        self.answers({"type": "workspace_list"})
        self.assertRefused(self.close(), "did not answer with a list")
        self.assertClosedNothing()

    def test_an_entry_with_no_workspace_id_is_refused(self):
        # A partial match is how the wrong workspace gets closed, so an entry that
        # cannot be matched at all stops the whole read rather than being skipped.
        worktree = self.retired()
        self.answers(listing({"label": "make-thing"},
                             self.workspace(worktree)))
        self.assertRefused(self.close(), "no workspace_id")
        self.assertClosedNothing()

    def test_a_workspace_still_listed_after_closing_is_reported(self):
        # `workspace.close` answering `ok` is herdr saying it took the request. What
        # this command claims is that the workspace is gone, so it asks again.
        worktree = self.retired()
        self.answers(listing(self.workspace(worktree)))
        self.assertRefused(self.close(), "still lists", "did not take effect")
        self.assertEqual(self.herdr.calls_to("workspace.close"),
                         [{"workspace_id": self.WS}])

    def test_a_herdr_that_goes_silent_after_the_close_is_reported(self):
        worktree = self.retired()
        self.answers(listing(self.workspace(worktree)), CLOSE)
        self.assertRefused(self.close(), "closed without a response")

    def test_the_confirmation_is_a_fresh_read(self):
        worktree = self.retired()
        self.answers(listing(self.workspace(worktree)), listing())
        self.assertAccepted(self.close())
        self.assertEqual(len(self.herdr.calls_to("workspace.list")), 2)


if __name__ == "__main__":
    unittest.main()
