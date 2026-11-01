"""Reject bounded edits on malformed report-only responses that may be repair envelopes.

Issue #2347: A malformed terminal response from a first report repair may itself be
a repair envelope. This test suite verifies the system marks such sources with repair_response
on report_only=True malformed responses and declines bounded edits for them.
"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import autocode_report_edits as edits
import autocode_report_source as report_source
from autocode_util import file_hash


class RepairEnvelopeMarkerTests(unittest.TestCase):
    """Tests for repair_response marker and bounded-edit rejection."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def _write_report(self, text, name="repair.response.txt"):
        path = self.root / name
        path.write_text(text)
        return path

    def test_text_source_descriptor_preserves_marker_bytes_and_hash(self):
        malformed = '{"repair_report":{"edits":[]}]})'
        response = self._write_report(malformed)
        record = {
            "engine": "codex",
            "stage": "requirements_gather_report_repair",
            "original_stage": "requirements_gather",
            "report_only": True,
            "output": str(self.root / "repair.json"),
            "response_text": str(response),
        }
        source = report_source.repair_report_source(record)
        self.assertEqual("text", source["format"])
        self.assertEqual(malformed, source["content"])
        self.assertIn("parse_error", source)
        self.assertEqual(len(malformed.encode()), source["bytes"])
        self.assertEqual(hashlib.sha256(malformed.encode()).hexdigest(), source["sha256"])
        self.assertFalse(source.get("truncated", False))
        self.assertTrue(source.get("repair_response"))

    def test_text_source_with_marker_ineligible_for_bounded_edits(self):
        malformed = '{"repair_report":{"edits":[]}]})'
        response = self._write_report(malformed)
        record = {
            "engine": "codex",
            "stage": "requirements_gather_report_repair",
            "original_stage": "requirements_gather",
            "report_only": True,
            "output": str(self.root / "repair.json"),
            "response_text": str(response),
        }
        source = report_source.repair_report_source(record)
        schema = self.root / "schema.json"
        schema.write_text(
            json.dumps({"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]})
        )
        self.assertFalse(edits.eligible("requirements_gather", source))
        self.assertEqual("", edits.instruction("requirements_gather", source))
        offered = edits.prepare(schema, source, "requirements_gather")
        self.assertEqual(schema, offered)

    def test_malformed_initial_report_without_marker_offers_text_edit(self):
        malformed = '{"summary":"incomplete","findings":[{"id":"F1"'
        response = self._write_report(malformed, "initial.response.txt")
        record = {
            "engine": "codex",
            "stage": "terra",
            "output": str(self.root / "report.json"),
            "response_text": str(response),
        }
        source = report_source.repair_report_source(record)
        self.assertEqual("text", source["format"])
        self.assertNotIn("repair_response", source)

        schema = self.root / "schema.json"
        schema.write_text(
            json.dumps({"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]})
        )
        self.assertTrue(edits.eligible("requirements_gather", source))
        instruction = edits.instruction("requirements_gather", source)
        self.assertIn("BOUNDED JSON TEXT REPAIR", instruction)
        offered = edits.prepare(schema, source, "requirements_gather")
        self.assertNotEqual(schema, offered)
        result_schema = json.loads(offered.read_text())
        self.assertIn("repair_report", result_schema["properties"])

    def test_valid_json_report_only_has_no_marker_and_supports_edits(self):
        valid = json.dumps({"summary": "corrected", "checks": []})
        response = self._write_report(valid)
        record = {
            "engine": "codex",
            "stage": "astra_discovery_report_repair",
            "original_stage": "astra_discovery",
            "report_only": True,
            "output": str(self.root / "repair.json"),
            "response_text": str(response),
        }
        source = report_source.repair_report_source(record)
        self.assertEqual("json", source["format"])
        self.assertEqual(json.loads(valid), source["content"])
        self.assertNotIn("repair_response", source)

        schema = self.root / "schema.json"
        schema.write_text(
            json.dumps(
                {
                    "type": "object",
                    "properties": {"summary": {"type": "string"}, "checks": {"type": "array"}},
                    "required": ["summary"],
                }
            )
        )
        self.assertTrue(edits.eligible("astra_discovery", source))
        instruction = edits.instruction("astra_discovery", source)
        self.assertIn("BOUNDED REPORT EDITS", instruction)
        offered = edits.prepare(schema, source, "astra_discovery")
        self.assertNotEqual(schema, offered)

    def test_existing_envelope_ineligibility_remains_covered_by_existing_tests(self):
        envelope = {"repair_report": {"summary": "nested"}}
        source_path = self._write_report(json.dumps(envelope), "envelope.json")
        descriptor = {
            "path": str(source_path),
            "sha256": file_hash(source_path),
            "format": "json",
            "content": envelope,
            "truncated": False,
        }
        schema = self.root / "schema.json"
        schema.write_text(
            json.dumps({"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]})
        )
        self.assertFalse(edits.eligible("requirements_gather", descriptor))
        offered = edits.prepare(schema, descriptor, "requirements_gather")
        self.assertEqual(schema, offered)
