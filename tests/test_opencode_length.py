"""Output-limit regressions using native transport fixtures, never live providers."""
import copy
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode as runner
import autocode_goals as goals
import autocode_opencode as opencode
import autocode_support as support
from goal_fixtures import approve_fixture


def event(kind, **part):
    return {"type": kind, "sessionID": "ses_limit", "part": {
        "id": "prt_" + kind, "sessionID": "ses_limit", "messageID": "msg_limit", **part}}


def length_event():
    # Usage reported by the real failed implementation attempt. Reasoning and
    # visible output share the output cap; cache reads count toward input usage.
    return event("step_finish", reason="length", tokens={"total": 56262,
        "input": 11206, "output": 47, "reasoning": 31953, "cache": {"write": 0, "read": 13056}})


class OutputLimitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)
        self.log = self.run / "length.jsonl"

    def write_events(self, rows):
        self.log.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    def test_length_is_a_failed_turn_with_known_usage_not_completion(self):
        rows = [event("step_start"), length_event(), copy.deepcopy(length_event())]
        self.write_events(rows)
        normalized = support.events(self.log)
        self.assertFalse(any(row["type"] == "turn.completed" for row in normalized))
        failures = [row for row in normalized if row["type"] == "turn.failed"]
        self.assertEqual(1, len(failures))
        self.assertEqual("output_token_limit", failures[0]["error"]["code"])
        self.assertIn("output token limit", support.terminal_failure_reason(self.log))
        metrics = support.event_metrics(self.log)
        self.assertEqual({"input_tokens": 24262, "cached_input_tokens": 13056,
                          "output_tokens": 32000, "reasoning_output_tokens": 31953}, metrics["provider_tokens"])
        self.assertEqual(0, metrics["completed_turns"])
        # Output capacity is not an account quota failure or an approval.
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", support.failure_status(self.log))
        with self.assertRaisesRegex(RuntimeError, "no successful terminal step"):
            opencode.final_report(self.log)

    def test_a_stream_ending_on_tool_calls_is_a_failed_turn_with_known_usage(self):
        # A live Validator mistyped the workspace path; every tool call was
        # auto-rejected and `opencode run` exited after that step. Unknown usage
        # lost usage accounting (issue #112, repair 3).
        tokens = {"input": 10, "output": 5, "reasoning": 7, "cache": {"read": 2, "write": 3}}
        rows = [event("step_start", id="prt_s1"),
                event("tool_use", id="prt_tool", tool="read", state={"status": "error", "error": "external_directory"}),
                event("step_finish", id="prt_f1", reason="tool-calls", tokens=tokens)]
        self.write_events(rows)
        failures = [row for row in support.events(self.log) if row["type"] == "turn.failed"]
        self.assertEqual(["incomplete_turn"], [row["error"]["code"] for row in failures])
        self.assertIn("tool-calls", support.terminal_failure_reason(self.log))
        metrics = support.event_metrics(self.log)
        self.assertEqual({"input_tokens": 15, "cached_input_tokens": 2, "output_tokens": 12,
                          "reasoning_output_tokens": 7}, metrics["provider_tokens"])
        self.assertEqual(0, metrics["completed_turns"])
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", support.failure_status(self.log))
        with self.assertRaisesRegex(RuntimeError, "no successful terminal step"):
            opencode.final_report(self.log)
        # A later step (the model continuing after its tools ran) is not terminal
        # yet, and a finished later step completes the turn as before.
        self.write_events(rows + [event("step_start", id="prt_s2")])
        self.assertFalse(any(row["type"] in ("turn.completed", "turn.failed") for row in support.events(self.log)))
        self.write_events(rows + [event("step_start", id="prt_s2"),
                                  event("step_finish", id="prt_f2", reason="stop", tokens=tokens)])
        self.assertEqual(1, support.event_metrics(self.log)["completed_turns"])
        self.assertIsNone(support.terminal_failure_reason(self.log))

    def test_length_usage_includes_prior_unique_steps(self):
        first = event("step_finish", id="prt_first", reason="tool-calls", tokens={
            "input": 10, "output": 5, "reasoning": 7, "cache": {"read": 2, "write": 3}})
        self.write_events([first, copy.deepcopy(first), length_event()])
        usage = support.event_metrics(self.log)["provider_tokens"]
        self.assertEqual(24277, usage["input_tokens"])
        self.assertEqual(32012, usage["output_tokens"])

    def test_interrupted_step_retains_partial_usage_without_terminal_evidence(self):
        tokens = {"input": 10, "output": 5, "reasoning": 7, "cache": {"read": 2, "write": 3}}
        first = event("step_finish", id="prt_first", reason="tool-calls", tokens=tokens)
        second = event("step_finish", id="prt_second", reason="tool-calls", tokens=tokens)
        rows = [event("step_start", id="prt_start1"), first,
                event("step_start", id="prt_start2"), second,
                event("step_start", id="prt_start3"), copy.deepcopy(first)]
        self.write_events(rows)
        metrics = support.event_metrics(self.log)
        self.assertEqual({"input_tokens": 30, "cached_input_tokens": 4,
                          "output_tokens": 24, "reasoning_output_tokens": 14}, metrics["provider_tokens"])
        self.assertTrue(metrics["provider_tokens_partial"])
        self.assertEqual(0, metrics["completed_turns"])
        self.assertFalse(any(row["type"] in ("turn.completed", "turn.failed") for row in support.events(self.log)))
        self.assertIsNone(support.terminal_failure_reason(self.log))
        with self.assertRaisesRegex(RuntimeError, "no successful terminal step"):
            opencode.final_report(self.log)

        self.write_events(rows + [event("step_finish", id="prt_last", reason="stop", tokens=tokens)])
        finished = support.event_metrics(self.log)
        self.assertEqual(45, finished["provider_tokens"]["input_tokens"])
        self.assertEqual(36, finished["provider_tokens"]["output_tokens"])
        self.assertFalse(finished["provider_tokens_partial"])
        self.assertEqual(1, finished["completed_turns"])

    def test_unidentified_new_step_retains_only_identified_usage(self):
        self.write_events([length_event(), event("step_start", id=None), copy.deepcopy(length_event())])
        metrics = support.event_metrics(self.log)
        self.assertEqual(24262, metrics["provider_tokens"]["input_tokens"])
        self.assertEqual(32000, metrics["provider_tokens"]["output_tokens"])
        self.assertTrue(metrics["provider_tokens_partial"])
        self.assertFalse(any(row["type"] in ("turn.completed", "turn.failed") for row in support.events(self.log)))

    def test_interrupted_stream_keeps_known_steps_when_later_usage_is_invalid(self):
        for tokens in (None, [], "invalid", {"input": True, "output": -1, "cache": {}}, {}):
            with self.subTest(tokens=tokens):
                self.write_events([length_event(),
                    event("step_finish", id="prt_missing_usage", reason="tool-calls", tokens=tokens),
                    event("step_start", id="prt_unfinished")])
                metrics = support.event_metrics(self.log)
                self.assertEqual({"input_tokens": 24262, "cached_input_tokens": 13056,
                                  "output_tokens": 32000, "reasoning_output_tokens": 31953},
                                 metrics["provider_tokens"])
                self.assertTrue(metrics["provider_tokens_partial"])
                self.assertEqual(0, metrics["completed_turns"])

    def test_missing_or_invalid_usage_remains_unknown(self):
        end = length_event()
        del end["part"]["tokens"]["cache"]
        end["part"]["tokens"]["reasoning"] = True
        self.write_events([end])
        metrics = support.event_metrics(self.log)
        self.assertTrue(all(value is None for value in metrics["provider_tokens"].values()))
        self.assertEqual(0, metrics["completed_turns"])

    def test_non_object_usage_preserves_failure_with_unknown_consumption(self):
        for tokens in (None, [], [1], "invalid", True, 10, 1.5):
            with self.subTest(tokens=tokens):
                end = length_event()
                end["part"]["tokens"] = tokens
                self.write_events([end])
                self.assertIn("output token limit", support.terminal_failure_reason(self.log))
                metrics = support.event_metrics(self.log)
                self.assertTrue(all(value is None for value in metrics["provider_tokens"].values()))
                self.assertEqual(0, metrics["completed_turns"])

    def test_replayed_finish_cannot_close_a_newer_unfinished_step(self):
        for reason in ("stop", "length"):
            for start_id in ("prt_next_start", None):
                with self.subTest(reason=reason, start_id=start_id):
                    previous = length_event()
                    previous["part"]["reason"] = reason
                    newer = event("step_start", id=start_id)
                    self.write_events([previous, newer, copy.deepcopy(previous)])
                    normalized = support.events(self.log)
                    self.assertFalse(any(row["type"] in ("turn.completed", "turn.failed") for row in normalized))
                    self.assertIsNone(support.terminal_failure_reason(self.log))
                    self.assertEqual(0, support.event_metrics(self.log)["completed_turns"])
                    with self.assertRaisesRegex(RuntimeError, "no successful terminal step"):
                        opencode.final_report(self.log)

    def test_newest_unique_terminal_selects_report_despite_old_finish_replay(self):
        tokens = {"input": 10, "output": 5, "reasoning": 7, "cache": {"read": 2, "write": 3}}
        previous = event("step_finish", id="prt_old_finish", messageID="msg_old", reason="stop", tokens=tokens)
        self.write_events([
            event("step_start", id="prt_old_start"),
            event("text", id="prt_old_text", messageID="msg_old", text='{"result":"old"}'),
            previous,
            event("step_start", id="prt_new_start"),
            event("text", id="prt_new_text", messageID="msg_new", text='{"result":"new"}'),
            event("step_finish", id="prt_new_finish", messageID="msg_new", reason="stop", tokens=tokens),
            copy.deepcopy(previous),
        ])
        metrics = support.event_metrics(self.log)
        self.assertEqual(1, metrics["completed_turns"])
        self.assertEqual(30, metrics["provider_tokens"]["input_tokens"])
        self.assertEqual(24, metrics["provider_tokens"]["output_tokens"])
        self.assertEqual({"result": "new"}, opencode.final_report(self.log))

    def test_incomplete_or_mixed_session_stream_does_not_claim_terminal_length(self):
        for rows in ([length_event(), event("step_start")],
                     [length_event(), {**event("text"), "sessionID": "ses_other"}],
                     [event("text", text="The output token limit was exhausted")]):
            with self.subTest(rows=rows):
                self.write_events(rows)
                self.assertIsNone(support.terminal_failure_reason(self.log))
                self.assertEqual(0, support.event_metrics(self.log)["completed_turns"])

    def test_real_provider_error_keeps_existing_failure_classification(self):
        self.write_events([length_event(), {"type": "error", "sessionID": "ses_limit",
                                          "error": {"message": "rate_limit: 429"}}])
        self.assertEqual("PAUSED_RATE_LIMIT", support.failure_status(self.log))
        self.assertEqual(32000, support.event_metrics(self.log)["provider_tokens"]["output_tokens"])
        self.assertFalse(any(row["type"] == "turn.completed" for row in support.events(self.log)))

    def test_error_after_stop_preserves_usage_without_success(self):
        end = length_event()
        end["part"]["reason"] = "stop"
        self.write_events([end, {"type": "error", "sessionID": "ses_limit", "error": {"message": "429"}}])
        metrics = support.event_metrics(self.log)
        self.assertEqual(24262, metrics["provider_tokens"]["input_tokens"])
        self.assertTrue(metrics["provider_tokens_partial"])
        self.assertEqual(0, metrics["completed_turns"])
        self.assertEqual("PAUSED_RATE_LIMIT", support.failure_status(self.log))

    def test_success_and_failed_usage_are_counted_with_only_success_completed(self):
        self.write_events([{"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 4}},
                           {"type": "turn.failed", "usage": {"input_tokens": 7, "output_tokens": 2}}])
        metrics = support.event_metrics(self.log)
        self.assertEqual(17, metrics["provider_tokens"]["input_tokens"])
        self.assertEqual(6, metrics["provider_tokens"]["output_tokens"])
        self.assertEqual(1, metrics["completed_turns"])
        self.assertIsNone(metrics["provider_tokens"]["reasoning_output_tokens"])

    def test_runner_pauses_then_explicit_recovery_retains_usage_and_approval(self):
        state = {"version": 2, "workspace": str(self.root), "task": "Fixture",
                 "status": "RUNNING", "iteration": 1, "sessions": {}, "stages": [], "history": [],
                 "settings": {"engine": "opencode", "roles": {
                     role: {"model": "fixture/" + role} for role in ("astra", "terra", "sol", "completion")}}}
        approve_fixture(state, goals)
        approved = copy.deepcopy(state["goal_contract"])
        partial = self.root / "greet.py"
        emitted = [event("step_start"), length_event()]
        launches = []

        class Child:
            pid = 987654321
            def __init__(child, command, **kwargs):
                launches.append(command)
                partial.write_text("# retained unfinished implementation\n")
                kwargs["stdout"].write("\n".join(json.dumps(row) for row in emitted))

        snapshot = {"head": "h", "files": {}, "revision": "r"}
        with patch.object(runner.opencode, "launch", return_value=(["fixture-provider"], {}, {})), \
             patch.object(runner.subprocess, "Popen", Child), \
             patch.object(support, "snapshot", return_value=snapshot), \
             patch.object(runner.processes, "preflight", return_value=None), \
             patch.object(runner.processes, "wait_for_stage", return_value=(0, False)):
            with self.assertRaises(support.Paused) as caught:
                runner.run_role(role="terra", prompt="Fixture", sandbox="workspace-write", workspace=self.root,
                    run_dir=self.run, state=state, schema=runner.SCHEMA_DIR / "v2/terra-report.schema.json",
                    model="fixture/terra", allow_write=True, dry_run=False)
        self.assertEqual(1, len(launches))
        self.assertEqual("PAUSED_UNCERTAIN_STAGE", caught.exception.status)
        self.assertIn("output token limit", str(caught.exception))
        record = state["active_stage"]
        persisted = support.read(self.run / "state.json")["active_stage"]
        self.assertEqual(32000, persisted["metrics"]["provider_tokens"]["output_tokens"])
        self.assertEqual(0, persisted["metrics"]["completed_turns"])
        self.assertTrue(persisted["accounted"])
        self.assertFalse(persisted["timed_out"])
        seconds = state["active_seconds"]
        self.assertEqual(approved, state["goal_contract"])
        self.assertEqual("terra", state["next_stage"])
        self.assertFalse(runner.automatically_recover_timed_out_stage(state, self.run, self.root, caught.exception))

        with patch.object(runner, "assert_stage_stopped"), patch.object(runner.subprocess, "Popen") as popen:
            with self.assertRaises(support.Paused) as reconciled:
                runner.reconcile_active(state, self.run, self.root)
            self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", reconciled.exception.status)
            self.assertIn("output token limit", str(reconciled.exception))
            self.assertIn("never automatically replayed", str(reconciled.exception))
            self.assertIn("--abandon-stage 001/builder-01", str(reconciled.exception))
            self.assertEqual([], state["stages"])
            with patch.object(support, "snapshot", return_value=snapshot):
                runner.abandon_stage(state, self.run, self.root, runner.attempt_id(record))
            popen.assert_not_called()
        self.assertEqual("PAUSED_STAGE_ABANDONED", state["status"])
        self.assertEqual("terra", state["next_stage"])
        self.assertEqual(approved, state["goal_contract"])
        self.assertNotIn("active_stage", state)
        self.assertNotIn("terra", state["sessions"])
        archived = state["stages"][-1]
        self.assertTrue(archived["abandoned"])
        self.assertTrue(archived["rejected"])
        self.assertTrue(Path(archived["events"]).is_file())
        self.assertEqual(32000, archived["metrics"]["provider_tokens"]["output_tokens"])
        self.assertEqual(0, archived["metrics"]["completed_turns"])
        self.assertEqual(persisted["metrics"]["provider_tokens"], archived["metrics"]["provider_tokens"])
        runner.account_stage(state, archived)
        self.assertEqual(seconds, state["active_seconds"])
        self.assertEqual([], state["history"])
        self.assertNotIn("implementation", state)
        self.assertNotIn("validation", state)
        self.assertEqual("# retained unfinished implementation\n", partial.read_text())

        # Exercise the saved-run CLI gate, stopping at dispatch so this test can
        # never contact a provider, including after explicit acknowledgement.
        (self.root / ".git").mkdir()
        argv = ["autocode", "--workspace", str(self.root), "--run-dir", str(self.run)]
        with patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "registry-home")}), \
             patch.object(support, "assert_no_legacy_process"), \
             patch.object(runner.autocode_providers, "resolve", return_value=runner.opencode), \
             patch.object(runner.opencode, "check_models"), \
             patch.object(runner.orchestrator, "drive") as drive, \
             patch.object(runner, "run_role") as launch, \
             contextlib.redirect_stdout(io.StringIO()):
            with patch.object(sys, "argv", argv):
                self.assertEqual(2, runner.main())
            drive.assert_not_called()
            saved = support.read(self.run / "state.json")
            self.assertEqual("PAUSED_STAGE_ABANDONED", saved["status"])
            with patch.object(sys, "argv", argv + ["--resume-paused"]):
                self.assertEqual(2, runner.main())
            drive.assert_called_once()
            launch.assert_not_called()
        resumed = support.read(self.run / "state.json")
        self.assertEqual("RUNNING", resumed["status"])
        self.assertEqual("terra", resumed["next_stage"])
        self.assertEqual(approved, resumed["goal_contract"])
        self.assertEqual([archived], resumed["stages"])
        self.assertEqual(seconds, resumed["active_seconds"])
        self.assertEqual([], resumed["history"])
        self.assertNotIn("terra", resumed["sessions"])
        self.assertNotIn("validation", resumed)
        self.assertEqual("# retained unfinished implementation\n", partial.read_text())


if __name__ == "__main__":
    unittest.main()
