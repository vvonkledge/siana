"""siana-findings: the ledger a resolved review finding leaves the queue for.

Three things are being checked here, and they are not the same kind of thing.

The first is that the store cannot lose a finding. `datafile put` over an existing
key reports `updated`, and `compact` then deletes the old version for good: two
commands rewrite a finding into "nothing was wrong" and leave no trace. So the write
path is refused ahead of time and the log is checked afterwards, and both halves are
driven here against a real `datafile` rather than a stub, because a stub would agree
with whatever this suite believed.

The second is the archive's ordering. Every ledger write happens before any queue
removal, and the read-back sits between them. Those are crash properties, so they
are tested by failing a step in-process and then re-running the real command as a
process against the state the failure left.

The third is the boundary. Nothing here may decide that a successor resolved a
finding, and nothing here may answer a finding the pipeline marked for the captain.
Both are tested by building the case that would tempt it - a rejected head contained
by the merge that landed, a `decide` flag with no ruling beside it - and asserting a
refusal.
"""

import atexit
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from helpers import HomeTest, script

f = script("siana-findings")

# One checkout, built once and copied per test.
#
# Every case here needs a real repository with three commits, because the head
# checks, the pin and the containment check all run `git` against one. Building it
# per test cost a dozen processes in every `setUp`, which was most of what this
# module spent; copying a small tree instead is a fraction of one of them. It is
# never handed out directly, only copied, so no test can leave a ref or a branch
# behind for the next one.
_TEMPLATE_REPO = None


def template_repo():
    global _TEMPLATE_REPO
    if _TEMPLATE_REPO is None:
        root = tempfile.mkdtemp(prefix="findings-repo-")
        atexit.register(shutil.rmtree, root, True)
        repo = os.path.join(root, "repo")
        os.makedirs(repo)

        def run(*args):
            return subprocess.run(["git", "-C", repo, *args], check=True,
                                  capture_output=True, text=True)

        run("init", "-q", "-b", "main", ".")
        run("config", "user.email", "t@example.invalid")
        run("config", "user.name", "t")
        for text in ("one", "two", "three", "four"):
            with open(os.path.join(repo, "a.txt"), "w") as fh:
                fh.write(text + "\n")
            run("add", "-A")
            run("commit", "-qm", f"feat: {text}")
        # Newest first out of `git log`, and the fixture reads them oldest first.
        heads = run("log", "--format=%H", "-4").stdout.split()
        _TEMPLATE_REPO = (repo, tuple(reversed(heads)))
    return _TEMPLATE_REPO


