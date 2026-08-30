"""siana-clean: the delegated cleanup run, and the protocol that keeps it safe.

Everything here drives the real command as a real process against a real filesystem.
The model is the one thing scripted, by `tests/fake_cleaner.py` installed as a file
named `pi` on the front of the PATH, for the reason `tests/fake_pi.mjs` gives about
the harness it fakes: a live pi wants a terminal, a model and the captain's
credentials, so a suite that drove one would spend money on every run and would still
be unable to script the four cases that actually matter - a cleaner that asks and
stops, one that dies mid-round, one that never returns, and one whose output is not
JSON at all.

So the process group, the signals, the atomic writes, the lock, the guard shims and
the runbook are all the ones that ship. Nothing here stubs a store.
"""

import json
import os
import shutil
import signal
import subprocess
import time
import unittest

from helpers import BIN, TEMPLATE, HomeTest, gone_pid, script, until

c = script("siana-clean")

# The clauses `siana-reap` is guarded with, which is the one command needing two:
# refused outright without the grant, and refused its `--yes` with it.
GUARD_REAP = c.GUARDS["siana-reap"]

# A round that finishes and reports, which is the ordinary case every other script
# here is a variation on.
QUIET = {"steps": [{"say": "Inventoried 3 worktrees. Nothing outstanding."}],
         "exit": 0}


class Shims(unittest.TestCase):
    """The guard, as text. Generated per run out of the grant, so what it refuses is
    a pure function of its arguments and belongs under a direct test rather than
    behind a spawned agent."""

    QUESTION = "/nowhere/question.json"

    def shim(self, name, clauses, real, grants):
        return c.shim(name, clauses, real, grants, self.QUESTION)

    def test_a_refusal_never_executes_its_own_message(self):
        # This is not hypothetical. The first version wrote the message into a
        # double-quoted `echo`, and the refusal for `herdr` contains the words
        # `siana-retire` in backticks, so every refused herdr call ran siana-retire.
        # A guard that executes its own refusal text is worse than no guard.
        body = self.shim("herdr", (("words:close", "removal is `siana-retire`'s"),),
                         "/bin/echo", ["inventory"])
        self.assertNotIn('echo "', body)
        self.assertIn("printf '%s\\n'", body)
        self.assertIn("'\\''", body)     # the apostrophe, escaped rather than eaten

    def test_a_granted_command_passes_straight_through(self):
        body = self.shim("siana-retire", (("grant:retire", "no"),), "/bin/true",
                         ["inventory", "retire"])
        self.assertIn("exec '/bin/true' \"$@\"", body)
        self.assertNotIn("is not this cleanup run's to call", body)

    def test_an_ungranted_command_never_reaches_the_real_one(self):
        body = self.shim("siana-retire", (("grant:retire", "no"),), "/bin/true",
                         ["inventory"])
        self.assertIn("refused", body)
        self.assertNotIn("exec", body)

    def test_a_command_that_is_not_installed_is_still_refused(self):
        # Absent today and installed tomorrow must not become reachable to a run
        # that was already refused it.
        body = self.shim("siana-publish", (("always", "no"),), None, ["inventory"])
        self.assertIn("refused", body)
        self.assertNotIn("exec", body)

    def test_the_git_pair_rule_reads_the_word_after_the_verb(self):
        body = self.shim("git", (("words:push", "no"),), "/bin/true", ["inventory"])
        self.assertIn("'worktree') SAW=1 ;;", body)
        self.assertIn("'add'|'remove'|'prune'|'move'|'repair') BAD=1 ;;", body)

    def test_a_delegated_command_is_exec_with_the_guard_off_its_path(self):
        # `siana-retire` ends with `git worktree remove`, and the guard refuses that.
        # It is the safety boundary this whole design defers to, so it reaches the
        # real tools; the guard is there to stop the cleaner reaching them directly.
        body = self.shim("siana-retire", (("grant:retire", "no"),), "/bin/true",
                         ["inventory", "retire"])
        self.assertIn("PATH=", body)
        self.assertIn("exec '/bin/true'", body)

    def test_a_primitive_is_not_exempted_that_way(self):
        body = self.shim("git", (("words:push", "no"),), "/bin/true", ["inventory"])
        self.assertNotIn("PATH=", body)

    def test_a_second_clause_still_applies_once_the_first_is_granted(self):
        # `siana-reap` is the reason clauses are a list. With one clause it had only
        # the flag rule, so the `reap-report` grant unlocked nothing while four
        # documents said it did.
        body = self.shim("siana-reap", GUARD_REAP, "/bin/true",
                         ["inventory", "reap-report"])
        self.assertIn("'--yes') BAD=1 ;;", body)
        self.assertIn("exec '/bin/true'", body)

    def test_an_ungranted_first_clause_refuses_before_the_second_is_read(self):
        body = self.shim("siana-reap", GUARD_REAP, "/bin/true", ["inventory"])
        self.assertIn("does not include reaping", body)
        self.assertNotIn("--yes", body)
        self.assertNotIn("exec", body)

    def test_every_shim_refuses_while_a_question_is_waiting(self):
        # The guarantee is that nothing after the uncertain point runs, and until
        # this existed the only thing holding it was a sentence in a prompt.
        for name, clauses in c.GUARDS.items():
            body = self.shim(name, clauses, "/bin/true", list(c.GRANTS))
            if "exec" not in body:
                continue          # refused outright; the gate is beside the point
            self.assertIn(f"if [ -e '{self.QUESTION}' ]; then", body, name)
            self.assertLess(body.index("question is waiting"), body.index("exec"),
                            name)


