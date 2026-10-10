"""Offline truth tables for progressive evidence-driven stagnation policy."""

import copy
import unittest

from autocode_progressive_progress import classify


def inputs():
    binding = {
        "contract_token": "contract",
        "plan_hash": "plan",
        "slice_id": "S1",
        "task_id": "T1",
        "attempt": 1,
        "assignment_source": "before-build",
        "validated_source": "after-build",
    }
    receipt = {
        "status": "PASS",
        "exit_code": 0,
        "identity": "receipt-A",
        "check_hash": "definition-A",
        "source_revision": "after-build",
        "binding": copy.deepcopy(binding),
        "evidence_hashes": {"output": "hash"},
    }
    return {
        "due_checks": [{"id": "A", "check_hash": "definition-A"}],
        "results": {"A": receipt},
        "current_binding": binding,
        "proof_identity": "manifest-A",
        "receipts_authenticated": True,
        "new_obligation_ids": ["A"],
        "seen_proof_identities": [],
        "seen_receipt_identities": [],
        "gaps": [
            {"kind": "criterion", "id": "C1", "status": "NOT_VERIFIED"},
            {"kind": "task", "id": "T2", "status": "OPEN"},
        ],
        "deferred_criteria": ["C1"],
        "deferred_tasks": ["T2"],
        "findings": [],
    }


