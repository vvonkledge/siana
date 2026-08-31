"""`siana-fact`: what the captain records about a project, and who may use the one
kind of it that is a secret.

Two halves, and they fail differently.

The nonsecret half fails by being wrong: a URL nobody validated, a value with a
newline in it that forges the section of a minion's orders around itself, a fact
recorded against a project the registry does not have. Those are checked the way
every store in this distro is checked, against a real `datafile` and a real
contract.

The credential half fails by leaking, and a leak is a negative. So it is driven
against an instrumented keychain and an instrumented child that record what they
were actually given, and the assertions are about where the value is not: not in an
argv, not in a record, not in the store, not in a report, and not in this repository.
`tests/fake_keychain.py` and `tests/fake_child.py` say why each is a fake and what
each one faithfully reproduces.

The secret below is one string, used everywhere, and `NoLeak` scans for it. That is
the point of it being one string: a test that invented its own would prove nothing
about the paths the others exercise.
"""

import json
import os
import subprocess
import unittest
from unittest import mock

from fake_child import FakeChild
from fake_keychain import INTERACTION_NOT_ALLOWED, FakeKeychain
from helpers import BIN, DISTRO, HomeTest, script

f = script("siana-fact")

# One string, so `NoLeak` below is scanning for the value every other test here
# actually pushed through the real code path. Spelled so that finding it anywhere
# is unambiguous: nothing else in this repository could produce it by accident.
SECRET = "orpington-vestibule-97531-not-a-real-password"
ACCOUNT = "qa@example.test"


class Facts(HomeTest):
    """A home with the two contracts, a registry with one project, a real queue, and
    a keychain that is not this machine's."""

    def setUp(self):
        super().setUp()
        self.contract("projects", "facts", "grants")
        self.queue()
        self.project("demo")
        self.keychain = FakeKeychain(self.at("keychain"))
        self.child = FakeChild(self.at("child"))

    def env(self, **extra):
        e = dict(self.keychain.env())
        e.update(self.child.env())
        e.update(extra)
        return e

    def fact(self, *args, stdin=None, **extra):
        """The command under test. Not `run_bin`, because `credential` is driven by
        writing the two lines a person would have typed at the keychain's prompt,
        and nothing else in this suite needs stdin."""
        return subprocess.run([os.path.join(BIN, "siana-fact"), *args],
                              cwd=self.home, env=self.command_env(self.env(**extra)),
                              text=True, capture_output=True, timeout=120,
                              input=stdin)

    def record_credential(self, slug="test-user", secret=SECRET, account=ACCOUNT,
                          project="demo", replace=False, **extra):
        argv = ["credential", project, slug, "--account", account]
        if replace:
            argv.append("--replace")
        return self.fact(*argv, stdin=f"{secret}\n{secret}\n", **extra)

    def task(self, tid="ship-it", status="todo", project="demo", **fields):
        """A task written through `datafile` against the queue's own contract, so a
        status this suite needs to start from is set rather than walked to."""
        args = {"id": tid, "title": tid.replace("-", " "), "verify": "true",
                "status": status, "updated": "2026-08-31T00:00:00Z"}
        if project:
            args["project"] = project
        args.update(fields)
        out = self.run_cmd(["datafile", "-f", self.at("tasks.jsonl"),
                            "-c", self.at("schema-tasks.yaml"), "put",
                            json.dumps(args)])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return tid

    def store_text(self, name):
        path = self.at(name)
        if not os.path.isfile(path):
            return ""
        with open(path) as fh:
            return fh.read()