class Runbook(unittest.TestCase):
    """The durable gotchas. Nothing may reach this file except a question a cleaner
    wrote down and an answer SIANA recorded, so its whole surface is two strings."""

    def setUp(self):
        self.dir = os.path.join(os.environ.get("TMPDIR", "/tmp"),
                                f"siana-runbook-{os.getpid()}")
        os.makedirs(self.dir, exist_ok=True)
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = os.path.join(self.dir, "runbook.md")

    def test_appending_the_same_question_twice_writes_once(self):
        qid = "clean-20260101-000000-q1"
        self.assertTrue(c.runbook_append(self.path, qid, "Is it?", "No."))
        self.assertFalse(c.runbook_append(self.path, qid, "Is it?", "No."))
        with open(self.path) as fh:
            self.assertEqual(fh.read().count(qid), 1)

    def test_the_same_wording_under_a_different_id_is_a_second_entry(self):
        # Two runs can legitimately reach the same wording about different
        # worktrees, and de-duplicating on prose would silently drop the second.
        c.runbook_append(self.path, "clean-20260101-000000-q1", "Is it?", "No.")
        self.assertTrue(c.runbook_append(self.path, "clean-20260102-000000-q1",
                                         "Is it?", "Yes."))
        with open(self.path) as fh:
            text = fh.read()
        self.assertIn("No.", text)
        self.assertIn("Yes.", text)

    def test_initialising_an_existing_runbook_never_touches_it(self):
        c.runbook_append(self.path, "clean-20260101-000000-q1", "Is it?", "No.")
        c.runbook_init(self.path)
        with open(self.path) as fh:
            self.assertIn("No.", fh.read())


class AssistantText(unittest.TestCase):
    """What is read out of pi's JSON stream. A partial line at the end of a killed
    process is the ordinary way that stream ends, so malformed output must be skipped
    rather than reported - and nothing may ever be acted on from it."""

    def one(self, text):
        return c.assistant_text(json.dumps(
            {"type": "message_end",
             "message": {"role": "assistant",
                         "content": [{"type": "text", "text": text}]}}))

    def test_the_assistant_text_is_what_comes_out(self):
        self.assertEqual(self.one("the report"), "the report")

    def test_a_half_written_line_is_skipped_and_not_a_fault(self):
        self.assertIsNone(c.assistant_text('{"type": "message_e'))

    def test_a_line_that_is_not_json_at_all_is_skipped(self):
        self.assertIsNone(c.assistant_text("Traceback (most recent call last):"))

    def test_a_user_message_is_not_the_report(self):
        self.assertIsNone(c.assistant_text(json.dumps(
            {"type": "message_end", "message": {"role": "user", "content": "hi"}})))

    def test_an_event_of_another_type_is_not_the_report(self):
        self.assertIsNone(c.assistant_text(json.dumps({"type": "turn_start"})))

    def test_a_content_list_of_something_else_yields_nothing(self):
        self.assertIsNone(c.assistant_text(json.dumps(
            {"type": "message_end",
             "message": {"role": "assistant",
                         "content": [{"type": "toolCall", "name": "bash"}]}})))


