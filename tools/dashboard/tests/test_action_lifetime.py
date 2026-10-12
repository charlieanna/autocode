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
                'Path("parent.ready").write_text("ready")\n'
                'w=Path.cwd();c=Console([w],w/"worker.py")\n'
                'c._execute(str(w),str(w),{"id":"restart-test",'
                '"command":[sys.executable,str(w/"worker.py")]})\n'
            )
            logs = workspace / ".autocode/dashboard-actions/restart-test"
            stderr_path = workspace / "parent-stderr.log"
            with stderr_path.open("w") as parent_stderr:
                parent = subprocess.Popen(
                    [sys.executable, "-c", parent_code],
                    cwd=workspace,
                    stdout=subprocess.DEVNULL,
                    stderr=parent_stderr,
                    start_new_session=True,
                )
            worker_pid = None

            def wait_for(ready, seconds, what):
                # Bootstrap and worker start have separate bounds; a timeout says which, with the parent's stderr (#925).
                deadline = time.monotonic() + seconds
                while time.monotonic() < deadline:
                    value = ready()
                    if value:
                        return value
                    self.assertIsNone(parent.poll(), f"dashboard parent exited: {stderr_path.read_text()[-2000:]}")
                    time.sleep(0.02)
                self.fail(f"{what} not ready within {seconds} s; parent stderr: {stderr_path.read_text()[-2000:]}")

            def started_worker():
                # The worker's complete PID, once the launch receipt the dashboard saved names the same process.
                try:
                    pid = int((workspace / "worker.pid").read_text())
                    receipt = json.loads((logs / "action.json").read_text())
                except (OSError, ValueError):
                    return None
                return pid if receipt.get("pid") == pid else None

            try:
                wait_for(lambda: (workspace / "parent.ready").exists(), 60, "dashboard parent bootstrap")
                worker_pid = wait_for(started_worker, 5, "worker and its launch receipt")
                os.killpg(parent.pid, signal.SIGTERM)
                parent.wait(timeout=5)
                (workspace / "continue").touch()
                deadline = time.monotonic() + 5
                while not (workspace / "done").exists() and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertEqual("complete", (workspace / "done").read_text())
                self.assertIn("survived dashboard restart", (logs / "stdout.log").read_text())
                self.assertEqual("", (logs / "stderr.log").read_text())
                self.assertEqual(worker_pid, json.loads((logs / "action.json").read_text())["pid"])
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.wait(timeout=5)
                if worker_pid is None:  # a worker that started before a wait failed is still ours to stop
                    with contextlib.suppress(OSError, ValueError):
                        worker_pid = int((workspace / "worker.pid").read_text())
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
