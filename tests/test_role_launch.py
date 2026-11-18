"""The launch steps autocode.run_role composes (#2753)."""

from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import autocode_role_launch as role_launch
from autocode_support import Paused


class ReplayContextTests(unittest.TestCase):
    def test_first_review_and_its_repair_bind_the_current_draft_snapshot(self):
        for report_only in (False, True):
            with (
                self.subTest(report_only=report_only),
                patch.object(role_launch.failure_correction, "snapshot", return_value="draft-failure-v1:current"),
            ):
                context = role_launch.replay_context(
                    {},
                    stage="review_plan_report_repair" if report_only else "review_plan",
                    iteration=1,
                    report_only=report_only,
                    original_stage="review_plan",
                    changed_code_proof=None,
                )
                self.assertEqual("draft-failure-v1:current", context["draft_inference_snapshot"])

    def test_first_review_without_a_draft_snapshot_keeps_the_legacy_context_shape(self):
        with patch.object(role_launch.failure_correction, "snapshot", return_value=None):
            context = role_launch.replay_context(
                {},
                stage="review_plan",
                iteration=1,
                report_only=False,
                original_stage=None,
                changed_code_proof=None,
            )
        self.assertNotIn("draft_inference_snapshot", context)

    def test_the_context_names_the_launch_replay_matches(self):
        state = {
            "turns": [{}, {}],
            "current_task": {"id": "T-1", "milestone_id": "M-1"},
            "goal_contract": {"hash": "h"},
            "criteria_revision": "c",
            "stages": [{"replay": {}}, {"replay": None}, {}],
            "regression_proof": {"path": "proof.json"},
        }
        context = role_launch.replay_context(
            state,
            stage="tester_report_repair",
            iteration=3,
            report_only=True,
            original_stage="tester",
            changed_code_proof="p",
        )
        self.assertEqual(
            {
                "stage": "tester_report_repair",
                "iteration": 3,
                "turn": 3,
                "milestone_id": "M-1",
                "task_id": "T-1",
                "contract_hash": "h",
                "criteria_revision": "c",
                "report_only": True,
                "original_stage": "tester",
                "consumed": 1,
                "changed_code_proof": "p",
                "regression_proof_path": "proof.json",
            },
            context,
        )

    def test_a_first_launch_names_no_original_stage(self):
        context = role_launch.replay_context(
            {}, stage="builder", iteration=1, report_only=False, original_stage="builder", changed_code_proof=None
        )
        self.assertEqual(
            (1, None, 0, None), (context["turn"], context["original_stage"], context["consumed"], context["task_id"])
        )


class NativeEnvironmentTests(unittest.TestCase):
    def setUp(self):
        patch.object(role_launch.agent_env, "scrubbed", return_value={"PATH": "/bin"}).start()
        patch.object(role_launch.output_policy, "environment", return_value={"CAP": "1"}).start()
        self.addCleanup(patch.stopall)

    def test_a_report_file_tool_gets_a_capture_context_bound_to_the_source(self):
        adapter = SimpleNamespace(OUTPUT="report_file")
        environment, capture = role_launch.native_environment(
            {"settings": {}}, "/w", "e.jsonl", adapter, Path("out.json"), {"revision": "r1"}
        )
        self.assertEqual(("out.json", "r1"), (capture["attempt"], capture["source_revision"]))
        self.assertEqual(32, len(capture["nonce"]))
        self.assertEqual(capture, json.loads(environment["AUTOCODE_CAPTURE_CONTEXT"]))
        self.assertEqual(("/bin", "1"), (environment["PATH"], environment["CAP"]))

    def test_an_event_stream_tool_gets_no_capture_context(self):
        environment, capture = role_launch.native_environment(
            {"settings": {}}, "/w", "e.jsonl", SimpleNamespace(), Path("out.json"), {"revision": "r1"}
        )
        self.assertIsNone(capture)
        self.assertEqual({"PATH": "/bin", "CAP": "1"}, environment)


