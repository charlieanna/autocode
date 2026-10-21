"""Compact prompt schemas retain the full output contract."""

import json
import unittest
from pathlib import Path

from providers import opencode


class CompactSchemaPromptTests(unittest.TestCase):
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
