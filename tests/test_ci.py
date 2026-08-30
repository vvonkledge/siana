"""What CI is for: running this project's own verify command, on a clean machine,
against the commit somebody actually submitted.

The workflow is YAML and this suite has no YAML parser, because `tests/` is
standard library and nothing else. So these read it as text, which is enough for
the rules worth holding. Each one is a line that could be edited in a hurry and
pass review unnoticed: a check that runs something weaker than `just test`, a write
scope nothing here needs, an action pinned to a tag somebody else can move.
"""

import os
import re
import unittest

from helpers import DISTRO

WORKFLOW = os.path.join(DISTRO, ".github", "workflows", "ci.yml")

# What ORDERS.md names as how work here is checked. CI runs this, or CI is not
# checking the thing the captain thinks it is.
VERIFY = "just test"


def text():
    with open(WORKFLOW) as fh:
        return fh.read()


def block(name):
    """One top-level block of the workflow, stripped, without its comments.

    Indentation is the only structure a text read can lean on, so a block runs
    from its key to the next line that starts in column zero.
    """
    out, inside = [], False
    for line in text().splitlines():
        if line.startswith(f"{name}:"):
            inside = True
            continue
        if inside:
            if line and not line[0].isspace():
                break
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                out.append(stripped)
    return out


class Workflow(unittest.TestCase):
    """The rules the check has to keep to be worth requiring before a merge."""

    def test_it_runs_the_projects_own_verify_command_unchanged(self):
        # A check that runs something narrower than the command ORDERS.md names is
        # a green that means less than everyone reading it will assume.
        self.assertIn(f"run: {VERIFY}",
                      [line.lstrip("- ") for line in block("jobs")])

    def test_it_asks_for_no_more_than_read_access_to_the_repository(self):
        # It clones, installs and runs tests. A write scope would be reach this
        # workflow has no use for and a compromised step would.
        self.assertEqual(block("permissions"), ["contents: read"])

    def test_it_runs_on_pull_requests_to_main_and_on_pushes_to_main(self):
        # Both halves matter. The pull request run is what gates a merge; the push
        # run is what makes `main` itself a verdict later work can be published
        # against, rather than a branch nothing has ever checked.
        on = block("on")
        self.assertIn("pull_request:", on)
        self.assertIn("push:", on)
        self.assertEqual([line for line in on if line.startswith("branches:")],
                         ["branches: [main]", "branches: [main]"])

    def test_every_action_is_pinned_to_a_commit_and_never_to_a_tag(self):
        # A tag can be moved under us, so `@v4` is whatever that account pushes
        # next and not what this branch was reviewed against.
        used = re.findall(r"uses:\s*(\S+)", text())
        self.assertTrue(used, "the workflow uses no actions at all")
        for action in used:
            self.assertRegex(action, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$",
                             f"{action} is not pinned to a commit")

    def test_the_stores_the_suite_drives_are_pinned_to_a_commit(self):
        # The suite drives a real `tasks` and `datafile` rather than stubs, so they
        # are under test here too. Tracking their default branch would make a red
        # run mean "something, somewhere, changed" and stop it being reproducible.
        for ref in ("TASKS_REF", "DATAFILE_REF"):
            self.assertRegex(text(), rf"{ref}:\s*[0-9a-f]{{40}}\b",
                             f"{ref} is not pinned to a commit")

    def test_it_installs_a_node_that_can_run_the_wake_extension(self):
        # The extension is TypeScript and `tests/test_wake.py` skips itself on a
        # node that cannot strip types. A runner whose node happened to be too old
        # would not turn this workflow red; it would turn the whole extension half
        # of the wake path green by never running it. So the version is pinned like
        # the stores are, and this is what says it has to be.
        self.assertIn("uses: actions/setup-node", "\n".join(block("jobs")))
        self.assertRegex(text(), r'NODE_VERSION:\s*"\d+\.\d+\.\d+"',
                         "NODE_VERSION is not pinned to an exact version")

    def test_it_tests_the_submitted_commit_and_not_githubs_merge_commit(self):
        # On a pull request the default ref is a merge commit GitHub synthesises.
        # Nobody submitted it and nobody can check it out again, so a red run on it
        # names a commit that is not on the branch and cannot be reproduced there.
        self.assertIn(
            "ref: ${{ github.event.pull_request.head.sha || github.sha }}",
            block("jobs"))

    def test_a_superseded_run_is_cancelled_without_touching_another_branch(self):
        # The group has to carry the ref. Keyed on the workflow alone, one branch's
        # push would cancel every other branch's run in flight.
        group = [line for line in block("concurrency") if line.startswith("group:")]
        self.assertEqual(len(group), 1, "concurrency has no group to key on")
        self.assertIn("github.ref", group[0])

    def test_the_job_cannot_hold_a_runner_forever(self):
        # Without a timeout a hang sits there until GitHub's own limit, hours later,
        # with nobody told why. The value is not asserted, only its presence: the
        # suite is fifteen to twenty minutes and grows with the fleet, so a number
        # pinned here would be a budget this file was quietly enforcing rather than
        # the hang guard the workflow says it is.
        self.assertTrue(
            [line for line in block("jobs") if line.startswith("timeout-minutes:")],
            "the job has no timeout")


if __name__ == "__main__":
    unittest.main()
