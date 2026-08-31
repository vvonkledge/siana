"""siana-dispatch's other half: everything that only happens once herdr answers.

`test_dispatch.py` covers the half that decides *where* a minion lands. This covers
the half that decides whether one is actually there, and it is the half where every
failure is expensive. A dispatch that calls a half-started agent ready types the task
into a TUI that cannot take it yet and returns success. A prompt that is swallowed
leaves a minion holding a task it never saw, forever. A claim the queue refuses after
the pane exists leaves a container behind that no record points at. And `--check`
reading herdr's silence as death is how a captain is talked into resetting live work.

Only herdr is scripted here. The queue is a real `tasks`, the registry a real
`datafile`, and the commands speak the real socket protocol to a real socket.
"""

import contextlib
import io
import json
import os
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import fake_semantic
from fake_herdr import CLOSE, FakeHerdr, HerdrError
from helpers import HomeTest, script

d = script("siana-dispatch")


def seen(kind="claude", ready=True, status="idle"):
    """What `agent.get` says about a pane."""
    return {"agent": {"agent": kind, "interactive_ready": ready,
                      "agent_status": status}}


NOTHING = {"agent": {}}          # herdr answering that the pane holds no agent yet.

# A pane taking input that herdr has not identified an agent in. It is the answer
# that separates the two halves of the readiness test, which `NOTHING` cannot: that
# one fails both at once, so it cannot say which half is doing the work.
UNNAMED = {"agent": {"interactive_ready": True, "agent_status": "idle"}}

# herdr's own `agent_name_taken`, which names the incumbent in its message. That
# pane id is the whole value of the error: it is a previous minion on this task.
TAKEN = HerdrError("agent_name_taken",
                   "agent name is already used; candidates: "
                   "terminal_id=term_65a15 pane_id=w9:p4 workspace_id=w9 "
                   "tab_id=w9:t1 cwd=/private/tmp status=Unknown")

# What herdr says about a name no live agent holds. A minion that exits frees its
# name at once, so this is also what a pane whose agent just died answers.
VANISHED = HerdrError("agent_not_found", "agent target not found")


class Dispatched:
    """What one run of `siana-dispatch` left behind."""

    def __init__(self, refusal, out, err):
        self.refusal, self.out, self.err = refusal, out, err

    @property
    def binding(self):
        return json.loads(self.out)

    @property
    def said(self):
        return f"{self.refusal}\n" + " ".join(getattr(self.refusal, "hints", ()))


class HerdrTest(HomeTest):
    """A home with a herdr that answers on cue."""

    def setUp(self):
        super().setUp()
        self.herdr = FakeHerdr().start()
        self.addCleanup(self.herdr.stop)

    def socket_env(self):
        return {"HERDR_SOCKET_PATH": self.herdr.path}


class DispatchTest(HerdrTest):
    """Everything a real dispatch reads: standing orders, a registry, and a queue."""

    def setUp(self):
        super().setUp()
        self.contract("projects")
        self.template("orders.md")
        self.queue()
        self.work = self.at("work")
        self.tree = self.at("tree")
        os.makedirs(self.work)
        os.makedirs(self.tree)

    @contextlib.contextmanager
    def release_refused(self):
        """A `tasks reset` that will not run, and every other `tasks` call real.

        Nothing can make a real queue refuse to release a claim dispatch itself
        just made, and the hint printed when it does is the one command on this
        whole path the captain types by hand. So the release alone is intercepted.

        Scoped to the dispatch, and never wider: this replaces `subprocess.run`
        itself, so left standing it would refuse the very command the test then
        runs to prove the hint works.
        """
        real = subprocess.run

        def refuse_reset(argv, *a, **kw):
            if "reset" in argv:
                return subprocess.CompletedProcess(argv, 1, "", "reset refused")
            return real(argv, *a, **kw)

        with mock.patch.object(subprocess, "run", refuse_reset):
            yield

    def prescribed(self, said):
        """The command a refusal tells the captain to run, taken back out of it."""
        found = re.search(r"`([^`]*\breset\b[^`]*)`", said)
        self.assertIsNotNone(found, f"no reset command was prescribed in:\n{said}")
        return shlex.split(found.group(1))

    def shared_project(self):
        """A project with worktree isolation off: one pane, no branch."""
        self.project("proj", path=self.work, worktree=False)
        self.herdr.reply("workspace.create", {"workspace": {"workspace_id": "ws1"},
                                              "root_pane": {"pane_id": "w1:p1"}})

    def isolated_project(self):
        """The default: a worktree of its own, and a work pane split off the root."""
        self.project("proj", path=self.work)
        self.herdr.reply("worktree.create", {"workspace": {"workspace_id": "ws1"},
                                             "worktree": {"path": self.tree},
                                             "root_pane": {"pane_id": "w1:p1"}})
        self.herdr.reply("pane.split", {"pane": {"pane_id": "w1:p2"}})

    def task(self, title="Do a thing", **flags):
        argv = ["tasks", "add", title, "--verify", "true", "--project", "proj"]
        for key, value in flags.items():
            argv += [f"--{key}", value]
        out = self.run_cmd(argv)
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return next(line.split(": ", 1)[1] for line in out.stdout.splitlines()
                    if line.startswith("id: "))

    def record(self, task_id):
        return d.fold(self.at("tasks.jsonl"), "id")[task_id]

    def dispatch(self, task_id, *argv, ready=None, landed=None, env=None):
        """One `siana-dispatch`, run in-process so the timeouts can be shortened.

        They are the point of several of these tests and they are minutes long, so
        driving this as a process would mean either not covering them or a suite
        nobody runs."""
        out, err = io.StringIO(), io.StringIO()
        env = {"SIANA_HOME": self.home, "SIANA_TASKS_FILE": self.at("tasks.jsonl"),
               **self.socket_env(), **(env or {})}
        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.dict(os.environ, env))
            stack.enter_context(mock.patch.object(
                sys, "argv", ["siana-dispatch", task_id, *argv]))
            if ready is not None:
                stack.enter_context(mock.patch.object(d, "READY_TIMEOUT_S", ready))
            if landed is not None:
                stack.enter_context(mock.patch.object(d, "PROMPT_LANDED_S", landed))
            stack.enter_context(redirect_stdout(out))
            stack.enter_context(redirect_stderr(err))
            try:
                d.main()
                refusal = None
            except d.Refusal as r:
                refusal = r
            except KeyboardInterrupt:
                # The scripted herdr's backstop: this loop was never going to stop.
                self.fail(f"the dispatch never stopped, after "
                          f"{len(self.herdr.calls)} calls to herdr")
        return Dispatched(refusal, out.getvalue(), err.getvalue())


