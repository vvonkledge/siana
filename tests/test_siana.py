"""`siana`: the captain's one interface, and the lock that keeps it one.

Two invariants. What SIANA is told at startup has to be in its system prompt, in
one flag, because a second flag is silently dropped and SIANA would start believing
it had context it never received. And exactly one SIANA leads the fleet, because a
second would race the first for every task in the queue while the captain talked to
one of the two with no way to tell which.

`pi` is stood in for. Starting a real agent session is not what is under test here,
and the stub is what makes the flags it was called with readable.
"""

import os
import signal
import subprocess
import unittest

from helpers import BIN, HomeTest, until

STUB = """#!/bin/sh
# Stands in for `pi`. Records what it was called with, and holds the session open
# when asked to, which is what makes the leader lock observable.
printf '%s\\0' "$@" > "$PI_STUB_ARGV"
[ -n "${PI_STUB_HOLD:-}" ] || exit 0
while :; do sleep 0.2; done
"""


class Siana(HomeTest):

    def setUp(self):
        super().setUp()
        self.stub_dir = self.at("stub")
        os.makedirs(self.stub_dir)
        stub = os.path.join(self.stub_dir, "pi")
        with open(stub, "w") as fh:
            fh.write(STUB)
        os.chmod(stub, 0o755)
        self.argv_file = self.at("pi-argv")
        # `siana` calls `siana-owe` by name, so the distro's own commands have to be
        # findable the way an install makes them findable.
        self.path = f"{self.stub_dir}:{BIN}:{os.environ['PATH']}"

    def initialize(self):
        with open(self.at("siana.env"), "w") as fh:
            fh.write(f"SIANA_HOME={self.home}\n")

    def env(self, **extra):
        # HERDR_PANE_ID is emptied unless a test sets it. These tests are themselves
        # run inside herdr, and inheriting its pane would make "SIANA outside herdr"
        # unreachable here while staying perfectly reachable for the captain.
        return {"HERDR_PANE_ID": "", "PATH": self.path,
                "PI_STUB_ARGV": self.argv_file, **extra}

    def start(self, *args, **extra):
        return self.run_bin("siana", *args, env=self.env(**extra))

    def hold(self, **extra):
        """A SIANA that stays up, in its own process group so the stub's `sleep`
        children go with it."""
        e = dict(os.environ)
        e.update(SIANA_HOME=self.home, PI_STUB_HOLD="1", **self.env(**extra))
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

    def pi_argv(self):
        self.assertTrue(until(lambda: os.path.exists(self.argv_file)),
                        "pi was never started")
        with open(self.argv_file) as fh:
            return [a for a in fh.read().split("\0") if a]

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
        prompt = "\n".join(self.pi_argv())
        self.assertIn("The captain's projects", prompt)
        self.assertIn("workshop", prompt)
        self.assertIn("just check", prompt)

    def test_the_worktree_policy_is_pinned_into_the_prompt(self):
        # The default view shows only the first few fields and would drop it. A
        # registry that looks complete while hiding a project's isolation policy is
        # worse than no registry.
        self.project("workshop", worktree="false")
        self.assertAccepted(self.start())
        self.assertIn("worktree", "\n".join(self.pi_argv()))

    def test_what_siana_owes_reaches_the_system_prompt(self):
        self.assertAccepted(self.run_bin("siana-owe", "promise", "Report at noon"))
        self.assertAccepted(self.start())
        prompt = "\n".join(self.pi_argv())
        self.assertIn("What SIANA owes", prompt)
        self.assertIn("Report at noon", prompt)

    def test_owing_nothing_is_stated_rather_than_left_out(self):
        # Its absence would read as never having asked.
        self.assertAccepted(self.start())
        prompt = "\n".join(self.pi_argv())
        self.assertIn("What SIANA owes", prompt)
        self.assertIn("owed     nothing", prompt)

    def test_both_halves_travel_in_one_flag(self):
        # Passed twice, only the last survives and the first is dropped in silence.
        # One flag cannot have that bug.
        self.project("workshop")
        self.assertAccepted(self.run_bin("siana-owe", "promise", "Report at noon"))
        self.assertAccepted(self.start())
        argv = self.pi_argv()
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
        self.assertIn("--approve", self.pi_argv())

    def test_the_captain_s_own_arguments_are_passed_through(self):
        self.assertAccepted(self.start("--model", "opus"))
        argv = self.pi_argv()
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

    def test_the_session_records_the_pid_and_the_pane_herdr_gave_it(self):
        # The pane comes from the environment herdr sets, never from searching for
        # an agent that looks about right: herdr's labels are not unique.
        p = self.hold(HERDR_PANE_ID="w3D:p2")
        self.assertEqual(self.session(), {"SIANA_PID": str(p.pid),
                                          "SIANA_PANE": "w3D:p2"})

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
        self.assertIn("--approve", self.pi_argv())

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
        self.assertIn("--approve", self.pi_argv())

    def test_an_empty_session_file_does_not_lock_the_captain_out(self):
        open(self.at("session"), "w").close()
        self.assertAccepted(self.start())
        self.assertIn("--approve", self.pi_argv())


if __name__ == "__main__":
    unittest.main()
