"""Compact prompt schemas retain the full output contract."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from providers import opencode


class CompactSchemaPromptTests(unittest.TestCase):
    def test_compiled_validator_prefers_receipt_fields_without_retyping_commands(self):
        import autocode_support as support
        from units import common

        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            (workspace / "answer.py").write_text("def answer(): return 42\n")
            state = {
                "version": 2,
                "task": "Verify the answer without changing the source",
                "workspace": str(workspace),
                "settings": {"roles": {"sol": {"engine": "opencode", "model": "fixture"}}, "limits": {}},
            }
            # Approval and filesystem identity are outside this prompt contract;
            # assemble the real request and role schema without a controller or provider.
            with (
                patch.object(common.goals, "execution_guard"),
                patch.object(
                    common.stage_context.source_scope, "snapshot", return_value={"revision": "s", "head": "h"}
                ),
            ):
                request = common.execution_request(
                    state, "sol", workspace / "state.json", Path(support.__file__).parent / "autocode-schemas"
                )
            schema = request.schema
            prompt = opencode.prompt_for_schema(request.prompt, schema, workspace / "events.jsonl")

        self.assertIn("independent read-only validation agent", prompt)
        self.assertIn("For a captured Validator check, prefer command null and exit_code null", prompt)
        self.assertIn("If either field is supplied, it must exactly match the receipt", prompt)
        self.assertIn("Other captured check reports must copy these exact values", prompt)
        self.assertNotIn("For a captured check, checks[].command must equal", prompt)
        self.assertIn("Never create or edit a receipt manually", prompt)
        self.assertIn("A PASS report must list at least one successful executed check", prompt)
        support.validate_schema(
            {"command": None, "exit_code": None, "evidence_ref": ".autocode/evidence/check.json"},
            schema["properties"]["checks"]["items"],
        )
        self.assertIn(json.dumps(schema, separators=(",", ":")), prompt)

    def test_conversation_keeps_the_schema_and_boundary_without_execution_evidence_instructions(self):
        schema = {"type": "object", "properties": {"assistant_reply": {"type": "string"}}}
        prompt = opencode.prompt_for_schema("Task\nCURRENT HANDOFF DATA\n{}", schema, Path("/run/events.jsonl"))
        self.assertIn(json.dumps(schema, separators=(",", ":")), prompt)
        self.assertIn("assistant_reply first", prompt)
        self.assertIn("filesystem boundary", prompt)
        self.assertIn("Never modify this log", prompt)
        self.assertIn("/run/events.jsonl", prompt)
        self.assertNotIn("A PASS report", prompt)
        self.assertNotIn("checks[].command", prompt)

    def test_planning_keeps_full_schema_without_execution_or_private_log_instructions(self):
        schema = {"properties": {"summary": {"type": "string"}, "concerns": {"type": "array"}}}
        packet = {"joint_planning": True, "goal_contract": {"task": "retained"}}
        original = "Review the plan\nCURRENT HANDOFF DATA\n" + json.dumps(packet)
        prompt = opencode.prompt_for_schema(original, schema, Path("/private-run/events.jsonl"))
        self.assertIn(json.dumps(schema, separators=(",", ":")), prompt)
        self.assertEqual(packet, json.loads(prompt.split("\nCURRENT HANDOFF DATA\n")[1]))
        self.assertNotIn("A PASS report", prompt)
        self.assertNotIn("checks[].command", prompt)
        self.assertNotIn("/private-run/events.jsonl", prompt)
        self.assertNotIn("--output .autocode/evidence", prompt)
        self.assertIn("strict filesystem boundary", prompt)
        self.assertIn("without tools", prompt)
        self.assertIn("missing evidence as a concern", prompt)

    def test_a_verdict_report_with_assistant_text_keeps_execution_evidence_rules(self):
        schema = {"properties": {"assistant_reply": {"type": "string"}, "checks": {"type": "array"}}}
        prompt = opencode.prompt_for_schema("Task\nCURRENT HANDOFF DATA\n{}", schema, Path("/run/events.jsonl"))
        self.assertIn("A PASS report", prompt)

    def test_schema_prompt_is_compact_and_round_trips_exactly(self):
        path = Path(__file__).resolve().parents[1] / "tools/autocode-schemas/v2/sol-report.schema.json"
        schema = json.loads(path.read_text())
        prompt = opencode.prompt_for_schema("Task\nCURRENT HANDOFF DATA\n{}", schema, Path("/run/events.jsonl"))
        prefix = prompt.split("\nCURRENT HANDOFF DATA\n", 1)[0]
        contract = prefix.index("\nOPENCODE OUTPUT CONTRACT\n")
        start = prefix.index("\n{", contract) + 1
        rendered, end = json.JSONDecoder().raw_decode(prefix[start:])
        rendered_json = prefix[start : start + end]

        self.assertEqual(schema, rendered)
        self.assertEqual(json.dumps(schema, separators=(",", ":")), rendered_json)
        self.assertLess(len(rendered_json), len(json.dumps(schema, indent=2)))


if __name__ == "__main__":
    unittest.main()
