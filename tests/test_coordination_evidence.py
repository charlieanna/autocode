"""Joint publication consumes fresh native proof, including mixed plan methods."""

import copy
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_coordinate as coordinator
import autocode_coordination_evidence as evidence
from autocode_coordination_store import CoordinationError


def completed_view():
    methods = [
        "Run `python3 -m pytest tests -q` from the workspace root and assert exit 0",
        "test: test_composed_total",
        "guard: test_preserved_boundary",
        "Inspect exact candidate-copy equality including deletions",
        "Run `cd repos/a && python3 -m pytest tests -q` and `cd repos/b && python3 -m pytest tests -q`",
    ]
    criteria = [
        {"id": f"AC{i}", "criterion": f"Behavior {i}", "verification_method": method, "human_review": False}
        for i, method in enumerate(methods, 1)
    ]
    document = {
        "binding": "binding",
        "plan": {"contract_token": "approved"},
        "revision": {"base_commit": "baseline", "source_revision": "candidate"},
        "criteria": [
            {**row, "status": "verified", "validator_status": "PASS", "evidence_refs": ["authenticated-check"]}
            for row in criteria
        ],
        "regression_proof": {
            "source_revision": "candidate",
            "verdict": "PASS",
            "fail_to_pass": ["test_composed_total"],
            "case_tests": {"AC2": ["test_composed_total"], "AC3": ["test_preserved_boundary"]},
        },
    }
    return {
        "done": True,
        "approved_contract": {"token": "approved", "body": {"acceptance_criteria": criteria}},
        "evidence_report": {
            "availability": "current",
            "binding": "binding",
            "json_path": "/joint/evidence.json",
            "json_sha256": "json-hash",
            "markdown_sha256": "markdown-hash",
            "document": document,
        },
    }


class CoordinationEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.view = completed_view()
        self.run = Mock(run_dir=Path("/joint/run"), status=Mock(return_value=self.view))

    def test_mixed_approved_methods_keep_their_exact_authenticated_evidence(self):
        result = evidence.capture(self.run, "baseline")
        self.assertEqual("PASS", result["verdict"])
        self.assertEqual("task_run", result["kind"])
        self.assertEqual(self.view["evidence_report"], result["evidence_report"])
        self.run.status.assert_called_once_with(inspect_evidence=True)
        evidence.require_current(self.run, "baseline", result)

    def test_only_current_legacy_reports_can_select_the_external_verifier(self):
        self.assertTrue(evidence.uses_native_proof(self.view))
        self.view["evidence_report"]["document"]["regression_proof"] = {"verdict": "FAIL"}
        self.assertTrue(evidence.uses_native_proof(self.view))
        self.view["evidence_report"]["document"]["regression_proof"] = None
        self.assertFalse(evidence.uses_native_proof(self.view))

    def test_missing_or_invalid_report_cannot_fall_back_to_another_verifier(self):
        for report in (
            {},
            {"availability": "invalid"},
            {"availability": "stale", "document": {}},
            {"availability": "current"},
        ):
            with self.subTest(report=report):
                self.view["evidence_report"] = report
                with self.assertRaisesRegex(CoordinationError, "current authenticated"):
                    evidence.uses_native_proof(self.view)

    def test_missing_stale_wrong_or_partial_proof_never_authorizes_delivery(self):
        changes = [
            lambda v: v.update(done=False),
            lambda v: v["evidence_report"].update(availability="recorded"),
            lambda v: v["evidence_report"].update(availability="stale"),
            lambda v: v["evidence_report"]["document"].update(binding="other"),
            lambda v: v["evidence_report"]["document"]["revision"].update(base_commit="other"),
            lambda v: v["evidence_report"]["document"]["plan"].update(contract_token="other"),
            lambda v: v["evidence_report"]["document"]["regression_proof"].update(verdict="UNVERIFIED"),
            lambda v: v["evidence_report"]["document"]["regression_proof"].update(source_revision="old"),
            lambda v: v["evidence_report"]["document"]["regression_proof"].update(fail_to_pass=[]),
            lambda v: v["evidence_report"]["document"]["regression_proof"]["case_tests"].pop("AC3"),
            lambda v: v["evidence_report"]["document"]["regression_proof"]["case_tests"].update(AC2=["unproven"]),
            lambda v: v["evidence_report"]["document"]["criteria"].pop(),
            lambda v: v["evidence_report"]["document"]["criteria"][0].update(validator_status="NOT_VERIFIED"),
            lambda v: v["evidence_report"]["document"]["criteria"][0].update(evidence_refs=[]),
            lambda v: v["evidence_report"]["document"]["criteria"][0].update(verification_method="different"),
            lambda v: v["approved_contract"]["body"]["acceptance_criteria"][0].update(human_review=True),
        ]
        for index, change in enumerate(changes):
            with self.subTest(index=index):
                view = copy.deepcopy(self.view)
                change(view)
                self.run.status.return_value = view
                with self.assertRaises(CoordinationError):
                    evidence.capture(self.run, "baseline")

    def test_publication_rechecks_the_saved_binding_without_launching_a_verifier(self):
        saved = copy.deepcopy(evidence.capture(self.run, "baseline"))
        document = {"joint": {"base": "baseline", "workspace": "/joint", "verification": saved}}
        with (
            patch.object(coordinator, "handle", return_value=self.run),
            patch.object(coordinator.joint, "mismatches", return_value=[]),
            patch.object(coordinator.tasks, "run_process") as launch,
        ):
            coordinator.require_joint_evidence(document)
            self.view["evidence_report"]["json_sha256"] = "changed"
            with self.assertRaisesRegex(CoordinationError, "changed after verification"):
                coordinator.require_joint_evidence(document)
            launch.assert_not_called()

    def test_joint_native_cost_is_counted_once_and_legacy_external_review_is_retained(self):
        self.run.status.return_value = {"run_dir": "/joint/run", "cost": {"spent_usd": 3}}
        joint = {"started": True, "run_dir": "/joint/run", "verification": {"kind": "task_run"}}
        document = {"members": [], "joint": joint, "joint_attempts": [joint]}
        with patch.object(coordinator, "handle", return_value=self.run):
            _, history = coordinator.budget_observations(document, {})
            self.assertEqual([self.run.status.return_value], history)
            joint["verification"] = {
                "record": "/external",
                "evidence_report": {"document": {"attempts": [{"reported_cost_usd": 2}]}},
            }
            _, history = coordinator.budget_observations(document, {})
            self.assertEqual(5, sum(row["cost"]["spent_usd"] for row in history))