class PromptDecorationTests(unittest.TestCase):
    def test_opencode_schema_and_containment_keep_exact_current_proof_binding(self):
        for stage, containment, required, current in (
            ("tester", True, True, True),
            ("tester", True, True, False),
            ("tester", True, False, False),
            ("tester", False, True, False),
            ("builder", True, True, False),
        ):
            with self.subTest(stage=stage, containment=containment, required=required, current=current):
                state, schema = {}, {"type": "object"}
                adapter = SimpleNamespace(prompt_for_schema=MagicMock(return_value="schema prompt"))
                worker = {"tool_containment": {"scratch": "/scratch"}} if containment else {}
                with (
                    patch.object(role_launch.regression, "required", return_value=required),
                    patch.object(role_launch.regression, "complete", return_value=current) as complete,
                    patch.object(role_launch.regression, "handoff", return_value={"proof": "current"}) as handoff,
                    patch.object(role_launch.source_scope, "snapshot", return_value={"revision": "revision"}),
                    patch.object(role_launch.provider_launch, "containment_prompt", return_value="contained") as render,
                ):
                    result = role_launch.decorate_prompt(
                        state, stage, "/workspace", adapter, "original", schema, "events", worker
                    )
                self.assertEqual("contained", result)
                adapter.prompt_for_schema.assert_called_once_with("original", schema, "events")
                if stage == "tester" and containment and required:
                    complete.assert_called_once_with(state, "revision", require_changed_code=False)
                else:
                    complete.assert_not_called()
                self.assertEqual(current, handoff.called)
                render.assert_called_once_with(
                    "schema prompt",
                    worker,
                    stage=stage,
                    regression_proof_current=current,
                    regression_handoff={"proof": "current"} if current else None,
                    regression_context_root=None,
                )


class ReviewerFallbackTests(unittest.TestCase):
    def state(self):
        return {
            "planning": {
                "reviewer_route_fallbacks": [
                    {"id": "g-0", "consumed": True, "binding": {"selected_route": {"role": "other"}}},
                    {"id": "g-1", "consumed": True, "binding": {"selected_route": {"role": "planner"}}},
                ]
            }
        }

    def test_the_consumed_grant_is_named_on_the_record(self):
        record = {}
        with patch.object(role_launch.reviewer_fallback, "admit", return_value={"role": "planner"}):
            role_launch.admit_reviewer_fallback(self.state(), "/run", "/w", "review_plan", {"role": "planner"}, record)
        self.assertEqual("g-1", record["reviewer_fallback_grant"])

    def test_a_changed_fallback_pauses_before_the_provider(self):
        record = {}
        with patch.object(role_launch.reviewer_fallback, "admit", return_value={"role": "other"}):
            with self.assertRaises(Paused) as caught:
                role_launch.admit_reviewer_fallback(
                    self.state(), "/run", "/w", "review_plan", {"role": "planner"}, record
                )
        self.assertEqual("PAUSED_REVIEWER_FALLBACK", caught.exception.status)
        self.assertNotIn("reviewer_fallback_grant", record)


class ActivityReporterTests(unittest.TestCase):
    def test_every_observation_is_saved_and_one_line_is_printed_per_change(self):
        save, state, record = MagicMock(), {}, {}
        reporter = role_launch.activity_reporter(
            state,
            record,
            stage="builder",
            model="m",
            idle_limit=300,
            tool_timeout=1800,
            stage_timeout=None,
            started=0,
            state_path="state.json",
            save=save,
            clock=lambda: "now",
        )
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            reporter({"activity": "thinking"})
            reporter({"activity": "thinking"})
            reporter({"activity": "stalled", "detail": "no output"})
        self.assertEqual(3, save.call_count)
        lines = printed.getvalue().splitlines()
        self.assertEqual(2, len(lines))
        self.assertIn("thinking; model=m;", lines[0])
        self.assertIn("idle=0s/300; tool=0s/1800; stage_limit=off", lines[0])
        self.assertTrue(lines[1].endswith("; no output"))
        self.assertEqual(("now", None), (record["activity"]["observed_at"], record["activity"]["stage_limit_seconds"]))


