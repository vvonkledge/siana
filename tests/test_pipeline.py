"""What `siana-pipeline` records, and what it refuses to call a pass.

The whole design rests on one comparison: a run records the commit it validated, and
the verify refuses when the branch has moved off it. Everything else here is a way a
green could be produced by something other than a run that earned it - a review that
never happened, a suite that was never reached, a record from a previous head.

The reviewer is the one thing scripted, in `fake_reviewer.py`. A real one is an agent:
it answers when it answers and never the same way twice, so the answers that matter
most here - it wrote nothing, it wrote nonsense, it found something nobody here can
settle - are exactly the ones it could not be made to give on cue. Nothing else is
faked: a real git repository, a real queue, a real registry.
"""

import json
import os
import shutil
import stat
import unittest

from helpers import HomeTest, script

pipeline = script("siana-pipeline")

PASSED = {"verdict": "passed", "branch": "siana/make-thing", "head": "a" * 40}


class Stale(unittest.TestCase):
    """The comparison that stands between a QA minion and a head nobody validated."""

    def test_a_run_that_matches_is_not_stale(self):
        self.assertIsNone(pipeline.stale(PASSED, "siana/make-thing", "a" * 40))

    def test_a_moved_branch(self):
        # The ordinary way to get this wrong: commit once more after a passing run.
        # The QA worktree is cut from the branch, so that commit would be reviewed
        # wearing this task's green.
        why = pipeline.stale(PASSED, "siana/make-thing", "b" * 40)
        self.assertIn("it validated aaaaaaaaaaaa", why)
        self.assertIn("now at bbbbbbbbbbbb", why)

    def test_a_different_branch(self):
        why = pipeline.stale(PASSED, "siana/other", "a" * 40)
        self.assertIn("this worktree is on siana/other", why)

    def test_a_run_that_did_not_pass(self):
        # A failed record must never read as absence of a record either: "no run" and
        # "the run said no" are fixed differently.
        why = pipeline.stale({**PASSED, "verdict": "failed"}, "siana/make-thing",
                             "a" * 40)
        self.assertIn("did not pass", why)


class Findings(unittest.TestCase):
    """Every way a reviewer can fail to review is the same failure: nobody read the
    change. None of them is a pass."""

    def parse(self, payload):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write(payload)
            path = fh.name
        self.addCleanup(os.unlink, path)
        return pipeline.read_findings(path)

    def test_no_file_is_not_a_pass(self):
        findings, problem = pipeline.read_findings("/nonexistent/x.findings.json")
        self.assertIsNone(findings)
        self.assertIn("no findings file", problem)

    def test_unreadable_file_is_not_a_pass(self):
        findings, problem = self.parse("not json at all")
        self.assertIsNone(findings)
        self.assertIn("unreadable", problem)

    def test_a_payload_with_no_findings_list(self):
        findings, problem = self.parse('{"verdict": "looks fine"}')
        self.assertIsNone(findings)
        self.assertIn("no `findings` list", problem)

    def test_a_finding_that_says_nothing(self):
        # Half a finding reaches the minion as a round it cannot act on, so it is
        # refused here rather than printed as an empty line.
        findings, problem = self.parse('{"findings": [{"where": "a.py:1"}]}')
        self.assertIsNone(findings)
        self.assertIn("says nothing", problem)

    def test_an_empty_list_is_a_pass(self):
        findings, problem = self.parse('{"findings": []}')
        self.assertIsNone(problem)
        self.assertEqual(findings, [])

    def test_a_finding_keeps_its_decide_flag(self):
        findings, problem = self.parse(
            '{"findings": [{"where": "a.py:1", "what": "wrong", "decide": true},'
            ' {"what": "also wrong"}]}')
        self.assertIsNone(problem)
        self.assertEqual([f["decide"] for f in findings], [True, False])
        self.assertEqual(findings[1]["where"], "?")


