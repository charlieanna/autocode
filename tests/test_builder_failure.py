"""A Builder failure's classification has its own budget per milestone (#686).

Since #660 each Builder failure of unknown cause goes to the Investigator before it is retried.
Those calls counted against the run's three stuck investigations, so a multi-turn conversation
paused at PAUSED_BUILDER_CLASSIFICATION after about three Builder failures over its whole life,
each of them classified and acted on. Now a milestone may have as many failures classified as its
retry lane takes, and classifications and stuck investigations no longer spend each other's budget.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_builder_failure as builder_failure
import autocode_builder_policy as policy
import autocode_stuck_job as stuck


def run_state(workspace, *, contract="C1", milestone="M1", **settings):
    return {
        "version": 3,
        "workspace": str(workspace),
        "status": "RUNNING",
        "phase": "EXECUTING",
        "next_stage": "terra",
        "stages": [],
        "current_task": {"id": "T1", "milestone_id": milestone},
        "goal_contract": {"hash": contract, "revision": 1},
        "settings": settings,
    }


class BudgetTests(unittest.TestCase):
    def setUp(self):
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.root = Path(root.name)
        (self.root / "workspace").mkdir()
        snapshot = patch("autocode_builder_failure.source_scope.snapshot", return_value={"revision": "source-1"})
        snapshot.start()
        self.addCleanup(snapshot.stop)
        self.failures = 0

    def failure(self, state):
        """A new Builder failure: its own output file, so its own failure identity."""
        self.failures += 1
        output = self.root / f"builder-{self.failures}.json"
        output.write_text(f'{{"attempt": {self.failures}}}\n')
        return builder_failure.evidence(state, {"output": str(output), "source_revision": "source-1"})

    def classify(self, state):
        queued = builder_failure.queue(state, self.failure(state), "Unknown failure", enabled=True)
        if queued:
            state["stuck_investigations"][-1]["outcome"] = "classified"
            state.update(status="RUNNING", phase="EXECUTING", next_stage="terra")
            state.pop("stuck_investigation")
        return queued

    def test_a_milestone_classifies_as_many_failures_as_its_retry_lane_takes(self):
        for retries in (0, 1, 3):
            with self.subTest(ordinary_retries=retries):
                state = run_state(self.root / "workspace", builder_retry={"ordinary_retries": retries})
                limit = policy.classification_limit(state)
                self.assertEqual(retries + 2, limit, "the retries, the stronger attempt, then the pause")
                self.assertEqual([True] * limit, [self.classify(state) for _ in range(limit)])
                self.assertFalse(self.classify(state))
                self.assertEqual("PAUSED_BUILDER_CLASSIFICATION", state["status"])

    def test_a_run_saved_without_a_retry_policy_uses_the_default(self):
        self.assertEqual(policy.DEFAULTS["ordinary_retries"] + 2, policy.classification_limit({}))
        self.assertEqual(3, policy.classification_limit({"settings": {"builder_retry": {"ordinary_retries": "x"}}}))

    def test_the_next_milestone_and_a_later_turns_contract_start_their_own(self):
        state = run_state(self.root / "workspace")
        for _ in range(policy.classification_limit(state)):
            self.assertTrue(self.classify(state))
        state["current_task"] = {"id": "T2", "milestone_id": "M2"}
        self.assertTrue(self.classify(state), "the next milestone")
        # The live case: a build turn after a design turn installs a fresh contract.
        state.update(current_task={"id": "T1", "milestone_id": "M1"}, goal_contract={"hash": "C2", "revision": 1})
        self.assertTrue(self.classify(state), "the build turn's contract")
        self.assertEqual(5, len(state["stuck_investigations"]))

    def test_classifications_and_stuck_investigations_spend_separate_budgets(self):
        state = run_state(self.root / "workspace")
        state["stuck_investigations"] = [
            {"identity": f"astra_plan:PAUSED_{n}", "trigger": "non_convergence"} for n in range(stuck.MAX_CALLS)
        ]
        self.assertTrue(self.classify(state), "three stuck investigations leave classification its budget")
        self.assertTrue(self.classify(state))
        state["stuck_investigations"] = [
            {"identity": f"builder-failure:{n}", "trigger": "builder_failure", "milestone_key": policy.key(state)}
            for n in range(5)
        ]
        state.update(next_stage="sol")
        self.assertTrue(
            stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "The Tester keeps failing"),
            "five classifications leave the stuck investigation its budget",
        )

    def test_a_classification_saved_before_this_budget_counts_toward_neither(self):
        state = run_state(self.root / "workspace")
        legacy = [
            {"identity": f"builder-failure:{n}", "trigger": "builder_failure", "outcome": "classified"}
            for n in range(3)
        ]
        state["stuck_investigations"] = list(legacy)
        self.assertTrue(self.classify(state))
        state["stuck_investigations"] = list(legacy)
        state.update(next_stage="sol")
        self.assertTrue(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "The Tester keeps failing"))

    def test_the_same_failure_is_still_classified_once(self):
        state = run_state(self.root / "workspace")
        evidence = self.failure(state)
        self.assertTrue(builder_failure.queue(state, evidence, "Unknown failure", enabled=True))
        state.update(status="RUNNING", phase="EXECUTING", next_stage="terra")
        self.assertFalse(builder_failure.queue(state, evidence, "Unknown failure", enabled=True))
        self.assertEqual("PAUSED_BUILDER_CLASSIFICATION", state["status"])
        self.assertEqual(1, len(state["stuck_investigations"]))

    def test_turning_investigations_off_still_turns_classification_off(self):
        state = run_state(self.root / "workspace", stuck_investigation={"max_calls_per_run": 0})
        self.assertFalse(
            builder_failure.queue(state, self.failure(state), "Unknown failure", enabled=stuck.enabled(state))
        )
        self.assertEqual("PAUSED_BUILDER_CLASSIFICATION", state["status"])
        self.assertNotIn("stuck_investigations", state)


if __name__ == "__main__":
    unittest.main()
