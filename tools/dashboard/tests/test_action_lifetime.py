"""A dashboard's lifetime must not control an approved runner's lifetime."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console import Console


class ActionLifetimeTests(unittest.TestCase):
    def test_runner_survives_dashboard_process_group_termination(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            worker = workspace / 'worker.py'
            worker.write_text(
                'from pathlib import Path\nimport os,time\n'
                'Path("worker.pid").write_text(str(os.getpid()))\n'
                'print("started",flush=True)\n'
                'while not Path("continue").exists(): time.sleep(.02)\n'
                'print("survived dashboard restart",flush=True)\n'
                'Path("done").write_text("complete")\n')
            parent_code = (
                'import sys\nfrom pathlib import Path\n'
                f'sys.path.insert(0,{str(Path(__file__).resolve().parents[1])!r})\n'
                'from agent_console import Console\n'
                'w=Path.cwd();c=Console([w],w/"worker.py")\n'
                'c._execute(str(w),str(w),{"id":"restart-test",'
                '"command":[sys.executable,str(w/"worker.py")]})\n')
            parent = subprocess.Popen([sys.executable, '-c', parent_code], cwd=workspace,
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                      start_new_session=True)
            worker_pid = None
            try:
                deadline = time.monotonic() + 5
                while not (workspace / 'worker.pid').exists() and time.monotonic() < deadline:
                    self.assertIsNone(parent.poll())
                    time.sleep(.02)
                worker_pid = int((workspace / 'worker.pid').read_text())
                os.killpg(parent.pid, signal.SIGTERM)
                parent.wait(timeout=5)
                (workspace / 'continue').touch()
                deadline = time.monotonic() + 5
                while not (workspace / 'done').exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertEqual('complete', (workspace / 'done').read_text())
                logs = workspace / '.autocode/dashboard-actions/restart-test'
                self.assertIn('survived dashboard restart', (logs / 'stdout.log').read_text())
                self.assertEqual('', (logs / 'stderr.log').read_text())
                self.assertEqual(worker_pid, json.loads((logs / 'action.json').read_text())['pid'])
            finally:
                if parent.poll() is None:
                    parent.kill()
                    parent.wait(timeout=5)
                if worker_pid:
                    try:
                        os.kill(worker_pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass

    def test_action_response_is_bounded_but_full_output_is_retained(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp)
            console = Console([workspace], workspace / 'unused.py')
            action = {'id': 'large-output', 'command': [sys.executable, '-c',
                      'import sys;print("x"*200000);print("error detail",file=sys.stderr)']}
            console._execute(str(workspace), str(workspace), action)
            self.assertEqual('finished', action['status'])
            self.assertEqual(0, action['exit_status'])
            self.assertLess(len(action['stdout']), 132000)
            self.assertEqual(200001, Path(action['stdout_path']).stat().st_size)
            self.assertEqual('error detail\n', action['stderr'])


if __name__ == '__main__':
    unittest.main()
