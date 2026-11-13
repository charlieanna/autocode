"""An explicit allowance preserves request identity across one settings update."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import autocode as runner
import autocode_recovery_grants as grants
import autocode_resolver_human as human
import autocode_support as support


class RecoveryGrantTests(unittest.TestCase):
    def setUp(self):
        self.previous = {"limits": {"max_stage_seconds": 30}}
        self.state = {
            "status": "WAITING_FOR_USER",
            "settings": {"limits": {"max_stage_seconds": 300}},
            "source_revision": "source",
            "automatic_recoveries_since_resume": 3,
            "consecutive_timeout_recoveries": 3,
            "automatic_timeout_recoveries": [{"stage": "builder", "attempt_id": "one"}],
            "resolver": {
                "human_escalations": {
                    "issued": {"identity": {"proposal": {"origin": {"pause_status": "PAUSED_TIMEOUT_RECOVERY"}}}}
                }
            },
        }
        self.issued = {"request_id": "issued", "scope": "operational_exhaustion"}
        self.current = Mock(
            side_effect=lambda state: (
                self.issued if state["settings"] == self.previous and state["source_revision"] == "source" else None
            )
        )
        self.persist = Mock()
        self.supersede = Mock()

    def grant(self, amount=1, previous=True, limit_events=None):
        return grants.grant(
            self.state,
            Path("/run"),
            amount,
            previous_settings=self.previous if previous else None,
            current_request=self.current,
            count=3,
            maximum=3,
            supersede=self.supersede,
            persist=self.persist,
            pending_limit_events=list(limit_events or ()),
        )

    def test_settings_change_uses_exact_prior_request_without_resetting_history(self):
        history = copy.deepcopy(self.state["automatic_timeout_recoveries"])
        selected = copy.deepcopy(self.state["settings"])
        self.assertEqual(2, self.grant())
        self.assertEqual(history, self.state["automatic_timeout_recoveries"])
        self.assertEqual(selected, self.state["settings"])
        self.assertEqual(0, self.state["consecutive_timeout_recoveries"])
        receipt = self.state["recovery_grants"][-1]
        self.assertEqual(
            ("issued", 3, 2, 1),
            tuple(receipt[key] for key in ("request_id", "previous_count", "remaining_count", "amount")),
        )
        self.persist.assert_called_once_with(Path("/run/state.json"), self.state)
        self.supersede.assert_called_once()

    def test_changed_source_cannot_be_reauthorized_by_previous_settings(self):
        self.state["source_revision"] = "different"
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "exhausted timeout recovery"):
            self.grant()
        self.assertEqual(before, self.state)
        self.persist.assert_not_called()
        self.supersede.assert_not_called()

    def test_the_grants_single_save_already_carries_the_deferred_limit_events(self):
        # The invocation's limit_change events must not change the frontier before validation, yet
        # no exit may persist an accepted grant without them (#2479, #2412): one save holds both.
        saved = []
        self.persist = Mock(side_effect=lambda path, state: saved.append(copy.deepcopy(state)))
        raised = [{"kind": "limit_change", "actor": "user_cli", "flag": "--max-stage-seconds"}]
        self.assertEqual(2, self.grant(limit_events=raised))
        self.assertEqual(1, self.persist.call_count)
        kinds = [row["kind"] for row in saved[0]["user_events"]]
        self.assertEqual(["recovery_grant", "limit_change"], kinds)

    def test_a_refused_grant_persists_neither_limit_events_nor_settings(self):
        self.state["source_revision"] = "different"
        before = copy.deepcopy(self.state)
        raised = [{"kind": "limit_change", "actor": "user_cli", "flag": "--max-stage-seconds"}]
        with self.assertRaisesRegex(ValueError, "exhausted timeout recovery"):
            self.grant(limit_events=raised)
        self.assertEqual(before, self.state)
        self.assertNotIn("user_events", self.state)
        self.persist.assert_not_called()

    def test_no_prior_settings_cannot_reuse_the_old_request(self):
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "exhausted timeout recovery"):
            self.grant(previous=False)
        self.assertEqual(before, self.state)
        self.persist.assert_not_called()
        self.supersede.assert_not_called()

    def test_invalid_amounts_do_not_change_state_or_publish(self):
        for amount in (True, 0, -1, 1.5):
            with self.subTest(amount=amount):
                before = copy.deepcopy(self.state)
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    self.grant(amount)
                self.assertEqual(before, self.state)
        self.current.assert_not_called()
        self.persist.assert_not_called()

    def test_a_grant_is_accepted_at_the_consecutive_timeouts_stop_that_names_it(self):
        # recovery_limits.stop_reason publishes its exhaustion stop when consecutive
        # timeouts reach the no-progress limit, and that stop names --grant-recovery.
        # The grant must accept exactly that stop even when the originating pause is not
        # itself grantable (a live run paused on PAUSED_OUTPUT_CAP and was refused).
        self.state["settings"]["limits"] = {"no_progress_batches": 3}
        self.state["resolver"]["human_escalations"]["issued"]["identity"]["proposal"]["origin"] = {
            "pause_status": "PAUSED_OUTPUT_CAP"
        }
        self.assertEqual(3, grants.accounting.consecutive_timeouts(self.state))
        self.assertTrue(
            grants.eligible(
                self.state,
                previous_settings=self.previous,
                current_request=self.current,
                count=1,
                maximum=6,
            )
        )

    def test_a_grant_is_still_refused_before_any_recovery_budget_is_exhausted(self):
        self.state["settings"]["limits"] = {"no_progress_batches": 3}
        self.state["consecutive_timeout_recoveries"] = 0
        self.state["resolver"]["human_escalations"]["issued"]["identity"]["proposal"]["origin"] = {
            "pause_status": "PAUSED_OUTPUT_CAP"
        }
        self.assertFalse(
            grants.eligible(
                self.state,
                previous_settings=self.previous,
                current_request=self.current,
                count=1,
                maximum=6,
            )
        )

    def test_authenticated_queued_operational_hold_at_resolver_pending_grants_and_supersedes(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary) / "run"
            workspace = Path(temporary) / "ws"
            run_dir.mkdir()
            workspace.mkdir()
            state = {
                "version": 2,
                "iteration": 1,
                "status": "PAUSED_TIMEOUT_RECOVERY",
                "workspace": str(workspace),
                "run_dir": str(run_dir),
                "task_id": "t",
                "settings": copy.deepcopy(self.previous),
                "source_revision": "rev-1",
                "automatic_recoveries_since_resume": 3,
                "consecutive_timeout_recoveries": 3,
                "automatic_timeout_recoveries": [{"stage": "builder", "attempt_id": "one"}],
                "resolver": {"human_escalations": {}},
                "history": [],
                "user_events": [],
                "stages": [],
            }
            error = support.Paused("PAUSED_TIMEOUT_RECOVERY", "Recovery exhausted")
            self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(runner, state, run_dir, error))
            self.assertEqual("RESOLVER_PENDING", state["status"])
            queued = human.queued_operational(state)
            self.assertIsNotNone(queued)

            # Unauthenticated receipt or changed source_revision is rejected, and queued_request=None refuses RESOLVER_PENDING.
            self.assertFalse(grants.eligible(state, current_request=human.current, count=3, maximum=3))
            self.assertTrue(
                grants.eligible(
                    state,
                    current_request=human.current,
                    queued_request=human.queued_operational,
                    count=3,
                    maximum=3,
                )
            )
            cap_state = {
                "version": 2,
                "iteration": 1,
                "status": "PAUSED_OUTPUT_CAP",
                "workspace": str(workspace),
                "run_dir": str(run_dir),
                "task_id": "t",
                "settings": copy.deepcopy(self.previous),
                "source_revision": "rev-1",
                "automatic_recoveries_since_resume": 3,
                "consecutive_timeout_recoveries": 0,
                "automatic_timeout_recoveries": [],
                "resolver": {"human_escalations": {}},
                "history": [],
                "user_events": [],
                "stages": [],
            }
            self.assertTrue(
                runner.resolver_runtime.record_operational_exhaustion(
                    runner, cap_state, run_dir, support.Paused("PAUSED_OUTPUT_CAP", "Output cap")
                )
            )
            self.assertTrue(
                grants.eligible(
                    cap_state,
                    current_request=human.current,
                    queued_request=human.queued_operational,
                    count=3,
                    maximum=3,
                )
            )
            self.assertFalse(
                grants.eligible(
                    cap_state,
                    current_request=human.current,
                    queued_request=human.queued_operational,
                    count=2,
                    maximum=3,
                )
            )
            receipt_id = queued["evidence"]["resolver_receipt_id"]
            bad_receipt = copy.deepcopy(state)
            bad_receipt["resolver"]["operational_receipts"][receipt_id]["runner_owned"] = False
            self.assertIsNone(human.queued_operational(bad_receipt))
            self.assertFalse(
                grants.eligible(
                    bad_receipt,
                    current_request=human.current,
                    queued_request=human.queued_operational,
                    count=3,
                    maximum=3,
                )
            )
            bad_source = copy.deepcopy(state)
            bad_source["source_revision"] = "rev-changed"
            self.assertIsNone(human.queued_operational(bad_source))

            # Valid held RESOLVER_PENDING is eligible and grants across a single settings adjustment.
            state["settings"] = {"limits": {"max_stage_seconds": 300}}
            self.assertTrue(
                grants.eligible(
                    state,
                    previous_settings=self.previous,
                    current_request=human.current,
                    queued_request=human.queued_operational,
                    count=3,
                    maximum=3,
                )
            )
            persist = Mock()
            granted = grants.grant(
                state,
                run_dir,
                1,
                previous_settings=self.previous,
                current_request=human.current,
                queued_request=human.queued_operational,
                count=3,
                maximum=3,
                supersede=human.supersede_operational,
                persist=persist,
            )
            self.assertEqual(2, granted)
            self.assertEqual("PAUSED_TIMEOUT_RECOVERY", state["status"])
            self.assertIsNone(state.get("resolver_human_proposal"))
            self.assertIsNone(human.queued_operational(state))
            self.assertEqual(2, state["automatic_recoveries_since_resume"])
            self.assertEqual(0, state["consecutive_timeout_recoveries"])
            self.assertEqual(["recovery_grant"], [e["kind"] for e in state["user_events"]])
            persist.assert_called_once()