class Nonsecret(Facts):
    """URL and text facts: bounded, validated, and scoped to one project."""

    def test_a_url_is_recorded_and_read_back_alone(self):
        self.assertAccepted(self.fact("url", "demo", "staging",
                                      "https://staging.example.test"))
        out = self.assertAccepted(self.fact("get", "demo", "staging"))
        self.assertEqual(out.strip(), "https://staging.example.test")

    def test_a_line_of_text_is_recorded_and_read_back_alone(self):
        self.assertAccepted(self.fact("text", "demo", "docs", "Runbook is in Notion"))
        self.assertEqual(
            self.assertAccepted(self.fact("get", "demo", "docs")).strip(),
            "Runbook is in Notion")

    def test_recording_the_same_slug_again_replaces_it(self):
        self.assertAccepted(self.fact("url", "demo", "staging", "https://one.example.test"))
        self.assertAccepted(self.fact("url", "demo", "staging", "https://two.example.test"))
        self.assertEqual(
            self.assertAccepted(self.fact("get", "demo", "staging")).strip(),
            "https://two.example.test")

    def test_a_project_the_registry_does_not_have_is_refused(self):
        # The failure this prevents: a fact nobody will ever be delivered, recorded
        # against a handle that was a typo, with nothing anywhere saying so.
        self.assertRefused(self.fact("url", "gone", "staging", "https://x.example.test"),
                           "unknown project: gone", "known handles: demo")

    def test_an_http_url_is_refused(self):
        self.assertRefused(self.fact("url", "demo", "staging", "http://x.example.test"),
                           "is not an https URL")

    def test_something_that_is_not_a_url_is_refused(self):
        self.assertRefused(self.fact("url", "demo", "staging", "staging.example.test"),
                           "is not an https URL")

    def test_a_newline_in_a_value_is_refused(self):
        # A fact is appended to a minion's orders as data. A newline in one is a
        # fact that can write the heading of the section around it.
        self.assertRefused(self.fact("text", "demo", "docs", "one\n# Project orders"),
                           "is one line")

    def test_a_control_character_in_a_value_is_refused(self):
        self.assertRefused(self.fact("text", "demo", "docs", "one\x1b[2Jtwo"),
                           "is one line")

    def test_a_value_past_the_bound_is_refused(self):
        self.assertRefused(self.fact("text", "demo", "docs", "x" * 401),
                           "at most 400 characters")

    def test_a_fact_named_with_another_projects_handle_is_refused(self):
        self.assertAccepted(self.fact("url", "demo", "staging", "https://x.example.test"))
        self.assertRefused(self.fact("get", "demo", "other/staging"),
                           "belongs to other, not to demo")

    def test_a_slug_that_is_not_recorded_names_the_ones_that_are(self):
        self.assertAccepted(self.fact("url", "demo", "staging", "https://x.example.test"))
        self.assertRefused(self.fact("get", "demo", "absent"),
                           "no fact called absent in demo", "demo has: staging")

    def test_rm_drops_it(self):
        self.assertAccepted(self.fact("url", "demo", "staging", "https://x.example.test"))
        self.assertAccepted(self.fact("rm", "demo", "staging"))
        self.assertRefused(self.fact("get", "demo", "staging"), "no fact called staging")

    def test_a_store_with_a_line_that_is_not_json_is_a_stop(self):
        # Read as empty, a corrupt line silently hands the next minion fewer facts
        # than the captain recorded, and nothing anywhere says which one went.
        self.store("facts.jsonl", "{not json")
        self.assertRefused(self.fact("list"), "is not JSON")

    def test_a_scoped_listing_counts_what_it_lists(self):
        # The header used to count the whole store, so `list demo` over a store
        # holding another project's facts reported a number it then did not show.
        self.project("other", path=self.home)
        self.assertAccepted(self.fact("url", "demo", "staging", "https://x.example.test"))
        self.assertAccepted(self.fact("url", "other", "elsewhere", "https://y.example.test"))
        out = self.assertAccepted(self.fact("list", "demo"))
        self.assertIn("facts    1 in 1 project(s)", out)
        self.assertNotIn("elsewhere", out)

    def test_a_project_with_nothing_recorded_says_so_once(self):
        self.project("other", path=self.home)
        self.assertAccepted(self.fact("url", "other", "elsewhere", "https://y.example.test"))
        out = self.assertAccepted(self.fact("list", "demo"))
        self.assertIn("nothing recorded in demo", out)

    def test_the_list_never_shows_a_credential_value(self):
        self.assertAccepted(self.record_credential())
        out = self.assertAccepted(self.fact("list"))
        self.assertIn("credential", out)
        self.assertIn(ACCOUNT, out)
        self.assertNotIn(SECRET, out)


class NothingPrintsACredential(Facts):
    """There is no command that prints a credential, and `get` is where somebody
    looking for one tries first."""

    def test_get_refuses_a_credential_and_says_what_to_run_instead(self):
        self.assertAccepted(self.record_credential())
        out = self.assertRefused(self.fact("get", "demo", "test-user"),
                                 "is a credential, and nothing prints one",
                                 "siana-fact exec test-user")
        self.assertNotIn(SECRET, out)

    def test_no_subcommand_offers_to_reveal_one(self):
        # A structural check, because the guarantee is about the command's whole
        # surface and not about one refusal. A subcommand added later that reads a
        # value has to be a deliberate change to this list.
        sub = next(a for a in f.build_parser()._subparsers._group_actions)
        self.assertEqual(
            sorted(sub.choices),
            ["credential", "exec", "get", "grant", "list", "revoke", "rm", "status",
             "text", "url"])


