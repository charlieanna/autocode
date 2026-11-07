"""A Tester check may cite a capture receipt without retyping its command (#1710).

The Tester's report spent most of its 6.7k output tokens copying commands and exit codes the
receipts already hold. A check with command null is filled from the receipt it cites and then
verified exactly as a typed one; the receipt must still be genuine.
"""

import hashlib
import json
import shlex
import tempfile
import unittest
from pathlib import Path

import autocode_support as support

ARGV = ["python3", "-B", "-c", "import sys; print('peek ok'); sys.exit(0)"]


class CommandFromReceiptTests(unittest.TestCase):
    def test_ac12_capture_receipt_and_report_file_policy_preserved(self):
        from providers.command import CommandProvider

        checks = [{"command": None, "exit_code": None, "evidence_ref": ".autocode/evidence/check.json"}]
        receipt_path = self.workspace / checks[0]["evidence_ref"]
        output_path = Path(self.receipt["full_output"])
        before = {path: path.read_bytes() for path in (receipt_path, output_path, self.events)}
        support.verify_checks(checks, self.workspace, self.events)
        self.assertEqual(
            [{"command": shlex.join(ARGV), "exit_code": 0, "evidence_ref": ".autocode/evidence/check.json"}], checks
        )
        for path, contents in before.items():
            self.assertEqual(contents, path.read_bytes())
        config = {"name": "synthetic", "command": ["fixture", "{report}"], "roles": {}, "output": "report_file"}
        provider = CommandProvider(config, self.workspace / "provider.toml")
        schema = support.read(Path(support.__file__).parent / "autocode-schemas/v2/sol-report.schema.json")
        prompt = provider.prompt_for_schema("Verify\nCURRENT HANDOFF DATA\n{}", schema, self.events)
        self.assertIn("receipt", prompt.lower())
        self.assertIn("event:", prompt)
        self.assertRegex(prompt.lower(), r"(?s)(?:not|never|do not|cannot|can't|must not).{0,180}event:")
        tampered = dict(self.receipt, exit_code=True)
        receipt_path.write_text(json.dumps(tampered))
        with self.assertRaises(ValueError):
            support.verify_checks(
                [{"command": None, "exit_code": None, "evidence_ref": ".autocode/evidence/check.json"}],
                self.workspace,
                self.events,
            )

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        evidence = self.workspace / ".autocode" / "evidence"
        evidence.mkdir(parents=True)
        log = evidence / "check.log"
        log.write_text("peek ok\n")
        self.receipt = {
            "command": ARGV,
            "exit_code": 0,
            "full_output": str(log),
            "full_output_sha256": hashlib.sha256(log.read_bytes()).hexdigest(),
        }
        (evidence / "check.json").write_text(json.dumps(self.receipt))
        self.events = self.workspace / "events.jsonl"
        self.capture_event(json.dumps(self.receipt) + "\n")

    def capture_event(self, output):
        row = {
            "type": "tool_use",
            "sessionID": "s",
            "part": {
                "id": "capture-1",
                "sessionID": "s",
                "messageID": "m",
                "tool": "shell",
                "state": {
                    "status": "completed",
                    "input": {
                        "command": "autocode capture --output .autocode/evidence/check.json -- " + shlex.join(ARGV)
                    },
                    "metadata": {"exit": 0},
                    "output": output,
                },
            },
        }
        self.events.write_text(json.dumps(row) + "\n")

    def test_a_receipt_check_without_its_command_gets_the_receipts_command(self):
        reported = [{"command": None, "exit_code": None, "evidence_ref": ".autocode/evidence/check.json"}]
        checks = json.loads(json.dumps(reported))
        support.verify_checks(checks, self.workspace, self.events)
        self.assertEqual(
            [{"command": shlex.join(ARGV), "exit_code": 0, "evidence_ref": ".autocode/evidence/check.json"}], checks
        )
        self.assertEqual(
            ["exit_code", "command"],
            [row["field"] for row in support.derived_check_fields(checks, reported, str(self.events))],
        )

    def test_a_typed_command_is_still_compared_with_the_receipt(self):
        typed = {"command": "python3 -c 'print(1)'", "exit_code": 0, "evidence_ref": ".autocode/evidence/check.json"}
        with self.assertRaises(ValueError):
            support.verify_checks([typed], self.workspace, self.events)

    def test_the_receipt_must_still_be_genuine(self):
        self.capture_event("")  # the capture's printed receipt is gone: nothing proves this attempt ran it
        check = {"command": None, "exit_code": None, "evidence_ref": ".autocode/evidence/check.json"}
        with self.assertRaisesRegex(ValueError, "No independently executed Validator tool event"):
            support.verify_checks([check], self.workspace, self.events)

    def test_an_event_citation_still_needs_its_command(self):
        check = {"command": None, "exit_code": None, "evidence_ref": "event:"}
        with self.assertRaisesRegex(ValueError, "needs a command"):
            support.verify_checks([check], self.workspace, self.events)

    def test_the_tester_schema_accepts_a_null_command(self):
        schema = json.loads(
            (Path(support.__file__).parent / "autocode-schemas" / "v2" / "sol-report.schema.json").read_text()
        )
        self.assertEqual(["string", "null"], schema["properties"]["checks"]["items"]["properties"]["command"]["type"])


if __name__ == "__main__":
    unittest.main()
