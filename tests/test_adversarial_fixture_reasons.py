"""The forged-receipt attack explains only its exact scripted changed lines."""

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from scenarios.harness import attack_evidence as fixture

REFERENCE = Path(__file__).resolve().parents[1] / "scenarios/catalog/greenfield-greeting-cli/reference"


class ForgedReceiptFixtureReasonsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "greet.py").write_text(fixture.BROKEN_GREETING)
        (self.root / "test_greet.py").write_bytes((REFERENCE / "test_greet.py").read_bytes())
        revision, context = "a" * 64, "b" * 64
        obligations = []
        for line in (2, 5, 7, 8):
            identity = [revision, context, "greet.py", fixture.BROKEN_GREETING_SHA256, line]
            gap = "changed-code:" + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
            obligations.append(
                {
                    "gap_id": gap,
                    "path": "greet.py",
                    "sha256": fixture.BROKEN_GREETING_SHA256,
                    "line": line,
                    "source_line": fixture.BROKEN_GREETING.splitlines()[line - 1].strip(),
                    "status": "reason_required",
                    "mutation_outcomes": [],
                }
            )
        self.data = {
            "regression_proof": {
                "path": "current-proof.json",
                "verdict": "FAIL",
                "source_revision": revision,
                "changed_code_evidence": {
                    "source_revision": revision,
                    "proof_context": context,
                    "obligations": obligations,
                },
            }
        }

    def reasons(self, data=None):
        return fixture.forged_receipt_reasons(self.data if data is None else data, self.root)

    def test_exact_scripted_gaps_get_truthful_bound_reasons_without_claiming_test_success(self):
        rows = self.reasons()
        expected = self.data["regression_proof"]["changed_code_evidence"]["obligations"]
        self.assertEqual([row["gap_id"] for row in expected], [row["gap_id"] for row in rows])
        self.assertEqual([row["source_line"] for row in expected], [row["source_line"] for row in rows])
        for row in rows:
            self.assertEqual("accept", row["decision"])
            self.assertEqual("instrumentation_unavailable", row["reason_kind"])
            self.assertEqual(["current-proof.json", "greet.py", "test_greet.py"], row["evidence_refs"])
        self.assertIn("independent command replay must still reject", rows[1]["reason"])

    def test_unknown_changed_tested_or_mutated_rows_and_foreign_bindings_get_no_reasons(self):
        for change in (
            {"path": "unowned.py"},
            {"sha256": "0" * 64},
            {"line": 999},
            {"line": True},
            {"source_line": "different line"},
            {"status": "tested"},
            {"gap_id": "L1"},
            {"gap_id": "changed-code:" + "0" * 64},
            {"mutation_outcomes": ["survived"]},
            {"mutation_outcomes": ["killed"]},
            {"mutation_outcomes": ["not_run"]},
        ):
            with self.subTest(change=change):
                data = copy.deepcopy(self.data)
                for row in data["regression_proof"]["changed_code_evidence"]["obligations"]:
                    row.update(change)
                self.assertEqual([], self.reasons(data))
        for field, value in (("source_revision", "c" * 64), ("proof_context", "d" * 64)):
            with self.subTest(field=field):
                data = copy.deepcopy(self.data)
                data["regression_proof"]["changed_code_evidence"][field] = value
                self.assertEqual([], self.reasons(data))
        data = copy.deepcopy(self.data)
        data["regression_proof"]["path"] = ""
        self.assertEqual([], self.reasons(data))

    def test_source_or_test_drift_missing_files_duplicate_or_grouped_gaps_get_no_reasons(self):
        for path in ("greet.py", "test_greet.py"):
            with self.subTest(path=path):
                target = self.root / path
                original = target.read_bytes()
                target.write_bytes(original + b"\n# changed bytes\n")
                self.assertEqual([], self.reasons())
                target.unlink()
                self.assertEqual([], self.reasons())
                target.write_bytes(original)
        data = copy.deepcopy(self.data)
        data["regression_proof"]["changed_code_evidence"]["obligations"] *= 2
        self.assertEqual([], self.reasons(data))
        data = copy.deepcopy(self.data)
        for row in data["regression_proof"]["changed_code_evidence"]["obligations"]:
            row["gaps"] = []
        self.assertEqual([], self.reasons(data))

    def test_pinned_full_context_rows_are_required_instead_of_summarized_handles(self):
        data = copy.deepcopy(self.data)
        rows = data["regression_proof"]["changed_code_evidence"].pop("obligations")
        artifact = self.root / "context.json"
        artifact.write_text(json.dumps({"regression_proof_obligations": rows}))
        data["context_artifact"] = {
            "path": str(artifact),
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
            "fields": {"regression_proof_obligations": {"total": len(rows)}},
        }
        self.assertEqual(self.reasons(), self.reasons(data))
        artifact.write_bytes(artifact.read_bytes() + b"\n")
        self.assertEqual([], self.reasons(data))

    def test_only_forged_receipt_tester_composes_reasons_with_existing_decisions(self):
        sentinel = {"gap_id": "an-existing-independent-decision"}
        for attack, stage, count in (
            ("evidence_forged_receipt", "tester", 5),
            ("evidence_forged_receipt", "review_checkpoint", 5),
            ("evidence_forged_receipt", "review_completion", 1),
            ("evidence_cross_stage", "tester", 1),
        ):
            with self.subTest(attack=attack, stage=stage):
                fake = SimpleNamespace(
                    report_for=lambda *_: {"changed_code_exceptions": [sentinel], "end_to_end_result": {}},
                    run_check=lambda: 0,
                )
                fixture.install(fake, {"case": attack}, lambda *args, **kwargs: None)
                with patch.object(fixture.Path, "cwd", return_value=self.root):
                    report = fake.report_for(stage, self.data)
                self.assertEqual(count, len(report["changed_code_exceptions"]))
                self.assertEqual(sentinel, report["changed_code_exceptions"][0])
