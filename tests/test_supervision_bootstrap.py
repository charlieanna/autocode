"""The pre-exec gate distinguishes keeper admission from durable checkpoint completion."""

import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import call, patch

import autocode_supervision as supervision
import autocode_supervision_bootstrap as bootstrap


class BootstrapReleaseTests(unittest.TestCase):
    def run_bootstrap(self, messages, *, ready=True):
        with (
            patch.object(bootstrap.sys, "argv", ["bootstrap", "42", '["provider"]']),
            patch.object(bootstrap.select, "select", return_value=([42] if ready else [], [], [])) as select,
            patch.object(bootstrap.os, "read", side_effect=messages) as read,
            patch.object(bootstrap.os, "close") as close,
            patch.object(bootstrap.os, "execvpe") as execute,
        ):
            result = bootstrap.main()
        close.assert_called_once_with(42)
        return result, select, read, execute

    def test_armed_checkpoint_wait_uses_keeper_deadline_before_release(self):
        result, select, read, execute = self.run_bootstrap([b"A", b"G"])
        self.assertIsNone(result)
        select.assert_called_once_with([42], [], [], 15)
        self.assertEqual(read.call_args_list, [call(42, 1), call(42, 1)])
        execute.assert_called_once_with("provider", ["provider"], bootstrap.os.environ)

    def test_unarmed_wait_still_expires_without_executing(self):
        result, select, read, execute = self.run_bootstrap([], ready=False)
        self.assertEqual(result, 126)
        select.assert_called_once_with([42], [], [], 15)
        read.assert_not_called()
        execute.assert_not_called()

    def test_release_requires_both_admission_and_checkpoint(self):
        for messages in ([b"G"], [b""], [b"X"], [b"A", b""], [b"A", b"X"]):
            with self.subTest(messages=messages):
                result, _, _, execute = self.run_bootstrap(messages)
                self.assertEqual(result, 126)
                execute.assert_not_called()


@unittest.skipUnless(sys.platform == "linux", "supervision qualification requires supported Linux")
class AdmittedCheckpointTests(unittest.TestCase):
    def test_checkpoint_longer_than_bootstrap_admission_window_preserves_owned_command(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "executed"

            def checkpoint(metadata):
                # Deliberately cross the old release timer. This is a fault
                # injection, not a speed assertion: the keeper's limit is 90s.
                threading.Event().wait(16)
                self.assertFalse(marker.exists())
                self.assertEqual(supervision.receipt(metadata)["phase"], "armed")

            with supervision.launch_command(
                [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
                receipt_path=Path(directory) / "receipt.json",
                timeout=90,
                checkpoint=checkpoint,
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ) as child:
                self.assertEqual(child.wait(timeout=30), 0)
            self.assertTrue(marker.is_file())
            receipt = json.loads((Path(directory) / "receipt.json").read_text())
            self.assertIsNone(receipt["cleanup_error"])
