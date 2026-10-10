"""Per-pause-class resume advice that never suggests a rejected flag (#301)."""

import unittest

import autocode_run_actions as run_actions


class NextCommandTests(unittest.TestCase):
    def test_grant_recovery_is_named_only_for_timeout_recovery(self):
        state = {"status": "PAUSED_TIMEOUT_RECOVERY"}
        self.assertIn("--grant-recovery", run_actions.next_command(state, None, "rd", "ws"))
        for pause in ("PAUSED_TIME_LIMIT", "PAUSED_MILESTONE_BUDGET", "PAUSED_REPEATED_FAILURE"):
            advice = run_actions.next_command({"status": pause}, None, "rd", "ws")
            self.assertNotIn("--grant-recovery", advice, pause)

    def test_budget_pauses_name_the_matching_bound(self):
        self.assertIn("--max-seconds", run_actions.next_command({"status": "PAUSED_TIME_LIMIT"}, None, "rd", "ws"))
        self.assertIn(
            "--max-iterations", run_actions.next_command({"status": "PAUSED_ITERATION_LIMIT"}, None, "rd", "ws")
        )
        self.assertIn(
            "--retry-failed-stage", run_actions.next_command({"status": "PAUSED_REPEATED_FAILURE"}, None, "rd", "ws")
        )


if __name__ == "__main__":
    unittest.main()