class Credentials(Facts):
    """Setting a value: through the keychain's own prompt, and never through an
    argument, a file or this process."""

    def test_the_value_never_appears_in_the_keychains_argv(self):
        self.assertAccepted(self.record_credential())
        calls = self.keychain.calls()
        self.assertTrue(calls)
        for argv in calls:
            self.assertNotIn(SECRET, " ".join(argv))
        # `-w` last and with nothing after it is what makes the real `security`
        # prompt instead of taking the value from the command line.
        self.assertEqual(calls[0][0], "add-generic-password")
        self.assertEqual(calls[0][-1], "-w")

    def test_the_record_holds_a_reference_and_no_value(self):
        self.assertAccepted(self.record_credential())
        rec = json.loads(self.store_text("facts.jsonl").strip().splitlines()[-1])
        self.assertEqual(rec["kind"], "credential")
        self.assertEqual(rec["account"], ACCOUNT)
        self.assertEqual(rec["service"], "siana/demo/test-user")
        self.assertIsNone(rec.get("value"))
        self.assertNotIn(SECRET, json.dumps(rec))

    def test_a_retype_that_does_not_match_records_nothing(self):
        out = self.fact("credential", "demo", "test-user", "--account", ACCOUNT,
                        stdin=f"{SECRET}\nsomething-else\n")
        self.assertNotEqual(out.returncode, 0)
        self.assertEqual(self.store_text("facts.jsonl"), "")

    def test_an_item_that_already_exists_is_not_overwritten_by_accident(self):
        self.assertAccepted(self.record_credential())
        self.assertRefused(self.record_credential(secret="a-different-one"),
                           "already holds an item", "--replace")

    def test_replace_sets_a_new_value_for_the_item_that_is_there(self):
        self.assertAccepted(self.record_credential())
        self.assertAccepted(self.record_credential(secret="a-different-one",
                                                   replace=True))
        self.assertIn("-U", self.keychain.calls()[-1])

    def test_a_slug_the_contract_will_refuse_is_refused_before_the_prompt(self):
        # The record can only be written after the keychain accepts the value, so a
        # slug `datafile` will reject has to be caught first. Otherwise the captain
        # types the secret twice, it is stored under a service nothing will ever
        # name, the write fails, and no report walks the keychain to find it.
        out = self.fact("credential", "demo", "Test_User", "--account", ACCOUNT,
                        stdin=f"{SECRET}\n{SECRET}\n")
        self.assertRefused(out, "is not a fact slug")
        self.assertEqual(self.keychain.calls(), [])
        self.assertEqual(self.store_text("facts.jsonl"), "")

    def test_an_account_the_contract_will_refuse_is_refused_before_the_prompt(self):
        out = self.record_credential(account="x" * 201)
        self.assertRefused(out, "an account is 1 to 200 characters")
        self.assertEqual(self.keychain.calls(), [])

    def test_a_note_the_contract_will_refuse_is_refused_before_the_prompt_too(self):
        # Every field the refused record would have carried, and not only the two
        # that name the keychain item: a note the contract rejects fails the write
        # just as completely and leaves the same orphan behind.
        out = self.fact("credential", "demo", "test-user", "--account", ACCOUNT,
                        "--note", "x" * 201, stdin=f"{SECRET}\n{SECRET}\n")
        self.assertRefused(out, "a note is 1 to 200 characters")
        self.assertEqual(self.keychain.calls(), [])
        self.assertEqual(self.store_text("facts.jsonl"), "")

    def test_a_keychain_that_will_not_open_records_nothing(self):
        # The record is written after the keychain accepts the value, never before:
        # a reference to an item that does not exist is the one state `exec` cannot
        # tell from a credential that was revoked.
        out = self.record_credential(**{"FAKE_KEYCHAIN_EXIT": str(INTERACTION_NOT_ALLOWED)})
        self.assertNotEqual(out.returncode, 0)
        self.assertIn("would not release", out.stdout + out.stderr)
        self.assertEqual(self.store_text("facts.jsonl"), "")

    def test_a_platform_with_no_keychain_refuses(self):
        with mock.patch("sys.platform", "sunos5"), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("SIANA_KEYCHAIN", None)
            with self.assertRaises(f.Refusal) as caught:
                f.require_keychain()
        self.assertIn("no keychain on this platform", str(caught.exception))

    def test_a_fact_does_not_change_kind_under_a_slug(self):
        self.assertAccepted(self.fact("url", "demo", "staging", "https://x.example.test"))
        self.assertRefused(self.record_credential(slug="staging"),
                           "is already a url fact")
        self.assertAccepted(self.record_credential())
        self.assertRefused(self.fact("url", "demo", "test-user", "https://x.example.test"),
                           "is already a credential")

    def test_rm_of_a_half_written_credential_reports_rather_than_raising(self):
        # A credential record with no service is a state `status` reports and that
        # `rm` is the natural answer to, so a traceback here would leave the captain
        # unable to tell whether the record had already gone.
        self.store("facts.jsonl", {"id": "demo/half", "project": "demo",
                                   "slug": "half", "kind": "credential",
                                   "account": ACCOUNT,
                                   "recorded": "2026-08-31T00:00:00Z"})
        out = self.assertAccepted(self.fact("rm", "demo", "half"))
        self.assertIn("dropped  demo/half", out)
        self.assertIn("the record named no service", out)
        self.assertNotIn("Traceback", out)

    def test_rm_prints_no_command_it_cannot_make_safe(self):
        # `shlex.quote` is shell-safe and not terminal-safe. Escaping the control
        # character instead would print a command that no longer names the item, so
        # there is no command to print at all.
        self.store("facts.jsonl", {"id": "demo/odd", "project": "demo",
                                   "slug": "odd", "kind": "credential",
                                   "account": ACCOUNT, "service": "siana/x\ny",
                                   "recorded": "2026-08-31T00:00:00Z"})
        out = self.assertAccepted(self.fact("rm", "demo", "odd"))
        self.assertNotIn("delete-generic-password", out)
        self.assertIn("siana/x\\x0ay", out)
        self.assertEqual(len(out.splitlines()),
                         len([line for line in out.splitlines() if line.strip()]))

    def test_rm_leaves_the_keychain_item_and_says_how_to_remove_it(self):
        self.assertAccepted(self.record_credential())
        out = self.assertAccepted(self.fact("rm", "demo", "test-user"))
        self.assertIn("its keychain item is untouched", out)
        self.assertIn("security delete-generic-password -s siana/demo/test-user", out)
        self.assertNotIn("delete-generic-password",
                         " ".join(sum(self.keychain.calls(), [])))


