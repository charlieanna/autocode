"""Exact-bound planning report edits retain content and fail closed."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import autocode as runner
import autocode_report_edits as edits
from autocode_util import file_hash, validate_schema


class ReportEditsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.base = {"summary": "original", "proposed_assumptions": [{"text": "Preserve empty pages"}], "extra": "bad"}
        self.source = self.root / "original.json"
        self.source.write_text(json.dumps(self.base))
        self.original_schema = {
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "proposed_assumptions": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "category": {"type": "string", "enum": ["behavior"]},
                        },
                        "required": ["text", "category"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["summary", "proposed_assumptions"],
            "additionalProperties": False,
        }
        self.schema = self.root / "schema.json"
        self.schema.write_text(json.dumps(self.original_schema))
        self.descriptor = {
            "path": str(self.source),
            "sha256": file_hash(self.source),
            "format": "json",
            "truncated": False,
            "content": self.base,
        }
        self.offered = edits.prepare(self.schema, self.descriptor, "requirements_gather")
        self.record = {
            "stage": "requirements_gather_report_repair",
            "report_only": True,
            "schema": str(self.offered),
            "output": str(self.root / "repair.json"),
            "engine": "codex",
        }
        self.patch = {
            "repair_report": {
                "sha256": file_hash(self.source),
                "edits": [
                    {"op": "remove", "path": ["extra"], "value": None},
                    {"op": "add", "path": ["proposed_assumptions", 0, "category"], "value": "behavior"},
                ],
            }
        }

    def test_report_loader_expands_and_retains_raw_patch_before_full_validation(self):
        Path(self.record["output"]).write_text(json.dumps(self.patch))
        fixed = runner.load_stage_report(self.record)
        validate_schema(fixed, self.original_schema)
        self.assertEqual(
            {"summary": "original", "proposed_assumptions": [{"text": "Preserve empty pages", "category": "behavior"}]},
            fixed,
        )
        self.assertEqual(self.patch, json.loads(Path(self.record["reported_output"]).read_text()))
        self.assertEqual(self.base, json.loads(self.source.read_text()))

    def test_full_response_stays_compatible_and_semantically_invalid_patch_still_fails(self):
        complete = edits.expand(self.patch, self.record)
        self.assertIs(complete, edits.expand(complete, self.record))
        for value in ["not-a-category", False]:
            patch = copy.deepcopy(self.patch)
            patch["repair_report"]["edits"][1]["value"] = value
            Path(self.record["output"]).write_text(json.dumps(patch))
            with self.assertRaises(ValueError):
                runner.load_stage_report(self.record)

    def test_stale_source_hash_stage_or_unoffered_patch_is_rejected(self):
        for kind in ["hash", "source", "stage", "ordinary", "legacy"]:
            with self.subTest(kind=kind):
                patch = copy.deepcopy(self.patch)
                record = dict(self.record)
                if kind == "hash":
                    patch["repair_report"]["sha256"] = "0" * 64
                elif kind == "source":
                    self.source.write_text("{}")
                elif kind == "stage":
                    record["stage"] = "terra_report_repair"
                elif kind == "ordinary":
                    record["report_only"] = False
                else:
                    record["schema"] = str(self.schema)
                try:
                    with self.assertRaises(ValueError):
                        edits.expand(patch, record)
                finally:
                    self.source.write_text(json.dumps(self.base))

    def test_invalid_or_overlapping_operations_never_apply(self):
        bad = [
            [{"op": "remove", "path": [], "value": None}],
            [{"op": "remove", "path": ["proposed_assumptions", 0], "value": None}],
            [{"op": "replace", "path": ["proposed_assumptions", True], "value": {}}],
            [{"op": "replace", "path": ["absent", "child"], "value": 0}],
            [{"op": "add", "path": ["summary"], "value": "new"}],
            [{"op": "remove", "path": ["extra"], "value": "not null"}],
            [
                {"op": "replace", "path": ["proposed_assumptions"], "value": []},
                {"op": "replace", "path": ["proposed_assumptions", 0], "value": {}},
            ],
            [{"op": "remove", "path": ["extra"], "value": None}] * 2,
            [],
            [{"op": "remove", "path": ["extra"], "value": None}] * 33,
        ]
        for operations in bad:
            with self.subTest(operations=operations):
                patch = copy.deepcopy(self.patch)
                patch["repair_report"]["edits"] = operations
                with self.assertRaises(ValueError):
                    edits.expand(patch, self.record)
                self.assertEqual(self.base, json.loads(self.source.read_text()))

    def test_generation_schema_roundtrip_and_mixed_response_rejection(self):
        offered = json.loads(self.offered.read_text())
        self.assertEqual("object", offered["type"])
        self.assertEqual(self.original_schema, edits.validation_schema(offered))
        mixed = {**self.patch, "summary": "replacement"}
        with self.assertRaisesRegex(ValueError, "mixed"):
            edits.expand(mixed, self.record)

    def test_unparsed_truncated_and_other_stage_reports_keep_original_schema(self):
        for source in [
            {**self.descriptor, "format": "text"},
            {**self.descriptor, "truncated": True},
            {**self.descriptor, "content": []},
        ]:
            self.assertEqual(self.schema, edits.prepare(self.schema, source, "requirements_gather"))
        self.assertEqual(self.schema, edits.prepare(self.schema, self.descriptor, "sol"))

    def test_legacy_or_conflicting_schemas_keep_the_original_protocol(self):
        for schema in [
            {},
            {"type": "object"},
            {"type": "array"},
            {"type": "object", "properties": {"repair_report": {"type": "string"}}},
        ]:
            self.schema.write_text(json.dumps(schema))
            self.assertEqual(self.schema, edits.prepare(self.schema, self.descriptor, "glm_revise"))