class Ledger(HomeTest):
    """A home holding one closed rejection chain, ready to archive.

    One round, because most rules here are about one record and the multi-round
    cases build on this rather than repeating it. The queue, the registry and the
    checkout are all real: the lineage checks read `base` and `deps` off actual
    task records, and the git checks run against actual commits."""

    def setUp(self):
        super().setUp()
        self.contract("findings", "projects", "obligations")
        self.queue()
        self.repo = self.at("repo")
        source, heads = template_repo()
        shutil.copytree(source, self.repo)
        self.rejected, self.fixed, self.landed, self.later = heads
        self.project("demo", path=self.repo)
        os.makedirs(self.at("reports"))
        os.makedirs(self.at("briefs"))
        self.branch = "siana/feat/one"
        self.add("qa one", status="blocked",
                 reason="the boundary could be opened from inside an instance file")
        self.add("fix one", status="done", base=self.branch)
        self.add("qa fix one", status="done", dep="fix-one")
        self.report = self.evidence("reports/qa-one.md", "what the QA found\n")

    # ---- the world -------------------------------------------------------

    def git(self, *args):
        out = self.run_cmd(["git", "-C", self.repo, *args])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out.stdout.strip()

    def tasks(self, *args):
        return self.run_cmd(["tasks", "--file", self.at("tasks.jsonl"), *args])

    def add(self, title, status="todo", reason="because", base=None, dep=None):
        """One queue task in the state a case needs it in.

        Driven through `tasks` rather than written as a record, because every
        lineage check here reads a field `tasks` is the one thing that sets."""
        argv = ["add", title, "--verify", "true", "--project", "demo"]
        if base:
            argv += ["--base", base]
        if dep:
            argv += ["--dep", dep]
        self.assertAccepted(self.tasks(*argv))
        task_id = title.replace(" ", "-")
        if status == "todo":
            return task_id
        self.assertAccepted(self.tasks("start", task_id, "--owner", "m"))
        if status == "doing":
            return task_id
        verb = "block" if status == "blocked" else "done"
        self.assertAccepted(self.tasks(verb, task_id, "--reason", reason))
        return task_id

    def record(self, task_id):
        with open(self.at("tasks.jsonl")) as fh:
            found = [json.loads(line) for line in fh if line.strip()]
        return [r for r in found if r.get("id") == task_id][-1]

    def evidence(self, relative, text):
        path = self.at(*relative.split("/"))
        with open(path, "w") as fh:
            fh.write(text)
        return path

    def obligation(self, oid, status="closed", task="qa-one"):
        rec = {"id": oid, "kind": "decision", "body": "which way?",
               "status": status, "opened": "2026-08-30T09:00:00Z", "task": task}
        if status == "closed":
            rec.update(closed="2026-08-30T10:00:00Z", answer="the captain chose A")
        out = self.run_cmd(["datafile", "-f", self.at("obligations.jsonl"),
                            "-c", self.at("schema-obligations.yaml"), "put",
                            json.dumps(rec)])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)

    # ---- the plan --------------------------------------------------------

    def round_one(self, **overrides):
        rec = {"id": "qa-one", "case": "demo-case", "round": 1, "project": "demo",
               "kind": "qa",
               "summary": "the boundary could be opened from inside",
               "consequence": "an entity would have committed with no identifier",
               "tags": ["coverage-guarantee"],
               "branch": self.branch, "head": self.rejected,
               "resolver": "fix-one", "resolver_head": self.fixed,
               "acceptance": "qa-fix-one", "landed": f"demo#1 {self.landed}",
               "resolution": "fix-one closed it; qa-fix-one reproduced it",
               "resolved_by": "siana", "evidence": [self.report],
               "superseded": [], "unknown": []}
        rec.update(overrides)
        return {k: v for k, v in rec.items() if v is not _OMIT}

    def plan(self, *records, name="demo-case"):
        os.makedirs(self.at("findings", "plans"), exist_ok=True)
        path = self.at("findings", "plans", f"{name}.json")
        with open(path, "w") as fh:
            json.dump(list(records) or [self.round_one()], fh, indent=1)
        return path

    # ---- the command -----------------------------------------------------

    def findings(self, *args):
        return self.run_bin("siana-findings", *args)

    def archive(self, *records, name="demo-case"):
        return self.findings("archive", "--plan", self.plan(*records, name=name))

    def archived(self, *records, name="demo-case"):
        return self.assertAccepted(self.archive(*records, name=name))

    def ledger_lines(self):
        path = self.at("findings.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as fh:
            return [line for line in fh if line.strip()]

    def blob(self, sha):
        return self.at("findings", "blobs", sha[:2], sha[2:])

    def killed_after(self, step, calls):
        """The real step, up to a point, and then a crash.

        Patching a step out entirely would test a different thing: with the write
        skipped, a re-run has nothing to converge on and the test passes without the
        half-written state it was written for. So earlier calls go through to the
        real function and only the one after them raises."""
        real, seen = getattr(f, step), []

        def killed(*args, **kwargs):
            seen.append(1)
            if len(seen) > calls:
                raise RuntimeError("killed")
            return real(*args, **kwargs)

        return mock.patch.object(f, step, killed)

    def crash(self, *argv):
        """Run the command in-process with a step failing, and leave whatever that
        left behind.

        In-process because the failure has to happen between two named steps, and a
        subprocess has no seam there that is not a flag in the command itself. A
        flag would be test-only machinery in the thing under test, and the crash
        tests would then be testing the flag."""
        env = dict(os.environ)
        env["SIANA_HOME"] = self.home
        env.pop("SIANA_TASKS_FILE", None)
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(sys, "argv", ["siana-findings", *argv]), \
                open(os.devnull, "w") as quiet, \
                mock.patch.object(sys, "stdout", quiet):
            with self.assertRaises((RuntimeError, SystemExit)):
                f.main()

    def digest(self, path):
        return f.digest_of(path)


class _Omit:
    """A field left out of a plan, as against one set to null. `round_one` builds a
    complete record and every test that needs a hole punches it, so a test about an
    absent field cannot silently become a test about a null one."""


_OMIT = _Omit()


# --------------------------------------------------------------------------
# The store, before anything is in it
# --------------------------------------------------------------------------

class EmptyAndUnavailable(Ledger):
    """A ledger that has archived nothing and a ledger that cannot be read are
    opposite facts, and both are ordinary states of a real home. Reporting the
    second as the first would say this fleet has found nothing where the truth is
    that nobody can tell."""

    def test_a_home_with_the_contract_and_no_store_is_empty(self):
        out = self.assertAccepted(self.findings())
        self.assertIn("empty", out)
        self.assertNotIn("missing", out)

    def test_a_home_with_no_contract_is_unavailable_and_never_empty(self):
        os.remove(self.at("schema-findings.yaml"))
        out = self.assertRefused(self.findings(), "unavailable")
        self.assertIn("schema-findings.yaml", out)
        self.assertNotIn("empty", out)

    def test_verify_on_an_empty_ledger_is_green(self):
        out = self.assertAccepted(self.findings("verify"))
        self.assertIn("0 records", out)


class MalformedStore(Ledger):

    def test_a_line_that_is_not_json_names_its_line_number(self):
        self.archived()
        self.store("findings.jsonl", "{not json")
        out = self.assertRefused(self.findings("verify"), "not JSON")
        self.assertIn(f"line {len(self.ledger_lines())}", out)

    def test_a_line_that_fails_the_contract_is_named_by_field(self):
        # Valid JSON, so the fold reads it. What refuses it is the contract, asked
        # of the code that enforces it at the write rather than re-implemented here.
        self.archived()
        self.half_a_record()
        out = self.assertRefused(self.findings("verify"), "contract")
        self.assertIn("resolution", out)

    def test_every_reader_reports_such_a_line_rather_than_dying_on_it(self):
        # The default view formatted straight into a width spec, where a missing
        # field raises rather than renders. It is the first thing a captain types
        # and it was the one reader that answered a malformed row with a traceback.
        self.archived()
        self.half_a_record()
        for argv in ((), ("case", "demo-case"), ("show", "qa-one")):
            with self.subTest(argv=argv):
                out = self.findings(*argv)
                self.assertNotIn("Traceback", out.stdout + out.stderr)
        listing = self.assertAccepted(self.findings())
        self.assertIn("half-a-record", listing)
        self.assertIn("missing fields this view needs", listing)

    def half_a_record(self):
        self.store("findings.jsonl", {"id": "half-a-record", "case": "demo-case",
                                      "round": 2})


# --------------------------------------------------------------------------
# `unknown`: how a null says which kind of null it is
# --------------------------------------------------------------------------

class Unknown(Ledger):

    def test_a_name_that_is_not_a_field_is_refused(self):
        self.assertRefused(self.archive(self.round_one(unknown=["rejected-bytes"])),
                           "rejected-bytes is not a field of this contract")

    def test_naming_a_field_that_holds_a_value_is_refused(self):
        # The record would be claiming a hole where it holds a value, which is the
        # one thing a field invented to record holes must never say.
        self.assertRefused(self.archive(self.round_one(unknown=["head"])),
                           "head is named as unknown but holds a value")

    def test_a_field_that_is_null_and_named_is_accepted_and_reads_as_unknown(self):
        self.archived(self.round_one(head=None, branch=None, unknown=["head"]))
        out = self.assertAccepted(self.findings("show", "qa-one"))
        self.assertIn("head     (unknown; it applied and was not established)", out)
        # And the one beside it, null and not named, reads as the other fact.
        self.assertIn("branch   (n/a)", out)

    def test_one_member_of_a_list_can_be_named_as_lost(self):
        # The rejected artifact of a document review has been overwritten twice
        # since; the bytes exist nowhere and the ledger cannot invent them. What it
        # can do is say so in one contract-checked field, without claiming the rest
        # of the evidence is missing too.
        self.archived(self.round_one(unknown=["evidence:rejected-handoff"]))
        self.assertAccepted(self.findings("verify"))

    def test_a_lost_member_of_the_evidence_is_printed_where_evidence_is_read(self):
        # The whole point of recording it. Without this the reader sees the files
        # that were archived and nothing saying the artifact actually rejected is
        # not among them, which is a knowingly partial set read as a complete one.
        self.archived(self.round_one(unknown=["evidence:rejected-handoff"]))
        for out in (self.assertAccepted(self.findings("show", "qa-one")),
                    self.assertAccepted(self.findings("case", "demo-case"))):
            self.assertIn("rejected-handoff: it applied here and was never archived",
                          out)

    def test_qualifying_a_field_that_is_not_a_list_is_refused(self):
        self.assertRefused(self.archive(self.round_one(unknown=["head:something"])),
                           "is not a list")


# --------------------------------------------------------------------------
# Evidence: copied, not referenced
# --------------------------------------------------------------------------

class Evidence(Ledger):

    def test_a_report_is_copied_into_the_blob_store_under_its_digest(self):
        sha = self.digest(self.report)
        self.archived()
        self.assertTrue(os.path.exists(self.blob(sha)))
        with open(self.blob(sha)) as fh:
            self.assertEqual(fh.read(), "what the QA found\n")

    def test_a_report_named_in_a_plan_that_is_gone_is_refused_before_any_write(self):
        os.remove(self.report)
        self.assertRefused(self.archive(), "cannot be read", self.report)
        self.assertEqual(self.ledger_lines(), [])
        self.assertEqual(self.record("qa-one")["status"], "blocked")

    def test_a_report_that_cannot_be_read_is_refused_and_named(self):
        os.chmod(self.report, 0o000)
        self.addCleanup(os.chmod, self.report, 0o644)
        if os.access(self.report, os.R_OK):
            self.skipTest("this user can read a mode 000 file")
        self.assertRefused(self.archive(), "cannot be read", self.report)
        self.assertEqual(self.ledger_lines(), [])

    def test_a_relative_path_is_refused(self):
        # A plan is read from wherever it is run, so a relative path means a
        # different file depending on where the archive happened.
        self.assertRefused(self.archive(self.round_one(evidence=["reports/x.md"])),
                           "relative path")

    def test_bytes_that_change_after_the_archive_are_reported_as_drift(self):
        sha = self.digest(self.report)
        self.archived()
        with open(self.report, "w") as fh:
            fh.write("rewritten by later work\n")
        out = self.assertRefused(self.findings("verify"), "drifted")
        self.assertIn(sha[:12], out)
        # And the archived bytes are still readable, which is the whole reason the
        # design copies rather than references.
        self.assertEqual(self.assertAccepted(self.findings("blob", sha)),
                         "what the QA found\n")

    def test_a_report_deleted_after_the_archive_is_reported_and_still_readable(self):
        sha = self.digest(self.report)
        self.archived()
        os.remove(self.report)
        self.assertRefused(self.findings("verify"), "is gone from where it was")
        self.assertEqual(self.assertAccepted(self.findings("blob", sha)),
                         "what the QA found\n")

    def test_the_same_bytes_from_two_records_are_one_blob(self):
        twin = self.evidence("briefs/qa-one.md", "what the QA found\n")
        sha = self.digest(self.report)
        self.assertEqual(sha, self.digest(twin))
        self.archived(self.round_one(evidence=[self.report, twin]))
        self.assertEqual(len(os.listdir(os.path.dirname(self.blob(sha)))), 1)
        self.assertAccepted(self.findings("verify"))

    def test_a_missing_blob_fails_verify(self):
        sha = self.digest(self.report)
        self.archived()
        os.remove(self.blob(sha))
        self.assertRefused(self.findings("verify"), "is not in the blob store")


# --------------------------------------------------------------------------
# Project, lineage and acceptance
# --------------------------------------------------------------------------

class Lineage(Ledger):

    def test_a_project_the_queue_disagrees_with_is_refused(self):
        self.project("other", path=self.repo)
        self.assertRefused(self.archive(self.round_one(project="other")),
                           "the queue said project demo")

    def test_a_project_that_is_not_in_the_registry_is_refused(self):
        os.remove(self.at("projects.jsonl"))
        self.assertRefused(self.archive(), "not a handle in projects.jsonl")

    def test_a_project_whose_path_is_not_a_directory_is_refused(self):
        shutil.rmtree(self.repo)
        self.assertRefused(self.archive(), "path is not a directory")

    def test_a_resolver_cut_from_a_different_branch_is_refused(self):
        self.assertRefused(self.archive(self.round_one(branch="siana/feat/other")),
                           "was cut from", "not from siana/feat/other")

    def test_an_acceptance_that_does_not_depend_on_the_resolver_is_refused(self):
        self.add("qa something else", status="done")
        self.assertRefused(
            self.archive(self.round_one(acceptance="qa-something-else")),
            "does not depend on fix-one")

    def test_a_resolver_that_has_not_finished_is_refused(self):
        for status in ("todo", "doing", "blocked"):
            with self.subTest(status=status):
                self.add(f"fix {status} one", status=status)
                out = self.archive(
                    self.round_one(resolver=f"fix-{status}-one", branch=None))
                self.assertRefused(out, f"is {status}, not done")

    def test_a_chain_whose_last_acceptance_has_not_finished_is_refused(self):
        self.add("qa still running", status="doing")
        self.assertRefused(
            self.archive(self.round_one(acceptance="qa-still-running")),
            "the chain has no accepted end")

    def test_a_highest_round_naming_nothing_that_accepted_it_is_refused(self):
        self.assertRefused(self.archive(self.round_one(acceptance=None)),
                           "names nothing that accepted it")

    def test_an_acceptance_that_exists_nowhere_is_refused(self):
        self.assertRefused(self.archive(self.round_one(acceptance="qa-imaginary")),
                           "neither in the queue nor in this ledger")

    def test_a_source_task_that_was_not_blocked_is_refused(self):
        self.add("qa two", status="done")
        self.assertRefused(self.archive(self.round_one(id="qa-two")),
                           "was done and not blocked")


class AncestryIsNotProof(Ledger):
    """The one inference this command may never make.

    Every rejected head in this fleet is contained by the merge that landed its
    case, because each repair was cut from the branch it repaired. A rule of the
    form "contained by a merge, therefore resolved" would have closed
    `fix-pipeline-review-base` before its repair was written, because the head it
    blocked on landed in `main` carrying the exact refusal the reviewer flagged."""

    def test_containment_never_substitutes_for_an_accepted_successor(self):
        # A correct `landed` that really does contain the repair, and a resolver
        # that has not finished. Containment is checked and passes; the archive is
        # refused anyway, on the acceptance.
        self.add("fix two", status="doing", base=self.branch)
        self.assertRefused(
            self.archive(self.round_one(resolver="fix-two", acceptance=None)),
            "is doing, not done")

    def test_a_landed_commit_that_does_not_contain_the_repair_is_refused(self):
        self.assertRefused(
            self.archive(self.round_one(landed=f"demo#1 {self.rejected}")),
            "does not contain")

    def test_a_landed_commit_is_resolved_even_where_there_is_no_repair_commit(self):
        # A review of a document rejects no branch and its repair produces no commit
        # of its own, so `landed` is that record's only pointer to where the work
        # went - and it is the record whose other evidence is already known to be
        # unrecoverable. A truncated or mistyped sha there must not pass unread.
        self.assertRefused(
            self.archive(self.round_one(
                kind="review", branch=None, head=None, resolver_head=None,
                unknown=["head"], landed="demo#1 " + "0" * 40)),
            "is not a commit in")

    def test_a_case_may_be_archived_with_nothing_published_yet(self):
        # Merged is evidence and never proof, so its absence is not a fault either.
        self.archived(self.round_one(landed=None))
        self.assertAccepted(self.findings("verify"))

    def test_verify_says_which_half_of_a_record_it_did_not_check(self):
        self.archived()
        out = self.assertAccepted(self.findings("verify"))
        self.assertIn("not checked", out)
        self.assertIn("resolution and resolved_by", out)


class CaptainDecision(Ledger):
    """A finding the pipeline marked `decide` is one SIANA may not answer, and the
    flag is already on disk, so this reads a fact rather than a claim."""

    def setUp(self):
        super().setUp()
        os.makedirs(self.at("pipeline"))
        self.ship = self.round_one(kind="ship", id="qa-one")

    def flag(self, decide=True):
        with open(self.at("pipeline", "qa-one.findings.json"), "w") as fh:
            json.dump({"findings": [{"where": "bin/x:1", "what": "wide",
                                     "decide": decide}]}, fh)

    def test_a_decide_finding_with_no_ruling_is_refused(self):
        self.flag()
        self.assertRefused(self.archive(self.ship), "marked a finding here for the"
                                                    " captain")

    def test_a_decide_finding_with_an_open_obligation_is_refused(self):
        self.flag()
        self.obligation("choose-a-way", status="open")
        self.assertRefused(
            self.archive(dict(self.ship, evidence=[self.report,
                                                   "obligation:choose-a-way"])),
            "the captain has not answered it")

    def test_a_ruling_about_a_different_task_is_refused(self):
        self.flag()
        self.obligation("choose-a-way", task="some-other-task")
        self.assertRefused(
            self.archive(dict(self.ship, evidence=[self.report,
                                                   "obligation:choose-a-way"])),
            "is about some-other-task")

    def test_a_decide_finding_with_the_captains_closed_ruling_is_archived(self):
        self.flag()
        self.obligation("choose-a-way")
        self.archived(dict(self.ship, evidence=[self.report,
                                                "obligation:choose-a-way"]))
        self.assertAccepted(self.findings("verify"))

    def test_a_pipeline_finding_nobody_had_to_settle_needs_no_ruling(self):
        self.flag(decide=False)
        self.archived(self.ship)

    def test_a_named_obligation_that_does_not_exist_is_refused(self):
        self.assertRefused(
            self.archive(dict(self.ship, evidence=[self.report,
                                                   "obligation:never-asked"])),
            "is not in obligations.jsonl")


# --------------------------------------------------------------------------
# A whole case, and the invariants that make it one
# --------------------------------------------------------------------------

class TwoRounds(Ledger):
    """A second round on top of the fixture, and nothing else.

    Split out from the tests that use it because two test classes need the same
    two-round world. A test class inherited for its fixture runs the parent's tests
    a second time in every child, which is a suite paying twice for one rule."""

    def setUp(self):
        super().setUp()
        self.second = self.later
        self.add("qa two", status="blocked", base="siana/fix/fix-one",
                 reason="and a second defect, in the repair")
        self.tasks("dep", "qa-two", "--on", "fix-one")
        self.add("fix two", status="done", base="siana/fix/fix-one")
        self.add("qa fix two", status="done", dep="fix-two")
        self.report_two = self.evidence("reports/qa-two.md", "the second finding\n")

    def rounds(self, **second):
        one = self.round_one(acceptance="qa-two")
        two = {"id": "qa-two", "case": "demo-case", "round": 2, "project": "demo",
               "kind": "qa", "summary": "and a second defect, in the repair",
               "consequence": "the repair would have shipped its own defect",
               "tags": ["refusal-contract"],
               "branch": "siana/fix/fix-one", "head": self.fixed,
               "resolver": "fix-two", "resolver_head": self.second,
               "acceptance": "qa-fix-two", "landed": None,
               "resolution": "fix-two closed it; qa-fix-two reproduced it",
               "resolved_by": "siana", "evidence": [self.report_two],
               "superseded": [], "unknown": []}
        two.update(second)
        return one, two


class Chain(TwoRounds):
    """Round 1's repair was read by round 2's rejection, and round 2 ends in an
    acceptance that finished. That is the whole shape of a rejection chain in this
    fleet, and it is checked rather than asserted."""

    def test_a_whole_chain_archives_and_verifies(self):
        out = self.archived(*self.rounds())
        self.assertIn("2 records, 2 tasks removed", out)
        self.assertIn("2 record", self.assertAccepted(self.findings("verify")))
        self.assertIn("round 2", self.assertAccepted(
            self.findings("case", "demo-case")))

    def test_a_round_whose_acceptance_is_not_the_next_round_is_refused(self):
        one, two = self.rounds()
        self.assertRefused(self.archive(dict(one, acceptance="qa-fix-one"), two),
                           "is not round 2")

    def test_a_gap_in_the_rounds_is_refused(self):
        _, two = self.rounds()
        self.assertRefused(self.archive(dict(two, round=3)), "incomplete: round")

    def test_two_records_at_the_same_round_are_refused(self):
        one, two = self.rounds()
        self.assertRefused(self.archive(one, dict(two, round=1)),
                           "two records at round 1")

    def test_a_plan_mixing_two_cases_is_refused(self):
        one, two = self.rounds()
        self.assertRefused(self.archive(one, dict(two, case="other-case")),
                           "mixes 2 cases")

    def test_a_partial_case_in_the_ledger_reports_as_incomplete(self):
        # The crash between two puts. The highest round present has an acceptance
        # that is another round's id, so the chain has no end and is named rather
        # than accepted as finished.
        one, two = self.rounds()
        with self.killed_after("write_record", 1):
            self.crash("archive", "--plan", self.plan(one, two))
        self.assertEqual(len(self.ledger_lines()), 1)
        # The case reads as unfinished because the highest round present names an
        # acceptance that has not finished, which is exactly what a truncated case
        # looks like. Nothing in a ledger says how many rounds a case was going to
        # have, so this is the check that catches a missing tail.
        out = self.assertRefused(self.findings("verify"), "incomplete")
        self.assertIn("qa-two", out)
        # And nothing was dropped, because dropping is after the whole write loop.
        self.assertEqual(self.record("qa-one")["status"], "blocked")



# --------------------------------------------------------------------------
# Re-runs, crash points and the boundary between writing and dropping
# --------------------------------------------------------------------------

class Converge(TwoRounds):
    """Both stores are idempotent primitives, so a plan is safe to re-run from any
    state a crash can leave. Each of these puts the world in one of those states and
    runs the real command against it."""

    def test_the_same_plan_twice_converges_and_writes_one_row_per_record(self):
        one, two = self.rounds()
        self.archived(one, two)
        again = self.archived(one, two)
        self.assertIn("already archived", again)
        self.assertEqual(len(self.ledger_lines()), 2)
        ids = [json.loads(line)["id"] for line in self.ledger_lines()]
        self.assertEqual(sorted(ids), ["qa-one", "qa-two"])

    def test_a_plan_with_one_field_changed_is_refused_by_name(self):
        one, two = self.rounds()
        self.archived(one, two)
        out = self.assertRefused(
            self.archive(dict(one, summary="nothing was wrong"), two),
            "already archived and this plan differs")
        self.assertIn("summary", out)
        self.assertEqual(len(self.ledger_lines()), 2)

    def test_a_plan_with_a_field_taken_back_out_is_refused(self):
        # The edit SIANA makes when a pointer was wrong. Read against the plan's own
        # keys alone, a deleted field was the one change that converged silently: a
        # green run against a ledger that still held the value, in a store that is
        # append-only by refusal, so nothing else would ever have said so.
        one, two = self.rounds()
        self.archived(one, two)
        out = self.assertRefused(self.archive(self.round_one(landed=_OMIT,
                                                             acceptance="qa-two"),
                                              two),
                                 "already archived and this plan differs")
        self.assertIn("landed: the plan no longer carries it", out)

    def test_a_plan_that_never_carried_an_optional_field_still_converges(self):
        # The other half of the same rule. A plan that never set `tags` agrees with
        # the empty list the store materialised, and must not read as a change.
        one, two = self.rounds()
        one, two = dict(one), dict(two)
        one.pop("tags"), two.pop("tags")
        self.archived(one, two)
        self.assertIn("already archived", self.archived(one, two))

    def test_a_re_run_after_the_blobs_copies_nothing_twice(self):
        one, two = self.rounds()
        sha = self.digest(self.report)
        with self.killed_after("write_record", 0):
            self.crash("archive", "--plan", self.plan(one, two))
        self.assertTrue(os.path.exists(self.blob(sha)))
        self.assertEqual(self.ledger_lines(), [])
        stamp = os.stat(self.blob(sha)).st_ino
        out = self.archived(one, two)
        self.assertNotIn(f"blob     {sha[:12]}", out)
        self.assertEqual(os.stat(self.blob(sha)).st_ino, stamp)

    def test_a_re_run_after_some_puts_writes_the_rest_and_drops(self):
        one, two = self.rounds()
        with self.killed_after("write_record", 1):
            self.crash("archive", "--plan", self.plan(one, two))
        self.assertEqual(len(self.ledger_lines()), 1)
        self.archived(one, two)
        self.assertEqual(len(self.ledger_lines()), 2)
        for task_id in ("qa-one", "qa-two"):
            self.assertTrue(self.record(task_id).get("_deleted"))

    def test_a_re_run_after_every_put_drops_and_the_queue_over_reports(self):
        one, two = self.rounds()
        with self.killed_after("drop", 0):
            self.crash("archive", "--plan", self.plan(one, two))
        self.assertEqual(len(self.ledger_lines()), 2)
        # In the window the ledger holds the whole case and the queue still holds
        # its tasks, which is the safe direction: the fleet looks like it has more
        # to do than it has, never less.
        self.assertAccepted(self.findings("verify"))
        self.assertEqual(self.record("qa-one")["status"], "blocked")
        self.archived(one, two)
        self.assertTrue(self.record("qa-one").get("_deleted"))

    def test_a_re_run_after_some_drops_drops_the_rest(self):
        one, two = self.rounds()
        with self.killed_after("drop", 1):
            self.crash("archive", "--plan", self.plan(one, two))
        self.assertTrue(self.record("qa-one").get("_deleted"))
        self.assertEqual(self.record("qa-two")["status"], "blocked")
        out = self.archived(one, two)
        self.assertIn("dropped  qa-two", out)
        self.assertTrue(self.record("qa-two").get("_deleted"))

    def test_a_failed_readback_drops_nothing(self):
        # The one ordering the whole design rests on. Dropping is structurally after
        # the read-back loop returns, so a store that did not keep what it was given
        # stops the archive with every task still in the queue.
        one, two = self.rounds()
        with self.killed_after("read_back", 0):
            self.crash("archive", "--plan", self.plan(one, two))
        self.assertEqual(len(self.ledger_lines()), 2)
        for task_id in ("qa-one", "qa-two"):
            self.assertEqual(self.record(task_id)["status"], "blocked")

    def test_a_readback_that_disagrees_with_what_was_written_refuses(self):
        one, two = self.rounds()

        def wrong(store, schema, record):
            return f.die("the ledger kept qa-one.summary as something else",
                         "nothing has been dropped from the queue")

        with mock.patch.object(f, "read_back", side_effect=wrong):
            self.crash("archive", "--plan", self.plan(one, two))
        for task_id in ("qa-one", "qa-two"):
            self.assertEqual(self.record(task_id)["status"], "blocked")

    def test_a_task_that_merely_reuses_an_archived_id_is_never_dropped(self):
        # A queue id is derived from the title against the live fold, so a dropped
        # one is free again and re-briefing under the same title reclaims it. A
        # re-run is advertised as safe from any state, so without this it would have
        # removed live work under a reason naming a record that is not about it -
        # and a waiting task goes without a word, since the queue only guards work
        # that is in flight.
        one, two = self.rounds()
        self.archived(one, two)
        self.assertAccepted(self.tasks("add", "qa one", "--verify", "true",
                                       "--project", "demo"))
        self.assertEqual(self.record("qa-one")["status"], "todo")
        out = self.assertRefused(self.archive(one, two),
                                 "a task this record did not archive")
        self.assertIn("new task wearing an archived name", out)
        self.assertEqual(self.record("qa-one")["status"], "todo")

    def test_the_crash_window_this_guard_has_to_tolerate_still_converges(self):
        # The queue holding the very task `source` is a copy of is the state a crash
        # between the writes and the drops leaves, and re-running is how it is
        # cleared. Presence alone could not tell the two apart, which is why the
        # guard compares rather than counts.
        one, two = self.rounds()
        with self.killed_after("drop", 0):
            self.crash("archive", "--plan", self.plan(one, two))
        self.assertEqual(self.record("qa-one")["status"], "blocked")
        self.archived(one, two)
        self.assertTrue(self.record("qa-one").get("_deleted"))

    def test_a_task_gone_from_the_queue_and_absent_here_is_a_fault(self):
        one, two = self.rounds()
        self.assertAccepted(self.tasks("drop", "qa-one", "--reason", "by hand"))
        self.assertRefused(self.archive(one, two),
                           "neither in the queue nor in this ledger")
        self.assertEqual(self.ledger_lines(), [])


# --------------------------------------------------------------------------
# The waiting QA that can never run
# --------------------------------------------------------------------------

class Superseded(Ledger):
    """A task removed alongside a record because it exists only to serve it. All
    four conditions are mechanical, and the fourth is the one that matters: it goes
    only where an independent acceptance of the same work exists somewhere else."""

    def setUp(self):
        super().setUp()
        self.orphan = self.add("qa the orphan", dep="qa-one")

    def test_a_task_whose_id_contains_the_word_absent_is_reported_as_dropped(self):
        # The queue echoes the id and this command's own reason back on stdout, so a
        # substring match read a real removal as a task that was already gone. The
        # queue ends in the right state either way; what was wrong was the archive's
        # only account of the step that removes queue records.
        absent = self.add("qa absent blob", dep="qa-one")
        out = self.archived(self.round_one(superseded=[absent]))
        self.assertIn(f"dropped  {absent}", out)
        self.assertIn("2 tasks removed", out)
        self.assertNotIn("already archived", out)

    def test_the_accepted_path_removes_it_and_says_what_replaced_it(self):
        self.archived(self.round_one(superseded=[self.orphan]))
        self.assertTrue(self.record(self.orphan).get("_deleted"))
        out = self.assertAccepted(self.findings("show", "qa-one"))
        self.assertIn(self.orphan, out)
        self.assertIn("qa-fix-one", out)

    def test_a_task_that_is_in_flight_is_never_removed(self):
        # The queue refuses to start a task whose dependency is still blocked,
        # which is the orphan's whole situation. `--force` is how a real one would
        # have been put in flight anyway, and in flight is what this refuses on.
        self.assertAccepted(self.tasks("start", self.orphan, "--owner", "m",
                                       "--force"))
        self.assertRefused(self.archive(self.round_one(superseded=[self.orphan])),
                           "is doing, not todo")

    def test_a_task_that_does_not_depend_on_this_one_is_never_removed(self):
        other = self.add("qa unrelated")
        self.assertRefused(self.archive(self.round_one(superseded=[other])),
                           "does not depend on qa-one")

    def test_nothing_is_removed_while_no_independent_acceptance_is_done(self):
        self.add("qa still running", status="doing")
        out = self.archive(self.round_one(superseded=[self.orphan],
                                          acceptance="qa-still-running"))
        self.assertRefused(out, "independent acceptance")

    def test_a_task_the_queue_does_not_have_is_refused_on_a_first_archive(self):
        # Typing the orphan's title where its id belongs is how this is reached, and
        # the two differ in this fleet's own records. Passed over, the archive
        # removes nothing and writes into the ledger that it did, so `show` reports
        # a removal that never happened while the real stranded task sits in the
        # queue with nothing pointing at it, and `verify` never looks again.
        out = self.assertRefused(
            self.archive(self.round_one(superseded=["qa-the-orphan-that-never-was"])),
            "is not in the queue")
        self.assertIn("never happened", out)
        self.assertEqual(self.ledger_lines(), [])

    def test_a_re_run_after_the_orphan_is_gone_converges(self):
        self.archived(self.round_one(superseded=[self.orphan]))
        self.assertIn("already archived",
                      self.archived(self.round_one(superseded=[self.orphan])))


# --------------------------------------------------------------------------
# Pinning, and what a pin is a fact about
# --------------------------------------------------------------------------

class Pins(Ledger):

    def ref(self):
        return self.run_cmd(["git", "-C", self.repo, "rev-parse", "--verify",
                             "--quiet", f"{f.REF_NAMESPACE}/qa-one"]).stdout.strip()

    def test_the_rejected_head_survives_its_branch_and_a_prune(self):
        # The whole reason a ref is pinned. Without one, `gc --prune=now` makes an
        # unreferenced commit unreadable and the ledger points at nothing.
        self.git("branch", "-f", self.branch, self.rejected)
        self.archived()
        self.git("branch", "-D", self.branch)
        self.git("reset", "-q", "--hard", self.landed)
        self.run_cmd(["git", "-C", self.repo, "gc", "--prune=now", "-q"])
        self.assertEqual(self.ref(), self.rejected)
        self.assertAccepted(self.findings("verify"))

    def test_a_pin_that_is_gone_is_reported(self):
        self.archived()
        self.git("update-ref", "-d", f"{f.REF_NAMESPACE}/qa-one")
        self.assertRefused(self.findings("verify"), "is gone; the rejected head is"
                                                    " unanchored")

    def test_a_record_with_no_head_pins_nothing(self):
        self.archived(self.round_one(head=None, branch=None, unknown=["head"]))
        self.assertEqual(self.ref(), "")
        self.assertAccepted(self.findings("verify"))

    def test_every_pin_is_written_to_the_refs_log(self):
        self.archived()
        with open(self.at("findings", "refs.log")) as fh:
            line = fh.read().strip()
        self.assertIn(f"{f.REF_NAMESPACE}/qa-one", line)
        self.assertIn(self.rejected, line)


# --------------------------------------------------------------------------
# What a plan may and may not carry
# --------------------------------------------------------------------------

class Plan(Ledger):

    def test_a_plan_carrying_a_field_the_archive_fills_is_refused(self):
        for field, value in (("archived_at", "2026-08-30T09:00:00Z"),
                             ("source", {"id": "qa-one"}),
                             ("reason", "typed from memory"),
                             ("head_pinned", True)):
            with self.subTest(field=field):
                self.assertRefused(self.archive(self.round_one(**{field: value})),
                                   f"sets {field}, which the archive fills in")

    def test_a_record_the_contract_would_refuse_writes_nothing_at_all(self):
        # The contract used to be applied first by the `datafile put`, which is after
        # the blobs are copied and after `git update-ref` has written into the
        # captain's own checkout. An abbreviated head reaches that far, because
        # `git cat-file -e` resolves an abbreviation and only the contract's pattern
        # does not - so the archive wrote outside itself and then refused.
        sha = self.digest(self.report)
        out = self.assertRefused(self.archive(self.round_one(head=self.rejected[:7])),
                                 "would not accept")
        self.assertIn("head", out)
        self.assertIn("nothing has been written", out)
        self.assertFalse(os.path.exists(self.blob(sha)))
        self.assertFalse(os.path.exists(self.at("findings", "refs.log")))
        self.assertEqual(self.run_cmd(
            ["git", "-C", self.repo, "rev-parse", "--verify", "--quiet",
             f"{f.REF_NAMESPACE}/qa-one"]).stdout.strip(), "")
        self.assertEqual(self.ledger_lines(), [])

    def test_a_required_field_the_plan_left_out_is_refused_by_the_contract(self):
        self.assertRefused(self.archive(self.round_one(resolution=_OMIT)),
                           "would not accept", "resolution")

    def test_a_plan_naming_a_field_the_contract_does_not_have_is_refused(self):
        self.assertRefused(self.archive(self.round_one(note="a stray key")),
                           "fields this contract does not have: note")

    def test_a_plan_that_is_not_an_array_is_refused(self):
        path = self.at("findings", "plans", "bad.json")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as fh:
            fh.write('{"id": "qa-one"}')
        self.assertRefused(self.findings("archive", "--plan", path),
                           "not a non-empty JSON array")

    def test_a_plan_that_is_not_there_is_refused(self):
        self.assertRefused(self.findings("archive", "--plan", self.at("nope.json")),
                           "no plan at")

    def test_a_plan_naming_one_task_twice_is_refused(self):
        self.assertRefused(self.archive(self.round_one(),
                                        self.round_one(round=2)), "twice")

    def test_the_queue_record_is_archived_verbatim_and_the_reason_lifted_out(self):
        self.archived()
        rec = json.loads(self.ledger_lines()[0])
        self.assertEqual(rec["source"]["id"], "qa-one")
        self.assertEqual(rec["source"]["status"], "blocked")
        self.assertEqual(rec["reason"], rec["source"]["reason"])
        self.assertIn("the boundary could be opened", rec["reason"])


# --------------------------------------------------------------------------
# Append-only, compaction, and a contract that has not grown yet
# --------------------------------------------------------------------------

class AppendOnly(Ledger):

    def datafile(self, *args):
        return self.run_cmd(["datafile", "-f", self.at("findings.jsonl"),
                             "-c", self.at("schema-findings.yaml"), *args])

    def test_a_second_put_over_an_archived_key_is_named(self):
        self.archived()
        rec = json.loads(self.ledger_lines()[0])
        rec["summary"] = "nothing was wrong"
        self.assertAccepted(self.datafile("put", json.dumps(rec)))
        out = self.assertRefused(self.findings("verify"), "append-only")
        self.assertIn("qa-one a second time", out)

    def test_a_tombstone_is_named(self):
        self.archived()
        self.assertAccepted(self.datafile("delete", "qa-one"))
        self.assertRefused(self.findings("verify"), "tombstone for qa-one")

    def test_compacting_a_clean_ledger_changes_nothing(self):
        self.archived()
        with open(self.at("findings.jsonl"), "rb") as fh:
            before = fh.read()
        self.assertAccepted(self.datafile("compact"))
        with open(self.at("findings.jsonl"), "rb") as fh:
            self.assertEqual(fh.read(), before)
        self.assertAccepted(self.findings("verify"))

    def test_verifying_one_case_still_asks_whether_the_whole_log_is_intact(self):
        # Otherwise a rewrite could be hidden by verifying the case it is not in.
        self.archived()
        rec = json.loads(self.ledger_lines()[0])
        self.assertAccepted(self.datafile("put", json.dumps(
            dict(rec, summary="nothing was wrong"))))
        self.assertRefused(self.findings("verify", "demo-case"), "append-only")


class OlderContract(Ledger):
    """A home installed before a field was added still has a contract without it,
    because neither `init` nor `upgrade` ever rewrites a live one. That is a state
    the fleet has to keep working in."""

    def test_a_record_written_without_a_later_field_verifies_and_reads_as_absent(self):
        self.older_contract("findings", "landed")
        self.archived(self.round_one(landed=_OMIT))
        rec = json.loads(self.ledger_lines()[0])
        self.assertNotIn("landed", rec)
        self.assertAccepted(self.findings("verify"))
        self.assertIn("landed   (n/a)",
                      self.assertAccepted(self.findings("show", "qa-one")))

    def test_a_field_the_contract_does_not_have_cannot_be_named_as_unknown(self):
        self.older_contract("findings", "landed")
        self.assertRefused(
            self.archive(self.round_one(landed=_OMIT, unknown=["landed"])),
            "landed is not a field of this contract")


# --------------------------------------------------------------------------
# The readers
# --------------------------------------------------------------------------

class Views(Ledger):

    def test_the_default_view_groups_by_case_one_line_per_record(self):
        self.archived()
        out = self.assertAccepted(self.findings())
        self.assertIn("findings 1 in 1 case", out)
        self.assertIn("demo-case  (demo, 1 round)", out)
        self.assertIn("qa-one", out)

    def test_the_default_view_is_also_reachable_by_name(self):
        self.archived()
        self.assertEqual(self.assertAccepted(self.findings("list")),
                         self.assertAccepted(self.findings()))

    def test_a_count_of_one_reads_as_one(self):
        # Two of the four real cases this store was designed for have exactly one
        # round, so `1 rounds` is what a captain would have met first.
        self.archived()
        for out in (self.assertAccepted(self.findings()),
                    self.assertAccepted(self.findings("case", "demo-case")),
                    self.assertAccepted(self.findings("verify"))):
            self.assertNotIn("1 rounds", out)
            self.assertNotIn("1 records", out)
            self.assertNotIn("1 cases", out)
        self.assertIn("1 round", self.assertAccepted(self.findings()))

    def test_show_names_a_finding_that_is_not_there(self):
        self.assertRefused(self.findings("show", "qa-nothing"),
                           "no finding under that id")

    def test_case_names_a_case_that_is_not_there(self):
        self.assertRefused(self.findings("case", "no-such-case"),
                           "no case under that name")

    def test_blob_refuses_something_that_is_not_a_digest(self):
        self.assertRefused(self.findings("blob", "deadbeef"), "not a sha256")

    def test_blob_names_a_digest_it_does_not_hold(self):
        self.assertRefused(self.findings("blob", "0" * 64), "no blob under")

    def test_show_prints_the_judgment_apart_from_the_mechanics(self):
        self.archived()
        out = self.assertAccepted(self.findings("show", "qa-one"))
        self.assertIn("judgment, not checked here", out)


class Unverifiable(Ledger):
    """`verify` degrades if a `done` task is removed by other means. That is neither
    a fault in the ledger nor something a green may be claimed over, so it is
    reported as its own third answer."""

    def test_a_resolver_removed_from_the_queue_is_unverifiable_not_failed(self):
        self.archived()
        self.assertAccepted(self.tasks("drop", "fix-one", "--reason", "by hand"))
        out = self.assertAccepted(self.findings("verify"))
        self.assertIn("unverifiable", out)
        self.assertIn("could not be checked", out)
        self.assertNotIn("ok            every check", out)

    def test_an_acceptance_removed_from_the_queue_is_unverifiable_not_failed(self):
        # The highest round's acceptance is a task in the queue, and the queue is
        # not this store's to keep: the design recommends `datafile roll` if it ever
        # gets too big, and a record whose acceptance went that way is a record
        # nothing is wrong with. Failing it would report an intact ledger as broken.
        self.archived()
        self.assertAccepted(self.tasks("drop", "qa-fix-one", "--reason", "by hand"))
        out = self.assertAccepted(self.findings("verify"))
        self.assertIn("unverifiable  qa-one  acceptance", out)
        self.assertNotIn("failed", out)


class MissingTools(Ledger):
    """A home where a store tool is not installed. `just doctor` prints `missing
    datafile` for exactly this, and every other store reader in bin/ refuses it by
    name rather than raising."""

    def without(self, name, *args):
        return self.run_bin("siana-findings", *args,
                            env={"PATH": self.path_without(name)})

    def test_reading_without_datafile_refuses_rather_than_raising(self):
        self.archived()
        out = self.assertRefused(self.without("datafile", "verify"),
                                 "datafile is not on PATH")
        self.assertNotIn("Traceback", out)

    def test_archiving_without_datafile_refuses_before_any_write(self):
        out = self.assertRefused(self.without("datafile", "archive", "--plan",
                                              self.plan()),
                                 "datafile is not on PATH")
        self.assertNotIn("Traceback", out)
        self.assertEqual(self.ledger_lines(), [])

    def test_archiving_without_tasks_refuses_after_the_writes_and_says_so(self):
        # The one call reached after the ledger writes have landed, which is why a
        # traceback there would take the place of the archive's only account of the
        # step that removes queue records.
        out = self.assertRefused(self.without("tasks", "archive", "--plan",
                                              self.plan()),
                                 "tasks is not on PATH")
        self.assertNotIn("Traceback", out)
        self.assertIn("re-running this plan", out)
        self.assertEqual(len(self.ledger_lines()), 1)
        # And the state it left is the ordinary crash window: the ledger holds the
        # case, the queue still holds its task, and re-running clears it.
        self.assertEqual(self.record("qa-one")["status"], "blocked")
        self.assertIn("dropped  qa-one", self.archived())


class Pure(unittest.TestCase):
    """The parts that are pure functions of their inputs, driven directly."""

    def test_a_commit_is_read_off_the_end_of_a_landed_line(self):
        self.assertEqual(f.last_commit("demo#2 " + "a" * 40), "a" * 40)
        self.assertEqual(f.last_commit("nothing published"), None)
        self.assertEqual(f.last_commit(None), None)

    def test_evidence_splits_on_the_first_space_only(self):
        sha = "b" * 64
        self.assertEqual(f.split_evidence(f"{sha} /a path/with a space.md"),
                         (sha, "/a path/with a space.md"))
        self.assertEqual(f.split_evidence("obligation:choose-a-way"),
                         (None, "obligation:choose-a-way"))
        # A path with a space but no digest is not a digest and a path.
        self.assertEqual(f.split_evidence("/a path.md"), (None, "/a path.md"))

    def test_a_datetime_compares_by_instant_and_not_by_spelling(self):
        # `datafile` stores what this writes as `+00:00` normalised to `Z`, and the
        # read-back is the last gate before anything is dropped.
        self.assertEqual(f.instant("2026-08-30T09:00:00+00:00"),
                         f.instant("2026-08-30T09:00:00Z"))
        self.assertEqual(f.instant("not a date"), "not a date")

    def test_the_contract_is_read_off_the_installed_file(self):
        fields = f.contract_fields(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "template", "schema-findings.yaml"))
        self.assertEqual(fields["evidence"], "list")
        self.assertEqual(fields["round"], "int")
        self.assertEqual(fields["head_pinned"], "bool")
        self.assertNotIn("name", fields)


if __name__ == "__main__":
    unittest.main()
