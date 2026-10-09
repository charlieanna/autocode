"""A final message that is only a shell command (issue #512): recognized, refused and routed.

Pure rules. The behavior a person sees, a Plan Reviewer corrected or stopped through the CLI,
is in test_cmd_only_report_cli.
"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import autocode_cmd_only_report as cmd_only
import autocode_failures as failures
import autocode_format_correction as format_correction
import autocode_report_source as report_source

REPORT = {"summary": "Plan reviewed", "concerns": [], "obligation_decisions": []}
SCHEMA = {"type": "object", "required": ["summary", "concerns"],
          "properties": {"summary": {"type": "string"}, "concerns": {"type": "array"},
                         "obligation_decisions": {"type": "array"}}}


class CommandOnlyShapeTests(unittest.TestCase):
    def test_a_command_with_no_report_field_is_command_only(self):
        for value in ({"cmd": "ls docs/dti-11734/ && wc -l docs/*.md"},
                      {"command": ["bash", "-lc", "ls docs"]},
                      {"cmd": "ls", "workdir": "/repo", "timeout_ms": 1000, "yield_time_ms": 500}):
            with self.subTest(value=value):
                self.assertTrue(cmd_only.is_command_only(value, SCHEMA))

    def test_a_report_or_anything_beyond_a_shell_call_is_not(self):
        for value in (REPORT, {**REPORT, "cmd": "ls"}, {"cmd": "ls", "note": "then report"}, {"cmd": ""},
                      {"cmd": "   "}, {"command": []}, {"command": ["ls", 3]}, {"cmd": 5}, {"workdir": "/repo"},
                      {}, ["cmd", "ls"], "ls", None):
            with self.subTest(value=value):
                self.assertFalse(cmd_only.is_command_only(value, SCHEMA))

    def test_a_schema_that_declares_the_key_owns_it(self):
        schema = {"type": "object", "properties": {"command": {"type": "string"}}}
        self.assertFalse(cmd_only.is_command_only({"command": "ls"}, schema))
        self.assertTrue(cmd_only.is_command_only({"cmd": "ls"}, schema))

    def test_a_report_embedded_in_the_command_is_not_unwrapped(self):
        wrapped = {"cmd": "cat <<'EOF'\n" + json.dumps(REPORT) + "\nEOF"}
        with self.assertRaises(cmd_only.CommandOnlyReport):
            cmd_only.refuse(wrapped)

    def test_refuse_passes_a_report_through_unchanged_and_names_the_defect(self):
        with tempfile.TemporaryDirectory() as directory:
            schema = Path(directory) / "stage.schema.json"
            schema.write_text(json.dumps(SCHEMA))
            self.assertIs(REPORT, cmd_only.refuse(REPORT, str(schema)))
            with self.assertRaises(cmd_only.CommandOnlyReport) as raised:
                cmd_only.refuse({"cmd": "ls"}, str(schema))
            owned = Path(directory) / "command.schema.json"
            owned.write_text(json.dumps({"type": "object", "properties": {"command": {"type": "string"}}}))
            self.assertEqual({"command": "ls"}, cmd_only.refuse({"command": "ls"}, str(owned)))
            # A missing or unreadable schema never lets a command through.
            schema.write_text("{")
            with self.assertRaises(cmd_only.CommandOnlyReport):
                cmd_only.refuse({"cmd": "ls"}, str(schema))
            with self.assertRaises(cmd_only.CommandOnlyReport):
                cmd_only.refuse({"cmd": "ls"}, str(Path(directory) / "missing.json"))
        error = raised.exception
        self.assertIsInstance(error, RuntimeError)  # the runner's ordinary report rejection
        self.assertEqual(cmd_only.ERROR, str(error))
        self.assertTrue(cmd_only.matches(error))
        self.assertFalse(cmd_only.matches("$: missing summary"))
        # The failure ledger groups it as its own class, apart from other invalid reports.
        record = {"stage": "astra_challenge", "source_revision": "rev"}
        self.assertEqual("CommandOnlyReport", failures.identity(record, error)["error_class"])
        self.assertNotEqual(failures.key(failures.identity(record, error)),
                            failures.key(failures.identity(record, RuntimeError("$: missing summary"))))


class CorrectionRouteTests(unittest.TestCase):
    """The one same-session correction (autocode_format_correction) and the full repair's instruction."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.events = Path(directory.name) / "plan-challenge-01.jsonl"
        self.events.write_text(json.dumps({"type": "thread.started", "thread_id": "reviewer-session"}) + "\n"
                               + json.dumps({"type": "turn.completed"}) + "\n")
        self.route = {"engine": "codex", "provider": "gocode", "model": "reviewer-model", "reasoning_effort": "high"}
        self.state = {"settings": {"provider": "opencode", "roles": {"plan_reviewer": dict(self.route)}}}

    def pending(self, error=cmd_only.ERROR, **original):
        record = {"role": "astra", "route_role": "plan_reviewer", "stage": "astra_challenge", "planning": True,
                  "engine": "codex", "supports_sessions": True, "events": str(self.events),
                  "schema": str(self.events.with_name("stage.schema.json")),
                  "launch_route": dict(self.route), **original}
        return {"original": record, "attempts": 0, "error": error}

    def test_a_command_final_resumes_its_own_session_on_a_planning_stage(self):
        self.assertEqual("reviewer-session", format_correction.session_for(self.state, self.pending()))
        opencode = self.pending(engine="opencode", planning=False)
        self.assertEqual("reviewer-session", format_correction.session_for(self.state, opencode))

    def test_the_correction_is_targeted_and_runs_nothing(self):
        prompt = format_correction.prompt(cmd_only.ERROR)
        self.assertEqual(cmd_only.CORRECTION, prompt)
        self.assertIn("shell command", prompt)
        self.assertIn("never runs a command", prompt)
        self.assertIn("Run no more commands", prompt)
        self.assertNotIn("keep its content unchanged", prompt)  # there is no report to keep
        self.assertNotIn("CURRENT HANDOFF DATA", prompt)

    def test_one_correction_per_rejection_and_none_once_a_full_repair_ran(self):
        for spent in ({"correction_attempted": True}, {"attempts": 1}):
            with self.subTest(spent=spent):
                self.assertIsNone(format_correction.session_for(self.state, {**self.pending(), **spent}))

    def test_no_session_to_resume_means_the_full_repair(self):
        for original in ({"supports_sessions": False}, {"engine": "custom"},
                         {"launch_route": {**self.route, "model": "fallback-model"}}):
            with self.subTest(original=original):
                self.assertIsNone(format_correction.session_for(self.state, self.pending(**original)))
        self.events.write_text(json.dumps({"type": "turn.completed"}) + "\n")
        self.assertIsNone(format_correction.session_for(self.state, self.pending()))

    def correct(self, pending, route_role):
        """Run execute as the runner does, the correction's route being ``route_role``; the launch kwargs, or None."""
        runtime = SimpleNamespace(planning=SimpleNamespace(route_for=lambda state, stage, role: route_role),
                                  run_role=Mock(return_value=({}, {})), write_json=Mock(), account_stage=Mock(),
                                  accept_repaired_report=Mock())
        self.state["pending_report_repair"] = pending
        ran = format_correction.execute(runtime, self.state, self.events.parent, self.events.parent)
        self.assertEqual(ran, runtime.run_role.called)
        return runtime.run_role.call_args.kwargs if ran else None

    def test_a_session_is_resumed_only_on_the_route_the_correction_runs_on(self):
        launch = self.correct(self.pending(), "plan_reviewer")
        self.assertEqual(("reviewer-session", "reviewer-model"), (launch["resume_session"], launch["model"]))
        # The Plan Reviewer's one-use fallback (autocode_reviewer_fallback) ran this attempt on another
        # configured role's model; the correction runs on the reviewer's own route, so it must not resume
        # that session on it: the full repair runs instead, and the correction stays unspent.
        fallback = {"engine": "opencode", "provider": "opencode", "model": "fallback-model", "reasoning_effort": "high"}
        self.state["settings"]["roles"].update(plan_reviewer={**fallback, "model": "reviewer-model"}, glm=dict(fallback))
        pending = self.pending(engine="opencode", route_role="glm", launch_route=dict(fallback))
        self.assertIsNone(self.correct(pending, "plan_reviewer"))
        self.assertNotIn("correction_attempted", pending)

    def test_other_rejections_keep_the_existing_rules(self):
        # The serialization error stays OpenCode-only and never resumes a planning stage; other errors never resume.
        self.assertIsNone(format_correction.session_for(self.state, self.pending(error=format_correction.FORMAT_ERROR)))
        self.assertIsNone(format_correction.session_for(
            self.state, self.pending(error=format_correction.FORMAT_ERROR, engine="opencode")))
        self.assertEqual("reviewer-session", format_correction.session_for(
            self.state, self.pending(error=format_correction.FORMAT_ERROR, engine="opencode", planning=False)))
        self.assertIsNone(format_correction.session_for(self.state, self.pending(error="$: missing summary")))

    def test_the_full_repair_is_told_the_rejected_report_is_a_command(self):
        instruction = report_source.repair_report_instruction(self.pending())
        self.assertEqual(cmd_only.REPAIR_INSTRUCTION, instruction)
        self.assertIn("did not run that command", instruction)
        self.assertTrue(instruction.endswith("Do not redo "))  # the repair prompt continues the sentence
        later = {**self.pending(error="$.concerns: expected array")}
        self.assertEqual("report from this completed stage. Do not redo ",
                         report_source.repair_report_instruction(later))


if __name__ == "__main__":
    unittest.main()
