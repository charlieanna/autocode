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

    def test_non_git_folder_bootstraps_a_safe_task_project(self):
        empty = self.root / 'new-project'; empty.mkdir()
        project = w.bootstrap(empty, 'Build calculator')
        self.assertEqual(empty.resolve(), project)
        self.assertTrue((project / '.git').is_dir())
        self.assertTrue(w.git(project, 'rev-parse', '--verify', 'HEAD'))

    def test_bootstrap_refuses_missing_paths_and_folders_inside_a_repository(self):
        missing = self.root / 'mistyped'
        with self.assertRaisesRegex(ValueError, 'does not exist'):
            w.bootstrap(missing, 'Build calculator')
        self.assertFalse(missing.exists())
        nested = self.project / 'src'; nested.mkdir()
        with self.assertRaisesRegex(ValueError, 'inside the Git repository'):
            w.bootstrap(nested, 'Build calculator')
        self.assertEqual([], list(nested.iterdir()))

    def test_populated_folder_gets_a_child_project_without_becoming_a_repository(self):
        populated = self.root / 'home-like'; populated.mkdir()
        (populated / 'keep.txt').write_text('do not version this folder')
        project = w.bootstrap(populated, 'Build calculator')
        self.assertTrue(project.is_relative_to(populated / 'autocode-projects'))
        self.assertTrue((project / '.git').is_dir())
        self.assertFalse((populated / '.git').exists())
        self.assertEqual('do not version this folder', (populated / 'keep.txt').read_text())

    def test_project_snapshot_excludes_nested_task_edits(self):
        before = support.snapshot(self.project)
        one = w.create(self.project, 'one')
        (Path(one['workspace']) / 'app.txt').write_text('other task')
        self.assertEqual(before, support.snapshot(self.project))


class IsolatedCli(unittest.TestCase):
    def test_non_git_workspace_bootstraps_a_task_project_without_initializing_the_parent(self):
        flow = test_subprocess.SubprocessFlow(); flow.setUp()
        self.addCleanup(flow.doCleanups)
        folder = flow.root / 'plain-folder'; folder.mkdir()
        (folder / 'keep.txt').write_text('preserve parent files')
        result = subprocess.run([*flow.entry, '--workspace', str(folder), '--engine', 'codex', '--no-chat',
                                 'Build calculator'], cwd=flow.root, env={**flow.env, 'AUTOCODE_FIXTURE_MODE': 'no-human'},
                                capture_output=True, text=True, timeout=35)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        projects = list((folder / 'autocode-projects').glob('build-calculator-*'))
        self.assertEqual(1, len(projects))
        self.assertTrue((projects[0] / '.git').is_dir())
        self.assertFalse((folder / '.git').exists())

    def test_read_only_commands_never_bootstrap_a_plain_folder(self):
        flow = test_subprocess.SubprocessFlow(); flow.setUp()
        self.addCleanup(flow.doCleanups)
        folder = flow.root / 'plain-folder'; folder.mkdir()
        (folder / 'keep.txt').write_text('preserve parent files')
        for flag in ('--dry-run', '--status'):
            result = subprocess.run([*flow.entry, '--workspace', str(folder), '--engine', 'codex', flag,
                                     'Build calculator'], cwd=flow.root, env=flow.env,
                                    capture_output=True, text=True, timeout=35)
            self.assertEqual(2, result.returncode, result.stdout + result.stderr)
            self.assertIn('not a Git repository', result.stderr)
        missing = flow.root / 'mistyped'
        result = subprocess.run([*flow.entry, '--workspace', str(missing), '--engine', 'codex', '--no-chat',
                                 'Build calculator'], cwd=flow.root, env=flow.env,
                                capture_output=True, text=True, timeout=35)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertEqual(['keep.txt'], [p.name for p in folder.iterdir()])
        self.assertFalse(missing.exists())

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
