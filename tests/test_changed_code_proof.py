"""Measurement custody is reusable independently of policy verdict or reviewer authority."""

import copy
import json
import os
import tempfile
import unittest
from pathlib import Path

import autocode_changed_code_evidence as policy
import autocode_changed_code_proof as proof_policy
from autocode_util import file_hash

from .test_command_receipt import guarded_command_receipt


@unittest.skipUnless(
    hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"), "POSIX descriptor-anchored capture custody"
)
class ChangedCodeProofTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="changed-code-proof-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.product = self.root / "calc.py"
        self.product.write_text("def value(n):\n    return n + 1\n")
        self.coverage_path = self.root / "coverage.json"
        self.coverage_path.write_text(json.dumps({"files": {"calc.py": {"executed_lines": [2], "missing_lines": []}}}))
        self.coverage_receipt = self.receipt("coverage", 0)
        self.mutation_receipt = self.receipt("mutant", 0)
        self.inventory = [{"path": "calc.py", "sha256": file_hash(self.product), "lines": [2]}]
        common = {"source_revision": "source-one", "proof_context": "context-one"}
        coverage = {**common, "files": [{**self.inventory[0], "executed_lines": [2]}]}
        mutation = {
            **common,
            "files": [
                {
                    "path": "calc.py",
                    "sha256": file_hash(self.product),
                    "mutants": [
                        {
                            "line": 2,
                            "outcome": "survived",
                            "failed_tests": [],
                            "evidence": [self.mutation_receipt["output"]],
                        }
                    ],
                }
            ],
        }
        self.inputs = {"inventory": self.inventory, "coverage": coverage, "mutation": mutation}
        self.assessment = policy.assess(self.inventory, coverage, mutation, **common)
        self.result = {
            "source_revision": "source-one",
            "verdict": "PASS",
            "changed_code_inputs": copy.deepcopy(self.inputs),
            "changed_code_evidence": copy.deepcopy(self.assessment),
            "changed_code_receipts": [self.coverage_receipt, self.mutation_receipt],
            "diff_coverage": {
                "collected": True,
                "report_path": str(self.coverage_path),
                "report_sha256": file_hash(self.coverage_path),
                "command_outputs": [self.coverage_receipt["output"]],
            },
        }
        self.path = self.root / "proof.json"
        self.path.write_text(json.dumps(self.result))
        self.proof = {**copy.deepcopy(self.result), "path": str(self.path), "receipt_sha256": file_hash(self.path)}
        self.review = self.root / "tester.json"
        self.review.write_text("Independent source-bound review.\n")
        gap = self.assessment["obligations"][0]
        self.exception = {
            **common,
            **{key: gap[key] for key in ("gap_id", "path", "sha256", "line")},
            "accepted": True,
            "reason": "Independent inspection establishes this exact mutant is equivalent.",
            "evidence": [str(self.product), str(self.review)],
            "reviewer": {"stage": "sol", "role": "sol", "output": str(self.review)},
        }
        self.validation = {
            "source_revision": "source-one",
            "reviewer_role": "sol",
            "changed_code_decisions": [self.exception],
            "evidence_hashes": {str(path): file_hash(path) for path in (self.product, self.review)},
        }

    def receipt(self, name, code):
        path = self.root / (name + ".log")
        path.write_text("Complete synthetic command capture " + name)
        return guarded_command_receipt(
            {
                "command": "python -m unittest tests.test_value",
                "output": str(path),
                "output_sha256": file_hash(path),
                "exit_code": code,
                "timed_out": False,
                "interrupted": False,
            }
        )

    def test_valid_unverified_gaps_reuse_measurement_without_claiming_ready(self):
        before = copy.deepcopy((self.proof, self.result))
        self.assertEqual("UNVERIFIED", self.assessment["status"])
        self.assertTrue(proof_policy.intact(self.proof, self.result))
        self.assertFalse(proof_policy.ready(self.proof, self.result, None))
        self.assertEqual(before, (self.proof, self.result))
        for value in ("PASS", "UNVERIFIED", "FAIL"):
            proof = {**self.proof, "verdict": value}
            result = {**self.result, "verdict": value}
            self.assertTrue(proof_policy.intact(proof, result))

    def test_unavailable_instrumentation_without_measurement_claims_remains_reusable(self):
        inputs = copy.deepcopy(self.inputs)
        inputs["coverage"]["files"] = []
        inputs["mutation"]["files"] = []
        assessment = policy.assess(
            inputs["inventory"],
            inputs["coverage"],
            inputs["mutation"],
            source_revision="source-one",
            proof_context="context-one",
        )
        result = {
            **self.result,
            "changed_code_inputs": inputs,
            "changed_code_evidence": assessment,
            "changed_code_receipts": [],
            "diff_coverage": {"collected": False, "note": "No available tool"},
        }
        proof = {
            **self.proof,
            "changed_code_inputs": copy.deepcopy(inputs),
            "changed_code_evidence": copy.deepcopy(assessment),
        }
        self.assertTrue(proof_policy.intact(proof, result))
        self.assertFalse(proof_policy.ready(proof, result, None))

    def test_changed_missing_or_redirected_coverage_artifact_refuses_reuse(self):
        original = self.coverage_path.read_bytes()
        for change in ("changed", "missing", "symlink"):
            with self.subTest(change=change):
                self.coverage_path.write_bytes(original)
                if change == "changed":
                    self.coverage_path.write_text("{}")
                else:
                    self.coverage_path.unlink()
                    if change == "symlink":
                        alternate = self.root / "alternate.json"
                        alternate.write_bytes(original)
                        self.coverage_path.symlink_to(alternate)
                self.assertFalse(proof_policy.intact(self.proof, self.result))
                if self.coverage_path.is_symlink():
                    self.coverage_path.unlink()

    def test_missing_coverage_pin_cannot_authenticate_a_measured_statement_map(self):
        for field in ("report_sha256", "report_path"):
            result = copy.deepcopy(self.result)
            result["diff_coverage"].pop(field)
            with self.subTest(field=field):
                self.assertFalse(proof_policy.intact(self.proof, result))

    def test_changed_command_output_or_custody_pin_refuses_reuse(self):
        for number in (0, 1):
            for field in ("output_sha256", "supervision_sha256"):
                result = copy.deepcopy(self.result)
                result["changed_code_receipts"][number][field] = "0" * 64
                with self.subTest(number=number, field=field):
                    self.assertFalse(proof_policy.intact(self.proof, result))
        Path(self.mutation_receipt["output"]).unlink()
        self.assertFalse(proof_policy.intact(self.proof, self.result))

    def test_missing_measurement_command_cannot_leave_normalized_claims_authoritative(self):
        for receipts in (None, [], [self.coverage_receipt], [self.mutation_receipt]):
            result = {**self.result, "changed_code_receipts": receipts}
            with self.subTest(receipts=receipts):
                self.assertFalse(proof_policy.intact(self.proof, result))

    def test_full_ordered_command_inventory_is_bound_even_beyond_referenced_observations(self):
        inputs = copy.deepcopy(self.inputs)
        inputs["command_outputs"] = [row["output"] for row in self.result["changed_code_receipts"]]
        result = {**self.result, "changed_code_inputs": inputs}
        proof = {**self.proof, "changed_code_inputs": copy.deepcopy(inputs)}
        self.assertTrue(proof_policy.intact(proof, result))
        reversed_receipts = list(reversed(result["changed_code_receipts"]))
        self.assertFalse(proof_policy.intact(proof, {**result, "changed_code_receipts": reversed_receipts}))
        extra = self.receipt("unreferenced", 0)
        self.assertFalse(
            proof_policy.intact(proof, {**result, "changed_code_receipts": [*result["changed_code_receipts"], extra]})
        )

    def test_altered_normalized_inputs_or_legacy_absence_refuses_reuse(self):
        result = copy.deepcopy(self.result)
        result["changed_code_inputs"]["inventory"][0]["lines"] = [3]
        self.assertFalse(proof_policy.intact(self.proof, result))
        for value in (None, [], "old summary"):
            result = {**self.result, "changed_code_inputs": value}
            self.assertFalse(proof_policy.intact(self.proof, result))
        self.assertFalse(proof_policy.intact({}, {}))

    def test_current_pinned_independent_reason_closes_only_its_exact_gap(self):
        before = copy.deepcopy((self.proof, self.result, self.validation))
        self.assertTrue(proof_policy.ready(self.proof, self.result, self.validation))
        self.assertEqual(before, (self.proof, self.result, self.validation))
        for role in ("sol", "astra"):
            self.assertTrue(proof_policy.ready(self.proof, self.result, {**self.validation, "reviewer_role": role}))
        for key, value in (("source_revision", "other"), ("proof_context", "other"), ("line", 3), ("accepted", False)):
            validation = copy.deepcopy(self.validation)
            validation["changed_code_decisions"][0][key] = value
            with self.subTest(key=key):
                self.assertFalse(proof_policy.ready(self.proof, self.result, validation))

    def test_stale_source_builder_role_or_missing_review_pins_cannot_approve_reason(self):
        for patch in (
            {"source_revision": "old"},
            {"reviewer_role": "terra"},
            {"reviewer_role": "self_check"},
            {"evidence_hashes": {}},
            {"changed_code_decisions": []},
        ):
            with self.subTest(patch=patch):
                self.assertFalse(proof_policy.ready(self.proof, self.result, {**self.validation, **patch}))
        for change in ("changed", "missing"):
            with self.subTest(change=change):
                if change == "changed":
                    self.review.write_text("Different decision.")
                else:
                    self.review.unlink()
                self.assertFalse(proof_policy.ready(self.proof, self.result, self.validation))

    def test_demonstrated_mutation_needs_no_exception_but_still_needs_custody(self):
        inputs = copy.deepcopy(self.inputs)
        mutant = inputs["mutation"]["files"][0]["mutants"][0]
        killed = self.receipt("observed-failure", 1)
        mutant.update(
            outcome="killed", failed_tests=["tests.test_value.Value.test_boundary"], evidence=[killed["output"]]
        )
        common = {"source_revision": "source-one", "proof_context": "context-one"}
        assessment = policy.assess(inputs["inventory"], inputs["coverage"], inputs["mutation"], **common)
        result = {
            **self.result,
            "changed_code_inputs": inputs,
            "changed_code_evidence": assessment,
            "changed_code_receipts": [self.coverage_receipt, killed],
        }
        proof = {
            **self.proof,
            "changed_code_inputs": copy.deepcopy(inputs),
            "changed_code_evidence": copy.deepcopy(assessment),
        }
        self.assertEqual("PASS", assessment["status"])
        self.assertTrue(proof_policy.ready(proof, result, None))
        Path(killed["output"]).write_text("Changed capture.")
        self.assertFalse(proof_policy.ready(proof, result, None))


