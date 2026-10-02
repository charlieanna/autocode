"""Explicit operator recovery grants through the public TaskRun CLI client."""
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from autocode_taskrun import TaskRun, TaskRunError


class RecoveryGrantTests(unittest.TestCase):
    def test_grant_resumes_with_exact_positive_allowance_and_returns_public_view(self):
        run = TaskRun(Path('/repo'), Path('/repo/.autocode/runs/task'),
                      command=('autocode',), options=('--engine', 'opencode'))
        view = {'status': 'RUNNING', 'done': False, 'needs': {'kind': 'continue'}}
        results = [subprocess.CompletedProcess([], 0, '', ''),
                   subprocess.CompletedProcess([], 0, json.dumps({'view': view}), '')]
        with patch('autocode_taskrun.subprocess.run', side_effect=results) as execute:
            self.assertEqual(view, run.grant_recovery(1))
        self.assertEqual(['autocode', '--resume-paused', '--grant-recovery', '1', '--no-chat',
                          '--engine', 'opencode', '--workspace', '/repo',
                          '--run-dir', '/repo/.autocode/runs/task'], execute.call_args_list[0].args[0])
        self.assertNotIn('--grant-recovery', run.options)

    def test_invalid_allowance_never_invokes_cli(self):
        run = TaskRun(Path('/repo'), Path('/repo/.autocode/runs/task'))
        for amount in (0, -1, True, 1.5, '1', None):
            with self.subTest(amount=amount), patch('autocode_taskrun.subprocess.run') as execute:
                with self.assertRaisesRegex(ValueError, 'positive integer'):
                    run.grant_recovery(amount)
                execute.assert_not_called()

    def test_prefixed_cli_rejection_is_not_reported_as_an_accepted_pause(self):
        run = TaskRun(Path('/repo'), Path('/repo/.autocode/runs/task'))
        for output in ('stdout', 'stderr'):
            with self.subTest(output=output):
                streams = {'stdout': '', 'stderr': ''}
                streams[output] = ('AutoResolver: internal checkpoint updated\n'
                                   'Input rejected: current recovery request required\n')
                result = subprocess.CompletedProcess([], 2, **streams)
                with patch('autocode_taskrun.subprocess.run', return_value=result) as execute, \
                        self.assertRaisesRegex(TaskRunError, 'current recovery request required'):
                    run.grant_recovery(1)
                self.assertEqual(1, execute.call_count)
