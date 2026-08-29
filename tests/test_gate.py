"""What `siana-gate` answers, and what it writes down while answering it.

Every test here is one of two things: a way an action could happen that nobody
authorised, or a way a decision could reach the captain's ledger saying less than it
must. The second matters as much as the first, because an advisory night produces
nothing but the ledger, and a proposal justified by nothing is the one output that
would waste the run.

The gate's exit codes carry the whole protocol, so they are asserted directly rather
than read out of the message. `0` means nothing here refuses, `1` is a policy answer
the caller can act on, and `2` means nothing is known - and while advisory is the only
mode there is, the decision path never reaches `0` at all.
"""

import json
import os
import unittest

from advisory import PROPOSAL, Advisory
from helpers import script

gate = script("siana-gate")

REFUSED, FAULT = 1, 2


class Constants(unittest.TestCase):
    """The two facts the whole design rests on, checked where they are written.

    A test that only drove the command would pass against an allowlist that had
    quietly grown a value, as long as the class under test was not the one added."""

    def test_the_allowlist_is_empty(self):
        # Advisory is the only mode there is. This is what makes "no code path can
        # return permission" a fact about the file rather than a claim about it.
        self.assertEqual(gate.ALLOWED, ())

    def test_publish_is_the_only_class_and_it_is_r2(self):
        # The enum is closed here and read from nowhere else, so adding a class is a
        # code change with its own tests rather than a value someone can write into a
        # record. R2 is externally visible, compensable, and not undoable.
        self.assertEqual(gate.CLASSES, {"publish": "R2"})


class TheContract(Advisory):
    """The ledger's own refusals, driven through `datafile` rather than through this
    suite's idea of the contract."""

    def test_the_store_cannot_hold_a_permitted_verdict(self):
        # The zero-permission invariant, one layer below the gate. Even a hand-written
        # line cannot put `permitted` into the captain's audit trail while advisory is
        # the only mode there is, so a ledger read in the morning cannot say something
        # happened that could not have.
        out = self.run_cmd(["datafile", "-f", self.at("decisions.jsonl"),
                            "-c", self.at("schema-decisions.yaml"), "put",
                            json.dumps({"id": "publish-forged",
                                        "at": "2026-08-29T20:00:00Z",
                                        "class": "publish", "action": "anything",
                                        "verdict": "permitted"})])
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)
        self.assertEqual(self.ledger(), [])

    def test_the_store_cannot_hold_a_class_the_gate_does_not_know(self):
        out = self.run_cmd(["datafile", "-f", self.at("decisions.jsonl"),
                            "-c", self.at("schema-decisions.yaml"), "put",
                            json.dumps({"id": "merge-forged",
                                        "at": "2026-08-29T20:00:00Z",
                                        "class": "merge", "action": "anything",
                                        "verdict": "refused"})])
        self.assertNotEqual(out.returncode, 0, out.stdout + out.stderr)

    def test_every_class_the_gate_knows_is_a_value_the_contract_knows(self):
        # Two lists that have to agree, and a class the gate would decide and the
        # store would then refuse is a fault at the worst moment: after the decision
        # and before it can be recorded.
        with open(self.at("schema-decisions.yaml")) as fh:
            contract = fh.read()
        # Anchored on the field rather than on the first enum in the file: a contract
        # that grew another enum above this one would otherwise have this test
        # quietly checking that one instead.
        values = contract.split("\n  class:\n")[1].split("values: [")[1].split("]")[0]
        self.assertEqual(sorted(v.strip() for v in values.split(",")),
                         sorted(gate.CLASSES))


