"""A grading result is publishable only after its owned processes stop."""
import json
from contextlib import nullcontext
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_grader_process as grader
import autocode_process as processes


class GraderExitObservationTests(unittest.TestCase):
    def observed_process(self, status, birth=1.0):
        process = Mock()
        process.oneshot.return_value = nullcontext()
        process.create_time.return_value = birth
        process._ident = (77, birth)
        process.ppid.return_value = grader.os.getpid()
        process.status.return_value = status
        return process

    def test_exit_between_status_and_group_read_uses_fresh_zombie_identity(self):
        before = self.observed_process('running')
        after = self.observed_process(grader.psutil.STATUS_ZOMBIE)
        with patch.object(grader.psutil, 'Process', side_effect=[before, after]), \
                patch.object(grader.os, 'getpgid', side_effect=ProcessLookupError):
            row = grader._leader(SimpleNamespace(pid=77))
        self.assertEqual(grader.psutil.STATUS_ZOMBIE, row['state'])
        self.assertEqual(77, row['group'])

    def test_unavailable_group_does_not_authorize_a_different_birth_identity(self):
        before = self.observed_process('running')
        after = self.observed_process(grader.psutil.STATUS_ZOMBIE, birth=2.0)
        with patch.object(grader.psutil, 'Process', side_effect=[before, after]), \
                patch.object(grader.os, 'getpgid', side_effect=ProcessLookupError):
            with self.assertRaisesRegex(processes.ProcessError, 'zombie identity'):
                grader._leader(SimpleNamespace(pid=77))

    def test_exit_transition_waits_for_observed_zombie_not_an_unknown_status(self):
        observations = [self.observed_process(status) for status in
                        ('running', 'unknown', grader.psutil.STATUS_ZOMBIE)]
        with patch.object(grader.psutil, 'Process', side_effect=observations), \
                patch.object(grader.os, 'getpgid', side_effect=ProcessLookupError), \
                patch.object(grader.time, 'sleep'):
            row = grader._leader(SimpleNamespace(pid=77))
        self.assertEqual(grader.psutil.STATUS_ZOMBIE, row['state'])


class GraderProcessTests(unittest.TestCase):
    def fixture(self, root):
        # Children block on their own pipe, so parent exit cannot release them.
        code = ("import subprocess,sys,json\nfrom pathlib import Path\n"
                "children=[subprocess.Popen([sys.executable,'-c',"
                f"'import os; r,w=os.pipe(); os.read(r,1)',{str(root)!r}]) for _ in range(4)]\n"
                f"Path({str(root / 'children.json')!r}).write_text(json.dumps([p.pid for p in children]))\n")
        child = subprocess.Popen([sys.executable, '-c', code], start_new_session=True)
        cleanup = processes.ProcessTree(child.pid, lambda rows: None)
        cleanup.capture_root()
        return child, cleanup

    def test_fast_parent_exit_waits_for_children_and_preserves_unrelated_process(self):
        sentinel = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                child, cleanup = self.fixture(root)
                try:
                    code, expired, receipt = grader.wait(child, 10)
                    self.assertEqual(0, code)
                    self.assertFalse(expired)
                    self.assertTrue(receipt['checked'])
                    self.assertEqual([], processes.live_processes(receipt['owned']))
                    expected = set(json.loads((root / 'children.json').read_text()))
                    self.assertTrue(expected <= {row['pid'] for row in receipt['owned']})
                    self.assertIsNone(sentinel.poll())
                finally:
                    cleanup.stop(child)
        finally:
            sentinel.stdin.close()
            sentinel.wait(timeout=5)

    def test_incomplete_cleanup_cannot_return_a_grading_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            child, cleanup = self.fixture(Path(tmp))
            try:
                # Negative control: a cleanup implementation that returns while
                # workers still exist must raise, even after a successful root.
                with patch.object(processes.ProcessTree, 'stop', return_value=None):
                    with self.assertRaisesRegex(processes.ProcessError, 'live owned processes') as rejected:
                        grader.wait(child, 10)
                    cleanup.known.update({row['pid']: row for row in rejected.exception.processes})
            finally:
                cleanup.stop(child)