class Grants(Facts):
    """Explicit, task-specific, and written before the task starts."""

    def setUp(self):
        super().setUp()
        self.assertAccepted(self.record_credential())

    def test_a_todo_task_can_be_granted(self):
        self.task("ship-it")
        out = self.assertAccepted(self.fact("grant", "ship-it", "test-user"))
        self.assertIn("granted  demo/test-user to ship-it", out)

    def test_a_task_that_has_already_started_cannot_be(self):
        # The rule: a grant is a decision the captain made about work they had read,
        # not an answer to a minion that has started and asked for one.
        self.task("ship-it", status="doing")
        self.assertRefused(self.fact("grant", "ship-it", "test-user"),
                           "is doing, and a grant is written before dispatch")

    def test_a_finished_task_cannot_be(self):
        self.task("ship-it", status="done")
        self.assertRefused(self.fact("grant", "ship-it", "test-user"), "is done")

    def test_a_nonsecret_fact_needs_no_grant(self):
        self.task("ship-it")
        self.assertAccepted(self.fact("url", "demo", "staging", "https://x.example.test"))
        self.assertRefused(self.fact("grant", "ship-it", "staging"),
                           "is a url fact, and needs no grant")

    def test_a_grant_across_projects_is_refused(self):
        self.project("other", path=self.home)
        self.task("ship-it", project="other")
        self.assertRefused(self.fact("grant", "ship-it", "demo/test-user"),
                           "belongs to demo, not to other")

    def test_a_task_with_no_project_cannot_be_granted(self):
        self.task("ship-it", project=None)
        self.assertRefused(self.fact("grant", "ship-it", "test-user"),
                           "carries no project")

    def test_revoking_twice_is_not_an_error(self):
        self.task("ship-it")
        self.assertAccepted(self.fact("grant", "ship-it", "test-user"))
        self.assertAccepted(self.fact("revoke", "ship-it", "test-user"))
        self.assertIn("already", self.assertAccepted(
            self.fact("revoke", "ship-it", "test-user")))

    def test_a_grant_outliving_the_fact_it_names_can_still_be_withdrawn(self):
        # `rm` leaves the grant standing on purpose and says which tasks still hold
        # one. A revoke that first demanded the fact exist made exactly those
        # impossible to withdraw, and `status` called them broken for as long as
        # they stood, with nothing on the command surface to fix it.
        self.task("ship-it")
        self.assertAccepted(self.fact("grant", "ship-it", "test-user"))
        out = self.assertAccepted(self.fact("rm", "demo", "test-user"))
        self.assertIn("siana-fact revoke ship-it demo/test-user", out)
        self.assertAccepted(self.fact("revoke", "ship-it", "test-user"))
        self.assertAccepted(self.fact("status"))

    def test_a_grant_on_a_task_that_left_the_queue_can_still_be_withdrawn(self):
        # SIANA drops a task it decides not to run. Its grant is exactly the record
        # that most needs withdrawing, `status` calls it broken, and nothing else
        # deletes a grant - so a revoke that insisted on the task left a
        # permanently red report with no command to clear it.
        self.task("ship-it")
        self.assertAccepted(self.fact("grant", "ship-it", "test-user"))
        self.run_cmd(["datafile", "-f", self.at("tasks.jsonl"), "-c",
                      self.at("schema-tasks.yaml"), "delete", "ship-it"])
        out = self.assertAccepted(self.fact("revoke", "ship-it", "test-user"))
        self.assertIn("revoked  demo/test-user from ship-it", out)
        self.assertAccepted(self.fact("status"))

    def test_a_revoke_naming_neither_a_task_nor_a_grant_is_still_refused(self):
        self.assertRefused(self.fact("revoke", "never-existed", "test-user"),
                           "nothing grants anything to never-existed")

    def test_revoking_something_never_granted_is_refused(self):
        # A revocation that matched nothing would read as one that worked, which is
        # the worst possible answer to give somebody withdrawing a credential.
        self.task("ship-it")
        self.assertRefused(self.fact("revoke", "ship-it", "test-user"),
                           "nothing grants demo/test-user to ship-it")


