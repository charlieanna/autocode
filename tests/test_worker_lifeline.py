"""A Builder worker's lifeline (#454): the Orchestrator's declaration and receipt classification,
and how the worker's entry point reports a lifeline it refused."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import autocode_process as processes
import autocode_supervision_cli as supervision_cli
import autocode_worker_lifeline as lifeline


class WorkerLifelineTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.me = processes.identity(processes.process_table({os.getpid()})[os.getpid()])
        self.record = {'schema': 1, 'nonce': 'a' * 32, 'owner': self.me,
                       'receipt': str(self.root / 'lifeline' / ('a' * 32 + '-supervision.json')), 'started_at': 'then'}

    def write(self, **changes):
        value = {'schema': 1, 'nonce': 'a' * 32, 'owner': self.me, 'keeper': {'pid': 2 ** 30, 'birth_identity': 1.0},
                 'provider': {'pid': 2 ** 30 + 1, 'birth_identity': 2.0}, 'receipt': self.record['receipt'],
                 'phase': 'armed', 'cause': None, 'cleanup_error': None, 'processes': [], **changes}
        path = Path(self.record['receipt'])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
        return value

    def test_prepare_declares_a_fresh_lifeline_without_a_deadline(self):
        record, read_fd, write_fd = lifeline.prepare(self.root)
        try:
            self.assertEqual({'schema', 'nonce', 'owner', 'receipt', 'started_at'}, set(record))
            self.assertTrue(processes.matches(record['owner'], self.me))
            self.assertEqual(self.root / 'lifeline' / f"{record['nonce']}-supervision.json", Path(record['receipt']))
            self.assertFalse(Path(record['receipt']).exists())
            self.assertEqual(['--owner-lifeline-fd', str(read_fd)], lifeline.argv(read_fd))
            # The worker is the Orchestrator's child; here this process stands in for both.
            with patch.object(supervision_cli.os, 'getppid', return_value=os.getpid()):
                packet, remaining = supervision_cli.declaration(read_fd)
            self.assertIsNone(remaining)
            self.assertEqual({key: record[key] for key in ('nonce', 'owner', 'receipt')},
                             {key: packet[key] for key in ('nonce', 'owner', 'receipt')})
        finally:
            os.close(read_fd)
            os.close(write_fd)
        second, read_fd, write_fd = lifeline.prepare(self.root)
        os.close(read_fd)
        os.close(write_fd)
        self.assertNotEqual(record['nonce'], second['nonce'])

    def test_classify_adopts_only_verified_cleanup(self):
        cases = [({'phase': 'armed'}, 'running'), ({'phase': 'stopping', 'cause': 'owner_lost'}, 'running'),
                 ({'phase': 'discharged', 'cause': 'controller_finished'}, 'discharged'),
                 ({'phase': 'stopped', 'cause': 'owner_lost'}, 'stopped'),
                 ({'phase': 'stopped', 'cause': 'provider_stopped'}, 'stopped'),
                 ({'phase': 'stopped', 'cause': 'owner_lost', 'cleanup_error': 'ProcessError'}, 'uncertain'),
                 ({'phase': 'discharged', 'cause': 'owner_lost'}, 'uncertain'),
                 ({'phase': 'discharged', 'cause': 'controller_finished', 'cleanup_error': 'x'}, 'uncertain'),
                 ({'phase': 'uncertain', 'cause': 'keeper_failure'}, 'uncertain'), (None, 'uncertain')]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(expected, lifeline.classify(value))
        self.assertEqual({'discharged', 'stopped', 'never_armed'}, lifeline.ADOPTABLE)

    def test_settled_reads_only_the_receipt_bound_to_this_launch(self):
        self.assertEqual('never_armed', lifeline.settled(self.record))
        self.assertIsNone(lifeline.read(self.record))
        self.write()
        self.assertEqual('running', lifeline.settled(self.record))
        self.write(phase='stopped', cause='owner_lost')
        self.assertEqual('stopped', lifeline.settled(self.record))
        for changes in ({'nonce': 'b' * 32}, {'owner': {**self.me, 'birth_identity': 1.5}}, {'schema': 2}):
            with self.subTest(changes=changes):
                self.write(phase='stopped', cause='owner_lost', **changes)
                self.assertIsNone(lifeline.read(self.record))
                self.assertEqual('uncertain', lifeline.settled(self.record))
        Path(self.record['receipt']).write_text('{not json')
        self.assertEqual('uncertain', lifeline.settled(self.record))

    def test_owned_adds_the_keeper_and_its_inventory_to_the_sampled_tree(self):
        sampled = [{'pid': 2 ** 30 + 2, 'birth_identity': 3.0}]
        self.assertEqual(sampled, lifeline.owned(self.record, sampled))
        self.assertEqual([], lifeline.owned(None))
        self.write(phase='stopped', keeper=self.me, processes=[{'pid': 2 ** 30 + 3, 'birth_identity': 4.0}, {'pid': 'x'}, None])
        owned = lifeline.owned(self.record, sampled)
        self.assertEqual([*sampled, self.me, {'pid': 2 ** 30 + 3, 'birth_identity': 4.0}], owned)
        # A keeper still running keeps the worker's milestone busy even when nothing else was sampled.
        self.assertEqual([os.getpid()], [row['pid'] for row in processes.live_processes(lifeline.owned(self.record))])
        self.write(phase='stopped', nonce='b' * 32, keeper=self.me)
        self.assertEqual(sampled, lifeline.owned(self.record, sampled))


class WorkerEntryTests(unittest.TestCase):
    """Only the lifeline's own failures are reported as lifeline errors; the worker's keep their traceback."""
    script = Path(__file__).resolve().parents[1] / 'tools' / 'autocode_builder_worker.py'

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()

    def run_worker(self, *argv, pass_fds=()):
        child = subprocess.Popen([sys.executable, str(self.script), *argv], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, pass_fds=pass_fds)
        for fd in pass_fds:
            os.close(fd)
        self.addCleanup(lambda: child.poll() is None and child.kill())
        stdout, stderr = child.communicate(timeout=60)
        return child.returncode, stdout + stderr

    def test_a_refused_lifeline_is_named_and_does_no_work(self):
        code, output = self.run_worker(str(self.root), 'start', supervision_cli.FLAG, '1')
        self.assertEqual(2, code, output)
        self.assertIn('Builder lifeline: Owner lifeline must be an inherited pipe descriptor', output)
        self.assertNotIn('Traceback', output)
        self.assertEqual([], list(self.root.iterdir()))

    def test_the_workers_own_failure_keeps_its_traceback_under_a_sound_lifeline(self):
        record, read_fd, write_fd = lifeline.prepare(self.root)
        try:
            # No state.json: main() fails on its own, inside a lifeline that was armed and then discharged.
            code, output = self.run_worker(str(self.root / 'missing'), 'start', *lifeline.argv(read_fd),
                                           pass_fds=(read_fd,))
        finally:
            os.close(write_fd)
        self.assertEqual(1, code, output)
        self.assertIn('Traceback', output)
        self.assertIn("FileNotFoundError: [Errno 2] No such file or directory: '" + str(self.root / 'missing'), output)
        self.assertNotIn('Builder lifeline', output)
        self.assertEqual('discharged', lifeline.settled(record), lifeline.read(record))


if __name__ == '__main__':
    unittest.main()
