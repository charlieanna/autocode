"""Qwen transport tests with native stream-json events and no network/model calls.

The fixtures reproduce event bodies captured from Qwen Code 0.25.0: a successful
run_shell_command result is the command's bare stdout, a failed one is a
diagnostic envelope ending in "Exit Code: N", and a call the approval policy
refused is prose with no exit code.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode_dispatch as dispatch
import autocode_qwen as qwen
import autocode_support as support


SESSION = "307e3361-990b-4f61-b64e-20c370dbdd09"

# Captured verbatim: a nonzero exit arrives as an envelope, not as bare output.
FAILURE_ENVELOPE = ("Command: sh -c 'echo boom; exit 7'\nDirectory: (root)\nOutput: boom\n"
                    "Error: (none)\nExit Code: 7\nSignal: (none)\nProcess Group PGID: (none)")
# Captured verbatim from --approval-mode auto: a refusal, with no exit code.
REFUSAL = ("Blocked by auto mode policy: The user authorized exactly two commands, both already "
           "executed; this third command exceeds the explicitly requested scope.")


def system():
    return {"type": "system", "subtype": "init", "session_id": SESSION, "model": "qwen3.8-max"}


def tool_use(call_id, command, name="run_shell_command"):
    return {"type": "assistant", "session_id": SESSION, "message": {
        "id": "msg_" + call_id, "role": "assistant", "model": "qwen3.8-max",
        "content": [{"type": "tool_use", "id": call_id, "name": name,
                     "input": {"command": command, "description": "fixture"}}],
        "stop_reason": "tool_use", "usage": {"input_tokens": 14563, "output_tokens": 130}}}


def tool_result(call_id, content, is_error=False):
    return {"type": "user", "session_id": SESSION, "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": call_id, "is_error": is_error, "content": content}]}}


def terminal(**overrides):
    row = {"type": "result", "subtype": "success", "session_id": SESSION, "is_error": False,
           "num_turns": 2, "result": "done",
           "usage": {"input_tokens": 39107, "output_tokens": 516,
                     "cache_read_input_tokens": 16283, "total_tokens": 39623},
           "stats": {"models": {"qwen3.8-max": {"tokens": {
               "prompt": 39107, "candidates": 516, "total": 39623, "cached": 16283,
               "thoughts": 208}}}},
           "permission_denials": []}
    row.update(overrides)
    return row


def items(events):
    return [row["item"] for row in events if row.get("type") == "item.completed"]


def write_events(rows):
    handle = tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False)
    for row in rows:
        handle.write(json.dumps(row) + "\n")
    handle.close()
    return Path(handle.name)


class QwenEventTests(unittest.TestCase):
    def test_successful_command_is_command_evidence_with_exit_zero(self):
        events = qwen.normalized_events([system(), tool_use("call_ok", "echo hello"),
                                         tool_result("call_ok", "hello"), terminal()])
        found = items(events)
        self.assertEqual(1, len(found))
        self.assertEqual("command_execution", found[0]["type"])
        self.assertEqual("call_ok", found[0]["id"])
        self.assertEqual("echo hello", found[0]["command"])
        self.assertEqual(0, found[0]["exit_code"])
        self.assertEqual("hello", found[0]["aggregated_output"])

    def test_failed_command_takes_its_exit_code_from_the_envelope(self):
        events = qwen.normalized_events([system(), tool_use("call_bad", "sh -c 'echo boom; exit 7'"),
                                         tool_result("call_bad", FAILURE_ENVELOPE, is_error=True), terminal()])
        found = items(events)
        self.assertEqual(1, len(found))
        self.assertEqual("command_execution", found[0]["type"])
        self.assertEqual(7, found[0]["exit_code"])

    def test_refused_call_is_never_command_evidence(self):
        # A refusal proves no exit code, so it must not become a citable check.
        events = qwen.normalized_events([system(), tool_use("call_denied", "exit 3"),
                                         tool_result("call_denied", REFUSAL, is_error=True), terminal()])
        found = items(events)
        self.assertEqual(1, len(found))
        self.assertEqual("tool_output", found[0]["type"])
        self.assertNotIn("exit_code", found[0])

    def test_non_shell_tools_are_not_command_evidence(self):
        events = qwen.normalized_events([system(), tool_use("call_read", "/etc/hosts", name="read_file"),
                                         tool_result("call_read", "contents"), terminal()])
        self.assertEqual([], items(events))

    def test_a_terminal_success_completes_the_turn(self):
        events = qwen.normalized_events([system(), terminal()])
        self.assertEqual("turn.completed", events[-1]["type"])

    def test_usage_is_not_double_counted(self):
        # Qwen's input already includes cache reads and its output already
        # includes reasoning, so neither may be added again.
        usage = qwen.normalized_events([system(), terminal()])[-1]["usage"]
        self.assertEqual(39107, usage["input_tokens"])
        self.assertEqual(16283, usage["cached_input_tokens"])
        self.assertEqual(516, usage["output_tokens"])
        self.assertEqual(208, usage["reasoning_output_tokens"])

    def test_a_failed_result_reports_the_provider_words(self):
        said = "ERROR: exceeded retry limit, last status: 429 Too Many Requests"
        events = qwen.normalized_events([system(), terminal(is_error=True, subtype="error", result=said)])
        self.assertEqual("turn.failed", events[-1]["type"])
        self.assertEqual(said, events[-1]["error"]["message"])

    def test_a_missing_result_is_not_terminal_proof(self):
        events = qwen.normalized_events([system(), tool_use("call_ok", "echo hello"),
                                         tool_result("call_ok", "hello")])
        self.assertEqual("usage.partial", events[-1]["type"])
        self.assertFalse(any(row.get("type") == "turn.completed" for row in events))
        path = write_events([system(), tool_result("call_ok", "hello")])
        self.addCleanup(path.unlink)
        with self.assertRaises(RuntimeError):
            qwen.final_report(path)

    def test_mixed_sessions_are_refused(self):
        other = dict(tool_use("call_ok", "echo hello"), session_id="another-session")
        events = qwen.normalized_events([system(), other, terminal()])
        self.assertEqual([{"type": "turn.failed",
                           "error": {"message": "Missing or mixed Qwen sessions"}}], events)

    def test_a_denial_is_surfaced_as_an_error(self):
        events = qwen.normalized_events([system(), terminal(permission_denials=[{"reason": REFUSAL}])])
        self.assertTrue(any(row.get("type") == "error" for row in events))


class QwenReportTests(unittest.TestCase):
    def test_structured_output_is_the_report(self):
        report = {"summary": "done", "checks": [{"command": "echo hi", "exit_code": 0}]}
        path = write_events([system(), terminal(structured_result=report, result=json.dumps(report))])
        self.addCleanup(path.unlink)
        self.assertEqual(report, qwen.final_report(path))

    def test_a_json_final_message_is_accepted_without_structured_output(self):
        report = {"summary": "done"}
        path = write_events([system(), terminal(result=json.dumps(report))])
        self.addCleanup(path.unlink)
        self.assertEqual(report, qwen.final_report(path))

    def test_prose_before_the_report_is_recovered(self):
        report = {"summary": "done", "nested": {"a": [1, 2, {"b": None}]}}
        path = write_events([system(), terminal(result="The probe confirms the fix. Report: "
                                                       + json.dumps(report))])
        self.addCleanup(path.unlink)
        self.assertEqual(report, qwen.final_report(path))

    def test_prose_without_a_report_is_refused(self):
        path = write_events([system(), terminal(result="I could not finish the work.")])
        self.addCleanup(path.unlink)
        with self.assertRaises(RuntimeError):
            qwen.final_report(path)


class QwenLaunchTests(unittest.TestCase):
    def launch(self, **kwargs):
        arguments = {"role": "terra", "workspace": "/tmp/project", "run_dir": "/tmp/run",
                     "session": None, "model": "qwen/qwen3.8-max", "effort": "high",
                     "allow_write": True}
        arguments.update(kwargs)
        schema = arguments.pop("schema", "/tmp/run/stage.schema.json")
        sandbox = arguments.pop("sandbox", None)
        return qwen.launch(arguments.pop("role"), arguments.pop("workspace"), arguments.pop("run_dir"),
                           arguments.pop("session"), arguments.pop("model"), arguments.pop("effort"),
                           arguments.pop("allow_write"), schema=schema, sandbox=sandbox, **arguments)

    def test_stream_json_and_the_stage_schema_are_requested(self):
        command, _, _ = self.launch()
        # --output-format json prints one array on a line, which the event log cannot read.
        self.assertEqual(["--output-format", "stream-json"],
                         command[command.index("--output-format"):command.index("--output-format") + 2])
        self.assertEqual(["--json-schema", "@/tmp/run/stage.schema.json"],
                         command[command.index("--json-schema"):command.index("--json-schema") + 2])
        self.assertEqual(["--model", "qwen3.8-max"],
                         command[command.index("--model"):command.index("--model") + 2])

    def test_read_only_stages_get_plan_mode_and_writing_stages_get_yolo(self):
        self.assertEqual("plan", self.launch(sandbox="read-only")[0][-1])
        self.assertEqual("plan", self.launch(allow_write=False, planning=True)[0][-1])
        self.assertEqual("yolo", self.launch(sandbox="workspace-write")[0][-1])
        # "auto" refused an authorized command in a live probe, so it is not used.
        self.assertNotIn("auto", self.launch(sandbox="workspace-write")[0])

    def test_a_saved_session_is_resumed(self):
        command, _, _ = self.launch(session="ses_1")
        self.assertEqual(["--resume", "ses_1"], command[command.index("--resume"):])

    def test_a_model_outside_the_qwen_provider_is_refused(self):
        with self.assertRaises(ValueError):
            self.launch(model="openai/gpt-6-sol")
        with self.assertRaises(ValueError):
            self.launch(model="qwen3.8-max")

    def test_prompt_contract_names_the_structured_output(self):
        prompt = qwen.prompt_for_schema("\nCURRENT HANDOFF DATA\n{}", {"type": "object"}, "/tmp/e.jsonl")
        self.assertIn("structured_output", prompt)
        self.assertIn("CURRENT HANDOFF DATA", prompt)
        # This transport reports through events, and the runner sets a capture
        # context only for report_file providers.
        self.assertNotIn("capture_command", prompt)


class QwenRunnerIntegrationTests(unittest.TestCase):
    def test_the_runner_reads_qwen_events_through_the_adapter(self):
        path = write_events([system(), tool_use("call_test", "python3 -m pytest -q"),
                             tool_result("call_test", "2 passed"), terminal()])
        self.addCleanup(path.unlink)
        rows = support.events(str(path))
        self.assertEqual("thread.started", rows[0]["type"])
        self.assertEqual(0, items(rows)[0]["exit_code"])
        self.assertEqual("turn.completed", rows[-1]["type"])

    def test_a_validator_check_binds_to_an_executed_command(self):
        path = write_events([system(), tool_use("call_test", "python3 -m pytest -q"),
                             tool_result("call_test", "2 passed"), terminal()])
        self.addCleanup(path.unlink)
        with tempfile.TemporaryDirectory() as workspace:
            checks = [{"command": "python3 -m pytest -q", "evidence_ref": "event:", "exit_code": None}]
            support.verify_checks(checks, workspace, str(path))
            self.assertEqual(0, checks[0]["exit_code"])
            self.assertEqual("event:call_test", checks[0]["evidence_ref"])

    def test_a_check_no_command_ran_for_is_refused(self):
        path = write_events([system(), tool_use("call_denied", "python3 -m pytest -q"),
                             tool_result("call_denied", REFUSAL, is_error=True), terminal()])
        self.addCleanup(path.unlink)
        with tempfile.TemporaryDirectory() as workspace:
            checks = [{"command": "python3 -m pytest -q", "evidence_ref": "event:", "exit_code": None}]
            with self.assertRaises(ValueError):
                support.verify_checks(checks, workspace, str(path))


class QwenRoleTests(unittest.TestCase):
    def roles(self, models):
        return {"settings": {"single_model_mode": False,
                             "roles": {role: {"model": model} for role, model in models.items()}}}

    def test_default_roles_pass_cross_model_verification(self):
        dispatch.enforce_cross_model_verification(self.roles(qwen.DEFAULT_MODELS))

    def test_one_model_for_every_role_still_pauses(self):
        same = {role: "qwen/qwen3.8-max" for role in qwen.DEFAULT_MODELS}
        with self.assertRaises(support.Paused):
            dispatch.enforce_cross_model_verification(self.roles(same))

    def test_every_required_role_has_a_default(self):
        for role in ("astra", "terra", "sol", "completion", "glm", "plan_reviewer"):
            self.assertIn(role, qwen.DEFAULT_MODELS)
            self.assertIn(role, qwen.DEFAULT_REASONING_EFFORTS)


if __name__ == "__main__":
    unittest.main()
