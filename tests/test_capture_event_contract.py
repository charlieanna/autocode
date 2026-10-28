"""Capture's native event must retain the full receipt used for authentication."""

import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_opencode as opencode
import autocode_stage_context as stage_context
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

    def test_builder_handoff_captures_without_run_store_discovery(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            # No state file or evidence directory: the supplied handoff suffices.
            state_path = workspace / ".autocode/runs/builder/state.json"
            state = {
                "version": 3,
                "task": "Fix the approved defect",
                "workspace": str(workspace),
                "settings": {"roles": {"terra": {"engine": "codex", "model": "fixture"}}},
                "current_task": {"id": "task", "affected_paths": ["app.py"]},
                "goal_contract": {"hash": "approved", "body": {"constraints": ["Preserve existing tests"]}},
            }
            with patch.object(stage_context.support, "snapshot", return_value={"revision": "r", "head": "h"}):
                prompt, _ = stage_context.context_packet(state, "terra", state_path)
            packet = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
            instruction = packet["builder_artifact_policy"]["instruction"]
            self.assertIn("state_file identifies mutable runner bookkeeping, not an input to read", instruction)
            self.assertIn("capture creates missing parent directories itself", instruction)
            self.assertIn("An actual capture failure must still be reported accurately", instruction)
            self.assertEqual(state["current_task"], packet["current_task"])
            self.assertEqual(state["goal_contract"], packet["goal_contract"])
            evidence = Path(packet["builder_artifact_policy"]["evidence_directory"])
            self.assertFalse(evidence.exists())
            environment = {key: value for key, value in os.environ.items() if not key.startswith("AUTOCODE_")}
            environment["AUTOCODE_OUTPUT_STORE"] = str(workspace / ".output-store")
            for code in (1, 0):
                with self.subTest(exit_code=code):
                    # Every command starts with an absent parent, including a failing check.
                    output = evidence / f"check-{code}" / "receipt.json"
                    argv = shlex.split(packet["capture_command"]) + [
                        "--output",
                        str(output),
                        "--",
                        sys.executable,
                        "-c",
                        f"import sys; print('check result {code}'); sys.exit({code})",
                    ]
                    result = subprocess.run(
                        argv, cwd=workspace, env=environment, capture_output=True, text=True, timeout=20
                    )
                    self.assertEqual(code, result.returncode, result.stderr)
                    receipt = json.loads(result.stdout)
                    self.assertEqual(receipt, json.loads(output.read_text()))
                    self.assertEqual(code, receipt["exit_code"])
                    raw = Path(receipt["full_output"]).read_bytes()
                    self.assertEqual(f"check result {code}\n".encode(), raw)
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), receipt["full_output_sha256"])
                    original = output.read_bytes()
                    sentinel = workspace / "must-not-run"
                    duplicate = argv[: argv.index("--") + 1] + [
                        sys.executable,
                        "-c",
                        f"from pathlib import Path; Path({str(sentinel)!r}).touch()",
                    ]
                    refused = subprocess.run(
                        duplicate, cwd=workspace, env=environment, capture_output=True, text=True, timeout=20
                    )
                    self.assertEqual(2, refused.returncode)
                    self.assertIn("existing evidence is immutable", refused.stderr)
                    self.assertFalse(sentinel.exists())
                    self.assertEqual(original, output.read_bytes())
            self.assertFalse(state_path.exists())

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
