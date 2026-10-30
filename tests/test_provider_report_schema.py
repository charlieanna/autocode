"""Strict model transports can retain full reports without accepting generic JSON edits."""

import json
import tempfile
import unittest
from pathlib import Path

import autocode_provider_report_schema as reports


class ProviderReportSchemaTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "report.schema.json"
        self.original = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        }

    def test_ordinary_schema_path_and_bytes_are_preserved(self):
        self.path.write_text(json.dumps(self.original))
        before = self.path.read_bytes()
        self.assertEqual(self.path, reports.full_report(self.path))
        self.assertEqual(before, self.path.read_bytes())

    def test_optional_edits_offer_restores_exact_original_validation(self):
        offered = {
            **self.original,
            "properties": {
                **self.original["properties"],
                "repair_report": {"type": "object", "properties": {"value": {}}},
            },
            "required": [],
            "x-autocode-report-edits": {"required": ["summary"], "source_path": "pinned.json", "sha256": "abc"},
        }
        self.path.write_text(json.dumps(offered))
        before = self.path.read_bytes()
        result = reports.full_report(self.path)
        self.assertNotEqual(self.path, result)
        self.assertEqual(self.original, json.loads(result.read_text()))
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(result, reports.full_report(self.path))

    def test_strict_gocode_transport_uses_full_report_schema(self):
        offered = {
            **self.original,
            "properties": {
                **self.original["properties"],
                "repair_report": {
                    "type": "object",
                    "properties": {"edits": {"type": "array", "items": {"properties": {"value": {}}}}},
                },
            },
            "required": [],
            "x-autocode-report-edits": {"required": ["summary"]},
        }
        self.path.write_text(json.dumps(offered))

        settings = {"engine": "opencode", "roles": {"astra": {"provider": "gocode"}}}
        result = reports.for_route(self.path, settings, "astra")

        self.assertNotEqual(self.path, result)
        self.assertEqual(self.original, json.loads(result.read_text()))

    def test_strict_run_provider_uses_full_report_schema_for_unassigned_role(self):
        offered = {
            **self.original,
            "properties": {
                **self.original["properties"],
                "repair_report": {
                    "type": "object",
                    "properties": {"edits": {"type": "array", "items": {"properties": {"value": {}}}}},
                },
            },
            "required": [],
            "x-autocode-report-edits": {"required": ["summary"]},
        }
        self.path.write_text(json.dumps(offered))

        result = reports.for_route(
            self.path,
            {"engine": "opencode", "provider": "gocode", "roles": {"requirements": {"provider": None}}},
            "requirements",
        )

        self.assertNotEqual(self.path, result)
        self.assertEqual(self.original, json.loads(result.read_text()))

    def test_other_transports_keep_optional_report_edits(self):
        offered = {
            **self.original,
            "properties": {
                **self.original["properties"],
                "repair_report": {"type": "object", "properties": {"value": {}}},
            },
            "required": [],
            "x-autocode-report-edits": {"required": ["summary"]},
        }
        self.path.write_text(json.dumps(offered))

        result = reports.for_route(
            self.path, {"engine": "opencode", "roles": {"astra": {"provider": "opencode"}}}, "astra"
        )

        self.assertEqual(self.path, result)
        self.assertIn("repair_report", json.loads(result.read_text())["properties"])

    def test_optional_required_list_remains_absent_for_legacy_schema(self):
        offered = {
            **self.original,
            "properties": {**self.original["properties"], "repair_report": {}},
            "x-autocode-report-edits": {"required": None},
        }
        self.path.write_text(json.dumps(offered))
        self.assertNotIn("required", json.loads(reports.full_report(self.path).read_text()))

    def test_malformed_offer_is_not_silently_relaxed(self):
        self.path.write_text(json.dumps({"type": "object", "properties": {}, "x-autocode-report-edits": {}}))
        with self.assertRaises((KeyError, ValueError)):
            reports.full_report(self.path)
