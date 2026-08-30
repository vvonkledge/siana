"""siana-owe: the store that keeps a promise from dying with the session.

The pure half is the id derivation and the age the open view shows. The rest is
driven as a process, against a real `datafile`, because the three failures this
store exists to refuse are all failures of the whole command: closing from
recollection, closing twice, and rewriting a promise while retiring it.
"""

import json
import os
import unittest
from datetime import UTC, datetime, timedelta

from helpers import HomeTest, script

o = script("siana-owe")


class Slug(unittest.TestCase):
    """Ids are derived from the body, never invented, so an id is self-describing
    in the one line of the open view that has no room to repeat the body."""

    def test_words_are_kept_whole_up_to_the_budget(self):
        s = o.slug("Report on the fleet's health every morning", set())
        self.assertLessEqual(len(s), o.SLUG_BUDGET)
        self.assertFalse(s.endswith("-"))
        for word in s.split("-"):
            self.assertIn(word, "report on the fleet s health every morning".split())

    def test_it_never_ends_on_a_stopword(self):
        # "report-to-the" would read as a fragment cut mid-sentence.
        self.assertEqual(o.slug("report to the", set()), "report")

    def test_an_id_always_starts_with_a_letter(self):
        # The contract's pattern requires it, so a body starting with a digit has
        # to be prefixed here rather than refused at write.
        self.assertTrue(o.slug("3 things to fix", set()).startswith("owe-"))

    def test_a_body_with_no_usable_words_still_yields_an_id(self):
        self.assertEqual(o.slug("!!! ???", set()), "owed")

    def test_a_very_short_body_is_padded_to_the_contract_minimum(self):
        self.assertGreaterEqual(len(o.slug("hi", set())), 3)

    def test_a_taken_id_is_suffixed_rather_than_reused(self):
        first = o.slug("report to the", set())
        second = o.slug("report to the", {first})
        self.assertNotEqual(first, second)
        self.assertTrue(second.startswith("report"))

    def test_every_id_it_derives_satisfies_the_contract_pattern(self):
        import re
        pattern = re.compile(r"^[a-z][a-z0-9-]{2,47}$")
        taken = set()
        for body in ["hi", "3 things to fix", "!!!", "report to the",
                     "Ask the captain how their health is holding up today",
                     "UPPER CASE AND  weird   spacing", "a", "of the and or"]:
            s = o.slug(body, taken)
            self.assertRegex(s, pattern, f"{body!r} derived {s!r}")
            taken.add(s)


class Age(unittest.TestCase):
    """How long this has been owed. An old promise is one the captain has stopped
    expecting, so the number is the part of the history that matters."""

    def stamp(self, **delta):
        return (datetime.now(UTC) - timedelta(**delta)).isoformat()

    def test_minutes_hours_and_days(self):
        self.assertEqual(o.age(self.stamp(minutes=5)), "5m")
        self.assertEqual(o.age(self.stamp(hours=3)), "3h")
        self.assertEqual(o.age(self.stamp(days=9)), "9d")

    def test_a_hand_edited_row_without_a_zone_is_read_as_utc(self):
        naive = (datetime.now(UTC) - timedelta(hours=2)).replace(tzinfo=None)
        self.assertEqual(o.age(naive.isoformat()), "2h")

    def test_a_clock_that_ran_backwards_never_shows_a_negative_age(self):
        self.assertEqual(o.age(self.stamp(minutes=-30)), "0m")

    def test_an_unreadable_stamp_says_it_does_not_know(self):
        self.assertEqual(o.age("not a date"), "?")
        self.assertEqual(o.age(None), "?")


class Fold(HomeTest):

    def test_a_line_that_is_not_json_is_a_refusal_with_the_repair_to_run(self):
        # The queue's reader skips such a line; this one stops. An obligation store
        # that quietly reads short is the one wrong answer it must never give.
        store = self.store("obligations.jsonl", "{broken")
        with self.assertRaises(o.Refusal) as cm:
            o.fold(store, "id")
        self.assertIn("not JSON", cm.exception.message)
        self.assertIn("repair", " ".join(cm.exception.hints))


