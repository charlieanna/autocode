"""A roster timeout is a typed Core outcome, never a model-output/person gate."""

import contextlib
import copy
import io
import json
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_model_listing_failure as failure
import autocode_model_listing_failure_recovery as recovery
import autocode_run_actions as actions
import autocode_run_view as run_view
import autocode_status_summary as status_summary
import autocode_stop as stop
import autocode_util as util
from providers import command


class ModelListingFailureTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.run = self.workspace / ".autocode/runs/example"
        self.run.mkdir(parents=True)
        config = Path(command.__file__).parent / "configs/kilocode.toml"
        self.provider = command.CommandProvider(tomllib.loads(config.read_text()), config)
        self.state = {
            "workspace": str(self.workspace),
            "run_dir": str(self.run),
            "task_id": "task",
            "task": "Retain selected policy",
            "goal_contract": {"body": {"outcome": "approved"}},
            "settings": {
                "engine": "opencode",
                "provider": "kilocode",
                "roles": {"planner": {"model": "saved", "reasoning_effort": "high"}},
                "limits": {"max_cost_usd": 0, "max_planning_calls": 20},
            },
            "status": "RUNNING",
            "next_stage": "finalize_plan",
            "iteration": 1,
            "stages": [],
            "history": [],
        }
        self.writes = []
        self.runner = SimpleNamespace(
            write_json=lambda path, state: self.writes.append(copy.deepcopy(state)),
            migrate_opencode_roles=lambda *a: False,
            opencode=self.provider,
        )

    def error(self, **changes):
        return failure.ModelListingTimeout(
            "kilocode model listing timed out after 30 seconds and again after 60 seconds; no agent was launched",
            workspace=changes.get("workspace", self.workspace),
            config_sha256=changes.get("config_sha256", "a" * 64),
            direct_child_collected=changes.get("child_collected", True),
        )

    def test_actual_roster_timeout_is_typed_and_keeps_existing_two_bounds(self):
        with (
            patch.object(self.provider, "_preflight_environment", return_value={"PATH": "/test"}),
            patch.object(
                command.env_prep, "preflight_run", side_effect=subprocess.TimeoutExpired(["kilo", "models"], 60)
            ) as child,
        ):
            with self.assertRaises(RuntimeError) as raised:
                self.provider.available_models(self.workspace)
        observed = raised.exception
        self.assertIs(type(observed), failure.ModelListingTimeout)
        self.assertEqual(observed.workspace, str(self.workspace))
        self.assertTrue(observed.direct_child_collected)
        self.assertEqual(observed.command, ("kilo", "models"))
        self.assertEqual([call.kwargs["timeout"] for call in child.call_args_list], [30, 60])
        self.assertEqual(len(observed.config_sha256), 64)

    def test_other_provider_timeout_and_non_timeout_error_stay_untyped(self):
        for variant in ("other_provider", "other_command", "non_timeout"):
            with self.subTest(variant=variant):
                config = copy.deepcopy(self.provider._config)
                if variant == "other_provider":
                    config["name"] = "other"
                if variant == "other_command":
                    config["models_command"] = ["kilo", "models", "--custom"]
                provider = command.CommandProvider(config, self.provider._path)
                error = (
                    OSError("unrelated")
                    if variant == "non_timeout"
                    else subprocess.TimeoutExpired(config["models_command"], 60)
                )
                with (
                    patch.object(provider, "_preflight_environment", return_value={}),
                    patch.object(command.env_prep, "preflight_listing", side_effect=error),
                ):
                    with self.assertRaises(RuntimeError) as raised:
                        provider.available_models(self.workspace)
                self.assertIsNot(type(raised.exception), failure.ModelListingTimeout)

    def test_saved_run_admission_settles_before_any_stage_and_preserves_policy(self):
        original = copy.deepcopy(self.state)
        args = SimpleNamespace(chat=False, resume_paused=False)
        shown = io.StringIO()
        with (
            contextlib.redirect_stderr(shown),
            patch.object(actions.skip_requirements, "settle", return_value=False),
            patch.object(actions.conversation_ingress, "require_expected_goal"),
            patch.object(actions.conversation_ingress, "record_build_start") as build,
            patch.object(actions.role_transport, "check_models", side_effect=self.error()) as listing,
        ):
            result = actions.admit(
                self.runner, args, None, self.state, self.run / "state.json", self.run, self.workspace, None
            )
        self.assertEqual(result, 2)
        self.assertIn("AutoCode is blocked", shown.getvalue())
        self.assertNotIn("resume", shown.getvalue().lower())
        listing.assert_called_once()
        build.assert_not_called()
        self.assertIsNone(stop.outcome(self.state))
        self.assertIsNotNone(failure.blocked(self.state))
        self.assertEqual(self.state["settings"], original["settings"])
        self.assertEqual(self.state["goal_contract"], original["goal_contract"])
        self.assertEqual(self.state["next_stage"], original["next_stage"])
        self.assertEqual(self.state["stages"][0]["runner_calls"], 0)
        self.assertEqual(len(self.writes), 1)

    def test_matching_text_is_not_typed_recovery_authority(self):
        for error in (RuntimeError(str(self.error())), util.Paused("PAUSED_TRANSPORT_UNVERIFIED", str(self.error()))):
            self.assertFalse(recovery.settle(self.runner, self.state, self.run, self.workspace, error))
        self.assertEqual(self.writes, [])
        self.assertEqual(self.state["stages"], [])

    def test_foreign_unknown_and_every_unsettled_owner_remain_blocked(self):
        original = copy.deepcopy(self.state)
        for reason in ("foreign", "config", "collection", *recovery.limits.UNSETTLED):
            with self.subTest(reason=reason):
                self.state = copy.deepcopy(original)
                if reason in recovery.limits.UNSETTLED:
                    self.state[reason] = {"ownership": "retained"}
                error = (
                    self.error(workspace=self.workspace / "other")
                    if reason == "foreign"
                    else self.error(config_sha256=None)
                    if reason == "config"
                    else self.error(child_collected=False)
                    if reason == "collection"
                    else self.error()
                )
                self.assertTrue(recovery.settle(self.runner, self.state, self.run, self.workspace, error))
                self.assertIsNone(stop.outcome(self.state))
                self.assertIsNotNone(failure.blocked(self.state))
                evidence = failure.blocked(self.state)["receipt"]["evidence"]
                listing = evidence["listing"]
                self.assertEqual(listing["workspace_binding_matches"], reason not in ("foreign", "config"))
                self.assertEqual(listing["config_sha256"], None if reason == "config" else "a" * 64)
                self.assertEqual(listing["direct_child_collected"], reason != "collection")
                self.assertEqual(listing["descendant_custody"], "unconfirmed")
                self.assertFalse(listing["provider_request_started"])
                self.assertEqual(evidence["unsettled"], [reason] if reason in recovery.limits.UNSETTLED else [])
                self.assertEqual(evidence["binding"]["workspace"], str(self.workspace))
                self.assertEqual(evidence["binding"]["run_dir"], str(self.run))
                if reason in recovery.limits.UNSETTLED:
                    self.assertEqual(self.state[reason], {"ownership": "retained"})
                self.assertEqual(self.state["settings"], original["settings"])

    def test_recovery_does_not_launch_probe_or_accept_source(self):
        product = self.workspace / "source.py"
        product.write_text("preserved source\n")
        with patch.object(subprocess, "run") as child:
            recovery.settle(self.runner, self.state, self.run, self.workspace, self.error())
        child.assert_not_called()
        self.assertEqual(product.read_text(), "preserved source\n")
        recorded = failure.blocked(self.state)["receipt"]["evidence"]["listing"]
        self.assertTrue(recorded["direct_child_collected"])
        self.assertEqual(recorded["descendant_custody"], "unconfirmed")
        self.assertFalse(recorded["provider_request_started"])
        self.assertIsNone(stop.outcome(self.state))

    def test_public_blocked_never_offers_person_or_reconcile_action(self):
        for clean in (False, True):
            with self.subTest(clean=clean):
                self.state.pop("applied_interventions", None)
                self.state["status"] = "RUNNING"
                recovery.settle(self.runner, self.state, self.run, self.workspace, self.error(child_collected=clean))
                public = run_view.view(self.state)
                self.assertFalse(public["done"])
                self.assertIsNone(public["needs"])
                self.assertIsNone(public["next_command"])
                self.assertEqual(public["recovery"]["category"], "blocked")
                self.assertEqual(status_summary.project(public)["state"], "blocked")
                self.assertNotIn("resume", json.dumps(public["explanation"]).lower())

    def test_raw_status_and_settings_cannot_release_blocked_outcome(self):
        import autocode_action_recovery as action

        recovery.settle(self.runner, self.state, self.run, self.workspace, self.error(child_collected=False))
        self.state.update(status="RUNNING", phase="PLANNING")
        self.state.pop("stop_reason", None)
        self.state["settings"]["joint_planning"] = True
        public = run_view.view(self.state)
        self.assertIsNone(public["needs"])
        self.assertIsNone(public["next_command"])
        self.assertEqual(public["recovery"]["category"], "blocked")
        self.assertEqual([row["kind"] for row in public["recovery"]["actions"]], ["inspect"])
        result = action.resume(
            self.runner,
            SimpleNamespace(),
            None,
            self.state,
            self.run / "state.json",
            self.run,
            self.workspace,
            SimpleNamespace(gate=None, information_admitted=False),
        )
        self.assertEqual(result, 2)
        self.assertEqual(self.state["status"], "PAUSED_TRANSPORT_UNVERIFIED")
        self.assertIsNotNone(failure.blocked(self.state))

    def test_existing_3473_mechanism_remains_separate(self):
        import autocode_verification_failure as git_failure

        recovery.settle(self.runner, self.state, self.run, self.workspace, self.error())
        self.assertIsNone(git_failure.presentation(self.state))
        self.assertIsNotNone(failure.presentation(self.state))
        self.assertEqual(failure.blocked(self.state)["receipt"]["evidence"]["operation"], "model_listing")
        self.assertIsNone(stop.outcome(self.state))
