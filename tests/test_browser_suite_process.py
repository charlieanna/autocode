"""Short, controlled subprocess regressions for browser catalogue supervision."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_process as processes

from tests import browser_suite_process as runner


class BrowserSuiteProcessTests(unittest.TestCase):
    def test_success_and_assertion_failure_keep_separate_output_and_exit_receipts(self):
        for code in (0, 7):
            with self.subTest(code=code):
                result = runner.run(
                    [
                        sys.executable,
                        "-c",
                        f"import sys; print('stdout'); print('stderr', file=sys.stderr); sys.exit({code})",
                    ],
                    cwd=Path.cwd(),
                    env=os.environ.copy(),
                    timeout=15,
                )
                self.assertEqual(code, result.returncode)
                self.assertEqual("stdout\n", result.stdout)
                self.assertEqual("stderr\n", result.stderr)
                self.assertEqual("completed" if code == 0 else "exit_failure", result.receipt["reason"])
                self.assertEqual([], result.receipt["cleanup"]["live_pids"])
                self.assertTrue(result.receipt["cleanup"]["root_reaped"])

    def test_setup_failure_keeps_command_and_reason(self):
        with self.assertRaises(FileNotFoundError) as raised:
            runner.run(["/nonexistent/autocode-fixture"], cwd=Path.cwd(), env=os.environ.copy(), timeout=420)
        receipt = raised.exception.receipt
        self.assertEqual("setup_error", receipt["reason"])
        self.assertEqual(420, receipt["timeout_seconds"])
        self.assertEqual(["/nonexistent/autocode-fixture"], receipt["command"])
        self.assertIsNone(receipt["returncode"])

    def exercise_tree(self, interrupt):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            script = root / "parent.py"
            script.write_text("""import json, os, signal, subprocess, sys
from pathlib import Path
workers = []
for detached in (False, True):
    worker = subprocess.Popen(
        [sys.executable, '-c', "import signal; print('ready',flush=True); signal.pause()"],
        stdout=subprocess.PIPE, text=True, start_new_session=detached)
    assert worker.stdout.readline().strip() == 'ready'
    workers.append(worker)
print('partial stdout', flush=True)
print('partial stderr', file=sys.stderr, flush=True)
Path('ready.tmp').write_text(json.dumps([w.pid for w in workers]))
Path('ready.tmp').rename('ready.json')
signal.pause()
""")
            unrelated = subprocess.Popen(
                [sys.executable, "-c", "import signal; signal.pause()"], start_new_session=True
            )
            timers, fired, saved = [], [], []
            real_timer = threading.Timer
            real_sample = processes.ProcessTree.sample

            def timer(interval, callback, args=()):
                result = real_timer(interval, callback, args=args)
                if interval == 15:
                    timers.append(result)
                return result

            def sample(tree, **kwargs):
                rows = real_sample(tree, **kwargs)
                ready = root / "ready.json"
                if not fired and ready.exists():
                    pids = json.loads(ready.read_text())
                    if set(pids) <= set(tree.known):
                        saved[:] = list(tree.known.values())
                        fired.append(True)
                        # Trigger only once both ordinary and detached fixtures
                        # are known. No sleeps or long deadline in this test.
                        if interrupt:
                            os.kill(os.getpid(), signal.SIGTERM)
                        else:
                            timers[0].function(*timers[0].args)
                return rows

            expected = KeyboardInterrupt if interrupt else subprocess.TimeoutExpired
            try:
                with (
                    patch.object(threading, "Timer", timer),
                    patch.object(processes.ProcessTree, "sample", sample),
                    self.assertRaises(expected) as raised,
                ):
                    runner.run([sys.executable, str(script)], cwd=root, env=os.environ.copy(), timeout=15)
                self.assertEqual([True], fired, "fixture tree was never observed")
                receipt = raised.exception.receipt
                self.assertEqual("interrupted" if interrupt else "timeout", receipt["reason"])
                self.assertEqual("partial stdout\n", receipt["stdout"])
                self.assertEqual("partial stderr\n", receipt["stderr"])
                self.assertEqual(15, receipt["timeout_seconds"])
                self.assertTrue(receipt["cleanup"]["checked"])
                self.assertTrue(receipt["cleanup"]["root_reaped"])
                self.assertEqual([], receipt["cleanup"]["live_pids"])
                self.assertEqual([], processes.live_processes(saved))
                self.assertGreaterEqual(len(receipt["cleanup"]["owned"]), 3)
                self.assertIsNone(unrelated.poll(), "unrelated process was signalled")
                if not interrupt:
                    self.assertEqual(receipt["stdout"], raised.exception.output)
                    self.assertEqual(receipt["stderr"], raised.exception.stderr)
            finally:
                unrelated.kill()
                unrelated.wait(timeout=3)
                # Test isolation if a regression defeats the production cleanup.
                for row in processes.live_processes(saved):
                    os.kill(row["pid"], signal.SIGKILL)

    def test_timeout_fails_and_cleans_owned_detached_fixture_only(self):
        self.exercise_tree(interrupt=False)

    def test_sigterm_preserves_failure_receipt_and_cleans_owned_fixture_only(self):
        self.exercise_tree(interrupt=True)

    def test_catalogue_records_timeout_before_propagating_and_does_not_cache_it(self):
        from tests import test_catalogue_t12 as catalogue

        error = subprocess.TimeoutExpired(["node", "fixture"], 900)
        error.receipt = {
            "reason": "timeout",
            "stdout": "partial",
            "stderr": "diagnostic",
            "command": error.cmd,
            "timeout_seconds": 900,
            "cleanup": {"checked": True, "live_pids": []},
        }
        case = catalogue.DashboardCase()
        case.bundle = Mock()
        with (
            patch.object(catalogue.shutil, "which", return_value="/available"),
            patch.object(catalogue, "_SUITE_CACHE", {}),
            patch.object(catalogue.browser_suite_process, "run", side_effect=error) as invoke,
        ):
            with self.assertRaises(subprocess.TimeoutExpired):
                case.browser("a11y")
            self.assertEqual({}, catalogue._SUITE_CACHE)
        case.bundle.log.assert_called_once_with("dashboard_suite", suite="a11y", kind="browser", **error.receipt)
        self.assertEqual(900, invoke.call_args.kwargs["timeout"])


if __name__ == "__main__":
    unittest.main()
