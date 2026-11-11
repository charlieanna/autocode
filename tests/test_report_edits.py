"""Exact-bound planning report edits retain content and fail closed."""

import copy
import itertools
import json
import tempfile
import unittest
from pathlib import Path

import autocode as runner
import autocode_goals as goals
import autocode_report_edits as edits
import autocode_report_text_edits as text_edits
from autocode_util import file_hash, model_output_schema, validate_schema
from goal_fixtures import body


class ReportEditsTests(unittest.TestCase):
    def test_review_patch_preserves_commands_and_rejects_invented_execution(self):
        import shlex

        from .test_check_command_from_receipt import ARGV, CommandFromReceiptTests

        receipt = CommandFromReceiptTests()
        receipt.setUp()
        self.addCleanup(receipt.doCleanups)
        check = {"command": shlex.join(ARGV), "exit_code": 0, "evidence_ref": ".autocode/evidence/check.json"}
        original = {"checks": [check], "summary": "incorrect citation"}
        schema = {"type": "object", "properties": {"checks": {"type": "array"}, "summary": {"type": "string"}}}
        self.source.write_text(json.dumps(original))
        self.schema.write_text(json.dumps(schema))
        descriptor = {**self.descriptor, "content": original, "sha256": file_hash(self.source)}
        for stage in ("tester", "review_completion", "review_checkpoint"):
            with self.subTest(stage=stage):
                offered = edits.prepare(self.schema, descriptor, stage)
                self.assertNotEqual(self.schema, offered)
                record = {**self.record, "stage": stage + "_report_repair", "schema": str(offered)}
                patch = {
                    "repair_report": {
                        "sha256": descriptor["sha256"],
                        "edits": [{"op": "replace", "path": ["summary"], "value_json": '"corrected citation"'}],
                    }
                }
                repaired = edits.expand(patch, record)
                self.assertEqual([check], repaired["checks"])
                runner.support.verify_checks(repaired["checks"], receipt.workspace, receipt.events)
                patch["repair_report"]["edits"].append(
                    {"op": "replace", "path": ["checks", 0, "command"], "value_json": '"invented command"'}
                )
                forged = edits.expand(patch, record)
                with self.assertRaises(ValueError):
                    runner.support.verify_checks(forged["checks"], receipt.workspace, receipt.events)
                self.assertEqual(original, json.loads(self.source.read_text()))

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
                    {"op": "remove", "path": ["extra"], "value_json": "null"},
                    {
                        "op": "add",
                        "path": ["proposed_assumptions", 0, "category"],
                        "value_json": '"behavior"',
                    },
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

    def test_observed_bare_requirements_patch_retains_raw_response_and_pinned_source(self):
        base = {"summary": "original", "access_blockers": "controlled report-shape fault"}
        schema = {
            "type": "object",
            "properties": {"summary": {"type": "string"}, "access_blockers": {"type": "array"}},
            "required": ["summary", "access_blockers"],
            "additionalProperties": False,
        }
        self.source.write_text(json.dumps(base))
        self.schema.write_text(json.dumps(schema))
        source_bytes = self.source.read_bytes()
        descriptor = {**self.descriptor, "sha256": file_hash(self.source), "content": base}
        offered = edits.prepare(self.schema, descriptor, "requirements_gather")
        record = {**self.record, "schema": str(offered)}
        # The live response omitted only repair_report; bind this fixture's
        # identical edit to its own source bytes rather than a saved run path.
        raw = {
            "sha256": descriptor["sha256"],
            "edits": [{"op": "replace", "path": ["access_blockers"], "value_json": "[]"}],
        }
        Path(record["output"]).write_text(json.dumps(raw))
        self.assertEqual({"summary": "original", "access_blockers": []}, runner.load_stage_report(record))
        self.assertEqual(raw, json.loads(Path(record["reported_output"]).read_text()))
        self.assertEqual(source_bytes, self.source.read_bytes())
        self.assertEqual(descriptor["sha256"], file_hash(self.source))
        from .test_opencode import event, terminal

        events = self.root / "native-events.jsonl"
        events.write_text(json.dumps(event("text", text=json.dumps(raw))) + "\n" + json.dumps(terminal()) + "\n")
        event_hash = file_hash(events)
        native = {
            **self.record,
            "engine": "opencode",
            "schema": str(offered),
            "events": str(events),
            "output": str(self.root / "native-repair.json"),
        }
        self.assertEqual({"summary": "original", "access_blockers": []}, runner.load_stage_report(native))
        self.assertEqual(raw, json.loads(Path(native["reported_output"]).read_text()))
        self.assertEqual(raw, json.loads(Path(native["response_text"]).read_text()))
        self.assertEqual(event_hash, file_hash(events))
        self.assertEqual(source_bytes, self.source.read_bytes())

    def test_bare_patch_refuses_wrong_identity_stage_unoffered_and_malformed_edits(self):
        original = self.source.read_bytes()
        variants = (
            ("hash", {**self.patch["repair_report"], "sha256": "0" * 64}, {}),
            ("stage", self.patch["repair_report"], {"stage": "builder_report_repair"}),
            ("ordinary", self.patch["repair_report"], {"report_only": False}),
            ("unoffered", self.patch["repair_report"], {"schema": str(self.schema)}),
            ("extra", {**self.patch["repair_report"], "extra": "not a report"}, {}),
            ("mixed", {**self.patch["repair_report"], "summary": "not a full report"}, {}),
            ("mixed wrapper", {**self.patch, **self.patch["repair_report"]}, {}),
            ("text", {"sha256": file_hash(self.source), "text_edits": []}, {}),
            ("missing edits", {"sha256": file_hash(self.source)}, {}),
            ("empty edits", {"sha256": file_hash(self.source), "edits": []}, {}),
            ("malformed edits", {"sha256": file_hash(self.source), "edits": "not an array"}, {}),
            (
                "bad operation",
                {
                    "sha256": file_hash(self.source),
                    "edits": [{"op": "invent", "path": ["extra"], "value_json": "null"}],
                },
                {},
            ),
            (
                "bad value",
                {
                    "sha256": file_hash(self.source),
                    "edits": [{"op": "remove", "path": ["extra"], "value_json": "invalid JSON"}],
                },
                {},
            ),
            ("overlap", {"sha256": file_hash(self.source), "edits": [self.patch["repair_report"]["edits"][0]] * 2}, {}),
            (
                "invalid result",
                {
                    "sha256": file_hash(self.source),
                    "edits": [{"op": "replace", "path": ["summary"], "value_json": "false"}],
                },
                {},
            ),
        )
        for label, value, changes in variants:
            record = {**self.record, **changes, "output": str(self.root / (label + ".json"))}
            Path(record["output"]).write_text(json.dumps(value))
            with self.subTest(label=label), self.assertRaises(ValueError):
                runner.load_stage_report(record)
            self.assertEqual(original, self.source.read_bytes())
        self.source.write_text("{}")
        with self.assertRaisesRegex(ValueError, "Pinned rejected report changed"):
            edits.expand(copy.deepcopy(self.patch["repair_report"]), self.record)

    def test_bare_patch_requires_the_exact_offered_json_protocol(self):
        original = json.loads(self.offered.read_text())
        variants = []
        for field, value in (("format", "text"), ("stage", "builder")):
            schema = copy.deepcopy(original)
            schema[edits.META][field] = value
            variants.append(schema)
        for change in (
            lambda schema: schema.update(required=[]),
            lambda schema: schema["properties"].update(extra={"type": "string"}),
            lambda schema: schema["properties"]["repair_report"].update(anyOf=[]),
            lambda schema: schema["properties"]["repair_report"]["anyOf"].__setitem__(0, None),
            lambda schema: schema["properties"]["repair_report"]["anyOf"][0]["properties"].pop("edits"),
            lambda schema: schema["properties"]["repair_report"]["anyOf"][0].update(required=[]),
        ):
            schema = copy.deepcopy(original)
            change(schema)
            variants.append(schema)
        for index, schema in enumerate(variants):
            self.offered.write_text(json.dumps(schema))
            with self.subTest(index=index), self.assertRaises(ValueError):
                edits.expand(copy.deepcopy(self.patch["repair_report"]), self.record)

    def test_prepared_edit_values_have_a_provider_supported_type(self):
        schema = json.loads(self.offered.read_text())
        self.assertEqual(["repair_report"], schema["required"])
        self.assertEqual(["repair_report"], list(schema["properties"]))
        repair = schema["properties"]["repair_report"]
        self.assertEqual(2, len(repair["anyOf"]))
        edit_properties = repair["anyOf"][0]["properties"]["edits"]["items"]["properties"]
        self.assertEqual("string", edit_properties["value_json"]["type"])
        self.assertNotIn("value", edit_properties)
        strict = model_output_schema(schema)
        self.assertEqual(set(strict["properties"]), set(strict["required"]))
        self.assertEqual(strict["required"], schema["required"])
        for branch in repair["anyOf"]:
            if branch.get("type") == "object":
                self.assertEqual(set(branch["properties"]), set(branch["required"]))
                self.assertFalse(branch["additionalProperties"])

    def test_wrapped_full_review_keeps_exact_legacy_criteria_and_rejects_conflicts(self):
        criterion = "Preserve the approved behavior"
        schema = {
            "type": "object",
            "properties": {
                "acceptance_criteria": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"id": {"type": "string"}, "status": {"type": "string"}},
                        "required": ["id", "status"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["acceptance_criteria"],
            "additionalProperties": False,
        }
        self.schema.write_text(json.dumps(schema))
        offered = edits.prepare(self.schema, self.descriptor, "review_completion")
        state = {"acceptance_criteria": [{"id": "C1", "criterion": criterion}]}
        correct = {"acceptance_criteria": [{"id": "C1", "status": "verified", "criterion": criterion}]}
        for label, report, error in (
            ("matching", correct, None),
            (
                "conflicting",
                {"acceptance_criteria": [{"id": "C1", "status": "verified", "criterion": "changed"}]},
                "conflicts",
            ),
            ("unknown", {**correct, "invented": True}, "unexpected fields"),
            ("missing", {"acceptance_criteria": []}, "each approved criterion"),
            ("scalar", "not a report", "object"),
        ):
            with self.subTest(label=label):
                record = {
                    **self.record,
                    "stage": "review_completion_report_repair",
                    "original_stage": "review_completion",
                    "schema": str(offered),
                    "output": str(self.root / (label + ".json")),
                }
                wrapped = {"repair_report": report}
                Path(record["output"]).write_text(json.dumps(wrapped))
                if error:
                    with self.assertRaisesRegex(ValueError, error):
                        runner.load_stage_report(record, state=state)
                else:
                    self.assertEqual(correct, runner.load_stage_report(record, state=state))
                    self.assertEqual(wrapped, json.loads(Path(record["reported_output"]).read_text()))

    def test_report_edit_instructions_require_json_encoded_values(self):
        instruction = edits.instruction("requirements_gather", self.descriptor)
        self.assertIn("value_json", instruction)
        self.assertIn("JSON-encoded", instruction)
        self.assertIn('string "null" for remove', instruction)

    def test_apply_decodes_json_values_without_coercing_their_types(self):
        result = edits.apply(
            {"kept": True, "removed": "old"},
            [
                {"op": "replace", "path": ["kept"], "value_json": "false"},
                {"op": "remove", "path": ["removed"], "value_json": "null"},
                {"op": "add", "path": ["added"], "value_json": '{"items":[1,"two"]}'},
            ],
        )
        self.assertEqual({"kept": False, "added": {"items": [1, "two"]}}, result)

    def test_apply_rejects_malformed_or_oversized_json_values(self):
        for raw in ("{broken", "x" * (edits.MAX_EDIT_VALUE_BYTES + 1)):
            with self.subTest(raw_length=len(raw)):
                with self.assertRaisesRegex(ValueError, "value_json"):
                    edits.apply({"field": 1}, [{"op": "replace", "path": ["field"], "value_json": raw}])

    def test_bare_text_replacing_a_text_field_is_that_text(self):
        # The 2026-11-11 Requirements repair: correct removals, then a summary sent as bare text,
        # not JSON. The whole attempt was refused and a third one launched to resend the same edits.
        base = {
            "summary": "old summary",
            "requirements": [{"id": "R1", "source_quote": "pick a UI issue"}, {"id": "R2", "source_quote": "x"}],
            "notes": ["first"],
        }
        result = edits.apply(
            base,
            [
                {"op": "remove", "path": ["requirements", 1], "value_json": "null"},
                {"op": "replace", "path": ["summary"], "value_json": "The user delegated the pick: {see R1}."},
                {"op": "replace", "path": ["notes", 0], "value_json": "plain note"},
            ],
        )
        self.assertEqual("The user delegated the pick: {see R1}.", result["summary"])
        self.assertEqual(["plain note"], result["notes"])
        self.assertEqual(["R1"], [row["id"] for row in result["requirements"]])
        self.assertEqual("old summary", base["summary"])

    def test_text_that_is_not_json_stays_refused_where_its_meaning_is_unclear(self):
        base = {"summary": "old", "count": 1, "items": ["a"], "rows": [{"id": "R1"}]}
        for op, path in (
            ("replace", ["count"]),  # a number, not text
            ("replace", ["items"]),  # a list, not text
            ("replace", ["rows", 0]),  # an object, not text
            ("add", ["absent"]),  # no field to say what type is meant
            ("remove", ["summary"]),  # remove takes "null"
        ):
            with self.subTest(op=op, path=path):
                with self.assertRaisesRegex(ValueError, "not JSON.*own quotes") as caught:
                    edits.apply(base, [{"op": op, "path": path, "value_json": "bare words"}])
                self.assertIn(json.dumps(path), str(caught.exception))
        with self.assertRaisesRegex(ValueError, "not JSON"):  # a broken quoted string: where does it end?
            edits.apply(base, [{"op": "replace", "path": ["summary"], "value_json": '"unterminated'}])

    def test_apply_keeps_saved_raw_value_edit_format_compatible(self):
        self.assertEqual(
            {"field": {"kept": [1]}},
            edits.apply({}, [{"op": "add", "path": ["field"], "value": {"kept": [1]}}]),
        )

    def test_array_removals_and_neighbor_edits_use_original_indices_in_any_order(self):
        original = {"rows": [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "d", "refs": [1, 2, 3]}]}
        operations = [
            {"op": "remove", "path": ["rows", 0], "value_json": "null"},
            {"op": "remove", "path": ["rows", 2], "value_json": "null"},
            {"op": "replace", "path": ["rows", 1], "value_json": '{"id":"corrected b"}'},
            {"op": "remove", "path": ["rows", 3, "refs", 1], "value_json": "null"},
        ]
        for ordered in itertools.permutations(operations):
            with self.subTest(paths=[row["path"] for row in ordered]):
                self.assertEqual(
                    {"rows": [{"id": "corrected b"}, {"id": "d", "refs": [1, 3]}]},
                    edits.apply(original, ordered),
                )
        self.assertEqual(["a", "b", "c", "d"], [row["id"] for row in original["rows"]])
        self.assertEqual([1, 2, 3], original["rows"][3]["refs"])

    def test_removing_the_only_array_item_keeps_the_empty_array(self):
        self.assertEqual(
            {"rows": []},
            edits.apply({"rows": ["extra"]}, [{"op": "remove", "path": ["rows", 0], "value_json": "null"}]),
        )

    def test_one_bounded_removal_preserves_a_large_review_and_its_checks(self):
        decisions = [
            {"gap_id": f"L{i}", "reason": "Independently checked exact source and test. " * 5} for i in range(319)
        ]
        original = {"checks": [{"command": "original captured check", "exit_code": 0}], "decisions": decisions}
        rejected = copy.deepcopy(original)
        rejected["decisions"].insert(285, {"gap_id": "not-offered", "reason": "Extraneous entry"})
        schema = {
            "type": "object",
            "properties": {
                "checks": {"type": "array"},
                "decisions": {"type": "array", "maxItems": 319},
            },
        }
        self.source.write_text(json.dumps(rejected))
        self.schema.write_text(json.dumps(schema))
        source = {**self.descriptor, "content": rejected, "sha256": file_hash(self.source)}
        offered = edits.prepare(self.schema, source, "tester")
        record = {**self.record, "stage": "tester_report_repair", "schema": str(offered)}
        patch = {
            "repair_report": {
                "sha256": source["sha256"],
                "edits": [{"op": "remove", "path": ["decisions", 285], "value_json": "null"}],
            }
        }
        self.assertLess(len(json.dumps(patch)), 256)
        fixed = edits.expand(patch, record)
        validate_schema(fixed, schema)
        self.assertEqual(original, fixed)
        self.assertEqual(rejected, json.loads(self.source.read_text()))

    def test_array_remove_instructions_keep_original_indices_and_avoid_recopying(self):
        instruction = edits.instruction("tester", self.descriptor)
        self.assertIn("original array indices", instruction)
        self.assertIn("remove the item instead of rewriting the array", instruction)

    def test_saved_legacy_patch_schema_and_raw_values_still_expand(self):
        legacy_schema = json.loads(self.offered.read_text())
        metadata = legacy_schema[edits.META]
        metadata.pop("original_schema")
        repair = legacy_schema["properties"]["repair_report"]["anyOf"][0]
        edit_schema = repair["properties"]["edits"]["items"]
        edit_schema["properties"]["value"] = {}
        edit_schema["properties"].pop("value_json")
        edit_schema["required"] = ["op", "path", "value"]
        legacy_schema = {
            "type": "object",
            "properties": {**copy.deepcopy(self.original_schema["properties"]), "repair_report": repair},
            "required": [],
            "additionalProperties": False,
            edits.META: metadata,
        }
        schema_path = self.root / "legacy-schema.json"
        schema_path.write_text(json.dumps(legacy_schema))
        record = {**self.record, "schema": str(schema_path)}
        patch = copy.deepcopy(self.patch)
        for operation in patch["repair_report"]["edits"]:
            operation["value"] = json.loads(operation.pop("value_json"))
        Path(record["output"]).write_text(json.dumps(patch))

        fixed = runner.load_stage_report(record)

        self.assertEqual(
            {"summary": "original", "proposed_assumptions": [{"text": "Preserve empty pages", "category": "behavior"}]},
            fixed,
        )

    def test_discovery_edits_do_not_authorize_removing_protected_behavior(self):
        previous = body()
        previous["required_behaviors"].append("Preserve the existing greeting format")
        base = {"contract": previous, "extra": "bad"}
        self.source.write_text(json.dumps(base))
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"contract": goals.BODY_SCHEMA},
            "required": ["contract"],
        }
        self.schema.write_text(json.dumps(schema))
        descriptor = {**self.descriptor, "content": base, "sha256": file_hash(self.source)}
        offered = edits.prepare(self.schema, descriptor, "draft_plan")
        record = {**self.record, "stage": "draft_plan_report_repair", "schema": str(offered)}
        patch = {
            "repair_report": {
                "sha256": descriptor["sha256"],
                "edits": [
                    {"op": "remove", "path": ["extra"], "value_json": "null"},
                    {
                        "op": "replace",
                        "path": ["contract", "required_behaviors"],
                        "value_json": '["Weaken the requested outcome"]',
                    },
                ],
            }
        }
        Path(record["output"]).write_text(json.dumps(patch))
        fixed = runner.load_stage_report(record)
        state = {"goal_contract": {"body": previous, "approval_status": "approved", "revision": 1}}
        with self.assertRaisesRegex(ValueError, "without a user-backed contract change"):
            goals.revision_guard(state, fixed["contract"], [], "draft_plan")
        patch["repair_report"]["edits"][1] = {
            "op": "remove",
            "path": ["contract", "required_behaviors", 0],
            "value_json": "null",
        }
        remove_record = {
            **self.record,
            "stage": "draft_plan_report_repair",
            "schema": str(offered),
            "output": str(self.root / "remove-behavior-repair.json"),
        }
        Path(remove_record["output"]).write_text(json.dumps(patch))
        fixed = runner.load_stage_report(remove_record)
        with self.assertRaisesRegex(ValueError, "without a user-backed contract change"):
            goals.revision_guard(state, fixed["contract"], [], "draft_plan")
        self.assertEqual(base, json.loads(self.source.read_text()))

    def test_full_response_stays_compatible_and_semantically_invalid_patch_still_fails(self):
        complete = edits.expand(self.patch, self.record)
        self.assertIs(complete, edits.expand(complete, self.record))
        for value in ['"not-a-category"', "false"]:
            patch = copy.deepcopy(self.patch)
            patch["repair_report"]["edits"][1]["value_json"] = value
            Path(self.record["output"]).write_text(json.dumps(patch))
            with self.assertRaises(ValueError):
                runner.load_stage_report(self.record)

    def test_complete_report_inside_new_repair_wrapper_is_accepted(self):
        complete = {
            "summary": "corrected",
            "proposed_assumptions": [{"text": "Preserve empty pages", "category": "behavior"}],
        }
        self.assertEqual(complete, edits.expand({"repair_report": complete}, self.record))

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
                    record["stage"] = "builder_report_repair"
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
            [{"op": "remove", "path": [], "value_json": "null"}],
            [{"op": "add", "path": ["proposed_assumptions", 0], "value_json": "null"}],
            [{"op": "remove", "path": ["proposed_assumptions", 1], "value_json": "null"}],
            [{"op": "remove", "path": ["proposed_assumptions", 0], "value_json": "false"}],
            [
                {"op": "remove", "path": ["proposed_assumptions", 0], "value_json": "null"},
                {"op": "replace", "path": ["proposed_assumptions", 0, "text"], "value_json": '"new"'},
            ],
            [{"op": "remove", "path": ["proposed_assumptions", 0], "value_json": "null"}] * 2,
            [{"op": "replace", "path": ["proposed_assumptions", True], "value_json": "{}"}],
            [{"op": "replace", "path": ["absent", "child"], "value_json": "0"}],
            [{"op": "add", "path": ["summary"], "value_json": '"new"'}],
            [{"op": "remove", "path": ["extra"], "value_json": '"not null"'}],
            [
                {"op": "replace", "path": ["proposed_assumptions"], "value_json": "[]"},
                {"op": "replace", "path": ["proposed_assumptions", 0], "value_json": "{}"},
            ],
            [{"op": "remove", "path": ["extra"], "value_json": "null"}] * 2,
            [],
            [{"op": "remove", "path": ["extra"], "value_json": "null"}] * 33,
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
        self.assertEqual(self.schema, edits.prepare(self.schema, self.descriptor, "builder"))

    def test_legacy_or_conflicting_schemas_keep_the_original_protocol(self):
        for schema in [
            {},
            {"type": "object"},
            {"type": "array"},
            {"type": "object", "properties": {"repair_report": {"type": "string"}}},
        ]:
            self.schema.write_text(json.dumps(schema))
            self.assertEqual(self.schema, edits.prepare(self.schema, self.descriptor, "revise_plan"))


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
        self.offered = edits.prepare(self.schema, self.descriptor, "revise_plan")
        self.record = {
            "stage": "revise_plan_report_repair",
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
        self.assertIn("BOUNDED JSON TEXT REPAIR", edits.instruction("revise_plan", self.descriptor))
        Path(self.record["output"]).write_text(json.dumps(self.patch))
        fixed = runner.load_stage_report(self.record)
        self.assertEqual({"acceptance_criteria": [], "retain_requirement_trace": True}, fixed["contract"])
        self.assertEqual("Résumé ⚡", fixed["summary"])
        self.assertEqual([], fixed["access_blockers"])
        self.assertEqual(self.text, self.source.read_text())
        self.assertEqual(self.patch, json.loads(Path(self.record["reported_output"]).read_text()))
        self.assertEqual(self.original_schema, edits.validation_schema(json.loads(self.offered.read_text())))
        self.assertIs(fixed, edits.expand(fixed, self.record))

    def test_large_review_text_and_fences_are_repaired_only_by_exact_model_edits(self):
        original = {
            "checks": [{"command": "captured check", "exit_code": 0}],
            "decisions": [{"line": i, "reason": "Exact independently checked line. " * 7} for i in range(704)],
            "summary": "done",
        }
        schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"checks": {"type": "array"}, "decisions": {"type": "array"}, "summary": {"type": "string"}},
            "required": ["checks", "decisions", "summary"],
        }
        self.schema.write_text(json.dumps(schema))
        invalid = json.dumps(original).replace('"summary": "done"', '"summary": "done".replace("unused","")')
        for stage in ("tester", "review_completion", "review_checkpoint"):
            for prefix, suffix in (("", ""), ("All checks complete.\n```json\n", "\n```")):
                with self.subTest(stage=stage, fenced=bool(prefix)):
                    text = prefix + invalid + suffix
                    self.assertGreater(len(text.encode()), 128 * 1024)
                    self.source.write_text(text)
                    descriptor = {**self.descriptor, "content": text, "sha256": file_hash(self.source)}
                    offered = edits.prepare(self.schema, descriptor, stage)
                    self.assertNotEqual(self.schema, offered)
                    self.assertIn("must be removed explicitly", edits.instruction(stage, descriptor))
                    operations = [{"find": '.replace("unused","")', "replace": ""}]
                    if prefix:
                        operations.extend([{"find": prefix, "replace": ""}, {"find": "}" + suffix, "replace": "}"}])
                    patch = {"repair_report": {"sha256": descriptor["sha256"], "text_edits": operations}}
                    self.assertLess(len(json.dumps(patch)), 512)
                    record = {**self.record, "stage": stage + "_report_repair", "schema": str(offered)}
                    fixed = edits.expand(patch, record)
                    validate_schema(fixed, schema)
                    self.assertEqual(original, fixed)
                    self.assertEqual(text, self.source.read_text())

    def test_fenced_text_offer_refuses_ambiguous_or_unbounded_wrappers(self):
        for text in (
            '```python\n{"summary":"done"}\n```',
            '```json\n{"summary":"done"}\n```\nMore prose',
            '```json\n{"summary":"done"}\n```\n```json\n{}\n```',
            "é" * 1025 + "\n```json\n{}\n```",
            '```json\n{"summary":\n```',
        ):
            with self.subTest(text=text[:60]):
                self.assertFalse(text_edits.eligible({"format": "text", "content": text}))

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
                    record["stage"] = "tester_report_repair"
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
            self.assertEqual(self.schema, edits.prepare(self.schema, {**self.descriptor, **change}, "revise_plan"))
        self.assertEqual(self.schema, edits.prepare(self.schema, self.descriptor, "builder"))