class Exec(Facts):
    """The only path a credential is ever read on."""

    def setUp(self):
        super().setUp()
        self.assertAccepted(self.record_credential())
        self.task("ship-it")
        self.assertAccepted(self.fact("grant", "ship-it", "test-user"))
        self.start("ship-it")

    def start(self, tid):
        out = self.run_cmd(["tasks", "--file", self.at("tasks.jsonl"), "start", tid,
                            "--owner", "claude@p1"])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    def spend(self, *args, task="ship-it", slug="test-user", **extra):
        env = dict(extra)
        if task is not None:
            env["SIANA_TASK_ID"] = task
        return self.fact("exec", slug, "--", self.child.command, *args, **env)

    def test_the_child_receives_the_credential(self):
        self.assertAccepted(self.spend())
        saw = self.child.saw()
        self.assertEqual(saw["username"], ACCOUNT)
        self.assertEqual(saw["password_sha256"], FakeChild.digest(SECRET))

    def test_the_value_is_in_no_argv_anywhere(self):
        self.assertAccepted(self.spend("--exit", "0"))
        self.assertNotIn(SECRET, " ".join(self.child.saw()["argv"]))
        for argv in self.keychain.calls():
            self.assertNotIn(SECRET, " ".join(argv))

    def test_the_parent_does_not_keep_the_names(self):
        # A copy of the environment, never `os.environ`: the two names exist in the
        # child and in nothing else, including whatever this process runs next.
        out = self.assertAccepted(self.spend())
        self.assertNotIn("SIANA_FACT_PASSWORD", out)
        second = self.fact("list")
        self.assertNotIn(SECRET, second.stdout + second.stderr)

    def test_the_childs_exit_status_is_returned(self):
        self.assertEqual(self.spend("--exit", "7").returncode, 7)

    def test_a_child_killed_by_a_signal_is_reported_the_way_a_shell_reports_one(self):
        self.assertEqual(self.spend("--signal").returncode, 143)

    def test_without_a_task_id_nothing_runs(self):
        # The task is read from the environment a dispatch set, never from an
        # argument: a task id that could be typed is an authorisation that could be
        # borrowed.
        self.assertRefused(self.spend(task=None), "SIANA_TASK_ID is not set")
        self.assertIsNone(self.child.saw())

    def test_a_task_that_is_not_doing_cannot_spend_its_grant(self):
        self.run_cmd(["tasks", "--file", self.at("tasks.jsonl"), "done", "ship-it",
                      "--reason", "finished"])
        self.assertRefused(self.spend(), "is done, not doing")
        self.assertIsNone(self.child.saw())

    def test_a_task_with_no_grant_is_refused(self):
        self.task("other-task", status="doing")
        self.assertRefused(self.spend(task="other-task"),
                           "other-task was not granted demo/test-user")
        self.assertIsNone(self.child.saw())

    def test_a_grant_on_a_sibling_task_authorises_nothing(self):
        # A QA task paired with a ship task, a dependency, and a task that merely
        # ran earlier are all this same shape: a grant that exists and is not this
        # task's.
        self.task("qa-ship-it", status="doing")
        self.assertRefused(self.spend(task="qa-ship-it"), "was not granted")
        self.assertIsNone(self.child.saw())

    def test_a_revoked_grant_is_refused_before_the_keychain_is_read(self):
        before = len(self.keychain.calls())
        self.assertAccepted(self.fact("revoke", "ship-it", "test-user"))
        self.assertRefused(self.spend(), "was revoked at")
        self.assertIsNone(self.child.saw())
        self.assertEqual(len(self.keychain.calls()), before)

    def test_a_grant_recording_another_project_is_refused(self):
        # Hand-written, because `grant` cannot produce one. It is the record that
        # could hand a minion the keys to somewhere it is not working, so it refuses
        # rather than picking whichever project it likes.
        self.store("grants.jsonl", {"id": "ship-it/demo/test-user", "task": "ship-it",
                                    "fact": "demo/test-user", "project": "elsewhere",
                                    "status": "granted",
                                    "granted": "2026-08-31T00:00:00Z"})
        self.assertRefused(self.spend(), "does not agree with ship-it about the project")
        self.assertIsNone(self.child.saw())

    def test_a_keychain_item_that_is_gone_refuses_and_runs_nothing(self):
        for name in os.listdir(self.keychain.items):
            if name != "argv.jsonl":
                os.remove(os.path.join(self.keychain.items, name))
        out = self.assertRefused(self.spend(), "the keychain has no item for")
        self.assertIsNone(self.child.saw())
        self.assertNotIn(SECRET, out)

    def test_a_locked_keychain_refuses_and_runs_nothing(self):
        out = self.assertRefused(
            self.spend(**{"FAKE_KEYCHAIN_EXIT": str(INTERACTION_NOT_ALLOWED)}),
            "would not release")
        self.assertIsNone(self.child.saw())
        self.assertNotIn(SECRET, out)

    def test_an_answer_the_keychain_has_no_name_for_still_fails_closed(self):
        self.assertRefused(self.spend(**{"FAKE_KEYCHAIN_EXIT": "9"}),
                           "the keychain refused")
        self.assertIsNone(self.child.saw())

    def test_a_nonsecret_fact_carries_nothing_to_run_with(self):
        self.assertAccepted(self.fact("url", "demo", "staging", "https://x.example.test"))
        self.assertRefused(self.spend(slug="staging"), "carries no secret to run with")

    def test_a_command_that_does_not_exist_says_nothing_ran(self):
        out = self.assertRefused(
            self.fact("exec", "test-user", "--", "no-such-command-here",
                      SIANA_TASK_ID="ship-it"),
            "no such command", "nothing ran with it")
        self.assertNotIn(SECRET, out)

    def test_no_command_at_all_is_refused(self):
        self.assertRefused(self.fact("exec", "test-user", SIANA_TASK_ID="ship-it"),
                           "what should this run?")

    def test_the_childs_own_flags_are_the_childs(self):
        # Without REMAINDER, a `-v` meant for the command being run is parsed here
        # and the invocation dies about a flag this command does not have.
        out = self.fact("exec", "test-user", "--", "/bin/sh", "-c", "exit 3",
                        SIANA_TASK_ID="ship-it")
        self.assertEqual(out.returncode, 3)


