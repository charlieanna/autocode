"""Destructive endpoints exercised only against disposable directories and real Git worktrees."""
import fcntl
import http.client
import json
import os
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

TOOLS = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(TOOLS / 'dashboard'), str(TOOLS)]
import autocode_registry as registry
from agent_console import Console, Handler, LoopbackHTTPServer
from autocode_workspaces import create as create_worktree
from autocode_worktrees import deliver
from dashboard_command_gate import WorkspaceCommandGate
from dashboard_delete import git


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

    def delivered(self):
        metadata = self.managed()
        (self.workspace / 'feature.py').write_text('print("delivered")\n')
        message = deliver({'status': 'TASK_COMPLETE', 'task': 'Disposable owned task',
                           'run_dir': str(self.run)}, self.workspace)
        self.assertIn('Delivered on branch', message)
        self.assertIn('detached', git(self.project, 'worktree', 'list', '--porcelain'))
        self.status['status'] = 'TASK_COMPLETE'
        self.save_status()
        return metadata

    def test_delivered_task_worktree_and_branch_can_be_deleted(self):
        metadata = self.delivered()
        self.assertTrue(self.preview()['can_include_worktree'])
        preview = self.preview(include_worktree=True, include_branch=True)
        receipt = self.delete(preview)
        self.assertEqual('deleted', receipt['status'], receipt)
        self.assertFalse(self.workspace.exists())
        self.assertNotIn(str(self.workspace), git(self.project, 'worktree', 'list', '--porcelain'))
        self.assertEqual('', git(self.project, 'for-each-ref', '--format=%(refname)', 'refs/heads/' + metadata['branch']))
        self.assertEqual('source belongs to the project', (self.project / 'keep.txt').read_text())
        self.assertTrue((self.project / '.autocode/runs/selected/output.txt').exists())

    def test_delivered_packed_branch_can_be_deleted_with_the_same_tip_guard(self):
        metadata = self.delivered()
        git(self.project, 'pack-refs', '--all')
        preview = self.preview(include_worktree=True, include_branch=True)
        receipt = self.delete(preview)
        self.assertEqual('deleted', receipt['status'], receipt)
        self.assertEqual('', git(self.project, 'for-each-ref', '--format=%(refname)', 'refs/heads/' + metadata['branch']))

    def test_delivered_branch_checked_out_elsewhere_is_not_exclusively_owned(self):
        metadata = self.delivered()
        other = self.root / 'retained-checkout'
        git(self.project, 'worktree', 'add', str(other), metadata['branch'])
        preview = self.preview()
        self.assertFalse(preview['can_include_worktree'])
        self.assertIn('shared', preview['worktree_unavailable_reason'])
        self.assertTrue((other / 'feature.py').exists())
        self.assertTrue(self.run.exists())

    def test_detached_head_change_invalidates_saved_deletion_scope(self):
        metadata = self.delivered()
        preview = self.preview(include_worktree=True, include_branch=True)
        head = git(self.project, 'rev-parse', 'refs/heads/' + metadata['branch'])
        self.assertNotEqual(head, git(self.workspace, 'rev-parse', 'HEAD'))
        git(self.workspace, 'update-ref', '--no-deref', 'HEAD', head)
        code, result = self.post('delete', {'preview_id': preview['preview_id'], 'confirmation': preview['confirmation']})
        self.assertEqual(400, code, result)
        self.assertIn('ownership changed', result['error'])
        self.assertTrue(self.run.exists())
        self.assertEqual(head, git(self.project, 'rev-parse', 'refs/heads/' + metadata['branch']))

    def test_missing_delivered_branch_has_readable_ownership_failure(self):
        metadata = self.delivered()
        git(self.project, 'branch', '-D', '--', metadata['branch'])
        preview = self.preview()
        self.assertFalse(preview['can_include_worktree'])
        self.assertIn('could not verify', preview['worktree_unavailable_reason'])
        self.assertNotIn('fatal:', preview['worktree_unavailable_reason'])
        self.assertTrue(self.run.exists())

    def test_detached_metadata_cannot_select_another_unclaimed_branch(self):
        metadata = self.delivered()
        other = 'autocode/unrelated-owned-work'
        head = git(self.project, 'rev-parse', 'refs/heads/' + metadata['branch'])
        git(self.project, 'branch', other, head)
        path = self.workspace / '.autocode/task-workspace.json'
        document = json.loads(path.read_text())
        document['branch'] = other
        path.write_text(json.dumps(document))
        preview = self.preview()
        self.assertFalse(preview['can_include_worktree'])
        self.assertIn('dedicated task branch', preview['worktree_unavailable_reason'])
        self.assertEqual(head, git(self.project, 'rev-parse', 'refs/heads/' + other))
        self.assertTrue(self.run.exists())

    def test_detach_without_successful_delivery_cannot_claim_managed_scope(self):
        self.managed()
        git(self.workspace, 'checkout', '-q', '--detach')
        self.status['status'] = 'TASK_COMPLETE'
        self.save_status()
        preview = self.preview()
        self.assertFalse(preview['can_include_worktree'])
        self.assertIn('no verified delivery', preview['worktree_unavailable_reason'])
        self.assertTrue(self.run.exists())

    def test_branch_claimed_at_removal_is_preserved_by_git_checkout_guard(self):
        metadata = self.delivered()
        preview = self.preview(include_worktree=True, include_branch=True)
        original, other = git, self.root / 'concurrent-checkout'
        def checkout_before_delete(root, *args):
            if args[2:4] == ('branch', '-D'):
                original(self.project, 'worktree', 'add', str(other), metadata['branch'])
            return original(root, *args)
        with patch('dashboard_delete.git', side_effect=checkout_before_delete):
            receipt = self.delete(preview)
        self.assertEqual('partial', receipt['status'], receipt)
        self.assertEqual(preview['managed']['branch_head'], git(other, 'rev-parse', 'HEAD'))
        self.assertEqual(preview['managed']['branch_head'], git(self.project, 'rev-parse', metadata['branch']))
        self.assertEqual('print("delivered")\n', (other / 'feature.py').read_text())

    def test_existing_reference_hook_refusal_is_preserved_without_config_changes(self):
        self.delivered()
        preview = self.preview(include_worktree=True, include_branch=True)
        hooks = self.root / 'custom-hooks'
        hooks.mkdir()
        hook = hooks / 'reference-transaction'
        hook.write_text('#!/bin/sh\nif [ "$1" = prepared ]; then echo "Repository policy refused" >&2; exit 1; fi\n')
        hook.chmod(0o700)
        git(self.project, 'config', 'core.hooksPath', str(hooks))
        before = hook.read_bytes()
        receipt = self.delete(preview)
        self.assertEqual('partial', receipt['status'], receipt)
        self.assertIn('Repository policy refused', ' '.join(receipt['errors']))
        self.assertEqual(str(hooks), git(self.project, 'config', 'core.hooksPath'))
        self.assertEqual(before, hook.read_bytes())
        self.assertEqual(preview['managed']['branch_head'], git(self.project, 'rev-parse', preview['managed']['branch']))

    def test_branch_advance_at_removal_is_preserved_by_expected_tip_delete(self):
        metadata = self.delivered()
        preview = self.preview(include_worktree=True, include_branch=True)
        original, advanced = git, []
        def advance_before_delete(root, *args):
            if args[2:4] == ('branch', '-D'):
                old = preview['managed']['branch_head']
                tree = original(self.project, 'rev-parse', old + '^{tree}')
                new = original(self.project, '-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                               'commit-tree', tree, '-p', old, '-m', 'New work after confirmation')
                original(self.project, 'update-ref', 'refs/heads/' + metadata['branch'], new, old)
                advanced.append(new)
            return original(root, *args)
        with patch('dashboard_delete.git', side_effect=advance_before_delete):
            receipt = self.delete(preview)
        self.assertEqual('partial', receipt['status'], receipt)
        self.assertFalse(self.workspace.exists(), 'Only the already confirmed worktree was removed')
        self.assertEqual(1, len(advanced))
        self.assertEqual(advanced[0], git(self.project, 'rev-parse', 'refs/heads/' + metadata['branch']))
        retry = self.delete(preview)
        self.assertEqual('partial', retry['status'])
        self.assertTrue(retry['retry_blocked'])
        self.assertEqual(advanced[0], git(self.project, 'rev-parse', 'refs/heads/' + metadata['branch']))

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

    def test_owned_status_poll_finishes_before_preview_or_deletion(self):
        # Hold a real status subprocess using a socket, not a timing delay.
        # The observer orders the competing HTTP request at the gate boundary.
        self.delivered()
        preview = self.preview(include_worktree=True, include_branch=True)
        original_runner = self.fake.read_text()
        listener = socket.socket()
        self.addCleanup(listener.close)
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        listener.settimeout(10)
        for operation in ('delete-preview', 'delete'):
            marker = self.root / 'hold-poll-once'
            marker.write_text('hold one owned reader')
            block = ("import os,socket\nmarker=Path(" + repr(str(marker)) + ")\n"
                     "if '--status' in sys.argv:\n"
                     " try:marker.unlink()\n except FileNotFoundError:pass\n else:\n"
                     "  with socket.create_connection(('127.0.0.1'," + str(listener.getsockname()[1]) + "),timeout=10) as held:\n"
                     "   held.sendall(b'ready')\n   assert held.recv(1)==b'x'\n")
            self.fake.write_text(original_runner.replace('from autocode_registry import cli\n',
                                                         'from autocode_registry import cli\n' + block))
            events, armed = queue.Queue(), threading.Event()
            class ObservedGate(WorkspaceCommandGate):
                @contextmanager
                def hold(gate, workspace):
                    if armed.is_set():
                        events.put('gate entered')
                    with super().hold(workspace):
                        yield
            self.console.workspace_commands = ObservedGate()
            self.console.status_cache.clear()
            def poll(workspace=None, run=None):
                connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=15)
                try:
                    connection.request('GET', '/api/run?' + urlencode({'workspace': str(workspace or self.workspace), 'run': str(run or self.run)}))
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    connection.close()
            data = ({'workspace': str(self.workspace), 'run': str(self.run), 'include_worktree': True, 'include_branch': True}
                    if operation == 'delete-preview' else
                    {'preview_id': preview['preview_id'], 'confirmation': preview['confirmation']})
            with ThreadPoolExecutor(2) as pool:
                read = pool.submit(poll)
                held, _ = listener.accept()
                with held:
                    self.assertEqual(b'ready', held.recv(5))
                    other = pool.submit(poll, self.project, self.project / '.autocode/runs/selected')
                    self.assertEqual(200, other.result(timeout=10)[0], 'An unrelated workspace can still be inspected')
                    armed.set()
                    mutation = pool.submit(self.post, operation, data)
                    mutation.add_done_callback(events.put)
                    try:
                        first = events.get(timeout=10)
                        if first != 'gate entered':
                            first = first.result()
                        self.assertEqual('gate entered', first, 'An owned dashboard read must not cause a worker refusal')
                        self.assertFalse(read.done(), 'The controlled reader is still active')
                        self.assertFalse(mutation.done(), 'Deletion waits for the active dashboard reader')
                    finally:
                        held.sendall(b'x')
                    self.assertEqual(200, read.result(timeout=15)[0])
                    code, result = mutation.result(timeout=15)
                self.assertEqual(202, code, result)
                if operation == 'delete-preview':
                    self.assertTrue(self.workspace.exists())
                    preview = result
                else:
                    self.assertEqual('deleted', result['status'])
                    self.assertFalse(self.workspace.exists())
                    self.assertTrue((self.project / 'keep.txt').exists())
                    self.assertTrue((self.project / '.autocode/runs/selected/output.txt').exists())

    def test_external_live_process_still_blocks_preview_and_deletion(self):
        preview = self.preview()
        process = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.buffer.read(1)', str(self.run)],
                                   stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for operation, data in (
                ('delete-preview', {'workspace': str(self.workspace), 'run': str(self.run)}),
                ('delete', {'preview_id': preview['preview_id'], 'confirmation': preview['confirmation']}),
            ):
                code, result = self.post(operation, data)
                self.assertEqual(400, code, result)
                self.assertIn('live process', result['error'])
                self.assertTrue(self.run.exists())
        finally:
            process.communicate(b'x', timeout=5)

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
            if args[2:4] == ('branch', '-D'):
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
            if args[2:4] == ('branch', '-D'):
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
            if args[2:4] == ('branch', '-D'):
                raise SimulatedCrash()
            return result
        with patch('dashboard_delete.git', side_effect=crash_after_branch):
            with self.assertRaises(SimulatedCrash):
                self.console.delete_permanently({'preview_id': preview['preview_id'], 'confirmation': preview['confirmation']})
        def no_second_delete(root, *args):
            self.assertNotEqual(('branch', '-D'), args[2:4])
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


