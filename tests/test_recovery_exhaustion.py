"""A spent automatic recovery allowance ends the run with a recorded outcome; nobody is asked to grant more.

The run paused PAUSED_TIMEOUT_RECOVERY and asked a person to "authorize additional recoveries"
(`--grant-recovery N`, a number field in VS Code), or, on the live path, waited in an "AutoCode is
blocked on internal recovery" pause with nothing to answer. Both are AutoCode's own retries
(AGENTS.md "What a person is asked"). The Investigator has already looked by then (autocode_stuck_job).
"""

import copy
import unittest

import autocode_recovery_exhaustion as exhaustion
import autocode_resolver_human as human
import autocode_run_status as run_status
import autocode_run_view as run_view
import autocode_stop as stop
import autocode_support as support

from . import test_resolver_human
from .operational_fixtures import legacy_publication

AT = "2026-11-17T00:00:00Z"


class RecoveryExhaustionTests(unittest.TestCase):
    setUp = test_resolver_human.ResolverHumanTests.setUp
    contract = test_resolver_human.ResolverHumanTests.contract
    request = test_resolver_human.ResolverHumanTests.request

    def spend(self, count=3):
        self.contract()
        self.state.update(
            status=exhaustion.STATUS,
            stop_reason="Automatic recovery budget exhausted; no further provider will launch. "
            "Last cause: timeout. After fixing the cause, authorize more recoveries explicitly with "
            "autocode resume --grant-recovery N.",
            next_stage="review_plan",
            automatic_recoveries_since_resume=count,
            consecutive_timeout_recoveries=count,
        )

    def view(self):
        return run_view.view(self.state, run_dir=str(self.root / "run"), workspace=str(self.root))

    def test_a_spent_allowance_offers_no_grant_and_continues_on_its_own(self):
        self.spend()
        view = self.view()
        self.assertEqual("continue", view["needs"]["kind"])
        self.assertNotIn("command_inputs", view["needs"])
        self.assertNotIn("--grant-recovery", str(view["next_command"]))
        self.assertEqual(exhaustion.NOTICE, view["stop_reason"])
        self.assertEqual({"origin": "paused", "request_id": None}, exhaustion.pending(self.state))

    def test_resume_ends_the_run_with_the_recoveries_as_evidence(self):
        self.spend()
        self.state["stuck_investigations"] = [
            {"identity": "review_plan:PAUSED_TIMEOUT_RECOVERY", "status": exhaustion.STATUS, "outcome": "paused"}
        ]
        self.assertTrue(exhaustion.settle(self.state, at=AT))
        self.assertEqual(stop.STOP_STATUS, self.state["status"])
        self.assertTrue(self.state["stop_reason"].startswith(run_status.ENDED_PREFIX))
        outcome = stop.outcome(self.state)
        self.assertEqual("could_not_finish", outcome["result"])
        evidence = outcome["evidence"]
        self.assertEqual((3, 3), (evidence["recoveries_used"], evidence["recoveries_allowed"]))
        self.assertEqual("paused", evidence["investigations"][0]["outcome"])
        view = self.view()
        self.assertIsNone(view["needs"])
        self.assertIsNone(view["next_command"])
        self.assertIsNone(exhaustion.pending(self.state))
        self.assertFalse(exhaustion.settle(self.state, at=AT), "an ended run is not ended twice")

    def test_the_recorded_end_names_its_cause_without_the_command_an_operator_would_type(self):
        # A timeout's saved reason also tells an operator what to type; the end a person reads keeps
        # only its words, and the evidence keeps the whole cause.
        self.spend()
        cause = (
            "No new provider activity within the inactivity limit (1 seconds, set explicitly; "
            "change it with autocode resume --max-idle-seconds N)"
        )
        self.state["recovery_context"] = {"timeout_reason": cause}
        self.assertTrue(exhaustion.conclude(self.state, at=AT))
        outcome = stop.outcome(self.state)
        self.assertTrue(outcome["reason"].endswith("Last cause: No new provider activity within the inactivity limit"))
        self.assertNotIn("--", self.state["stop_reason"])
        self.assertEqual(cause, outcome["evidence"]["last_cause"])

    def test_a_published_request_about_the_allowance_is_withdrawn_before_the_end(self):
        self.spend()
        legacy_publication(self.state, self.root / "run", request=self.request())
        found = exhaustion.pending(self.state)
        self.assertEqual("published", found["origin"])
        self.assertEqual("continue", self.view()["needs"]["kind"])
        self.assertTrue(exhaustion.settle(self.state, at=AT))
        self.assertIsNone(human.retained(self.state))
        self.assertEqual(found["request_id"], stop.outcome(self.state)["evidence"]["superseded_request_id"])

    def test_the_live_pause_ends_only_when_the_allowance_is_spent_and_nothing_is_open(self):
        paused = support.Paused(exhaustion.STATUS, "Automatic recovery budget exhausted")
        self.spend(count=1)
        self.assertFalse(exhaustion.conclude_pause(copy.deepcopy(self.state), paused, at=AT))
        self.spend()
        other = support.Paused("PAUSED_COST_LIMIT", "Spend reached the cap")
        self.assertFalse(exhaustion.conclude_pause(copy.deepcopy(self.state), other, at=AT))
        busy = copy.deepcopy(self.state)
        busy["active_stage"] = {"stage": "review_plan"}
        self.assertFalse(exhaustion.conclude_pause(busy, paused, at=AT))
        self.assertIsNone(exhaustion.pending(busy))
        self.assertTrue(exhaustion.conclude_pause(self.state, paused, at=AT))
        self.assertEqual(str(paused), stop.outcome(self.state)["evidence"]["paused_with"])

    def test_the_consecutive_timeout_limit_is_the_same_rule_the_pause_uses(self):
        self.spend(count=0)
        self.state["consecutive_timeout_recoveries"] = 3
        self.state["automatic_recoveries_since_resume"] = 0
        self.state["settings"] = {"limits": {"no_progress_batches": 3}}
        self.assertTrue(exhaustion.spent(self.state))
        self.state["settings"]["limits"]["no_progress_batches"] = 5
        self.assertFalse(exhaustion.spent(self.state))


if __name__ == "__main__":
    unittest.main()
