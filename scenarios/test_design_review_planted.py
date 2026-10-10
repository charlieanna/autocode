"""Focused controls for the planted design-review oracle."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import catalog
from harness.project import materialize


class DesignReviewPlantedTests(unittest.TestCase):
    def setUp(self):
        self.scenario = catalog.load("design-review-planted")
        self.check = self.scenario.oracle()
        self.area_of = self.check.__globals__["area_of"]
        self.report = json.loads((self.scenario.reference / "review/design-review.json").read_text())

    def failed_checks(self, report):
        with tempfile.TemporaryDirectory() as root:
            project = materialize(self.scenario.seed, Path(root) / "project", self.scenario.reference)
            (project / "review/design-review.json").write_text(json.dumps(report))
            return {check.name for check in self.check(project, self.scenario) if not check.ok}

    def test_migration_label_disambiguates_duplicate_and_sequence_evidence(self):
        concern = {
            "area": "Migration consistency and rollback",
            "summary": "Retry duplicates need reconciliation and a cutover checkpoint.",
            "evidence": "Dual-write has no rollback; sequence state must transfer.",
        }
        self.assertEqual("migration", self.area_of(concern))
        self.report["concerns"][2].update(concern)
        self.assertEqual(set(), self.failed_checks(self.report))

    def test_supported_labels_disambiguate_each_category(self):
        for label, expected in (
            ("Per-domain ordering", "ordering"),
            ("Duplicate side effects", "idempotency"),
            ("Migration consistency and rollback", "migration"),
        ):
            with self.subTest(label=label):
                self.assertEqual(
                    expected,
                    self.area_of(
                        {"area": label, "evidence": "Key ordering, duplicate processing, and rollback are missing."}
                    ),
                )

    def test_label_alone_does_not_supply_evidence(self):
        for label in ("ordering by partition key", "duplicate processing", "migration rollback"):
            with self.subTest(label=label):
                self.assertIsNone(self.area_of({"area": label, "summary": "Needs more thought."}))

    def test_unsupported_label_uses_body_fallback(self):
        self.assertEqual(
            "idempotency",
            self.area_of({"area": "migration", "evidence": "Retries charge twice without deduplication."}),
        )
        self.assertEqual("migration", self.area_of({"area": "ordering", "evidence": "Dual-write has no rollback."}))

    def test_absent_unknown_and_ambiguous_labels_keep_first_match_fallback(self):
        for label in (None, "operations", "migration and duplicate processing"):
            with self.subTest(label=label):
                self.assertEqual(
                    "idempotency",
                    self.area_of({"area": label, "summary": "Duplicate processing and rollback need attention."}),
                )

    def test_one_vague_multi_keyword_concern_cannot_cover_all_gaps(self):
        self.report["concerns"] = [
            {"id": "F1", "severity": "blocking", "summary": "Partition key ordering, duplicate processing, rollback."}
        ]
        self.assertEqual(
            {"raises_idempotency_as_blocking", "raises_migration_as_blocking", "grounds_ordering_in_existing_contract"},
            self.failed_checks(self.report),
        )

    def test_each_planted_gap_remains_required_as_blocking(self):
        for index, area in enumerate(("ordering", "idempotency", "migration")):
            with self.subTest(area=area):
                report = json.loads(json.dumps(self.report))
                report["concerns"][index]["severity"] = "advisory"
                expected = {f"raises_{area}_as_blocking"}
                if area == "ordering":
                    expected.add("grounds_ordering_in_existing_contract")
                self.assertEqual(expected, self.failed_checks(report))

    def test_grounded_ordering_blocker_does_not_need_a_user_question(self):
        self.report["questions"] = []
        self.assertEqual(set(), self.failed_checks(self.report))

    def test_a_generic_ordering_complaint_is_not_grounded_in_the_existing_contract(self):
        self.report["concerns"][0].update(
            summary="Partitioning by kind breaks sequence order.", evidence="Producer uses kind as the partition key."
        )
        self.assertIn("grounds_ordering_in_existing_contract", self.failed_checks(self.report))

    def test_an_ordering_question_cannot_replace_the_blocking_concern(self):
        self.report["concerns"] = self.report["concerns"][1:]
        self.report["questions"] = [
            {
                "id": "Q1",
                "question": "How should per-domain sequence be preserved?",
                "options": ["domain partition key", "ordered dispatch"],
            }
        ]
        self.assertIn("raises_ordering_as_blocking", self.failed_checks(self.report))
        self.assertIn("grounds_ordering_in_existing_contract", self.failed_checks(self.report))

    def test_seed_reference_and_all_broken_controls(self):
        variants = [("seed", None, False), ("reference", self.scenario.reference, True)]
        variants += [(path.name, path, False) for path in self.scenario.broken]
        for name, overlay, expected in variants:
            with self.subTest(variant=name), tempfile.TemporaryDirectory() as root:
                project = materialize(self.scenario.seed, Path(root) / "project", *([overlay] if overlay else []))
                checks = self.check(project, self.scenario)
                self.assertEqual(
                    expected, all(check.ok for check in checks), [check.name for check in checks if not check.ok]
                )


if __name__ == "__main__":
    unittest.main()
