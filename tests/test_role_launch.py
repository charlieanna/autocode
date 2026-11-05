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
            stage="sol_report_repair",
            iteration=3,
            report_only=True,
            original_stage="sol",
            changed_code_proof="p",
        )
        self.assertEqual(
            {
                "stage": "sol_report_repair",
                "iteration": 3,
                "turn": 3,
                "milestone_id": "M-1",
                "task_id": "T-1",
                "contract_hash": "h",
                "criteria_revision": "c",
                "report_only": True,
                "original_stage": "sol",
                "consumed": 1,
                "changed_code_proof": "p",
                "regression_proof_path": "proof.json",
            },
            context,
        )

    def test_a_first_launch_names_no_original_stage(self):
        context = role_launch.replay_context(
            {}, stage="terra", iteration=1, report_only=False, original_stage="terra", changed_code_proof=None
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


class ReviewerFallbackTests(unittest.TestCase):
    def state(self):
        return {
            "planning": {
                "reviewer_route_fallbacks": [
                    {"id": "g-0", "consumed": True, "binding": {"selected_route": {"role": "other"}}},
                    {"id": "g-1", "consumed": True, "binding": {"selected_route": {"role": "glm"}}},
                ]
            }
        }

    def test_the_consumed_grant_is_named_on_the_record(self):
        record = {}
        with patch.object(role_launch.reviewer_fallback, "admit", return_value={"role": "glm"}):
            role_launch.admit_reviewer_fallback(self.state(), "/run", "/w", "astra_challenge", {"role": "glm"}, record)
        self.assertEqual("g-1", record["reviewer_fallback_grant"])

    def test_a_changed_fallback_pauses_before_the_provider(self):
        record = {}
        with patch.object(role_launch.reviewer_fallback, "admit", return_value={"role": "other"}):
            with self.assertRaises(Paused) as caught:
                role_launch.admit_reviewer_fallback(
                    self.state(), "/run", "/w", "astra_challenge", {"role": "glm"}, record
                )
        self.assertEqual("PAUSED_REVIEWER_FALLBACK", caught.exception.status)
        self.assertNotIn("reviewer_fallback_grant", record)


class ActivityReporterTests(unittest.TestCase):
    def test_every_observation_is_saved_and_one_line_is_printed_per_change(self):
        save, state, record = MagicMock(), {}, {}
        reporter = role_launch.activity_reporter(
            state,
            record,
            stage="terra",
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


class RaiseForExitTests(unittest.TestCase):
    def check(self, state=None, record=None, **overrides):
        options = dict(
            role="terra",
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
            route_role="terra",
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
            self.check(state, record, supports_sessions=True, route_role="glm")
        self.assertEqual(({"glm": "t-9"}, "t-9"), (state["sessions"], record["thread_id"]))

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
