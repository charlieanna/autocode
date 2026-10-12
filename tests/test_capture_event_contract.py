"""Capture's native event must retain the full receipt used for authentication."""

import hashlib
import json
import shlex
import tempfile
import unittest
from pathlib import Path

import autocode_opencode as opencode
import autocode_support as support
from providers.command import CommandProvider


class CaptureEventContractTests(unittest.TestCase):
    def test_native_and_configured_event_providers_explain_receipt_preservation(self):
        configured = CommandProvider(
            {
                "name": "event-fixture",
                "command": ["unused"],
                "output": "opencode_events",
                "roles": {
                    role: {"model": "fixture", "effort": "medium"}
                    for role in ("astra", "glm", "terra", "sol", "completion", "plan_reviewer")
                },
            },
            Path("/unused/event-fixture.toml"),
        )
        for adapter in (opencode, configured):
            with self.subTest(adapter=type(adapter).__name__):
                prompt = adapter.prompt_for_schema(
                    "Task\nCURRENT HANDOFF DATA\n{}", {"type": "object"}, Path("/run/events.jsonl")
                )
                self.assertIn("Keep the complete JSON receipt printed by capture in the shell tool output", prompt)
                self.assertIn("Do not redirect capture stdout", prompt)
                self.assertIn("Reading or reprinting an older receipt is not a new execution", prompt)

    def test_filtered_or_suppressed_native_output_cannot_authenticate_a_saved_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            evidence = workspace / ".autocode" / "evidence"
            evidence.mkdir(parents=True)
            raw = evidence / "check.log"
            raw.write_text("synthetic test output\n")
            receipt_path = evidence / "check.json"
            argv = ["python3", "-c", "print('synthetic test output')"]
            receipt = {
                "command": argv,
                "exit_code": 0,
                "full_output": str(raw),
                "full_output_sha256": hashlib.sha256(raw.read_bytes()).hexdigest(),
            }
            receipt_path.write_text(json.dumps(receipt))
            original = (receipt_path.read_bytes(), raw.read_bytes())
            events = workspace / "events.jsonl"
            for kind, output in (
                ("complete", json.dumps(receipt) + "\n"),
                ("suppressed", ""),
                ("filtered", json.dumps({"exit_code": 0}) + "\n"),
            ):
                with self.subTest(kind=kind):
                    row = {
                        "type": "tool_use",
                        "sessionID": "synthetic-session",
                        "part": {
                            "id": "synthetic-tool",
                            "sessionID": "synthetic-session",
                            "messageID": "synthetic-message",
                            "tool": "shell",
                            "state": {
                                "status": "completed",
                                "input": {
                                    "command": "autocode capture --output .autocode/evidence/check.json -- "
                                    + shlex.join(argv)
                                },
                                "metadata": {"exit": 0},
                                "output": output,
                            },
                        },
                    }
                    events.write_text(json.dumps(row) + "\n")
                    check = {
                        "command": shlex.join(argv),
                        "exit_code": 0,
                        "evidence_ref": ".autocode/evidence/check.json",
                    }
                    if kind == "complete":
                        support.verify_checks([check], workspace, events)
                    else:
                        with self.assertRaisesRegex(ValueError, "No independently executed Validator tool event"):
                            support.verify_checks([check], workspace, events)
                    self.assertEqual(original, (receipt_path.read_bytes(), raw.read_bytes()))
