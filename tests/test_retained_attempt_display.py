"""Retained attempts are historical observations, not evidence that a model still works."""

import copy
import io
import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_chat_activity as activity
import autocode_chat_log as chat_log
import autocode_chat_progress as chat_progress
import autocode_progress_view as progress
import autocode_run_stage_models as models
import autocode_stage_pipeline as pipeline


class RetainedAttemptDisplayTests(unittest.TestCase):
    def setUp(self):
        self.spawn = self.enterContext(patch.object(subprocess, "Popen", side_effect=AssertionError("no process")))
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        self.run = self.root / "run"
        self.run.mkdir()

    def tearDown(self):
        self.spawn.assert_not_called()

    def record(self, stage="requirements_gather", **extra):
        return {
            "stage": stage,
            "role": "requirements" if stage == "requirements_gather" else "builder",
            "model": "fixture/model",
            "iteration": 1,
            "started_at": "2026-11-15T12:00:00+00:00",
            "events": str(self.run / "attempt.jsonl"),
            "output": str(self.run / "attempt.json"),
            **extra,
        }

    def state(self, record, status="RUNNING"):
        return {
            "status": status,
            "iteration": 1,
            "sessions": {},
            "next_stage": "tester",
            "stages": [],
            "active_stage": record,
            "settings": {"engine": "codex", "roles": {"builder": {"model": "fixture/model"}}},
        }

    def render(self, state, result_ready):
        import autocode as runner
        import autocode_status_command as command

        (self.run / "state.json").write_text(json.dumps(state))
        output = io.StringIO()
        with (
            patch.object(
                command.controller_owner,
                "inspect",
                return_value={
                    "kind": "boundary",
                    "reason": "fixture",
                    "receipt": None,
                    "checked": True,
                    "alive": False,
                },
            ),
            patch.object(runner.processes, "recorded_worker_state", return_value={"checked": False, "alive": None}),
            patch.object(runner, "stage_completed", return_value=result_ready),
            patch.object(runner.milestones, "summary", return_value={"accepted_milestones": []}),
            patch.object(runner.dependency, "export", return_value={}),
            patch.object(runner, "intervention_metadata", return_value={}),
            patch.object(runner.resolver_human, "projection", return_value={}),
            redirect_stdout(output),
            redirect_stderr(io.StringIO()),
        ):
            command.render(
                runner,
                state,
                SimpleNamespace(inspect_evidence=False, run_dir=str(self.run), engine=None, explain=False),
                self.root,
                self.run,
            )
        return json.loads(output.getvalue())

    def test_public_status_separates_record_settlement_from_success_and_action_authority(self):
        variants = (
            ("failed", {"finished_at": "2026-11-15T12:00:01+00:00", "accounted": True, "exit_code": 1}, False),
            ("timeout", {"finished_at": "2026-11-15T12:00:01+00:00", "timed_out": True, "exit_code": -15}, False),
            ("interrupted", {"finished_at": "2026-11-15T12:00:01+00:00", "interrupted": True, "exit_code": -2}, False),
            ("rejected", {"finished_at": "2026-11-15T12:00:01+00:00", "rejected": True, "exit_code": 0}, True),
            ("successful-report", {"finished_at": "2026-11-15T12:00:01+00:00", "exit_code": 0}, True),
            ("legacy-accounted", {"accounted": True, "exit_code": 1}, False),
            ("cleanup-unknown", {"finished_at": "2026-11-15T12:00:01+00:00", "exit_code": None}, False),
            ("prepared", {"accounted": False}, False),
            ("open", {"accounted": False, "pid": 12345}, False),
        )
        for stage in ("requirements_gather", "builder"):
            for status in ("RUNNING", "PAUSED_INVALID_OUTPUT", "WAITING_FOR_USER"):
                for name, extra, ready in variants:
                    with self.subTest(stage=stage, status=status, variant=name):
                        state = self.state(self.record(stage, **extra), status)
                        before = copy.deepcopy(state)
                        import autocode_run_view as run_view

                        authority = run_view.view(state, run_dir=self.run, workspace=self.root)
                        shown = self.render(state, ready)
                        view = shown["view"]
                        self.assertEqual(
                            ready, shown["active_stage_finished"], "successful report readiness is unchanged"
                        )
                        self.assertEqual(before, state, "status must not reconcile or apply an attempt")
                        for field in ("needs", "next_command", "routes", "settings"):
                            if field in authority:
                                self.assertEqual(authority[field], view[field], field)
                        self.assertEqual(authority["chat"]["input"], view["chat"]["input"])
                        self.assertFalse(view["stage_models"]["changeable"])
                        job = "requirements" if stage == "requirements_gather" else "builder"
                        row = next(row for row in view["pipeline"] if row["job"] == job)
                        settled = name not in ("prepared", "open")
                        self.assertEqual("ran" if settled else "working", row["state"])
                        if settled:
                            attempt = next(
                                row for row in view["usage"]["accounting"]["attempts"] if row["stage"] == stage
                            )
                            self.assertFalse(attempt["active"])
                            self.assertEqual(extra.get("exit_code"), attempt["exit_code"])
                            self.assertNotIn("working", view["progress"]["headline"].lower())
                            self.assertNotIn("working", view["stage_models"]["reason"].lower())
                            self.assertIn("retained", view["stage_models"]["reason"].lower())
                            self.assertNotIn("next", {row["state"] for row in view["pipeline"]})
                        else:
                            self.assertEqual(models.BUSY, view["stage_models"]["reason"])

    def test_retained_records_do_not_generate_live_activity_heartbeat_or_model_start_notes(self):
        public = {"status": "RUNNING", "needs": {"kind": "continue"}, "liveness": {"kind": "supervised"}}
        for extra in (
            {"finished_at": "2026-11-15T12:00:01+00:00", "exit_code": 1},
            {"accounted": True, "exit_code": -15},
        ):
            with self.subTest(extra=extra):
                state = self.state(self.record(**extra))
                with (
                    patch.object(activity, "read", side_effect=AssertionError("no live tail")),
                    patch.object(chat_log, "_attempt", side_effect=AssertionError("no live log")),
                    patch.object(chat_log, "_start_note", side_effect=AssertionError("no start note")),
                ):
                    self.assertIsNone(activity.events_file(state, public, self.run))
                    self.assertEqual([], activity.project(state, public, self.run))
                    self.assertIsNone(activity.last_output(state, public, self.run))
                    self.assertEqual([], chat_log.project(state, public, self.run, supervised=lambda _: True))
                described = {
                    **public,
                    "progress": {"headline": "Requirements attempt retained", "stage": "Requirements"},
                }
                self.assertEqual(["Requirements attempt retained"], chat_progress.project(state, described))
                described["liveness"] = {"kind": "stopped"}
                self.assertEqual(["Requirements attempt retained"], chat_progress.project(state, described))

    def test_parallel_worker_log_uses_the_same_record_state_before_live_supervision(self):
        child_run = self.root / "worker"
        child_run.mkdir()
        state = {
            "workspace": str(self.root),
            "orchestration_batch": {
                "status": "BUILDING",
                "workers": [{"status": "RUNNING", "run_dir": str(child_run), "milestone_id": "one"}],
            },
        }
        public = {"status": "RUNNING", "needs": {"kind": "continue"}}
        child = {"active_stage": self.record("builder", accounted=True, exit_code=1)}
        (child_run / "state.json").write_text(json.dumps(child))
        with patch.object(chat_log, "_attempt", side_effect=AssertionError("settled child is not working")):
            supervised = unittest.mock.Mock(side_effect=AssertionError("settled child has no live activity"))
            self.assertEqual([], chat_log.project(state, public, self.run, supervised=supervised))
            supervised.assert_not_called()

    def test_direct_display_keeps_open_and_successful_report_controls_distinct(self):
        state = self.state(self.record("builder"))
        self.assertEqual(
            "Builder working", progress.progress(state, accepted=(), stage="Builder", needs=None)["headline"]
        )
        self.assertEqual(
            "Builder finished · Next: Tester",
            progress.progress(state, accepted=(), stage="Builder", needs=None, finished=True, following="Tester")[
                "headline"
            ],
        )
        refusal = models.refusal(state, "builder", "fixture/other", "high")
        state["active_stage"].update(accounted=True, exit_code=1)
        retained = models.refusal(state, "builder", "fixture/other", "high")
        self.assertIsNotNone(refusal)
        self.assertIsNotNone(retained)
        self.assertNotEqual(refusal, retained)
        self.assertIn("retained", retained)
        self.assertEqual(
            "Builder attempt retained", progress.progress(state, accepted=(), stage="Builder", needs=None)["headline"]
        )
        state["current_task"] = {"milestone_id": "one"}
        state["goal_contract"] = {"body": {"milestones": [{"id": "one", "objective": "Keep the failed work"}]}}
        tasks = progress.progress(state, accepted=(), stage="Builder", needs=None)["tasks"]["items"]
        self.assertNotIn("working", {row["state"] for row in tasks})
        viewed = {"done": False, "stage_decisions": [{"job": "builder", "decision": "run"}]}
        self.assertEqual(
            "ran", next(row for row in pipeline.project(state, viewed) if row["job"] == "builder")["state"]
        )
