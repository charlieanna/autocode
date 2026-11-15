"""Reapproval keeps the latest qualified test file, including partial case rewrites."""

from __future__ import annotations

import copy
import itertools
import json
import tempfile
import unittest
from pathlib import Path

import autocode_acceptance_tests as acceptance


class AcceptanceArchiveOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        body = {
            "acceptance_criteria": [
                {"id": cid, "criterion": cid, "verification_method": "test: test_" + cid.lower()}
                for cid in ("C1", "C2")
            ]
        }
        self.state = {
            "settings": {"spec_first": True},
            "goal_contract": {"hash": "h3", "revision": 3, "approval_status": "approved", "body": body},
            "contract_history": [{"hash": f"h{i}", "revision": i, "body": copy.deepcopy(body)} for i in (1, 2)],
            "user_events": [{"kind": "goal_approval", "token": f"r{i}:h{i}"} for i in (1, 2)],
            "stages": [{"output": "z-older.json"}, {"output": "a-newer.json"}],
            "next_stage": "tester",
        }
        self.old = self.receipt("h1", ["C1", "C2"], "z-older.json", "old helper\n")
        self.new = self.receipt("h2", ["C1"], "a-newer.json", "qualified helper\n")

    def receipt(self, contract, cases, output, text, path="tests/test_contract.py"):
        archive = self.root / (output + ".archive")
        archive.write_text(text)
        return {
            "contract_hash": contract,
            "case_ids": cases,
            "output": output,
            "results": {cid: "fails" for cid in cases},
            "files": {path: {"archive": str(archive), "sha256": acceptance.sha256(archive)}},
        }

    def install(self, rows):
        # Real saves sort batch hashes; their lexical order is not generation order.
        self.state["acceptance_tests"] = {"receipts": json.loads(json.dumps(dict(rows), sort_keys=True))}

    def test_reapproval_keeps_newest_shared_file_for_partial_or_whole_case_rewrites(self):
        for cases, receipts in itertools.product(
            (["C1"], ["C1", "C2"]),
            ((("a", self.new), ("z", self.old)), (("a", self.old), ("z", self.new))),
        ):
            with self.subTest(cases=cases, old_first=receipts[0][1] is self.old):
                self.new["case_ids"] = cases
                self.install(receipts)
                before = copy.deepcopy(self.state)
                self.assertEqual(self.new["files"], acceptance.protected(self.state))
                self.assertEqual({"C1", "C2"}, acceptance.covered(self.state))
                self.assertEqual("a-newer.json", acceptance.view(self.state)["output"])
                self.assertEqual(before, self.state)

    def test_additive_builder_test_is_kept_and_does_not_queue_another_builder(self):
        self.install((("a", self.old), ("z", self.new)))
        target = self.workspace / "tests/test_contract.py"
        target.parent.mkdir()
        candidate = b"qualified helper\nnew detecting test\n"
        target.write_bytes(candidate)
        self.assertFalse(acceptance.check_untouched(self.state, self.workspace))
        self.assertEqual(candidate, target.read_bytes())
        self.assertEqual("tester", self.state["next_stage"])
        self.assertNotIn("builder_edits", self.state["acceptance_tests"])

    def test_changed_builder_lines_restore_latest_archive_and_keep_the_gate_strict(self):
        self.install((("a", self.new), ("z", self.old)))
        target = self.workspace / "tests/test_contract.py"
        target.parent.mkdir()
        target.write_text("weakened assertion\n")
        self.assertTrue(acceptance.check_untouched(self.state, self.workspace))
        self.assertEqual(b"qualified helper\n", target.read_bytes())
        self.assertEqual("builder", self.state["next_stage"])

    def test_file_selection_keeps_older_files_not_touched_by_the_latest_generation(self):
        other = self.receipt("h1", ["C2"], "middle.json", "other tests\n", "pkg/other.spec.js")
        self.state["stages"].insert(1, {"output": "middle.json"})
        for rows in itertools.permutations((self.old, self.new, other)):
            self.install(zip(("a", "b", "c"), rows, strict=True))
            self.assertEqual({**other["files"], **self.new["files"]}, acceptance.all_files(self.state))

    def test_multiple_batches_in_one_contract_follow_stage_order_not_batch_or_output_name(self):
        self.new["contract_hash"] = "h1"
        for rows in ((("a", self.new), ("z", self.old)), (("a", self.old), ("z", self.new))):
            self.install(rows)
            self.assertEqual("a-newer.json", acceptance.inherited(self.state)["C1"]["output"])
            self.assertEqual(self.new["files"], acceptance.protected(self.state))

    def test_saved_contract_revision_orders_legacy_receipts_without_stage_rows(self):
        self.state["stages"] = []
        self.install((("a", self.new), ("z", self.old)))
        self.assertEqual(self.new["files"], acceptance.protected(self.state))

    def test_a_stage_without_an_output_does_not_order_a_legacy_receipt(self):
        self.new["contract_hash"] = "h1"
        self.old.pop("output")
        self.state["stages"].append({"stage": "local bookkeeping"})
        self.install((("a", self.new), ("z", self.old)))
        self.assertEqual(self.new["files"], acceptance.protected(self.state))
        self.assertEqual("a-newer.json", acceptance.view(self.state)["output"])

    def test_unrelated_legacy_history_entries_do_not_break_file_custody(self):
        self.state["contract_history"][:0] = [None, {}]
        self.install((("a", self.new), ("z", self.old)))
        self.assertEqual(self.new["files"], acceptance.protected(self.state))

    def test_current_contract_archive_wins_over_inherited_files(self):
        self.new["contract_hash"] = "h3"
        self.state["goal_contract"].pop("revision")  # legacy fixture: current authority still wins
        self.install((("a", self.new), ("z", self.old)))
        self.assertEqual(self.new["files"], acceptance.protected(self.state))
        self.assertEqual("a-newer.json", acceptance.view(self.state)["output"])

    def test_ineligible_newer_receipts_cannot_replace_an_inherited_file(self):
        for reason in ("unapproved", "changed criterion", "corrupt archive"):
            with self.subTest(reason=reason):
                state = copy.deepcopy(self.state)
                self.install((("a", self.old), ("z", self.new)))
                if reason == "unapproved":
                    self.state["user_events"].pop()
                elif reason == "changed criterion":
                    self.state["contract_history"][1]["body"]["acceptance_criteria"][0]["criterion"] = "different"
                else:
                    self.new["files"]["tests/test_contract.py"]["sha256"] = "not the archive hash"
                    self.install((("a", self.old), ("z", self.new)))
                self.assertEqual(self.old["files"], acceptance.protected(self.state))
                self.state = state

    def test_invalid_stage_inventories_leave_qualified_revision_fallback_intact(self):
        self.install((("a", self.new), ("z", self.old)))
        for stages in (None, "none", True, 1, {"output": "foreign.json"}, (), [None, "bad"], [False, {}]):
            with self.subTest(stages=stages):
                self.state["stages"] = stages
                before = copy.deepcopy(self.state)
                self.assertEqual(self.new["files"], acceptance.all_files(self.state))
                self.assertEqual(self.new["files"], acceptance.protected(self.state))
                self.assertEqual({"C1", "C2"}, acceptance.covered(self.state))
                self.assertEqual("a-newer.json", acceptance.view(self.state)["output"])
                self.assertEqual(before, self.state)

    def test_only_dictionary_rows_with_string_outputs_supply_generation_order(self):
        self.new["contract_hash"] = "h1"
        for noise in (
            None,
            "bad",
            False,
            {},
            {"output": None},
            {"output": ""},
            {"output": []},
            {"output": {}},
            {"output": ["bad"]},
            {"output": {"bad": 1}},
            {"output": 1},
        ):
            for rows in ((("a", self.new), ("z", self.old)), (("a", self.old), ("z", self.new))):
                with self.subTest(noise=noise, newer_first=rows[0][1] is self.new):
                    self.state["stages"] = [noise, {"output": "z-older.json"}, noise, {"output": "a-newer.json"}, noise]
                    self.install(rows)
                    before = copy.deepcopy(self.state)
                    self.assertEqual("a-newer.json", acceptance.inherited(self.state)["C1"]["output"])
                    self.assertEqual(self.new["files"], acceptance.protected(self.state))
                    self.assertEqual("a-newer.json", acceptance.view(self.state)["output"])
                    self.assertEqual(before, self.state)

    def test_malformed_revision_hashes_do_not_break_an_empty_public_view(self):
        for value in (["bad"], {"bad": 1}, True, 1, None, ""):
            for location in ("history", "current"):
                with self.subTest(value=value, location=location):
                    record = {"hash": value, "revision": 99}
                    state = {"status": "RUNNING", "stages": []}
                    state["contract_history" if location == "history" else "goal_contract"] = (
                        [record] if location == "history" else record
                    )
                    before = copy.deepcopy(state)
                    self.assertIsNone(acceptance.view(state))
                    self.assertEqual(before, state)

    def test_non_string_hashes_cannot_supply_revision_precedence(self):
        for value in (None, "", True, 1):
            with self.subTest(value=value):
                state = copy.deepcopy(self.state)
                state["contract_history"].append({"hash": value, "revision": 99})
                known = {"contract_hash": "h1"}
                unknown = {"contract_hash": value}
                self.assertEqual([unknown, known], acceptance._ordered_receipts(state, [known, unknown]))

    def test_malformed_unqualified_receipt_outputs_supply_no_order_or_authority(self):
        for value in (["bad"], {"bad": 1}, True, 1, None, ""):
            with self.subTest(value=value):
                state = copy.deepcopy(self.state)
                state["acceptance_tests"] = {"receipts": {"foreign": {"contract_hash": "foreign", "output": value}}}
                before = copy.deepcopy(state)
                self.assertEqual({}, acceptance.inherited(state))
                self.assertEqual({}, acceptance.protected(state))
                self.assertIsNone(acceptance.view(state))
                self.assertEqual(before, state)
