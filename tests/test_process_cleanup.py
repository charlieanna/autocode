"""ProcessTree.stop keeps a reap timeout typed and still forces the recorded workers down (#553)."""

import signal
import subprocess
import unittest
from unittest import mock

import autocode_process as processes


class ReapTimeoutTests(unittest.TestCase):
    def stop(self, wait_error):
        """Stop a tree whose workers are gone but whose direct child is not reaped; (error, signals sent)."""
        tree = processes.ProcessTree(4242, lambda _rows: None)
        worker = {"pid": 4243, "started": "Mon Oct 12 07:00:00 2026"}
        tree.known = {4243: worker}  # a recorded, birth-verified worker
        child = mock.Mock(pid=4242)
        child.poll.return_value = None
        child.wait.side_effect = wait_error
        sent = []
        with (
            mock.patch.object(tree, "sample", return_value=[]),
            mock.patch.object(
                tree, "signal", side_effect=lambda rows, sig: sent.append((sig, [r["pid"] for r in rows]))
            ),
            self.assertRaises(processes.ProcessError) as caught,
        ):
            tree.stop(child)
        return caught.exception, sent

    def test_a_child_not_reaped_in_time_is_typed_and_runs_the_kill_fallback(self):
        error, sent = self.stop(subprocess.TimeoutExpired(["provider"], 1))
        self.assertIn("not reaped after cleanup", str(error))
        self.assertIsInstance(error.__cause__, subprocess.TimeoutExpired)
        self.assertIn((signal.SIGKILL, [4243]), sent)

    def test_a_typed_cleanup_failure_is_unchanged(self):
        error, sent = self.stop(processes.ProcessError("Cannot signal owned process 4243: permission denied"))
        self.assertEqual("Cannot signal owned process 4243: permission denied", str(error))
        self.assertIn((signal.SIGKILL, [4243]), sent)


if __name__ == "__main__":
    unittest.main()