class Readiness(DispatchTest):
    """herdr identifying the agent is the honest readiness test. `agent.wait` is
    level-triggered and answers before anything has been drawn."""

    def test_a_minion_that_comes_up_and_takes_its_prompt_prints_its_binding(self):
        self.shared_project()
        self.herdr.reply("agent.get", seen(), seen(status="working"))
        task_id = self.task()

        result = self.dispatch(task_id)

        self.assertIsNone(result.refusal, result.err)
        self.assertEqual(result.binding["owner"], "claude@w1:p1")
        self.assertEqual(result.binding["pane_id"], "w1:p1")
        self.assertEqual(result.binding["cwd"], self.work)
        self.assertIsNone(result.binding["branch"])
        # The task is claimed by the pane the minion is actually in, and retargeted
        # at its own tree, so `done` verifies where the work happened.
        self.assertEqual(self.record(task_id)["status"], "doing")
        self.assertEqual(self.record(task_id)["owner"], "claude@w1:p1")
        self.assertEqual(self.record(task_id)["cwd"], self.work)

    def test_an_isolated_minion_works_in_the_worktree_and_not_the_project_tree(self):
        self.isolated_project()
        self.herdr.reply("agent.get", seen(), seen(status="working"))
        task_id = self.task()

        result = self.dispatch(task_id)

        self.assertIsNone(result.refusal, result.err)
        self.assertEqual(result.binding["branch"], f"siana/{task_id}")
        self.assertEqual(result.binding["cwd"], self.tree)
        self.assertEqual(result.binding["project_path"], self.work)
        self.assertEqual(self.record(task_id)["cwd"], self.tree)
        # The root pane carries no env, so the minion is put in a split that does and
        # the env-less pane is closed behind it. A dispatch that skipped that would
        # look identical and leave a minion with no identity.
        self.assertEqual(result.binding["pane_id"], "w1:p2")
        self.assertEqual(self.herdr.calls_to("pane.close"), [{"pane_id": "w1:p1"}])

    def test_the_minion_is_started_with_the_orders_it_was_promised(self):
        # A minion started without its orders does not know how to report back, and
        # a dispatch that dropped them looks exactly like one that did not.
        self.shared_project()
        self.herdr.reply("agent.get", seen(), seen(status="working"))
        task_id = self.task()

        self.dispatch(task_id)

        started, = self.herdr.calls_to("agent.start")
        self.assertEqual(started["kind"], "claude")
        self.assertIn("--dangerously-skip-permissions", started["args"])
        flag = started["args"].index("--append-system-prompt-file")
        with open(started["args"][flag + 1]) as fh:
            self.assertIn("Standing orders", fh.read())

    def test_nothing_is_typed_until_herdr_says_the_agent_can_take_it(self):
        # Both halves matter and each is a whole window on its own: herdr has not
        # identified the agent yet, and herdr has identified one whose TUI still
        # cannot take a keystroke. A prompt sent in either is swallowed with no
        # error, and the dispatch reads as a success.
        self.shared_project()
        self.herdr.reply("agent.get", NOTHING, seen(ready=False), seen(),
                         seen(status="working"))
        task_id = self.task()

        result = self.dispatch(task_id)

        self.assertIsNone(result.refusal, result.err)
        asked = [method for method, _ in self.herdr.calls]
        self.assertEqual(asked.index("agent.prompt") - asked.index("agent.get"), 3,
                         "the pane was typed into before herdr said it could take it")

    def test_a_pane_taking_input_is_not_ready_until_herdr_has_named_its_agent(self):
        # The other half of the readiness test, on its own. herdr reports a pane as
        # taking input before it has identified what is in it, and typing then is
        # typing into whatever the pane happens to be running - a shell, a pager, a
        # dialog. Nothing about the answer looks wrong, which is why it needs its
        # own case: a scripted answer that fails both halves at once proves neither.
        self.shared_project()
        self.herdr.reply("agent.get", UNNAMED)
        task_id = self.task()

        result = self.dispatch(task_id, ready=0.2, landed=0.2)

        self.assertIn("did not become ready", result.said)
        self.assertEqual(self.herdr.calls_to("agent.prompt"), [])

    def test_an_agent_that_dies_mid_poll_ends_in_the_readiness_refusal(self):
        # A name is freed the moment its agent exits, so a minion that dies while
        # dispatch is waiting on it reads as `agent_not_found`. That is one instant's
        # observation, not an answer about the pane: read as a refusal it escapes the
        # loop and the captain gets a herdr code instead of the pane and the owner.
        self.shared_project()
        self.herdr.reply("agent.get", NOTHING, VANISHED)
        task_id = self.task()

        # Long enough to poll twice: the first answer is a pane still coming up and
        # the second is the agent it was coming up as, already gone.
        result = self.dispatch(task_id, ready=0.8, landed=0.2)

        self.assertIn("did not become ready", result.said)
        self.assertIn("w1:p1", result.said)
        self.assertIn("claude@w1:p1", result.said)
        self.assertNotIn("agent_not_found", result.said)

    def test_a_minion_that_never_becomes_ready_names_the_pane_and_the_owner(self):
        self.shared_project()
        self.herdr.reply("agent.get", NOTHING)
        task_id = self.task()

        result = self.dispatch(task_id, ready=0.2)

        self.assertIn("did not become ready", result.said)
        self.assertIn("w1:p1", result.said)
        self.assertIn("claude@w1:p1", result.said)
        self.assertEqual(self.herdr.calls_to("agent.prompt"), [])

    def test_a_minion_that_never_becomes_ready_keeps_its_claim(self):
        # Deliberately not abandoned: by now the pane may hold a working agent, and
        # `tasks reset` is the captain's call once they have read it. A dispatch that
        # tidied up here would kill a minion that was merely slow.
        self.shared_project()
        self.herdr.reply("agent.get", NOTHING)
        task_id = self.task()

        self.dispatch(task_id, ready=0.2)

        self.assertEqual(self.record(task_id)["status"], "doing")
        self.assertEqual(self.herdr.calls_to("workspace.close"), [])

    def test_a_minion_born_blocked_is_never_prompted(self):
        # An untrusted-directory dialog is not skipped by
        # --dangerously-skip-permissions, and prompting into a modal types the task
        # into a dialog box.
        self.shared_project()
        self.herdr.reply("agent.get", seen(status="blocked"))
        task_id = self.task()

        result = self.dispatch(task_id)

        self.assertIn("blocked before it has its task", result.said)
        self.assertEqual(self.herdr.calls_to("agent.prompt"), [])


