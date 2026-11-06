"""A narrowed batch repair cannot silently authorize another component's tests."""

import copy
import unittest

import autocode_changed_code_rework as rework
import autocode_recovery_history as history


class ChangedCodeRepairScopeTests(unittest.TestCase):
    def fixture(self):
        state = {
            "status": "RUNNING",
            "iteration": 9,
            "current_task": {
                "id": "repair-store",
                "milestone_id": "D",
                "milestone_ids": ["D", "E"],
                "affected_paths": ["tests/store/test_store.py"],
                "requirements": ["Repair only the storage tests; do not modify other files."],
            },
            "goal_contract": {
                "hash": "plan-one",
                "revision": 3,
                "body": {
                    "milestones": [
                        {"id": "D", "affected_paths": ["app/store/", "tests/store/"]},
                        {"id": "E", "affected_paths": ["app/evaluate/", "tests/evaluate/"]},
                    ]
                },
            },
            "regression_proof": {
                "verdict": "PASS",
                "changed_code_evidence": {
                    "source_revision": "source-one",
                    "status": "UNVERIFIED",
                    "reason": "Comparison branches have no proving assertions",
                },
            },
        }
        record = {"stage": "sol", "role": "sol", "source_revision": "source-one", "output": "/run/tester.json"}
        rejected = [{"path": "app/evaluate/core.py", "line": 136, "reason": "Assert unequal values for <=."}]
        return state, record, rejected

    def test_narrowed_batch_assignment_returns_to_review_without_spending_builder_round(self):
        state, record, rejected = self.fixture()
        original_task = copy.deepcopy(state["current_task"])
        original_contract = copy.deepcopy(state["goal_contract"])
        self.assertTrue(rework.route(state, record, investigation_enabled=True, rejected=rejected))
        self.assertEqual("astra_review", state["next_stage"])
        self.assertEqual(9, state["iteration"])
        self.assertEqual(original_task, state["current_task"])
        self.assertEqual(original_contract, state["goal_contract"])
        event = state["regression_proofs"][-1]
        self.assertEqual("reviewer", event["action"])
        self.assertIn("E", event["scope_reason"])
        self.assertIn("app/evaluate/core.py:136", state["next_action"])
        self.assertIn("reassign", state["next_action"].lower())
        self.assertEqual("UNVERIFIED", state["regression_proof"]["changed_code_evidence"]["status"])
        # Reconciliation is idempotent; a combined checkpoint must still apply its review decision.
        self.assertTrue(rework.route(state, record, investigation_enabled=True, rejected=rejected))
        self.assertEqual(1, len(state["regression_proofs"]))
        self.assertFalse(rework.queued(state, record))

    def test_scope_review_does_not_consume_the_later_authorized_builder_allowance(self):
        state, record, rejected = self.fixture()
        rework.route(state, record, investigation_enabled=True, rejected=rejected)
        rework.route(
            state, {**record, "output": "/run/second-review.json"}, investigation_enabled=True, rejected=rejected
        )
        state["current_task"]["affected_paths"].append("tests/evaluate/")
        rework.route(
            state, {**record, "output": "/run/next-tester.json"}, investigation_enabled=True, rejected=rejected
        )
        self.assertEqual(
            ("terra", "builder", 10),
            (state["next_stage"], state["regression_proofs"][-1]["action"], state["iteration"]),
        )

    def test_test_only_assignment_for_the_same_component_keeps_direct_repair(self):
        state, record, rejected = self.fixture()
        rejected[0]["path"] = "app/store/core.py"
        rework.route(state, record, investigation_enabled=False, rejected=rejected)
        self.assertEqual("terra", state["next_stage"])
        self.assertEqual("builder", state["regression_proofs"][-1]["action"])

    def test_combined_checkpoint_can_apply_its_review_instead_of_repeating_the_tester(self):
        state, record, rejected = self.fixture()
        record.update(stage="astra_checkpoint", role="astra")
        rework.route(state, record, investigation_enabled=False, rejected=rejected)
        self.assertEqual("astra_review", state["next_stage"])
        self.assertFalse(rework.queued(state, record))

    def test_full_batch_and_legacy_assignments_keep_existing_routing(self):
        for variant in ("full_batch", "parent_path", "single", "no_paths", "unknown_owner", "prefix_sibling"):
            with self.subTest(variant=variant):
                state, record, rejected = self.fixture()
                if variant == "full_batch":
                    state["current_task"]["affected_paths"].append("tests/evaluate/test_comparisons.py")
                elif variant == "parent_path":
                    state["current_task"]["affected_paths"] = ["tests/"]
                elif variant == "single":
                    state["current_task"].pop("milestone_ids")
                elif variant == "no_paths":
                    state["current_task"].pop("affected_paths")
                elif variant == "unknown_owner":
                    rejected[0]["path"] = "another/core.py"
                else:
                    rejected[0]["path"] = "app/evaluate_extra/core.py"
                rework.route(state, record, investigation_enabled=False, rejected=rejected)
                self.assertEqual("terra", state["next_stage"])

    def test_recovery_view_names_reassignment_instead_of_a_builder_attempt(self):
        state, record, rejected = self.fixture()
        rework.route(state, record, investigation_enabled=False, rejected=rejected)
        rows = history.project(state)
        self.assertTrue(rows)
        self.assertIn("reassign", str(rows).lower())
        self.assertNotIn("Builder sent back for untested lines", str(rows))