class Status(Facts):
    """Read-only, value-free, and it repairs nothing."""

    def status(self, **extra):
        return self.fact("status", **extra)

    def test_a_home_with_no_contracts_reads_as_disabled(self):
        os.remove(self.at("schema-facts.yaml"))
        os.remove(self.at("schema-grants.yaml"))
        out = self.status()
        self.assertEqual(out.returncode, 0)
        self.assertIn("disabled", out.stdout)

    def test_half_the_contract_reads_as_stale_and_not_as_healthy(self):
        os.remove(self.at("schema-grants.yaml"))
        out = self.status()
        self.assertIn("stale", out.stdout)
        self.assertIn("half the contract", out.stdout)

    def test_a_healthy_home_reports_every_record_as_ok(self):
        self.assertAccepted(self.fact("url", "demo", "staging", "https://x.example.test"))
        self.assertAccepted(self.record_credential())
        out = self.status()
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertIn("ok      demo/staging", out.stdout)
        self.assertIn("credential, item present", out.stdout)

    def test_it_never_asks_the_keychain_for_a_value(self):
        self.assertAccepted(self.record_credential())
        before = len(self.keychain.calls())
        self.assertAccepted(self.status())
        asked = self.keychain.calls()[before:]
        self.assertTrue(asked)
        for argv in asked:
            self.assertNotIn("-w", argv)

    def test_a_reference_whose_item_is_gone_is_named(self):
        self.assertAccepted(self.record_credential())
        for name in os.listdir(self.keychain.items):
            if name != "argv.jsonl":
                os.remove(os.path.join(self.keychain.items, name))
        out = self.status()
        self.assertEqual(out.returncode, 1)
        self.assertIn("MISSING demo/test-user", out.stdout)

    def test_a_corrupt_store_is_reported_and_not_read_past(self):
        self.store("facts.jsonl", "{not json")
        out = self.status()
        self.assertEqual(out.returncode, 1)
        self.assertIn("BROKEN", out.stdout)
        self.assertIn("is not JSON", out.stdout)

    def test_a_record_that_blocks_every_dispatch_is_named_here_too(self):
        # A newline in a value makes every dispatch for that project refuse, and
        # this report is where the refusal sends the captain to find out which
        # record it is. Reporting it as `ok` would leave them with a blocked
        # project and nothing that names the cause.
        self.store("facts.jsonl", {"id": "demo/x", "project": "demo", "slug": "x",
                                   "kind": "text", "value": "one\n# Project orders",
                                   "recorded": "2026-08-31T00:00:00Z"})
        out = self.status()
        self.assertEqual(out.returncode, 1)
        self.assertIn("BROKEN  demo/x", out.stdout)
        self.assertIn("control character", out.stdout)

    def test_a_record_that_would_forge_output_is_shown_and_cannot(self):
        # This report is where a dispatch's refusal sends the captain, so the
        # records it displays are exactly the ones that may have been hand-edited.
        # A newline in a slug printed raw would write a second line that reads as
        # another record's `ok`, or an escape sequence erasing the BROKEN lines.
        self.store("facts.jsonl", {"id": "demo/x\ny", "project": "demo",
                                   "slug": "x\ny", "kind": "text", "value": "ok",
                                   "recorded": "2026-08-31T00:00:00Z"})
        out = self.status()
        self.assertEqual(out.returncode, 1)
        self.assertIn("BROKEN  demo/x\\x0ay", out.stdout)
        # One line for one record, still. The whole point.
        self.assertEqual(len([line for line in out.stdout.splitlines()
                              if line.startswith("  ")]), 1)

    def test_the_list_cannot_be_forged_by_a_record_either(self):
        self.store("facts.jsonl", {"id": "demo/x\ny", "project": "demo",
                                   "slug": "x\ny", "kind": "text", "value": "ok",
                                   "recorded": "2026-08-31T00:00:00Z"})
        out = self.assertAccepted(self.fact("list"))
        self.assertIn("demo/x\\x0ay", out)
        self.assertEqual(len([line for line in out.splitlines()
                              if line.startswith("  ")]), 1)

    def test_a_record_no_reader_can_deliver_is_named(self):
        self.store("facts.jsonl", {"id": "demo/half", "project": "demo",
                                   "slug": "half", "kind": "url",
                                   "recorded": "2026-08-31T00:00:00Z"})
        out = self.status()
        self.assertEqual(out.returncode, 1)
        self.assertIn("BROKEN  demo/half", out.stdout)

    def test_a_grant_whose_task_has_finished_is_stale_and_says_how_to_withdraw_it(self):
        self.assertAccepted(self.record_credential())
        self.task("ship-it")
        self.assertAccepted(self.fact("grant", "ship-it", "test-user"))
        self.task("ship-it", status="done")
        out = self.status()
        self.assertIn("stale   ship-it -> demo/test-user", out.stdout)
        self.assertIn("siana-fact revoke ship-it demo/test-user", out.stdout)

    def test_it_changes_nothing(self):
        self.assertAccepted(self.record_credential())
        before = (self.store_text("facts.jsonl"), self.store_text("grants.jsonl"))
        self.assertAccepted(self.status())
        self.assertEqual((self.store_text("facts.jsonl"),
                          self.store_text("grants.jsonl")), before)


