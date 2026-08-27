"""siana-dispatch's mechanics: reading a store, and turning a handle into a place.

Everything here runs without herdr. What herdr does is dispatch's other half, but
the half that decides *where* a minion lands is pure, and it is the half that fails
silently: a fold that misreads a tombstone dispatches into a project the captain
deleted, and a resolve that guesses puts a minion in the wrong tree with no sign
that anything went wrong.
"""

import io
import os
import unittest
from contextlib import redirect_stdout

from helpers import HomeTest, script

d = script("siana-dispatch")


class Fold(HomeTest):
    """The append-only read every reader in the distro shares."""

    def test_no_file_is_an_empty_store(self):
        # A store with no writes yet has no .jsonl at all. Absent must read as
        # empty, because the contract is what says the store exists.
        self.assertEqual(d.fold(self.at("nothing.jsonl"), "id"), {})

    def test_last_write_for_an_id_wins(self):
        store = self.store("s.jsonl", {"id": "a", "v": 1}, {"id": "a", "v": 2})
        self.assertEqual(d.fold(store, "id")["a"]["v"], 2)

    def test_a_tombstone_is_an_absence_not_an_empty_record(self):
        # The failure this guards: reading `_deleted` as just another last write
        # leaves a record with every field missing, which a caller reads as a
        # project that exists and has no path.
        store = self.store("s.jsonl", {"id": "a", "v": 1}, {"id": "a", "_deleted": True})
        self.assertNotIn("a", d.fold(store, "id"))

    def test_a_record_after_a_tombstone_resurrects_it(self):
        store = self.store("s.jsonl", {"id": "a", "v": 1},
                           {"id": "a", "_deleted": True}, {"id": "a", "v": 3})
        self.assertEqual(d.fold(store, "id")["a"]["v"], 3)

    def test_a_tombstone_for_an_unseen_id_is_not_an_error(self):
        store = self.store("s.jsonl", {"id": "ghost", "_deleted": True})
        self.assertEqual(d.fold(store, "id"), {})

    def test_blank_lines_and_keyless_records_are_skipped(self):
        store = self.store("s.jsonl", {"id": "a", "v": 1}, "", "   ", {"v": 9})
        self.assertEqual(list(d.fold(store, "id")), ["a"])

    def test_the_key_is_the_one_asked_for(self):
        store = self.store("s.jsonl", {"handle": "p", "path": "/tmp"})
        self.assertEqual(list(d.fold(store, "handle")), ["p"])
        self.assertEqual(d.fold(store, "id"), {})


class LoadRegistry(HomeTest):

    def test_no_contract_is_a_stop_not_an_empty_registry(self):
        # Dispatching against an empty registry means dispatching to a path nobody
        # wrote down, so absent has to refuse rather than resolve to nothing.
        with self.assertRaises(d.Refusal) as cm:
            d.load_registry(self.at("projects.jsonl"), self.at("schema-projects.yaml"))
        self.assertIn("no project registry", cm.exception.message)
        self.assertIn("just init", " ".join(cm.exception.hints))

    def test_a_contract_with_no_records_is_an_empty_registry(self):
        self.contract("projects")
        self.assertEqual(
            d.load_registry(self.at("projects.jsonl"), self.at("schema-projects.yaml")),
            {})


class Resolve(HomeTest):
    """Handle to configuration. What a contract cannot check is checked here."""

    def registry(self, **fields):
        return {"p": {"handle": "p", "path": self.home, **fields}}

    def test_an_unknown_handle_names_the_ones_that_exist(self):
        with self.assertRaises(d.Refusal) as cm:
            d.resolve(self.registry(), "nope", "reg")
        self.assertIn("unknown project: nope", cm.exception.message)
        self.assertIn("p", " ".join(cm.exception.hints))

    def test_an_empty_registry_says_so_rather_than_listing_nothing(self):
        with self.assertRaises(d.Refusal) as cm:
            d.resolve({}, "nope", "reg")
        self.assertIn("the registry is empty", " ".join(cm.exception.hints))

    def test_a_path_that_is_not_a_directory_is_refused(self):
        with self.assertRaises(d.Refusal) as cm:
            d.resolve({"p": {"handle": "p", "path": self.at("gone")}}, "p", "reg")
        self.assertIn("not a directory", cm.exception.message)

    def test_worktree_defaults_to_isolated_for_a_record_predating_the_field(self):
        # Isolation withheld risks data loss; isolation added does not. A record
        # written before the field has to read as the safe direction.
        self.assertTrue(d.resolve(self.registry(), "p", "reg")["worktree"])

    def test_worktree_false_survives_the_read(self):
        self.assertFalse(d.resolve(self.registry(worktree=False), "p", "reg")["worktree"])

    def test_relative_orders_resolve_against_the_project_path(self):
        open(self.at("ORDERS.md"), "w").close()
        cfg = d.resolve(self.registry(orders="ORDERS.md"), "p", "reg")
        self.assertEqual(cfg["orders"], os.path.join(self.home, "ORDERS.md"))

    def test_absolute_orders_are_taken_as_written(self):
        other = self.at("elsewhere.md")
        open(other, "w").close()
        cfg = d.resolve(self.registry(orders=other), "p", "reg")
        self.assertEqual(cfg["orders"], other)

    def test_orders_that_do_not_exist_are_refused(self):
        # A minion started without orders it was promised is a silent half-briefing,
        # which looks exactly like a working dispatch.
        with self.assertRaises(d.Refusal) as cm:
            d.resolve(self.registry(orders="missing.md"), "p", "reg")
        self.assertIn("which does not exist", cm.exception.message)

    def test_ship_and_qa_are_carried_through(self):
        cfg = d.resolve(self.registry(ship="just check", qa="just qa"), "p", "reg")
        self.assertEqual((cfg["ship"], cfg["qa"]), ("just check", "just qa"))


