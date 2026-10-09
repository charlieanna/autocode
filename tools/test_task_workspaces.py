"""Real Git/process tests for concurrent tasks originating in one project."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from tools import autocode_workspaces as w, autocode_support as support
from tools import test_subprocess


class TaskWorkspaces(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        w.git(self.project, 'init', '-q')
        (self.project / 'app.txt').write_text('committed\n')
        w.git(self.project, 'add', 'app.txt')
        w.git(self.project, '-c', 'user.name=Test', '-c', 'user.email=t@example.test', 'commit', '-qm', 'base')

    def test_two_tasks_isolate_files_branches_and_writer_locks(self):
        (self.project / 'app.txt').write_text('user unsaved work\n')
        with support.workspace_lock(self.project):
            one = w.create(self.project, 'Same task')
            two = w.create(self.project, 'Same task')
            left, right = Path(one['workspace']), Path(two['workspace'])
            with support.workspace_lock(left), support.workspace_lock(right):
                (left / 'app.txt').write_text('task one\n')
                (right / 'app.txt').write_text('task two\n')
                self.assertEqual('user unsaved work\n', (self.project / 'app.txt').read_text())
                with self.assertRaises(support.Paused):
                    with support.workspace_lock(left):
                        pass
        self.assertNotEqual(one['branch'], two['branch'])
        self.assertEqual(one['base_commit'], two['base_commit'])
        self.assertEqual('task one\n', (left / 'app.txt').read_text())
        self.assertEqual(left, w.resume_workspace(self.project, one))
        self.assertEqual(str(self.project), w.metadata(right)['project_workspace'])

    def test_unrelated_project_cannot_resume_task(self):
        one = w.create(self.project, 'First')
        with self.assertRaises(ValueError):
            w.resume_workspace(self.root, one)

    def test_empty_repository_has_actionable_error_and_no_worktree(self):
        empty = self.root / 'empty'; empty.mkdir(); w.git(empty, 'init', '-q')
        with self.assertRaisesRegex(ValueError, 'initial Git commit'):
            w.create(empty, 'task')
        self.assertFalse((empty / '.autocode').exists())

    def test_project_snapshot_excludes_nested_task_edits(self):
        before = support.snapshot(self.project)
        one = w.create(self.project, 'one')
        (Path(one['workspace']) / 'app.txt').write_text('other task')
        self.assertEqual(before, support.snapshot(self.project))


class IsolatedCli(unittest.TestCase):
    def test_two_concurrent_cli_tasks_and_resume_from_original_project(self):
        flow = test_subprocess.SubprocessFlow(); flow.setUp()
        self.addCleanup(flow.doCleanups)
        env = {**flow.env, 'AUTOCODE_FIXTURE_MODE': 'no-human'}
        command = [*flow.entry, '--workspace', str(flow.project), '--engine', 'codex', '--no-chat']
        processes = [subprocess.Popen([*command, title], cwd=flow.root, env=env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                     for title in ('Build greeting one', 'Build greeting two')]
        for process in processes:
            out, err = process.communicate(timeout=35)
            self.assertEqual(2, process.returncode, out + err)
        runs = list(flow.project.glob('.autocode/worktrees/*/.autocode/runs/*/state.json'))
        self.assertEqual(2, len(runs))
        states = [json.loads(path.read_text()) for path in runs]
        self.assertEqual(2, len({s['workspace'] for s in states}))
        for path, state in zip(runs, states):
            self.assertEqual(str(flow.project), state['project_workspace'])
            result = subprocess.run([*flow.entry, '--workspace', str(flow.project), '--run-dir', str(path.parent), '--status'],
                                    cwd=flow.root, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertEqual(state['workspace'], json.loads(result.stdout)['workspace'])
        self.assertFalse((flow.project / 'greet.py').exists())


if __name__ == '__main__':
    unittest.main()
