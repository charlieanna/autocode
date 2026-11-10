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
    def test_large_capture_authenticates_from_either_end_of_native_display(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            ref = ".autocode/evidence/large.json"
            argv = [sys.executable, "-c", "print('x' * 400000)"]
            capture = [sys.executable, str(Path(support.__file__).parent / "autocode.py"), "capture"]
            command = capture + ["--mode", "raw", "--output", ref, "--", *argv]
            environment = {key: value for key, value in os.environ.items() if not key.startswith("AUTOCODE_")}
            environment["AUTOCODE_OUTPUT_STORE"] = str(workspace / ".output-store")
            ran = subprocess.run(
                command,
                cwd=workspace,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=20,
            )
            self.assertEqual(0, ran.returncode)
            receipt = workspace / ref
            original = receipt.read_bytes()
            self.assertEqual("x" * 400000 + "\n", Path(json.loads(original)["full_output"]).read_text())
            events = workspace / "events.jsonl"
            for kind, display in (("head", ran.stdout[:8192]), ("tail", ran.stdout[-8192:])):
                with self.subTest(display=kind):
                    item = {
                        "type": "command_execution",
                        "id": "large",
                        "command": shlex.join(command),
                        "exit_code": 0,
                        "aggregated_output": display,
                    }
                    events.write_text(json.dumps({"type": "item.completed", "item": item}) + "\n")
                    check = {"command": shlex.join(argv), "exit_code": 0, "evidence_ref": ref}
                    try:
                        support.verify_checks([check], workspace, events)
                    except ValueError as error:
                        self.fail(f"The {kind} display lost capture authentication: {error}")
                    receipt.write_text(json.dumps({**json.loads(original), "duration_seconds": 0}))
                    with self.assertRaisesRegex(ValueError, "independently executed"):
                        support.verify_checks([check], workspace, events)
                    receipt.write_bytes(original)

    def test_native_and_configured_event_providers_explain_receipt_preservation(self):
        configured = CommandProvider(
            {
                "name": "event-fixture",
                "command": ["unused"],
                "output": "opencode_events",
                "roles": {
                    role: {"model": "fixture", "effort": "medium"}
                    for role in ("task_planner", "planner", "terra", "sol", "completion", "plan_reviewer")
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

    def test_a_stage_that_shortens_captures_display_still_authenticates_its_receipt(self):
        # Live Testers piped capture through `| tail -c 500`, `| grep -o '"exit_code": [0-9]*'` or `>/dev/null`;
        # the receipt line was cut, and each report was rejected, repaired and re-run (2026-11-02). The short
        # attestation line capture prints on stderr names the receipt's exact bytes, so it authenticates instead.
        capture = shlex.join([sys.executable, str(Path(support.__file__).parent / "autocode.py"), "capture"])
        argv = [sys.executable, "-c", "print('ok')"]
        filters = {
            "tail": "| tail -c 200",
            "merged-tail": "2>&1 | tail -c 200",
            "grep": "| grep -o '\"exit_code\": [0-9]*'",
            "discarded": ">/dev/null",
            # A merged line filter (live run 2026-11-02): the attestation line names "exit_code", so it is kept.
            "merged-grep": "2>&1 | grep -E '\"exit_code\"|Tests ' | cut -c1-300",
        }
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            environment = {key: value for key, value in os.environ.items() if not key.startswith("AUTOCODE_")}
            environment["AUTOCODE_OUTPUT_STORE"] = str(workspace / ".output-store")
            for number, (kind, shortened) in enumerate(filters.items(), 1):
                with self.subTest(kind=kind):
                    ref = f".autocode/evidence/{kind}.json"
                    command = f"{capture} --output {ref} -- {shlex.join(argv)} {shortened}"
                    ran = subprocess.run(
                        ["sh", "-c", command], cwd=workspace, env=environment, capture_output=True, text=True
                    )
                    self.assertEqual(0, ran.returncode, ran.stderr)
                    whole = json.dumps(json.loads((workspace / ref).read_text()))
                    self.assertNotIn(whole, ran.stdout)
                    # Claude Code's Bash result: stdout, then stderr (providers/claude_stream._output).
                    events = workspace / f"events-{kind}.jsonl"
                    item = {
                        "type": "command_execution",
                        "id": f"item_{number}",
                        "command": command,
                        "exit_code": 0,
                        "aggregated_output": ran.stdout + "\n" + ran.stderr,
                    }
                    events.write_text(json.dumps({"type": "item.completed", "item": item}) + "\n")
                    check = {"command": shlex.join(argv), "exit_code": 0, "evidence_ref": ref}
                    support.verify_checks([check], workspace, events)
                    # The line attests these bytes only: a receipt edited afterwards is refused.
                    receipt = workspace / ref
                    receipt.write_text(json.dumps({**json.loads(receipt.read_text()), "duration_seconds": 0}))
                    with self.assertRaisesRegex(ValueError, "No independently executed Validator tool event"):
                        support.verify_checks([dict(check)], workspace, events)

    def test_a_filter_that_drops_every_receipt_line_is_named_in_the_rejection(self):
        # `2>&1 | grep -o` keeps only the matched text, so no line can attest the receipt. The rejection says
        # which filter dropped it, so the fresh Tester attempt runs capture unfiltered instead of repeating it.
        capture = shlex.join([sys.executable, str(Path(support.__file__).parent / "autocode.py"), "capture"])
        argv = [sys.executable, "-c", "print('ok')"]
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary).resolve()
            environment = {key: value for key, value in os.environ.items() if not key.startswith("AUTOCODE_")}
            environment["AUTOCODE_OUTPUT_STORE"] = str(workspace / ".output-store")
            ref = ".autocode/evidence/only-exit.json"
            command = f"{capture} --output {ref} -- {shlex.join(argv)} 2>&1 | grep -o '\"exit_code\": [0-9]*'"
            ran = subprocess.run(["sh", "-c", command], cwd=workspace, env=environment, capture_output=True, text=True)
            self.assertEqual('"exit_code": 0\n' * 3, ran.stdout)
            events = workspace / "events.jsonl"
            item = {"type": "command_execution", "id": "item_1", "command": command, "exit_code": 0}
            item["aggregated_output"] = ran.stdout
            events.write_text(json.dumps({"type": "item.completed", "item": item}) + "\n")
            check = {"command": shlex.join(argv), "exit_code": 0, "evidence_ref": ref}
            with self.assertRaises(ValueError) as refused:
                support.verify_checks([check], workspace, events)
            message = str(refused.exception)
            self.assertTrue(message.startswith("No independently executed Validator tool event matches receipt"))
            self.assertIn("""`2>&1 | grep -o '"exit_code": [0-9]*'`""", message)
            self.assertIn("Run capture again without filtering its output", message)

    def test_a_cited_command_that_ran_only_inside_a_longer_call_is_named_in_the_rejection(self):
        # Live Testers ran `cmd 2>&1 | tail -15` or `a; b` in one call and cited `cmd` with a bare event:
        # (2026-11-02, 2026-11-06). Its own exit code is unknown, so it is still refused, and the rejection
        # now names the call so the fresh attempt runs the cited command by itself.
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            events = workspace / "events.jsonl"
            ran = "node node_modules/vitest/vitest.mjs run 2>&1 | tail -15"
            item = {"type": "command_execution", "id": "item_1", "command": ran, "exit_code": 0}
            item["aggregated_output"] = "Tests  4 passed (4)"
            events.write_text(json.dumps({"type": "item.completed", "item": item}) + "\n")
            check = {"command": "node node_modules/vitest/vitest.mjs run", "exit_code": None, "evidence_ref": "event:"}
            with self.assertRaises(ValueError) as refused:
                support.verify_checks([check], workspace, events)
            message = str(refused.exception)
            self.assertTrue(message.startswith("Check is not supported by an exact executed Validator event: "))
            self.assertIn(f"`{ran}`", message)
            self.assertIn("Run each command you cite as its own Bash call", message)
            # A command that ran nowhere keeps the plain message.
            other = {"command": "go test ./...", "exit_code": None, "evidence_ref": "event:"}
            with self.assertRaisesRegex(ValueError, "^Check is not supported by an exact executed Validator event$"):
                support.verify_checks([other], workspace, events)
