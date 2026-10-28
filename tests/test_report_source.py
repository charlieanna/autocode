"""Report repair reads the original report from wherever the stage's provider kept it (#717)."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import autocode_report_repair_context as repair_context
import autocode_report_source as report_source
from providers import claude, claude_stream


class ReportSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, rows):
        path = self.root / name
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path

    def claude_record(self, *rows, **fields):
        events = self.write("tester-01.jsonl", [{"type": "thread.started", "thread_id": "s"}, *rows])
        return {
            "engine": "opencode",
            "output_mode": "codex_events",
            "provider": "claude",
            "events": str(events),
            "output": str(self.root / "tester-01.json"),
            **fields,
        }

    def test_the_original_report_comes_from_the_provider_s_own_record(self):
        report = {"summary": "done", "finding_dispositions": [{"id": "F1", "status": "fixed"}]}
        events = self.write("builder-01.jsonl", [{"type": "turn.completed"}])
        output = self.root / "builder-01.json"
        output.write_text(json.dumps(report))
        configured = {"engine": "opencode", "output_mode": "report_file", "provider": "kilocode"}
        found = report_source.original_report_for_repair({**configured, "events": str(events), "output": str(output)})
        self.assertEqual({"report": report, "extraction_error": None}, found)
        item = {"id": claude_stream.REPORT_ITEM, "type": "agent_message", "text": json.dumps(report)}
        record = self.claude_record({"type": "item.completed", "item": item}, {"type": "turn.completed"})
        self.assertEqual({"report": report, "extraction_error": None}, report_source.original_report_for_repair(record))
        prose = {"id": claude_stream.RESULT_TEXT_ITEM, "type": "agent_message", "text": "Done."}
        record = self.claude_record({"type": "item.completed", "item": prose}, {"type": "turn.completed"})
        self.assertEqual(
            {"report": None, "extraction_error": claude.FORMAT_REJECTION},
            report_source.original_report_for_repair(record),
        )

    def test_malformed_report_retains_exact_text_and_unicode_syntax_location(self):
        # Representative live failure: a list closes before its milestone object.
        raw = '{\n"milestones":[{"title":"café", "summary":"keep every finding"],"other":[]} '
        response = self.root / "revision.response.txt"
        response.write_text(raw)
        record = {"engine": "codex", "output": str(self.root / "revision.json"), "response_text": str(response)}
        source = report_source.repair_report_source(record)
        position = raw.index('],"other"')
        self.assertEqual("text", source["format"])
        self.assertEqual(raw, source["content"])
        self.assertEqual(raw.encode(), response.read_bytes())
        self.assertEqual(hashlib.sha256(raw.encode()).hexdigest(), source["sha256"])
        self.assertEqual(len(raw.encode()), source["bytes"])
        self.assertEqual(
            {"message": "Expecting ',' delimiter", "position": position, "line": 2, "column": position - 1},
            source["parse_error"],
        )
        self.assertFalse(source["truncated"])
        self.assertFalse(Path(record["output"]).exists())

    def test_valid_json_has_no_syntax_diagnostic_and_prose_is_not_coerced(self):
        output = self.root / "report.json"
        for raw, format_, content in (
            ('{"summary": "done"}', "json", {"summary": "done"}),
            ("not JSON", "text", "not JSON"),
        ):
            with self.subTest(raw=raw):
                output.write_text(raw)
                source = report_source.repair_report_source({"engine": "codex", "output": str(output)})
                self.assertEqual(format_, source["format"])
                self.assertEqual(content, source["content"])
                if format_ == "json":
                    self.assertNotIn("parse_error", source)
                else:
                    self.assertEqual(0, source["parse_error"]["position"])
                self.assertEqual(raw, output.read_text())

    def test_repair_context_uses_latest_syntax_diagnostic_not_the_original(self):
        original = self.root / "original.json"
        latest = self.root / "latest.response.txt"
        original.write_text('{"summary": "original schema failure"}')
        latest.write_text('{"summary": "latest syntax failure" "checks": []}')
        pending = {
            "original": {"engine": "codex", "stage": "sol", "output": str(original)},
            "latest_rejected": {"engine": "codex", "output": str(latest)},
        }
        before, current = repair_context.sources(pending, report_source.repair_report_source)
        instruction, _ = repair_context.prompt_parts({}, pending, before, current, ("", {}))
        self.assertNotIn("parse_error", before)
        self.assertEqual(latest.read_text(), current["content"])
        self.assertEqual(latest.read_text().index('"checks"'), current["parse_error"]["position"])
        self.assertIn("rejected_report.parse_error", instruction)
        self.assertIn("zero-based Unicode character offset", instruction)
        for unknown in ({**current, "truncated": True}, before):
            instruction, _ = repair_context.prompt_parts({}, pending, before, unknown, ("", {}))
            self.assertNotIn("JSON SYNTAX DIAGNOSTIC", instruction)

    def test_a_truncated_claude_review_keeps_its_partial_report(self):
        partial = {"id": claude_stream.PARTIAL_ITEM, "type": "agent_message", "text": '{"findings": [{"id": "F1"'}
        cap = {"type": "turn.failed", "error": {"code": "output_token_limit", "message": "Claude Code exhausted its"}}
        record = self.claude_record(
            {"type": "turn.started"}, {"type": "item.completed", "item": partial}, cap, truncated_output=True
        )
        source = report_source.repair_report_source(record)
        self.assertTrue(source["truncated"])
        self.assertEqual('{"findings": [{"id": "F1"', source["content"]["partial_text"])
        self.assertEqual("Claude Code exhausted its", source["content"]["diagnostic"])
        opencode_rows = [
            {
                "type": "text",
                "sessionID": "s",
                "part": {"id": "p1", "messageID": "m1", "sessionID": "s", "text": "OK so far"},
            },
            {
                "type": "step_finish",
                "sessionID": "s",
                "part": {"id": "f1", "messageID": "m1", "sessionID": "s", "reason": "length"},
            },
        ]
        events = self.write("opencode.jsonl", opencode_rows)
        record = {
            "engine": "opencode",
            "events": str(events),
            "output": str(self.root / "x.json"),
            "truncated_output": True,
        }
        self.assertEqual("OK so far", report_source.repair_report_source(record)["content"]["partial_text"])
        codex = {**record, "engine": "codex"}
        self.assertIsNone(report_source.repair_report_source(codex)["content"]["partial_text"])

    def test_only_length_truncated_review_attempts_qualify_as_repair_inputs(self):
        length = {"type": "turn.failed", "error": {"code": "output_token_limit", "message": "Output token limit"}}
        record = {"stage": "astra_challenge", "truncated_output": True}
        for stage in ("sol", "astra_review", "astra_checkpoint", "astra_challenge", "astra_finalize"):
            with self.subTest(stage=stage):
                record.update(stage=stage, events=str(self.write("review.jsonl", [length])))
                self.assertTrue(report_source.valid_truncated_report_attempt(record, False))
                self.assertFalse(report_source.valid_truncated_report_attempt(record, True))
                repair = {**record, "stage": stage + "_report_repair", "original_stage": stage, "report_only": True}
                self.assertTrue(report_source.valid_truncated_report_attempt(repair, False))
        for case, fields, rows in (
            ("builder", {"stage": "terra"}, [length]),
            ("unknown stage", {"stage": "investigate_bug"}, [length]),
            ("forged review owner", {"stage": "terra", "original_stage": "sol"}, [length]),
            (
                "mismatched repair owner",
                {"stage": "terra_report_repair", "original_stage": "sol", "report_only": True},
                [length],
            ),
            ("unmarked repair", {"stage": "sol_report_repair", "original_stage": "sol"}, [length]),
            ("missing marker", {"truncated_output": False}, [length]),
            ("no failure", {}, [{"type": "thread.started"}]),
            # This recognized failure is deliberately given length-like prose;
            # only the structured failure kind may admit a truncated response.
            (
                "incomplete",
                {},
                [{"type": "turn.failed", "error": {"code": "incomplete_turn", "message": "Output token limit"}}],
            ),
            (
                "unknown error",
                {},
                [{"type": "turn.failed", "error": {"code": "other", "message": "Output token limit"}}],
            ),
            ("completed", {}, [length, {"type": "turn.completed"}]),
        ):
            with self.subTest(case=case):
                candidate = {
                    **record,
                    "stage": "astra_challenge",
                    **fields,
                    "events": str(self.write("review.jsonl", rows)),
                }
                self.assertFalse(report_source.valid_truncated_report_attempt(candidate, False))

    def test_a_claude_stage_without_its_report_file_is_read_from_its_events(self):
        report = {"summary": "done"}
        item = {"id": claude_stream.REPORT_ITEM, "type": "agent_message", "text": json.dumps(report)}
        record = self.claude_record({"type": "item.completed", "item": item}, {"type": "turn.completed"})
        source = report_source.repair_report_source(record)
        self.assertEqual(("json", report), (source["format"], source["content"]))
        self.assertEqual(report, json.loads(Path(record["output"]).read_text()))


if __name__ == "__main__":
    unittest.main()
