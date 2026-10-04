"""Destructive endpoints exercised only against disposable directories and real Git worktrees."""
import fcntl
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(TOOLS / 'dashboard'), str(TOOLS)]
from agent_console import Console, Handler, LoopbackHTTPServer
import autocode_registry as registry
from dashboard_delete import git
from autocode_workspaces import create as create_worktree


class PermanentDeleteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed_temp = tempfile.TemporaryDirectory()
        cls.seed = Path(cls.seed_temp.name).resolve()
        git(cls.seed, 'init', '-q')
        # Keep the shared seed immutable while copytree walks its Git objects.
        git(cls.seed, 'config', 'maintenance.auto', 'false')
        git(cls.seed, 'config', 'gc.auto', '0')
        (cls.seed / 'keep.txt').write_text('source belongs to the project')
        git(cls.seed, 'add', 'keep.txt')
        git(cls.seed, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'fixture')

    @classmethod
    def tearDownClass(cls):
        cls.seed_temp.cleanup()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / 'project'
        shutil.copytree(self.seed, self.project)
        self.workspace = self.project
        self.run = self.workspace / '.autocode/runs/selected'
        self.run.mkdir(parents=True)
        self.environment = patch.dict(os.environ, {'AUTOCODE_HOME': str(self.root / 'registry')})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.fake = self.root / 'runner.py'
        self.fake.write_text('import json,sys\nfrom pathlib import Path\nsys.path.insert(0,' + repr(str(TOOLS)) + ')\n'
                             'from autocode_registry import cli\n'
                             'if sys.argv[1] == "registry": raise SystemExit(cli(sys.argv[2:]))\n'
                             'print(Path(__file__).with_name("status-fixture.json").read_text())\n')
        self.configure()

    def configure(self):
        (self.run / 'state.json').write_text(json.dumps({'workspace': str(self.workspace), 'task': 'Disposable test', 'status': 'PAUSED_INTERVENTION'}))
        (self.run / 'output.txt').write_text('selected output')
        registry.register_run(self.workspace, self.run, {'workspace': str(self.workspace)})
        self.status = {'workspace': str(self.workspace), 'run_dir': str(self.run), 'status': 'PAUSED_INTERVENTION',
                       'active_stage': None, 'view': {'evidence': {'outcome': 'Disposable test'}}}
        self.save_status()
        self.console = Console([self.workspace], self.fake, lambda: None,
                               conversation_root=self.root / 'dashboard/conversations')
        self.addCleanup(self.console.pool.shutdown, wait=True)
        self.server = LoopbackHTTPServer(('127.0.0.1', 0), Handler)
        self.server.console = self.console
        self.server.hosts = {'127.0.0.1:' + str(self.server.server_port)}
        worker = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        worker.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def save_status(self):
        (self.root / 'status-fixture.json').write_text(json.dumps(self.status))

    def post(self, endpoint, data):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=20)
        try:
            connection.request('POST', '/api/tasks/' + endpoint, json.dumps(data), {'Content-Type': 'application/json'})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def preview(self, **extra):
        code, result = self.post('delete-preview', {'workspace': str(self.workspace), 'run': str(self.run), **extra})
        self.assertEqual(202, code, result)
        return result

    def delete(self, preview):
        code, result = self.post('delete', {'preview_id': preview['preview_id'], 'confirmation': preview['confirmation']})
        self.assertEqual(202, code, result)
        return result

    def managed(self):
        # Preserve the shared project's unrelated run and create a separate managed task.
        metadata = create_worktree(self.project, 'Disposable owned task')
        self.workspace = Path(metadata['workspace'])
        self.run = self.workspace / '.autocode/runs/owned'
        self.run.mkdir(parents=True)
        self.configure()
        return metadata

    def test_scope_confirmation_deletes_only_selected_run_and_tidies_registry(self):
        sibling = self.run.with_name('unrelated')
        sibling.mkdir()
        (sibling / 'precious.txt').write_text('untouched')
        self.console.task_archive.remove(str(self.run))
        preview = self.preview()
        self.assertFalse(preview['can_include_worktree'])
        code, _ = self.post('delete', {'preview_id': preview['preview_id'], 'confirmation': 'yes'})
        self.assertEqual(400, code)
        self.assertTrue(self.run.exists())
        receipt = self.delete(preview)
        self.assertEqual('deleted', receipt['status'], receipt)
        self.assertFalse(self.run.exists())
        self.assertEqual('untouched', (sibling / 'precious.txt').read_text())
        self.assertTrue((self.project / 'keep.txt').exists())
        self.assertFalse(any(row['run_dir'] == str(self.run) for row in registry.listing()['runs']))
        self.assertFalse(self.console.archived_task(self.run))
        self.assertEqual(receipt, self.delete(preview))

    def test_managed_worktree_files_git_registration_and_branch_are_removed(self):
        metadata = self.managed()
        (self.workspace / 'disposable-test-output').write_text('owned test data')
        preview = self.preview(include_worktree=True, include_branch=True)
        receipt = self.delete(preview)
        self.assertEqual('deleted', receipt['status'], receipt)
        self.assertFalse(self.workspace.exists())
        self.assertNotIn(str(self.workspace), git(self.project, 'worktree', 'list', '--porcelain'))
        self.assertEqual('', git(self.project, 'for-each-ref', '--format=%(refname)', 'refs/heads/' + metadata['branch']))
        self.assertTrue((self.project / 'keep.txt').exists())
        self.assertTrue((self.project / '.autocode/runs/selected/output.txt').exists())

    def test_shared_worktree_scope_and_unrelated_run_are_rejected(self):
        code, _ = self.post('delete-preview', {'workspace': str(self.workspace), 'run': str(self.run), 'include_worktree': True})
        self.assertEqual(400, code)
        code, _ = self.post('delete-preview', {'workspace': str(self.workspace), 'run': str(self.root)})
        self.assertEqual(400, code)
        self.managed()
        (self.run.parent / 'other').mkdir()
        self.assertFalse(self.preview()['can_include_worktree'])

    def test_symlink_and_traversal_scope_are_rejected(self):
        alias = self.root / 'alias'
        alias.symlink_to(self.workspace, target_is_directory=True)
        for run, workspace in [(str(alias / '.autocode/runs/selected'), str(alias)),
                               (str(self.run.parent) + '/selected/../selected', str(self.workspace))]:
            code, _ = self.post('delete-preview', {'workspace': workspace, 'run': run})
            self.assertEqual(400, code)
        external = self.root / 'untouched'
        external.write_text('keep')
        (self.run / 'escape').symlink_to(external)
        code, _ = self.post('delete-preview', {'workspace': str(self.workspace), 'run': str(self.run)})
        self.assertEqual(400, code)
        self.assertEqual('keep', external.read_text())

    def test_live_uncertain_and_changed_status_reject_deletion(self):
        preview = self.preview()
        self.status['active_stage'] = {'pid': 123, 'stage': 'terra'}
        self.save_status()
        code, receipt = self.post('delete', {'preview_id': preview['preview_id'], 'confirmation': preview['confirmation']})
        self.assertEqual(400, code)
        self.assertTrue(self.run.exists())
        self.status.update(active_stage=None, active_stage_workers={'checked': False})
        self.save_status()
        code, _ = self.post('delete-preview', {'workspace': str(self.workspace), 'run': str(self.run)})
        self.assertEqual(400, code)

    def test_writer_lock_rejects_even_when_saved_status_is_stopped(self):
        handle = (self.run / 'writer.lock').open('a+')
        self.addCleanup(handle.close)
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        code, _ = self.post('delete-preview', {'workspace': str(self.workspace), 'run': str(self.run)})
        self.assertEqual(400, code)
        self.assertTrue(self.run.exists())

    def test_stale_preview_rejects_new_files(self):
        preview = self.preview()
        (self.run / 'new-evidence').write_text('new work')
        code, receipt = self.post('delete', {'preview_id': preview['preview_id'], 'confirmation': preview['confirmation']})
        self.assertEqual(400, code)
        self.assertIn('changed', receipt['error'])
        self.assertTrue((self.run / 'new-evidence').exists())

    def test_partial_file_failure_is_truthful_and_retryable(self):
        preview = self.preview()
        original = shutil.rmtree
        def partial(path, *args, **kwargs):
            if Path(path) == self.run:
                (self.run / 'output.txt').unlink()
                raise OSError('Injected permission failure')
            return original(path, *args, **kwargs)
        with patch('dashboard_delete.shutil.rmtree', side_effect=partial):
            receipt = self.delete(preview)
        self.assertEqual('partial', receipt['status'])
        self.assertTrue(receipt['remaining'])
        self.assertTrue(self.run.exists())
        self.assertEqual('deleted', self.delete(preview)['status'])

    def test_discovery_failure_after_file_removal_is_not_success_and_retry_reconciles(self):
        preview = self.preview()
        with patch.object(self.console, '_forget_deleted', side_effect=ValueError('discovery unavailable')):
            receipt = self.delete(preview)
        self.assertEqual('partial', receipt['status'])
        self.assertFalse(self.run.exists())
        self.assertEqual('deleted', self.delete(preview)['status'])

    def test_registry_forget_refuses_existing_run(self):
        with self.assertRaises(registry.RegistryError):
            registry.forget_deleted(self.workspace, self.run)
        self.assertTrue(self.run.exists())

    def test_old_confirmation_cannot_delete_recreated_path(self):
        preview = self.preview()
        self.assertEqual('deleted', self.delete(preview)['status'])
        self.run.mkdir()
        (self.run / 'new-user-file').write_text('new ownership')
        receipt = self.delete(preview)
        self.assertEqual('partial', receipt['status'])
        self.assertEqual('new ownership', (self.run / 'new-user-file').read_text())

    def test_branch_cleanup_failure_retains_truthful_receipt_and_retries(self):
        self.managed()
        preview = self.preview(include_worktree=True, include_branch=True)
        original = git
        def fail_branch(root, *args):
            if args[:2] == ('branch', '-D'):
                raise ValueError('Injected branch cleanup failure')
            return original(root, *args)
        with patch('dashboard_delete.git', side_effect=fail_branch):
            receipt = self.delete(preview)
        self.assertEqual('partial', receipt['status'])
        self.assertFalse(self.workspace.exists())
        self.assertEqual(['branch'], [item['kind'] for item in receipt['remaining']])
        self.assertEqual('deleted', self.delete(preview)['status'])

    def test_completed_receipt_preserves_recreated_branch(self):
        metadata = self.managed()
        preview = self.preview(include_worktree=True, include_branch=True)
        self.assertEqual('deleted', self.delete(preview)['status'])
        git(self.project, 'branch', metadata['branch'], preview['managed']['branch_head'])
        receipt = self.delete(preview)
        self.assertEqual('partial', receipt['status'], receipt)
        self.assertEqual(['branch'], [item['kind'] for item in receipt['remaining']])
        self.assertNotIn('branch', [item['kind'] for item in receipt['deleted']])
        self.assertIn('recreated', ' '.join(receipt['errors']).lower())
        self.assertEqual(preview['managed']['branch_head'], git(self.project, 'rev-parse', 'refs/heads/' + metadata['branch']))

    def test_registry_failure_records_branch_as_removed(self):
        self.managed()
        preview = self.preview(include_worktree=True, include_branch=True)
        with patch.object(self.console, '_forget_deleted', side_effect=ValueError('discovery unavailable')):
            receipt = self.delete(preview)
        self.assertEqual('partial', receipt['status'])
        self.assertEqual([], receipt['remaining'], 'Removed branch must not be reported as still present')
        self.assertEqual(preview['scope'], receipt['deleted'])
        self.assertEqual('deleted', self.delete(preview)['status'])

    def test_partial_receipt_never_deletes_recreated_branch(self):
        metadata = self.managed()
        preview = self.preview(include_worktree=True, include_branch=True)
        with patch.object(self.console, '_forget_deleted', side_effect=ValueError('discovery unavailable')):
            self.assertEqual('partial', self.delete(preview)['status'])
        git(self.project, 'branch', metadata['branch'], preview['managed']['branch_head'])
        receipt = self.delete(preview)
        self.assertEqual('partial', receipt['status'], 'Old confirmation must not delete recreated same-head branch')
        self.assertEqual(preview['managed']['branch_head'], git(self.project, 'rev-parse', 'refs/heads/' + metadata['branch']))
        self.assertEqual(['branch'], [item['kind'] for item in receipt['remaining']])
        self.assertNotIn('branch', [item['kind'] for item in receipt['deleted']])

    def test_partial_receipt_never_deletes_recreated_empty_run(self):
        preview = self.preview()
        with patch.object(self.console, '_forget_deleted', side_effect=ValueError('discovery unavailable')):
            self.assertEqual('partial', self.delete(preview)['status'])
        self.run.mkdir()
        receipt = self.delete(preview)
        self.assertEqual('partial', receipt['status'])
        self.assertTrue(self.run.is_dir())

    def test_lost_branch_receipt_preserves_recreated_branch(self):
        metadata = self.managed()
        preview = self.preview(include_worktree=True, include_branch=True)
        class SimulatedCrash(BaseException):
            pass
        original = git
        def crash_after_branch(root, *args):
            result = original(root, *args)
            if args[:2] == ('branch', '-D'):
                raise SimulatedCrash()
            return result
        with patch('dashboard_delete.git', side_effect=crash_after_branch):
            with self.assertRaises(SimulatedCrash):
                self.console.delete_permanently({'preview_id': preview['preview_id'], 'confirmation': preview['confirmation']})
        git(self.project, 'branch', metadata['branch'], preview['managed']['branch_head'])
        receipt = self.delete(preview)
        self.assertEqual('partial', receipt['status'])
        self.assertTrue(receipt['retry_blocked'])
        self.assertEqual(preview['managed']['branch_head'], git(self.project, 'rev-parse', 'refs/heads/' + metadata['branch']))
        self.assertEqual('partial', self.delete(preview)['status'])

    def test_lost_branch_receipt_reconciles_absence_without_second_delete(self):
        self.managed()
        preview = self.preview(include_worktree=True, include_branch=True)
        class SimulatedCrash(BaseException):
            pass
        original = git
        def crash_after_branch(root, *args):
            result = original(root, *args)
            if args[:2] == ('branch', '-D'):
                raise SimulatedCrash()
            return result
        with patch('dashboard_delete.git', side_effect=crash_after_branch):
            with self.assertRaises(SimulatedCrash):
                self.console.delete_permanently({'preview_id': preview['preview_id'], 'confirmation': preview['confirmation']})
        def no_second_delete(root, *args):
            self.assertNotEqual(('branch', '-D'), args[:2])
            return original(root, *args)
        with patch('dashboard_delete.git', side_effect=no_second_delete):
            self.assertEqual('deleted', self.delete(preview)['status'])

    def test_completed_receipt_cannot_claim_success_when_git_is_unreadable(self):
        self.managed()
        preview = self.preview(include_worktree=True, include_branch=True)
        self.assertEqual('deleted', self.delete(preview)['status'])
        with patch('dashboard_delete.git', side_effect=ValueError('Git repository unavailable')):
            receipt = self.delete(preview)
        self.assertEqual('partial', receipt['status'])
        self.assertIn('inspected', ' '.join(receipt['errors']))


if __name__ == '__main__':
    unittest.main()
