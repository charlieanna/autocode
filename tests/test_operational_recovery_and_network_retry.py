"""Regression tests for operational recovery route switches, PAUSED_STAGE_ABANDONED view, and network retry roles."""

from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_goals as goals
import autocode_network_retry as network_retry
import autocode_operational_admission as operational_admission
import autocode_operational_disposition as disposition
import autocode_resolver_runtime as resolver_runtime
import autocode_route_fit as route_fit
import autocode_run_actions as run_actions
import autocode_run_records as run_records
import autocode_run_view as run_view
import autocode_support as support
import goal_fixtures


class SharedCauseERecoveryCheckedRouteAndViewTests(unittest.TestCase):
    """Checked operational recovery agrees with view.next_command and survives authenticated route switches."""

    def setUp(self):
        scratch = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(scratch).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "README.md").write_text("# Borg\n")
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=T", "-c", "user.email=t@e.t", "add", "README.md"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=T", "-c", "user.email=t@e.t", "commit", "-qm", "init"],
            check=True,
        )
        self.run = self.root / ".autocode" / "runs" / "borg-1"
        (self.run / "iterations" / "001").mkdir(parents=True)

    def test_network_retry_read_only_roles_include_reviewers_and_investigator_but_keep_tester_hold(self):
        for role in ("requirements", "planner", "task_planner", "plan_reviewer", "completion", "stuck_investigator"):
            self.assertIn(role, network_retry.READ_ONLY_ROLES)
        for role in ("builder", "tester"):
            self.assertNotIn(role, network_retry.READ_ONLY_ROLES)

    def test_abandon_then_stage_model_or_resume_flag_preserves_checked_recovery_and_view_next_command(self):
        snap = support.snapshot(self.root)
        bef = self.run / "iterations" / "001" / "builder-01.before.json"
        ev = self.run / "iterations" / "001" / "builder-01.jsonl"
        out = self.run / "iterations" / "001" / "builder-01.json"
        bef.write_text(json.dumps(snap))
        ev.write_text('{"type":"error","error":{"name":"APIError","data":{"message":"unsupported model"}}}\n')
        state = {
            "version": 3,
            "workspace": str(self.root),
            "run_dir": str(self.run),
            "task": "Build borg",
            "task_id": "borg-1",
            "status": "PAUSED_PROVIDER_UNCERTAIN",
            "phase": "PAUSED_OR_BLOCKED",
            "next_stage": "builder",
            "iteration": 1,
            "settings": {
                "engine": "opencode",
                "roles": {
                    "builder": {
                        "engine": "opencode",
                        "model": "openai/gpt-5.3-codex-spark",
                        "reasoning_effort": "medium",
                    },
                    "tester": {"engine": "opencode", "model": "zai-coding-plan/glm-5.3", "reasoning_effort": "high"},
                    "completion": {
                        "engine": "opencode",
                        "model": "zai-coding-plan/glm-5.3",
                        "reasoning_effort": "high",
                    },
                },
                "declared_variants": {"zai-coding-plan/glm-5.3": ["low", "high", "max"]},
                "effort_origins": {"builder": "runner_default"},
                "route_origins": {"builder": "user_explicit"},
                "limits": {"max_cost_usd": 0.0},
            },
            "sessions": {"builder": "ses-1"},
            "stages": [],
            "history": [],
            "user_events": [],
        }
        goal_fixtures.approve_fixture(state, goals)
        state["active_stage"] = {
            "stage": "builder",
            "role": "builder",
            "iteration": 1,
            "output": str(out),
            "events": str(ev),
            "before_ref": str(bef),
            "started_at": "2026-11-16T13:49:30+00:00",
            "finished_at": "2026-11-16T13:49:35+00:00",
            "exit_code": 1,
            "accounted": True,
        }
        state.update(status="PAUSED_PROVIDER_UNCERTAIN", phase="PAUSED_OR_BLOCKED", next_stage="builder")
        route_fit.record_unverified(
            state["settings"], "builder", route_fit.fit(state["settings"], "builder"), stage="builder"
        )
        resolver_runtime.record_operational_exhaustion(
            runner, state, self.run, support.Paused("PAUSED_PROVIDER_UNCERTAIN", "builder exited 1")
        )
        run_records.write_json(self.run / "state.json", state)

        # 1. Before --abandon-stage: public view is blocked with next_command=None (#3682).
        pre_view = run_view.view(state, workspace=self.root, run_dir=self.run)
        self.assertEqual("blocked", pre_view["recovery"]["category"])
        self.assertIsNone(pre_view["next_command"])

        # 2. Run --abandon-stage 001/builder-01: PAUSED_STAGE_ABANDONED now agrees with guard and offers --resume-paused.
        args_ab, parser = runner.cli_args.parse(
            None,
            ["--workspace", str(self.root), "--run-dir", str(self.run), "--abandon-stage", "001/builder-01"],
            runner.opencode.DEFAULT_MODELS,
        )
        self.assertEqual(
            0, run_actions.handle(runner, args_ab, parser, state, self.run / "state.json", self.run, self.root)
        )
        self.assertEqual("PAUSED_STAGE_ABANDONED", state["status"])
        self.assertIsNone(disposition.presentation(state))
        ab_view = run_view.view(state, workspace=self.root, run_dir=self.run)
        self.assertIn("--resume-paused", ab_view.get("next_command") or "")

        # 3. Switch Builder route via --stage-model (both with current _FLAG_ORIGINS and pre-fix missing effort_fits).
        with patch.object(runner.opencode, "check_models", return_value=None):
            args_sm, parser = runner.cli_args.parse(
                None,
                [
                    "--workspace",
                    str(self.root),
                    "--run-dir",
                    str(self.root / ".autocode" / "runs" / "borg-1"),
                    "--stage-model",
                    "builder",
                    "openai/gpt-5.6-terra",
                    "medium",
                ],
                runner.opencode.DEFAULT_MODELS,
            )
            self.assertEqual(
                0, run_actions.handle(runner, args_sm, parser, state, self.run / "state.json", self.run, self.root)
            )
        self.assertEqual("openai/gpt-5.6-terra", state["settings"]["roles"]["builder"]["model"])
        held = disposition.held(state)
        self.assertIsNotNone(disposition.checked_recovery(state, held))
        sm_view = run_view.view(state, workspace=self.root, run_dir=self.run)
        self.assertIn("--resume-paused", sm_view.get("next_command") or "")

        # Pre-fix checkpoint compatibility (event saved without effort_fits in settings_change, like borg c77b492e).
        prefix_state = copy.deepcopy(state)
        prefix_state["user_events"][-1]["settings_change"].pop("effort_fits", None)
        self.assertIsNotNone(disposition.checked_recovery(prefix_state, disposition.held(prefix_state)))

        # Unauthorized budget/limit modification is still strictly refused.
        tampered = copy.deepcopy(state)
        tampered["settings"]["limits"]["max_cost_usd"] = 999.0
        self.assertIsNone(disposition.checked_recovery(tampered, disposition.held(tampered)))
        self.assertFalse(operational_admission.ready(tampered, run_actions.Decided()))

        # 4. --resume-paused succeeds, transitions to RUNNING on the new model, and retires the incident.
        with patch.object(runner.opencode, "check_models", return_value=None):
            args_res, parser = runner.cli_args.parse(
                None,
                ["--workspace", str(self.root), "--run-dir", str(self.run), "--resume-paused", "--no-chat"],
                runner.opencode.DEFAULT_MODELS,
            )
            self.assertIsNone(
                run_actions.handle(runner, args_res, parser, prefix_state, self.run / "state.json", self.run, self.root)
            )
        self.assertEqual("RUNNING", prefix_state["status"])
        self.assertEqual("builder", prefix_state["next_stage"])
        self.assertEqual("openai/gpt-5.6-terra", prefix_state["settings"]["roles"]["builder"]["model"])
        self.assertIsNone(disposition.held(prefix_state))


if __name__ == "__main__":
    unittest.main()
