"""Real POSIX process-tree regressions; no inference or network access."""
import os
import signal
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode_process as processes


class ProcessTests(unittest.TestCase):
    def exercise_tree(self, parent_lifetime, timeout):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            worker = root / 'worker.py'
            worker.write_text("from pathlib import Path\nimport os,time\nPath('worker.pid').write_text(str(os.getpid()))\ntime.sleep(10)\nPath('late-write').write_text('leaked')\n")
            parent = root / 'parent.py'
            parent.write_text("import subprocess,sys,time\nsubprocess.Popen([sys.executable,'worker.py'],start_new_session=True)\ntime.sleep(" + str(parent_lifetime) + ")\n")
            child = subprocess.Popen([sys.executable, str(parent)], cwd=root, start_new_session=True)
            saved = []
            try:
                code, expired = processes.wait_for_stage(child, timeout, lambda rows: saved.__setitem__(slice(None), rows))
                pid = int((root / 'worker.pid').read_text())
                self.assertIn(pid, [p['pid'] for p in saved])
                self.assertEqual([], processes.live_processes(saved))
                self.assertFalse((root / 'late-write').exists())
                return code, expired
            finally:
                if child.poll() is None:
                    child.kill()
                    child.wait()

    def test_timeout_stops_detached_tool_processes(self):
        code, expired = self.exercise_tree(30, 1)
        self.assertTrue(expired)
        self.assertNotEqual(0, code)

    def test_normal_provider_exit_stops_background_writers(self):
        code, expired = self.exercise_tree(.8, 5)
        self.assertFalse(expired)
        self.assertEqual(0, code)

    def test_checkpoint_failure_still_cleans_up_child(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        def checkpoint(rows):
            raise OSError('fixture disk full')
        with self.assertRaisesRegex(OSError, 'disk full'):
            processes.wait_for_stage(child, 5, checkpoint)
        self.assertIsNotNone(child.poll())

    def test_reused_pid_is_not_a_live_owned_process(self):
        row = processes.process_table()[os.getpid()]
        self.assertEqual([], processes.live_processes([{**processes.identity(row), 'started':'old process'}]))

    def test_sigterm_cleans_up_before_interrupt_propagates(self):
        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
        def checkpoint(rows):
            os.kill(os.getpid(), signal.SIGTERM)
        with processes.interruption_handler(), self.assertRaises(KeyboardInterrupt):
            processes.wait_for_stage(child, 5, checkpoint)
        self.assertIsNotNone(child.poll())


if __name__ == '__main__':
    unittest.main()
