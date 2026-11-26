"""A planning timeout cannot exhaust a Builder's unchanged-batch allowance."""

import copy
import unittest
from unittest import mock

import autocode_recovery_progress as progress


class RecoveryProgressTests(unittest.TestCase):
    def state(self):
        return {
            "status": "WAITING_FOR_USER",
            "no_progress_batches": 3,
            "automatic_recoveries_since_resume": 2,
            "consecutive_timeout_recoveries": 0,
            "automatic_timeout_recoveries": [
                {"stage": "requirements_gather", "attempt_id": str(i), "changed_files": []} for i in range(3)
            ],
            "stages": [
                {"stage": "requirements_gather", "automatic_recovery": True},
                {"stage": "resolver", "runner_owned": True},
                {"stage": "finalize_plan"},
            ],
            "resolver": {
                "human_escalations": {
                    "request": {"identity": {"proposal": {"origin": {"pause_status": "PAUSED_NO_PROGRESS"}}}}
                }
            },
        }

    def reconcile(self, state, *, issued=True, approved=True, supersede=True):
        return progress.reconcile(
            state,
            issued={"request_id": "request", "scope": "operational_exhaustion"} if issued else None,
            approved=approved,
            supersede=lambda *_: supersede,
            now=lambda: "now",
        )

    def test_correcting_prebuild_hold_preserves_spent_timeout_allowance_and_history(self):
        state = self.state()
        before = copy.deepcopy(state)
        self.assertTrue(self.reconcile(state))
        self.assertEqual(("RUNNING", 0), (state["status"], state["no_progress_batches"]))
        for key in (
            "automatic_recoveries_since_resume",
            "consecutive_timeout_recoveries",
            "automatic_timeout_recoveries",
            "stages",
        ):
            self.assertEqual(before[key], state[key])
        self.assertEqual(3, state["user_events"][-1]["previous_no_progress_batches"])
        self.assertFalse(self.reconcile(state), "the correction must apply only once")

    def test_stale_unapproved_or_unretirable_request_cannot_resume(self):
        for option in ("issued", "approved", "supersede"):
            with self.subTest(option=option):
                state = self.state()
                before = copy.deepcopy(state)
                self.assertFalse(self.reconcile(state, **{option: False}))
                self.assertEqual(before, state)

    def test_implementation_or_parallel_worker_history_keeps_its_counter(self):
        for mutation in (
            lambda s: s["stages"].append({"stage": "builder"}),
            lambda s: s.update(orchestration_history=[{"workers": [{"status": "FAILED"}]}]),
            lambda s: s.update(orchestration_batch={"status": "BUILDING"}),
            lambda s: s.update(implementation={"summary": "candidate"}),
            lambda s: s["stages"].append({"stage": "unknown_future_stage"}),
        ):
            state = self.state()
            mutation(state)
            before = copy.deepcopy(state)
            self.assertFalse(self.reconcile(state))
            self.assertEqual(before, state)

    def test_unattributed_or_duplicate_recoveries_cannot_clear_a_counter(self):
        state = self.state()
        state["automatic_timeout_recoveries"] = [state["automatic_timeout_recoveries"][0]] * 3
        self.assertFalse(self.reconcile(state))
        state = self.state()
        state["no_progress_batches"] = 4
        self.assertFalse(self.reconcile(state))

    def test_other_pause_kinds_retain_their_boundary(self):
        state = self.state()
        state["resolver"]["human_escalations"]["request"]["identity"]["proposal"]["origin"]["pause_status"] = (
            "PAUSED_TIMEOUT_RECOVERY"
        )
        before = copy.deepcopy(state)
        self.assertFalse(self.reconcile(state))
        self.assertEqual(before, state)


class PendingOfferTests(unittest.TestCase):
    """What a status view and a row of `autocode runs` ask of every run they show."""

    ISSUED = {"request_id": "request", "scope": "operational_exhaustion"}

    def pending(self, state, *, issued, approved=True):
        with (
            mock.patch.object(progress.human, "retained", return_value=issued),
            mock.patch.object(progress.contracts, "approved", return_value=approved),
        ):
            return progress.pending(state)

    def test_a_run_with_nothing_to_retire_is_refused_without_copying_its_saved_state(self):
        """A listing of 158 runs copied every run's whole state, twice, to be refused on the first line."""
        state = RecoveryProgressTests.state(self)
        with mock.patch.object(progress, "deepcopy", side_effect=AssertionError("the state was copied")):
            self.assertFalse(progress.pending(state), "a state no resolver request was published for")
            self.assertFalse(self.pending(state, issued=None))
            self.assertFalse(self.pending(state, issued={**self.ISSUED, "scope": "product_decision"}))
            self.assertFalse(self.pending(state, issued=self.ISSUED, approved=False))

    def test_a_hold_that_retained_progress_disproves_is_offered_and_the_state_stays_as_saved(self):
        state = RecoveryProgressTests.state(self)
        before = copy.deepcopy(state)
        self.assertTrue(self.pending(state, issued=self.ISSUED))
        self.assertEqual(before, state, "the offer is read from a copy")
        self.assertFalse(self.pending({**state, "pause_requested": True}, issued=self.ISSUED))
