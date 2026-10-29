"""An explicit allowance preserves request identity across one settings update."""

import copy
import unittest
from pathlib import Path
from unittest.mock import Mock

import autocode_recovery_grants as grants


class RecoveryGrantTests(unittest.TestCase):
    def setUp(self):
        self.previous = {"limits": {"max_stage_seconds": 30}}
        self.state = {
            "status": "WAITING_FOR_USER",
            "settings": {"limits": {"max_stage_seconds": 300}},
            "source_revision": "source",
            "automatic_recoveries_since_resume": 3,
            "consecutive_timeout_recoveries": 3,
            "automatic_timeout_recoveries": [{"stage": "terra", "attempt_id": "one"}],
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
