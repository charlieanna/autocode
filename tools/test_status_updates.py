import copy
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from . import autocode_status as status, autocode_support as s, autocode_context as context
from . import autocode_process as processes


class StatusTests(unittest.TestCase):
    def setUp(self):
        self.state = {'task': 'fix things', 'workspace': '/tmp/task', 'status': 'RUNNING',
                      'iteration': 1, 'active_stage': {'stage': 'terra', 'started_at': 'now'},
                      'current_task': {'id': 't1', 'objective': 'Fix routing'}}

    def test_transition_heartbeat_restart_and_bounded_history(self):
        self.assertIn('Builder started: Fix routing', status.record(self.state, timestamp=100)['text'])
        self.assertIsNone(status.record(self.state, timestamp=159))
        status.record(self.state, timestamp=160)
        reloaded = copy.deepcopy(self.state)
        self.assertIsNone(status.record(reloaded, timestamp=170))
        for tick in range(220, 10000, 60):
            status.record(reloaded, timestamp=tick)
        self.assertEqual(2, len(reloaded['progress_messages']))
        for index in range(150):
            reloaded['iteration'] = index
            status.record(reloaded, timestamp=10000 + index)
        self.assertEqual(100, len(reloaded['progress_messages']))

    def test_immediate_blocker_and_completion_updates(self):
        status.record(self.state, timestamp=100)
        self.state.update(status='WAITING_FOR_USER', user_request={'question': 'Allow a new dependency?'})
        entry = status.record(self.state, timestamp=101)
        self.assertIn('Allow a new dependency?', entry['text'])
        self.assertIn('Next:', entry['text'])
        self.assertIsNone(status.record(self.state, timestamp=900))
        self.state.update(status='PAUSED_BUDGET', user_request=None, stop_reason='Milestone time exhausted')
        self.assertIn('Milestone time exhausted', status.record(self.state, timestamp=901)['text'])
        self.state['status'] = 'TASK_COMPLETE'
        self.assertIn('Task complete', status.record(self.state, timestamp=902)['text'])

    def test_report_repair_has_semantic_role(self):
        self.state['active_stage']['stage'] = 'terra_report_repair'
        entry = status.record(self.state, timestamp=0)
        self.assertEqual('Builder', entry['speaker'])
        self.assertIn('Repairing', entry['text'])

    def test_terminal_null_stage_and_parallel_progress(self):
        self.state.update(active_stage=None, next_stage=None, status='TASK_COMPLETE')
        self.assertIn('Task complete', status.record(self.state, timestamp=0)['text'])
        self.state.update(status='RUNNING', next_stage='orchestrator',
                          orchestration_batch={'status': 'BUILDING', 'workers': [{'milestone_id': 'M1', 'status': 'RUNNING'}]})
        self.assertIn('M1: RUNNING', status.record(self.state, timestamp=1)['text'])
        self.assertEqual('heartbeat', status.record(self.state, timestamp=61)['kind'])
        self.state['orchestration_batch']['workers'][0]['status'] = 'BUILT'
        self.assertEqual('transition', status.record(self.state, timestamp=62)['kind'])

    def test_persist_records_restart_safe_notifications(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            status.persist(path, self.state)
            loaded = s.read(path)
            self.assertEqual(self.state['progress_messages'], loaded['progress_messages'])
            self.assertIsNone(status.record(loaded, timestamp=loaded['progress_checkpoint']['at'] + 1))


class ContextTests(unittest.TestCase):
    def test_large_history_archived_without_losing_constraints(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            base = {'task': 'exact task', 'goal_contract': {'body': 'exact goal'},
                    'saved_answers': {'id': {'text': 'No dependency changes'}},
                    'permission_reuse_context': {'answer': 'deny'},
                    'unresolved_findings': [{'finding': 'fix this'}],
                    'current_task': {'objective': 'keep this'},
                    'source_snapshot': {'revision': 'a', 'head': 'b', 'files': {str(i): 'hash' for i in range(500)}},
                    'evidence_locations': ['evidence/' + str(i) for i in range(1000)]}
            original = copy.deepcopy(base)
            smaller, moved = context.compact(base, path)
            self.assertEqual(original, base)
            for key in ('task', 'goal_contract', 'saved_answers', 'permission_reuse_context', 'unresolved_findings', 'current_task'):
                self.assertEqual(base[key], smaller[key])
            artifact = Path(smaller['context_artifact']['path'])
            archived = s.read(artifact)
            self.assertEqual(base['evidence_locations'], archived['evidence_locations'])
            self.assertEqual(base['source_snapshot'], archived['source_snapshot'])
            self.assertEqual(s.file_hash(artifact), smaller['context_artifact']['sha256'])
            self.assertEqual(['evidence/997', 'evidence/998', 'evidence/999'], smaller['evidence_locations'])
            self.assertEqual(smaller, context.compact(base, path)[0])
            artifact.write_text('{}')
            with self.assertRaisesRegex(ValueError, 'artifact changed'):
                context.compact(base, path)

    def test_small_handoff_does_not_create_artifact(self):
        data = {'saved_answers': {'answer': 'no'}, 'evidence_locations': ['a']}
        self.assertEqual((data, []), context.compact(data, '/nonexistent/state.json'))


class StaleCheckpointTests(unittest.TestCase):
    """A checkpoint left by a dead runner must not read as current work."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        subprocess.run(['git', 'init', '-q', str(self.workspace)], check=True)
        self.run = self.workspace / '.autocode' / 'runs' / 'fixture'
        self.run.mkdir(parents=True)
        self.state_path = self.run / 'state.json'

    def write_state(self, worker):
        self.state_path.write_text(json.dumps({
            'version': 3, 'task': 'fixture', 'workspace': str(self.workspace),
            'status': 'RUNNING', 'iteration': 1, 'sessions': {}, 'stages': [],
            'next_stage': 'astra_challenge',
            'active_stage': {'stage': 'astra_challenge', 'iteration': 1, 'role': 'astra',
                             'started_at': 'now', 'pid': worker.get('pid'),
                             'processes': [worker] if worker.get('pid') else [],
                             'output': str(self.run / 'iterations' / '001' / 'astra_challenge-02.json'),
                             'events': str(self.run / 'iterations' / '001' / 'astra_challenge-02.jsonl')}}, indent=2))

    def run_status(self):
        return subprocess.run(
            [sys.executable, str(Path(__file__).with_name('autocode.py')),
             '--workspace', str(self.workspace), '--run-dir', str(self.run), '--status'],
            capture_output=True, text=True, check=False)

    def test_recorded_worker_state_distinguishes_live_dead_and_unknown(self):
        live = processes.identity(processes.process_table({os.getpid()})[os.getpid()])
        self.assertEqual({'checked': True, 'alive': True, 'live_pids': [os.getpid()]},
                         processes.recorded_worker_state({'processes': [live]}))
        dead = dict(live, pid=2 ** 22 - 1)
        self.assertEqual({'checked': True, 'alive': False, 'live_pids': []},
                         processes.recorded_worker_state({'processes': [dead]}))
        self.assertEqual({'checked': True, 'alive': False, 'live_pids': []},
                         processes.recorded_worker_state({'pid': 2 ** 22 - 1, 'exit_code': None}))
        self.assertEqual({'checked': True, 'alive': False, 'live_pids': []},
                         processes.recorded_worker_state({'pid': os.getpid(), 'exit_code': 0}))

    def test_status_labels_a_dead_provider_attempt_as_stale(self):
        self.write_state({'pid': 2 ** 22 - 1, 'started': 'now', 'group': 2 ** 22 - 1,
                          'birth_time': 0.0})
        before = self.state_path.read_bytes()
        result = self.run_status()
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual('RUNNING', payload['status'])
        self.assertTrue(payload['stale'])
        self.assertFalse(payload['active_stage_finished'])
        self.assertFalse(payload['active_stage_workers']['alive'])
        self.assertIn('--abandon-stage', payload['next_action'])
        self.assertIn('STALE CHECKPOINT', result.stderr)
        self.assertEqual(before, self.state_path.read_bytes())

    def test_status_reports_a_live_worker_as_current(self):
        live = processes.identity(processes.process_table({os.getpid()})[os.getpid()])
        self.write_state(live)
        result = self.run_status()
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertFalse(payload['stale'])
        self.assertIsNone(payload['next_action'])
        self.assertTrue(payload['active_stage_workers']['alive'])
        self.assertNotIn('STALE CHECKPOINT', result.stderr)