class WithoutASession(Advisory):
    """Advisory by absence. The captain is at the helm, so nothing is gated, and the
    proposal is written down anyway."""

    def test_it_refuses_and_records_a_proposal(self):
        out = self.decide()
        self.assertEqual(out.returncode, REFUSED, out.stdout + out.stderr)
        self.assertIn("proposed", out.stdout)
        rec, = self.ledger()
        self.assertEqual(rec["verdict"], "proposed")
        self.assertEqual(rec["action"], "siana-publish qa-add-json")
        self.assertEqual(rec["class"], "publish")
        self.assertEqual(rec["task"], "qa-add-json")
        self.assertEqual(rec["project"], "demo")
        self.assertEqual(rec["principles"], PROPOSAL["principles"])
        self.assertEqual(rec["confidence"], "high")
        self.assertEqual(rec["reversibility"], "R2")
        # No session, so nothing says which principles this was held to. Absent
        # rather than guessed: a record naming a grant that did not exist would be
        # unreplayable in exactly the way the field exists to prevent.
        self.assertIsNone(rec["grant"])

    def test_the_record_carries_the_evidence_and_what_was_rejected(self):
        # The captain reads the ledger instead of a merge request, so what SIANA read
        # and what it turned down have to survive into it verbatim.
        self.decide()
        rec, = self.ledger()
        self.assertEqual(rec["evidence"], PROPOSAL["evidence"])
        self.assertEqual(rec["alternatives"], PROPOSAL["alternatives"])

    def test_an_unknown_class_is_a_fault_and_never_a_refusal(self):
        # A refusal is a policy answer a caller can act on. A class this script has
        # never heard of means the caller and the script disagree about what the
        # world contains, and the only safe reading of that is that nothing is known.
        out = self.gate("merge", "--task", "qa-add-json",
                        "--record", self.proposal())
        self.assertEqual(out.returncode, FAULT, out.stdout + out.stderr)
        self.assertIn("unknown action class: merge", out.stderr)
        self.assertEqual(self.ledger(), [])

    def test_a_missing_ledger_contract_is_a_fault(self):
        # A decision that cannot be recorded does not happen. Answered before
        # anything else, because a refusal nobody can record is a refusal nobody will
        # ever read.
        os.remove(self.at("schema-decisions.yaml"))
        out = self.decide()
        self.assertEqual(out.returncode, FAULT, out.stdout + out.stderr)
        self.assertIn("no decision ledger contract", out.stderr)

    def test_a_ledger_that_cannot_be_written_is_a_fault(self):
        # No unrecorded autonomy, ever, including when the disk is full. Driven by
        # making the store itself unwritable, so the failure is `datafile`'s own and
        # not a guess this script makes about one.
        self.store("decisions.jsonl")
        os.chmod(self.at("decisions.jsonl"), 0o444)
        self.addCleanup(os.chmod, self.at("decisions.jsonl"), 0o644)
        out = self.decide()
        self.assertEqual(out.returncode, FAULT, out.stdout + out.stderr)
        self.assertIn("does not happen", out.stderr)


class TheRecordFile(Advisory):
    """What a proposal must say to be a decision at all.

    Shape and never meaning. Whether the cited principle supports the action is
    understanding, and a script that adjudicated it would be the thing this fleet
    keeps out of scripts. That one was cited is exact, and it removes the likeliest
    quiet failure: acting on a general sense that this is fine."""

    def test_a_proposal_that_quotes_no_principle_is_refused(self):
        out = self.decide(record=self.proposal(principles=[]))
        self.assertEqual(out.returncode, REFUSED, out.stdout + out.stderr)
        self.assertIn("quotes no principle", out.stdout)
        rec, = self.ledger()
        self.assertEqual(rec["verdict"], "refused")

    def test_a_reversibility_that_does_not_match_the_class_is_refused(self):
        # An agent that thinks publishing is reversible has misunderstood the action
        # it is about to take, and that is worth catching before the ledger records
        # it as a considered judgement.
        out = self.decide(record=self.proposal(reversibility="R0"))
        self.assertEqual(out.returncode, REFUSED, out.stdout + out.stderr)
        self.assertIn("publish is R2", out.stdout)

    def test_a_proposal_with_no_evidence_is_refused(self):
        out = self.decide(record=self.proposal(evidence=[]))
        self.assertRefused(out, "cites no evidence")

    def test_a_proposal_with_no_alternative_is_refused(self):
        # What was rejected is the half of a decision that nothing else records. The
        # diff, when there is one, only ever shows what happened.
        out = self.decide(record=self.proposal(alternatives=[]))
        self.assertRefused(out, "names no alternative")

    def test_a_confidence_the_contract_does_not_know_is_refused(self):
        out = self.decide(record=self.proposal(confidence="certain"))
        self.assertRefused(out, "confidence is 'certain'")

    def test_a_record_that_names_no_action_records_nothing(self):
        # Refused rather than faulted, and nothing is written: a record with no
        # command in it carries no decision, so there is nothing for the captain to
        # read in the morning and nothing to put in front of them.
        out = self.decide(record=self.proposal(action=None))
        self.assertEqual(out.returncode, REFUSED, out.stdout + out.stderr)
        self.assertIn("names no action", out.stderr)
        self.assertEqual(self.ledger(), [])

    def test_a_record_file_that_is_not_there_records_nothing(self):
        out = self.gate("publish", "--task", "qa-add-json",
                        "--record", self.at("never-written.json"))
        self.assertEqual(out.returncode, REFUSED, out.stdout + out.stderr)
        self.assertIn("no decision record at", out.stderr)
        self.assertEqual(self.ledger(), [])

    def test_a_record_file_that_is_not_json_records_nothing(self):
        path = self.at("broken.json")
        with open(path, "w") as fh:
            fh.write("{not json")
        out = self.gate("publish", "--task", "qa-add-json", "--record", path)
        self.assertEqual(out.returncode, REFUSED, out.stdout + out.stderr)
        self.assertEqual(self.ledger(), [])

    def test_a_decision_with_no_record_is_a_fault(self):
        # A caller that did not say what it wants has told this script nothing about
        # the action, and silence is not a policy answer.
        out = self.gate("publish", "--task", "qa-add-json")
        self.assertEqual(out.returncode, FAULT, out.stdout + out.stderr)
        self.assertIn("--task and --record", out.stderr)


