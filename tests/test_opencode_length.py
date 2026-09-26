"""Output-limit regressions using native transport fixtures, never live providers."""
# path bootstrap: runtime in tools/, fakes in tests/fakes/
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / 'tools'
_FAKES = _ROOT / 'tests' / 'fakes'
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

_ROOT = _Path(__file__).resolve().parents[1] if _Path(__file__).name != 'live_trial.py' else _Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / 'tools', _ROOT / 'tests', _ROOT / 'tests' / 'fakes'):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
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

    def test_length_usage_includes_prior_unique_steps(self):
        first = event("step_finish", id="prt_first", reason="tool-calls", tokens={
            "input": 10, "output": 5, "reasoning": 7, "cache": {"read": 2, "write": 3}})
        self.write_events([first, copy.deepcopy(first), length_event()])
        usage = support.event_metrics(self.log)["provider_tokens"]
        self.assertEqual(24277, usage["input_tokens"])
        self.assertEqual(32012, usage["output_tokens"])

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
                 "settings": {"engine": "opencode", "roles": {"terra": {"model": "fixture/model"}}}}
        approve_fixture(state, goals)
        approved = copy.deepcopy(state["goal_contract"])
        emitted = [event("step_start"), length_event()]
        launches = []

        class Child:
            pid = 987654321
            def __init__(child, command, **kwargs):
                launches.append(command)
                kwargs["stdout"].write("\n".join(json.dumps(row) for row in emitted))

        snapshot = {"head": "h", "files": {}, "revision": "r"}
        with patch.object(runner.opencode, "launch", return_value=(["fixture-provider"], {}, {})), \
             patch.object(runner.subprocess, "Popen", Child), \
             patch.object(support, "snapshot", return_value=snapshot), \
             patch.object(runner.processes, "process_table", return_value={}), \
             patch.object(runner.processes, "wait_for_stage", return_value=(0, False)):
            with self.assertRaises(support.Paused) as caught:
                runner.run_role(role="terra", prompt="Fixture", sandbox="workspace-write", workspace=self.root,
                    run_dir=self.run, state=state, schema=runner.SCHEMA_DIR / "v2/terra-report.schema.json",
                    model="fixture/model", allow_write=True, dry_run=False)
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
            self.assertIn("--abandon-stage 001/terra-01", str(reconciled.exception))
            self.assertEqual([], state["stages"])
            with patch.object(support, "snapshot", return_value=snapshot):
                runner.abandon_stage(state, self.run, self.root, runner.attempt_id(record))
            popen.assert_not_called()
        self.assertEqual("PAUSED_STAGE_ABANDONED", state["status"])
        self.assertEqual("astra_review", state["next_stage"])
        self.assertEqual(approved, state["goal_contract"])
        self.assertNotIn("active_stage", state)
        self.assertNotIn("terra", state["sessions"])
        archived = state["stages"][-1]
        self.assertTrue(archived["abandoned"])
        self.assertTrue(Path(archived["events"]).is_file())
        self.assertEqual(32000, archived["metrics"]["provider_tokens"]["output_tokens"])
        self.assertEqual(0, archived["metrics"]["completed_turns"])
        runner.account_stage(state, archived)
        self.assertEqual(seconds, state["active_seconds"])


if __name__ == "__main__":
    unittest.main()
