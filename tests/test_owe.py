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
        self.contract("obligations")

    def owe(self, *args):
        return self.run_bin("siana-owe", *args)

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
        self.assertAccepted(self.owe("decision", "Who owns the push", "--task", "t1"))
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

    def test_a_mistyped_field_is_refused_by_the_contract_not_accepted(self):
        # `extra: forbid` is the point of keeping this in a store.
        out = self.run_cmd(["datafile", "-f", self.at("obligations.jsonl"),
                            "-c", self.at("schema-obligations.yaml"), "put",
                            "--set", "id=abc", "--set", "kind=promise",
                            "--set", "bdoy=typo", "--set", "opened=2026-01-01T00:00:00Z"])
        self.assertNotEqual(out.returncode, 0)


if __name__ == "__main__":
    unittest.main()
