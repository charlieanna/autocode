"""Retiring validation must not retire its bounded independent test repair."""

import copy
import tempfile
import unittest
from pathlib import Path

import autocode_acceptance_tests as acceptance
import autocode_validation_recovery as recovery

from . import test_independent_evidence_repair as fixtures


class RetainedEvidenceRepairTests(unittest.TestCase):
    def retained(self, root, product="src/bind.py", test="tests/test_bind.py"):
        state, record = fixtures.IndependentEvidenceRepairTests().fixture(root, product, test)
        self.assertEqual("builder", recovery.permission_retry(state, {"stage": "builder", "changed_files": []}))
        self.assertNotIn("validation", state)
        return state, record

    def test_real_retirement_keeps_the_independent_repair_without_restoring_validation(self):
        for product, test in (
            ("src/bind.py", "tests/test_bind.py"),
            ("lib/bind.js", "spec/bind.spec.js"),
            ("bind.go", "bind_test.go"),
        ):
            with self.subTest(product=product), tempfile.TemporaryDirectory() as directory:
                state, _ = self.retained(Path(directory), product, test)
                before = copy.deepcopy(state)
                self.assertEqual(["C1", "C2"], [case["id"] for case in acceptance.due(state)])
                self.assertEqual(before, state, "Reading retained authority must not restore validation")
                acceptance.route(state)
                self.assertEqual(acceptance.STAGE, state["next_stage"])
                self.assertNotIn("validation", state)
                for key in ("goal_contract", "current_task", "settings", "regression_proof", "validation_archive"):
                    self.assertEqual(before[key], state[key], key)
                self.assertEqual(len(before["regression_proofs"]), len(state["regression_proofs"]))
                self.assertEqual(before["regression_proofs"][-1]["round"], state["regression_proofs"][-1]["round"])

    def test_identical_retained_copies_and_unrelated_history_do_not_choose_another_review(self):
        with tempfile.TemporaryDirectory() as directory:
            state, _ = self.retained(Path(directory))
            accepted = state["validation_archive"][0]
            other = copy.deepcopy(accepted)
            other["validation"]["output"] = "unrelated-review.json"
            for current in (False, True):
                for rows in ([other, accepted], [accepted, other, copy.deepcopy(accepted)]):
                    with self.subTest(current=current, unrelated_first=rows[0] is other):
                        candidate = {**copy.deepcopy(state), "validation_archive": copy.deepcopy(rows)}
                        if current:
                            candidate["validation"] = copy.deepcopy(accepted["validation"])
                        self.assertEqual(["C1", "C2"], [case["id"] for case in acceptance.due(candidate)])

    def test_retired_repairs_keep_all_source_review_and_allowance_bindings(self):
        with tempfile.TemporaryDirectory() as directory:
            state, _ = self.retained(Path(directory))
            for owner, key, value in (
                ("validation", "source_revision", "another-source"),
                ("validation", "task_id", "another-task"),
                ("validation", "contract_revision", 2),
                ("validation", "reviewer_role", "builder"),
                ("validation", "evidence_hashes", {state["validation_archive"][0]["validation"]["output"]: "0" * 64}),
                ("validation", "changed_code_decisions", []),
                ("archive", "rolled_back", True),
                ("archive", "checked_earlier", True),
                ("event", "action", "investigator"),
                ("event", "review_output", None),
                ("event", "source_revision", "another-source"),
                ("event", "task_id", "another-task"),
                ("event", "contract_hash", "another-contract"),
                ("event", "contract_revision", 2),
            ):
                with self.subTest(owner=owner, key=key):
                    candidate = copy.deepcopy(state)
                    target = candidate["validation_archive"][0]
                    if owner == "validation":
                        target = target["validation"]
                    elif owner == "event":
                        target = candidate["regression_proofs"][-1]
                    target[key] = value
                    self.assertEqual([], acceptance.due(candidate))
            for fault in ("conflicting_copy", "new_current_review", "newer_rework", "source_drift"):
                with self.subTest(fault=fault):
                    candidate = copy.deepcopy(state)
                    if fault == "conflicting_copy":
                        duplicate = copy.deepcopy(candidate["validation_archive"][0])
                        duplicate["validation"]["changed_code_decisions"][0]["reason"] = "conflicting decision"
                        candidate["validation_archive"].append(duplicate)
                    elif fault == "new_current_review":
                        candidate["validation"] = {**candidate["validation_archive"][0]["validation"], "output": "new"}
                    elif fault == "newer_rework":
                        later = {**candidate["regression_proofs"][-1], "review_output": "new-review"}
                        candidate["regression_proofs"].append(later)
                    else:
                        candidate["workspace"] = str(Path(directory))
                        product = Path(directory) / "src/bind.py"
                        original = product.read_bytes()
                        product.write_bytes(original + b"# changed after review\n")
                    self.assertEqual([], acceptance.due(candidate))