class Pipeline(HomeTest):
    """A project whose rigor is a driven pipeline, and a minion's worktree in it."""

    TASK = "make-thing"

    def setUp(self):
        super().setUp()
        self.contract("projects")
        self.queue()
        self.template("review.md")

        self.repo = self.at("repo")
        os.makedirs(self.repo)
        self.git("init", "-q", "-b", "main", ".")
        self.git("config", "user.email", "minion@example.com")
        self.git("config", "user.name", "minion")
        self.write(self.repo, ".gitignore", "litter/\n")
        self.write(self.repo, "a.txt", "base\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "base")

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

    def tasks(self, *args):
        return self.assertAccepted(
            self.run_cmd(["tasks", "--file", self.at("tasks.jsonl"), *args]))

    def fake_reviewer(self):
        """A `claude` earlier on PATH than any real one, driven by the environment.

        Put on PATH rather than injected through a flag: the command builds the
        reviewer's argv itself, and a seam for the suite would be a seam a run could
        take too."""
        bindir = self.at("fakebin")
        os.makedirs(bindir)
        target = os.path.join(bindir, "claude")
        shutil.copy(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "fake_reviewer.py"), target)
        os.chmod(target, os.stat(target).st_mode | stat.S_IXUSR)
        return bindir

    def project(self, handle="proj", **fields):
        fields.setdefault("path", self.repo)
        fields.setdefault("ship", "exit 0")
        fields.setdefault("pipeline", "true")
        fields.setdefault("target", "main")
        super().project(handle, **fields)

    def dispatched(self, task_id=None, project="proj", base="main", brief=True):
        """A task in the state a real dispatch leaves it in: its own worktree on
        `siana/<id>`, and a brief the reviewer is meant to judge it against."""
        task_id = task_id or self.TASK
        self.tasks("add", task_id.replace("-", " "), "--verify",
                   "siana-pipeline check",
                   *(["--project", project] if project else []),
                   *(["--base", base] if base else []))
        worktree = self.at("wt", task_id)
        self.git("worktree", "add", "-q", "-b", f"siana/{task_id}", worktree,
                 *([base] if base else []))
        self.tasks("start", task_id, "--owner", "claude@w1:p1", "--cwd", worktree)
        if brief:
            self.write(self.at("briefs"), f"{task_id}.md",
                       "# Brief\n\n## The task\n\nAdd b.txt.\n")
        return worktree

    def diverged(self):
        """A side branch the work starts on, and a main line that has moved since.

        The shape a rebase leaves behind: replaying the work onto `main` succeeds by
        putting it where `old` is not, so the ref the task recorded as its base ends
        up sharing nothing with the result but a fork point."""
        self.git("checkout", "-q", "-b", "old")
        self.write(self.repo, "old.txt", "the line the work started on\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "old line")
        self.git("checkout", "-q", "main")
        self.write(self.repo, "main.txt", "the line it is replayed onto\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "main moves on")
        return "old"

    def commit_in(self, worktree, name="b.txt", text="work\n"):
        self.write(worktree, name, text)
        self.git("add", "-A", cwd=worktree)
        self.git("commit", "-qm", "work", cwd=worktree)
        return self.git("rev-parse", "HEAD", cwd=worktree).strip()

    def pipe(self, *args, cwd=None, task=None, findings="[]", **env):
        e = {"PATH": self.reviewer + os.pathsep + os.environ["PATH"],
             "SIANA_TASK_ID": task or self.TASK,
             "FAKE_FINDINGS": findings,
             "FAKE_PROMPT_OUT": self.at("prompt.txt")}
        e.update(env)
        return self.run_bin("siana-pipeline", *args, cwd=cwd, env=e)

    def record(self, task_id=None):
        with open(self.at("pipeline", f"{task_id or self.TASK}.json")) as fh:
            return json.load(fh)

    def prompt(self):
        with open(self.at("prompt.txt")) as fh:
            return fh.read()

    def reviewed(self):
        return os.path.exists(self.at("prompt.txt"))


class Run(Pipeline):
    """What a run refuses to start on, and what it writes when it finishes."""

    def test_a_project_that_is_not_pipeline_driven(self):
        # A green recorded here is a green nothing reads: the tasks in such a project
        # verify with a command. Refused rather than written.
        self.project(pipeline="false", ship="just test")
        wt = self.dispatched()
        out = self.pipe("run", cwd=wt)
        self.assertRefused(out, "not validated by a driven pipeline", "just test")
        self.assertFalse(os.path.exists(self.at("pipeline", f"{self.TASK}.json")))

    def test_the_wrong_branch(self):
        self.project()
        wt = self.dispatched()
        self.git("checkout", "-q", "-b", "sidetrack", cwd=wt)
        out = self.pipe("run", cwd=wt)
        self.assertRefused(out, "is on sidetrack", f"siana/{self.TASK}")

    def test_an_uncommitted_change(self):
        # A run validates a commit and records it, so work that is not in that commit
        # would be passed without having been seen.
        self.project()
        wt = self.dispatched()
        self.write(wt, "a.txt", "edited\n")
        out = self.pipe("run", cwd=wt)
        self.assertRefused(out, "uncommitted changes", "a.txt")
        self.assertEqual(out.returncode, 1, "yours to fix, not a block")
        self.assertFalse(self.reviewed())

    def test_untracked_litter_does_not_stop_a_run(self):
        # Build litter is `siana-retire`'s problem. What matters here is only whether
        # the head is the whole of what the minion committed.
        self.project()
        wt = self.dispatched()
        self.write(wt, "litter/x.pyc", "junk\n")
        self.write(wt, "scratch.txt", "not added\n")
        self.assertAccepted(self.pipe("run", cwd=wt))

    def test_a_task_with_no_fork_point(self):
        # Reviewing a diff against a guessed base judges work nobody did here.
        self.project(target="")
        wt = self.dispatched(base=None)
        out = self.pipe("run", cwd=wt)
        self.assertRefused(out, "cannot tell what", "forked from")

    def test_a_base_the_head_was_replayed_off(self):
        """The base is an ancestor or there is no review to run.

        Reproduces the failure this refusal exists for: the task recorded a real ref
        as its base, the work was rebased onto another line, and every later reader
        of `base..HEAD` got that whole line instead of the one commit."""
        marker = self.at("ship-ran")
        self.project(ship=f"touch {marker}")
        self.diverged()
        wt = self.dispatched(base="old")
        self.commit_in(wt)
        self.git("rebase", "-q", "main", cwd=wt)
        head = self.git("rev-parse", "HEAD", cwd=wt).strip()
        out = self.pipe("run", cwd=wt)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertRefused(out, "base old is not an ancestor", head[:12],
                           "every commit either line has made since")
        self.assertFalse(os.path.exists(marker), "the ship command ran anyway")
        self.assertFalse(self.reviewed())
        self.assertEqual(self.record()["verdict"], "failed")

    def test_a_base_that_moves_off_a_head_that_did_not(self):
        # The one way a pass could outlive this refusal. A ref can stop being an
        # ancestor without the branch moving at all, and `check` compares the head,
        # so the refusal is recorded rather than only printed.
        self.project()
        self.diverged()
        wt = self.dispatched(base="old")
        self.commit_in(wt)
        self.assertAccepted(self.pipe("run", cwd=wt))
        self.git("branch", "-f", "old", "main")
        self.assertEqual(self.pipe("run", cwd=wt).returncode, 2)
        self.assertRefused(self.pipe("check", cwd=wt), "did not pass",
                           "is not an ancestor")

    def test_the_projects_target_is_a_base_like_any_other(self):
        # The fallback is measured the same way. A task queued with no base of its
        # own is reviewed against `target`, and against that commit only.
        self.project(target="main")
        wt = self.dispatched(base=None)
        self.commit_in(wt)
        self.assertAccepted(self.pipe("run", cwd=wt))
        self.assertEqual(self.record()["base"],
                         self.git("rev-parse", "main").strip())

    def test_a_target_the_branch_is_not_on(self):
        self.project(target="old")
        self.diverged()
        wt = self.dispatched(base=None)
        self.commit_in(wt)
        out = self.pipe("run", cwd=wt)
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertRefused(out, "base old is not an ancestor")

    def test_the_base_is_pinned_before_anything_reads_it(self):
        """A ref moves, and a commit does not.

        What the reviewer was given and what the record says it measured have to keep
        meaning the same work after the run, or the green describes a range nobody
        can reconstruct."""
        self.project()
        self.diverged()
        wt = self.dispatched(base="old")
        self.commit_in(wt)
        pinned = self.git("rev-parse", "old").strip()
        self.assertAccepted(self.pipe("run", cwd=wt))
        self.assertIn(pinned, self.prompt())
        self.assertNotIn("base    old", self.prompt())
        self.assertEqual(self.record()["base"], pinned)
        self.git("branch", "-D", "old")
        self.assertEqual(self.record()["base"], pinned)
        self.assertIn(pinned, self.assertAccepted(self.pipe("check", cwd=wt)))

    def test_a_missing_brief(self):
        self.project()
        wt = self.dispatched(brief=False)
        out = self.pipe("run", cwd=wt)
        self.assertRefused(out, "no brief at", "judges by its own lights")
        self.assertFalse(self.reviewed())

    def test_a_red_suite_stops_before_the_reviewer(self):
        # The suite is exact and takes a minute; the reviewer is an agent and costs
        # tokens. Spending them on a change that does not work yet buys nothing.
        self.project(ship="exit 1")
        wt = self.dispatched()
        out = self.pipe("run", cwd=wt)
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("test failed", out.stdout + out.stderr)
        self.assertFalse(self.reviewed())
        self.assertEqual(self.record()["verdict"], "failed")

    def test_a_pass_records_the_head_it_validated(self):
        self.project()
        wt = self.dispatched()
        head = self.commit_in(wt)
        out = self.pipe("run", cwd=wt)
        self.assertAccepted(out)
        record = self.record()
        self.assertEqual(record["verdict"], "passed")
        self.assertEqual(record["head"], head)
        self.assertEqual(record["branch"], f"siana/{self.TASK}")
        self.assertEqual([s["name"] for s in record["steps"]], ["test", "review"])

    def test_the_reviewer_is_told_what_to_read(self):
        # Without the brief it judges by its own lights, and without the base it
        # reviews somebody else's commits as well as this task's.
        self.project()
        wt = self.dispatched()
        self.commit_in(wt)
        self.assertAccepted(self.pipe("run", cwd=wt))
        prompt = self.prompt()
        self.assertIn(self.at("briefs", f"{self.TASK}.md"), prompt)
        self.assertIn(f"siana/{self.TASK}", prompt)
        self.assertIn(f"base    {self.git('rev-parse', 'main').strip()}", prompt)
        self.assertNotIn("{", prompt.split("## How to report")[0])

    def test_the_reviewer_is_told_where_the_conventions_are(self):
        # A project's own orders are where most of what it would call a finding
        # lives, and they are the one thing not readable out of the diff.
        self.project(orders="ORDERS.md")
        wt = self.dispatched()
        self.commit_in(wt)
        self.assertAccepted(self.pipe("run", cwd=wt))
        self.assertIn(os.path.join(wt, "ORDERS.md"), self.prompt())

    def test_a_project_with_no_orders_says_so(self):
        # Rather than an empty line the reviewer would read as a path it could not
        # open, or as a file it simply failed to find.
        self.project()
        wt = self.dispatched()
        self.commit_in(wt)
        self.assertAccepted(self.pipe("run", cwd=wt))
        self.assertIn("(this project has none)", self.prompt())

    def test_findings_to_fix(self):
        self.project()
        wt = self.dispatched()
        self.commit_in(wt)
        out = self.pipe("run", cwd=wt, findings=json.dumps(
            [{"where": "b.txt:1", "what": "this is wrong"}]))
        self.assertEqual(out.returncode, 1, out.stdout + out.stderr)
        self.assertIn("this is wrong", out.stdout)
        self.assertEqual(self.record()["verdict"], "failed")

    def test_a_finding_only_a_human_can_settle(self):
        # Exit 2 is the minion's whole instruction to `block` rather than fix. A
        # finding the pipeline marked for a human is a decision that has left the
        # worktree, and answering it there is deciding it.
        self.project()
        wt = self.dispatched()
        self.commit_in(wt)
        out = self.pipe("run", cwd=wt, findings=json.dumps(
            [{"where": "b.txt:1", "what": "ship it or not", "decide": True}]))
        self.assertEqual(out.returncode, 2, out.stdout + out.stderr)
        self.assertIn("nobody here can settle", out.stdout)
        self.assertIn("ship it or not", out.stdout)
        self.assertIn("block", out.stderr)

    def test_a_reviewer_that_wrote_nothing(self):
        self.project()
        wt = self.dispatched()
        self.commit_in(wt)
        out = self.pipe("run", cwd=wt, findings="silent")
        self.assertRefused(out, "wrote no findings file")
        self.assertEqual(out.returncode, 2, "not the minion's to fix")
        self.assertEqual(self.record()["verdict"], "failed")

    def test_last_rounds_findings_are_not_this_rounds_verdict(self):
        """A file left by the previous round would otherwise be read as a pass on
        findings nobody raised, about a head nobody looked at."""
        self.project()
        wt = self.dispatched()
        self.commit_in(wt)
        self.assertAccepted(self.pipe("run", cwd=wt))
        self.assertTrue(os.path.exists(self.at("pipeline",
                                               f"{self.TASK}.findings.json")))
        self.commit_in(wt, "c.txt")
        out = self.pipe("run", cwd=wt, findings="silent")
        self.assertRefused(out, "wrote no findings file")

    def test_no_reviewer_installed(self):
        # The suite passed, so nothing is wrong with the work: there is just nobody
        # here to read it. Recorded as failed all the same, because a record that
        # outlived the run it describes is what `check` must never be handed.
        self.project()
        wt = self.dispatched()
        self.commit_in(wt)
        self.assertAccepted(self.pipe("run", cwd=wt))
        bare = self.at("emptybin")
        os.makedirs(bare)
        out = self.pipe("run", cwd=wt,
                        PATH=os.pathsep.join([bare, "/usr/bin", "/bin"]))
        self.assertRefused(out, "claude is not installed")
        self.assertEqual(self.record()["verdict"], "failed")

    def test_no_task_named_anywhere(self):
        self.project()
        wt = self.dispatched()
        out = self.pipe("run", cwd=wt, SIANA_TASK_ID="")
        self.assertRefused(out, "which task?")


class Check(Pipeline):
    """The verify. It reads the outcome of a run and starts nothing."""

    def passing(self):
        self.project()
        wt = self.dispatched()
        head = self.commit_in(wt)
        self.assertAccepted(self.pipe("run", cwd=wt))
        return wt, head

    def test_no_run_at_all(self):
        self.project()
        wt = self.dispatched()
        out = self.pipe("check", cwd=wt)
        self.assertRefused(out, "no pipeline run recorded", "siana-pipeline run")

    def test_a_pass_at_the_head_that_was_validated(self):
        wt, head = self.passing()
        out = self.pipe("check", cwd=wt)
        self.assertAccepted(out)
        self.assertIn(head[:12], out.stdout)

    def test_a_commit_after_a_passing_run(self):
        """The constraint the whole design turns on. The QA worktree is cut from this
        branch, so a commit made after the run would be judged wearing this green."""
        wt, head = self.passing()
        moved = self.commit_in(wt, "c.txt")
        out = self.pipe("check", cwd=wt)
        self.assertRefused(out, "does not describe", head[:12], moved[:12])

    def test_an_uncommitted_change_after_a_passing_run(self):
        # The head is right and it is not the whole of what the minion has.
        wt, _ = self.passing()
        self.write(wt, "a.txt", "edited after the run\n")
        self.assertRefused(self.pipe("check", cwd=wt), "uncommitted changes", "a.txt")

    def test_a_failed_run_is_not_a_pass(self):
        self.project()
        wt = self.dispatched()
        self.commit_in(wt)
        self.pipe("run", cwd=wt, findings=json.dumps([{"what": "wrong"}]))
        out = self.pipe("check", cwd=wt)
        self.assertRefused(out, "did not pass", "1 finding")

    def test_it_starts_nothing(self):
        # A verify that started the rigor would run it once, after the minion had
        # already declared itself finished. Nothing it demanded could be acted on.
        wt, _ = self.passing()
        os.unlink(self.at("prompt.txt"))
        self.assertAccepted(self.pipe("check", cwd=wt))
        self.assertFalse(self.reviewed())

    def test_a_record_for_another_branch(self):
        wt, _ = self.passing()
        self.git("checkout", "-q", "-b", "sidetrack", cwd=wt)
        self.assertRefused(self.pipe("check", cwd=wt), "this worktree is on sidetrack")

    def test_an_unreadable_record(self):
        wt, _ = self.passing()
        with open(self.at("pipeline", f"{self.TASK}.json"), "w") as fh:
            fh.write("{ half a record")
        self.assertRefused(self.pipe("check", cwd=wt), "unreadable")

    def queue_done(self, wt):
        """`tasks done`, run the way a minion runs it: the verify by the name SIANA
        wrote on the task, in the minion's own worktree, with its own environment."""
        # `distro_path` because this is the one place the verify is reached by name
        # rather than by the path this suite happens to know, and by name it would
        # otherwise find the captain's installed SIANA instead of this checkout.
        env = {"PATH": self.distro_path(self.reviewer),
               "SIANA_TASK_ID": self.TASK, "SIANA_HOME": self.home}
        return self.run_cmd(["tasks", "--file", self.at("tasks.jsonl"), "done",
                             self.TASK, "--reason", "built it"], cwd=wt, env=env)

    def test_the_queue_accepts_a_run_that_earned_it(self):
        """End to end through `tasks done`, which is the only place this verify is
        ever executed. A green here is the whole of what reaches SIANA."""
        wt, _ = self.passing()
        self.assertAccepted(self.queue_done(wt))

    def test_the_queue_refuses_a_head_the_run_never_saw(self):
        """The same call, after one more commit. This is the failure the verify
        exists to catch, and `done` is where it has to be caught: a QA worktree is
        cut from this branch as soon as the task comes back."""
        wt, _ = self.passing()
        self.commit_in(wt, "c.txt")
        self.assertRefused(self.queue_done(wt), "does not describe")


if __name__ == "__main__":
    unittest.main()