class DiscoveryFixtureSchemaTests(unittest.TestCase):
    def test_discovery_object_fixture_emits_offered_value_json_schema(self):
        import contextlib
        import io
        import os
        import sys
        import unittest.mock

        from tests import stage_repair_provider as provider

        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)

        goal_schema = goals.BODY_SCHEMA
        contract = body()
        contract["contract_changes_placeholder"] = "to_remove"
        rejected = {"contract": contract}
        source = root / "rejected.json"
        source.write_text(json.dumps(rejected))

        output_schema = {
            "type": "object",
            "additionalProperties": False,
            "properties": {"contract": goal_schema},
            "required": ["contract"],
        }
        schema_file = root / "schema.json"
        schema_file.write_text(json.dumps(output_schema))
        descriptor = {
            "path": str(source),
            "sha256": file_hash(source),
            "format": "json",
            "truncated": False,
            "content": rejected,
        }
        offered_schema = edits.prepare(schema_file, descriptor, "draft_plan")

        output_file = root / "repair.json"
        data = {
            "report_repair": True,
            "original": {"stage": "draft_plan"},
            "rejected_report": {
                "path": str(source),
                "sha256": file_hash(source),
                "format": "json",
                "truncated": False,
                "content": rejected,
            },
        }
        prompt = "BOUNDED REPORT EDITS\nCURRENT HANDOFF DATA\n" + json.dumps(data)
        delegate = root / "delegate.py"
        delegate.write_text("import sys\nsys.exit(0)\n")
        trace_file = root / "trace.jsonl"
        args = [
            "codex",
            "exec",
            "--output-schema",
            str(offered_schema),
            "-o",
            str(output_file),
            "resume",
            "test-session",
        ]
        stdin = io.StringIO(prompt)
        stdout = io.StringIO()
        stderr = io.StringIO()

        with (
            unittest.mock.patch.dict(
                os.environ,
                STAGE_REPAIR_CASE="discovery-edits-object",
                STAGE_REPAIR_DELEGATE=str(delegate),
                STAGE_REPAIR_TRACE=str(trace_file),
            ),
            unittest.mock.patch.object(sys, "argv", args),
            unittest.mock.patch.object(sys, "stdin", stdin),
            contextlib.redirect_stdout(stdout),
            contextlib.redirect_stderr(stderr),
        ):
            code = provider.main()
            self.assertEqual(0, code)

        repair = json.loads(output_file.read_text())
        validate_schema(repair, json.loads(offered_schema.read_text()))
        self.assertIn("repair_report", repair)
        self.assertIn("edits", repair["repair_report"])
        self.assertEqual(1, len(repair["repair_report"]["edits"]))
        edit = repair["repair_report"]["edits"][0]
        self.assertEqual("remove", edit["op"])
        self.assertEqual(["contract", "contract_changes_placeholder"], edit["path"])
        self.assertIn("value_json", edit)
        self.assertEqual("null", edit["value_json"])

        base = json.loads(source.read_text())
        fixed = edits.apply(base, repair["repair_report"]["edits"])
        expected_contract = {k: v for k, v in contract.items() if k != "contract_changes_placeholder"}
        self.assertEqual(expected_contract, fixed["contract"])