class NoLeak(Facts):
    """The whole point, as one assertion each.

    A credential is driven through every path that touches it - recorded, granted,
    delivered into a minion's orders, spent by a child, reported on - and then the
    home and this repository are read whole. The value is allowed in exactly one
    place, which is the keychain, and the test names that place rather than
    excluding it by pattern: an exception written as a pattern would have quietly
    excused whatever else happened to match it.
    """

    def everything(self):
        """Every path in the home, plus the durable orders directory, as (path,
        text). Binary and unreadable files are skipped, because the value is text
        and anything that cannot hold it cannot leak it."""
        for dirpath, dirnames, filenames in os.walk(self.home):
            dirnames[:] = [d for d in dirnames if d != "__pycache__"]
            for name in filenames:
                path = os.path.join(dirpath, name)
                try:
                    with open(path, encoding="utf-8") as fh:
                        yield path, fh.read()
                except (OSError, UnicodeDecodeError):
                    continue

    def exercise(self):
        """Every path a credential travels, driven once."""
        self.assertAccepted(self.fact("url", "demo", "staging", "https://x.example.test"))
        self.assertAccepted(self.record_credential())
        self.task("ship-it")
        self.assertAccepted(self.fact("grant", "ship-it", "test-user"))
        self.run_cmd(["tasks", "--file", self.at("tasks.jsonl"), "start", "ship-it",
                      "--owner", "claude@p1"])
        # The orders a dispatch would write, through the real assembler.
        d = script("siana-dispatch")
        with open(self.at("orders.md"), "w") as fh:
            fh.write("FLEET ORDERS\n")
        d.assemble_orders(self.home, "ship-it", self.at("orders.md"), None, "demo",
                          d.facts_section(self.home, "demo", "ship-it"))
        out = self.fact("exec", "test-user", "--", self.child.command,
                        SIANA_TASK_ID="ship-it")
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.child.saw()["password_sha256"], FakeChild.digest(SECRET))
        self.assertAccepted(self.fact("status"))
        return out

    def test_the_value_is_nowhere_in_the_home_but_the_keychain(self):
        self.exercise()
        held = [path for path, text in self.everything() if SECRET in text]
        keychain_items = set(self.keychain.files())
        self.assertEqual([p for p in held if p not in keychain_items], [],
                         f"the value reached {held}")
        # And it really is in the keychain, so the assertion above is not passing
        # because the value was never stored at all.
        self.assertTrue([p for p in held if p in keychain_items])

    def test_the_orders_a_minion_is_given_name_the_credential_and_not_its_value(self):
        self.exercise()
        with open(self.at("orders", "ship-it.md")) as fh:
            orders = fh.read()
        self.assertIn("test-user", orders)
        self.assertIn(ACCOUNT, orders)
        self.assertIn("SIANA_FACT_PASSWORD", orders)
        self.assertNotIn(SECRET, orders)

    def test_the_value_is_in_no_output_this_command_ever_wrote(self):
        out = self.exercise()
        self.assertNotIn(SECRET, out.stdout + out.stderr)

    def test_the_value_is_in_no_argv_the_keychain_or_the_child_was_given(self):
        self.exercise()
        for argv in self.keychain.calls():
            self.assertNotIn(SECRET, " ".join(argv))
        self.assertNotIn(SECRET, " ".join(self.child.saw()["argv"]))

    def test_no_file_in_this_repository_carries_it(self):
        # The generated pi package, the templates, the fixtures and the suite. A
        # test credential committed here is the leak that outlives every test run.
        found = []
        for dirpath, dirnames, filenames in os.walk(DISTRO):
            dirnames[:] = [d for d in dirnames
                           if d not in (".git", "__pycache__", ".ci-deps")]
            for name in filenames:
                path = os.path.join(dirpath, name)
                try:
                    with open(path, encoding="utf-8") as fh:
                        text = fh.read()
                except (OSError, UnicodeDecodeError):
                    continue
                # This module and the fake child define the string; everything else
                # only ever handles it.
                if SECRET in text and os.path.abspath(path) != os.path.abspath(__file__):
                    found.append(os.path.relpath(path, DISTRO))
        self.assertEqual(found, [], f"\nthe test credential is committed in: {found}")


