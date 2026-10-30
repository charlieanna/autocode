"""Public native cleanup advice follows the actual pure request predicate.

All states and TaskRun transport replies are controlled. No original native
journal, saved run, provider or recovery action is read or executed.
"""

import contextlib
import copy
import io
import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_kilo_native as kilo
import autocode_native_stage_recovery as request_policy
import autocode_progress_view as progress
import autocode_recovery_view as recovery
import autocode_run_view as run_view
import autocode_status_command as status_command
import autocode_stop_explanations as explanations
import autocode_taskrun as taskrun
import autocode_visual_profile as profile


class NativeCleanupGuidanceTests(unittest.TestCase):
    def test_ordinary_cleanup_recovery_hint_is_not_a_native_display_marker(self):
        state = self.state(native=False)
        ordinary_need = {
            "kind": "resume",
            "recovery_hint": "ordinary saved source inspection advice",
            "action": "--resume-paused",
        }
        text = explanations.explain("PAUSED_PROCESS_CLEANUP", needs=ordinary_need)
        self.assertEqual(
            explanations.TABLE["PAUSED_PROCESS_CLEANUP"],
            (
                text["what_it_means"],
                text["what_the_command_does"],
            ),
        )
        self.assertNotIn("Native planning", text["explanation"])
        shown_progress = progress.progress(state, accepted=(), needs=ordinary_need, finished=True, stage="Reviewer")
        self.assertEqual("inspect the pause, then use --resume-paused", shown_progress["needs_you"])
        self.assertNotIn(ordinary_need["recovery_hint"], shown_progress["line"])
        self.assertNotIn("native_cleanup", run_view.needs(state))
        for timed_out in (False, True):
            self.assertIs(True, run_view.needs(self.state(timed_out=timed_out))["native_cleanup"])

    def test_public_explanation_matches_held_or_conditional_native_need(self):
        for timed_out in (False, True):
            with self.subTest(timed_out=timed_out):
                state = self.state(timed_out=timed_out)
                shown = run_view.view(state)
                # The status renderer composes this exact pure progress API
                # with its already computed needs; no runtime inspection here.
                shown["progress"] = progress.progress(
                    state,
                    accepted=(),
                    needs=shown["needs"],
                    finished=True,
                    stage="Plan Reviewer",
                )
                explanation = shown["explanation"]
                self.assertEqual(shown["needs"]["recovery_hint"], explanation["what_the_command_does"])
                self.assertEqual(shown["needs"]["recovery_hint"], shown["progress"]["needs_you"])
                self.assertIn(shown["needs"]["recovery_hint"], shown["progress"]["line"])
                self.assertNotIn("--resume-paused", explanation["explanation"])
                self.assertNotIn("inspect the pause, then", shown["progress"]["line"])
                public_text = io.StringIO()
                with contextlib.redirect_stdout(public_text):
                    status_command.render(
                        SimpleNamespace(run_view=run_view),
                        state,
                        SimpleNamespace(explain=True),
                        Path("/controlled"),
                        Path("/controlled/nonexistent-run"),
                    )
                self.assertIn(shown["needs"]["recovery_hint"], public_text.getvalue())
                self.assertNotIn("--resume-paused", public_text.getvalue())
                if timed_out:
                    self.assertIn("must still authenticate", explanation["explanation"])
                    self.assertIn("separate action after acceptance", explanation["explanation"])
                else:
                    self.assertNotIn("--abandon-stage", explanation["explanation"])
                    self.assertNotIn("--abandon-stage", public_text.getvalue())
                    self.assertIn("ordinary resume is unavailable", explanation["explanation"])
        ordinary = run_view.view(self.state(native=False))["explanation"]
        self.assertEqual(explanations.TABLE["PAUSED_PROCESS_CLEANUP"][1], ordinary["what_the_command_does"])
        untouched = explanations.explain("PAUSED_PROVIDER_TIMEOUT", needs={"recovery_hint": "ignore unrelated hint"})
        self.assertEqual(explanations.TABLE["PAUSED_PROVIDER_TIMEOUT"][1], untouched["what_the_command_does"])

    def state(self, *, timed_out=False, native=True):
        sdk = ".autocode/controlled-kilo"
        intent = {
            "version": 1,
            "mode": kilo.MODE,
            "opencode_version": profile.VERSION,
            "model": kilo.MODEL,
            "reasoning_effort": "high",
            "sdk_path": ".autocode/controlled-opencode",
            "sdk_manifest_sha256": "a" * 64,
            "transport_identity_sha256": "b" * 64,
            "executable_sha256": "c" * 64,
            "max_requests": 8,
            "image_limits": dict(profile.IMAGE_BOUNDS),
            "kilo": {
                "version": kilo.VERSION,
                "release_version": kilo.RELEASE_VERSION,
                "sdk_path": sdk,
                "sdk_manifest_sha256": "d" * 64,
                "transport_identity_sha256": "e" * 64,
                "executable_sha256": "f" * 64,
                "source_hooks": {"path": sdk + "/source-hooks.cjs", "sha256": "1" * 64},
                "build_acceptance": {
                    key: {"path": sdk + "/" + key + ".json", "sha256": "2" * 64} for key in ("result", "root", "peer")
                },
            },
        }
        task = "unchanged complete operator scope"
        if native:
            task += "\n" + profile.MARKER + json.dumps(intent, sort_keys=True)
            self.assertEqual(intent, profile.initial_intent(task))
        return {
            "version": 3,
            "workspace": "/controlled",
            "task": task,
            "status": "PAUSED_PROCESS_CLEANUP",
            "stop_reason": "original keeper process stopped",
            "phase": "PLANNING",
            "iteration": 1,
            "next_stage": "plan_finalize",
            "current_task": None,
            "goal_contract": {"approval_status": "draft", "body": {}},
            "settings": {
                "planning_flow": "v2",
                "roles": {
                    "glm": {"model": "controlled/glm", "reasoning_effort": "high"},
                    "astra": {"model": "controlled/mimo", "reasoning_effort": "high"},
                    "sol": {"model": kilo.MODEL, "reasoning_effort": "high"},
                },
                "limits": {
                    "max_seconds": 7200,
                    "stage_timeout_seconds": 900,
                    "idle_timeout_seconds": 300,
                    "tool_timeout_seconds": 600,
                },
            },
            "stages": [],
            "history": [],
            "active_stage": {
                "iteration": 1,
                "stage": "plan_finalize",
                "role": "astra",
                "engine": "opencode",
                "output": "/controlled/run/iterations/001/plan-finalize-01.json",
                "pid": 301,
                "exit_code": -15,
                "finished_at": "2026-10-15T03:57:12Z",
                "accounted": True,
                "timed_out": timed_out,
                "timeout_kind": "idle",
                "timeout_reason": "original inactivity deadline",
                "supervision": {"original_controlled_identity": True},
            },
        }

    def assert_held(self, state):
        before = copy.deepcopy(state)
        shown = run_view.view(state)
        need = shown["needs"]
        self.assertEqual("recover_source", need["kind"])
        self.assertIsNone(need["action"])
        self.assertNotIn("abandon_stage", need)
        self.assertIn("ordinary resume is unavailable", need["recovery_hint"])
        self.assertFalse({"abandon", "resume"} & {row["kind"] for row in shown["recovery"]["actions"]})
        self.assertEqual(before, state)
        return shown

    def test_current_native_cleanup_without_original_timeout_is_inspection_only(self):
        self.assert_held(self.state())

    def test_eligible_cancellation_offers_only_exact_authentication_subject_request(self):
        for kind in ("idle", "tool", "stage"):
            with self.subTest(kind=kind):
                state = self.state(timed_out=True)
                state["active_stage"]["timeout_kind"] = kind
                before = copy.deepcopy(state)
                with patch.object(request_policy, "request", wraps=request_policy.request) as shared:
                    shown = run_view.view(state)
                shared.assert_called()
                self.assertEqual("001/plan-finalize-01", shown["needs"]["abandon_stage"])
                self.assertEqual("--abandon-stage 001/plan-finalize-01", shown["needs"]["action"])
                actions = shown["recovery"]["actions"]
                request = next(row for row in actions if row["kind"] == "abandon")
                self.assertIn("must still authenticate", request["effect"])
                self.assertNotIn("resume", [row["kind"] for row in actions])
                self.assertEqual(before, state)

    def test_stale_or_forged_public_need_cannot_offer_native_cleanup_action(self):
        state = self.state()
        card = recovery.project(state, {"kind": "resume", "abandon_stage": "001/plan-finalize-01"})
        self.assertEqual(["inspect", "feedback"], [row["kind"] for row in card["actions"]])
        self.assertIn("ordinary resume is unavailable", card["what_happened"])

    def test_unknown_identity_or_ineligible_frontier_stays_held(self):
        for key, value in (
            ("pid", None),
            ("exit_code", None),
            ("exit_code", 0),
            ("accounted", False),
            ("timeout_kind", "monitor"),
            ("timeout_reason", ""),
            ("supervision", {}),
            ("supervision_errors", ["unverified"]),
            ("cleanup_error", "uncollected"),
            ("report_only", True),
            ("interrupted", True),
            ("stage", "terra"),
        ):
            with self.subTest(key=key, value=value):
                state = self.state(timed_out=True)
                state["active_stage"][key] = value
                self.assert_held(state)
        state = self.state(timed_out=True)
        state["goal_contract"]["approval_status"] = "approved"
        self.assert_held(state)
        state = self.state(timed_out=True)
        state["active_stage"]["stage"] = state["next_stage"] = "glm_revise"
        self.assert_held(state)  # A legacy stage is outside the actual V2 caller.

    def test_malformed_native_directive_cannot_fall_back_to_ordinary_recovery(self):
        for timed_out in (False, True):
            with self.subTest(timed_out=timed_out):
                state = self.state(timed_out=timed_out)
                state["task"] = "original input\n" + profile.MARKER + "{}"
                self.assert_held(state)

    def test_ordinary_cleanup_keeps_original_recovery_guidance(self):
        state = self.state(native=False)
        shown = run_view.view(state)
        # Setting aside the uncertain attempt is the core's own recovery, so no person is asked.
        self.assertEqual("continue", shown["needs"]["kind"])
        self.assertEqual("--abandon-stage 001/plan-finalize-01 then --resume-paused", shown["needs"]["action"])
        self.assertIn("abandon", [row["kind"] for row in shown["recovery"]["actions"]])

    def test_native_timeout_output_limit_and_reconciled_resume_guidance_stay_unchanged(self):
        for status in ("PAUSED_PROVIDER_TIMEOUT", "PAUSED_OUTPUT_CAP"):
            with self.subTest(status=status):
                state = self.state(timed_out=status == "PAUSED_PROVIDER_TIMEOUT")
                state["status"] = status
                state["active_stage"]["exit_code"] = -15 if status == "PAUSED_PROVIDER_TIMEOUT" else 0
                need = run_view.needs(state)
                self.assertEqual("--abandon-stage 001/plan-finalize-01 then --resume-paused", need["action"])
        state = self.state()
        state.update(status="PAUSED_STAGE_ABANDONED", active_stage=None)
        shown = run_view.view(state)
        self.assertEqual("resume", shown["needs"]["kind"])
        self.assertIn("resume", [row["kind"] for row in shown["recovery"]["actions"]])

    def test_public_taskrun_status_preserves_held_advice_without_advancing(self):
        state = self.state()
        before = copy.deepcopy(state)
        calls = []

        def public_status(argv, **options):
            calls.append((argv, options))
            self.assertIn("--status", argv)
            self.assertNotIn("--resume-paused", argv)
            self.assertNotIn("--abandon-stage", argv)
            self.assertFalse(options.get("advancing", False))
            return subprocess.CompletedProcess(argv, 0, json.dumps({"view": run_view.view(state)}), "")

        run = taskrun.TaskRun(Path("/controlled"), Path("/controlled/run"), command=("controlled-cli",))
        with patch.object(taskrun, "run_process", side_effect=public_status):
            shown = run.status()
        self.assertEqual(1, len(calls))
        self.assertEqual("recover_source", shown["needs"]["kind"])
        self.assertIsNone(shown["needs"]["action"])
        self.assertEqual(before, state)
