"""`siana`: the captain's one interface, and the lock that keeps it one.

Three invariants. What SIANA is told at startup has to be in its system prompt, in
one flag, because a second flag is silently dropped and SIANA would start believing
it had context it never received. Exactly one SIANA leads the fleet, because a
second would race the first for every task in the queue while the captain talked to
one of the two with no way to tell which. And a session only ever opens in a harness
this home has a queue integration for: one that does not is a SIANA with no fleet
queue in front of it, which looks exactly like a working one.

The harnesses are stood in for. Starting a real agent session is not what is under
test here, and the stubs are what make the flags each was called with readable.
"""

import os
import signal
import subprocess
import unittest

from helpers import BIN, HomeTest, until

# One stub for both harnesses. It records the name it was invoked as alongside the
# arguments, so which harness started is read off the same file the flags are, and
# a test cannot pass by finding the right flags on the wrong harness.
STUB = """#!/bin/sh
# Stands in for a harness. Records what it was called with, and holds the session
# open when asked to, which is what makes the leader lock observable.
printf '%s\\0' "$(basename "$0")" "$@" > "$HARNESS_STUB_ARGV"
[ -n "${HARNESS_STUB_HOLD:-}" ] || exit 0
while :; do sleep 0.2; done
"""


class Siana(HomeTest):

    def setUp(self):
        super().setUp()
        self.stub_dir = self.at("stub")
        os.makedirs(self.stub_dir)
        for harness in ("pi", "claude"):
            stub = os.path.join(self.stub_dir, harness)
            with open(stub, "w") as fh:
                fh.write(STUB)
            os.chmod(stub, 0o755)
        self.argv_file = self.at("harness-argv")
        # `siana` calls `siana-owe` by name, so the distro's own commands have to be
        # findable the way an install makes them findable.
        self.path = f"{self.stub_dir}:{BIN}:{os.environ['PATH']}"

    def initialize(self, *harnesses):
        """A home `just init` has run in, installed for the harnesses named.

        Defaults to pi, which is what a home installed before there was a choice
        holds. The queue integration is the whole of what says a harness was
        installed for, so a home given none is one nothing starts in.
        """
        with open(self.at("siana.env"), "w") as fh:
            fh.write(f"SIANA_HOME={self.home}\n")
        for harness in harnesses or ("pi",):
            self.integration(harness)

    def integration(self, harness):
        """The queue integration `init` writes for one harness: pi's project-local
        package config, or the claude settings carrying the SessionStart hook.

        Written empty on purpose. `siana` reads whether it is there and never what
        is in it, and a test that furnished it would be asserting about a shape
        `tasks` owns rather than about the check under test."""
        path = self.at({"pi": ".pi", "claude": ".claude"}[harness], "settings.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as fh:
            fh.write("{}\n")
        return path

    def env(self, **extra):
        # HERDR_PANE_ID is emptied unless a test sets it. These tests are themselves
        # run inside herdr, and inheriting its pane would make "SIANA outside herdr"
        # unreachable here while staying perfectly reachable for the captain.
        return {"HERDR_PANE_ID": "", "PATH": self.path,
                "SIANA_HARNESS": "", "HARNESS_STUB_ARGV": self.argv_file, **extra}

    def start(self, *args, **extra):
        return self.run_bin("siana", *args, env=self.env(**extra))

    def hold(self, **extra):
        """A SIANA that stays up, in its own process group so the stub's `sleep`
        children go with it."""
        e = dict(os.environ)
        e.update(SIANA_HOME=self.home, HARNESS_STUB_HOLD="1", **self.env(**extra))
        p = subprocess.Popen([os.path.join(BIN, "siana")], cwd=self.home, env=e,
                             text=True, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True)

        def stop():
            try:
                os.killpg(os.getpgid(p.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            p.wait(timeout=10)
            p.stdout.close()
            p.stderr.close()

        self.addCleanup(stop)
        self.assertTrue(until(lambda: os.path.exists(self.at("session"))),
                        "the leading SIANA never recorded its session")
        return p

    def argv(self):
        """What the harness stub was called with, the name it was invoked as first.

        The name travels with the flags so which harness started is read off the
        same record, and a test cannot pass by finding the right flags on the
        wrong harness."""
        self.assertTrue(until(lambda: os.path.exists(self.argv_file)),
                        "no harness was ever started")
        with open(self.argv_file) as fh:
            return [a for a in fh.read().split("\0") if a]

    def started(self):
        return self.argv()[0]

    def session(self):
        with open(self.at("session")) as fh:
            return dict(line.strip().split("=", 1) for line in fh if "=" in line)


class NotInitialized(Siana):

    def test_a_home_that_was_never_created_is_a_refusal_and_not_an_install(self):
        # `init` would happily build one here, turning a typo in SIANA_HOME into a
        # fresh empty fleet that looks like a working one.
        out = self.assertRefused(self.start(), "not initialized", "just init")
        self.assertIn(self.home, out)
        self.assertFalse(os.path.exists(self.argv_file))


class Context(Siana):
    """What SIANA is handed at startup."""

    def setUp(self):
        super().setUp()
        self.initialize()
        self.contract("projects", "obligations")

    def test_the_registry_reaches_the_system_prompt(self):
        self.project("workshop", ship="just check")
        self.assertAccepted(self.start())
        prompt = "\n".join(self.argv())
        self.assertIn("The captain's projects", prompt)
        self.assertIn("workshop", prompt)
        self.assertIn("just check", prompt)

    def test_the_worktree_policy_is_pinned_into_the_prompt(self):
        # The default view shows only the first few fields and would drop it. A
        # registry that looks complete while hiding a project's isolation policy is
        # worse than no registry.
        self.project("workshop", worktree="false")
        self.assertAccepted(self.start())
        self.assertIn("worktree", "\n".join(self.argv()))

    def test_a_driven_rigor_is_pinned_into_the_prompt(self):
        # Dropped by the default view, like `worktree`. It is what says ship tasks
        # there take `siana-pipeline check` as their verify rather than the project's
        # `ship` command, and SIANA writes that verify from what it knows here.
        self.project("workshop", pipeline="true")
        self.assertAccepted(self.start())
        self.assertIn("pipeline", "\n".join(self.argv()))

    def test_a_standing_merge_grant_is_pinned_into_the_prompt(self):
        # Dropped by the default view, like the two above, and the one field in the
        # registry that lets accepted work reach a default branch without the captain
        # typing anything. A registry that hid it would leave SIANA unable to say
        # what publishing a project does.
        self.project("workshop", automerge="squash")
        self.assertAccepted(self.start())
        self.assertIn("automerge", "\n".join(self.argv()))

    def test_what_siana_owes_reaches_the_system_prompt(self):
        self.assertAccepted(self.run_bin("siana-owe", "promise", "Report at noon"))
        self.assertAccepted(self.start())
        prompt = "\n".join(self.argv())
        self.assertIn("What SIANA owes", prompt)
        self.assertIn("Report at noon", prompt)

    def test_owing_nothing_is_stated_rather_than_left_out(self):
        # Its absence would read as never having asked.
        self.assertAccepted(self.start())
        prompt = "\n".join(self.argv())
        self.assertIn("What SIANA owes", prompt)
        self.assertIn("owed     nothing", prompt)

    def test_both_halves_travel_in_one_flag(self):
        # Passed twice, only the last survives and the first is dropped in silence.
        # One flag cannot have that bug.
        self.project("workshop")
        self.assertAccepted(self.run_bin("siana-owe", "promise", "Report at noon"))
        self.assertAccepted(self.start())
        argv = self.argv()
        self.assertEqual(argv.count("--append-system-prompt"), 1)
        prompt = argv[argv.index("--append-system-prompt") + 1]
        self.assertIn("The captain's projects", prompt)
        self.assertIn("What SIANA owes", prompt)

    def test_a_registry_that_cannot_be_read_stops_rather_than_reads_as_empty(self):
        # Injecting the error would leave SIANA believing the captain has no
        # projects, which is the one wrong answer this read must never give.
        with open(self.at("schema-projects.yaml"), "w") as fh:
            fh.write("fields: [this is not\n  a contract\n")
        self.assertRefused(self.start(), "not readable")
        self.assertFalse(os.path.exists(self.argv_file))

    def test_consent_to_this_directory_is_passed_explicitly(self):
        self.assertAccepted(self.start())
        self.assertIn("--approve", self.argv())

    def test_the_captain_s_own_arguments_are_passed_through(self):
        self.assertAccepted(self.start("--model", "opus"))
        argv = self.argv()
        self.assertIn("--model", argv)
        self.assertIn("opus", argv)

    def test_running_outside_herdr_says_the_watcher_cannot_wake_this_session(self):
        out = self.assertAccepted(self.start())
        self.assertIn("siana-watch cannot wake this session", out)


class Leadership(Siana):
    """One SIANA leads the fleet, and `$SIANA_HOME/session` is what says which."""

    def setUp(self):
        super().setUp()
        self.initialize()

    def test_the_session_records_the_pid_the_pane_and_the_harness(self):
        # The pane comes from the environment herdr sets, never from searching for
        # an agent that looks about right: herdr's labels are not unique. The
        # harness goes with it because `siana-watch` will not run against a pane
        # herdr reports a different agent in, and herdr answers with the agent's
        # name.
        p = self.hold(HERDR_PANE_ID="w3D:p2")
        self.assertEqual(self.session(), {"SIANA_PID": str(p.pid),
                                          "SIANA_PANE": "w3D:p2",
                                          "SIANA_HARNESS": "pi"})

    def test_the_session_records_the_harness_that_actually_started(self):
        # Not the set the home could have started. A watcher told "pi or claude"
        # would go on raising wakes for a pane the other harness has taken over,
        # where nothing is reading them - and would take a claude SIANA for a pi
        # one, which is the session it refuses to run against at all.
        self.integration("claude")
        p = self.hold(HERDR_PANE_ID="w3D:p2", SIANA_HARNESS="claude")
        self.assertEqual(self.session()["SIANA_HARNESS"], "claude")
        self.assertEqual(self.session()["SIANA_PID"], str(p.pid))

    def test_a_second_siana_is_refused_while_the_first_is_alive(self):
        self.hold(HERDR_PANE_ID="w3D:p2")
        out = self.assertRefused(self.start(), "already leading the fleet",
                                 "race the first")
        self.assertIn("w3D:p2", out)
        self.assertIn("herdr agent attach", out)

    def test_a_leader_outside_herdr_is_said_to_have_no_pane_to_attach_to(self):
        self.hold()
        self.assertRefused(self.start(), "already leading the fleet",
                           "no pane to attach to")

    def test_the_session_is_released_when_siana_exits(self):
        self.assertAccepted(self.start())
        self.assertFalse(os.path.exists(self.at("session")))

    def test_a_session_whose_process_is_gone_is_taken_over(self):
        # A SIANA killed before its trap ran. Taking the file over is the whole
        # recovery, and a captain who has to delete a file by hand has been stopped
        # by bookkeeping.
        dead = subprocess.Popen(["true"])
        dead.wait()
        with open(self.at("session"), "w") as fh:
            fh.write(f"SIANA_PID={dead.pid}\n")
        self.assertAccepted(self.start())
        self.assertIn("--approve", self.argv())

    def test_a_pid_now_worn_by_something_else_is_not_a_live_siana(self):
        # Pids are reused, so liveness alone is not enough: the process wearing it
        # has to still be a SIANA. It has to be a process this user can signal,
        # or `kill -0` refuses it and the check under test is never reached.
        stranger = subprocess.Popen(["sleep", "60"])
        self.addCleanup(stranger.wait)
        self.addCleanup(stranger.kill)
        with open(self.at("session"), "w") as fh:
            fh.write(f"SIANA_PID={stranger.pid}\n")
        self.assertAccepted(self.start())
        self.assertIn("--approve", self.argv())

    def test_an_empty_session_file_does_not_lock_the_captain_out(self):
        open(self.at("session"), "w").close()
        self.assertAccepted(self.start())
        self.assertIn("--approve", self.argv())


class Harness(Siana):
    """Which agent SIANA's own session opens in.

    Two are supported and they are not interchangeable: each needs its own queue
    integration in the home, and starting in one the home has none for is a SIANA
    with no fleet queue in front of it. That session answers about the queue from
    nothing and looks exactly like a working one, which is why every path here
    refuses rather than starts.
    """

    def setUp(self):
        super().setUp()
        self.contract("projects", "obligations")

    def test_a_home_installed_for_pi_alone_starts_in_pi(self):
        # The home every captain had before there was a choice. Nothing about it
        # changed, so neither does what `siana` with no argument does in it.
        self.initialize("pi")
        self.assertAccepted(self.start())
        self.assertEqual(self.started(), "pi")

    def test_a_home_installed_for_claude_alone_starts_in_claude(self):
        # No pi to default to. Falling back to it would refuse a home that is
        # complete, over a harness the captain never installed.
        self.initialize("claude")
        self.assertAccepted(self.start())
        self.assertEqual(self.started(), "claude")

    def test_a_home_installed_for_both_starts_in_pi(self):
        # Both work, so the tie is broken the way it was before claude was an
        # option rather than by asking. A captain who wants the other says so.
        self.initialize("pi", "claude")
        self.assertAccepted(self.start())
        self.assertEqual(self.started(), "pi")

    def test_the_flag_chooses_the_harness(self):
        self.initialize("pi", "claude")
        self.assertAccepted(self.start("--harness", "claude"))
        self.assertEqual(self.started(), "claude")

    def test_the_flag_is_taken_joined_by_an_equals_too(self):
        # The other spelling of the same flag. Passed through instead, it would
        # reach pi as an argument pi has never heard of, and the refusal would be
        # about a flag rather than about the harness the captain asked for.
        self.initialize("pi", "claude")
        self.assertAccepted(self.start("--harness=claude"))
        self.assertEqual(self.started(), "claude")

    def test_the_environment_chooses_the_harness(self):
        self.initialize("pi", "claude")
        self.assertAccepted(self.start(SIANA_HARNESS="claude"))
        self.assertEqual(self.started(), "claude")

    def test_the_flag_wins_over_the_environment(self):
        # The environment is the captain's standing answer and the flag is this
        # start's. A shell that exports one must not make the flag unreachable.
        self.initialize("pi", "claude")
        self.assertAccepted(self.start("--harness", "pi", SIANA_HARNESS="claude"))
        self.assertEqual(self.started(), "pi")

    def test_the_captain_s_own_arguments_still_pass_through(self):
        # Only the harness flag is consumed. Eating more would silently drop
        # arguments the captain meant for the session.
        self.initialize("pi", "claude")
        self.assertAccepted(self.start("--harness", "claude", "--model", "opus"))
        argv = self.argv()
        self.assertEqual(argv[0], "claude")
        self.assertIn("--model", argv)
        self.assertIn("opus", argv)

    def test_what_siana_is_told_travels_the_same_way_in_claude(self):
        # Both harnesses take --append-system-prompt, and both drop all but the
        # last of two, so the one-flag rule is not pi's and has to hold here too.
        self.initialize("claude")
        self.project("workshop")
        self.assertAccepted(self.run_bin("siana-owe", "promise", "Report at noon"))
        self.assertAccepted(self.start("--harness", "claude"))
        argv = self.argv()
        self.assertEqual(argv.count("--append-system-prompt"), 1)
        prompt = argv[argv.index("--append-system-prompt") + 1]
        self.assertIn("The captain's projects", prompt)
        self.assertIn("What SIANA owes", prompt)

    def test_claude_is_not_started_with_its_permission_checks_waived(self):
        # A minion's are waived by `siana-dispatch`, because nobody is there to
        # answer a prompt. The captain is sitting in front of this one, so waiving
        # theirs is a decision this command would be making for them.
        self.initialize("claude")
        self.assertAccepted(self.start("--harness", "claude"))
        self.assertNotIn("--dangerously-skip-permissions", self.argv())

    def test_a_harness_with_no_queue_integration_is_refused(self):
        # The whole reason this check exists: the session would open, look like a
        # working SIANA, and have no fleet queue in front of it.
        self.initialize("pi")
        out = self.assertRefused(self.start("--harness", "claude"),
                                 "no queue integration", "just init")
        self.assertIn("claude", out)
        self.assertFalse(os.path.exists(self.argv_file))

    def test_a_home_with_no_integration_at_all_is_refused(self):
        # And named as the install it is missing, not as the harness: nothing here
        # starts, so there is no one harness to point at.
        # Built by installing one and taking it away, because `initialize` has no
        # way to write a home with none: neither does `init`, which refuses before
        # it writes anything when it can find no harness to install for.
        self.initialize("pi")
        os.remove(self.at(".pi", "settings.json"))
        self.assertRefused(self.start(), "no harness is installed", "just init")
        self.assertFalse(os.path.exists(self.argv_file))

    def test_an_unknown_harness_is_refused_rather_than_guessed(self):
        # A name nobody supports is a typo or a harness this distro has never
        # started. Falling back to the default would open a session the captain
        # did not ask for and say nothing about it.
        self.initialize("pi", "claude")
        self.assertRefused(self.start("--harness", "codex"),
                           "unknown harness: codex", "pi or claude")
        self.assertFalse(os.path.exists(self.argv_file))

    def test_the_flag_without_a_name_is_refused(self):
        # Left to shift past the end it would take the captain's next argument, or
        # nothing, and start the default while looking like it had been told.
        self.initialize("pi", "claude")
        self.assertRefused(self.start("--harness"), "--harness needs a name")
        self.assertFalse(os.path.exists(self.argv_file))

    def test_a_harness_that_is_not_installed_is_refused(self):
        # The integration is there and the command is not: a home installed for a
        # harness the captain has since removed.
        self.initialize("claude")
        out = self.assertRefused(
            self.start("--harness", "claude", PATH=f"{BIN}:/usr/bin:/bin"),
            "missing: claude")
        self.assertIn("asked to start in", out)

    def test_a_refused_start_leaves_no_session_behind(self):
        # Every refusal here lands before the leader lock. After it, the claim
        # would be taken and released, which a watcher reads as a SIANA that
        # started and stopped, and the captain reads as a fleet that is under way.
        self.initialize("pi", "claude")
        self.assertRefused(self.start("--harness", "codex"), "unknown harness")
        self.assertFalse(os.path.exists(self.at("session")))


if __name__ == "__main__":
    unittest.main()
