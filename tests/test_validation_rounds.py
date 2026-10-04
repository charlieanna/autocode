"""Validation-only rounds that change nothing stop before another Validator (issue #300)."""
import unittest

import autocode_validation_rounds as rounds


class ValidationRounds(unittest.TestCase):
    def state(self, task="task-1", output="validator-1.json", passed=("C1",)):
        return {"goal_contract": {"hash": "contract"}, "current_task": {"id": task, "kind": "validate"},
                "validation": {"contract_hash": "contract", "source_revision": "rev", "output": output,
                               "criterion_results": [{"id": cid, "status": "PASS"} for cid in passed]},
                "stages": []}

    def blocking(self, *ids, **extra):
        return [{"id": ident, "source": "sol", "status": "open", "blocking": True, **extra} for ident in ids]

    def rounds(self, state, *attempts, blocking=None):
        blocking = blocking or self.blocking("F-1", not_rechecked_in="validator.json")
        results = []
        for task, output in attempts:
            state["current_task"]["id"], state["validation"]["output"] = task, output
            results.append(rounds.admit(state, blocking, "rev"))
        return results

    def test_third_unchanged_round_stops_and_names_each_finding_and_why_it_is_open(self):
        state = self.state()
        state["stages"] = [{"stage": "sol", "task_id": "task-2", "rejected": True,
                            "rejection_reason": "No independently executed Validator tool event matches receipt"}]
        blocking = self.blocking("F-1", not_rechecked_in="validator.json") + [
            {"id": "F-2", "source": "astra", "status": "open", "blocking": True,
             "pending_resolution": {"reason": "Finding scope lacks fully passing verification",
                                    "unverified_criteria": ["C2"]}}]
        first, second, third = self.rounds(state, ("task-1", "v0"), ("task-2", "v1"), ("task-3", "v2"),
                                           blocking=blocking)
        self.assertEqual((None, None), (first, second))
        self.assertIn("F-1, F-2", third["request"]["decision_needed"])
        why = third["request"]["impact"]
        self.assertIn("F-1 (Validator): the Validator's latest accepted report did not recheck it", why)
        self.assertIn("F-2 (Completion Owner): its resolution was not accepted "
                      "(Finding scope lacks fully passing verification; unverified C2)", why)
        self.assertIn("No independently executed Validator tool event matches receipt", why)
        self.assertEqual(third["reason"], third["request"]["discovered"])
        self.assertTrue(third["request"]["proposed_delta"].startswith("Answering closes no finding"))

    def test_another_attempt_at_an_admitted_round_is_not_a_new_round(self):
        state = self.state()
        self.assertEqual([None] * 4, self.rounds(state, ("task-1", "v0"), ("task-1", "v0"),
                                                 ("task-2", "v1"), ("task-2", "v1")))
        self.assertIsNotNone(self.rounds(state, ("task-3", "v2"))[0])

    def test_a_round_that_changed_findings_passes_or_acceptance_restarts_the_count(self):
        changes = {
            "closed finding": lambda state: {"blocking": self.blocking("F-2")},
            "new passing criterion": lambda state: state["validation"]["criterion_results"].append(
                {"id": "C2", "status": "PASS"}),
            "accepted milestone": lambda state: state.update(milestone_progress={
                "contract:M1": {"accepted": True, "contract_hash": "contract"}}),
        }
        for name, change in changes.items():
            with self.subTest(name):
                state = self.state()
                self.assertEqual([None, None], self.rounds(state, ("task-1", "v0"), ("task-2", "v1")))
                state["current_task"]["id"], state["validation"]["output"] = "task-3", "v2"
                override = change(state) or {}
                blocking = override.get("blocking", self.blocking("F-1"))
                self.assertIsNone(rounds.admit(state, blocking, "rev"))
                state["current_task"]["id"], state["validation"]["output"] = "task-4", "v3"
                self.assertIsNone(rounds.admit(state, blocking, "rev"))
                state["current_task"]["id"], state["validation"]["output"] = "task-5", "v4"
                self.assertIsNotNone(rounds.admit(state, blocking, "rev"))

    def test_first_validation_of_new_source_or_without_blockers_is_not_validation_only(self):
        state = self.state()
        self.assertIsNone(rounds.admit(state, [], "rev"))
        self.assertIsNone(rounds.admit(state, self.blocking("F-1"), "new-rev"))
        state["validation"]["contract_hash"] = "old-contract"
        self.assertIsNone(rounds.admit(state, self.blocking("F-1"), "rev"))
        self.assertNotIn(rounds.KEY, state)


if __name__ == "__main__":
    unittest.main()