class Command(HomeTest):
    """The whole command, against a real store."""

    def setUp(self):
        super().setUp()
        self.contract("obligations", "attended")

    def owe(self, *args):
        return self.run_bin("siana-owe", *args)

    # The reasoning half of a decision, as one argument list. Every field of it is
    # required, so a test that only cares about the obligation would otherwise have
    # to restate all five, and the restatements would drift apart.
    REASONING = ("--situation", "Two worktrees claim the same branch",
                 "--option", "Retire the older one",
                 "--consequence", "One tree goes; the branch is untouched",
                 "--option", "Leave both and report",
                 "--consequence", "Dispatch stays blocked on that project",
                 "--recommend", "Leave both and report",
                 "--because", "Neither tree can be shown to be the stale one")

    def decision(self, body, *args):
        return self.owe("decision", body, *self.REASONING, *args)

    def records(self):
        with open(self.at("obligations.jsonl")) as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_no_contract_is_a_stop(self):
        os.remove(self.at("schema-obligations.yaml"))
        self.assertRefused(self.owe(), "no obligations contract", "just init")

    def test_nothing_owed_reads_as_a_zero_and_not_a_warning(self):
        self.assertIn("owed     nothing", self.assertAccepted(self.owe()))

    def test_a_promise_is_recorded_and_shows_up_in_the_open_view(self):
        out = self.assertAccepted(self.owe("promise", "Report on the fleet at noon"))
        self.assertIn("(promise)", out)
        view = self.assertAccepted(self.owe())
        self.assertIn("promise", view)
        self.assertIn("Report on the fleet at noon", view)

    def test_the_clock_is_stamped_by_the_command_not_typed_by_an_agent(self):
        self.assertAccepted(self.owe("promise", "Report on the fleet at noon"))
        opened = datetime.fromisoformat(self.records()[0]["opened"])
        self.assertLess(abs((datetime.now(UTC) - opened).total_seconds()), 120)

    def test_an_empty_body_is_refused(self):
        self.assertRefused(self.owe("promise", "   "), "needs a body")

    def test_a_decision_can_name_the_task_it_is_about(self):
        self.assertAccepted(self.decision("Who owns the push", "--task", "t1"))
        self.assertEqual(self.records()[0]["task"], "t1")
        self.assertIn("(t1)", self.assertAccepted(self.owe()))

    def test_closing_without_naming_what_answered_it_is_refused(self):
        # An obligation retired by recollection is the thing this store replaces.
        out = self.assertAccepted(self.owe("promise", "Report on the fleet at noon"))
        rid = self.records()[0]["id"]
        self.assertRefused(self.owe("close", rid), "what answered", "recollection")
        self.assertIn(rid, out)

    def test_closing_an_unknown_id_lists_what_is_open(self):
        self.assertAccepted(self.owe("promise", "Report on the fleet at noon"))
        rid = self.records()[0]["id"]
        self.assertRefused(self.owe("close", "nope", "--answer", "x"),
                           "nothing owed under that id", rid)

    def test_closing_retires_it_and_the_body_survives_untouched(self):
        # `put` writes a whole record, so a close that restated the body from memory
        # could silently rewrite the promise it was meant to retire.
        body = "Report on the fleet at noon: 3 in flight, 1 blocked"
        self.assertAccepted(self.owe("promise", body))
        rid = self.records()[0]["id"]
        self.assertIn("answered", self.assertAccepted(
            self.owe("close", rid, "--answer", "the noon report, delivered")))
        closed = [r for r in self.records() if r["id"] == rid][-1]
        self.assertEqual(closed["body"], body)
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(closed["answer"], "the noon report, delivered")
        self.assertIn("owed     nothing", self.assertAccepted(self.owe()))

    def test_closing_twice_is_refused_and_names_what_answered_it_first(self):
        # Closing again would hide which event actually retired it.
        self.assertAccepted(self.owe("promise", "Report on the fleet at noon"))
        rid = self.records()[0]["id"]
        self.assertAccepted(self.owe("close", rid, "--answer", "the noon report"))
        self.assertRefused(self.owe("close", rid, "--answer", "something else"),
                           "already answered by", "the noon report")

    def test_two_promises_with_the_same_body_get_different_ids(self):
        self.assertAccepted(self.owe("promise", "Report to the captain"))
        self.assertAccepted(self.owe("promise", "Report to the captain"))
        self.assertEqual(len({r["id"] for r in self.records()}), 2)

    def test_the_open_view_is_oldest_first(self):
        # The longest-owed obligation is the one most likely to have been forgotten.
        self.assertAccepted(self.owe("promise", "First thing owed"))
        self.assertAccepted(self.owe("promise", "Second thing owed"))
        view = self.assertAccepted(self.owe())
        self.assertLess(view.index("First thing owed"), view.index("Second thing owed"))

    def test_the_closed_view_shows_what_was_answered_and_by_what(self):
        # An obligation without what answered it is the recollection this store
        # replaces, so a closed view that omitted the answer would be worse than none.
        self.assertAccepted(self.owe("promise", "Report on the fleet at noon"))
        rid = self.records()[0]["id"]
        self.assertAccepted(self.owe("close", rid, "--answer", "the noon report"))
        out = self.assertAccepted(self.owe("closed"))
        self.assertIn(rid, out)
        self.assertIn("Report on the fleet at noon", out)
        self.assertIn("the noon report", out)

    def test_the_closed_view_omits_what_is_still_open(self):
        self.assertAccepted(self.owe("promise", "Still owed"))
        out = self.assertAccepted(self.owe("closed"))
        self.assertNotIn("Still owed", out)
        self.assertIn("answered nothing", out)

    def test_the_closed_view_is_newest_first(self):
        # The opposite of the open view, and deliberately so: a captain asking what
        # was delivered is asking about the recent past.
        self.assertAccepted(self.owe("promise", "First thing owed"))
        self.assertAccepted(self.owe("promise", "Second thing owed"))
        first, second = (r["id"] for r in self.records()[:2])
        self.assertAccepted(self.owe("close", first, "--answer", "answered first"))
        self.assertAccepted(self.owe("close", second, "--answer", "answered second"))
        out = self.assertAccepted(self.owe("closed"))
        self.assertLess(out.index("Second thing owed"), out.index("First thing owed"))

    def test_nothing_answered_yet_reads_as_a_zero_and_not_a_warning(self):
        self.assertIn("answered nothing", self.assertAccepted(self.owe("closed")))

    def test_the_bare_view_is_untouched_by_the_closed_one(self):
        # `siana` injects the bare view into SIANA's system prompt at every session
        # start, so a change to its shape changes what SIANA is told it owes.
        self.assertIn("owed     nothing", self.assertAccepted(self.owe()))
        self.assertAccepted(self.owe("promise", "Report on the fleet at noon"))
        rid = self.records()[0]["id"]
        self.assertAccepted(self.owe("promise", "Second thing owed"))
        self.assertAccepted(self.owe("close", rid, "--answer", "the noon report"))
        view = self.assertAccepted(self.owe())
        self.assertIn("owed     1", view)
        self.assertIn("Second thing owed", view)
        self.assertNotIn("Report on the fleet at noon", view)
        self.assertNotIn("the noon report", view)

    def hand_written_closed(self, rid="no-answer-field"):
        """A closed obligation carrying no answer. `close` refuses to make one, so
        this is written straight through `datafile` - the only way such a record
        exists, and therefore the only way the guard against it can be tested."""
        out = self.run_cmd(["datafile", "-f", self.at("obligations.jsonl"),
                            "-c", self.at("schema-obligations.yaml"), "put",
                            json.dumps({"id": rid, "kind": "decision",
                                        "body": "Closed with no answer recorded",
                                        "status": "closed",
                                        "opened": "2026-01-01T00:00:00+00:00",
                                        "closed": "2026-08-01T00:00:00+00:00"})])
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return rid

    def test_an_answer_the_store_holds_as_null_reads_as_unrecorded(self):
        # `datafile` materialises every optional field, so an absent answer is
        # stored as null and not as a missing key. A fallback written against the
        # key's absence never fires, and the captain reads a Python `None`.
        rid = self.hand_written_closed()
        out = self.assertAccepted(self.owe("closed"))
        self.assertIn("(unrecorded)", out)
        self.assertNotIn("None", out)

    def test_the_already_answered_refusal_reads_as_unrecorded_too(self):
        # The same expression, in the refusal that says why a second close is not
        # allowed. A `None` there is the captain being told the obligation was
        # answered by nothing.
        rid = self.hand_written_closed()
        out = self.assertRefused(self.owe("close", rid, "--answer", "anything"),
                                 "already answered by")
        self.assertIn("(unrecorded)", out)
        self.assertNotIn("None", out)

    def test_a_real_answer_is_never_replaced_by_the_fallback(self):
        # The guard must not swallow the thing it guards.
        self.assertAccepted(self.owe("promise", "Report on the fleet at noon"))
        rid = self.records()[0]["id"]
        self.assertAccepted(self.owe("close", rid, "--answer", "the noon report"))
        out = self.assertAccepted(self.owe("closed"))
        self.assertIn("the noon report", out)
        self.assertNotIn("(unrecorded)", out)

    def test_a_mistyped_field_is_refused_by_the_contract_not_accepted(self):
        # `extra: forbid` is the point of keeping this in a store.
        out = self.run_cmd(["datafile", "-f", self.at("obligations.jsonl"),
                            "-c", self.at("schema-obligations.yaml"), "put",
                            "--set", "id=abc", "--set", "kind=promise",
                            "--set", "bdoy=typo", "--set", "opened=2026-01-01T00:00:00Z"])
        self.assertNotEqual(out.returncode, 0)