class Run(HomeTest):
    """The whole command, with the model scripted and everything else real."""

    def setUp(self):
        super().setUp()
        self.fakebin = self.at("fakebin")
        os.makedirs(self.fakebin, exist_ok=True)
        shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "fake_cleaner.py"),
                    os.path.join(self.fakebin, "pi"))
        os.chmod(os.path.join(self.fakebin, "pi"), 0o755)
        self.script_path = self.at("fake-pi.json")
        self.write_script(QUIET)

    def write_script(self, script_body):
        with open(self.script_path, "w") as fh:
            json.dump(script_body, fh)

    def calls(self):
        """What the fake pi was actually given, one record per invocation. The brief
        a resumed cleaner is handed and the guard it is started with are both things
        a test can only check by watching what arrived."""
        try:
            with open(self.script_path + ".calls") as fh:
                return [json.loads(line) for line in fh if line.strip()]
        except OSError:
            return []

    def clean(self, *args, extra=None, timeout=120):
        env = {"PATH": self.distro_path(self.fakebin),
               "SIANA_FAKE_PI": self.script_path}
        env.update(extra or {})
        return self.run_cmd([os.path.join(BIN, "siana-clean"), *args], env=env,
                            timeout=timeout)

    def runs(self):
        return sorted(os.listdir(self.at("cleanup", "runs")))

    def only_run(self):
        ids = self.runs()
        self.assertEqual(len(ids), 1, ids)
        return ids[0]

    def record(self, run_id):
        with open(self.at("cleanup", "runs", run_id, "run.json")) as fh:
            return json.load(fh)

    # -- the ordinary round ------------------------------------------------

    def test_a_quiet_round_finishes_and_reports(self):
        out = self.clean("start")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("nothing outstanding", out.stdout)
        self.assertIn("Inventoried 3 worktrees", out.stdout)
        self.assertEqual(self.record(self.only_run())["status"], "finished")

    def test_the_run_reports_a_durable_identity(self):
        out = self.assertAccepted(self.clean("start"))
        run_id = self.only_run()
        self.assertIn(run_id, out)
        self.assertIn(run_id, self.assertAccepted(self.clean("status")))

    def test_starting_never_happens_by_merely_reading_status(self):
        # Nothing starts a model except `start` and `resume`. `status` on an empty
        # home is a zero and not a fault, and it must not create a run to say so.
        out = self.assertAccepted(self.clean("status"))
        self.assertIn("no runs", out)
        self.assertEqual(self.calls(), [])

    def test_the_default_grant_is_inventory_and_nothing_else(self):
        self.assertAccepted(self.clean("start"))
        self.assertEqual(self.record(self.only_run())["grants"], ["inventory"])
        self.assertEqual(self.calls()[0]["grants"], "inventory")

    def test_an_unknown_grant_is_refused_before_anything_starts(self):
        out = self.clean("start", "--grant", "everything")
        self.assertRefused(out, "no such grant", "inventory, retire, reap-report")
        self.assertEqual(self.calls(), [])

    def test_the_child_never_inherits_a_minion_task_id(self):
        # `siana-afk` refuses to activate in a minion's environment by reading this,
        # and a cleaner wearing one would be carrying a task id that is not its.
        self.assertAccepted(self.clean("start", extra={"SIANA_TASK_ID": "some-task"}))
        self.assertEqual(self.calls()[0]["task"], "")

    def test_the_cleaner_is_given_the_definition_the_distro_ships(self):
        self.assertAccepted(self.clean("start"))
        argv = self.calls()[0]["argv"]
        self.assertIn("--append-system-prompt", argv)
        system = argv[argv.index("--append-system-prompt") + 1]
        with open(system) as fh:
            given = fh.read()
        with open(os.path.join(TEMPLATE, "pi-siana", "agents", "cleaner.md")) as fh:
            self.assertEqual(given, fh.read())

    def test_the_child_never_loads_sianas_own_project_files(self):
        # The child runs in the home, which is SIANA's project directory. It holds
        # `AGENTS.md`, which is SIANA's instructions, and `.pi/extensions/wake.ts`,
        # which consumes the watcher's counter. A cleaner that loaded the first would
        # believe it was SIANA; one that loaded the second would take SIANA's wakes
        # and nothing would ever notice, because a taken wake looks like a delivered
        # one.
        self.assertAccepted(self.clean("start"))
        argv = self.calls()[0]["argv"]
        self.assertIn("--no-approve", argv)
        self.assertIn("-nc", argv)

    def test_the_child_runs_in_the_home_so_the_stores_are_where_it_looks(self):
        self.assertAccepted(self.clean("start"))
        self.assertEqual(os.path.realpath(self.calls()[0]["cwd"]),
                         os.path.realpath(self.home))

    def test_the_queue_is_readable_from_inside_a_run(self):
        # `tasks` finds `datafile` on PATH and imports the file it finds as a python
        # module, so the guard's `/bin/sh` shim made every `tasks` call in a run die
        # on a loader error. A live cleaner found this and worked around it, which
        # cost a round that could not read the queue at all.
        self.queue()
        self.write_script({"steps": [{"run": ["tasks"]}], "exit": 0})
        out = self.assertAccepted(self.clean("start"))
        self.assertIn("ran tasks, exit 0", out)
        self.assertNotIn("Traceback", out)

    def test_the_child_is_told_which_run_it_is(self):
        self.assertAccepted(self.clean("start"))
        self.assertEqual(self.calls()[0]["run"], self.only_run())

    # -- asking, answering, resuming ---------------------------------------

    def ask_script(self, body="Is the .env in that tree disposable?", kind="siana"):
        return {"steps": [{"ask": {"body": body, "kind": kind,
                                   "options": ["Keep it", "Remove it"]}},
                          {"say": "Stopped on a question."}], "exit": 0}

    def test_a_question_returns_control_with_exit_three(self):
        self.write_script(self.ask_script())
        out = self.clean("start")
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        self.assertIn("Is the .env in that tree disposable?", out.stdout)
        self.assertIn("Keep it", out.stdout)

    def test_the_question_is_on_disk_before_anybody_reads_it(self):
        # `ask` writes it and returns, and only then is the cleaner allowed to stop.
        # So the file exists whether or not the round that asked it ever finished.
        self.write_script({"steps": [{"ask": {"body": "Is it?", "kind": "siana"}},
                                     {"die": int(signal.SIGKILL)}], "exit": 0})
        out = self.clean("start")
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        run_id = self.only_run()
        self.assertTrue(os.path.exists(
            self.at("cleanup", "runs", run_id, "question.json")))
        self.assertEqual(self.record(run_id)["status"], "question")

    def test_a_pending_question_survives_a_restart(self):
        # Nothing is held in memory. A second process, started from nothing, reads
        # the same question out of the same file.
        self.write_script(self.ask_script())
        self.clean("start")
        out = self.clean("status")
        self.assertEqual(out.returncode, 3)
        self.assertIn("Is the .env in that tree disposable?", out.stdout)

    def test_resuming_before_answering_is_refused(self):
        # Nothing after the uncertain point runs until an answer is durably
        # recorded, and this is the check that makes that a property rather than a
        # hope.
        self.write_script(self.ask_script())
        self.clean("start")
        run_id = self.only_run()
        before = len(self.calls())
        out = self.clean("resume", run_id)
        self.assertRefused(out, "still waiting for an answer")
        self.assertEqual(len(self.calls()), before)

    def test_answering_records_the_answer_and_the_runbook(self):
        self.write_script(self.ask_script())
        self.clean("start")
        run_id = self.only_run()
        out = self.assertAccepted(
            self.clean("answer", run_id, "--text", "Never. Refuse and let a human "
                       "clear it."))
        self.assertIn("runbook written", out)
        with open(self.at("runbook.md")) as fh:
            book = fh.read()
        self.assertIn("Is the .env in that tree disposable?", book)
        self.assertIn("Never. Refuse and let a human clear it.", book)

    def test_answering_twice_writes_the_runbook_once(self):
        self.write_script(self.ask_script())
        self.clean("start")
        run_id = self.only_run()
        self.assertAccepted(self.clean("answer", run_id, "--text", "Refuse it."))
        out = self.clean("answer", run_id, "--text", "Refuse it.")
        self.assertRefused(out, "not waiting on a question")
        with open(self.at("runbook.md")) as fh:
            self.assertEqual(fh.read().count("Refuse it."), 1)

    def test_an_empty_answer_is_refused(self):
        self.write_script(self.ask_script())
        self.clean("start")
        out = self.clean("answer", self.only_run(), "--text", "   ")
        self.assertRefused(out, "what is the answer to")

    def test_resume_carries_the_answer_and_the_earlier_report_forward(self):
        self.write_script(self.ask_script())
        self.clean("start")
        run_id = self.only_run()
        self.clean("answer", run_id, "--text", "Refuse it and report.")
        self.write_script({"steps": [{"say": "Carried on. Nothing outstanding."}],
                           "exit": 0})
        out = self.assertAccepted(self.clean("resume", run_id))
        self.assertIn("round  2", out)
        # What the resumed child was actually handed: the answer, and the previous
        # round's report. Never the previous round's transcript.
        brief = self.calls()[-1]["argv"][-1]
        self.assertIn("Refuse it and report.", brief)
        self.assertIn("Stopped on a question.", brief)
        self.assertNotIn("message_end", brief)

    def test_resuming_twice_off_one_answer_is_refused(self):
        # The duplicate-delivery failure. The answers log is append-only and says
        # nothing about which of its entries a round has already been given.
        self.write_script(self.ask_script())
        self.clean("start")
        run_id = self.only_run()
        self.clean("answer", run_id, "--text", "Refuse it.")
        self.write_script(QUIET)
        self.assertAccepted(self.clean("resume", run_id))
        rounds = len(self.calls())
        out = self.clean("resume", run_id)
        self.assertRefused(out, "already carried", "already used")
        self.assertEqual(len(self.calls()), rounds)

    def test_resuming_a_run_that_never_asked_anything_is_refused(self):
        self.assertAccepted(self.clean("start"))
        out = self.clean("resume", self.only_run())
        self.assertRefused(out, "no answered question to carry on from",
                           "siana-clean start")

    def test_the_runbook_reaches_the_next_run(self):
        self.write_script(self.ask_script())
        self.clean("start")
        run_id = self.only_run()
        self.clean("answer", run_id, "--text", "Refuse it and report.")
        self.clean("abort", run_id, "--reason", "done with this one")
        self.write_script(QUIET)
        self.assertAccepted(self.clean("start"))
        self.assertIn("Refuse it and report.", self.calls()[-1]["argv"][-1])

    # -- the captain's authority -------------------------------------------

    def test_a_captain_question_cannot_be_answered_by_siana(self):
        self.write_script(self.ask_script(kind="captain"))
        out = self.clean("start")
        self.assertEqual(out.returncode, 3)
        self.assertIn("this one is the captain's", out.stdout)
        refused = self.clean("answer", self.only_run(), "--text", "go ahead")
        self.assertRefused(refused, "the captain's, not SIANA's",
                           "siana-owe decision",
                           "cannot make captain authority")

    def decision(self, answer=None):
        """A real decision in the obligation store, written by `siana-owe` so that it
        passes the same contract a live one does. Returns its id."""
        self.contract("obligations", "attended")
        out = self.run_bin(
            "siana-owe", "decision", "Whether to reap the retained branches",
            "--situation", "Two are older than a week",
            "--option", "Reap them now", "--consequence", "Recovery is the forge",
            "--option", "Keep them", "--consequence", "The list grows",
            "--recommend", "Keep them", "--because", "A wrong reap loses work")
        self.assertAccepted(out)
        with open(self.at("obligations.jsonl")) as fh:
            oid = json.loads(fh.readline())["id"]
        if answer is not None:
            self.assertAccepted(
                self.run_bin("siana-owe", "close", oid, "--answer", answer))
        return oid

    def test_a_captain_question_is_answered_only_against_an_obligation(self):
        oid = self.decision(answer="the captain said reap them")
        self.write_script(self.ask_script(kind="captain"))
        self.clean("start")
        run_id = self.only_run()
        out = self.assertAccepted(
            self.clean("answer", run_id, "--text", "the captain said go ahead",
                       "--captain-decided", oid))
        self.assertIn("the captain said reap them", out)
        with open(self.at("cleanup", "runs", run_id, "answers.jsonl")) as fh:
            self.assertEqual(json.loads(fh.readline())["decision"], oid)

    def test_an_obligation_id_that_does_not_exist_is_refused(self):
        # Otherwise the whole captain path is a spelling convention: before this,
        # `--captain-decided anything` unblocked a captain question.
        self.contract("obligations")
        self.write_script(self.ask_script(kind="captain"))
        self.clean("start")
        out = self.clean("answer", self.only_run(), "--text", "go ahead",
                         "--captain-decided", "no-such-decision")
        self.assertRefused(out, "no obligation under that id")

    def test_a_decision_the_captain_has_not_answered_yet_is_refused(self):
        oid = self.decision()
        self.write_script(self.ask_script(kind="captain"))
        self.clean("start")
        out = self.clean("answer", self.only_run(), "--text", "go ahead",
                         "--captain-decided", oid)
        self.assertRefused(out, "still open", "manufacturing the authority")

    def test_a_promise_is_not_a_decision(self):
        self.contract("obligations")
        self.assertAccepted(self.run_bin("siana-owe", "promise", "Report at noon"))
        with open(self.at("obligations.jsonl")) as fh:
            oid = json.loads(fh.readline())["id"]
        self.assertAccepted(
            self.run_bin("siana-owe", "close", oid, "--answer", "the noon report"))
        self.write_script(self.ask_script(kind="captain"))
        self.clean("start")
        out = self.clean("answer", self.only_run(), "--text", "go ahead",
                         "--captain-decided", oid)
        self.assertRefused(out, "is a promise, not a decision")

    def test_answering_a_captain_question_never_closes_the_obligation(self):
        # Closing one stays `siana-owe`'s. A cleanup run that could retire a
        # decision by answering a cleaner would be the same manufactured authority
        # arriving from the other side.
        oid = self.decision(answer="reap them")
        self.write_script(self.ask_script(kind="captain"))
        self.clean("start")
        before = os.path.getsize(self.at("obligations.jsonl"))
        self.assertAccepted(self.clean("answer", self.only_run(), "--text", "go",
                                       "--captain-decided", oid))
        self.assertEqual(os.path.getsize(self.at("obligations.jsonl")), before)

    # -- one run at a time -------------------------------------------------

    def test_a_second_start_while_a_question_is_open_is_refused(self):
        self.write_script(self.ask_script())
        self.clean("start")
        out = self.clean("start")
        self.assertRefused(out, "still question", "one run at a time")
        self.assertEqual(len(self.runs()), 1)

    def test_a_second_start_after_a_finished_run_is_allowed(self):
        self.assertAccepted(self.clean("start"))
        self.assertAccepted(self.clean("start"))
        self.assertEqual(len(self.runs()), 2)

    def test_a_held_lock_refuses_and_names_the_holder(self):
        os.makedirs(self.at("cleanup"), exist_ok=True)
        holder = subprocess.Popen(["sleep", "60"])
        self.addCleanup(holder.kill)
        command = subprocess.run(["ps", "-p", str(holder.pid), "-o", "command="],
                                 capture_output=True, text=True).stdout.strip()
        with open(self.at("cleanup", "lock"), "w") as fh:
            json.dump({"pid": holder.pid, "command": command, "what": "start",
                       "taken": "2026-01-01T00:00:00+00:00"}, fh)
        out = self.clean("start")
        self.assertRefused(out, "already starting or resuming", str(holder.pid))
        self.assertEqual(self.calls(), [])

    def test_a_lock_left_by_a_dead_process_is_reclaimed(self):
        os.makedirs(self.at("cleanup"), exist_ok=True)
        with open(self.at("cleanup", "lock"), "w") as fh:
            json.dump({"pid": gone_pid(), "command": "sleep 60", "what": "start",
                       "taken": "2026-01-01T00:00:00+00:00"}, fh)
        self.assertAccepted(self.clean("start"))

    def test_a_lock_naming_a_live_pid_that_is_something_else_is_reclaimed(self):
        # Pids are reused. A lock is only held while the process wearing its pid is
        # still the one that recorded it, which is the check `siana-watch` makes of
        # its own grant, for the same reason.
        os.makedirs(self.at("cleanup"), exist_ok=True)
        other = subprocess.Popen(["sleep", "60"])
        self.addCleanup(other.kill)
        with open(self.at("cleanup", "lock"), "w") as fh:
            json.dump({"pid": other.pid, "command": "some other command",
                       "what": "start", "taken": "2026-01-01T00:00:00+00:00"}, fh)
        self.assertAccepted(self.clean("start"))

    def test_the_lock_is_released_when_a_run_finishes(self):
        self.assertAccepted(self.clean("start"))
        self.assertFalse(os.path.exists(self.at("cleanup", "lock")))

    # -- failure, and what it leaves behind ---------------------------------

    def test_no_pi_is_a_refusal_that_names_the_manual_route(self):
        # The captain's own pi is on this machine, and the first version of this
        # test left it on the PATH: it was found, and the suite made a live model
        # call while asserting that pi was missing. `path_without` mirrors each
        # directory without it rather than dropping the directory, so python3 and
        # `ps` are still there and the refusal under test is the one that fires.
        out = self.run_cmd([os.path.join(BIN, "siana-clean"), "start"],
                           env={"PATH": self.path_without("pi"),
                                "SIANA_FAKE_PI": self.script_path})
        self.assertRefused(out, "pi is not on PATH", "siana-retire")

    def test_a_child_that_exits_nonzero_leaves_the_run_failed_and_says_so(self):
        self.write_script({"steps": [{"say": "half a round"}], "exit": 7})
        out = self.clean("start")
        self.assertEqual(out.returncode, 1)
        self.assertIn("the cleaner exited 7", out.stdout)
        self.assertIn("nothing was left half-done", out.stdout)
        self.assertEqual(self.record(self.only_run())["status"], "failed")

    def test_a_child_that_is_killed_leaves_the_run_failed(self):
        self.write_script({"steps": [{"die": int(signal.SIGKILL)}], "exit": 0})
        out = self.clean("start")
        self.assertEqual(out.returncode, 1)
        self.assertEqual(self.record(self.only_run())["status"], "failed")

    def test_output_that_is_not_json_is_survived_and_never_acted_on(self):
        self.write_script({"steps": [{"raw": "Traceback (most recent call last):"},
                                     {"raw": '{"type": "message_e'},
                                     {"say": "the real report"}], "exit": 0})
        out = self.assertAccepted(self.clean("start"))
        self.assertIn("the real report", out)
        self.assertNotIn("Traceback", out)

    def test_a_round_that_never_returns_is_killed_and_reported(self):
        self.write_script({"steps": [{"say": "starting"}, {"sleep": 120}],
                           "exit": 0})
        started = time.time()
        out = self.clean("start", extra={"SIANA_CLEAN_ROUND_S": "1"}, timeout=90)
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("passed 0 minutes", out.stdout)
        self.assertLess(time.time() - started, 60)

    def test_a_killed_round_leaves_nothing_of_the_child_behind(self):
        # The whole process group, because pi spawns tools and a SIGTERM to the
        # parent alone leaves a `git` or a `herdr` holding the terminal.
        self.write_script({"steps": [{"say": "starting"}, {"sleep": 120}],
                           "exit": 0})
        self.clean("start", extra={"SIANA_CLEAN_ROUND_S": "1"}, timeout=90)
        self.assertTrue(until(lambda: not self.stray_children()),
                        "a child of the killed round is still running")

    def stray_children(self):
        out = subprocess.run(["ps", "-eo", "command="], capture_output=True,
                             text=True).stdout
        return [line for line in out.splitlines() if self.script_path in line]

    def test_an_interrupted_run_reads_as_interrupted_rather_than_running(self):
        # A process killed between spawning a child and recording its exit never
        # wrote anything, so the one status that could be a lie is checked against
        # the operating system every time it is read.
        self.assertAccepted(self.clean("start"))
        run_id = self.only_run()
        rec = self.record(run_id)
        rec.update(status="running", pid=gone_pid(), command="sleep 60")
        with open(self.at("cleanup", "runs", run_id, "run.json"), "w") as fh:
            json.dump(rec, fh)
        out = self.assertAccepted(self.clean("status"))
        self.assertIn("failed", out)
        self.assertIn("it was interrupted", out)

    def test_a_run_record_that_will_not_parse_is_a_stop(self):
        self.assertAccepted(self.clean("start"))
        run_id = self.only_run()
        with open(self.at("cleanup", "runs", run_id, "run.json"), "w") as fh:
            fh.write("{half a record")
        out = self.clean("status", run_id)
        self.assertRefused(out, "cannot read the run record", "not resumable")

    def test_an_unknown_run_id_lists_the_ones_there_are(self):
        self.assertAccepted(self.clean("start"))
        out = self.clean("status", "clean-19700101-000000")
        self.assertRefused(out, "no cleanup run under that id", self.only_run())

    def test_aborting_keeps_the_state_and_says_nothing_was_undone(self):
        self.write_script(self.ask_script())
        self.clean("start")
        run_id = self.only_run()
        out = self.clean("abort", run_id, "--reason", "the captain said stop")
        self.assertRefused(out, "aborted", "nothing was undone")
        self.assertEqual(self.record(run_id)["status"], "failed")
        self.assertTrue(os.path.exists(
            self.at("cleanup", "runs", run_id, "question.json")))

    # -- bounds -------------------------------------------------------------

    def test_a_round_that_floods_its_stream_is_truncated_and_says_so(self):
        big = "x" * 4096
        self.write_script({"steps": [{"say": big}] * 300 + [{"say": "the end"}],
                           "exit": 0})
        out = self.assertAccepted(self.clean("start"))
        self.assertIn("hit its cap and was truncated", out)
        stream = self.at("cleanup", "runs", self.only_run(), "round-1.jsonl")
        self.assertLess(os.path.getsize(stream), c.STREAM_CAP * 1.1)

    # -- the guard, from inside a real run ----------------------------------

    def probe(self, *argv):
        """Run one command from inside a cleanup round, and return what it said.
        Through the PATH the child was given, because what a shim does is only true
        if the child reaches the shim."""
        self.write_script({"steps": [{"run": list(argv)}], "exit": 0})
        out = self.clean("start")
        return out.stdout + out.stderr

    def test_the_ungranted_retire_is_refused_inside_the_run(self):
        said = self.probe("siana-retire", "some-task")
        self.assertIn("not this cleanup run's to call", said)
        self.assertIn("does not include retiring", said)

    def test_the_granted_retire_reaches_the_real_command(self):
        self.queue()
        self.store("tasks.jsonl", {"id": "a-task", "title": "A task",
                                   "status": "doing", "project": "siana"})
        self.write_script({"steps": [{"run": ["siana-retire", "a-task"]}],
                           "exit": 0})
        out = self.clean("start", "--grant", "retire")
        # The real command's own refusal, not the shim's. The whole point of the
        # grant is that the safety judgment stays where it already is: `doing` means
        # somebody is in that tree, and retire is what knows so.
        self.assertNotIn("not this cleanup run's to call", out.stdout)
        self.assertIn("a-task is still held by a minion", out.stdout)

    def test_a_delegated_command_reaches_the_real_git(self):
        # The test above stops at retire's first check, so for a long time it said
        # nothing about retire's last one. `siana-retire` ends with `git worktree
        # remove`, resolved through PATH, and the guard refuses exactly that: a
        # cleanup run passed every one of retire's own safety checks and then died on
        # the final line. The `retire` grant could not retire anything.
        #
        # Stood in for here, because what is under test is whether a delegated
        # command reaches the real git rather than what retire does with it.
        stand_in = os.path.join(self.fakebin, "siana-retire")
        with open(stand_in, "w") as fh:
            fh.write("#!/bin/sh\ngit worktree remove /nowhere 2>&1 | head -1\n")
        os.chmod(stand_in, 0o755)
        self.write_script({"steps": [{"run": ["siana-retire", "a-task"]}],
                           "exit": 0})
        out = self.clean("start", "--grant", "retire")
        self.assertNotIn("not this cleanup run's to call", out.stdout)
        # git's own complaint about the path, which is proof it was reached.
        self.assertIn("fatal", out.stdout)

    def test_the_cleaner_itself_still_cannot_reach_that_git(self):
        # The other half, and the reason the exemption is per command rather than
        # global: the guard exists to stop the cleaner calling git directly.
        self.assertIn("not this cleanup run's to call",
                      self.probe("git", "worktree", "remove", "/nowhere"))

    def test_reap_is_refused_outright_without_the_grant(self):
        # The grant has to unlock something, or SIANA choosing the narrowest one is
        # being told it withheld something it did not.
        self.assertIn("does not include reaping",
                      self.probe("siana-reap", "siana"))

    def test_reap_reaches_the_real_command_under_its_grant(self):
        self.write_script({"steps": [{"run": ["siana-reap", "siana"]}], "exit": 0})
        out = self.clean("start", "--grant", "reap-report")
        self.assertNotIn("not this cleanup run's to call", out.stdout)
        self.assertIn("unknown project", out.stdout)

    def test_reap_under_its_grant_still_refuses_the_flag(self):
        # Report-only first, and the flag is refused however it is spelled.
        for flag in ("--yes", "-y"):
            self.write_script({"steps": [{"run": ["siana-reap", "siana", flag]}],
                               "exit": 0})
            out = self.clean("start", "--grant", "reap-report")
            self.assertIn("reaping is the captain's", out.stdout, flag)

    def test_nothing_granted_is_callable_once_a_question_is_waiting(self):
        # The brief's guarantee, as a property of the mechanism rather than of the
        # cleaner's prompt: a cleaner that asked and then kept going is refused.
        self.queue()
        self.store("tasks.jsonl", {"id": "a-task", "title": "A task",
                                   "status": "done", "project": "siana"})
        self.write_script({"steps": [
            {"ask": {"body": "Is that tree's .env yours?", "kind": "siana"}},
            {"run": ["siana-retire", "a-task"]},
            {"say": "kept going after asking"}], "exit": 0})
        out = self.clean("start", "--grant", "retire")
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)
        stream = self.at("cleanup", "runs", self.only_run(), "round-1.jsonl")
        with open(stream) as fh:
            said = fh.read()
        self.assertIn("not callable while a question is waiting", said)
        self.assertNotIn("still held by a minion", said)

    def test_the_gate_lifts_once_the_question_is_answered(self):
        # A gate that never lifted would make a resumed run useless.
        self.write_script({"steps": [{"ask": {"body": "Is it?", "kind": "siana"}}],
                           "exit": 0})
        self.clean("start", "--grant", "retire")
        run_id = self.only_run()
        self.clean("answer", run_id, "--text", "Refuse it.")
        self.write_script({"steps": [{"run": ["siana-reap", "siana"]}], "exit": 0})
        out = self.assertAccepted(self.clean("resume", run_id))
        self.assertNotIn("while a question is waiting", out)

    def test_the_destructive_git_verbs_are_refused_and_the_readers_are_not(self):
        for argv in (["git", "push"], ["git", "worktree", "remove", "x"],
                     ["git", "branch", "-D", "x"], ["git", "reset", "--hard"]):
            self.assertIn("not this cleanup run's to call", self.probe(*argv), argv)
        for argv in (["git", "worktree", "list"], ["git", "status"]):
            self.assertNotIn("not this cleanup run's to call", self.probe(*argv),
                             argv)

    def test_herdr_removal_and_closing_are_refused(self):
        said = self.probe("herdr", "workspace", "close", "w1")
        self.assertIn("not this cleanup run's to call", said)
        self.assertIn("siana-retire", said)

    def test_writing_the_queue_is_refused_and_reading_it_is_not(self):
        self.assertIn("not this cleanup run's to call",
                      self.probe("tasks", "done", "x"))
        self.assertNotIn("not this cleanup run's to call",
                         self.probe("tasks", "list"))

    def test_a_nested_agent_is_refused(self):
        self.assertIn("does not start another agent", self.probe("pi", "-p", "hi"))

    def test_siana_clean_itself_is_reachable_so_the_cleaner_can_ask(self):
        # The one write a cleaner makes. A guard that refused this would make the
        # whole protocol unreachable from inside the run it governs.
        self.write_script(self.ask_script())
        out = self.clean("start")
        self.assertEqual(out.returncode, 3, out.stdout + out.stderr)

    def test_the_child_looks_pi_up_past_its_own_guard(self):
        # `pi` is refused to the child, so a run that resolved its own pi through
        # the guard could never start at all. That it started twice is the whole
        # proof, and the second round is the one that matters: a resume writes a new
        # guard, and a lookup that did not strip the previous one would find a shim
        # where pi should be.
        self.write_script(self.ask_script())
        self.clean("start")
        run_id = self.only_run()
        self.clean("answer", run_id, "--text", "refuse it")
        self.write_script(QUIET)
        self.assertAccepted(self.clean("resume", run_id))
        self.assertEqual(len(self.calls()), 2)
        for call in self.calls():
            # The guard is first for the child, and it holds a refusing `pi`.
            first = call["path"].split(os.pathsep)[0]
            self.assertTrue(first.endswith("guard"), first)
            with open(os.path.join(first, "pi")) as fh:
                self.assertIn("refused", fh.read())