class ProgressiveProgressTests(unittest.TestCase):
    def assert_kind(self, data, kind, reason):
        before = copy.deepcopy(data)
        result = classify(**data)
        self.assertEqual(
            {"kind": kind, "reason": reason, "proof_identity": data["proof_identity"] if kind == "progress" else None},
            result,
        )
        self.assertEqual(before, data, "classification must not consume proof or mutate caller state")

    def test_due_result_truth_table(self):
        rows = [
            ({}, "failure", "due_check_not_pass"),
            ({"status": "FAIL"}, "failure", "due_check_not_pass"),
            ({"status": "SKIPPED"}, "failure", "due_check_not_pass"),
            ({"status": "NOT_VERIFIED"}, "failure", "due_check_not_pass"),
            ({"status": "PASS"}, "progress", "new_verified_due_proof"),
            ({"status": "PASS", "exit_code": 1}, "failure", "due_check_not_pass"),
            ({"status": "PASS", "exit_code": False}, "failure", "due_check_not_pass"),
        ]
        for change, kind, reason in rows:
            with self.subTest(change=change):
                data = inputs()
                data["results"]["A"].pop("status")
                data["results"]["A"].update(change)
                self.assert_kind(data, kind, reason)
        for receipt in (None, {}, "PASS", True):
            with self.subTest(receipt=receipt):
                data = inputs()
                data["results"]["A"] = receipt
                reason = (
                    "missing_due_check"
                    if receipt is None
                    else "due_check_not_pass"
                    if receipt == {}
                    else "malformed_receipt"
                )
                self.assert_kind(data, "failure", reason)
        data = inputs()
        data["results"] = {}
        self.assert_kind(data, "failure", "missing_due_check")

    def test_explicit_authentication_not_receipt_truthiness(self):
        for auth in (False, None, 1, "verified", {"success": True}):
            with self.subTest(auth=auth):
                data = inputs()
                data["receipts_authenticated"] = auth
                self.assert_kind(data, "failure", "unauthenticated_receipts")

    def test_receipt_definition_and_validated_source_cannot_be_substituted(self):
        for field, value in (
            ("check_hash", "old-definition"),
            ("source_revision", "before-build"),
            ("source_revision", "old-source"),
        ):
            with self.subTest(field=field, value=value):
                data = inputs()
                data["results"]["A"][field] = value
                self.assert_kind(data, "failure", "stale_receipt")

    def test_required_receipt_fields(self):
        for field in ("identity", "check_hash", "source_revision", "binding", "evidence_hashes", "exit_code"):
            with self.subTest(field=field):
                data = inputs()
                del data["results"]["A"][field]
                reason = (
                    "stale_receipt"
                    if field in ("check_hash", "source_revision")
                    else "malformed_binding"
                    if field == "binding"
                    else "due_check_not_pass"
                    if field == "exit_code"
                    else "malformed_receipt"
                )
                self.assert_kind(data, "failure", reason)
        for evidence in ({}, [], {"output": ""}, {"": "hash"}, {"output": True}):
            with self.subTest(evidence=evidence):
                data = inputs()
                data["results"]["A"]["evidence_hashes"] = evidence
                self.assert_kind(data, "failure", "malformed_receipt")

    def test_every_binding_identity_must_be_exact_and_current(self):
        for field in inputs()["current_binding"]:
            with self.subTest(field=field):
                data = inputs()
                data["results"]["A"]["binding"][field] = 2 if field == "attempt" else "old"
                self.assert_kind(data, "failure", "stale_receipt")
        for binding in (
            None,
            {},
            {**inputs()["current_binding"], "attempt": True},
            {**inputs()["current_binding"], "validated_source": ""},
        ):
            with self.subTest(binding=binding):
                data = inputs()
                data["current_binding"] = binding
                self.assert_kind(data, "failure", "malformed_binding")
        # Assignment and validated snapshots need not be equal.
        self.assert_kind(inputs(), "progress", "new_verified_due_proof")

    def test_findings_use_entire_ledger_and_existing_blocking_flags(self):
        for severity in ("critical", "high", "medium", "low"):
            for status in ("open", "resolved", "retracted"):
                for blocking in (True, False, None):
                    with self.subTest(severity=severity, status=status, blocking=blocking):
                        data = inputs()
                        row = {
                            "id": "future-product-defect",
                            "severity": severity,
                            "status": status,
                            "criterion_ids": ["C1"],
                            "task_id": "T2",
                        }
                        if blocking is not None:
                            row["blocking"] = blocking
                        data["findings"] = [{"status": "resolved", "severity": "low"}, row]
                        blocked = status == "open" and blocking is not False
                        self.assert_kind(
                            data,
                            "failure" if blocked else "progress",
                            "product_blocker" if blocked else "new_verified_due_proof",
                        )
        for ledger in (
            {},
            [None],
            [{"status": "open", "severity": "unknown"}],
            [{"status": "open", "severity": "low", "blocking": "false"}],
        ):
            with self.subTest(ledger=ledger):
                data = inputs()
                data["findings"] = ledger
                self.assert_kind(data, "failure", "malformed_findings")

    def test_only_explicit_expected_future_gaps(self):
        for kind, deferred_key in (("criterion", "deferred_criteria"), ("task", "deferred_tasks")):
            for status in ("OPEN", "NOT_VERIFIED", "FAIL", "SKIPPED", "BLOCKED"):
                for declared in (True, False):
                    with self.subTest(kind=kind, status=status, declared=declared):
                        data = inputs()
                        data["gaps"] = [{"kind": kind, "id": "future", "status": status}]
                        data[deferred_key] = ["future"] if declared else []
                        expected = declared and status in ("OPEN", "NOT_VERIFIED")
                        self.assert_kind(
                            data,
                            "progress" if expected else "failure",
                            "new_verified_due_proof" if expected else "unexpected_gap",
                        )
        data = inputs()
        data["gaps"] = []
        self.assert_kind(data, "progress", "new_verified_due_proof")

    def test_duplicate_and_no_new_proof_never_progress(self):
        for field, value, reason in (
            ("seen_proof_identities", ["manifest-A"], "duplicate_proof"),
            ("seen_receipt_identities", ["receipt-A"], "duplicate_proof"),
            ("new_obligation_ids", [], "no_new_obligation"),
        ):
            with self.subTest(field=field):
                data = inputs()
                data[field] = value
                self.assert_kind(data, "pending", reason)
                data["results"]["A"]["status"] = "FAIL"
                self.assert_kind(data, "failure", "due_check_not_pass")
        data = inputs()
        data["seen_proof_identities"] = ["manifest-A"]
        data["findings"] = [{"severity": "low", "status": "open"}]
        self.assert_kind(data, "failure", "product_blocker")
        data = inputs()
        data.update(due_checks=[], results={}, new_obligation_ids=[])
        self.assert_kind(data, "pending", "no_due_checks")

    def test_intermediate_task_and_checkpoint_use_the_due_set(self):
        data = inputs()
        # B is not due at the intermediate boundary, so only A needs proof.
        self.assert_kind(data, "progress", "new_verified_due_proof")
        data["due_checks"].append({"id": "B", "check_hash": "definition-B"})
        # A declared future task cannot waive a now-due cumulative obligation.
        data["deferred_tasks"].append("B")
        self.assert_kind(data, "failure", "missing_due_check")
        data["results"]["B"] = {
            **copy.deepcopy(data["results"]["A"]),
            "identity": "receipt-B",
            "check_hash": "definition-B",
        }
        data["new_obligation_ids"] = ["B"]
        self.assert_kind(data, "progress", "new_verified_due_proof")
        data["results"]["A"]["status"] = "FAIL"
        self.assert_kind(data, "failure", "due_check_not_pass")

    def test_one_receipt_identity_cannot_prove_two_due_checks(self):
        data = inputs()
        data["due_checks"].append({"id": "B", "check_hash": "definition-B"})
        data["results"]["B"] = {**copy.deepcopy(data["results"]["A"]), "check_hash": "definition-B"}
        data["new_obligation_ids"] = ["B"]
        self.assert_kind(data, "failure", "malformed_receipt")

    def test_malformed_normalized_inputs_fail_closed(self):
        rows = [
            ("new_obligation_ids", ["unknown"], "malformed_checks"),
            ("due_checks", [{"id": "A"}], "malformed_checks"),
            ("due_checks", inputs()["due_checks"] * 2, "malformed_checks"),
            ("results", {"unknown": {}}, "malformed_checks"),
            ("results", [], "malformed_checks"),
            ("gaps", [{}], "malformed_gaps"),
            ("gaps", [{"kind": [], "id": "C1", "status": "OPEN"}], "malformed_gaps"),
            ("gaps", inputs()["gaps"] * 2, "malformed_gaps"),
            ("proof_identity", "", "malformed_proof_identity"),
            ("deferred_tasks", "T2", "malformed_input"),
            ("seen_proof_identities", [None], "malformed_input"),
        ]
        for field, value, reason in rows:
            with self.subTest(field=field, value=value):
                data = inputs()
                data[field] = value
                self.assert_kind(data, "failure", reason)


if __name__ == "__main__":
    unittest.main()