class PromptDelivery(DispatchTest):
    """Delivering a prompt is not the same as it arriving. `interactive_ready` flips
    true while the TUI still cannot take input, and a prompt sent then is swallowed
    with no error: the dispatch reads as a success and the minion never starts."""

    def test_a_prompt_that_lands_is_not_sent_twice(self):
        self.shared_project()
        self.herdr.reply("agent.get", seen(), seen(status="working"))
        task_id = self.task()

        result = self.dispatch(task_id, landed=0.2)

        self.assertIsNone(result.refusal, result.err)
        prompt, = self.herdr.calls_to("agent.prompt")
        self.assertEqual(prompt["text"], "Take your task.")
        self.assertEqual(prompt["target"], task_id)

    def test_a_prompt_that_is_swallowed_is_sent_once_more(self):
        self.shared_project()
        # Ready, then a window that stays idle, then the resend takes.
        self.herdr.reply("agent.get", seen(), seen(), seen(status="working"))
        task_id = self.task()

        result = self.dispatch(task_id, landed=0.2)

        self.assertIsNone(result.refusal, result.err)
        self.assertEqual(len(self.herdr.calls_to("agent.prompt")), 2)
        self.assertEqual(self.record(task_id)["status"], "doing")

    def test_a_prompt_that_never_lands_twice_is_refused_not_called_green(self):
        self.shared_project()
        self.herdr.reply("agent.get", seen())          # idle forever
        task_id = self.task()

        result = self.dispatch(task_id, landed=0.2)

        self.assertIn("did not take its prompt", result.said)
        self.assertIn("never started working", result.said)
        self.assertEqual(len(self.herdr.calls_to("agent.prompt")), 2)
        # Still held, and the refusal says by whom: the pane is alive and may yet
        # be doing something, so reclaiming it is the captain's call.
        self.assertEqual(self.record(task_id)["status"], "doing")
        self.assertIn("claude@w1:p1", result.said)

    def test_an_agent_that_dies_in_the_delivery_window_is_a_refusal_that_says_so(self):
        # herdr answers `agent_not_found` to the prompt and to every poll after it.
        # None of that is delivery, and dispatch's own refusal is the only one that
        # names the pane and the owner the captain has to act on.
        self.shared_project()
        self.herdr.reply("agent.get", seen(), VANISHED)
        self.herdr.reply("agent.prompt", VANISHED)
        task_id = self.task()

        result = self.dispatch(task_id, landed=0.2)

        self.assertIn("did not take its prompt", result.said)
        self.assertIn("no agent in w1:p1 any more", result.said)
        self.assertIn("claude@w1:p1", result.said)
        self.assertNotIn("agent_not_found", result.said)
        # Still held: the pane is there to be read, and reclaiming is the captain's.
        self.assertEqual(self.record(task_id)["status"], "doing")

    def test_a_custom_prompt_is_the_one_delivered(self):
        self.shared_project()
        self.herdr.reply("agent.get", seen(), seen(status="working"))
        task_id = self.task()

        self.dispatch(task_id, "--prompt", "Resume where you left off.")

        prompt, = self.herdr.calls_to("agent.prompt")
        self.assertEqual(prompt["text"], "Resume where you left off.")


