"""One `docs` ship task, all the way through, on the branch its brief names.

The branch a ship task's work lives on is stated once, when SIANA briefs it, and
read from there by everything downstream: dispatch, the pipeline that validates it,
the QA task cut from it, publishing, retiring, reaping. A partial rename would leave
one of those looking somewhere else, and the failure is silent in the worst
direction - a QA minion cut from a different head than the one that passed, or a
publish of work nobody accepted. So this drives the whole chain against one task and
never rebuilds the branch name for any step of it.

herdr is scripted, as everywhere else in this suite, but its `worktree.create` makes
a real worktree here: the point is that the branch each command goes looking for is
the branch that actually exists.
"""

import json
import os
import re
import shutil
import stat
import unittest

from fake_herdr import FakeHerdr
from helpers import BIN, HomeTest

# The line a ship brief records its branch on. Read rather than rebuilt, because
# rebuilding it here would be the test agreeing with itself about the one thing it
# is checking.
RECORDED = re.compile(r"^\s+branch\s+(\S+)\s*$", re.M)


class TypedFlow(HomeTest):

    TASK = "write the guide"
    TYPE = "docs"

    def setUp(self):
        super().setUp()
        self.contract("projects")
        self.template("brief-ship.md", "brief-qa.md", "orders.md", "review.md")
        self.queue()
        self.repo = self.at("repo")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main", ".")
        self.git("config", "user.email", "minion@example.com")
        self.git("config", "user.name", "minion")
        self.write(self.repo, "a.txt", "base\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")
        self.project("proj", path=self.repo, ship="exit 0", qa="exit 0",
                     pipeline="true", target="main")
        self.herdr = FakeHerdr().start()
        self.addCleanup(self.herdr.stop)
        self.reviewer = self.fake_reviewer()

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

    def tasks(self, *args, **kw):
        return self.assertAccepted(
            self.run_cmd(["tasks", "--file", self.at("tasks.jsonl"), *args], **kw))

    def record(self, task_id):
        found = None
        with open(self.at("tasks.jsonl")) as fh:
            for line in fh:
                rec = json.loads(line)
                if rec.get("id") == task_id:
                    found = rec
        self.assertIsNotNone(found, f"no record for {task_id}")
        return found

    def ids(self):
        out = self.tasks("list")
        return [line.strip().split(",")[0] for line in out.splitlines()
                if line.startswith("  ") and "," in line]

    def fake_reviewer(self):
        bindir = self.at("fakebin")
        os.makedirs(bindir)
        target = os.path.join(bindir, "claude")
        shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "fake_reviewer.py"), target)
        os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR)
        return bindir

    def serve_worktrees(self):
        """A herdr whose `worktree.create` really creates one, so every command
        after this is looking for a branch git actually has."""
        def create(params):
            path = self.at("wt", params["branch"].replace("/", "_"))
            self.git("worktree", "add", "-q", "-b", params["branch"], path,
                     *([params["base"]] if params.get("base") else []))
            return {"workspace": {"workspace_id": "ws1"},
                    "worktree": {"path": path},
                    "root_pane": {"pane_id": "w1:p1"}}

        ready = {"agent": {"agent": "claude", "interactive_ready": True,
                           "agent_status": "idle"}}
        working = {"agent": {"agent": "claude", "interactive_ready": True,
                             "agent_status": "working"}}
        self.herdr.reply("worktree.create", create)
        self.herdr.reply("pane.split", {"pane": {"pane_id": "w1:p2"}})
        self.herdr.reply("agent.get", ready, working)

    def fill(self, task_id):
        """Every `{...}` SIANA is asked to fill, filled."""
        path = self.at("briefs", f"{task_id}.md")
        with open(path) as fh:
            brief = fh.read()
        for marker, text in (("{TASK}", "Write the guide."),
                             ("{DONE}", "The guide is there."),
                             ("{BACKGROUND}", "There is no guide."),
                             ("{SCOPE}", "Nothing else.")):
            brief = brief.replace(marker, text)
        with open(path, "w") as fh:
            fh.write(brief)

    def briefed_branch(self, task_id):
        """The branch the brief records, taken off the brief itself."""
        with open(self.at("briefs", f"{task_id}.md")) as fh:
            named = RECORDED.findall(fh.read())
        self.assertEqual(len(named), 1, f"{task_id}'s brief records {named}")
        return named[0]

    # -- the flow ---------------------------------------------------------------

    def test_a_docs_ship_task_runs_on_the_branch_its_brief_names(self):
        ship = self.tasks("add", self.TASK, "--project", "proj", "--cwd", self.repo,
                          "--verify", "siana-pipeline check")
        ship = next(line.split(": ", 1)[1] for line in ship.splitlines()
                    if line.startswith("id: "))

        # 1. Briefing is where the type is stated, and the only place it is.
        out = self.assertAccepted(
            self.run_bin("siana-brief", ship, "--ship", "--type", self.TYPE))
        branch = self.briefed_branch(ship)
        self.assertEqual(branch, f"siana/{self.TYPE}/{ship}")
        self.assertIn(branch, out)

        # SIANA fills the rest of the brief, as it would before dispatching. The
        # branch is not one of them: the script wrote that, and publishing refuses a
        # brief still carrying a placeholder.
        self.fill(ship)

        # 2. The QA task is queued behind it, cut from that same branch.
        qa = next(i for i in self.ids() if i != ship)
        self.assertEqual(self.record(qa)["base"], branch)
        with open(self.at("briefs", f"{qa}.md")) as fh:
            self.assertIn(branch, fh.read())

        # 3. Dispatch makes it, and says so.
        self.serve_worktrees()
        out = self.assertAccepted(self.run_bin(
            "siana-dispatch", ship,
            env={"HERDR_SOCKET_PATH": self.herdr.path,
                 "SIANA_TASKS_FILE": self.at("tasks.jsonl")}))
        binding = json.loads(out[out.index("{"):])
        self.assertEqual(binding["branch"], branch)
        worktree = binding["cwd"]
        self.assertEqual(
            self.git("rev-parse", "--abbrev-ref", "HEAD", cwd=worktree).strip(),
            branch)

        # 4. A run validates that branch, and records which one it validated.
        self.write(worktree, "guide.md", "the guide\n")
        self.git("add", "-A", cwd=worktree)
        self.git("commit", "-qm", "docs: write the guide", cwd=worktree)
        head = self.git("rev-parse", "HEAD", cwd=worktree).strip()
        pipe_env = {"PATH": self.distro_path(self.reviewer),
                    "SIANA_TASK_ID": ship, "FAKE_FINDINGS": "[]",
                    "SIANA_TASKS_FILE": self.at("tasks.jsonl")}
        self.assertAccepted(self.run_bin("siana-pipeline", "run", cwd=worktree,
                                         env=pipe_env))
        with open(self.at("pipeline", f"{ship}.json")) as fh:
            recorded = json.load(fh)
        self.assertEqual(recorded["branch"], branch)
        self.assertEqual(recorded["head"], head)
        self.assertEqual(recorded["verdict"], "passed")

        # 5. The verify reads that record, and the queue accepts the work on it.
        self.assertAccepted(self.run_bin("siana-pipeline", "check", cwd=worktree,
                                         env=pipe_env))

        # The queue runs that verify by name, through a shell, so `bin/` being on
        # the PATH it runs it with is the whole of what makes the next line drive
        # this checkout. Asserted by taking it back off, because the alternative is
        # a green that came from wherever `siana-pipeline` happened to be installed
        # - which is what this suite did until a runner with no SIANA on it read
        # exit 127 out of the same call.
        bare = os.pathsep.join(d for d in pipe_env["PATH"].split(os.pathsep)
                               if d != BIN)
        self.assertRefused(
            self.run_cmd(["tasks", "--file", self.at("tasks.jsonl"), "done", ship,
                          "--reason", "wrote it"], env={**pipe_env, "PATH": bare}),
            "siana-pipeline check", "127")

        self.tasks("done", ship, "--reason", "wrote it", env=pipe_env)
        self.assertEqual(self.record(ship)["status"], "done")

        # 6. A QA verdict authorises publishing that branch and no other. The
        #    comparison behind that is between the QA task's base and the ship
        #    task's branch, so a typed one has to survive it.
        self.git("remote", "add", "origin", "git@github.com:o/r.git")
        self.tasks("start", qa, "--owner", "claude@w1:p9")
        self.write(self.at("reports"), f"{qa}.md", "it holds up\n")
        self.tasks("done", qa, "--reason", "it holds up",
                   env={"SIANA_TASK_ID": qa})
        out = self.assertAccepted(self.run_bin("siana-publish", qa, "--dry-run"))
        self.assertIn(f"branch:  {branch}", out)
        self.git("remote", "remove", "origin")

        # 7. The captain lands it. Reaping now finds the branch and hands it back as
        #    the task that owns it, because its worktree is retire's to remove.
        self.git("merge", "-q", "--no-ff", "-m", "land it", branch)
        text = self.assertAccepted(self.run_bin("siana-reap", "proj", "--yes"))
        self.assertIn(branch, text)
        self.assertIn(f"siana-retire {ship}", text)
        self.assertTrue(os.path.isdir(worktree))

        # 8. Retire finds the tree by the same name, and leaves the branch alone.
        out = self.assertAccepted(self.run_bin("siana-retire", ship))
        self.assertIn(f"kept   {branch}", out)
        self.assertFalse(os.path.isdir(worktree))

        # 9. And then the branch is reapable, which is the end of it.
        text = self.assertAccepted(self.run_bin("siana-reap", "proj", "--yes"))
        self.assertIn(f"{branch}  reaped", text)
        self.assertEqual(self.git("branch", "--list", branch).strip(), "")

    def test_a_task_briefed_before_types_existed_still_dispatches(self):
        """Nothing already in flight is migrated. A brief with no branch line - every
        brief written before this convention - reads as `siana/<task-id>`, which is
        where dispatch has always put one."""
        ship = self.tasks("add", "make a thing", "--project", "proj",
                          "--cwd", self.repo, "--verify", "true")
        ship = next(line.split(": ", 1)[1] for line in ship.splitlines()
                    if line.startswith("id: "))
        self.write(self.at("briefs"), f"{ship}.md",
                   "# Brief\n\n## Delivery: ship\n\nYour work lands.\n")

        self.serve_worktrees()
        out = self.assertAccepted(self.run_bin(
            "siana-dispatch", ship,
            env={"HERDR_SOCKET_PATH": self.herdr.path,
                 "SIANA_TASKS_FILE": self.at("tasks.jsonl")}))

        self.assertEqual(json.loads(out[out.index("{"):])["branch"],
                         f"siana/{ship}")

    def test_a_branch_in_the_way_is_refused_before_anything_is_created(self):
        """git stores a ref as a path, so a task whose id is a commit type takes the
        single-segment name and blocks every ship branch of that type. Refused here,
        because the same refusal from git arrives through herdr after a workspace
        exists, as a lock failure naming neither branch."""
        self.git("branch", f"siana/{self.TYPE}")
        ship = self.tasks("add", self.TASK, "--project", "proj", "--cwd", self.repo,
                          "--verify", "siana-pipeline check")
        ship = next(line.split(": ", 1)[1] for line in ship.splitlines()
                    if line.startswith("id: "))
        self.assertAccepted(
            self.run_bin("siana-brief", ship, "--ship", "--type", self.TYPE))

        self.serve_worktrees()
        out = self.run_bin("siana-dispatch", ship,
                           env={"HERDR_SOCKET_PATH": self.herdr.path,
                                "SIANA_TASKS_FILE": self.at("tasks.jsonl")})

        self.assertRefused(out, f"cannot hold a branch called siana/{self.TYPE}/{ship}",
                           f"siana/{self.TYPE}")
        # No claim, and no worktree: herdr was never asked for one.
        self.assertEqual(self.record(ship)["status"], "todo")
        self.assertEqual(self.herdr.calls_to("worktree.create"), [])


if __name__ == "__main__":
    unittest.main()
