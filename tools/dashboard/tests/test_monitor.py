import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import dashboard_monitor as monitor


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run = Path(self.tmp.name) / 'run'
        self.run.mkdir()
        (self.run / 'state.json').write_text('{}')
        self.active = {'stage': 'terra', 'pid': 123, 'role': 'terra', 'processes': [{'pid': 123, 'started': 'saved start'}]}
        self.worker = {'state': 'S', 'started': 'saved start', 'elapsed': '01:02:03', 'command': 'private args'}

    def test_live_identity_and_pid_reuse(self):
        self.assertEqual(monitor.worker_status(self.active, self.run, {123: self.worker})['state'], 'alive')
        self.assertEqual(monitor.worker_status(self.active, self.run, {123: {**self.worker, 'started': 'new start'}})['state'], 'unknown')
        self.assertEqual(monitor.worker_status(self.active, self.run, {})['state'], 'exited')
        self.assertEqual(monitor.worker_status(self.active, self.run, {123: {**self.worker, 'state': 'Z'}})['state'], 'exited')
        self.assertEqual(monitor.worker_status(self.active, self.run, None)['state'], 'unknown')

    def test_runner_resolver_history_does_not_imply_live_worker(self):
        state = {'status': 'PAUSED_RESOLVER', 'next_stage': 'terra',
                 'stages': [{'stage': 'resolver', 'role': 'resolver', 'runner_owned': True,
                             'finished_at': 'yesterday', 'exit_code': 0}]}
        with patch.object(monitor, 'process_table') as probe:
            result = monitor.snapshot(state, self.run, detailed=True)
        probe.assert_not_called()
        self.assertEqual('none', result['live']['state'])
        self.assertTrue(result['history'][0]['runner_owned'])
        self.assertEqual('resolver', result['history'][0]['stage'])

    def test_fresh_checkpoint_and_running_log_do_not_override_exited_worker(self):
        log = self.run / 'events.jsonl'
        log.write_text(json.dumps({'type': 'item.started', 'item': {
            'id': '1', 'type': 'command_execution', 'status': 'running'}}))
        state = {'status': 'RUNNING', 'active_stage': {**self.active, 'events': str(log)}}
        with patch.object(monitor, 'process_table', return_value={}):
            result = monitor.snapshot(state, self.run, detailed=True)
        self.assertEqual(result['live']['state'], 'exited')
        self.assertIsNotNone(result['checkpoint_updated'])
        self.assertIsNotNone(result['log_updated'])
        self.assertEqual(result['activity'][0]['status'], 'running')

    def test_legacy_requires_provider_and_exact_run_path(self):
        active = {'pid': 123}
        def check(command):
            return monitor.worker_status(active, self.run, {123: {**self.worker, 'command': command}})['state']
        self.assertEqual(check('codex exec --output ' + str(self.run / 'out.json')), 'alive')
        self.assertEqual(check('codex exec --output ' + str(self.run) + '-other/out.json'), 'unknown')
        self.assertEqual(check('python monitor.py ' + str(self.run)), 'unknown')

    def test_log_boundary_and_redaction(self):
        log = self.run / 'events.jsonl'
        log.write_text('\n'.join(json.dumps(e) for e in [
            {'type': 'item.completed', 'item': {'id': '1', 'type': 'reasoning', 'text': 'private reasoning'}},
            {'type': 'item.completed', 'item': {'id': '2', 'type': 'command_execution', 'command': 'secret command', 'aggregated_output': 'secret output', 'status': 'completed', 'exit_code': 0}},
            {'type': 'tool_use', 'part': {'id': '3', 'tool': 'edit', 'state': {'status': 'completed', 'input': {'filePath': '/repo/app.py', 'newString': 'private patch'}}}}
        ]) + '\n{unfinished')
        result = monitor.activity_log(log)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['label'], 'Edit: app.py')
        self.assertNotIn('secret', json.dumps(result))
        self.assertNotIn('private', json.dumps(result))
        outside = self.run.parent / 'outside'; outside.write_text('private')
        (self.run / 'escape').symlink_to(outside)
        self.assertIsNone(monitor.local_file(self.run, str(outside)))
        self.assertIsNone(monitor.local_file(self.run, 'escape'))

    def test_finished_stage_never_reports_active_worker(self):
        state = {'active_stage': {**self.active, 'finished_at': 'yesterday'}, 'settings': {'workflow': {'mode': 'glm_final_audit_v2'}, 'roles': {'terra': {'model': 'glm-5.3', 'api_key': 'secret'}}}}
        with patch.object(monitor, 'process_table', return_value={123: self.worker}) as probe:
            result = monitor.snapshot(state, self.run)
        probe.assert_not_called()
        self.assertEqual(result['live']['state'], 'none')
        self.assertEqual(result['workflow_mode'], 'glm_final_audit_v2')
        self.assertNotIn('secret', json.dumps(result))

    def test_completion_route_is_projected_as_the_active_owner(self):
        state = {'active_stage': {**self.active, 'stage': 'astra_review', 'role': 'astra',
                                  'route_role': 'completion'},
                 'settings': {'roles': {'completion': {'model': 'openai/gpt-5.6-sol',
                                                       'reasoning_effort': 'medium'}}}}
        with patch.object(monitor, 'process_table', return_value={123: self.worker}):
            result = monitor.snapshot(state, self.run)
        self.assertEqual('completion', result['active_role'])
        self.assertEqual({'model': 'openai/gpt-5.6-sol', 'reasoning_effort': 'medium'},
                         result['roles']['completion'])

    def test_process_table_parser_and_cache(self):
        result = type('Result', (), {'returncode': 0, 'stdout': '123 12 Sun Sep 20 01:00:00 2026 02:00 S codex exec work\n'})()
        with patch.object(monitor, '_checked', 0), patch.object(monitor.subprocess, 'run', return_value=result) as command:
            table = monitor.process_table(); monitor.process_table()
        self.assertEqual(table[123]['started'], 'Sun Sep 20 01:00:00 2026')
        command.assert_called_once()

    def test_orchestration_projects_saved_workers_without_claiming_liveness(self):
        batch = {'id': 'batch-1', 'status': 'BUILDING', 'baseline': {'private': 'source'},
                 'workers': [{'milestone_id': 'M1', 'status': 'RUNNING', 'workspace': '/repo/worker-1',
                              'run_dir': '/repo/run/worker-1', 'processes': [{'pid': 123}],
                              'task': {'private': 'prompt'}}]}
        state = {'settings': {'orchestration': {'enabled': True, 'max_parallel': 2}},
                 'status': 'RUNNING', 'next_stage': 'orchestrator', 'orchestration_batch': batch,
                 'orchestration_history': [{**batch, 'status': 'INTEGRATED'}],
                 'stages': [{'stage': 'orchestrator', 'runner_owned': True, 'finished_at': 'yesterday'}]}
        with patch.object(monitor, 'process_table') as probe:
            result = monitor.snapshot(state, self.run, detailed=True)
        probe.assert_not_called()
        self.assertEqual(result['live']['state'], 'none')
        self.assertEqual(result['next_stage'], 'orchestrator')
        self.assertEqual(result['orchestration'], {'enabled': True, 'max_parallel': 2})
        self.assertEqual(result['orchestration_batch'], {
            'id': 'batch-1', 'status': 'BUILDING', 'workers': [
                {'milestone_id': 'M1', 'status': 'RUNNING', 'workspace': '/repo/worker-1',
                 'run_dir': '/repo/run/worker-1'}]})
        self.assertNotIn('private', json.dumps(result))
        self.assertEqual(result['orchestration_history'][0]['status'], 'INTEGRATED')
        self.assertTrue(result['history'][0]['runner_owned'])
        self.assertNotIn('orchestration_history', monitor.snapshot(state, self.run))
        state.pop('orchestration_batch')
        result = monitor.snapshot(state, self.run, detailed=True)
        self.assertIsNone(result['orchestration_batch'])
        self.assertEqual(len(result['orchestration_history']), 1)

    def test_legacy_and_malformed_orchestration_are_safe_to_display(self):
        for state in ({}, {'settings': {'orchestration': None}, 'orchestration_batch': [],
                          'orchestration_history': [None, {}, 'invalid']}):
            result = monitor.snapshot(state, self.run, detailed=True)
            self.assertEqual(result['orchestration'], {})
            self.assertIsNone(result['orchestration_batch'])
            self.assertEqual(result['orchestration_history'], [])
        self.assertEqual(monitor.batch_summary({'id': 'empty', 'workers': [None, {'status': 123}]}),
                         {'id': 'empty', 'workers': [{}]})


if __name__ == '__main__':
    unittest.main()