class AbandonedDispatch(DispatchTest):
    """A container no task record points at is one nobody will ever find again. The
    claim happens after the pane exists, so the queue refusing it is the one moment
    a half-made dispatch has to undo itself."""

    def blocked_task(self):
        """A task the queue will refuse to start: its dependency is not done."""
        first = self.task("First")
        return self.task("Second", dep=first)

    def test_a_claim_the_queue_refuses_closes_the_workspace_it_had_made(self):
        self.shared_project()
        result = self.dispatch(self.blocked_task())

        self.assertIn("tasks refused to dispatch", result.said)
        self.assertIn("unmet dependency", result.said)
        self.assertEqual(self.herdr.calls_to("workspace.close"),
                         [{"workspace_id": "ws1"}])

    def test_a_claim_the_queue_refuses_removes_the_worktree_it_had_made(self):
        # Removing it is safe precisely because it is brand new: nothing has run in
        # it, so there is no work in there to destroy.
        self.isolated_project()
        result = self.dispatch(self.blocked_task())

        self.assertIn("tasks refused to dispatch", result.said)
        self.assertEqual(self.herdr.calls_to("worktree.remove"),
                         [{"workspace_id": "ws1"}])

    def test_a_worktree_that_will_not_remove_is_closed_and_said_out_loud(self):
        # The pane still goes, because a container nothing points at is the thing
        # this must never leave behind. What is left is a directory, and a directory
        # nobody is told about is one nobody cleans up.
        self.isolated_project()
        self.herdr.reply("worktree.remove", HerdrError("busy", "worktree is in use"))

        result = self.dispatch(self.blocked_task())

        self.assertEqual(self.herdr.calls_to("workspace.close"),
                         [{"workspace_id": "ws1"}])
        self.assertIn(f"worktree left behind at {self.tree}", result.err)

    def test_nothing_is_abandoned_when_the_claim_succeeds(self):
        self.isolated_project()
        self.herdr.reply("agent.get", seen(), seen(status="working"))

        self.dispatch(self.task())

        self.assertEqual(self.herdr.calls_to("worktree.remove"), [])
        self.assertEqual(self.herdr.calls_to("workspace.close"), [])


class StartRefusals(DispatchTest):
    """`agent.start` is the last call that can refuse, and the first that refuses
    holding a claim. Left to escape, it exits with a raw herdr line, an orphan
    workspace nothing points at, and a task in `doing` whose pane holds no agent -
    and the next dispatch of that task then fails on the leftover worktree instead,
    saying it may hold unlanded work that never existed."""

    def started(self, answer):
        self.herdr.reply("agent.start", answer)
        return self.task()

    def test_a_name_a_live_agent_already_holds_names_the_pane_to_read_first(self):
        # herdr enforces one live agent per name, so this is a previous minion on
        # this same task announcing itself. Its pane is in herdr's own message, and
        # it is exactly what the captain has to read before replacing anything.
        self.shared_project()
        task_id = self.started(TAKEN)

        result = self.dispatch(task_id)

        self.assertIn(f"a live agent is already named {task_id} at w9:p4", result.said)
        self.assertIn("previous minion on this task", result.said)

    def test_a_refused_start_gives_the_task_back_and_takes_the_workspace_down(self):
        self.shared_project()
        task_id = self.started(TAKEN)

        result = self.dispatch(task_id)

        self.assertEqual(self.record(task_id)["status"], "todo")
        self.assertIsNone(self.record(task_id)["owner"])
        self.assertEqual(self.herdr.calls_to("workspace.close"),
                         [{"workspace_id": "ws1"}])
        self.assertIn("back in the queue", result.said)

    def test_a_refused_start_removes_the_worktree_it_had_just_made(self):
        # Safe for the same reason the claim rollback is: the agent never started,
        # so nothing has run in there and there is no work to destroy.
        self.isolated_project()
        task_id = self.started(TAKEN)

        self.dispatch(task_id)

        self.assertEqual(self.herdr.calls_to("worktree.remove"),
                         [{"workspace_id": "ws1"}])
        self.assertEqual(self.record(task_id)["status"], "todo")

    def test_a_worktree_that_will_not_remove_is_left_and_said_out_loud(self):
        # A cleanup that cannot prove it is safe stops and reports. The pane still
        # goes, because a container no record points at is the thing to never leave.
        self.isolated_project()
        self.herdr.reply("worktree.remove", HerdrError("busy", "worktree is in use"))
        task_id = self.started(TAKEN)

        result = self.dispatch(task_id)

        self.assertIn(f"worktree left behind at {self.tree}", result.err)
        self.assertEqual(self.herdr.calls_to("workspace.close"),
                         [{"workspace_id": "ws1"}])

    def test_a_cleanup_that_also_refuses_does_not_replace_what_it_cleans_up(self):
        # Two herdr calls refusing in one dispatch. The second is about the cleanup
        # and the first is about the dispatch, and only the first is what the
        # captain came for: a rollback that threw its own error would hand them a
        # bare herdr code instead of the reason, the hints, and the queue result.
        self.isolated_project()
        self.herdr.reply("worktree.remove", HerdrError("busy", "worktree is in use"))
        self.herdr.reply("workspace.close", HerdrError("no_such_workspace", "gone"))
        task_id = self.started(TAKEN)

        result = self.dispatch(task_id)

        self.assertIn(f"a live agent is already named {task_id} at w9:p4", result.said)
        self.assertIn("previous minion on this task", result.said)
        self.assertIn("back in the queue", result.said)
        self.assertEqual(self.record(task_id)["status"], "todo")
        # Both halves of the mess, because both are still on disk and in herdr.
        self.assertIn(f"worktree left behind at {self.tree}", result.err)
        self.assertIn("workspace ws1 is still open", result.err)

    def test_a_workspace_that_will_not_close_is_a_warning_not_the_refusal(self):
        # The same loss, one call earlier: a project with no worktree closes its
        # workspace directly, so that refusal is the only one cleanup can hit.
        self.shared_project()
        self.herdr.reply("workspace.close", HerdrError("no_such_workspace", "gone"))
        task_id = self.started(TAKEN)

        result = self.dispatch(task_id)

        self.assertIn(f"a live agent is already named {task_id} at w9:p4", result.said)
        self.assertIn("back in the queue", result.said)
        self.assertEqual(self.record(task_id)["status"], "todo")
        self.assertIn("workspace ws1 is still open", result.err)

    def test_a_release_that_fails_prescribes_a_reset_that_runs(self):
        # The only manual action the rollback ever asks for. `tasks reset` requires
        # `--reason`, so a hint written against the older signature exits
        # USAGE_ERROR when it is typed back, and this path has nothing else to try.
        self.shared_project()
        task_id = self.started(TAKEN)

        with self.release_refused():
            result = self.dispatch(task_id)

        self.assertIn(f"{task_id} is still held by claude@w1:p1", result.said)
        self.assertEqual(self.record(task_id)["status"], "doing")
        command = self.prescribed(result.said)
        # The store, spelled out: a captain reading a refusal has no reason to be
        # standing anywhere in particular, and dispatch knows which queue this was.
        self.assertIn(self.at("tasks.jsonl"), command)
        self.assertAccepted(self.run_cmd(command))
        self.assertEqual(self.record(task_id)["status"], "todo")

    def test_a_task_id_herdr_will_not_name_an_agent_says_what_the_limit_is(self):
        # Unreachable through the queue's own ids, which are checked before anything
        # is created. Still hinted, because herdr's grammar is herdr's to change.
        self.shared_project()
        task_id = self.started(HerdrError("invalid_agent_name", "1-32 characters"))

        result = self.dispatch(task_id)

        self.assertIn("will not name an agent", result.said)
        self.assertIn("1 to 32", result.said)
        self.assertEqual(self.record(task_id)["status"], "todo")

    def test_a_refusal_herdr_gives_no_hint_for_still_rolls_the_dispatch_back(self):
        self.shared_project()
        task_id = self.started(HerdrError("pane_busy", "pane already has an agent"))

        result = self.dispatch(task_id)

        self.assertIn("pane_busy", result.said)
        self.assertEqual(self.record(task_id)["status"], "todo")
        self.assertEqual(self.herdr.calls_to("workspace.close"),
                         [{"workspace_id": "ws1"}])

    def test_a_herdr_that_stops_answering_still_gives_the_task_back(self):
        # The claim is released first precisely for this: it needs nothing from
        # herdr, and it is the half of the mess that outlives the workspace.
        self.shared_project()
        self.herdr.reply("agent.start", CLOSE)
        self.herdr.reply("workspace.close", CLOSE)
        task_id = self.task()

        result = self.dispatch(task_id)

        self.assertEqual(self.record(task_id)["status"], "todo")
        self.assertIn("back in the queue", result.said)
        self.assertIn("workspace ws1 is still open", result.err)


