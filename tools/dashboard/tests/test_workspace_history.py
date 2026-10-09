"""Missing-workspace presentation uses fresh evidence and never removes records."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard_workspace_history import annotate_workspace_history
from agent_console import Console, Handler, LoopbackHTTPServer


class WorkspaceHistoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / 'expired-test'
        self.row = {'workspace': str(self.workspace), 'run': str(self.workspace / '.autocode/runs/one'),
                    'error': 'workspace_missing', 'task': 'Retained test history'}

    def test_missing_rows_remain_and_return_to_active_when_workspace_returns(self):
        rows = [self.row]
        annotate_workspace_history(rows, process_reader=lambda: [])
        self.assertEqual([self.row], rows)
        self.assertTrue(rows[0]['workspace_cleanup']['eligible'])
        self.workspace.mkdir()
        important = self.workspace / 'important.txt'
        important.write_text('restored source')
        annotate_workspace_history(rows, process_reader=lambda: [])
        self.assertFalse(rows[0]['workspace_cleanup']['eligible'])
        self.assertEqual('workspace_exists', rows[0]['workspace_cleanup']['reason'])
        self.assertEqual('restored source', important.read_text())

    def test_existing_paused_and_incomplete_tasks_never_hide_from_cached_errors(self):
        self.workspace.mkdir()
        for status in ('PAUSED_INTERVENTION', 'RUNNING', 'WAITING_FOR_USER', 'TASK_COMPLETE'):
            self.row['status'] = status
            annotate_workspace_history([self.row], process_reader=lambda: [])
            self.assertFalse(self.row['workspace_cleanup']['eligible'])

    def test_missing_but_live_uncertain_or_uninspectable_work_remains_visible(self):
        for state in ('alive', 'unknown', 'uncertain'):
            self.row['monitor'] = {'live': {'state': state}}
            annotate_workspace_history([self.row], process_reader=lambda: [])
            self.assertFalse(self.row['workspace_cleanup']['eligible'])
        self.row.pop('monitor')
        for processes in (None, ['123 provider --dir ' + self.row['workspace']], ['234 runner --run-dir ' + self.row['run']]):
            annotate_workspace_history([self.row], process_reader=lambda: processes)
            self.assertFalse(self.row['workspace_cleanup']['eligible'])

    def test_inaccessible_paths_and_unrelated_missing_projects_remain_visible(self):
        with patch.object(Path, 'lstat', side_effect=PermissionError('no access')):
            annotate_workspace_history([self.row], process_reader=lambda: [])
        self.assertFalse(self.row['workspace_cleanup']['eligible'])
        row = {'workspace': '/Users/example/my-repository', 'error': 'workspace_missing'}
        annotate_workspace_history([row], process_reader=lambda: [])
        self.assertFalse(row['workspace_cleanup']['eligible'])

    def test_api_keeps_history_row_and_rechecks_on_each_refresh(self):
        console = Console([], str(self.root / 'unused-runner.py'), lambda: None,
                          conversation_root=self.root / 'dashboard/conversations')
        self.addCleanup(console.pool.shutdown, wait=True)
        self.addCleanup(lambda: console._conversation_store is not None and console._conversation_store.close())
        source = {'workspaces': [], 'runs': [self.row], 'conversations': []}
        server = LoopbackHTTPServer(('127.0.0.1', 0), Handler)
        server.console = console
        server.hosts = {'127.0.0.1:' + str(server.server_port)}
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        def read():
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            try:
                connection.request('GET', '/api/runs')
                response = connection.getresponse()
                self.assertEqual(200, response.status)
                return json.loads(response.read())
            finally:
                connection.close()
        with patch.object(console, '_build_dashboard_snapshot', return_value=source), \
                patch('dashboard_workspace_history.subprocess.run') as processes:
            processes.return_value.returncode = 0
            processes.return_value.stdout = ''
            first = read()
            self.assertEqual(1, len(first['runs']))
            self.assertTrue(first['runs'][0]['workspace_cleanup']['eligible'])
            self.workspace.mkdir()
            restored = read()
            self.assertEqual(1, len(restored['runs']))
            self.assertFalse(restored['runs'][0]['workspace_cleanup']['eligible'])


if __name__ == '__main__':
    unittest.main()
