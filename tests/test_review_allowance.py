"""The plan-review allowance is spent by reviews that return a report, not by attempts that fail."""
import unittest

import autocode_support as s
from units import autoplanner as planning


def new_state(limit=2):
    return {"settings": {"planning_review_call_limit": limit}, "stages": [],
            "planning": {"astra_calls": 0, "reports": {}, "final_token": None}}


def attempt(state, stage, **outcome):
    """Admit one review call, then record how it ended (as the runner does when it archives the attempt)."""
    record = {"stage": stage, "output": f"{stage}.json"}
    planning.charge(state, stage, record, None)
    record.update(outcome)
    state["stages"].append(record)
    return record


class ReviewAllowanceTests(unittest.TestCase):
    def test_the_two_reviews_of_a_normal_cycle_fit(self):
        state = new_state()
        attempt(state, "astra_challenge", exit_code=0)
        attempt(state, "astra_finalize", exit_code=0)
        self.assertEqual(2, state["planning"]["astra_calls"])

    def test_a_timed_out_review_is_given_back(self):
        state = new_state()
        attempt(state, "astra_challenge", exit_code=-15, timed_out=True)
        attempt(state, "astra_challenge", exit_code=0)
        attempt(state, "astra_finalize", exit_code=0)
        self.assertEqual(2, state["planning"]["astra_calls"])
        self.assertTrue(state["stages"][0]["planning_review_refunded"])

    def test_a_failed_provider_is_given_back(self):
        state = new_state()
        attempt(state, "astra_challenge", exit_code=1)
        attempt(state, "astra_challenge", exit_code=0)
        self.assertEqual(1, state["planning"]["astra_calls"])

    def test_a_refund_is_given_once(self):
        state = new_state()
        attempt(state, "astra_challenge", exit_code=-15, timed_out=True)
        for _ in range(3):
            planning.refund_unreported(state, state["planning"])
        self.assertEqual(0, state["planning"]["astra_calls"])

    def test_a_review_that_returned_a_report_still_counts_even_if_rejected(self):
        state = new_state()
        attempt(state, "astra_challenge", exit_code=0, rejected=True)
        attempt(state, "astra_challenge", exit_code=0)
        with self.assertRaises(s.Paused) as raised:
            attempt(state, "astra_finalize", exit_code=0)
        self.assertEqual("PAUSED_PLANNING_BUDGET", raised.exception.status)

    def test_the_allowance_still_stops_a_third_returned_review(self):
        state = new_state()
        attempt(state, "astra_challenge", exit_code=0)
        attempt(state, "astra_finalize", exit_code=0)
        with self.assertRaises(s.Paused) as raised:
            attempt(state, "astra_finalize", exit_code=0)
        self.assertEqual("PAUSED_PLANNING_BUDGET", raised.exception.status)

    def test_calls_charged_before_this_change_are_never_refunded(self):
        state = new_state()
        state["planning"]["astra_calls"] = 2
        state["stages"] = [{"stage": "astra_challenge", "exit_code": -15, "timed_out": True}]
        with self.assertRaises(s.Paused) as raised:
            attempt(state, "astra_finalize", exit_code=0)
        self.assertEqual("PAUSED_PLANNING_BUDGET", raised.exception.status)
        self.assertEqual(2, state["planning"]["astra_calls"])

    def test_a_new_planning_cycle_does_not_refund_the_old_ones(self):
        state = new_state()
        attempt(state, "astra_challenge", exit_code=-15, timed_out=True)
        state["planning"] = {"astra_calls": 0, "reports": {}, "final_token": None}
        planning.refund_unreported(state, state["planning"])
        self.assertEqual(0, state["planning"]["astra_calls"])


if __name__ == "__main__":
    unittest.main()
