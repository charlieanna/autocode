"""Ready check batches preserve literal argv and the individual capture fallback."""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_ready_capture_batch as ready
import autocode_stage_context as context
import autocode_support as support


class ReadyCaptureBatchTests(unittest.TestCase):
    def packet(self, commands, stage="sol"):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            state = {
                "version": 3,
                "task": "Verify the approved greeting",
                "workspace": str(root),
                "settings": {"roles": {stage: {"engine": "codex", "model": "fixture"}}},
                "current_task": {"id": "task", "validation_plan": commands},
                "goal_contract": {"hash": "approved", "body": {}},
            }
            with patch.object(context.support, "snapshot", return_value={"revision": "source", "head": "head"}):
                prompt, _ = context.context_packet(state, stage, root / ".autocode/runs/run/state.json")
            return json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])

    def test_tester_receives_one_executable_batch_for_all_exact_approved_commands(self):
        commands = ["python3 -m unittest tests.test_report -v", "python3 -m unittest discover -v"]
        packet = self.packet(commands)
        batch = packet["ready_capture_batch"]
        self.assertEqual(commands, packet["verification_obligations"]["required_commands"])
        outer = shlex.split(batch["command"])
        self.assertEqual([sys.executable, "-c"], outer[:2])
        payload = json.loads(outer[3])
        self.assertEqual([shlex.split(command) for command in commands], payload["commands"])
        self.assertEqual(shlex.split(packet["capture_command"]), payload["capture"])
        self.assertIn("sequential", batch["instruction"])

    def test_plain_quoted_arguments_stay_literal_and_shell_keywords_fall_back(self):
        command = "python3 -c 'print(\"hello; world\")'"
        self.assertEqual(shlex.split(command), ready.argv(command))
        for unsupported in ("time python3 -m unittest", "command python3 -m unittest", "wait"):
            with self.subTest(command=unsupported):
                self.assertIsNone(ready.argv(unsupported))

    def test_unsupported_shell_or_single_checks_keep_the_individual_capture_command(self):
        for unsupported in (
            "python3 -m unittest && python3 -m pytest",
            "python3 -m unittest | tail -5",
            "python3 -m unittest > tests.log",
            "PYTHONPATH=src python3 -m unittest",
            "python3 -m unittest $TESTS",
            "python3 -m unittest test_*.py",
            "cd src",
        ):
            with self.subTest(command=unsupported):
                packet = self.packet(["python3 -m unittest", f"`{unsupported}`"])
                self.assertNotIn("ready_capture_batch", packet)
                self.assertIn("capture_command", packet)
        for stage in ("terra", "astra_review", "astra_checkpoint"):
            with self.subTest(stage=stage):
                self.assertNotIn(
                    "ready_capture_batch", self.packet(["python3 -m unittest", "python3 -m pytest"], stage)
                )
        self.assertNotIn("ready_capture_batch", self.packet(["python3 -m unittest"]))

    def test_batch_keeps_order_all_failures_fresh_receipts_and_existing_authentication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / ".autocode").mkdir()
            (root / "check.py").write_text(
                "import sys\nfrom pathlib import Path\n"
                "number=int(sys.argv[1])\n"
                "with Path('order.txt').open('a') as stream: stream.write(str(number)+'\\n')\n"
                "print('checked',number)\nraise SystemExit(1 if number==2 else 0)\n"
            )
            commands = [shlex.join([sys.executable, "check.py", str(i)]) for i in range(1, 5)]
            capture = shlex.join([sys.executable, str(Path(support.__file__).with_name("autocode.py")), "capture"])
            batch = ready.context(capture, commands, root / ".autocode/evidence")
            identity = {"attempt": "current", "nonce": "nonce", "source_revision": "source"}
            environment = {key: value for key, value in os.environ.items() if not key.startswith("AUTOCODE_")}
            environment["AUTOCODE_CAPTURE_CONTEXT"] = json.dumps(identity)
            environment["AUTOCODE_OUTPUT_STORE"] = str(root / "store")
            ran = subprocess.run(
                shlex.split(batch["command"]), cwd=root, env=environment, capture_output=True, text=True, timeout=30
            )
            self.assertEqual(1, ran.returncode, ran.stderr)
            self.assertEqual("1\n2\n3\n4\n", (root / "order.txt").read_text())
            checks = [json.loads(line) for line in ran.stdout.splitlines() if '"evidence_ref"' in line]
            self.assertEqual([0, 1, 0, 0], [row["exit_code"] for row in checks])
            self.assertEqual(4, len({row["evidence_ref"] for row in checks}))
            for check in checks:
                check["command"] = shlex.join(check["command"])
            events = root / "events.jsonl"
            item = {
                "id": "batch",
                "type": "command_execution",
                "command": batch["command"],
                "exit_code": 1,
                "aggregated_output": ran.stdout + ran.stderr,
            }
            events.write_text(json.dumps({"type": "item.completed", "item": item}) + "\n")
            support.verify_checks(checks, root, events)
            native = {
                "type": "tool_use",
                "sessionID": "batch-session",
                "part": {
                    "id": "batch",
                    "sessionID": "batch-session",
                    "messageID": "batch-message",
                    "tool": "bash",
                    "state": {
                        "status": "completed",
                        "input": {"command": batch["command"]},
                        "metadata": {"exit": 1},
                        "output": ran.stdout + ran.stderr,
                    },
                },
            }
            events.write_text(json.dumps(native) + "\n")
            support.verify_checks(checks, root, events)
            support.verify_checks(checks, root, events, receipt_only=True, capture_context=identity)
            for changed in ({"nonce": "stale"}, {"source_revision": "different"}, {"attempt": "earlier"}):
                with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "validation attempt"):
                    support.verify_checks(
                        checks, root, events, receipt_only=True, capture_context={**identity, **changed}
                    )
            item.update(type="tool_output")
            item.pop("exit_code")
            events.write_text(json.dumps({"type": "item.completed", "item": item}) + "\n")
            with self.assertRaisesRegex(ValueError, "independently executed"):
                support.verify_checks(checks, root, events)
            Path(checks[0]["evidence_ref"]).unlink()
            with self.assertRaises(ValueError):
                support.verify_checks(checks, root, events, receipt_only=True, capture_context=identity)