class UnderASession(Advisory):
    """A live session, which is a real `siana-afk` process. It permits nothing, and
    every path through it has to end that way."""

    def setUp(self):
        super().setUp()
        self.session()

    def test_the_allowlist_is_where_every_well_formed_proposal_ends(self):
        out = self.decide()
        self.assertEqual(out.returncode, REFUSED, out.stdout + out.stderr)
        self.assertIn("in no allowlist", out.stdout)
        rec, = self.ledger()
        self.assertEqual(rec["verdict"], "refused")

    def test_the_record_names_the_session_and_the_principles_it_was_held_to(self):
        # Without this the captain who edits their principles has lost the ability to
        # read their own history: nothing would say which version of which file the
        # reasoning was measured against.
        self.decide()
        rec, = self.ledger()
        grant = self.grant()
        self.assertEqual(rec["grant"], grant["started"])
        self.assertEqual(rec["policy"], grant["sha256"])

    def test_a_task_the_queue_does_not_hold_is_refused(self):
        out = self.decide(task="qa-nothing")
        self.assertRefused(out, "is not in", "which project")

    def test_a_project_the_session_does_not_cover_is_refused(self):
        # Scope is typed at activation and gone when the session ends. A decision
        # about anything else is refused however well formed it is.
        self.project("other")
        self.store("tasks.jsonl",
                   {"id": "qa-other", "title": "QA other", "status": "done",
                    "verify": "true", "verify_kind": "cmd", "deps": [],
                    "context": [], "project": "other",
                    "updated": "2026-08-29T10:00:00Z"})
        out = self.decide(task="qa-other")
        self.assertRefused(out, "this session covers demo")

    def test_a_second_decision_gets_an_id_of_its_own(self):
        # Ids are derived from the class and the task, so two proposals about one
        # task would collide. The ledger is append-only and keyed on the id, so a
        # collision would overwrite the first decision with the second.
        self.decide()
        self.decide()
        ids = sorted(rec["id"] for rec in self.ledger())
        self.assertEqual(ids, ["publish-qa-add-json", "publish-qa-add-json-2"])


class TheLog(Advisory):
    """The captain's return report. It is the whole product of an advisory night, so
    it has to read back what was recorded and never summarise it away."""

    def test_nothing_decided_is_a_zero_and_never_a_fault(self):
        text = self.assertAccepted(self.gate("log"))
        self.assertIn("decided  nothing", text)

    def test_it_reads_back_what_was_recorded(self):
        self.decide()
        text = self.assertAccepted(self.gate("log"))
        self.assertIn("proposed", text)
        self.assertIn("siana-publish qa-add-json", text)
        self.assertIn("(no session)", text)

    def test_one_decision_comes_back_whole(self):
        # The evidence and the alternatives are what make a decision reviewable, and
        # neither fits on a line.
        self.decide()
        text = self.assertAccepted(self.gate("log", "--full", "publish-qa-add-json"))
        rec = json.loads(text)
        self.assertEqual(rec["evidence"], PROPOSAL["evidence"])
        self.assertEqual(rec["alternatives"], PROPOSAL["alternatives"])

    def test_an_id_that_is_not_there_says_what_is(self):
        self.decide()
        out = self.gate("log", "--full", "nope")
        self.assertRefused(out, "no decision nope", "publish-qa-add-json")

    def test_since_takes_a_duration_and_drops_what_is_older(self):
        self.decide()
        self.store("decisions.jsonl",
                   {"id": "publish-last-week", "at": "2026-08-20T09:00:00Z",
                    "class": "publish", "action": "siana-publish qa-old",
                    "verdict": "proposed"})
        recent = self.assertAccepted(self.gate("log", "--since", "1h"))
        self.assertIn("publish-qa-add-json", recent)
        self.assertNotIn("publish-last-week", recent)
        everything = self.assertAccepted(self.gate("log"))
        self.assertIn("publish-last-week", everything)

    def test_a_since_that_is_neither_a_time_nor_a_duration_is_refused(self):
        # Never read as "everything". A filter nobody could parse, silently ignored,
        # is a captain believing they have read the night and having read a slice.
        self.assertRefused(self.gate("log", "--since", "lastnight"),
                           "neither a time nor a duration")


if __name__ == "__main__":
    unittest.main()
