"""A complete report's trailing commas need no model repair; admission stays independent."""

import hashlib
import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_report_source as source
import autocode_report_syntax as syntax
from autocode_util import atomic_json, read_object
from providers import claude, claude_stream, opencode
from providers.command import CommandProvider

from . import test_autocode as base
from .test_opencode import event, terminal


class SyntaxTests(unittest.TestCase):
    def test_only_trailing_commas_outside_strings_are_removed(self):
        raw = '{"summary":"café,} \\"quoted\\"", "checks":[{"command":"exit 1", "exit_code":1,},],}'
        result, offsets = syntax.trailing_commas(raw)
        self.assertEqual({"summary": 'café,} "quoted"', "checks": [{"command": "exit 1", "exit_code": 1}]}, result)
        self.assertEqual(3, len(offsets))
        corrected = "".join(char for index, char in enumerate(raw) if index not in offsets)
        self.assertEqual(result, json.loads(corrected))
        self.assertTrue(all(raw[index] == "," for index in offsets))

    def test_exact_bounds_and_nested_objects(self):
        raw = '{"rows":[' + ",".join('{"n":1,}' for _ in range(syntax.MAX_COMMAS)) + "]}"
        value, offsets = syntax.trailing_commas(raw)
        self.assertEqual([{"n": 1}] * syntax.MAX_COMMAS, value["rows"])
        self.assertEqual(syntax.MAX_COMMAS, len(offsets))
        self.assertIsNone(syntax.trailing_commas(raw.replace('"rows":[', '"rows":[{"n":2,},', 1)))
        self.assertIsNone(syntax.trailing_commas('{"x":"' + "x" * syntax.MAX_REPORT_BYTES + '",}'))

    def test_a_report_exactly_at_the_byte_bound_can_normalize(self):
        prefix, suffix = '{"summary":"', '",}'
        value = "x" * (512 * 1024 - len(prefix + suffix))
        raw = prefix + value + suffix
        self.assertEqual(512 * 1024, len(raw.encode()))
        corrected, offsets = syntax.trailing_commas(raw)
        self.assertEqual({"summary": value}, corrected)
        self.assertEqual([len(raw) - 2], offsets)

    def test_incomplete_ambiguous_or_semantic_failures_are_left_for_existing_repair(self):
        for raw in (
            '{"summary":"done"}',
            '{"summary":"done" "checks":[]}',
            '{"checks":[{"exit_code":1,}',
            '{"checks":[1,,]}',
            '{"summary":"first","summary":"second",}',
            '{"checks":[{"result":NaN,}],}',
            '{"checks":[{"result":Infinity,}],}',
            '{"summary":"unterminated,}',
            '```json\n{"summary":"done",}\n```',
            '{"summary":"done",} {"summary":"other"}',
            "{,}",
            '[{"summary":"done",}]',
            ",}",
        ):
            with self.subTest(raw=raw):
                self.assertIsNone(syntax.trailing_commas(raw))


class LoaderTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / "report.json"
        self.raw = '{"summary":"keep failure", "checks":[{"exit_code":1,},],}'
        self.output.write_text(self.raw)
        self.record = {"engine": "codex", "output": str(self.output), "exit_code": 0}

    def load(self, record=None, reader=None):
        return source.completed_provider_report(
            record or self.record,
            opencode_report=reader,
            read_object=read_object,
            persist=atomic_json,
        )

    def assert_preserved(self, value, raw=None):
        raw = self.raw if raw is None else raw
        self.assertEqual(value, read_object(self.output))
        self.assertEqual(raw.encode(), Path(self.record["response_text"]).read_bytes())
        receipt = self.record["syntax_corrections"]
        self.assertEqual("trailing_commas", receipt["kind"])
        self.assertEqual(hashlib.sha256(raw.encode()).hexdigest(), receipt["source_sha256"])
        corrected = "".join(char for index, char in enumerate(raw) if index not in receipt["removed_offsets"])
        self.assertEqual(value, json.loads(corrected))

    def test_a_file_report_keeps_its_exact_source_and_failed_result(self):
        value = self.load()
        self.assertEqual(1, value["checks"][0]["exit_code"])
        self.assert_preserved(value)
        self.assertEqual(value, self.load(), "reloading the normalized report needs no new correction")

    def test_the_packaged_loader_preserves_the_same_report_and_receipt(self):
        packaged = importlib.import_module("tools.autocode_report_source")
        value = packaged.completed_provider_report(
            self.record, opencode_report=None, read_object=read_object, persist=atomic_json
        )
        self.assertEqual(1, value["checks"][0]["exit_code"])
        self.assert_preserved(value)

    def test_completed_opencode_events_use_the_exact_terminal_response(self):
        events = self.root / "events.jsonl"
        events.write_text("\n".join(json.dumps(row) for row in (event("text", text=self.raw), terminal())))
        self.output.unlink()
        self.record.update(engine="opencode", events=str(events))
        value = self.load(reader=opencode.final_report)
        self.assertEqual(1, value["checks"][0]["exit_code"])
        self.assert_preserved(value)

    def test_a_report_file_provider_uses_its_own_parser_failure(self):
        provider = CommandProvider({"name": "fixture", "command": ["unused"], "roles": {}}, self.root / "provider.toml")
        self.record.update(engine="opencode", output_mode="report_file", events=str(self.output.with_suffix(".jsonl")))
        value = self.load(reader=provider.final_report)
        self.assertEqual(1, value["checks"][0]["exit_code"])
        self.assert_preserved(value)

    def test_a_successful_claude_structured_report_can_normalize_but_a_rejected_one_cannot(self):
        events = self.root / "events.jsonl"
        self.record.update(engine="opencode", events=str(events), provider="claude", output_mode="codex_events")
        for item_id in (claude_stream.REJECTED_ITEM, claude_stream.REPORT_ITEM):
            with self.subTest(item_id=item_id):
                events.write_text(
                    "\n".join(
                        json.dumps(row)
                        for row in (
                            {"type": "thread.started", "thread_id": "s"},
                            {
                                "type": "item.completed",
                                "item": {"id": item_id, "type": "agent_message", "text": self.raw},
                            },
                            {"type": "turn.completed"},
                        )
                    )
                )
                if item_id == claude_stream.REJECTED_ITEM:
                    with self.assertRaises(RuntimeError) as caught:
                        self.load(reader=claude.final_report)
                    self.assertEqual(claude.FORMAT_REJECTION, str(caught.exception))
                    self.assertNotIn("syntax_corrections", self.record)
                else:
                    value = self.load(reader=claude.final_report)
                    self.assertEqual(1, value["checks"][0]["exit_code"])
                    self.assert_preserved(value)

    def test_failed_incomplete_and_report_only_attempts_do_not_normalize(self):
        for flags in (
            {"exit_code": 1},
            {"exit_code": None},
            {"truncated_output": True},
            {"timed_out": True},
            {"interrupted": True},
            {"abandoned": True},
            {"report_only": True},
        ):
            with self.subTest(flags=flags), self.assertRaises(RuntimeError):
                self.load({**self.record, **flags})
        self.assertEqual(self.raw, self.output.read_text())
        self.assertFalse(self.output.with_suffix(".response.txt").exists())

    def test_provider_failure_without_a_parse_cause_cannot_reuse_a_saved_response(self):
        self.record.update(engine="opencode", events=str(self.root / "events.jsonl"))
        response = self.output.with_suffix(".response.txt")
        response.write_text(self.raw)
        error = RuntimeError("Provider turn has no successful terminal result")
        with self.assertRaisesRegex(RuntimeError, "no successful terminal"):
            self.load(reader=mock.Mock(side_effect=error))
        self.assertNotIn("syntax_corrections", self.record)

    def test_source_changes_and_conflicting_raw_artifacts_decline_the_correction(self):
        try:
            json.loads(self.raw)
        except json.JSONDecodeError as cause:
            error = RuntimeError("parser failure")
            error.__cause__ = cause
        for mode in ("changed", "conflicting", "missing", "non-utf8"):
            with self.subTest(mode=mode):
                self.output.write_bytes(b"\xff" if mode == "non-utf8" else self.raw.encode())
                response = self.output.with_suffix(".response.txt")
                response.unlink(missing_ok=True)
                if mode == "changed":
                    self.output.write_text('{"changed":true}')
                if mode == "conflicting":
                    response.write_text("earlier response")
                if mode == "missing":
                    self.output.unlink()
                persist = mock.Mock()
                self.assertIsNone(source.syntax_report(self.record, error, persist=persist))
                persist.assert_not_called()
                self.assertNotIn("syntax_corrections", self.record)

    def test_nonrepairable_syntax_keeps_the_ordinary_error(self):
        self.output.write_text('{"summary":"done" "checks":[]}')
        with self.assertRaisesRegex(RuntimeError, "valid JSON"):
            self.load()
        self.assertNotIn("syntax_corrections", self.record)

    def test_corrected_syntax_still_fails_the_ordinary_report_schema(self):
        schema = self.root / "report.schema.json"
        atomic_json(schema, {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]})
        self.output.write_text('{"summary":false,}')
        record = {**self.record, "schema": str(schema), "stage": "review_plan"}
        with self.assertRaisesRegex(ValueError, "string"):
            base.runner.load_stage_report(record)
        self.assertEqual('{"summary":false,}', Path(record["response_text"]).read_text())
        self.assertIn("syntax_corrections", record)
