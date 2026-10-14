"""Report repair reads the original report from wherever the stage's provider kept it (#717)."""

import json
import tempfile
import unittest
from pathlib import Path

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

    def test_a_claude_stage_without_its_report_file_is_read_from_its_events(self):
        report = {"summary": "done"}
        item = {"id": claude_stream.REPORT_ITEM, "type": "agent_message", "text": json.dumps(report)}
        record = self.claude_record({"type": "item.completed", "item": item}, {"type": "turn.completed"})
        source = report_source.repair_report_source(record)
        self.assertEqual(("json", report), (source["format"], source["content"]))
        self.assertEqual(report, json.loads(Path(record["output"]).read_text()))


if __name__ == "__main__":
    unittest.main()
