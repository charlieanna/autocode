"""Per-pause-class resume advice that never suggests a rejected flag (#301)."""

import unittest

import autocode_recovery_limits as recovery_limits
import autocode_run_actions as run_actions
import autocode_run_view as run_view
import autocode_stage_recovery as stage_recovery


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

    def test_a_retry_the_cli_refuses_is_not_named_and_the_reason_is_published(self):
        # Live bugfix run (#1897): while a report repair was still pending the view named
        # --retry-failed-stage, which authorize_failure_retry refuses in that same state, so a
        # client taking only next_command was pointed at a command its own CLI rejects.
        # This pins the projection and the published reason. Whether any command then advances such
        # a run is #1899: on an unchanged source the plain resume holds again before reconciliation.
        blockers = {
            "active_stage": {"stage": "sol", "attempts": 2},
            "uncertain_artifacts": ["iterations/002/builder-01.json"],
            "pending_report_repair": {"attempts": 2},
        }
        for key, value in blockers.items():
            with self.subTest(key=key):
                state = {"status": "PAUSED_REPEATED_FAILURE", key: value, "run_dir": "rd", "workspace": "ws"}
                self.assertNotIn("--retry-failed-stage", run_actions.next_command(state, None, "rd", "ws"))
                view = run_view.action(state, run_dir="rd", workspace="ws")
                self.assertNotIn("--retry-failed-stage", view["next_command"])
                self.assertIn("--resume-paused", view["next_command"])
                # The pause says why the retry waits, in the CLI's own words, rather than dropping it.
                self.assertEqual(
                    recovery_limits.RETRY_UNRECONCILED_REFUSAL, (view["needs"] or {}).get("retry_unavailable")
                )

    def test_a_state_the_retry_gate_ignores_still_names_the_retry(self):
        # An active runner check or a building batch refuses a novelty hold's scoped retry, not this
        # one: withholding --retry-failed-stage here would hide a command the person can run (#1897).
        for key, value in (
            ("active_runner_check", {"stage": "sol"}),
            ("orchestration_batch", {"status": "BUILDING"}),
        ):
            with self.subTest(key=key):
                state = {"status": "PAUSED_REPEATED_FAILURE", key: value, "run_dir": "rd", "workspace": "ws"}
                self.assertIn("--retry-failed-stage", run_actions.next_command(state, None, "rd", "ws"))
                self.assertIn(
                    "--retry-failed-stage", run_view.action(state, run_dir="rd", workspace="ws")["next_command"]
                )
                self.assertNotIn("retry_unavailable", run_view.action(state, run_dir="rd", workspace="ws")["needs"])

    def test_the_advice_and_the_cli_gate_share_one_refusal(self):
        # One predicate for both, so the view cannot name a retry the CLI rejects (#1897).
        for key in recovery_limits.RETRY_UNRECONCILED:
            with self.subTest(key=key):
                state = {"status": "PAUSED_REPEATED_FAILURE", key: {"attempts": 1}}
                self.assertEqual(
                    recovery_limits.RETRY_UNRECONCILED_REFUSAL, recovery_limits.repeated_failure_retry_refusal(state)
                )
                with self.assertRaisesRegex(ValueError, "Reconcile the active attempt or pending report repair"):
                    stage_recovery.authorize_failure_retry({**state, "run_dir": "rd"}, "rd", "ws")

    def test_the_testers_pause_names_feedback_not_a_resume_that_repeats_it(self):
        advice = run_actions.next_command({"status": "PAUSED_ACCEPTANCE_TESTS"}, None, "rd", "ws")
        self.assertIn("--feedback", advice)
        self.assertNotIn("--resume-paused", advice)


if __name__ == "__main__":
    unittest.main()