class RetryStartupBudgetTests(unittest.TestCase):
    def state(self, stage="write_acceptance_tests", **authorization):
        return {
            "settings": {
                "limits": {"stage_timeout_seconds": 300, "idle_timeout_seconds": 300},
                "budget_origins": {"idle_timeout_seconds": "runner_default"},
            },
            "job_failure": {"stage": stage, "job_retry_token": "jr:current", "retry_stage_timeout_seconds": 600},
            "job_retry_authorization": {
                "stage": stage,
                "token": "jr:current",
                "consumed": False,
                **authorization,
            },
            # The previous attempt did useful work before its hard deadline, rather than starting silently.
            "recovery_context": {"stage": stage, "stall_phase": "during_reasoning_or_output"},
        }

    def limits(self, state, stage="write_acceptance_tests", model="openai/gpt-6-sol", *, repair=False):
        return role_launch.launch_limits(
            state, model, stage, stage + "_report_repair" if repair else stage, report_only=repair
        )

    def test_exact_job_retries_keep_their_ordinary_idle_limit_without_a_second_startup_guard(self):
        for stage in ("write_acceptance_tests", "check_design", "investigate_stuck", "resolve_repair"):
            for model, idle in (("openai/gpt-6-sol", 300), ("zai-coding-plan/glm-5.3", 300), ("mimo-v2.6-pro", 900)):
                with self.subTest(stage=stage, model=model):
                    timeout, _, _, watch, saved = self.limits(self.state(stage), stage, model)
                    self.assertEqual(600, timeout)
                    self.assertIsNone(watch["first_event_limit"])
                    self.assertIsNone(watch["first_event_origin"])
                    self.assertEqual(idle, watch["idle_limit"])
                    self.assertEqual(300, saved["idle_timeout_seconds"])
                    with tempfile.TemporaryDirectory() as directory:
                        events = Path(directory) / "events.jsonl"
                        events.touch()
                        monitor = role_launch.activity_monitor_mod.ActivityMonitor(
                            events, idle_seconds=idle, first_event_seconds=watch["first_event_limit"], clock=lambda: 0
                        )
                        monitor.clock = lambda: 120
                        self.assertIsNone(monitor.expired(), "the retry must survive a slow initial stream")
                        monitor.clock = lambda: idle + 1
                        self.assertEqual("idle", monitor.expired()["kind"], "ordinary inactivity still stops it")

    def test_first_launches_other_stages_repairs_and_stale_authorizations_keep_the_startup_guard(self):
        first = self.state()
        first.pop("job_retry_authorization")
        for state, stage, repair in (
            (first, "write_acceptance_tests", False),
            (self.state(), "check_design", False),
            (self.state(), "write_acceptance_tests", True),
            (self.state(token="jr:old"), "write_acceptance_tests", False),
            (self.state(consumed=True), "write_acceptance_tests", False),
            (self.state(stage="check_design"), "write_acceptance_tests", False),
        ):
            with self.subTest(state=state, stage=stage, repair=repair):
                timeout, _, _, watch, _ = self.limits(state, stage, repair=repair)
                self.assertEqual(300, timeout)
                self.assertEqual(90, watch["first_event_limit"])

    def test_an_explicit_idle_limit_and_the_saved_hard_deadline_stay_exact(self):
        state = self.state()
        state["settings"]["limits"]["idle_timeout_seconds"] = 37
        state["settings"]["budget_origins"]["idle_timeout_seconds"] = "user_explicit"
        timeout, _, _, watch, saved = self.limits(state)
        self.assertEqual((600, 37, 37), (timeout, watch["idle_limit"], saved["idle_timeout_seconds"]))
        self.assertIsNone(watch["first_event_limit"])
        self.assertEqual(300, state["settings"]["limits"]["stage_timeout_seconds"])


