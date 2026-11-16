"""One bounded repair sees the whole current line review, without repeating checks."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import autocode_changed_code_evidence as evidence
import autocode_changed_code_review as review
import autocode_changed_code_review_application as application


class ChangedCodeReportDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.proof = self.root / ".autocode/runs/run/proof-unique/verification.json"
        self.proof.parent.mkdir(parents=True)
        self.proof.write_text("{}")
        self.assessment = evidence.assess(
            [{"path": "calc.py", "sha256": "a" * 64, "lines": [2, 3, 4]}],
            None,
            None,
            source_revision="source-one",
            proof_context="proof-one",
            texts={"calc.py": {"sha256": "a" * 64, "lines": {str(n): f"return value_{n}" for n in (2, 3, 4)}}},
        )
        self.state = {
            "workspace": str(self.root),
            "regression_proof": {
                "path": str(self.proof),
                "source_revision": "source-one",
                "changed_code_evidence": self.assessment,
            },
        }
        self.schema = self.root / "schema.json"
        self.record = {
            "stage": "tester",
            "role": "tester",
            "source_revision": "source-one",
            "schema": str(self.schema),
            "output": str(self.root / "tester.json"),
            "changed_files": [],
        }
        self.write_schema("tester")

    def write_schema(self, stage):
        fields = {review.FIELD: copy.deepcopy(review.SCHEMA)}
        self.schema.write_text(
            json.dumps(
                review.generation_schema(
                    {"properties": {"validation": {"properties": fields}} if stage == "review_checkpoint" else fields},
                    self.assessment,
                    stage,
                )
            )
        )

    def report(self, refs=None):
        return {
            "verdict": "FAIL",
            "checks": [],
            "evidence_hashes": {},
            review.FIELD: [
                {
                    "gap_id": row["handle"],
                    "source_line": row["source_line"],
                    "decision": "reject",
                    "reason_kind": "missing_test",
                    "reason": "Add a selected test for this branch.",
                    "evidence_refs": refs or ["proof:current"],
                }
                for row in self.assessment["obligations"]
            ],
        }

    def test_all_line_citation_and_matrix_defects_are_reported_together_for_both_testers(self):
        for stage, role in (("tester", "tester"), ("review_checkpoint", "task_planner")):
            self.write_schema(stage)
            record = {**self.record, "stage": stage, "role": role}
            for repaired in (False, True):
                with self.subTest(stage=stage, repaired=repaired):
                    value = self.report(["wrong-proof-one", "wrong-proof-two"])
                    value[review.FIELD] = value[review.FIELD][:1]
                    value[review.FIELD][0]["source_line"] = "return another_value"
                    before = copy.deepcopy((self.state, value, record))
                    with self.assertRaises(ValueError) as raised:
                        application.preflight(
                            self.state, value, {**record, "report_repaired": repaired}, passing_claim=False
                        )
                    message = str(raised.exception)
                    for expected in (
                        "return value_2",
                        "wrong-proof-one",
                        "wrong-proof-two",
                        "L2",
                        "L3",
                        "proof:current",
                    ):
                        self.assertIn(expected, message)
                    self.assertEqual(before, (self.state, value, record))

    def test_repaired_missing_test_failure_still_requires_every_offered_decision(self):
        for change in ({"report_repaired": True}, {"stage": "tester_report_repair", "original_stage": "tester"}):
            value = self.report()
            value[review.FIELD].pop()
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "L3"):
                application.require_tester_decisions(self.state, value, {**self.record, **change})

    def test_current_proof_alias_resolves_only_to_the_pinned_current_file(self):
        for ref in ("proof:current", str(self.proof), str(self.proof.relative_to(self.root))):
            with self.subTest(ref=ref):
                value = self.report([ref])
                application.preflight(self.state, value, self.record, passing_claim=False)
                self.assertNotIn("changed_code_decisions", value)
                pins = []

                def pin(refs):
                    pins.extend(refs)
                    return {ref: hashlib.sha256(ref.encode()).hexdigest() for ref in refs}

                application.accept(self.state, value, self.record, workspace=self.root, pin=pin)
                self.assertIn(str(self.proof), pins)
                self.assertEqual([[str(self.proof)]] * 3, [row["evidence"] for row in value["changed_code_decisions"]])
                self.assertIn(str(self.proof), value["evidence_hashes"])
                self.assertNotIn("proof:current", value["evidence_hashes"])

    def test_near_alias_bad_path_and_stale_source_are_never_corrected(self):
        for ref, source in (
            ("proof:currentx", "source-one"),
            ("proof:current", "older-source"),
            (str(self.proof).replace("proof-unique", "proof-uniq"), "source-one"),
        ):
            with self.subTest(ref=ref, source=source), self.assertRaises(ValueError):
                application.accept(
                    self.state,
                    self.report([ref]),
                    {**self.record, "source_revision": source},
                    workspace=self.root,
                    pin=lambda refs: {ref: "a" * 64 for ref in refs},
                )

    def test_alias_follows_core_proof_not_a_model_path_or_an_earlier_binding(self):
        later = self.root / "proof-later.json"
        later.write_text("{}")
        self.state["regression_proof"]["path"] = str(later)
        value = self.report()
        application.accept(self.state, value, self.record, pin=lambda refs: {ref: "a" * 64 for ref in refs})
        self.assertEqual([[str(later)]] * 3, [row["evidence"] for row in value["changed_code_decisions"]])
