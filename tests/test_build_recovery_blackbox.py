"""Additional execution-boundary scenarios with real externally crashed processes."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import unittest

from . import test_build_blackbox as bb


class RecoveryBlackbox(unittest.TestCase):
    setUp = bb.BuildBlackbox.setUp
    command = bb.BuildBlackbox.command
    invoke = bb.BuildBlackbox.invoke
    seed = bb.BuildBlackbox.seed
    state = bb.BuildBlackbox.state
    events = bb.BuildBlackbox.events
    build = bb.BuildBlackbox.build
    candidate = bb.BuildBlackbox.candidate

    def fault(self, kind):
        hooks = self.root / 'hooks'
        hooks.mkdir()
        shutil.copy2(self.source / 'build_audit_fault_hooks.py', hooks / 'sitecustomize.py')
        self.env.update(PYTHONPATH=str(hooks), BUILD_AUDIT_CONTROLLER_FAULT=kind,
                        BUILD_AUDIT_FAULT_ROOT=str(self.root))

    def await_results(self, count):
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            found = list(self.project.glob('.autocode/builders/*/*/.autocode/runs/*/result.json'))
            if len(found) >= count:
                return found
            time.sleep(.05)
        self.fail(f'Only {len(found)} worker results appeared; expected {count}')

    def test_08_live_orphan_worker_blocks_second_writer(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT'] = 'hold'; self.fault('after_worker')
        self.invoke('autocode_build', ['--run-dir', str(self.run), '--no-chat'], 97)
        try:
            deadline = time.monotonic() + 20
            while not self.events() and time.monotonic() < deadline:
                time.sleep(.05)
            self.assertEqual(1, len(self.events()))
            self.build(2, extra=['--resume-paused', '--retry-builder', 'M1'])
            self.assertEqual(1, len(self.events()), 'Duplicate writer was launched')
        finally:
            (self.root / 'release').touch()
            self.await_results(1)

    def test_08_actual_timeout_then_controller_crash_keeps_live_writer_exclusive(self):
        spec = bb.plan([([], 'Create output.txt containing done', ['output.txt'])],
                       {'M1': {'output.txt': 'done'}},
                       {'M1': "from pathlib import Path; assert Path('output.txt').read_text()=='done'"},
                       'Verify timeout is not proof of termination')
        self.seed(spec); self.env['BUILD_AUDIT_FAULT'] = 'hold_timeout'
        controller = subprocess.Popen(self.command('autocode_build', ['--run-dir',str(self.run),
            '--no-chat','--max-stage-seconds','1']), cwd=self.root, env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        provider_pid = None
        try:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                saved = self.state()
                active = saved.get('active_stage') or {}
                if active.get('activity', {}).get('timeout_kind') == 'stage':
                    provider_pid = active['pid']
                    break
                time.sleep(.005)
            self.assertIsNotNone(provider_pid, 'Real watchdog did not publish timeout')
            controller.kill()  # Simulate controller crash during timeout cleanup, not provider death.
            stdout, stderr = controller.communicate(timeout=5)
            (self.root/'timeout-controller.log').write_text(stdout+stderr)
            os.kill(provider_pid, signal.SIGCONT)
            os.kill(provider_pid, 0)
            self.assertEqual(1,len(self.events()))
            self.build(2,extra=['--resume-paused'])
            os.kill(provider_pid, 0)
            self.assertEqual(1,len(self.events()), 'A second writer started while timed-out provider remained alive')
            self.assertNotIn('autocode',self.state().get('unit_handoffs',{}))
        finally:
            if controller.poll() is None:
                controller.kill(); controller.communicate(timeout=5)
            if provider_pid:
                try: os.kill(provider_pid,signal.SIGCONT)
                except ProcessLookupError: pass
            (self.root/'release').touch()
            deadline=time.monotonic()+10
            while provider_pid and time.monotonic()<deadline:
                try: os.kill(provider_pid,0)
                except ProcessLookupError: break
                time.sleep(.05)
            else:
                if provider_pid:
                    try: os.kill(provider_pid,signal.SIGKILL)
                    except ProcessLookupError: pass

    def test_10_crash_before_spawn_recovery_launches_once(self):
        self.seed(); self.fault('before_worker')
        self.invoke('autocode_build', ['--run-dir', str(self.run), '--no-chat'], 97)
        self.assertEqual([], self.events())
        self.build(extra=['--resume-paused'])
        self.candidate()
        self.assertEqual(3, len(self.events()))

    def test_11_crash_after_spawn_before_pid_checkpoint(self):
        self.seed(); self.fault('after_worker')
        self.invoke('autocode_build', ['--run-dir', str(self.run), '--no-chat'], 97)
        self.await_results(1)
        self.build(extra=['--resume-paused']); self.candidate()
        self.assertEqual(3, len(self.events()))
        self.assertEqual(1, sum(e['milestone'] == 'M1' for e in self.events()))

    def test_12_crash_after_patch_application_is_not_reapplied(self):
        self.seed(); self.fault('after_integration')
        self.invoke('autocode_build', ['--run-dir', str(self.run), '--no-chat'], 97)
        before = {p: (self.project / p).read_bytes() for paths in self.spec['payloads'].values() for p in paths}
        self.build(extra=['--resume-paused']); self.candidate()
        self.assertEqual(3, len(self.events()))
        self.assertEqual(before, {p: (self.project / p).read_bytes() for p in before})

    def test_14_conflict_during_integration_preserves_both_sides(self):
        self.seed(); self.fault('integration_conflict'); self.build(2)
        self.assertEqual('USER_CONFLICT = True\n', (self.project / 'server/health.py').read_text())
        self.assertEqual(3, len(self.events()))
        self.assertTrue(list(self.project.glob('.autocode/builders/*/*/server/health.py')))
        self.assertNotIn('autocode', self.state()['unit_handoffs'])

    def test_17_behavioral_drift_five_retries_fails_three_retry_contract(self):
        spec = bb.plan([([], 'Set MAX_RETRIES to exactly 3', ['retry.py'])],
                       {'M1': {'retry.py': 'MAX_RETRIES = 5\n'}},
                       {'M1': 'from retry import MAX_RETRIES; assert MAX_RETRIES == 3'},
                       'Configure a strict maximum of three retries')
        self.seed(spec); self.build(); self.candidate()
        self.invoke('autoreview', ['--run-dir', str(self.run), '--no-chat'])
        self.assertEqual('FAIL', self.state()['validation']['verdict'])
        self.assertNotEqual('TASK_COMPLETE', self.state()['status'])
        self.assertEqual(self.original_contract, self.state()['goal_contract'])

    def test_30_duplicate_durable_result_consumed_once_after_crash(self):
        self.seed(); self.fault('after_integration')
        self.invoke('autocode_build', ['--run-dir', str(self.run), '--no-chat'], 97)
        for p in self.project.glob('.autocode/builders/*/*/.autocode/runs/*/result.json'):
            p.write_bytes(p.read_bytes())  # Same delivery repeated; no edited identities.
        self.build(extra=['--resume-paused']); self.candidate()
        first = self.state()['orchestration_history']
        self.build()
        self.assertEqual(first, self.state()['orchestration_history'])
        attempts = [r['worker_attempt'] for r in self.state()['stages'] if r.get('worker_attempt')]
        self.assertEqual(len(attempts), len(set(attempts)))

    def test_31_late_failed_attempt_result_cannot_replace_integrated_retry(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT'] = 'crash'; self.build(2)
        worker = next(w for w in self.state()['orchestration_batch']['workers'] if w['milestone_id'] == 'M1')
        receipt = Path(worker['run_dir']) / 'result.json'
        old_receipt = receipt.read_bytes()
        self.env.pop('BUILD_AUDIT_FAULT')
        self.build(extra=['--resume-paused', '--retry-builder', 'M1'])
        candidate = self.candidate()
        source = (self.project / 'server/health.py').read_bytes()
        receipt.write_bytes(old_receipt)
        self.build()
        self.assertEqual(candidate, self.candidate())
        self.assertEqual(source, (self.project / 'server/health.py').read_bytes())
        self.assertEqual(4, len(self.events()))

    def test_19_permission_denial_preserved_on_next_handoff(self):
        spec = bb.plan([([], 'Write the assigned health function only if permitted', ['server/health.py'])],
                       {'M1': {'server/health.py': "def health(): return {'status':'ok'}\n"}},
                       {'M1': "from server.health import health; assert health()['status']=='ok'"},
                       'Implement a scoped health function')
        self.seed(spec); self.env['BUILD_AUDIT_FAULT'] = 'permission'; self.build()
        self.invoke('autoreview', ['--run-dir', str(self.run), '--no-chat'], 2)
        state = self.state()
        self.assertEqual('WAITING_FOR_USER', state['status'])
        qid = state['pending_questions'][0]['id']
        self.invoke('autocode_build', ['--run-dir', str(self.run), '--answer', qid + '=No. Do not access any external account.', '--no-chat'])
        self.assertIn('No. Do not access', json.dumps(self.state()['answers']))
        self.assertEqual(self.original_contract, self.state()['goal_contract'])
        self.assertFalse((self.project / 'server/health.py').exists())
        self.invoke('autoreview', ['--run-dir', str(self.run), '--no-chat'], 2)
        self.assertFalse((self.project / 'server/health.py').exists())
        self.assertEqual([], self.state().get('pending_questions', []), 'Repeated request asked the user again')

    def test_32_plan_cannot_mutate_active_workers_and_revision_invalidates_old_approval(self):
        self.seed(); self.env['BUILD_AUDIT_FAULT'] = 'hold'
        revised = dict(self.spec['contract'])
        revised['constraints'] = [*revised['constraints'], 'Revision two: keep existing health response']
        path = self.root / 'revision-two.json'
        path.write_text(json.dumps(revised))
        process = subprocess.Popen(self.command('autocode_build', ['--run-dir', str(self.run), '--no-chat']),
            cwd=self.root, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline=time.monotonic()+20
            while not self.events() and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertTrue(self.events())
            self.invoke('autoplanner', ['--run-dir', str(self.run), '--edit-goal', str(path), '--no-chat'], 2)
            self.assertEqual(self.original_contract, self.state()['goal_contract'])
        finally:
            (self.root/'release').touch()
            stdout,stderr=process.communicate(timeout=40)
            (self.root/'active-revision.log').write_text(stdout+stderr)
        self.assertEqual(0,process.returncode,stdout+stderr)
        self.invoke('autoplanner', ['--run-dir', str(self.run), '--edit-goal', str(path), '--no-chat'])
        state=self.state()
        self.assertNotEqual(self.original_contract['hash'],state['goal_contract']['hash'])
        self.assertNotEqual('approved',state['goal_contract']['approval_status'])
        self.invoke('autocode_build', ['--run-dir',str(self.run),'--no-chat'], 2)
        self.assertEqual(3,len(self.events()))

    def test_prelaunch_recovery_rejects_unrecorded_workspace_edits(self):
        self.seed(); self.fault('before_worker')
        self.invoke('autocode_build', ['--run-dir', str(self.run), '--no-chat'], 97)
        worker=self.state()['orchestration_batch']['workers'][0]
        (Path(worker['workspace'])/'unrecorded.txt').write_text('Keep this unexplained work')
        self.build(2,extra=['--resume-paused'])
        self.assertEqual('Keep this unexplained work',(Path(worker['workspace'])/'unrecorded.txt').read_text())
        self.assertFalse(any(e['milestone']=='M1' for e in self.events()))

    def checkpoint_spec(self):
        files={f'screens/s{i}.txt': f'state {i}\n' for i in range(21)}
        c1="from pathlib import Path; assert all(Path(f'screens/s{i}.txt').read_text()==f'state {i}\\n' for i in range(21))"
        c2="from pathlib import Path; assert Path('screens/regression.txt').read_text()=='PASS\\n'"
        spec=bb.plan([([], 'Implement 21 screen-state fixtures, then pass the regression checkpoint', ['screens/'])],
            {'M1':files},{'M1':c1+'; '+c2},'Implement and checkpoint a 21-state dashboard fixture')
        spec['contract']['acceptance_criteria'][0]['criterion']='All 21 state artifacts match the baseline'
        spec['contract']['acceptance_criteria'].append(dict(id='C2',criterion='Final regression marker passes',verification_method=c2,human_review=False))
        spec['contract']['milestones'][0]['acceptance_criteria'].append('C2')
        spec['criterion_checks']={'C1':c1,'C2':c2}
        spec['checkpoint_files']=list(files)
        return spec

    def test_23_long_build_exposes_durable_progress_before_it_finishes(self):
        self.seed(self.checkpoint_spec()); self.env['BUILD_AUDIT_FAULT']='checkpoints'
        process=subprocess.Popen(self.command('autocode_build',['--run-dir',str(self.run),'--no-chat']),
            cwd=self.root,env=self.env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        try:
            deadline=time.monotonic()+25
            while not self.events('checkpoints_saved') and time.monotonic()<deadline:
                time.sleep(.02)
            self.assertEqual(21,len(self.events('checkpoint')))
            self.assertIsNone(process.poll(),'Progress was only available after completion')
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                checkpoints=self.state().get('execution_checkpoints',{})
                if any(r.get('completed_tools',0)>=21 for r in checkpoints.get('rows',[])):
                    break
                time.sleep(.05)
            self.assertEqual('running',checkpoints['rows'][0]['status'])
            self.assertGreaterEqual(checkpoints['rows'][1]['completed_tools'],21)
            self.assertTrue(all(r['status']=='not_verified' for r in checkpoints['rows'] if r['id'].startswith('criterion:')))
            status=self.invoke('autocode_build',['--run-dir',str(self.run),'--status'])
            self.assertIn('terra',status.stdout)
            event_files=list(self.run.glob('iterations/*/terra-*.jsonl'))
            self.assertTrue(event_files)
            self.assertEqual(21,sum('checkpoint-screens/' in line for p in event_files for line in p.read_text().splitlines()))
            self.assertEqual(21,len(list((self.project/'screens').glob('s*.txt'))))
        finally:
            (self.root/'release').touch()
            stdout,stderr=process.communicate(timeout=30)
            (self.root/'checkpoint-cli.log').write_text(stdout+stderr)
        self.assertEqual(0,process.returncode,stdout+stderr)
        self.candidate()

    def correct_failed_checkpoint(self, regression=False):
        spec=self.checkpoint_spec(); self.seed(spec); self.build()
        baseline={p:p.stat().st_mtime_ns for p in (self.project/'screens').glob('s*.txt')}
        self.invoke('autoreview',['--run-dir',str(self.run),'--no-chat'])
        initial=self.state()['validation']
        self.assertEqual({'C1':'PASS','C2':'NOT_VERIFIED'},{r['id']:r['status'] for r in initial['criterion_results']})
        self.invoke('autoresolver',['--run-dir',str(self.run),'--no-chat'])
        spec['payloads']['M1']={'screens/regression.txt':'PASS\n'}
        if regression:
            spec['payloads']['M1']['screens/s0.txt']='BROKEN\n'
        (self.root/'plan.json').write_text(json.dumps(spec))
        self.build()
        self.invoke('autoreview',['--run-dir',str(self.run),'--no-chat','--pause-after-stage'],2)
        self.assertEqual(2,len(self.events()))
        if not regression:
            self.assertEqual(baseline,{p:p.stat().st_mtime_ns for p in baseline})
        self.assertTrue(any(v['validation']['source_revision']==initial['source_revision'] for v in self.state()['validation_archive']))
        return self.state()['validation']

    def test_24_failed_later_checkpoint_retains_prior_work_and_evidence(self):
        final=self.correct_failed_checkpoint()
        self.assertEqual('PASS',final['verdict'])
        self.assertNotEqual('TASK_COMPLETE',self.state()['status'])

    def test_25_regression_of_previously_passing_criterion_is_not_hidden(self):
        final=self.correct_failed_checkpoint(regression=True)
        self.assertEqual('FAIL',final['verdict'])
        self.assertEqual({'C1':'NOT_VERIFIED','C2':'PASS'},{r['id']:r['status'] for r in final['criterion_results']})
        self.assertNotEqual('TASK_COMPLETE',self.state()['status'])


if __name__ == '__main__':
    unittest.main()