class RaiseForExitTests(unittest.TestCase):
    def check(self, state=None, record=None, **overrides):
        options = dict(
            role="builder",
            events="e.jsonl",
            output=MagicMock(is_file=MagicMock(return_value=True)),
            exit_code=0,
            cleanup_error=None,
            interrupted=False,
            timed_out=False,
            engine="codex",
            configured_tool=False,
            adapter=None,
            supports_sessions=False,
            session=None,
            report_only=False,
            route_role="builder",
        )
        options.update(overrides)
        role_launch.raise_for_exit(state if state is not None else {}, record if record is not None else {}, **options)

    def status(self, **overrides):
        with self.assertRaises(Paused) as caught:
            self.check(**overrides)
        return caught.exception.status

    def test_the_stops_pause_in_their_order(self):
        both = dict(cleanup_error="kill failed", interrupted=True, timed_out=True)
        self.assertEqual("PAUSED_PROCESS_CLEANUP", self.status(**both))
        self.assertEqual("PAUSED_INTERRUPTED", self.status(interrupted=True, timed_out=True))
        self.assertEqual(
            "PAUSED_PROVIDER_TIMEOUT", self.status(timed_out=True, record={"timeout_reason": "idle for 300s"})
        )

    def test_a_clean_exit_with_its_report_passes(self):
        self.check()

    def test_a_clean_exit_without_a_report_pauses_as_uncertain(self):
        with (
            patch.object(role_launch.stage_stops, "refused_at_clean_exit"),
            patch.object(role_launch.stage_stops, "stopped_at_context_window"),
        ):
            self.assertEqual(
                "PAUSED_UNCERTAIN_STAGE", self.status(output=MagicMock(is_file=MagicMock(return_value=False)))
            )

    def test_a_new_session_is_recorded_as_the_roles(self):
        state, record = {"sessions": {}}, {}
        with (
            patch.object(role_launch.format_correction, "event_thread_id", return_value="t-9"),
            patch.object(role_launch.support, "events", return_value=[{"type": "turn.completed"}]),
        ):
            self.check(state, record, supports_sessions=True, route_role="planner")
        self.assertEqual(({"planner": "t-9"}, "t-9"), (state["sessions"], record["thread_id"]))

    def test_an_unexpected_session_pauses(self):
        with patch.object(role_launch.format_correction, "event_thread_id", return_value="t-9"):
            self.assertEqual("PAUSED_UNCERTAIN_STAGE", self.status(supports_sessions=True, session="t-1"))


class RecordAfterTests(unittest.TestCase):
    def test_the_after_source_its_diff_and_tool_summary_are_named_on_the_record(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "builder-01"
            save, record = MagicMock(), {}
            with (
                patch.object(role_launch.source_scope, "snapshot", return_value={"revision": "r2"}),
                patch.object(role_launch.support, "changed_paths", return_value=["a.py"]),
                patch.object(role_launch.source_diff, "write") as write,
                patch.object(role_launch.support, "summarize_events") as summarize,
            ):
                after = role_launch.record_after({}, record, "/w", base, {"revision": "r1"}, "e.jsonl", save=save)
        self.assertEqual({"revision": "r2"}, after)
        save.assert_called_once_with(base.with_suffix(".after.json"), after)
        write.assert_called_once_with("/w", base.with_suffix(".diff"), after)
        summarize.assert_called_once_with("e.jsonl", base.with_suffix(".tools.json"))
        self.assertEqual(
            {
                "after_ref": str(base.with_suffix(".after.json")),
                "source_revision": "r2",
                "changed_files": ["a.py"],
                "diff_ref": str(base.with_suffix(".diff")),
                "tool_evidence": str(base.with_suffix(".tools.json")),
            },
            record,
        )


if __name__ == "__main__":
    unittest.main()