class TaskIdIsAlsoAnAgentName(DispatchTest):
    """A minion is named after its task, so the queue's id grammar has to fit inside
    herdr's. It does not: the queue allows 48 characters and herdr allows 32."""

    def test_an_id_too_long_to_be_an_agent_name_is_refused_before_anything_exists(self):
        # Caught late, this costs a workspace, a claim and a rollback, and it fails
        # in `agent.start` where the message is about herdr rather than about the id.
        self.shared_project()
        task_id = self.task("Reconciliationimplementationdocumentation of a thing")
        self.assertGreater(len(task_id), 32)

        result = self.dispatch(task_id)

        self.assertIn("cannot be a herdr agent name", result.said)
        self.assertIn("1 to 32 characters", result.said)
        # Nothing was made and nothing was taken: there is nothing to clean up.
        self.assertEqual(self.herdr.calls, [])
        self.assertEqual(self.record(task_id)["status"], "todo")

    def test_an_id_herdr_accepts_is_dispatched_as_normal(self):
        self.shared_project()
        self.herdr.reply("agent.get", seen(), seen(status="working"))
        task_id = self.task("A" * 32)
        self.assertEqual(len(task_id), 32)

        result = self.dispatch(task_id)

        self.assertIsNone(result.refusal, result.err)
        started, = self.herdr.calls_to("agent.start")
        self.assertEqual(started["name"], task_id)


class WorktreeRefusals(DispatchTest):
    """herdr's own refusals, turned into something the captain can act on. Each of
    these is a raw code that says nothing about what to do next."""

    def test_a_project_git_cannot_branch_says_how_to_record_it(self):
        self.project("proj", path=self.work)
        self.herdr.reply("worktree.create",
                         HerdrError("not_git_worktree", "not a git worktree"))

        result = self.dispatch(self.task())

        self.assertIn("not a git repository", result.said)
        self.assertIn("worktree=false", result.said)

    def test_a_base_the_repository_does_not_have_is_named(self):
        # A QA task queued against a branch nobody can find is a green nobody should
        # trust, so this stops rather than branching from whatever is checked out.
        self.project("proj", path=self.work)
        self.herdr.reply("worktree.create",
                         HerdrError("bad_ref", "invalid reference: gone"))

        result = self.dispatch(self.task(base="gone"))

        self.assertIn("branches from gone", result.said)
        self.assertIn("is not there under that name", result.said)

    def test_a_worktree_that_already_exists_is_a_stop_not_a_reuse(self):
        # It may hold work nobody has landed. Removing it to get on with the
        # dispatch is exactly the silent loss this refuses.
        self.project("proj", path=self.work)
        self.herdr.reply("worktree.create",
                         HerdrError("exists", "worktree already exists"))
        task_id = self.task()

        result = self.dispatch(task_id)

        self.assertIn("already exists", result.said)
        self.assertIn("may hold unlanded work", result.said)
        # Refused before the claim, so nothing was taken from the queue either.
        self.assertEqual(self.record(task_id)["status"], "todo")

    def test_a_refusal_herdr_gives_no_hint_for_is_not_swallowed(self):
        self.project("proj", path=self.work)
        self.herdr.reply("worktree.create", HerdrError("disk_full", "no space left"))

        result = self.dispatch(self.task())

        self.assertIn("disk_full", result.said)


