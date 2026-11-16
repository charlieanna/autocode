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
from .operational_fixtures import legacy_publication


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
                self.assertIsNone(human.current(saved))
                self.assertEqual(pause, saved["status"])
                self.assertEqual([], saved.get("pending_questions", []))
                self.assertTrue(saved["resolver"]["operational_diagnostics"][-1])
                self.assertEqual(settings, saved["settings"], "a hold changes no route or limit")
                view = run_view.view(saved, run_dir=run, workspace=Path(saved["workspace"]))
                self.assertFalse(view["done"])
                self.assertIsNone(view["needs"])
                self.assertIsNone(view["next_command"])
                self.assertEqual("blocked", view["recovery"]["category"])
                self.assertIsNone(view["chat"]["input"])
                self.assertNotIn("Provide corrective information", str(view["chat"]))
                self.assertIn("No recovery action is requested", view["explanation"]["what_the_command_does"])
                self.assertNotIn("resume", view["explanation"]["explanation"].lower())

    def test_person_owned_and_unknown_origin_limits_keep_their_existing_boundary(self):
        for pause in ("PAUSED_TIME_LIMIT", "PAUSED_COST_LIMIT"):
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

    def test_provider_status_without_an_authentic_model_choice_is_not_a_person_question(self):
        for pause in ("PAUSED_BUDGET", "PAUSED_PROVIDER_UNCERTAIN"):
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
                shown = run_view.view(self.state, run_dir=run, workspace=Path(self.state["workspace"]))
                self.assertEqual("blocked", shown["recovery"]["category"])
                self.assertIsNone(shown["needs"])
                self.assertIsNone(shown["next_command"])
                self.assertIsNone(shown["chat"]["input"])
                self.assertEqual(settings, self.state["settings"])

    def test_legacy_machinery_request_does_not_offer_or_accept_its_old_person_action(self):
        self.state.update(status="PAUSED_REPEATED_FAILURE", next_stage="review_plan", pending_questions=[])
        run = Path(self.state["run_dir"])
        old = legacy_publication(self.state, run)
        before = copy.deepcopy(self.state)
        view = run_view.view(self.state, run_dir=run, workspace=Path(self.state["workspace"]))
        self.assertIsNone(view["needs"])
        self.assertIsNone(view["next_command"])
        self.assertEqual("blocked", view["recovery"]["category"])
        self.assertIsNone(view["chat"]["input"])
        self.assertNotIn("Provide corrective information", str(view["chat"]))
        with self.assertRaises(ValueError):
            human.respond_operational(
                self.state, old["request_id"], old["request_token"], "provide_information", "Cause repaired"
            )
        self.assertEqual(before, self.state)
