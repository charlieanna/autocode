"""Offline pure activation preparation and persisted binding checks."""
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from tools import autocode_contract_identity as identity
from tools import autocode_progressive_activation as activation
from tools import autocode_progressive_artifacts as artifacts
from tools import autocode_progressive_plan as plan
from tools import autocode_util as util


def slice_row(sid, tentative=False):
    return {"id": sid, "intended_result": "Exercise persists learner progress",
            "criterion_ids": ["C1"], "paths": ["src/learning.py"],
            "depends_on": [], "tentative": tentative,
            "checks": [{"id": "check-" + sid, "relation": "contributes_to",
                        "criterion_ids": ["C1"],
                        "method": "python -m unittest tests.test_" + sid.lower()}]}


class ProgressiveActivationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.initial = {"version": 1, "needed_because": "Several useful deliveries",
                        "shared_decisions": ["Keep the storage interface"],
                        "outstanding_criteria": [], "done_slices": [],
                        "slices": [slice_row("S1"), slice_row("S2", True)]}
        body = {"acceptance_criteria": [{"id": "C1", "description": "Complete learning flow"}],
                **plan.disclosure(self.initial, ["C1"])}
        self.contract = {"task_id": "T1", "revision": 1, "body": body,
                         "approval_status": "approved"}
        self.contract["hash"] = util.digest({key: self.contract[key]
                                             for key in ("task_id", "revision", "body")})
        self.progressive = {"version": 1,
                            "delegation": plan.seal_delegation(self.initial, identity.token(self.contract)),
                            "initial_plan": {"proposal": self.initial,
                                             "plan_hash": plan.plan_identity(self.initial)},
                            "allowances": {"pool1": {"review_calls": 2, "stage_seconds": 5300}},
                            "whole_run_seconds": 9000, "repair_count": 3}
        self.previous = deepcopy(self.progressive["initial_plan"])
        self.proposal = deepcopy(self.initial)
        self.proposal["done_slices"] = ["S1"]
        self.proposal["slices"] = [slice_row("S2")]
        self.proposal["slices"][0]["depends_on"] = ["S1"]
        self.source = {"revision": "retained-source", "files": {"src/learning.py": "source-hash"}}
        self.required = plan.retain(self.initial["slices"][0]["checks"], "S1")
        self.receipt = {"authenticated": True, "accepted": True,
                        "stage": "configured-review", "role": "configured-reviewer",
                        "session": "review-session", "planner_stage": "configured-plan",
                        "planner_role": "configured-planner", "planner_session": "planner-session"}
        self.publish()

    def publish(self, *, candidate_bindings=None, review_bindings=None, report_changes=None):
        bindings = {"contract_token": identity.token(self.contract),
                    "predecessor_identity": self.previous["plan_hash"],
                    "candidate_identity": plan.plan_identity(self.proposal),
                    "plan_identity": plan.plan_identity(self.proposal),
                    "source_snapshot_identity": util.digest(self.source)}
        envelope, _ = artifacts.prepare("revision", report={"proposal": self.proposal},
                                         **{**bindings, **(candidate_bindings or {})})
        self.candidate = artifacts.persist(self.temp.name, envelope)
        report = {"accepted": True, "candidate_sha256": self.candidate["sha256"],
                  "product_changes": False, "permission_changes": False,
                  "unresolved_product_decisions": False,
                  **{key: self.receipt[key] for key in ("stage", "role", "session")},
                  **(report_changes or {})}
        envelope, _ = artifacts.prepare("review", report=report,
                                         **{**bindings, **(review_bindings or {})})
        self.review = artifacts.persist(self.temp.name, envelope)
        self.receipt.update(artifact=deepcopy(self.review), output_hash=util.digest(report))

    def inputs(self):
        return {"run_dir": self.temp.name, "contract": self.contract,
                "contract_authenticated": True, "progressive": self.progressive,
                "previous_plan": self.previous, "candidate_artifact": self.candidate,
                "review_artifact": self.review, "source_snapshot": self.source,
                "review_receipt": self.receipt, "verified_done": ["S1"],
                "verified_criteria": [], "required_checks": self.required,
                "product_findings": [], "blockers": dict.fromkeys(activation.BLOCKERS, False)}

    def prepare(self, **changes):
        return activation.prepare_activation(**{**self.inputs(), **changes})

    def test_prepares_active_revision_without_accepting_product(self):
        result = self.prepare()
        self.assertEqual(result["active"]["definition"]["id"], "S2")
        self.assertEqual(result["active"]["plan_hash"], plan.plan_identity(self.proposal))
        self.assertEqual(result["active"]["artifact"], self.candidate)
        self.assertEqual(result["active"]["review"], self.review)
        self.assertEqual([row["id"] for row in result["required_checks"]], ["check-S1", "check-S2"])
        self.assertEqual(result["outstanding_criteria"], ["C1"])
        self.assertNotEqual(result["active"]["plan_hash"], self.progressive["delegation"]["plan_hash"])

    def test_repeated_preparation_is_deterministic_and_detached_preserving_inputs(self):
        before = deepcopy(self.inputs())
        result = self.prepare()
        recovered = self.prepare()
        self.assertEqual(result, recovered)
        result["active"]["definition"]["paths"].append("other.py")
        result["required_checks"][0]["criterion_ids"].append("FAKE")
        result["active"]["artifact"]["sha256"] = "changed"
        self.assertEqual(self.inputs(), before)
        self.assertEqual(self.prepare(), recovered)

    def test_split_retains_checks_findings_and_spent_allowances(self):
        self.proposal["slices"] = [slice_row("S2a"), slice_row("S2b", True)]
        self.publish()
        before = deepcopy(self.progressive)
        findings = [{"id": "minor-product-finding", "blocking": False}]
        original_findings = deepcopy(findings)
        result = self.prepare(product_findings=findings)
        self.assertEqual([row["id"] for row in result["required_checks"]], ["check-S1", "check-S2a"])
        self.assertEqual(self.progressive, before)
        self.assertEqual(findings, original_findings)

    def test_changed_source_rejects_even_when_supplied_revision_label_matches(self):
        source = deepcopy(self.source)
        source["files"]["src/learning.py"] = "changed-during-review"
        with self.assertRaisesRegex(ValueError, "source_snapshot_identity"):
            self.prepare(source_snapshot=source)

    def test_every_candidate_and_review_envelope_binding_is_exact(self):
        for key in ("contract_token", "predecessor_identity", "candidate_identity",
                    "plan_identity", "source_snapshot_identity"):
            for side in ("candidate_bindings", "review_bindings"):
                with self.subTest(key=key, side=side):
                    self.publish(**{side: {key: "stale"}})
                    with self.assertRaisesRegex(ValueError, key):
                        self.prepare()

    def test_review_file_tampering_rejects(self):
        path = Path(self.temp.name) / self.review["path"]
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.prepare()

    def test_republished_review_cannot_reuse_accepted_output_receipt(self):
        old_receipt = deepcopy(self.receipt)
        self.publish(report_changes={"notes": "different accepted output"})
        old_receipt["artifact"] = self.review
        with self.assertRaisesRegex(ValueError, "accepted output"):
            self.prepare(review_receipt=old_receipt)

    def test_unknown_delegation_and_initial_identity_changes_reject(self):
        for change in ({"delegation": {}}, {"version": 2},
                       {"initial_plan": {"proposal": self.proposal, "plan_hash": plan.plan_identity(self.proposal)}}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.prepare(progressive={**self.progressive, **change})

    def test_tentative_head_and_fabricated_done_slices_reject(self):
        self.proposal["slices"][0]["tentative"] = True
        self.publish()
        with self.assertRaisesRegex(ValueError, "head slice"):
            self.prepare()
        self.proposal["slices"][0]["tentative"] = False
        self.proposal["done_slices"].append("invented")
        self.publish()
        with self.assertRaisesRegex(ValueError, "without independently verified"):
            self.prepare()

    def test_missing_completion_witness_cannot_satisfy_head_dependency(self):
        with self.assertRaises(ValueError):
            self.prepare(verified_done=[])

    def test_boundary_blockers_are_complete_exact_booleans(self):
        for key in activation.BLOCKERS:
            for value in (True, None, 0, "false"):
                blockers = dict.fromkeys(activation.BLOCKERS, False)
                blockers[key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    self.prepare(blockers=blockers)
        with self.assertRaises(ValueError):
            self.prepare(blockers={})

    def test_product_findings_cannot_be_hidden_or_unknown(self):
        for finding in ({"blocking": True}, {"blocking": 0}, {"status": "future"}):
            with self.subTest(finding=finding), self.assertRaises(ValueError):
                self.prepare(product_findings=[finding])

    def test_review_decision_requires_explicit_bool_and_no_protected_implications(self):
        for changes in ({"accepted": 1}, {"accepted": False}, {"candidate_sha256": "other"},
                        {"product_changes": True}, {"permission_changes": True},
                        {"unresolved_product_decisions": True}, {"permission_changes": 0}):
            with self.subTest(changes=changes):
                self.publish(report_changes=changes)
                with self.assertRaises(ValueError):
                    self.prepare()

    def test_runner_receipt_authenticates_actual_independent_acceptance(self):
        for changes in ({"authenticated": 1}, {"authenticated": False}, {"accepted": 1},
                        {"artifact": self.candidate}, {"output_hash": "fake"},
                        {"stage": "model-label"}, {"role": "model-label"},
                        {"session": "other"}, {"planner_session": "review-session"},
                        {"planner_role": "configured-reviewer"},
                        {"planner_stage": "configured-review"}, {"planner_session": ""}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.prepare(review_receipt={**self.receipt, **changes})

    def test_current_contract_approval_and_seal_are_required(self):
        with self.assertRaises(ValueError):
            self.prepare(contract_authenticated=1)
        for changes in ({"hash": "tamper"}, {"approval_status": "pending"}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.prepare(contract={**self.contract, **changes})

    def test_required_checks_cannot_silently_disappear_or_retarget(self):
        with self.assertRaisesRegex(ValueError, "drop check obligations"):
            self.prepare(required_checks=[])
        check = deepcopy(self.required[0])
        check["criterion_ids"] = ["unknown"]
        with self.assertRaisesRegex(ValueError, "unknown product criteria"):
            self.prepare(required_checks=[check])

    def test_previous_identity_is_not_the_candidate_or_initial_delegation(self):
        with self.assertRaisesRegex(ValueError, "previous plan identity"):
            self.prepare(previous_plan={**self.previous, "plan_hash": plan.plan_identity(self.proposal)})

    def test_later_revision_uses_evolving_predecessor_not_initial_declaration(self):
        self.previous = {"proposal": deepcopy(self.proposal),
                         "plan_hash": plan.plan_identity(self.proposal)}
        self.required += plan.retain(self.proposal["slices"][0]["checks"], "S2")
        self.proposal["done_slices"].append("S2")
        self.proposal["slices"] = [slice_row("S3")]
        self.publish()
        result = self.prepare(verified_done=["S1", "S2"])
        self.assertEqual(result["active"]["definition"]["id"], "S3")
        self.assertEqual([row["id"] for row in result["required_checks"]],
                         ["check-S1", "check-S2", "check-S3"])

    def test_reviewed_technical_replacement_retains_obligation_not_old_proof(self):
        replacement = deepcopy(self.required[0])
        replacement["method"] = "python -m unittest tests.test_replacement"
        self.proposal["slices"][0]["checks"].append(replacement)
        self.publish()
        result = self.prepare()
        old_obligation = result["required_checks"][0]
        self.assertEqual(old_obligation["id"], "check-S1")
        self.assertEqual(old_obligation["method"], replacement["method"])
        self.assertIs(old_obligation["verified_once"], False)

    def test_missing_artifact_cannot_be_reconstructed_from_model_report(self):
        (Path(self.temp.name) / self.candidate["path"]).unlink()
        with self.assertRaises(FileNotFoundError):
            self.prepare()

    def test_missing_review_fields_are_not_defaulted_to_safe(self):
        for key in ("accepted", "product_changes", "permission_changes",
                    "unresolved_product_decisions", "candidate_sha256", "stage", "role", "session"):
            with self.subTest(key=key):
                self.publish()
                envelope = artifacts.verify(self.temp.name, self.review)
                del envelope["report"][key]
                self.review = artifacts.persist(self.temp.name, envelope)
                self.receipt.update(artifact=self.review, output_hash=util.digest(envelope["report"]))
                with self.assertRaises(ValueError):
                    self.prepare()


if __name__ == "__main__":
    unittest.main()