class AssembleOrders(HomeTest):
    """Two system-prompt files become one, because claude keeps only the last."""

    def setUp(self):
        super().setUp()
        self.base = self.at("orders.md")
        with open(self.base, "w") as fh:
            fh.write("FLEET ORDERS\n")

    def test_no_project_orders_leaves_the_base_file_alone(self):
        self.assertEqual(d.assemble_orders(self.home, "t1", self.base, None, "p"),
                         self.base)
        self.assertFalse(os.path.exists(self.at("orders", "t1.md")))

    def test_project_orders_are_concatenated_into_one_durable_file(self):
        # The failure this exists to prevent: passing the flag twice silently drops
        # the fleet's standing orders and keeps only the project's.
        with open(self.at("project.md"), "w") as fh:
            fh.write("PROJECT ORDERS\n")
        combined = d.assemble_orders(self.home, "t1", self.base,
                                     self.at("project.md"), "p")
        self.assertEqual(combined, self.at("orders", "t1.md"))
        with open(combined) as fh:
            text = fh.read()
        self.assertIn("FLEET ORDERS", text)
        self.assertIn("PROJECT ORDERS", text)
        self.assertIn("# Project orders: p", text)
        self.assertLess(text.index("FLEET ORDERS"), text.index("PROJECT ORDERS"))


class CheckRegistry(HomeTest):
    """`--check` resolves the way a dispatch resolves, so a moved path surfaces
    here rather than halfway through starting a minion."""

    def check(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = d.check_registry(self.at("projects.jsonl"),
                                  self.at("schema-projects.yaml"))
        return rc, buf.getvalue()

    def test_an_empty_registry_is_reported_and_is_not_a_fault(self):
        self.contract("projects")
        rc, out = self.check()
        self.assertEqual(rc, 0)
        self.assertIn("no projects yet", out)

    def test_a_healthy_project_is_ok_and_shows_its_configuration(self):
        self.contract("projects")
        self.project("good", ship="just check", qa="just qa")
        rc, out = self.check()
        self.assertEqual(rc, 0)
        self.assertIn("ok      good", out)
        self.assertIn("ship: just check", out)
        self.assertIn("qa: just qa", out)

    def test_a_broken_project_fails_the_check_and_is_named(self):
        self.contract("projects")
        self.project("broken", path=self.at("gone"))
        rc, out = self.check()
        self.assertEqual(rc, 1)
        self.assertIn("BROKEN  broken", out)

    def test_one_broken_project_does_not_hide_the_others(self):
        # A check that stopped at the first bad entry would report a registry the
        # captain then believes they have fully seen.
        self.contract("projects")
        self.project("broken", path=self.at("gone"))
        self.project("good")
        rc, out = self.check()
        self.assertEqual(rc, 1)
        self.assertIn("BROKEN  broken", out)
        self.assertIn("ok      good", out)

    def test_worktree_isolation_on_a_non_git_path_is_warned_about(self):
        self.contract("projects")
        self.project("nogit")
        _, out = self.check()
        self.assertIn("WARNING: not a git repo", out)


class RefusalCarriesItsMessage(unittest.TestCase):

    def test_str_of_a_refusal_is_what_it_said(self):
        # A bare SystemExit(1) stringifies to "1", so every `in str(e)` check that
        # reacts to a refusal silently never matched. Dispatch has three.
        r = d.Refusal("the message", ["a hint"])
        self.assertEqual(str(r), "the message")
        self.assertEqual(r.hints, ("a hint",))

    def test_unreachable_is_a_refusal_and_distinguishable_from_one(self):
        # A caller asking after one pane must not read herdr's silence as an answer
        # about that pane, so the two have to be catchable apart.
        self.assertTrue(issubclass(d.Unreachable, d.Refusal))
        with self.assertRaises(d.Unreachable):
            d.die("nothing answered", kind=d.Unreachable)


if __name__ == "__main__":
    unittest.main()
