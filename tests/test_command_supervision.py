"""Public verification-command execution under normal and interrupted ownership."""
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
import autocode_verification_schedule as schedule
import autocode_verify as verify


class CommandSupervisionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="command-supervision-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def command(self, source):
        # exec preserves the actual shell child's native PID when Python starts.
        return "exec " + shlex.join([sys.executable, "-u", "-c", source])

    def test_real_nonzero_exit_preserves_identity_environment_and_failure_evidence(self):
        command = self.command("import json,os,sys; print(json.dumps({'pid':os.getpid(),"
                               "'parent':os.getppid(),'session':os.getsid(0),'cwd':os.getcwd(),"
                               "'secret':os.getenv('OPENAI_API_KEY'),'ci':os.getenv('CI')})); "
                               "print('stderr reached',file=sys.stderr); sys.exit(7)")
        result = verify.run_command(command, self.root, self.root / "normal.log",
                                    env={**os.environ, "OPENAI_API_KEY": "fixture-secret"})
        actual = json.loads(Path(result["output"]).read_text().splitlines()[0])
        self.assertEqual(7, result["exit_code"])
        self.assertFalse(result["timed_out"])
        self.assertEqual(result["supervision"]["provider"]["pid"], actual["pid"])
        self.assertEqual(os.getpid(), actual["parent"])
        self.assertEqual(actual["pid"], actual["session"])
        self.assertEqual(str(self.root.resolve()), actual["cwd"])
        self.assertIsNone(actual["secret"])
        self.assertEqual("1", actual["ci"])
        self.assertIn("stderr reached", result["tail"])
        self.assertTrue(receipts.completed(result, root=self.root))
        self.assertTrue(schedule.intact(result, root=self.root))
        self.assertEqual("failing", verify.suite_health(result))
        self.assertFalse(schedule.reusable(result, root=self.root))
        commands.reconcile(result["supervision"])
        admission = json.loads((Path(result["supervision"]["receipt"]).parent / "admission.json").read_text())
        self.assertEqual(command, admission["command"])
        self.assertEqual(str((self.root / "normal.log").resolve()), admission["output"])
        self.assertEqual(result["supervision"], admission["supervision"])

    def test_original_deadline_overrides_collected_zero_and_complete_passing_output(self):
        command = self.command("print('test_claim (__main__.Claim.test_claim) ... ok\\n\\nRan 1 test in 0.001s\\n\\nOK',flush=True)")
        # Drive the caller's original budget past its deadline only after the
        # actual child reports its zero exit. The independent keeper's clock
        # and launch/wait bounds stay real and unchanged.
        clock = iter((0, 0, 0, 901, 902))
        with patch.object(commands, "time", SimpleNamespace(monotonic=lambda: next(clock))):
            result = verify.run_suite(verify.Framework("unittest", command), command, self.root,
                                      self.root, "late-zero", timeout=900)
        final = receipts.load(result["supervision"])
        self.assertIn(final["cause"], ("provider_stopped", "controller_finished"))
        self.assertEqual("stopped", final["phase"])
        self.assertTrue(result["results"]["complete"])
        self.assertEqual(1, result["results"]["total"])
        self.assertTrue(result["timed_out"])
        self.assertIsNone(result["exit_code"])
        self.assertEqual("timeout", verify.suite_health(result))
        self.assertFalse(receipts.completed(result))
        self.assertFalse(schedule.intact(result, root=self.root))
        self.assertFalse(schedule.reusable(result, root=self.root))
        commands.reconcile(result["supervision"])

    def test_failed_before_exec_checkpoint_never_runs_command_and_allows_clean_retry(self):
        marker = self.root / "must-not-run"
        metadata = []
        command = self.command(f"from pathlib import Path; Path({str(marker)!r}).touch()")
        def checkpoint(_command, _output, value):
            metadata.append(value)
            raise OSError("admission persistence failed")
        with self.assertRaisesRegex(OSError, "admission persistence failed"):
            verify.run_command(command, self.root, self.root / "admission.log", checkpoint=checkpoint)
        self.assertFalse(marker.exists())
        commands.reconcile(metadata[0])
        result = verify.run_command(command, self.root, self.root / "retry.log")
        self.assertEqual(0, result["exit_code"])
        self.assertTrue(marker.is_file())
        self.assertTrue(receipts.completed(result))
        self.assertNotEqual(metadata[0]["nonce"], result["supervision"]["nonce"])


if __name__ == "__main__":
    unittest.main()
