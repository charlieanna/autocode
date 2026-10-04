"""Candidate process IDs must not transfer ownership across birth or parent changes."""
import ctypes
import errno
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import autocode_process_children as children


class ProcessChildrenTests(unittest.TestCase):
    def process(self, pid, parent, born):
        process = Mock(pid=pid, _ident=(pid, born))
        process.ppid.return_value = parent
        process.is_running.return_value = True
        return process

    def test_non_macos_uses_psutil_recursive_discovery(self):
        root = self.process(10, 1, 1)
        root.children.return_value = ['worker']
        with patch.object(children.sys, 'platform', 'linux'):
            self.assertEqual(['worker'], children.descendants(root))
        root.children.assert_called_once_with(recursive=True)

    def test_only_current_descendants_are_admitted(self):
        root = self.process(10, 1, 2)
        worker = self.process(11, 10, 3)
        grandchild = self.process(12, 11, 4)
        reparented = self.process(13, 99, 3)
        older = self.process(14, 10, 1)
        rows = {11: worker, 12: grandchild, 13: reparented, 14: older}
        candidates = {10: [11, 13, 14], 11: [12], 12: []}
        with patch.object(children.sys, 'platform', 'darwin'), \
                patch.object(children, '_child_pids', side_effect=lambda pid: candidates[pid]), \
                patch.object(children.psutil, 'Process', side_effect=lambda pid: rows[pid]):
            self.assertEqual([worker, grandchild], children.descendants(root))

    def test_reused_parent_cannot_authorize_candidate_children(self):
        root = self.process(10, 1, 2)
        root.is_running.side_effect = [True, False]
        worker = self.process(11, 10, 3)
        with patch.object(children.sys, 'platform', 'darwin'), \
                patch.object(children, '_child_pids', return_value=[11]), \
                patch.object(children.psutil, 'Process', return_value=worker):
            self.assertEqual([], children.descendants(root))

    def test_full_native_buffer_is_retried_without_losing_the_last_child(self):
        sizes = []
        def listing(pid, buffer, nbytes):
            sizes.append(nbytes)
            count = min(len(buffer), 65)
            for index in range(count):
                buffer[index] = 100 + index
            return count
        with patch.object(children, '_library', return_value=SimpleNamespace(proc_listchildpids=listing)):
            self.assertEqual(list(range(100, 165)), children._child_pids(10))
        self.assertEqual([64 * ctypes.sizeof(ctypes.c_int), 128 * ctypes.sizeof(ctypes.c_int)], sizes)

    def test_native_denial_is_not_an_empty_child_set(self):
        def denied(*args):
            ctypes.set_errno(errno.EPERM)
            return 0
        with patch.object(children, '_library', return_value=SimpleNamespace(proc_listchildpids=denied)):
            with self.assertRaises(PermissionError):
                children._child_pids(10)

    def test_invalid_native_count_fails_closed(self):
        with patch.object(children, '_library', return_value=SimpleNamespace(proc_listchildpids=lambda *args: 1000)):
            with self.assertRaisesRegex(OSError, 'Invalid child-process count'):
                children._child_pids(10)