class CheckOwners(HerdrTest):
    """`--check` asking herdr whether each in-flight pane still holds its minion.

    A dead minion is the one failure the fleet cannot see: it appends nothing, so
    the watcher never fires, and its task sits in `doing` with everything behind it
    waiting. This only reports, because reclaiming may discard unlanded work."""

    def doing(self, task_id, owner):
        return {"id": task_id, "status": "doing", "owner": owner}

    def check(self, *records):
        if records:
            self.store("tasks.jsonl", *records)
        buf = io.StringIO()
        with mock.patch.dict(os.environ, self.socket_env()), redirect_stdout(buf):
            rc = d.check_owners(self.at("tasks.jsonl"))
        return rc, buf.getvalue()

    def test_nothing_in_flight_is_not_a_fault(self):
        rc, out = self.check()
        self.assertEqual(rc, 0)
        self.assertIn("in flight  nothing", out)
        self.assertEqual(self.herdr.calls, [])

    def test_a_live_minion_reads_as_ok_with_the_status_herdr_reports(self):
        self.herdr.reply("agent.get", seen(status="working"))
        rc, out = self.check(self.doing("t1", "claude@w1:p1"))
        self.assertEqual(rc, 0)
        self.assertIn("ok      t1 -> claude@w1:p1 (working)", out)
        # Asked about the pane, never about the label: herdr's labels are not unique.
        self.assertEqual(self.herdr.calls_to("agent.get"), [{"target": "w1:p1"}])

    def test_a_pane_herdr_has_no_agent_in_reads_as_gone(self):
        self.herdr.reply("agent.get", HerdrError("no_agent", "no agent in w1:p1"))
        rc, out = self.check(self.doing("t1", "claude@w1:p1"))
        self.assertEqual(rc, 1)
        self.assertIn("GONE    t1", out)
        self.assertIn("reset t1 --reason", out)

    def test_a_pane_that_now_holds_a_different_agent_reads_as_gone(self):
        self.herdr.reply("agent.get", seen(kind="pi"))
        rc, out = self.check(self.doing("t1", "claude@w1:p1"))
        self.assertEqual(rc, 1)
        self.assertIn("now holds pi, not claude", out)

    def test_gone_is_always_qualified_by_a_herdr_that_may_have_restarted(self):
        # herdr loses its pane metadata on restart and re-detects from the pane, so
        # it truthfully reports no agent where a live minion is working. GONE is a
        # reading of this moment and never a verdict.
        self.herdr.reply("agent.get", HerdrError("no_agent", "no agent"))
        _, out = self.check(self.doing("t1", "claude@w1:p1"))
        self.assertIn("rerun before acting on this", out)

    def test_an_owner_that_names_no_pane_is_broken_and_herdr_is_not_asked(self):
        # Nothing can find this minion, so there is nothing to ask herdr about.
        rc, out = self.check(self.doing("t1", "minion-3"))
        self.assertEqual(rc, 1)
        self.assertIn("BROKEN  t1", out)
        self.assertIn("claimed outside dispatch", out)
        self.assertEqual(self.herdr.calls_to("agent.get"), [])

    def test_a_herdr_that_never_answers_checks_nothing_and_says_so(self):
        # The one thing worse than not knowing is saying a live fleet is dead. The
        # count is reported, and not one of them is called alive or gone.
        self.herdr.stop()
        rc, out = self.check(self.doing("t1", "claude@w1:p1"),
                             self.doing("t2", "claude@w1:p2"))
        self.assertEqual(rc, 1)
        self.assertIn("unknown 2 in flight, none checked", out)
        self.assertNotIn("GONE", out)
        self.assertNotIn("ok      t1", out)

    def test_herdr_going_away_partway_leaves_the_rest_unchecked_not_dead(self):
        # Reachability is asked once, so a herdr that stops mid-sweep is only
        # discovered here. Every task after it is unknown, and saying so is the
        # whole difference between a rerun and a captain resetting live work.
        self.herdr.reply("agent.get", seen(status="working"), CLOSE)
        rc, out = self.check(self.doing("t1", "claude@w1:p1"),
                             self.doing("t2", "claude@w1:p2"),
                             self.doing("t3", "claude@w1:p3"))
        self.assertEqual(rc, 1)
        self.assertIn("ok      t1", out)
        self.assertIn("unknown t2 and every task after it, unchecked", out)
        self.assertNotIn("GONE", out)
        self.assertNotIn("t3", out)
        # Stopped asking, rather than working through the rest and calling them dead.
        self.assertEqual(len(self.herdr.calls_to("agent.get")), 2)

    def test_a_dead_minion_does_not_hide_the_live_ones_behind_it(self):
        self.herdr.reply("agent.get", HerdrError("no_agent", "no agent"), seen())
        rc, out = self.check(self.doing("t1", "claude@w1:p1"),
                             self.doing("t2", "claude@w1:p2"))
        self.assertEqual(rc, 1)
        self.assertIn("GONE    t1", out)
        self.assertIn("ok      t2", out)


class CheckReadsWhatDispatchWrites(DispatchTest):
    """The two halves of `--check` and of a dispatch have to agree about a pane."""

    def test_the_owner_a_dispatch_writes_is_one_check_can_read(self):
        # An owner format only one of the two understood would make every live minion
        # read as BROKEN, with nothing anywhere saying why.
        self.shared_project()
        self.herdr.reply("agent.get", seen(), seen(status="working"))
        task_id = self.task()
        self.assertIsNone(self.dispatch(task_id).refusal)

        self.herdr.reply("agent.get", seen(status="working"))
        buf = io.StringIO()
        with mock.patch.dict(os.environ, self.socket_env()), redirect_stdout(buf):
            rc = d.check_owners(self.at("tasks.jsonl"))

        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn(f"ok      {task_id} -> claude@w1:p1", buf.getvalue())

    def test_the_reset_check_prescribes_for_a_dead_minion_runs(self):
        # `--check` only reports, so the command it prints is the whole of what it
        # offers. It carries the store the check read and a reason saying what the
        # check saw, because a captain typing it back has neither to hand.
        self.shared_project()
        self.herdr.reply("agent.get", seen(), seen(status="working"))
        task_id = self.task()
        self.assertIsNone(self.dispatch(task_id).refusal)

        self.herdr.reply("agent.get", VANISHED)
        buf = io.StringIO()
        with mock.patch.dict(os.environ, self.socket_env()), redirect_stdout(buf):
            rc = d.check_owners(self.at("tasks.jsonl"))

        self.assertEqual(rc, 1, buf.getvalue())
        self.assertIn("no claude agent in w1:p1", buf.getvalue())
        command = shlex.split(next(line.strip() for line in buf.getvalue().splitlines()
                                   if "reset" in line))
        self.assertIn(self.at("tasks.jsonl"), command)
        self.assertAccepted(self.run_cmd(command))
        self.assertEqual(self.record(task_id)["status"], "todo")


