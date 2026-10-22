"""Per-pause-class resume advice that never suggests a rejected flag (#301)."""

import unittest

import autocode_run_actions as run_actions
import autocode_run_view as run_view


class NextCommandTests(unittest.TestCase):
    def test_grant_recovery_is_named_only_for_timeout_recovery(self):
        state = {"status": "PAUSED_TIMEOUT_RECOVERY"}
        self.assertIn("--grant-recovery", run_actions.next_command(state, None, "rd", "ws"))
        for pause in ("PAUSED_TIME_LIMIT", "PAUSED_MILESTONE_BUDGET", "PAUSED_REPEATED_FAILURE"):
            advice = run_actions.next_command({"status": pause}, None, "rd", "ws")
            self.assertNotIn("--grant-recovery", advice, pause)

    def test_budget_pauses_name_the_matching_bound(self):
        self.assertIn("--max-seconds", run_actions.next_command({"status": "PAUSED_TIME_LIMIT"}, None, "rd", "ws"))
        cost_cap = run_actions.next_command({"status": "PAUSED_COST_LIMIT"}, None, "rd", "ws")
        self.assertIn("--resume-paused --max-cost-usd N", cost_cap)
        self.assertNotIn("--grant-recovery", cost_cap)
        self.assertIn(
            "--max-iterations", run_actions.next_command({"status": "PAUSED_ITERATION_LIMIT"}, None, "rd", "ws")
        )
        self.assertIn(
            "--retry-failed-stage", run_actions.next_command({"status": "PAUSED_REPEATED_FAILURE"}, None, "rd", "ws")
        )

    def test_a_pending_repair_or_check_names_a_resume_the_cli_accepts(self):
        # Live bugfix run (#1897): while a report repair was still pending the view named
        # --retry-failed-stage, which authorize_failure_retry always refuses in that state, so a
        # client that takes only next_command had no action it could run.
        for key in ("pending_report_repair", "active_runner_check"):
            with self.subTest(key=key):
                state = {
                    "status": "PAUSED_REPEATED_FAILURE",
                    key: {"attempts": 2},
                    "run_dir": "rd",
                    "workspace": "ws",
                }
                advice = run_actions.next_command(state, None, "rd", "ws")
                self.assertNotIn("--retry-failed-stage", advice)
                self.assertIn("--resume-paused", advice)
                named = run_view.action(state, run_dir="rd", workspace="ws")["next_command"]
                self.assertNotIn("--retry-failed-stage", named)
                self.assertIn("--resume-paused", named)

    def test_the_testers_pause_names_feedback_not_a_resume_that_repeats_it(self):
        advice = run_actions.next_command({"status": "PAUSED_ACCEPTANCE_TESTS"}, None, "rd", "ws")
        self.assertIn("--feedback", advice)
        self.assertNotIn("--resume-paused", advice)


if __name__ == "__main__":
    unittest.main()
