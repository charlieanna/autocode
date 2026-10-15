"""Which milestones the current task covers, and whether the saved validation still proves it."""

import tempfile
import unittest
from pathlib import Path

import autocode_milestone_scope as milestone_scope
import autocode_util as util

M1 = {"id": "M1", "objective": "Parse", "acceptance_criteria": ["AC1"]}
M2 = {"id": "M2", "objective": "Store", "acceptance_criteria": ["AC1", "AC2"]}


def run(**fields):
    state = {"goal_contract": {"hash": "h", "revision": 2, "body": {"milestones": [M1, M2]}}}
    state.update(fields)
    return state


class MilestoneScopeTests(unittest.TestCase):
    def test_a_task_names_its_milestone_or_a_batch_of_them(self):
        state = run(current_task={"id": "t1", "milestone_id": "M2"})
        self.assertEqual("h:M2", milestone_scope.key(state))
        self.assertEqual(M2, milestone_scope.scope(state))
        batch = {"id": "t2", "milestone_ids": ["M2", "M1"]}
        digest = util.digest(["M1", "M2"])[:16]
        self.assertEqual(f"h:batch:{digest}", milestone_scope.key(state, batch))
        self.assertEqual(
            {
                "id": f"batch:{digest}",
                "milestone_ids": ["M2", "M1"],
                "members": [M1, M2],
                "objective": "Parse; Store",
                "acceptance_criteria": ["AC1", "AC2"],
            },
            milestone_scope.scope(state, batch),
        )

    def test_a_task_from_before_milestones_keeps_its_saved_scope(self):
        task = {"id": "t1", "milestone_id": "legacy", "objective": "Old", "acceptance_criteria": ["AC9"]}
        state = run(current_task=task, milestone_progress={"h:legacy": {"acceptance_criteria": ["AC1"]}})
        self.assertEqual(
            {"id": "legacy", "objective": "Old", "acceptance_criteria": ["AC1"]}, milestone_scope.scope(state)
        )

    def test_a_validation_is_fresh_only_for_the_same_contract_task_source_and_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "receipt.json"
            evidence.write_text("{}")
            validation = {
                "reviewer_role": "sol",
                "evidence_hashes": {str(evidence): util.file_hash(evidence)},
                "contract_hash": "h",
                "contract_revision": 2,
                "criteria_revision": "c",
                "task_id": "t1",
                "source_revision": "r1",
            }
            state = run(current_task={"id": "t1"}, criteria_revision="c", validation=validation)
            self.assertTrue(milestone_scope.fresh_validation(state, {"revision": "r1"}))
            self.assertFalse(milestone_scope.fresh_validation(state, {"revision": "r2"}))
            evidence.write_text('{"changed": true}')
            self.assertFalse(milestone_scope.fresh_validation(state, {"revision": "r1"}))

    def test_a_null_contract_task_or_validation_reads_as_absent(self):
        # RunState declares goal_contract, current_task and validation as dict | None (#695).
        state = {"goal_contract": None, "current_task": None, "validation": None}
        self.assertEqual(":", milestone_scope.key(state))
        self.assertEqual({"id": "", "objective": "", "acceptance_criteria": []}, milestone_scope.scope(state))
        self.assertFalse(milestone_scope.fresh_validation(state, {"revision": "r1"}))


if __name__ == "__main__":
    unittest.main()