class Transport(HerdrTest):
    """The socket itself. This is where `Unreachable` is decided, and a reader that
    gets it wrong reports a live fleet as dead."""

    def herdr_client(self):
        return d.Herdr(self.herdr.path, timeout=5.0)

    def test_a_reply_too_big_for_one_read_is_assembled_before_it_is_parsed(self):
        self.herdr.reply("agent.get", {"agent": {"agent": "x" * 200_000}})
        self.assertEqual(len(self.herdr_client().call("agent.get")["agent"]["agent"]),
                         200_000)

    def test_an_error_reply_is_a_refusal_that_names_the_code(self):
        self.herdr.reply("agent.get", HerdrError("no_such_pane", "no pane w9:p9"))
        with self.assertRaises(d.Refusal) as cm:
            self.herdr_client().call("agent.get", target="w9:p9")
        self.assertNotIsInstance(cm.exception, d.Unreachable)
        self.assertIn("no_such_pane", cm.exception.message)

    def test_a_connection_closed_mid_request_is_unreachable_not_a_refusal(self):
        # An answer about herdr, and about no pane at all. Reading it as an answer
        # about the pane is how a live minion is reported dead.
        self.herdr.reply("agent.get", CLOSE)
        with self.assertRaises(d.Unreachable):
            self.herdr_client().call("agent.get", target="w1:p1")

    def test_a_herdr_that_takes_the_request_and_then_says_nothing_is_unreachable(self):
        # Wedged, or a listener bound by a server still starting. Without the
        # timeout this parks inside `recv` for as long as that lasts.
        self.herdr.reply("agent.get", lambda _p: (time.sleep(0.3), {})[1])
        with self.assertRaises(d.Unreachable) as cm:
            d.Herdr(self.herdr.path, timeout=0.05).call("agent.get")
        self.assertIn("agent.get", cm.exception.message)

    def test_a_socket_that_is_not_there_is_unreachable(self):
        self.herdr.stop()
        with self.assertRaises(d.Unreachable) as cm:
            self.herdr_client().call("workspace.list")
        self.assertIn("cannot reach herdr", cm.exception.message)


