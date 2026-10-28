"""Exact-bound planning report edits retain content and fail closed."""

import copy
import json
import tempfile
import unittest
from pathlib import Path

import autocode as runner
import autocode_report_edits as edits
import autocode_report_text_edits as text_edits
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


class MalformedReportEditsTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        # Retained live shape: the contract body closes before outer report
        # fields, with the opening contract wrapper and access_blockers absent.
        self.text = '{"acceptance_criteria":[],"retain_requirement_trace":true},"code_refs":[],"summary":"Résumé ⚡"}'
        self.source = self.root / "rejected.response.txt"
        self.source.write_text(self.text)
        self.descriptor = {
            "path": str(self.source),
            "sha256": file_hash(self.source),
            "format": "text",
            "content": self.text,
            "truncated": False,
        }
        self.schema = self.root / "original.schema.json"
        self.original_schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "contract": {"type": "object"},
                "code_refs": {"type": "array"},
                "summary": {"type": "string"},
                "access_blockers": {"type": "array"},
            },
            "required": ["contract", "code_refs", "summary", "access_blockers"],
        }
        self.schema.write_text(json.dumps(self.original_schema))
        self.offered = edits.prepare(self.schema, self.descriptor, "glm_revise")
        self.record = {
            "stage": "glm_revise_report_repair",
            "report_only": True,
            "schema": str(self.offered),
            "output": str(self.root / "repair.json"),
            "engine": "codex",
        }
        self.patch = {
            "repair_report": {
                "sha256": file_hash(self.source),
                "text_edits": [
                    {
                        "find": '{"acceptance_criteria":',
                        "replace": '{"access_blockers":[],"contract":{"acceptance_criteria":',
                    }
                ],
            }
        }

    def test_missing_outer_wrapper_and_field_use_small_patch_without_recopying(self):
        self.assertNotEqual(self.schema, self.offered)
        self.assertIn("BOUNDED JSON TEXT REPAIR", edits.instruction("glm_revise", self.descriptor))
        Path(self.record["output"]).write_text(json.dumps(self.patch))
        fixed = runner.load_stage_report(self.record)
        self.assertEqual({"acceptance_criteria": [], "retain_requirement_trace": True}, fixed["contract"])
        self.assertEqual("Résumé ⚡", fixed["summary"])
        self.assertEqual([], fixed["access_blockers"])
        self.assertEqual(self.text, self.source.read_text())
        self.assertEqual(self.patch, json.loads(Path(self.record["reported_output"]).read_text()))
        self.assertEqual(self.original_schema, edits.validation_schema(json.loads(self.offered.read_text())))
        self.assertIs(fixed, edits.expand(fixed, self.record))

    def test_text_repairs_keep_hash_route_offer_and_full_schema_guards(self):
        for fault in (
            "stale",
            "changed",
            "ordinary",
            "stage",
            "unoffered",
            "schema",
            "mixed",
            "bad_json",
            "wrong_mode",
        ):
            with self.subTest(fault=fault):
                patch, record = copy.deepcopy(self.patch), dict(self.record)
                if fault == "stale":
                    patch["repair_report"]["sha256"] = "0" * 64
                elif fault == "changed":
                    self.source.write_text(self.text + " ")
                elif fault == "ordinary":
                    record["report_only"] = False
                elif fault == "stage":
                    record["stage"] = "sol_report_repair"
                elif fault == "unoffered":
                    record["schema"] = str(self.schema)
                elif fault == "schema":
                    patch["repair_report"]["text_edits"].append({"find": '"Résumé ⚡"', "replace": "false"})
                elif fault == "mixed":
                    patch["summary"] = "new report"
                elif fault == "bad_json":
                    patch["repair_report"]["text_edits"][0]["replace"] = "["
                else:
                    patch["repair_report"]["edits"] = patch["repair_report"].pop("text_edits")
                Path(record["output"]).write_text(json.dumps(patch))
                try:
                    with self.assertRaises((ValueError, RuntimeError)):
                        runner.load_stage_report(record)
                finally:
                    self.source.write_text(self.text)

    def test_unique_original_matches_preserve_unicode_and_apply_simultaneously(self):
        fixed = text_edits.apply(
            '{"a":"é" "b":"old"}',
            [
                {"find": '"é" "b"', "replace": '"é", "b"'},
                {"find": '"old"', "replace": '"new"'},
            ],
        )
        self.assertEqual({"a": "é", "b": "new"}, fixed)
        # An inserted string is not a new target for another edit.
        with self.assertRaisesRegex(ValueError, "exactly once"):
            text_edits.apply(
                '{"a":0 "b":1}',
                [
                    {"find": '0 "b"', "replace": '0,"new"'},
                    {"find": '"new"', "replace": '"b"'},
                ],
            )

    def test_ambiguous_overlapping_empty_oversized_or_noop_edits_are_rejected(self):
        bad = [
            [],
            [{"find": "", "replace": "{}"}],
            [{"find": '"', "replace": ""}],
            [{"find": "aa", "replace": "b"}],  # overlapping occurrences also ambiguous
            [{"find": "missing", "replace": ""}],
            [{"find": "aaa", "replace": "aaa"}],
            [{"find": "aaa", "replace": "x"}, {"find": '"aaa"', "replace": '"x"'}],
            [{"find": "aaa", "replace": "x"}] * 33,
            [{"find": "aaa", "replace": "é" * 2048}],
            [{"find": "aaa", "replace": False}],
            [{"find": "aaa", "replace": "x", "extra": 0}],
        ]
        for patch in bad:
            with self.subTest(patch=patch):
                with self.assertRaises(ValueError):
                    text_edits.apply('{"a":"aaa" "b":1}', patch)
        with self.assertRaisesRegex(ValueError, "size bound"):
            text_edits.apply(
                '{"a":"' + "x" * (text_edits.MAX_REPORT_BYTES - 8) + '"}',
                [{"find": '{"a":', "replace": '{"longer_a":'}],
            )
        with self.assertRaisesRegex(ValueError, "object"):
            text_edits.apply("{bad", [{"find": "{bad", "replace": "[]"}])

    def test_valid_empty_truncated_prose_or_other_stage_cannot_use_text_protocol(self):
        for change in (
            {"content": ""},
            {"content": "{}"},
            {"truncated": True},
            {"content": "Here is a report: {bad"},
            {"content": []},
            {"content": "{" + "x" * text_edits.MAX_REPORT_BYTES},
        ):
            self.assertEqual(self.schema, edits.prepare(self.schema, {**self.descriptor, **change}, "glm_revise"))
        self.assertEqual(self.schema, edits.prepare(self.schema, self.descriptor, "sol"))