class Ask(HomeTest):
    """The cleaner's own call, driven directly: it runs inside a round, so the cases
    that matter are the ones a misbehaving cleaner produces."""

    def setUp(self):
        super().setUp()
        self.run_id = "clean-20260101-000000"
        os.makedirs(self.at("cleanup", "runs", self.run_id), exist_ok=True)

    def ask(self, *args):
        return self.run_bin("siana-clean", "ask", "--run", self.run_id, *args)

    def test_a_second_question_while_one_is_open_is_refused(self):
        # A cleaner that misread its instructions and kept going must not overwrite
        # the question SIANA is about to be shown.
        self.assertAccepted(self.ask("--body", "The first one"))
        out = self.ask("--body", "The second one")
        self.assertRefused(out, "already has a question waiting", "The first one")
        with open(self.at("cleanup", "runs", self.run_id, "question.json")) as fh:
            self.assertEqual(json.load(fh)["body"], "The first one")

    def test_an_unknown_kind_is_refused(self):
        self.assertRefused(self.ask("--body", "x", "--kind", "urgent"),
                           "no such question kind")

    def test_an_empty_body_is_refused(self):
        self.assertRefused(self.ask("--body", "   "), "needs a body")

    def test_a_run_that_does_not_exist_is_refused(self):
        out = self.run_bin("siana-clean", "ask", "--run", "clean-19700101-000000",
                           "--body", "x")
        self.assertRefused(out, "no cleanup run under that id")


if __name__ == "__main__":
    unittest.main()
