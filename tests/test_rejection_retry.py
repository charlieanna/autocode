"""AutoCode's own bounded retry of a stage whose answers keep being rejected (autocode_rejection_retry)."""

import unittest

import autocode_failures as failures
import autocode_rejection_retry as retry
import autocode_run_status as run_status

RETRIED = [{"identity": "revise_plan:PAUSED_INVALID_OUTPUT", "outcome": "retried"}]


def rejected_run(stage="revise_plan", errors=("A",)):
    state = {"stages": [], "next_stage": stage}
    for number, error in enumerate(errors, 1):
        row = {"stage": stage, "source_revision": "rev-a", "iteration": 1, "output": f"/run/{number}.json"}
        state["stages"].append(row)
        failures.record(state, row, ValueError(error), f"t{number}")
        row.update(rejected=True, rejection_reason=error)
    run_status.pause(state, retry.STATUS, "rejected", paused_at="t")
    return state


class AdmitTests(unittest.TestCase):
    def test_a_retry_runs_the_stage_in_its_phase_and_archives_the_spent_repair(self):
        state = rejected_run(errors=("A", "B"))
        state["pending_report_repair"] = {"attempts": 2}
        self.assertTrue(retry.admit(state, "revise_plan", retry.STATUS, RETRIED, at="now"))
        self.assertEqual(("RUNNING", "PLANNING", "revise_plan"), (state["status"], state["phase"], state["next_stage"]))
        for key in ("stop_reason", "paused_at", "pending_report_repair"):
            self.assertNotIn(key, state)
        self.assertEqual({"attempts": 2}, state["report_repair_archive"][-1]["repair"])
        (entry,) = state["failure_history"].values()
        self.assertEqual([{"at": "now", "attempt": 3, "limit": retry.LIMIT}], entry["automatic_retries"])

    def test_no_retry_without_an_investigation_that_retried_or_at_the_limit(self):
        cases = {
            "not investigated": (rejected_run(), retry.STATUS, []),
            "investigator asked the person": (rejected_run(), retry.STATUS, [{"outcome": "paused"}]),
            "another pause": (rejected_run(), "PAUSED_REPEATED_FAILURE", RETRIED),
            "attempt in flight": ({**rejected_run(), "active_stage": "revise_plan"}, retry.STATUS, RETRIED),
            "at the limit": (rejected_run(errors="ABAB"), retry.STATUS, RETRIED),
        }
        for name, (state, status, seen) in cases.items():
            with self.subTest(name):
                self.assertFalse(retry.admit(state, "revise_plan", status, seen, at="now"))
                self.assertEqual(retry.STATUS, state["status"], "the pause stays")

    def test_the_second_look_comes_once_at_the_limit(self):
        self.assertFalse(retry.second_look(rejected_run(errors="ABA"), "revise_plan", retry.STATUS, RETRIED))
        self.assertTrue(retry.second_look(rejected_run(errors="ABAB"), "revise_plan", retry.STATUS, RETRIED))
        self.assertFalse(retry.second_look(rejected_run(errors="ABAB"), "revise_plan", retry.STATUS, RETRIED * 2))

    def test_the_stop_reason_offers_no_retry_only_past_the_limit(self):
        reason = "Completed revise_plan output was rejected (B); attempt archived. " + failures.REJECTED_RESUME
        below = rejected_run(errors="ABA")
        self.assertEqual(reason, retry.stop_reason(below, "revise_plan", retry.STATUS, RETRIED, reason))
        same = rejected_run(errors="AAAA")
        text = retry.stop_reason(same, "revise_plan", retry.STATUS, RETRIED, reason)
        self.assertNotIn("Resume explicitly", text)
        self.assertIn("4 times at this source and asked the Investigator once", text)
        self.assertNotIn("The errors were", text, "one error is already in the reason")


if __name__ == "__main__":
    unittest.main()
