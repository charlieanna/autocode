import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console import Console


FAKE = r'''
import json,sys,time
from pathlib import Path
root=Path(__file__).parent
args=sys.argv[1:]
def read(name): return json.loads((root/name).read_text())
if args[:2]==['registry','location']:
 print(json.dumps({'registry_version':1,'operation':'location','registry_path':str(root/'registry.json')}))
elif args[:2]==['registry','list']:
 print(json.dumps(read('registry.json')))
elif '--status' in args:
 print(json.dumps(read('status.json')))
elif args[:2]==['intervention','inspect']:
 requests=read('inbox.json');print(json.dumps({'version':1,'operation':'inspect','requests':requests,'pending_count':len(requests)}))
elif args[:2]==['intervention','submit']:
 requests=read('inbox.json');ident=args[args.index('--request-id')+1];kind=args[args.index('--kind')+1];text=args[args.index('--text')+1] if '--text' in args else ''
 prior=next((r for r in requests if r['id']==ident),None)
 if prior and (prior['kind']!=kind or prior['text']!=text):
  print(json.dumps({'error':{'code':'request_id_conflict','message':'ID already has another payload'}}));raise SystemExit(2)
 receipt=prior or {'id':ident,'kind':kind,'text':text,'order':len(requests)+1,'submitted_at':'2026-09-20T00:00:00Z','observed_goal_token':'r1:fixture','boundary_pause_requested':True}
 if not prior:requests.append(receipt);(root/'inbox.json').write_text(json.dumps(requests))
 print(json.dumps({'version':1,'operation':'submit','accepted':True,'consumer':'pending_only','idempotent':bool(prior),'receipt':receipt}))
else:
 while (root/'hold-command').exists():time.sleep(.02)
 print('recorded')
'''


class RegistryInterventionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / 'project'
        (self.workspace / '.git').mkdir(parents=True)
        self.run = self.workspace / '.autocode/runs/fixture'
        self.run.mkdir(parents=True)
        self.state = {'version': 3, 'workspace': str(self.workspace), 'task': 'fixture', 'status': 'RUNNING',
                      'intervention_capability': {'supported': True, 'version': 1}}
        self.write_state()
        self.fake = self.root / 'runner.py'
        self.fake.write_text(FAKE)
        self.registry = {'operation': 'list', 'registry_version': 1,
                         'workspaces': [{'workspace': str(self.workspace)}],
                         'runs': [{'workspace': str(self.workspace), 'run_dir': str(self.run), 'availability': 'available'}]}
        self.status = {'interventions': {'inspector_capability': {'supported': True, 'version': 1},
                                        'runner_capability': {'supported': True, 'version': 1},
                                        'pending_ids': [], 'applied_receipts': [], 'blocked_conditions': []}}
        self.save('registry.json', self.registry)
        self.save('status.json', self.status)
        self.save('inbox.json', [])
        self.console = self.console_new()
        self.isolation = patch.dict(os.environ, {'AUTOCODE_HOME': str(self.root / 'home')})
        self.isolation.start()
        self.addCleanup(self.isolation.stop)

    def save(self, name, value):
        (self.root / name).write_text(json.dumps(value))

    def write_state(self):
        (self.run / 'state.json').write_text(json.dumps(self.state))

    def console_new(self, workspaces=(), **kwargs):
        console = Console(workspaces, self.fake, lambda: None, **kwargs)
        self.addCleanup(console.pool.shutdown, wait=True)
        return console

    def action(self, kind, **fields):
        return self.console.mutate({'workspace': str(self.workspace), 'run': str(self.run), 'action': kind, **fields})

    def test_registry_only_discovery_dedupes_and_rejects_unregistered_run(self):
        self.assertEqual([self.workspace], self.console.workspaces)
        self.assertEqual([str(self.run)], [r['run'] for r in self.console.discover()])
        combined = self.console_new([self.workspace], watch_roots=[self.root])
        self.assertEqual(1, len([r for r in combined.discover() if r.get('run') == str(self.run)]))
        arbitrary = self.run.with_name('unregistered')
        arbitrary.mkdir()
        (arbitrary / 'state.json').write_text(json.dumps(self.state))
        self.assertIsNone(self.console.run_for(self.workspace, str(arbitrary)))
        self.assertEqual([str(self.run)], [r['run'] for r in self.console.discover()])

    def test_registry_errors_preserve_other_sources_and_show_unavailable_entries(self):
        self.registry['runs'][0]['availability'] = 'checkpoint_missing'
        self.save('registry.json', self.registry)
        rows = self.console.discover()
        self.assertIn('checkpoint_missing', rows[0]['error'])
        self.registry['registry_version'] = 900
        self.save('registry.json', self.registry)
        compatible = self.console_new([self.workspace])
        self.assertIn('Unsupported', compatible.registry_status()['error'])
        self.assertEqual([str(self.run)], [r['run'] for r in compatible.discover()])

    def test_both_capabilities_are_required_and_unknown_version_cannot_write_marker(self):
        self.status['interventions']['inspector_capability']['version'] = 2
        self.save('status.json', self.status)
        with self.assertRaisesRegex(ValueError, 'Unsupported'):
            self.action('pause', request_id='pause')
        self.assertFalse((self.run / 'pause-requested').exists())
        self.assertEqual([], json.loads((self.root / 'inbox.json').read_text()))

    def test_live_submission_bypasses_busy_workspace_and_fully_occupied_pool(self):
        barrier = threading.Event()
        self.addCleanup(barrier.set)
        for _ in range(4):
            self.console.pool.submit(barrier.wait)
        self.console.workspace_busy.add(str(self.workspace))
        receipt = self.action('feedback', text='keep exact history', request_id='stable')
        self.assertEqual('queued', receipt['status'])
        self.assertTrue(receipt['durable'])
        self.assertFalse(barrier.is_set())
        retry = self.action('feedback', text='keep exact history', request_id='stable')
        self.assertTrue(retry['idempotent'])
        self.assertEqual(1, len(json.loads((self.root / 'inbox.json').read_text())))
        conflict = self.action('feedback', text='different payload', request_id='stable')
        self.assertEqual('failed', conflict['status'])
        self.assertIn('another payload', conflict['error'])
        barrier.set()

    def test_pending_text_and_applied_resumed_states_survive_new_console(self):
        receipt = self.action('feedback', text='durable words', request_id='restore')
        restored = self.console_new().view(self.workspace, self.run)['interventions']['entries']
        self.assertEqual([('restore', 'queued', 'durable words')], [(r['id'], r['status'], r['text']) for r in restored])
        self.save('inbox.json', [])
        applied = {k: v for k, v in receipt.items() if k not in ('status', 'durable', 'idempotent')}
        applied['applied_at'] = 'now'
        self.status['interventions']['applied_receipts'] = [applied]
        self.save('status.json', self.status)
        self.assertEqual('applied', self.console_new().view(self.workspace, self.run)['interventions']['entries'][0]['status'])
        applied['resumed_at'] = 'later'
        self.save('status.json', self.status)
        self.assertEqual('resumed', self.console_new().view(self.workspace, self.run)['interventions']['entries'][0]['status'])

    def test_uncertain_submit_is_visible_and_not_automatically_retried(self):
        self.console._registered()
        self.console._intervention_view(self.workspace, self.run)
        with patch.object(self.console, '_json_command', return_value=(None, {'message': 'timeout', 'uncertain': True})) as invoke:
            result = self.action('feedback', text='retain me', request_id='uncertain')
            self.assertEqual('uncertain', result['status'])
            self.assertFalse(result['durable'])
            self.assertEqual(1, invoke.call_count)

    def test_capable_continue_uses_explicit_resume_arguments(self):
        action = self.action('continue')
        self.assertEqual(['--no-chat', '--resume-paused'], action['command'][-2:])

    def interrupted(self):
        self.state.update(status='PAUSED_PROVIDER_UNCERTAIN', stop_reason='Inspect the interrupted provider attempt.')
        self.write_state()
        self.status.update(status=self.state['status'], attempt_id='023/terra-02')
        self.save('status.json', self.status)

    def test_interrupted_stage_is_visible_and_continue_requires_recovery(self):
        self.interrupted()
        view = self.console.view(self.workspace, self.run)
        self.assertEqual(self.state['stop_reason'], view['stop_reason'])
        self.assertEqual('023/terra-02', view['interventions']['attempt_id'])
        with self.assertRaisesRegex(ValueError, 'Recover saved work'):
            self.action('continue')
        self.assertEqual([], self.console.action_log(self.workspace, self.run))

    def test_recover_requires_fresh_exact_attempt_and_never_launches_resume(self):
        self.interrupted()
        for attempt in ('', '023/wrong-01', None):
            with self.assertRaisesRegex(ValueError, 'attempt changed'):
                self.action('recover_stage', attempt_id=attempt)
        before = (self.run / 'state.json').read_bytes()
        action = self.action('recover_stage', attempt_id='023/terra-02')
        self.assertEqual(['--no-chat', '--abandon-stage', '023/terra-02'], action['command'][-3:])
        self.assertNotIn('--resume-paused', action['command'])
        self.assertEqual(before, (self.run / 'state.json').read_bytes())

    def test_recovery_rejects_changed_status_and_busy_workspace(self):
        self.interrupted()
        self.console.view(self.workspace, self.run)
        self.status['status'] = 'RUNNING'
        self.save('status.json', self.status)
        with self.assertRaisesRegex(ValueError, 'no longer waiting'):
            self.action('recover_stage', attempt_id='023/terra-02')
        self.status['status'] = 'PAUSED_PROVIDER_UNCERTAIN'
        self.save('status.json', self.status)
        self.console.workspace_busy.add(str(self.workspace))
        with self.assertRaisesRegex(ValueError, 'already queued or running'):
            self.action('recover_stage', attempt_id='023/terra-02')
        self.assertEqual([], self.console.action_log(self.workspace, self.run))

    def legacy(self):
        self.status['interventions']['runner_capability'] = {'supported': False, 'reason': 'old'}
        self.save('status.json', self.status)

    def test_legacy_dangling_marker_is_rejected_for_pause_and_continue(self):
        self.legacy()
        (self.run / 'pause-requested').symlink_to(self.root / 'missing')
        for kind in ('pause', 'continue'):
            with self.assertRaisesRegex(ValueError, 'Pause marker|pause request'):
                self.action(kind)
        self.assertEqual([], self.console.action_log(self.workspace, self.run))
        self.assertFalse(self.console.workspace_busy)

    def test_legacy_failed_clear_prevents_launch_and_releases_reservation(self):
        self.legacy()
        self.action('pause')
        with patch.object(Path, 'unlink', side_effect=PermissionError('fixture denied')):
            with self.assertRaisesRegex(ValueError, 'Continue was not launched'):
                self.action('continue')
        self.assertEqual([], self.console.action_log(self.workspace, self.run))
        self.assertFalse(self.console.workspace_busy)

    def test_legacy_feedback_is_not_delivered_before_command_exits(self):
        self.legacy()
        hold = self.root / 'hold-command'
        hold.touch()
        self.addCleanup(hold.unlink, missing_ok=True)
        try:
            receipt = self.action('feedback', text='checkpoint only')
            self.assertEqual('queued', receipt['status'])
            self.assertEqual(['--feedback', 'checkpoint only'], self.console.action_log(self.workspace, self.run)[0]['command'][-2:])
            self.console.status_cache.clear()
            status = self.console.view(self.workspace, self.run)['interventions']['entries'][0]['status']
            self.assertIn(status, ('queued', 'running'))
            with self.assertRaises(ValueError):
                self.action('feedback', text='cannot queue another ordinary action')
        finally:
            hold.unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()
