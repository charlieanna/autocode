"""Successful progress ends routine failure streaks without deleting usage."""

import copy
import unittest

import autocode as runner
import autocode_recovery_accounting as accounting
import autocode_recovery_progress as progress


def checkpoint():
    return {
        "stage": "astra_challenge",
        "role": "astra",
        "exit_code": 0,
        "finished_at": "2026-10-21T10:00:10+00:00",
        "output": "/run/review.json",
        "events": "/run/review.jsonl",
        "schema": "/schemas/review.json",
    }


def legacy_state():
    events = [
        {"kind": "automatic_timeout_recovery", "at": f"2026-10-21T10:00:0{i}+00:00", "attempt_id": f"001/failed-{i}"}
        for i in range(3)
    ]
    events.append(
        {"kind": "automatic_timeout_recovery", "at": "2026-10-21T10:00:20+00:00", "attempt_id": "001/builder"}
    )
    return {
        "status": "WAITING_FOR_USER",
        "phase": "EXECUTING",
        "next_stage": "astra_review",
        "automatic_recoveries_since_resume": 4,
        "consecutive_timeout_recoveries": 1,
        "stages": [checkpoint()],
        "user_events": events,
        "settings": {"limits": {"max_seconds": 14400, "max_cost_usd": 5}},
        "active_seconds": 100,
        "cost": {"spent_usd": 1},
        "resolver": {
            "human_escalations": {
                "request": {
                    "status": "pending",
                    "identity": {"proposal": {"origin": {"pause_status": "PAUSED_TIMEOUT_RECOVERY"}}},
                }
            }
        },
    }


class RecoveryStreakTests(unittest.TestCase):
    def test_success_between_failures_renews_streak_not_usage_or_attempts(self):
        state = {
            "automatic_recoveries_since_resume": 2,
            "consecutive_timeout_recoveries": 2,
            "active_seconds": 123,
            "cost": {"spent_usd": 1.25},
            "user_events": [{"kind": "retained"}],
            "stages": [],
            "history": [],
        }
        saved = copy.deepcopy(state)
        runner.save_record(state, checkpoint())
        self.assertEqual(0, accounting.spent(state))
        self.assertEqual(0, accounting.consecutive_timeouts(state))
        for key in ("active_seconds", "cost", "user_events"):
            self.assertEqual(saved[key], state[key])
        self.assertEqual([checkpoint()], state["stages"])
        for _ in range(2):
            accounting.count(state)
        runner.timeout_recovery_guard(state)
        accounting.count(state)
        with self.assertRaises(runner.support.Paused):
            runner.timeout_recovery_guard(state)

    def test_failed_uncertain_or_runner_only_reports_cannot_renew_streak(self):
        for changed in (
            {"rejected": True},
            {"timed_out": True},
            {"abandoned": True},
            {"interrupted": True},
            {"runner_owned": True},
            {"exit_code": 1},
            {"finished_at": None},
            {"schema": None},
            {"events": None},
        ):
            with self.subTest(changed=changed):
                state = {"automatic_recoveries_since_resume": 3}
                runner.save_record(state, {**checkpoint(), **changed})
                self.assertEqual(3, accounting.spent(state))

    def test_old_balanced_history_proves_only_one_failure_after_progress_read_only(self):
        state = legacy_state()
        before = copy.deepcopy(state)
        self.assertEqual(1, accounting.spent(state))
        self.assertEqual(before, state)

    def test_missing_corrupt_or_unchanged_history_keeps_its_bound(self):
        for mutate in (
            lambda s: s.update(stages=[]),
            lambda s: s["stages"][0].update(rejected=True),
            lambda s: s["stages"][0].update(finished_at="2026-10-21T09:00:00+00:00"),
            lambda s: s["user_events"].pop(),
            lambda s: s["user_events"].append(copy.deepcopy(s["user_events"][-1])),
            lambda s: s["user_events"][0].update(at="unknown"),
            lambda s: s["user_events"].reverse(),
        ):
            state = legacy_state()
            mutate(state)
            before = copy.deepcopy(state)
            self.assertEqual(4, accounting.spent(state))
            self.assertEqual(before, state)

    def test_prior_explicit_grants_are_reconciled_without_fabricating_new_ones(self):
        state = legacy_state()
        state["user_events"].insert(
            3,
            {
                "kind": "recovery_grant",
                "at": "2026-10-21T10:00:05+00:00",
                "request_id": "old-grant",
                "previous_count": 3,
                "amount": 1,
            },
        )
        state["automatic_recoveries_since_resume"] = 3
        self.assertEqual(1, accounting.spent(state))
        state["user_events"][3]["previous_count"] = 2
        self.assertEqual(3, accounting.spent(state))

    def test_current_request_can_resume_without_grant_and_other_holds_cannot(self):
        issued = {"request_id": "request", "scope": "operational_exhaustion"}
        state = legacy_state()
        saved = copy.deepcopy(state)
        self.assertTrue(
            progress.reconcile(state, issued=issued, approved=True, supersede=lambda *_: True, now=lambda: "now")
        )
        self.assertEqual("RUNNING", state["status"])
        self.assertNotIn("recovery_grants", state)
        for key in ("settings", "stages", "active_seconds", "cost"):
            self.assertEqual(saved[key], state[key])
        for cause in ("PAUSED_TIME_LIMIT", "PAUSED_COST_LIMIT", "PAUSED_NO_PROGRESS", "PAUSED_REPEATED_FAILURE"):
            state = legacy_state()
            state["resolver"]["human_escalations"]["request"]["identity"]["proposal"]["origin"]["pause_status"] = cause
            self.assertFalse(progress.routine_resume(state, issued))
