"""Exhausted internal recovery is recorded as a failure, not a question."""

import contextlib
import copy
import io
import unittest
from pathlib import Path

import autocode as runner
import autocode_resolver_human as human
import autocode_run_view as run_view
import autocode_support as support

from . import test_resolver_human as fixture


class OperationalHoldTests(unittest.TestCase):
    setUp = fixture.ResolverHumanTests.setUp
    contract = fixture.ResolverHumanTests.contract

    def test_exhausted_internal_recovery_records_a_block_without_asking_for_machinery(self):
        for pause in ("PAUSED_REPORT_REPAIR_LIMIT", "PAUSED_REPEATED_FAILURE", "PAUSED_TIMEOUT_RECOVERY"):
            with self.subTest(pause=pause):
                self.setUp()
                self.state.update(status=pause, next_stage="review_plan", pending_questions=[])
                run = Path(self.state["run_dir"])
                error = support.Paused(pause, "The saved internal recovery could not finish")
                settings = copy.deepcopy(self.state["settings"])
                self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(runner, self.state, run, error))
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    runner.write_json(run / "state.json", self.state)
                self.assertNotIn("Answer its question", output.getvalue())
                self.assertNotIn("Provide corrective information", output.getvalue())
                saved = support.read(run / "state.json")
                self.assertIsNotNone(human.current(saved), "the operator's authenticated action remains available")
                self.assertEqual("WAITING_FOR_USER", saved["status"])
                self.assertTrue(saved["resolver"]["operational_diagnostics"][-1])
                self.assertEqual(settings, saved["settings"], "a hold changes no route or limit")
                view = run_view.view(saved, run_dir=run, workspace=Path(saved["workspace"]))
                self.assertFalse(view["done"])
                self.assertEqual("blocked", view["recovery"]["category"])
                self.assertIsNone(view["chat"]["input"])
                self.assertNotIn("Provide corrective information", str(view["chat"]))
                self.assertIn("No recovery action is requested", view["explanation"]["what_the_command_does"])
                self.assertNotIn("resume", view["explanation"]["explanation"].lower())

    def test_person_owned_limits_and_connection_stops_keep_their_existing_boundary(self):
        for pause in ("PAUSED_TIME_LIMIT", "PAUSED_COST_LIMIT", "PAUSED_BUDGET", "PAUSED_PROVIDER_UNCERTAIN"):
            with self.subTest(pause=pause):
                self.setUp()
                self.state.update(status=pause, next_stage="review_plan", pending_questions=[])
                run = Path(self.state["run_dir"])
                settings = copy.deepcopy(self.state["settings"])
                self.assertTrue(
                    runner.resolver_runtime.record_operational_exhaustion(
                        runner, self.state, run, support.Paused(pause, "The selected boundary is exhausted")
                    )
                )
                self.assertNotEqual(
                    "blocked",
                    (
                        run_view.view(self.state, run_dir=run, workspace=Path(self.state["workspace"])).get("recovery")
                        or {}
                    ).get("category"),
                )
                self.assertEqual(settings, self.state["settings"])

    def test_legacy_machinery_request_keeps_operator_action_but_is_not_a_person_question(self):
        self.state.update(status="PAUSED_REPEATED_FAILURE", next_stage="review_plan", pending_questions=[])
        run = Path(self.state["run_dir"])
        budget = {"category": "operational_recovery", "kind": None, "origin": "unknown", "limit": None}
        receipt = runner.resolver_runtime._operational_receipt(
            self.state, run, "hold", "Recovery exhausted", {"status": "PAUSED_REPEATED_FAILURE", "budget": budget}
        )
        human.queue(
            self.state,
            "operational_exhaustion",
            {"stage": "review_plan", "pause_status": "PAUSED_REPEATED_FAILURE", "budget": budget},
            request={"kind": "blocker", "decision_needed": "Provide corrective information", "impact": "Held"},
            evidence={"resolver_receipt_id": receipt},
        )
        self.assertEqual("escalate", human.evaluate(self.state))
        view = run_view.view(self.state, run_dir=run, workspace=Path(self.state["workspace"]))
        self.assertEqual("answer", view["needs"]["kind"], "existing operator contract is retained")
        self.assertIsNotNone(view["next_command"])
        self.assertEqual("blocked", view["recovery"]["category"])
        self.assertIsNone(view["chat"]["input"])
        self.assertNotIn("Provide corrective information", str(view["chat"]))