class TheStoreItself(Facts):
    """The contract, driven through `datafile` the way a real write is. A rule this
    suite believes and the contract does not hold is a rule that is not there."""

    def put(self, **fields):
        return self.run_cmd(["datafile", "-f", self.at("facts.jsonl"), "-c",
                             self.at("schema-facts.yaml"), "put",
                             json.dumps({"recorded": "2026-08-31T00:00:00Z", **fields})])

    def test_a_newline_in_a_value_is_refused_by_the_contract_too(self):
        # Not only by the command. A record written around `siana-fact` still cannot
        # carry the newline that would forge a section of a minion's orders.
        self.assertRefused(self.put(id="demo/x", project="demo", slug="x",
                                    kind="text", value="one\ntwo"))

    def test_a_kind_the_readers_do_not_know_is_refused_at_write(self):
        self.assertRefused(self.put(id="demo/x", project="demo", slug="x",
                                    kind="token", value="x"))

    def test_a_mistyped_key_is_refused_rather_than_recorded(self):
        self.assertRefused(self.put(id="demo/x", project="demo", slug="x",
                                    kind="text", value="x", secret="oops"))

    def test_a_grant_id_names_its_task_its_project_and_its_slug(self):
        out = self.run_cmd(["datafile", "-f", self.at("grants.jsonl"), "-c",
                            self.at("schema-grants.yaml"), "put",
                            json.dumps({"id": "not-three-parts", "task": "t",
                                        "fact": "demo/x", "project": "demo",
                                        "granted": "2026-08-31T00:00:00Z"})])
        self.assertRefused(out)


if __name__ == "__main__":
    unittest.main()
