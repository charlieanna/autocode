"""A read-only completion retry keeps exactly current independent evidence."""

import copy
import tempfile
import unittest
from pathlib import Path

import autocode_validation_recovery as recovery
from autocode_util import file_hash


class PermissionRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.evidence = Path(temp.name) / "validation.jsonl"
        self.evidence.write_text("executed tests\n")
        self.state = {
            "goal_contract": {"revision": 2, "hash": "approved"},
            "criteria_revision": 3,
            "current_task": {"id": "task"},
            "human_reviews": {"C1": "accepted"},
            "validation": {
                "verdict": "PASS",
                "source_revision": "source",
                "criteria_revision": 3,
                "contract_revision": 2,
                "contract_hash": "approved",
                "task_id": "task",
                "evidence_hashes": {str(self.evidence): file_hash(self.evidence)},
            },
        }
        self.record = {"stage": "astra_review", "source_revision": "source", "changed_files": []}

    def test_unchanged_completion_keeps_validation_and_human_reviews(self):
        before = copy.deepcopy(self.state)
        self.assertEqual("astra_review", recovery.permission_retry(self.state, self.record))
        self.assertEqual(before, self.state)

    def test_changed_source_contract_task_or_receipt_requires_fresh_validation(self):
        for field, wrong in (
            ("source_revision", "different"),
            ("contract_hash", "changed"),
            ("contract_revision", 5),
            ("criteria_revision", 9),
            ("task_id", "another"),
            ("evidence_hashes", {}),
        ):
            with self.subTest(field=field):
                state = copy.deepcopy(self.state)
                state["validation"][field] = wrong
                self.assertEqual("sol", recovery.permission_retry(state, self.record))
                self.assertNotIn("validation", state)
                self.assertEqual({}, state["human_reviews"])
        self.evidence.write_text("changed evidence\n")
        self.assertEqual("sol", recovery.permission_retry(self.state, self.record))

    def test_completion_without_validation_goes_to_validator_first(self):
        self.state.pop("validation")
        self.assertEqual("sol", recovery.permission_retry(self.state, self.record))

    def test_stale_completion_uses_the_workflows_verification_route(self):
        self.state.pop("validation")
        self.assertEqual(
            "astra_checkpoint", recovery.permission_retry(self.state, self.record, review_stage="astra_checkpoint")
        )

    def test_builder_interruption_archives_prior_validation_even_without_changes(self):
        self.record["stage"] = "terra"
        self.assertEqual("terra", recovery.permission_retry(self.state, self.record))
        self.assertNotIn("validation", self.state)