class PackedRefTipGuardTests(unittest.TestCase):
    """#668: a packed-ref delete fires more than one prepared transaction."""

    def setUp(self):
        from dashboard_git_delete import _GUARD
        self.root = Path(tempfile.mkdtemp(prefix='branch-guard-'))
        self.addCleanup(shutil.rmtree, self.root, True)
        subprocess.run(['git', 'init', '-q'], cwd=self.root, check=True)
        for key, value in (('user.name', 'T'), ('user.email', 't@e.test')):
            subprocess.run(['git', 'config', key, value], cwd=self.root, check=True)
        (self.root / 'a.txt').write_text('x\n')
        subprocess.run(['git', 'add', 'a.txt'], cwd=self.root, check=True)
        subprocess.run(['git', 'commit', '-qm', 'init'], cwd=self.root, check=True)
        subprocess.run(['git', 'branch', 'feature'], cwd=self.root, check=True)
        subprocess.run(['git', 'pack-refs', '--all'], cwd=self.root, check=True)
        self.head = subprocess.run(['git', 'rev-parse', 'refs/heads/feature'], cwd=self.root,
                                   capture_output=True, text=True, check=True).stdout.strip()
        self.reference = 'refs/heads/feature'
        self.zero = '0' * len(self.head)
        hooks = self.root / 'hooks'
        hooks.mkdir()
        self.script = hooks / 'guard.py'
        self.script.write_text('reference = ' + repr(self.reference) + '\n'
                               + 'head = ' + repr(self.head) + '\n'
                               + 'original_hook = ' + repr('') + '\n' + _GUARD)

    def guard(self, stage, data):
        return subprocess.run([sys.executable, str(self.script), stage], input=data,
                              capture_output=True, text=True, cwd=self.root)

    def test_a_second_prepared_transaction_that_already_sees_the_ref_gone_is_accepted(self):
        # packed-refs update noise plus the target row; rev-parse finds no ref.
        noise = f'{self.zero} {self.zero} packed-refs\n{self.zero} {self.zero} {self.reference}\n'
        self.assertEqual(0, self.guard('prepared', noise).returncode)

    def test_the_first_prepared_transaction_still_binds_the_confirmed_tip(self):
        data = f'{self.head} {self.zero} {self.reference}\n'
        self.assertEqual(0, self.guard('prepared', data).returncode)
        moved = f'{self.zero} {self.zero} {self.reference}\n'
        # Ref is packed and still at head; a zero old-value deletion is accepted.
        self.assertEqual(0, self.guard('prepared', moved).returncode)

    def test_a_moved_tip_or_a_foreign_ref_is_still_refused(self):
        other = '1' * len(self.head)
        self.assertEqual(1, self.guard('prepared', f'{other} {self.zero} {self.reference}\n').returncode)
        self.assertEqual(1, self.guard('prepared', f'{self.head} {self.zero} refs/heads/other\n').returncode)


if __name__ == '__main__':
    unittest.main()
