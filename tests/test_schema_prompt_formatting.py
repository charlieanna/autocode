"""Compact prompt schemas retain the full output contract."""
import json
import unittest
from pathlib import Path

from providers import opencode


class CompactSchemaPromptTests(unittest.TestCase):
    def test_schema_prompt_is_compact_and_round_trips_exactly(self):
        path = Path(__file__).resolve().parents[1] / "tools/autocode-schemas/v2/sol-report.schema.json"
        schema = json.loads(path.read_text())
        prompt = opencode.prompt_for_schema(
            "Task\nCURRENT HANDOFF DATA\n{}", schema, Path("/run/events.jsonl"))
        prefix = prompt.split("\nCURRENT HANDOFF DATA\n", 1)[0]
        contract = prefix.index("\nOPENCODE OUTPUT CONTRACT\n")
        start = prefix.index("\n{", contract) + 1
        rendered, end = json.JSONDecoder().raw_decode(prefix[start:])
        rendered_json = prefix[start:start + end]

        self.assertEqual(schema, rendered)
        self.assertEqual(json.dumps(schema, separators=(",", ":")), rendered_json)
        self.assertLess(len(rendered_json), len(json.dumps(schema, indent=2)))


if __name__ == "__main__":
    unittest.main()
