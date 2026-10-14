"""Public model-free probe execution preserves exact inputs and honest results."""

import hashlib
import json
import os
import shlex
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_command_receipt as receipts
import autocode_command_supervision as commands
import autocode_preflight_worker as worker


class WorkerExecutionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="preflight-worker-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "workspace with spaces ; $()"
        self.workspace.mkdir()
        self.environment = {"PATH": "/usr/bin:/bin", "LC_ALL": "C", "PYTHONUTF8": "1", "PROBE_VALUE": "literal value"}
        if sys.platform == "darwin":
            # The macOS runtime supplies this at startup when it is absent.
            self.environment["__CF_USER_TEXT_ENCODING"] = f"0x{os.getuid():X}:0x0:0x0"

    def test_exact_argv_environment_cwd_and_nonzero_exit_are_preserved_without_a_shell(self):
        marker = self.root / "must-not-be-created"
        arguments = ["two words", "quote'\"", "; touch " + str(marker), "$(touch " + str(marker) + ")", "first\nsecond"]
        code = (
            "import json,os,sys; "
            "print(json.dumps({'argv':sys.argv[1:],'cwd':os.getcwd(),'environment':dict(os.environ),"
            "'parent':os.getppid()},ensure_ascii=False)); sys.exit(13)"
        )
        argv = [sys.executable, "-u", "-c", code, *arguments]
        output = self.root / "probe.log"
        result = worker.run(argv, self.workspace, output, timeout=30, environment=self.environment)
        actual = json.loads(output.read_text())
        self.assertEqual(arguments, actual["argv"])
        self.assertEqual(str(self.workspace), actual["cwd"])
        self.assertEqual(self.environment, actual["environment"])
        self.assertEqual(os.getpid(), actual["parent"])
        self.assertFalse(marker.exists())
        self.assertEqual(shlex.join(argv), result["command"])
        self.assertEqual(str(output), result["output"])
        self.assertEqual(13, result["exit_code"])
        self.assertFalse(result["timed_out"])
        self.assertFalse(result["interrupted"])
        self.assertTrue(receipts.completed(result, root=self.root))
        self.assertIn("supervision", result)

    def test_merged_unicode_output_is_complete_and_the_tail_and_hash_match_it(self):
        stdout, stderr = "stdout\né用户🙂" * 1800, "\nstderr τέλος\n"
        expected = stdout + stderr
        code = "import os; os.write(1," + repr(stdout.encode()) + "); os.write(2," + repr(stderr.encode()) + ")"
        output = self.root / "unicode.log"
        result = worker.run(
            [sys.executable, "-c", code], self.workspace, output, timeout=30, environment=self.environment
        )
        self.assertEqual(expected.encode(), output.read_bytes())
        self.assertEqual(expected[-4000:], result["tail"])
        self.assertEqual(hashlib.sha256(expected.encode()).hexdigest(), result["output_sha256"])
        self.assertEqual(0, result["exit_code"])
        self.assertTrue(receipts.completed(result, root=self.root))

    def test_pause_interrupts_a_model_free_probe_without_publishing_an_exit(self):
        result = worker.run(
            [sys.executable, "-c", "import signal; signal.pause()"],
            self.workspace,
            self.root / "pause.log",
            timeout=30,
            environment=self.environment,
            paused=lambda: True,
        )
        self.assertTrue(result["interrupted"])
        self.assertFalse(result["timed_out"])
        self.assertIsNone(result["exit_code"])
        self.assertFalse(receipts.completed(result))

    def test_failed_persistence_checkpoint_never_executes_probe_and_cleanup_reconciles(self):
        marker = self.root / "must-not-run"
        argv = [sys.executable, "-c", "from pathlib import Path; Path(" + repr(str(marker)) + ").touch()"]
        admitted = []

        def checkpoint(command, output, metadata):
            admitted.append((command, output, metadata))
            raise OSError("probe admission persistence failed")

        token = commands.CHECKPOINT.set(checkpoint)
        try:
            with self.assertRaisesRegex(OSError, "probe admission persistence failed"):
                worker.run(argv, self.workspace, self.root / "checkpoint.log", timeout=30, environment=self.environment)
        finally:
            commands.CHECKPOINT.reset(token)
        self.assertFalse(marker.exists())
        self.assertEqual(1, len(admitted))
        self.assertEqual(shlex.join(argv), admitted[0][0])
        self.assertEqual(self.root / "checkpoint.log", admitted[0][1])
        final = commands.reconcile(admitted[0][2])
        self.assertEqual("stopped", final["phase"])
        self.assertFalse(final["cleanup_error"])

    def test_exhausted_budget_returns_timeout_without_executing_a_probe(self):
        marker = self.root / "must-not-start"
        output = self.root / "exhausted.log"
        result = worker.run(
            [sys.executable, "-c", "from pathlib import Path; Path(" + repr(str(marker)) + ").touch()"],
            self.workspace,
            output,
            timeout=0,
            environment=self.environment,
        )
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["interrupted"])
        self.assertIsNone(result["exit_code"])
        self.assertFalse(marker.exists())
        self.assertEqual(b"", output.read_bytes())

    def test_original_deadline_invalidates_complete_output_without_waiting_for_a_timeout(self):
        output = self.root / "deadline.log"
        completed = b"probe completed successfully\n"

        def monotonic():
            # Only the caller's clock changes. The native keeper and process
            # launch keep their real clocks and do not wait for an artificial timeout.
            return 901 if output.is_file() and completed in output.read_bytes() else 0

        with patch.object(worker, "time", SimpleNamespace(monotonic=monotonic)):
            result = worker.run(
                [sys.executable, "-c", "import os; os.write(1," + repr(completed) + ")"],
                self.workspace,
                output,
                timeout=900,
                environment=self.environment,
            )
        self.assertEqual(completed, output.read_bytes())
        self.assertTrue(result["timed_out"])
        self.assertFalse(result["interrupted"])
        self.assertIsNone(result["exit_code"])
        self.assertFalse(receipts.completed(result))


if __name__ == "__main__":
    unittest.main()
