"""Exhausted internal recovery is recorded as a failure, not a question."""

import argparse
import contextlib
import copy
import io
import unittest
from pathlib import Path
from unittest import mock

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

    def test_a_quota_stop_found_by_the_access_probe_asks_for_the_stopped_roles_model(self):
        # Live, 2026-11-18: the Tester's account hit its usage limit, the access probe caught it before the
        # Tester launched, and the run was recorded as blocked with no action. A quota stop asks for a model,
        # whether a stage or the probe found it.
        self.state.update(
            status="PAUSED_BUDGET",
            next_stage="tester",
            pending_questions=[],
            settings={
                "engine": "codex",
                "roles": {
                    "builder": {"model": "gpt-5.6-terra"},
                    "tester": {"model": "gpt-6-sol"},
                    "completion": {"model": "gpt-6-sol"},
                },
            },
        )
        run = Path(self.state["run_dir"])
        events = run / "model-preflight" / "probe" / "events.jsonl"
        events.parent.mkdir(parents=True)
        events.write_text('{"type": "error", "error": {"message": "The usage limit has been reached"}}\n')
        self.state["stages"].append(
            {
                "stage": "model_preflight",
                "role": "model_preflight",
                "runner_owned": True,
                "model": "gpt-6-sol",
                "iteration": 1,
                "events": str(events),
                "output": str(events.parent / "report.json"),
                "launch_route": {"engine": "codex", "model": "gpt-6-sol"},
                "preflight_roles": ["tester"],
                "preflight_outcome": {"model": "gpt-6-sol", "status": "PAUSED_BUDGET", "blocking": True},
            }
        )
        settings = copy.deepcopy(self.state["settings"])
        error = support.Paused(
            "PAUSED_BUDGET",
            "Model access preflight failed for gpt-6-sol (role: tester): The usage limit has been reached",
        )
        self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(runner, self.state, run, error))
        with contextlib.redirect_stdout(io.StringIO()):
            runner.write_json(run / "state.json", self.state)
        saved = support.read(run / "state.json")
        shown = run_view.view(saved, run_dir=run, workspace=Path(saved["workspace"]))
        self.assertNotEqual("blocked", (shown.get("recovery") or {}).get("category"))
        self.assertEqual("answer", shown["needs"]["kind"])
        self.assertEqual(["route-tester"], [question["id"] for question in shown["needs"]["questions"]])
        self.assertEqual("Tester", shown["needs"]["route"]["job"])
        self.assertIn("--answer route-tester=MODEL", shown["next_command"])
        self.assertNotIn("--abandon-stage", shown["next_command"])
        self.assertEqual(settings, saved["settings"], "asking changes no route")

        # The person names the model through the view's own action; nothing is set aside, and the
        # next resume probes the named model before the Tester runs.
        args = argparse.Namespace(
            answer=["route-tester=gpt-6-luna"],
            delegate=[],
            delegate_all=False,
            resolver_token=shown["needs"]["resolver_token"],
        )
        with mock.patch.object(runner, "abandon_stage") as abandon, contextlib.redirect_stdout(io.StringIO()):
            code = runner.action_resolver.answer_quota_question(runner, args, saved, run, Path(saved["workspace"]))
        self.assertEqual(0, code)
        abandon.assert_not_called()
        answered = support.read(run / "state.json")
        self.assertEqual("gpt-6-luna", answered["settings"]["roles"]["tester"]["model"])
        self.assertEqual("gpt-6-sol", answered["settings"]["roles"]["completion"]["model"])
        assignment = answered["user_events"][-1]
        self.assertEqual(
            ("route_assignment", "tester", "gpt-6-sol", "gpt-6-luna"),
            (assignment["kind"], assignment["role"], assignment["from"], assignment["to"]),
        )
        self.assertEqual("PAUSED_STAGE_ABANDONED", answered["status"])
        self.assertIsNone(human.current(answered))

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