class SemanticContext(DispatchTest):
    """Dispatch's half of the semantic exchange.

    The pack is exported and pinned before the workspace, the claim and the agent,
    because a pack that will not verify has to stop the dispatch rather than arrive
    after a minion is already working. `test_semantic.py` covers what is pinned and
    what is refused; this covers where in a dispatch it happens.
    """

    CONTENT = "<https://semantic-layer.19h09.co/l2/x> <https://y> <https://z> .\n"
    MANIFEST = '{\n  "api_root": "https://api.github.com"\n}\n'
    SOURCE = "https://semantic-layer.19h09.co/l2/github/api-github-com"
    TARGET = "vvonkledge/siana"

    def setUp(self):
        super().setUp()
        self.contract("semantic")
        self.provider = self.at("provider")
        self.pack_dir = os.path.join(self.provider, "packs", "p")
        os.makedirs(self.pack_dir)
        self.project("prov", path=self.provider)
        bindir = self.at("fakebin")
        os.makedirs(bindir)
        target = os.path.join(bindir, "semantic-layer")
        shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "fake_semantic.py"), target)
        os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR)
        self.layer = bindir
        self.plan_path = self.at("plan.json")
        self.calls_path = self.at("calls.jsonl")

    def bind(self):
        args = ["project=proj", "provider=prov", "pack=packs/p",
                f"source={self.SOURCE}", f"target={self.TARGET}",
                "agent=https://semantic-layer.19h09.co/biz/agent/siana",
                f"store={self.at('trace.sqlite3')}"]
        out = self.run_cmd(["datafile", "-f", self.at("semantic.jsonl"),
                            "-c", self.at("schema-semantic.yaml"), "put",
                            *sum((["--set", a] for a in args), [])])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def export(self, **over):
        """An answer about whatever instant the dispatch actually asked about.

        A real dispatch reads the clock, so nothing here can know the instant in
        advance: `fake_semantic` substitutes it, and the freshness window is wide
        enough to hold any clock this suite runs under."""
        return fake_semantic.export(
            fake_semantic.AS_OF, self.CONTENT, self.MANIFEST, source=self.SOURCE,
            target=self.TARGET, observed_at="2020-01-01T00:00:00Z",
            fresh_until="2099-01-01T00:00:00Z", **over)

    def scripted(self, entry):
        with open(self.plan_path, "w") as fh:
            json.dump({"pack export": entry}, fh)

    def env(self):
        return {"PATH": self.layer + os.pathsep + os.environ["PATH"],
                "FAKE_SEMANTIC_PLAN": self.plan_path,
                "FAKE_SEMANTIC_CALLS": self.calls_path}

    def calls(self):
        try:
            with open(self.calls_path) as fh:
                return [json.loads(line) for line in fh if line.strip()]
        except FileNotFoundError:
            return []

    def test_an_unbound_project_dispatches_exactly_as_it_did_before(self):
        # The state every project is in until the captain binds one. Nothing is run,
        # nothing is written, and the orders are the file the distro shipped.
        self.shared_project()
        self.herdr.reply("agent.get", seen(), seen(status="working"))
        self.scripted({"doc": fake_semantic.response("pack export", self.export())})
        task_id = self.task()

        result = self.dispatch(task_id, env=self.env())

        self.assertIsNone(result.refusal, result.err)
        self.assertEqual(result.binding["orders"], self.at("orders.md"))
        self.assertIsNone(result.binding["semantic"])
        self.assertEqual(self.calls(), [])
        self.assertFalse(os.path.exists(self.at("semantic")))

    def test_a_bound_project_briefs_its_minion_from_the_pinned_copy(self):
        self.shared_project()
        self.bind()
        self.herdr.reply("agent.get", seen(), seen(status="working"))
        self.scripted({"doc": fake_semantic.response("pack export", self.export())})
        task_id = self.task()

        result = self.dispatch(task_id, env=self.env())

        self.assertIsNone(result.refusal, result.err)
        self.assertEqual(result.binding["semantic"], self.at("semantic", task_id))
        with open(result.binding["orders"]) as fh:
            orders = fh.read()
        self.assertIn("# Semantic context", orders)
        self.assertIn(self.at("semantic", task_id, "pack", "content.nt"), orders)
        # The claim landed, so the pin is marked as one a minion really worked
        # against. Without that mark nothing would ever record what this run did.
        self.assertTrue(os.path.exists(
            self.at("semantic", task_id, "dispatched")))

    def test_a_pack_that_will_not_verify_stops_it_before_anything_exists(self):
        # Before the workspace, before the claim, before the agent. A minion briefed
        # from nothing looks exactly like a minion briefed from something, and it is
        # the second one this whole exchange exists to produce.
        self.shared_project()
        self.bind()
        self.scripted({"doc": fake_semantic.response(
            "pack export", error={"kind": "pack", "message": "the pack is stale"}),
            "exit": 1})
        task_id = self.task()

        result = self.dispatch(task_id, env=self.env())

        self.assertIsNotNone(result.refusal)
        self.assertIn("the pack is stale", result.said)
        self.assertEqual(self.herdr.calls, [])
        self.assertEqual(self.record(task_id)["status"], "todo")
        self.assertFalse(os.path.exists(self.at("semantic", task_id)))

    def test_a_distro_missing_the_command_refuses_rather_than_going_quiet(self):
        # The two ship together, so this is a broken checkout. It is a refusal and
        # never a silent disable: only that command knows whether this project has
        # a binding, so skipping it would look exactly like a project that has none.
        self.shared_project()
        self.bind()
        task_id = self.task()

        with mock.patch.object(d, "SEMANTIC", self.at("gone")):
            result = self.dispatch(task_id, env=self.env())

        self.assertIsNotNone(result.refusal)
        self.assertIn("no siana-semantic beside", result.said)
        self.assertEqual(self.herdr.calls, [])
        self.assertEqual(self.record(task_id)["status"], "todo")

    def test_a_claim_given_up_again_leaves_the_pin_unclaimed(self):
        # `agent.start` is the first thing that can refuse holding a claim, and the
        # dispatch gives the claim back. No minion ran, so the mark has to go with
        # it: left standing it says a run began here, and reconcile would report
        # that run's ending as lost on every wake, forever, about an execution that
        # never happened.
        self.shared_project()
        self.bind()
        self.herdr.reply("agent.start", TAKEN)
        self.scripted({"doc": fake_semantic.response("pack export", self.export())})
        task_id = self.task()

        result = self.dispatch(task_id, env=self.env())

        self.assertIsNotNone(result.refusal)
        self.assertEqual(self.record(task_id)["status"], "todo")
        self.assertTrue(os.path.exists(self.at("semantic", task_id, "pin.json")))
        self.assertFalse(os.path.exists(self.at("semantic", task_id, "dispatched")))

    def test_the_pin_a_released_claim_left_is_the_one_the_retry_uses(self):
        # And the whole point of taking the mark back: the pin is unclaimed again,
        # so the retry reuses it rather than minting a second pin and a second
        # trace id for an interruption that never produced a run.
        self.shared_project()
        self.bind()
        self.herdr.reply("agent.start", TAKEN)
        self.scripted({"doc": fake_semantic.response("pack export", self.export())})
        task_id = self.task()
        self.dispatch(task_id, env=self.env())
        with open(self.at("semantic", task_id, "pin.json")) as fh:
            first = json.load(fh)["trace_id"]

        self.herdr.reply("workspace.create", {"workspace": {"workspace_id": "ws2"},
                                              "root_pane": {"pane_id": "w2:p1"}})
        self.herdr.reply("agent.start", {})
        self.herdr.reply("agent.get", seen(), seen(status="working"))
        result = self.dispatch(task_id, env=self.env())

        self.assertIsNone(result.refusal, result.said + result.err)
        with open(self.at("semantic", task_id, "pin.json")) as fh:
            self.assertEqual(json.load(fh)["trace_id"], first)
        self.assertFalse(os.path.exists(self.at("semantic", f"{task_id}.1")))
        self.assertEqual(len(self.calls()), 1)

    def test_a_pin_whose_claim_never_landed_is_not_marked_as_dispatched(self):
        # The queue refuses a task another minion already holds, and the pin was
        # written before the claim was tried. It is kept, because a re-dispatch has
        # to land on the same bytes and the same trace id - but it is not marked,
        # because no minion ever worked against it.
        self.shared_project()
        self.bind()
        self.scripted({"doc": fake_semantic.response("pack export", self.export())})
        task_id = self.task()
        self.run_cmd(["tasks", "--file", self.at("tasks.jsonl"), "start", task_id,
                      "--owner", "claude@w9:p9"])

        result = self.dispatch(task_id, env=self.env())

        self.assertIsNotNone(result.refusal)
        self.assertTrue(os.path.exists(self.at("semantic", task_id, "pin.json")))
        self.assertFalse(os.path.exists(self.at("semantic", task_id, "dispatched")))


if __name__ == "__main__":
    unittest.main()