class AttendedDecisions(HomeTest):
    """The learning corpus: what the captain was asked, what they were offered, what
    SIANA would have chosen and why, what they said, and how it turned out.

    The property under test throughout is that the answer lives in exactly one store.
    Everything else about this arrangement is bookkeeping; that one thing is what
    keeps a corpus from becoming a second, staler record of the captain's decisions.
    """

    def setUp(self):
        super().setUp()
        self.contract("obligations", "attended")

    def owe(self, *args):
        return self.run_bin("siana-owe", *args)

    REASONING = ("--situation", "Six branches are retained and two are a week old",
                 "--option", "Reap them now",
                 "--consequence", "The branches go; recovery is the forge only",
                 "--option", "Keep them until QA lands",
                 "--consequence", "The list grows and dispatch eventually blocks",
                 "--recommend", "Keep them until QA lands",
                 "--because", "A wrong reap is the one mistake here that loses work")

    def decision(self, body="Whether to reap the retained branches", *args):
        return self.owe("decision", body, *self.REASONING, *args)

    def attended(self):
        with open(self.at("attended.jsonl")) as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_a_decision_records_its_reasoning_beside_the_obligation(self):
        self.assertAccepted(self.decision())
        rec = self.attended()[-1]
        self.assertEqual(rec["options"],
                         ["Reap them now", "Keep them until QA lands"])
        self.assertEqual(len(rec["consequences"]), 2)
        self.assertEqual(rec["recommendation"], "Keep them until QA lands")
        self.assertIn("loses work", rec["rationale"])

    def test_the_two_records_share_one_id(self):
        # The join, and the whole reason nothing is copied: the corpus reads the
        # answer out of the obligation every time, so there is nothing to go stale.
        self.assertAccepted(self.decision())
        with open(self.at("obligations.jsonl")) as fh:
            obligation = json.loads(fh.readline())
        self.assertEqual(self.attended()[-1]["id"], obligation["id"])

    def test_the_answer_is_never_copied_into_the_reasoning_record(self):
        self.assertAccepted(self.decision())
        rid = self.attended()[-1]["id"]
        self.assertAccepted(self.owe("close", rid, "--answer", "keep them"))
        self.assertNotIn("answer", self.attended()[-1])
        self.assertIn("keep them", self.assertAccepted(self.owe("history")))

    def test_one_option_is_refused(self):
        out = self.owe("decision", "Something", "--situation", "s",
                       "--option", "only one", "--consequence", "c",
                       "--recommend", "only one", "--because", "r")
        self.assertRefused(out, "at least two --option", "notification")

    def test_a_mismatched_consequence_list_is_refused(self):
        out = self.owe("decision", "Something", "--situation", "s",
                       "--option", "a", "--option", "b", "--consequence", "c",
                       "--recommend", "a", "--because", "r")
        self.assertRefused(out, "2 options and 1 consequences")

    def test_a_recommendation_that_is_not_an_option_is_refused(self):
        # Otherwise the corpus records agreement with a choice that was never on the
        # table, which is the one thing a training corpus must not hold.
        out = self.owe("decision", "Something", "--situation", "s",
                       "--option", "a", "--consequence", "ca",
                       "--option", "b", "--consequence", "cb",
                       "--recommend", "c", "--because", "r")
        self.assertRefused(out, "--recommend is not one of the options")

    def test_a_decision_missing_its_reasoning_is_refused(self):
        out = self.owe("decision", "Something", "--option", "a", "--option", "b",
                       "--consequence", "ca", "--consequence", "cb")
        self.assertRefused(out, "--situation", "--recommend", "--because")

    def test_nothing_is_written_anywhere_when_the_reasoning_is_refused(self):
        # The obligation is written first, so a validator that ran after it would
        # leave the captain a question with no reasoning behind it.
        self.owe("decision", "Something", "--situation", "s",
                 "--option", "only one", "--consequence", "c",
                 "--recommend", "only one", "--because", "r")
        self.assertFalse(os.path.exists(self.at("obligations.jsonl")))
        self.assertFalse(os.path.exists(self.at("attended.jsonl")))

    def test_a_promise_needs_none_of_it(self):
        # Only a decision carries reasoning. A promise is a thing SIANA owes, and
        # there is nothing for the captain to choose between.
        self.assertAccepted(self.owe("promise", "Report on the fleet at noon"))
        self.assertFalse(os.path.exists(self.at("attended.jsonl")))

    def test_an_outcome_before_an_answer_is_refused(self):
        self.assertAccepted(self.decision())
        rid = self.attended()[-1]["id"]
        out = self.owe("outcome", rid, "--outcome", "it went fine")
        self.assertRefused(out, "has not been answered yet", "is a guess")

    def test_an_outcome_after_the_answer_is_recorded(self):
        self.assertAccepted(self.decision())
        rid = self.attended()[-1]["id"]
        self.assertAccepted(self.owe("close", rid, "--answer", "keep them"))
        self.assertAccepted(self.owe("outcome", rid, "--outcome",
                                     "QA landed and they reaped cleanly"))
        self.assertEqual(self.attended()[-1]["outcome"],
                         "QA landed and they reaped cleanly")
        self.assertIn("outcome_at", self.attended()[-1])

    def test_history_joins_both_stores(self):
        self.assertAccepted(self.decision())
        rid = self.attended()[-1]["id"]
        self.assertAccepted(self.owe("close", rid, "--answer", "keep them"))
        out = self.assertAccepted(self.owe("history"))
        self.assertIn("Whether to reap the retained branches", out)
        self.assertIn("Keep them until QA lands", out)
        self.assertIn("keep them", out)

    def test_history_as_json_is_one_row_per_decision(self):
        self.assertAccepted(self.decision())
        rows = json.loads(self.assertAccepted(self.owe("history", "--json")))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "open")
        self.assertEqual(rows[0]["body"], "Whether to reap the retained branches")

    def test_an_open_decision_shows_no_captain_answer_at_all(self):
        # Absent, not "(unrecorded)". Nobody has been asked yet, and that is a
        # different fact from a record written without its answer.
        self.assertAccepted(self.decision())
        self.assertNotIn("captain", self.assertAccepted(self.owe("history")))

    def test_a_decision_written_before_this_store_existed_stays_readable(self):
        # The migration case. An obligation of kind `decision` with no reasoning is
        # not part of the corpus, because it never carried what a corpus is made of,
        # and inventing empty options for it would record that the captain was
        # offered nothing.
        self.store("obligations.jsonl",
                   {"id": "older-decision", "kind": "decision",
                    "body": "Something asked before the corpus existed",
                    "status": "open", "opened": "2026-01-01T00:00:00+00:00"})
        self.assertIn("Something asked before",
                      self.assertAccepted(self.owe()))
        self.assertIn("decided  nothing",
                      self.assertAccepted(self.owe("history")))

    def test_reasoning_whose_obligation_is_gone_is_reported_and_not_dropped(self):
        # A corpus that quietly loses rows is one nobody can count.
        self.assertAccepted(self.decision())
        rid = self.attended()[-1]["id"]
        self.store("obligations.jsonl", {"id": rid, "_deleted": True})
        out = self.assertAccepted(self.owe("history"))
        self.assertIn("orphan", out)
        self.assertIn("the obligation for this is gone", out)

    def test_no_attended_contract_is_a_stop_for_a_decision(self):
        os.remove(self.at("schema-attended.yaml"))
        self.assertRefused(self.decision(), "no attended-decision contract",
                           "just upgrade")

    def test_no_attended_contract_is_a_stop_for_history_too(self):
        # A home that has not been upgraded reported `decided nothing`, which is what
        # a home with an empty corpus reports, while `siana-owe` and `siana-owe
        # closed` went on listing real decisions. An uninstalled store rendered as an
        # empty one is the failure the rest of this reporting is written against.
        self.assertAccepted(self.decision())
        os.remove(self.at("schema-attended.yaml"))
        self.assertRefused(self.owe("history"), "no attended-decision contract")
        self.assertRefused(self.owe("history", "--json"),
                           "no attended-decision contract")
        # The obligation itself is still readable, which is what makes the silent
        # version of this so easy to miss.
        self.assertIn("Whether to reap", self.assertAccepted(self.owe()))

    def test_a_corrupt_line_names_the_store_that_is_corrupt(self):
        # `fold` had one caller and one store when its recovery was written. It has
        # two now, and a recovery naming the wrong file quarantines nothing.
        self.assertAccepted(self.decision())
        self.store("attended.jsonl", "{half a record")
        out = self.assertRefused(self.owe("history"), "is not JSON")
        self.assertIn("-f attended.jsonl repair", out)
        self.assertNotIn("obligations.jsonl repair", out)

    def test_the_advisory_ledger_is_a_different_store_entirely(self):
        # `decisions.jsonl` holds what SIANA would have done while the captain was
        # away, and nobody was asked. Folding the two would put rows in the corpus
        # with no captain answer that could ever arrive.
        self.assertAccepted(self.decision())
        self.assertFalse(os.path.exists(self.at("decisions.jsonl")))
        rows = json.loads(self.assertAccepted(self.owe("history", "--json")))
        self.assertEqual([r["id"] for r in rows], [self.attended()[-1]["id"]])


if __name__ == "__main__":
    unittest.main()
