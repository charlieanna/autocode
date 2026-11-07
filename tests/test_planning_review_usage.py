"""Inherited Plan Reviewer receipts count once, including old saved false pauses."""

import copy
import unittest

import autocode_planning_review_usage as usage
import autocode_run_view as run_view
from units import autoplanner


def inherited(*, activated=False, limit=3, origin="runner_default"):
    first = {"astra_calls": 1, "review_charges": ["review-A"], "turn_event_id": "turn-1"}
    last = {"astra_calls": 1, "review_charges": ["review-B"], "turn_event_id": "turn-1"}
    planning = {
        "astra_calls": 1,
        "review_charges": ["review-B"],
        "turn_event_id": "turn-1",
        "inherited_review": {"cycle": 1, "astra_calls": 1, "reports": ["astra_challenge", "glm_revise"]},
        "review_call_limit": limit,
        "review_call_limit_origin": origin,
        "reports": {},
    }
    if activated:
        planning.update(astra_calls=3, review_depth_reduced=True, review_calls_before_policy=2)
    return {
        "status": "PAUSED_PLANNING_BUDGET" if activated else "RUNNING",
        "next_stage": "astra_finalize",
        "workspace": "/tmp/project",
        "run_dir": "/tmp/run",
        "workflow": {"kind": "build", "clarity": "clear", "brief_size": "small"},
        "settings": {"joint_planning": True},
        "planning": planning,
        "planning_history": [first, last],
        "stages": [
            {"stage": "astra_challenge", "planning_review_charge": "review-A", "exit_code": 0},
            {"stage": "astra_challenge", "planning_review_charge": "review-B", "exit_code": 0},
        ],
    }


class InheritedReviewUsageTests(unittest.TestCase):
    def test_activation_counts_two_real_reviews_not_three_copies(self):
        state = inherited()
        history = copy.deepcopy(state["planning_history"])
        autoplanner.apply_review_depth_policy(state)
        self.assertEqual(2, state["planning"]["astra_calls"])
        self.assertEqual(1, state["planning"]["review_calls_before_policy"])
        self.assertEqual(history, state["planning_history"])

    def test_saved_false_pause_names_ordinary_resume_without_mutating_state(self):
        state = inherited(activated=True)
        before = copy.deepcopy(state)
        action = run_view.action(state)
        self.assertEqual("resume", action["needs"]["kind"])
        self.assertEqual("--resume-paused", action["needs"]["action"])
        self.assertNotIn("command_inputs", action["needs"])
        self.assertNotIn("--planning-review-call-limit", str(action["next_command"]))
        self.assertEqual(before, state)

    def test_saved_counter_correction_is_idempotent_and_preserves_history_and_limits(self):
        for origin in ("runner_default", "user_explicit"):
            with self.subTest(origin=origin):
                state = inherited(activated=True, origin=origin)
                before = copy.deepcopy(state)
                self.assertTrue(usage.can_resume(state))
                self.assertTrue(usage.reconcile(state, at="now"))
                self.assertEqual(2, state["planning"]["astra_calls"])
                self.assertEqual(1, state["planning"]["review_calls_before_policy"])
                self.assertEqual(before["planning_history"], state["planning_history"])
                self.assertEqual(before["stages"], state["stages"])
                self.assertEqual(before["settings"], state["settings"])
                self.assertEqual(before["planning"]["inherited_review"], state["planning"]["inherited_review"])
                self.assertEqual(
                    (3, origin), (state["planning"]["review_call_limit"], state["planning"]["review_call_limit_origin"])
                )
                corrected = copy.deepcopy(state)
                self.assertFalse(usage.reconcile(state, at="later"))
                self.assertEqual(corrected, state)

    def test_exact_owner_bound_still_holds_when_unique_reviews_exhaust_it(self):
        state = inherited(activated=True, limit=2, origin="user_explicit")
        before = copy.deepcopy(state)
        action = run_view.action(state)
        self.assertEqual("planning_budget", action["needs"]["kind"])
        row = action["needs"]["command_inputs"][0]
        self.assertEqual((2, 2, 3), (row["current"], row["used"], row["minimum"]))
        self.assertFalse(usage.can_resume(state))
        self.assertEqual(before, state)

    def test_a_new_review_receipt_is_never_deducted_again(self):
        state = inherited(activated=True)
        usage.reconcile(state, at="now")
        state["planning"]["review_charges"].append("review-C")
        state["planning"]["astra_calls"] += 1
        state["stages"].append({"stage": "astra_finalize", "planning_review_charge": "review-C", "exit_code": 0})
        self.assertEqual(3, usage.effective_calls(state))
        self.assertFalse(usage.can_resume(state))
        self.assertFalse(usage.reconcile(state, at="later"))

    def test_unproven_legacy_or_unrelated_usage_cannot_be_discounted(self):
        for case in (
            "no_inheritance",
            "unknown_cycle",
            "wrong_turn",
            "missing_turn",
            "different_carried_count",
            "missing_charge",
            "duplicate_receipt",
            "unknown_attempt",
            "failed_attempt",
            "report_repair",
            "refunded",
            "untracked_history",
            "untracked_current",
            "unknown_activation",
            "busy",
        ):
            with self.subTest(case=case):
                state = inherited(activated=True)
                planning = state["planning"]
                if case == "no_inheritance":
                    planning.pop("inherited_review")
                elif case == "unknown_cycle":
                    planning["inherited_review"]["cycle"] = 20
                elif case == "wrong_turn":
                    state["planning_history"][1]["turn_event_id"] = "another-turn"
                elif case == "missing_turn":
                    planning.pop("turn_event_id")
                elif case == "different_carried_count":
                    planning["inherited_review"]["astra_calls"] = 2
                elif case == "missing_charge":
                    planning["review_charges"] = []
                elif case == "duplicate_receipt":
                    state["stages"].append(copy.deepcopy(state["stages"][1]))
                elif case == "unknown_attempt":
                    state["stages"].pop()
                elif case == "failed_attempt":
                    state["stages"][1]["exit_code"] = 1
                elif case == "report_repair":
                    state["stages"][1]["report_only"] = True
                elif case == "refunded":
                    state["stages"][1]["planning_review_refunded"] = True
                elif case == "untracked_history":
                    state["planning_history"][0]["astra_calls"] += 1
                elif case == "untracked_current":
                    planning["astra_calls"] += 1
                elif case == "unknown_activation":
                    planning["review_calls_before_policy"] = 1
                elif case == "busy":
                    state["active_stage"] = {"stage": "astra_finalize"}
                before = copy.deepcopy(state)
                self.assertFalse(usage.can_resume(state))
                if case != "busy":
                    self.assertIsNone(usage.correction(state))
                    self.assertFalse(usage.reconcile(state, at="now"))
                self.assertEqual(before, state)

    def test_an_earlier_turn_does_not_join_the_current_turn_total(self):
        state = inherited()
        state["planning_history"].append({"astra_calls": 99, "turn_event_id": "earlier"})
        autoplanner.apply_review_depth_policy(state)
        self.assertEqual(2, state["planning"]["astra_calls"])

    def test_malformed_legacy_planning_cannot_offer_a_correction(self):
        for saved in ([], "legacy", 3):
            with self.subTest(saved=saved):
                state = inherited(activated=True)
                state["planning"] = saved
                before = copy.deepcopy(state)
                self.assertIsNone(usage.correction(state))
                self.assertIsNone(usage.effective_calls(state))
                self.assertFalse(usage.can_resume(state))
                self.assertFalse(usage.reconcile(state, at="now"))
                self.assertEqual(before, state)
