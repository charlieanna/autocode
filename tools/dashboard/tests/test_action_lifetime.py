"""A dashboard's lifetime must not control an approved runner's lifetime."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import contextlib

from agent_console import Console


class ActionLifetimeTests(unittest.TestCase):
    def test_runner_survives_dashboard_process_group_termination(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            worker = workspace / "worker.py"
            worker.write_text(
                "from pathlib import Path\nimport os,time\n"
                'Path("worker.pid").write_text(str(os.getpid()))\n'
                'print("started",flush=True)\n'
                'while not Path("continue").exists(): time.sleep(.02)\n'
                'print("survived dashboard restart",flush=True)\n'
                'Path("done").write_text("complete")\n'
            )
            parent_code = (
                "import sys\nfrom pathlib import Path\n"
                f"sys.path.insert(0,{str(Path(__file__).resolve().parents[1])!r})\n"
                "from agent_console import Console\n"
                'w=Path.cwd();c=Console([w],w/"worker.py")\n'
                'Path("parent.ready").write_text("ready")\n'
                'c._execute(str(w),str(w),{"id":"restart-test",'
                '"command":[sys.executable,str(w/"worker.py")]})\n'
            )
            parent_error = workspace / "parent.stderr"
            parent_stderr = parent_error.open("wb")
            parent = subprocess.Popen(
                [sys.executable, "-c", parent_code],
                cwd=workspace,
                stdout=subprocess.DEVNULL,
                stderr=parent_stderr,
                start_new_session=True,
            )
            worker_pid = None
            try:
                # Cold imports belong to fixture setup, before worker readiness.
                # Keep setup bounded without weakening the worker/survival gates.
                deadline = time.monotonic() + 30
                while not (workspace / "parent.ready").exists() and time.monotonic() < deadline:
                    self.assertIsNone(parent.poll(), parent_error.read_text())
                    time.sleep(0.02)
                self.assertTrue((workspace / "parent.ready").exists(),
                                "Dashboard fixture bootstrap did not become ready: " + parent_error.read_text())
                logs = workspace / ".autocode/dashboard-actions/restart-test"
                deadline = time.monotonic() + 5
                launch_ready = False
                while time.monotonic() < deadline:
                    self.assertIsNone(parent.poll(), parent_error.read_text())
                    try:
                        worker_pid = int((workspace / "worker.pid").read_text())
                        launch_ready = json.loads((logs / "action.json").read_text()).get("pid") == worker_pid
                    except (FileNotFoundError, ValueError):
                        launch_ready = False
                    if launch_ready:
                        break
                    time.sleep(0.02)
                self.assertTrue(launch_ready,
                                "Runner and its saved launch receipt did not become ready: " + parent_error.read_text())
                os.killpg(parent.pid, signal.SIGTERM)
                parent.wait(timeout=5)
                (workspace / "continue").touch()
                deadline = time.monotonic() + 5
                while not (workspace / "done").exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertEqual("complete", (workspace / "done").read_text())
                logs = workspace / ".autocode/dashboard-actions/restart-test"
                self.assertIn("survived dashboard restart", (logs / "stdout.log").read_text())
                self.assertEqual("", (logs / "stderr.log").read_text())
                self.assertEqual(worker_pid, json.loads((logs / "action.json").read_text())["pid"])
            finally:
                parent_stderr.close()
                if parent.poll() is None:
                    parent.kill()
                    parent.wait(timeout=5)
                if worker_pid:
                    with contextlib.suppress(ProcessLookupError):
                        os.kill(worker_pid, signal.SIGTERM)

    def test_action_response_is_bounded_but_full_output_is_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            console = Console([workspace], workspace / "unused.py")
            action = {
                "id": "large-output",
                "command": [sys.executable, "-c", 'import sys;print("x"*200000);print("error detail",file=sys.stderr)'],
            }
            console._execute(str(workspace), str(workspace), action)
            self.assertEqual("finished", action["status"])
            self.assertEqual(0, action["exit_status"])
            self.assertLess(len(action["stdout"]), 132000)
            self.assertEqual(200001, Path(action["stdout_path"]).stat().st_size)
            self.assertEqual("error detail\n", action["stderr"])


if __name__ == "__main__":
    unittest.main()
