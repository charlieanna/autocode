"""Retained attempts are historical observations, not evidence that a model still works."""

import copy
import importlib
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

from tests.test_liveness import inspection, metadata


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

    def inspected(self, stage, observed):
        saved = metadata()
        record = self.record(stage, supervision=saved, pid=saved["provider"]["pid"])
        state = self.state(record)
        state["goal_contract"] = {
            "hash": "fixture-contract",
            "revision": 1,
            "body": {"milestones": [{"id": "M1", "objective": "Keep the saved work"}]},
        }
        state["current_task"] = {"id": "M1", "milestone_id": "M1"}
        if stage.endswith("_report_repair"):
            state["stages"] = [
                {
                    **self.record(stage.removesuffix("_report_repair")),
                    "events": str(self.run / "original.jsonl"),
                    "output": str(self.run / "original.json"),
                    "finished_at": "2026-11-15T12:00:01+00:00",
                    "accounted": True,
                    "exit_code": 1,
                }
            ]
        current = {
            "live": inspection(saved),
            "unsupervised": inspection(saved, owner=False),
            "interrupted": inspection(
                saved, owner=False, keeper=False, provider=False, phase="stopped", cause="owner_lost"
            ),
            "stopped": inspection(saved, provider=False, phase="stopped", cause="provider_stopped"),
            "unknown": None,
        }[observed]
        import autocode as runner

        before = copy.deepcopy(state)
        with patch.object(runner.supervision, "observe", return_value=current):
            shown = self.render(state, False)
        self.assertEqual(before, state, "display inspection cannot settle an attempt or change its authority")
        attempt = next(row for row in shown["view"]["usage"]["accounting"]["attempts"] if row["stage"] == stage)
        self.assertTrue(attempt["active"], "unfinished accounting stays unresolved even when processes are dead")
        self.assertIsNone(attempt["exit_code"])
        self.assertIsNone(attempt["finished_at"])
        self.assertFalse(shown["active_stage_finished"])
        self.assertFalse(shown["view"]["done"])
        self.assertFalse(shown["view"]["stage_models"]["changeable"])
        return shown["view"]

    def test_pipeline_working_requires_the_existing_live_observation_for_every_job(self):
        for stage, job in (
            ("requirements_gather", "requirements"),
            ("draft_plan", "planner"),
            ("review_plan", "plan_reviewer"),
            ("builder", "builder"),
            ("tester", "tester"),
            ("review_completion", "completion"),
            ("write_acceptance_tests", "tester"),
            ("requirements_gather_report_repair", "requirements"),
        ):
            for observed in ("live", "unsupervised", "interrupted", "stopped", "unknown"):
                with self.subTest(stage=stage, observed=observed):
                    view = self.inspected(stage, observed)
                    row = next(row for row in view["pipeline"] if row["job"] == job)
                    self.assertEqual("working" if observed == "live" else "ran", row["state"])
                    self.assertNotIn("next", {row["state"] for row in view["pipeline"]})

    def test_progress_headline_and_tasks_use_the_same_live_observation(self):
        for observed in ("live", "unsupervised", "interrupted", "stopped", "unknown"):
            with self.subTest(observed=observed):
                view = self.inspected("builder", observed)
                tasks = [row["state"] for row in view["progress"]["tasks"]["items"]]
                if observed == "live":
                    self.assertEqual("Builder working", view["progress"]["headline"])
                    self.assertIn("working", tasks)
                else:
                    self.assertNotIn("working", view["progress"]["headline"].casefold())
                    self.assertNotIn("attempt ended", view["progress"]["headline"].casefold())
                    self.assertNotIn("working", tasks)

    def test_model_refusal_wording_does_not_turn_unresolved_into_live_or_finished(self):
        for observed in ("live", "unsupervised", "interrupted", "stopped", "unknown"):
            with self.subTest(observed=observed):
                reason = self.inspected("builder", observed)["stage_models"]["reason"]
                self.assertEqual(observed == "live", "working" in reason.casefold())
                self.assertNotIn("attempt ended", reason.casefold())

    def test_chat_working_notice_uses_the_same_owned_activity_observation(self):
        for observed in ("live", "unsupervised", "interrupted", "stopped", "unknown"):
            with self.subTest(observed=observed):
                notice = self.inspected("builder", observed)["chat"]["notice"]
                self.assertEqual("Working on your request…" if observed == "live" else None, notice)

    def test_partial_handoff_keeps_the_existing_bound_supervision_observation(self):
        import autocode_run_view as run_view

        saved = metadata()
        state = self.state(self.record("builder", supervision=saved))
        self.assertNotIn("pid", state["active_stage"])
        before = copy.deepcopy(state)
        view = run_view.view(state, liveness=inspection(saved), run_dir=self.run, workspace=self.root)
        row = next(row for row in view["pipeline"] if row["job"] == "builder")
        self.assertEqual("working", row["state"])
        self.assertEqual("Working on your request…", view["chat"]["notice"])
        self.assertIn("working", view["stage_models"]["reason"])
        self.assertEqual(before, state)

    def test_packaged_public_projection_keeps_interruption_display_only(self):
        packaged = importlib.import_module("tools.autocode_run_view")
        saved = metadata()
        state = self.state(self.record("builder", supervision=saved), status="PAUSED_UNCERTAIN_STAGE")
        before = copy.deepcopy(state)
        current = inspection(saved, owner=False, keeper=False, provider=False, phase="stopped", cause="owner_lost")
        view = packaged.view(state, liveness=current, run_dir=self.run, workspace=self.root)
        row = next(row for row in view["pipeline"] if row["job"] == "builder")
        self.assertEqual("ran", row["state"])
        self.assertNotIn("working", view["stage_models"]["reason"].casefold())
        self.assertTrue(view["usage"]["accounting"]["attempts"][0]["active"])
        self.assertIsNone(view["usage"]["accounting"]["attempts"][0]["exit_code"])
        self.assertEqual(before, state)

    def test_unstarted_slot_without_a_launch_decision_is_neither_working_nor_next(self):
        for stage in ("requirements_gather", "builder", "write_acceptance_tests"):
            with self.subTest(stage=stage):
                state = {"status": "RUNNING", "active_stage": {"stage": stage}, "next_stage": "tester"}
                rows = pipeline.project(state, {"done": False, "liveness": {"kind": "unknown"}})
                self.assertEqual({"pending"}, {row["state"] for row in rows})

    def test_public_pre_exec_slot_does_not_invent_a_launch_from_its_start_timestamp(self):
        import autocode_run_view as run_view

        for stage in ("requirements_gather", "builder", "write_acceptance_tests"):
            for prepared in ({"stage": stage}, self.record(stage)):
                with self.subTest(stage=stage, timestamp=bool(prepared.get("started_at"))):
                    state = self.state(prepared)
                    before = copy.deepcopy(state)
                    view = run_view.view(state)
                    self.assertEqual({"pending"}, {row["state"] for row in view["pipeline"]})
                    self.assertFalse(view["stage_decisions"], "a chosen pre-exec slot is not a launched stage")
                    self.assertEqual(before, state)

    def test_public_launch_evidence_keeps_typed_pid_and_settled_legacy_distinct(self):
        import autocode_run_view as run_view

        variants = [({"pid": pid}, type(pid) is int and pid > 0) for pid in (None, True, False, 0, -1, "103", 103)]
        variants.extend(
            ({"supervision": {"provider": {"pid": pid}}}, type(pid) is int and pid > 0)
            for pid in (None, True, False, 0, -1, "103", 103)
        )
        variants.extend([({"accounted": True}, True), ({"finished_at": "2026-11-15T12:00:01Z"}, True)])
        for extra, launched in variants:
            with self.subTest(extra=extra):
                state = self.state(self.record("builder", **extra))
                before = copy.deepcopy(state)
                view = run_view.view(state, run_dir=self.run, workspace=self.root)
                row = next(row for row in view["pipeline"] if row["job"] == "builder")
                self.assertEqual("ran" if launched else "pending", row["state"])
                self.assertEqual(launched, bool(view["stage_decisions"]))
                self.assertFalse(view["stage_models"]["changeable"])
                self.assertEqual(before, state)

    def test_foreign_or_saved_liveness_cannot_become_current_activity(self):
        import autocode_run_view as run_view

        saved = metadata()
        state = self.state(self.record("builder", supervision=saved))
        foreign = metadata()
        foreign["nonce"] = "b" * 32
        foreign["provider"]["pid"] = 999
        state["liveness"] = inspection(saved)
        for current in (None, inspection(foreign)):
            with self.subTest(current=current):
                before = copy.deepcopy(state)
                view = run_view.view(state, liveness=current, run_dir=self.run, workspace=self.root)
                self.assertEqual("unknown", view["liveness"]["kind"])
                row = next(row for row in view["pipeline"] if row["job"] == "builder")
                self.assertEqual("ran", row["state"], "its recorded own PID proves launch, not current activity")
                self.assertNotIn("working", view["stage_models"]["reason"].casefold())
                self.assertEqual(before, state)

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
                        self.assertEqual("pending" if name == "prepared" else "ran", row["state"])
                        if settled:
                            attempt = next(
                                row for row in view["usage"]["accounting"]["attempts"] if row["stage"] == stage
                            )
                            self.assertFalse(attempt["active"])
                            self.assertEqual(extra.get("exit_code"), attempt["exit_code"])
                            self.assertNotIn("working", view["progress"]["headline"].lower())
                            self.assertNotIn("working", view["stage_models"]["reason"].lower())
                            self.assertIn("needs recovery", view["stage_models"]["reason"].lower())
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
                    "progress": {"headline": "Requirements attempt ended", "stage": "Requirements"},
                }
                self.assertEqual(["Requirements attempt ended"], chat_progress.project(state, described))
                described["liveness"] = {"kind": "stopped"}
                self.assertEqual(["Requirements attempt ended"], chat_progress.project(state, described))

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
            "Builder: activity could not be confirmed.",
            progress.progress(state, accepted=(), stage="Builder", needs=None)["headline"],
        )
        self.assertEqual(
            "Builder working",
            progress.progress(
                state,
                accepted=(),
                stage="Builder",
                needs=None,
                liveness={"kind": "supervised", "receipt": {"provider": {"pid": 103}}},
            )["headline"],
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
        self.assertIn("needs recovery", retained)
        self.assertEqual(
            "Builder attempt ended", progress.progress(state, accepted=(), stage="Builder", needs=None)["headline"]
        )
        state["current_task"] = {"milestone_id": "one"}
        state["goal_contract"] = {"body": {"milestones": [{"id": "one", "objective": "Keep the failed work"}]}}
        tasks = progress.progress(state, accepted=(), stage="Builder", needs=None)["tasks"]["items"]
        self.assertNotIn("working", {row["state"] for row in tasks})
        viewed = {"done": False, "stage_decisions": [{"job": "builder", "decision": "run"}]}
        self.assertEqual(
            "ran", next(row for row in pipeline.project(state, viewed) if row["job"] == "builder")["state"]
        )