class SelectedTestsTests(unittest.TestCase):
    """The mutation check runs the proof command the runner can narrow to the observed proving tests."""

    GATE = "/opt/py/bin/python project_gate.py"
    SUITE = "/opt/py/bin/python -m unittest discover -v -s tests"
    REGRESSION = "/opt/py/bin/python -m unittest -v test_greet.py"

    def result(self, suite, regression, suite_passed, regression_passed):
        return {
            "framework": {"name": "unittest"},
            "commands": {"suite": suite, "regression": regression},
            "checks": {
                "suite_on_candidate": {"results": {"passed": suite_passed}},
                "regression_on_candidate": {"results": {"passed": regression_passed}},
            },
        }

    def test_a_narrowable_suite_is_preferred(self):
        result = self.result(self.SUITE, self.REGRESSION, ["tests.test_greet.T.test_ada"], ["test_greet.T.test_ada"])
        self.assertEqual((None, self.SUITE, ["tests.test_greet.T.test_ada"]), proof_policy.selected_tests(None, result))

    def test_a_project_gate_script_falls_back_to_the_regression_command(self):
        # A project's own gate (#2474 stall): the runner cannot name tests to `python project_gate.py`,
        # but the proof's regression command ran the same tests and can be narrowed to them.
        result = self.result(self.GATE, self.REGRESSION, ["test_greet.T.test_ada"], ["test_greet.T.test_ada"])
        self.assertEqual((None, self.REGRESSION, ["test_greet.T.test_ada"]), proof_policy.selected_tests(None, result))

    def test_without_a_narrowable_command_the_suite_is_kept_for_the_record(self):
        result = self.result(self.GATE, "/opt/py/bin/python regress.py", ["test_greet.T.test_ada"], ["t.T.test_a"])
        self.assertEqual((None, self.GATE, ["test_greet.T.test_ada"]), proof_policy.selected_tests(None, result))
        result = self.result(self.GATE, self.REGRESSION, ["test_greet.T.test_ada"], [])
        self.assertEqual((None, self.GATE, ["test_greet.T.test_ada"]), proof_policy.selected_tests(None, result))
