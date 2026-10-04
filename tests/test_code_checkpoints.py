"""Public checkpoint CLI tests with real Git and owned temporary run fixtures."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import autocode_code_checkpoints as checkpoints
import autocode_checkpoint_cli as operation
import autocode_contract_identity as identity
import autocode_util as util
from autocode_taskrun import TaskRun, TaskRunError
from . import test_subprocess
from goal_fixtures import body

TOOLS = Path(__file__).resolve().parents[1] / 'tools'


class CodeCheckpoints(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve(); self.workspace = self.root / 'project'; self.workspace.mkdir()
        self.git('init', '-q'); (self.workspace / 'app.txt').write_text('baseline\n')
        self.git('add', '.'); self.git('-c', 'user.name=Fixture', '-c', 'user.email=f@example.test', 'commit', '-qm', 'base')
        self.run = self.workspace / '.autocode/runs/fixture'; self.run.mkdir(parents=True)
        (self.workspace / '.autocode/.gitignore').write_text('*\n')
        self.state = {'version': 3, 'task_id': 'run-task', 'task': 'Build fixture', 'workspace': str(self.workspace),
            'run_dir': str(self.run), 'created_at': util.now(), 'status': 'PAUSED_REQUESTED', 'iteration': 3,
            'next_stage': 'sol', 'sessions': {}, 'history': [], 'stages': [], 'active_seconds': 12,
            'settings': {'engine': 'codex', 'roles': {'terra': {'model': 'gpt-6-sol', 'reasoning_effort': 'high', 'pinned': True}},
                         'limits': {'max_seconds': 0}}, 'user_events': [], 'findings_ledger': [{'id':'early','status':'open'}],
            'workflow': {'kind':'build'}, 'current_task': {'id':'bounded-task','milestone_id':'M1','objective':'Fixture code'}}
        contract = {'task_id': self.state['task_id'], 'revision': 1, 'body': body()}
        contract['hash'] = util.digest(contract)
        approval = {'kind':'goal_approval','actor':'user_cli','token':identity.token(contract),'at':util.now()}
        contract.update(approval_status='approved', approval_event=approval, origin='astra_discovery')
        self.state['goal_contract'] = contract; self.state['user_events'].append(approval)
        self.state['base_commit'] = self.git('rev-parse','HEAD')
        (self.workspace / 'app.txt').write_text('checkpoint code\n')
        (self.workspace / 'new file.txt').write_text('new at checkpoint\n')
        (self.workspace / 'run.sh').write_text('#!/bin/sh\nexit 0\n'); (self.workspace / 'run.sh').chmod(0o755)
        (self.workspace / 'link').symlink_to('app.txt')
        snap = util.snapshot(self.workspace)
        self.state['implementation'] = {'task_id':'bounded-task','source_revision':snap['revision'],'contract_hash':contract['hash']}
        self.state['stages'].append({'stage':'terra','task_id':'bounded-task','source_revision':snap['revision'],
                                    'exit_code':0,'finished_at':util.now(),'changed_files':['app.txt','new file.txt','run.sh','link']})
        checkpoints.update(self.state, self.run)
        self.assertTrue(self.state['code_checkpoints'][0]['available'], self.state['code_checkpoints'][0])
        self.ident = self.state['code_checkpoints'][0]['id']; self.save()
        self.client = TaskRun(self.workspace, self.run, command=(sys.executable,str(TOOLS/'autocode.py')),
                              env={'AUTOCODE_HOME':str(self.root/'registry')}, timeout=20)

    def git(self, *args):
        return checkpoints.git(self.workspace, *args).decode().strip()

    def save(self):
        util.atomic_json(self.run/'state.json',self.state)

    def test_capture_leaves_head_index_and_uncommitted_content_unchanged(self):
        self.assertEqual(self.state['base_commit'], self.git('rev-parse','HEAD'))
        self.assertEqual('app.txt',self.git('ls-files'))
        self.assertEqual('checkpoint code\n',(self.workspace/'app.txt').read_text())
        old=copy.deepcopy(self.state['code_checkpoints']); checkpoints.update(self.state,self.run)
        self.assertEqual(old,self.state['code_checkpoints'])
        comparison=self.client.compare_checkpoint(self.ident)
        self.assertEqual([],comparison['changed_files'])
        self.assertEqual('',comparison['patch'])

    def test_restore_preserves_original_dirty_index_and_marks_later_findings(self):
        checkpoint_bytes=(self.run/'code-checkpoints'/f'{self.ident}.json').read_bytes()
        (self.workspace/'app.txt').write_text('later work\n'); self.git('add','app.txt')
        self.state['validation']={'verdict':'PASS','source_revision':'later','contract_hash':self.state['goal_contract']['hash']}
        self.state['human_reviews']={'AC1':{'actor':'user_cli'}}; self.state['final_decision']={'status':'COMPLETE'}
        self.state['findings_ledger'][0]['status']='resolved'  # Fixed only by later code; rollback must reopen it.
        self.state['findings_ledger'].append({'id':'later','status':'open','finding':'Later code problem'})
        self.state['milestone_progress']={'M1':{'id':'M1','contract_hash':self.state['goal_contract']['hash'],
            'accepted':True,'seconds':12,'replans':1,'acceptance_criteria':['AC1']}}
        self.save()
        before=(self.git('rev-parse','HEAD'), self.git('write-tree'), self.git('diff','--cached'))
        compared=self.client.compare_checkpoint(self.ident)
        self.assertIn('app.txt',compared['changed_files']); self.assertIn('+later work',compared['patch'])
        child=self.client.restore_checkpoint(self.ident,compared['expected_token'],'restore-owned-one')
        self.assertNotEqual(self.workspace,child.workspace)
        self.assertEqual(before,(self.git('rev-parse','HEAD'),self.git('write-tree'),self.git('diff','--cached')))
        self.assertEqual('later work\n',(self.workspace/'app.txt').read_text())
        self.assertEqual('checkpoint code\n',(child.workspace/'app.txt').read_text())
        self.assertTrue((child.workspace/'run.sh').stat().st_mode & 0o111)
        self.assertTrue((child.workspace/'link').is_symlink())
        view=child.status(); self.assertEqual('PAUSED_REQUESTED',view['status']); self.assertFalse(view['done'])
        self.assertIsNone(view['evidence']['check_replay']); self.assertFalse(any(x['human_reviewed'] for x in view['evidence']['acceptance']))
        statuses={row['id']:row['status'] for row in view['evidence']['findings']}
        self.assertEqual({'early':'open','later':'rolled_back'},statuses)
        saved=util.read(child.run_dir/'state.json')  # owned fixture only
        self.assertEqual(self.state['goal_contract'],saved['goal_contract']); self.assertTrue(identity.approved(saved))
        self.assertEqual(self.state['settings'],saved['settings']); self.assertEqual(12,saved['active_seconds'])
        self.assertFalse(saved['milestone_progress']['M1']['accepted']); self.assertEqual(12,saved['milestone_progress']['M1']['seconds'])
        self.assertNotIn('validation',saved); self.assertNotIn('final_decision',saved)
        self.assertEqual(self.state,util.read(child.run_dir/'restoration-history.json'))
        self.assertEqual(checkpoint_bytes,(self.run/'code-checkpoints'/f'{self.ident}.json').read_bytes())
        again=self.client.restore_checkpoint(self.ident,compared['expected_token'],'restore-owned-one')
        self.assertEqual(child.run_dir,again.run_dir)
        self.assertEqual(1,len(list((self.workspace/'.autocode/worktrees').iterdir())))

    def test_stale_source_or_approval_live_worker_and_lock_refuse(self):
        comparison=self.client.compare_checkpoint(self.ident)
        (self.workspace/'app.txt').write_text('unsent later change')
        with self.assertRaisesRegex(TaskRunError,'source changed'):
            self.client.restore_checkpoint(self.ident,comparison['expected_token'],'stale-source-one')
        for field,value in [('active_stage',{'stage':'terra'}),('active_runner_check',{'stage':'regression_proof'}),
                            ('pending_questions',[{'id':'question'}]),('uncertain_artifacts',True)]:
            with self.subTest(field=field):
                self.state[field]=value;self.save();comparison=self.client.compare_checkpoint(self.ident)
                with self.assertRaisesRegex(TaskRunError,'Reconcile'):
                    self.client.restore_checkpoint(self.ident,comparison['expected_token'],'live-refusal-'+field)
                self.state.pop(field);self.save()
        with util.run_lock(self.run):
            with self.assertRaisesRegex(TaskRunError,'run lock'):
                self.client.restore_checkpoint(self.ident,comparison['expected_token'],'locked-refusal')
        self.state['goal_contract']['approval_status']='draft';self.save()
        with self.assertRaisesRegex(TaskRunError,'approved plan'):
            self.client.restore_checkpoint(self.ident,comparison['expected_token'],'unapproved-refusal')
        self.assertFalse((self.workspace/'.autocode/worktrees').exists())

    def test_changed_receipt_legacy_step_pending_intervention_refuse(self):
        with self.assertRaisesRegex(TaskRunError,'no restorable'):
            self.client.compare_checkpoint('legacy-step')
        comparison=self.client.compare_checkpoint(self.ident)
        util.atomic_json(self.run/'interventions.json',{'requests':[{'id':'new-pause'}]})
        with self.assertRaisesRegex(TaskRunError,'pending intervention'):
            self.client.restore_checkpoint(self.ident,comparison['expected_token'],'inbox-refusal')
        (self.run/'interventions.json').unlink()
        path=self.run/'code-checkpoints'/f'{self.ident}.json';path.write_text(path.read_text()+' ')
        with self.assertRaisesRegex(TaskRunError,'receipt changed'):
            self.client.compare_checkpoint(self.ident)

    def test_partial_failure_reconciles_same_candidate_without_reset(self):
        comparison=self.client.compare_checkpoint(self.ident)
        with patch.object(operation.registry,'register_run',side_effect=ValueError('registry unavailable')):
            with self.assertRaisesRegex(ValueError,'registry unavailable'):
                operation.restore(self.state,self.run,self.ident,comparison['expected_token'],'partial-owned-one')
        partial=util.read(self.run/'checkpoint-restores/partial-owned-one.json')
        workspace=Path(partial['workspace']); original=(workspace/'app.txt').read_bytes()
        child=self.client.restore_checkpoint(self.ident,comparison['expected_token'],'partial-owned-one')
        self.assertEqual(workspace,child.workspace);self.assertEqual(original,(workspace/'app.txt').read_bytes())
        with self.assertRaisesRegex(TaskRunError,'different checkpoint'):
            self.client.restore_checkpoint(self.ident,'wrong-token','partial-owned-one')

    def test_changed_partial_candidate_is_preserved_and_refused(self):
        comparison=self.client.compare_checkpoint(self.ident)
        with patch.object(operation.registry,'register_run',side_effect=ValueError('registry unavailable')):
            with self.assertRaises(ValueError):
                operation.restore(self.state,self.run,self.ident,comparison['expected_token'],'partial-changed-one')
        partial=util.read(self.run/'checkpoint-restores/partial-changed-one.json')
        workspace=Path(partial['workspace']);(workspace/'app.txt').write_text('independent later edit')
        with self.assertRaisesRegex(TaskRunError,'source changed'):
            self.client.restore_checkpoint(self.ident,comparison['expected_token'],'partial-changed-one')
        self.assertEqual('independent later edit',(workspace/'app.txt').read_text())


    def test_partial_continuation_accounting_change_refuses_without_reset(self):
        comparison=self.client.compare_checkpoint(self.ident)
        with patch.object(operation.registry,'register_run',side_effect=ValueError('registry unavailable')):
            with self.assertRaises(ValueError):
                operation.restore(self.state,self.run,self.ident,comparison['expected_token'],'partial-budget-one')
        partial=util.read(self.run/'checkpoint-restores/partial-budget-one.json')
        run=next((Path(partial['workspace'])/'.autocode/runs').iterdir()); state_path=run/'state.json'
        changed=util.read(state_path);changed['active_seconds']=0;util.atomic_json(state_path,changed)
        before=state_path.read_bytes()
        with self.assertRaisesRegex(TaskRunError,'continuation has changed'):
            self.client.restore_checkpoint(self.ident,comparison['expected_token'],'partial-budget-one')
        self.assertEqual(before,state_path.read_bytes())

    def test_completed_receipt_recovers_missing_chat_history_without_new_candidate(self):
        comparison=self.client.compare_checkpoint(self.ident)
        original_write=util.atomic_json
        def fail_status(path, data):
            if Path(path)==self.run/'state.json':
                raise OSError('status receipt interrupted')
            return original_write(path,data)
        with patch.object(util,'atomic_json',side_effect=fail_status):
            with self.assertRaisesRegex(OSError,'status receipt interrupted'):
                operation.restore(self.state,self.run,self.ident,comparison['expected_token'],'receipt-gap-owned')
        self.assertEqual([],self.client.status()['code_checkpoints']['restores'])
        child=self.client.restore_checkpoint(self.ident,comparison['expected_token'],'receipt-gap-owned')
        rows=self.client.status()['code_checkpoints']['restores']
        self.assertEqual([str(child.run_dir)],[r['run_dir'] for r in rows])
        self.assertEqual(1,len(list((self.workspace/'.autocode/worktrees').iterdir())))

    def test_partial_staged_change_is_preserved_and_refused(self):
        comparison=self.client.compare_checkpoint(self.ident)
        with patch.object(operation.registry,'register_run',side_effect=ValueError('registry unavailable')):
            with self.assertRaises(ValueError):
                operation.restore(self.state,self.run,self.ident,comparison['expected_token'],'partial-index-owned')
        partial=util.read(self.run/'checkpoint-restores/partial-index-owned.json')
        workspace=Path(partial['workspace']);file=workspace/'app.txt';old=file.read_bytes()
        file.write_text('staged later edit');checkpoints.git(workspace,'add','app.txt');file.write_bytes(old)
        staged=checkpoints.git(workspace,'write-tree')
        with self.assertRaisesRegex(TaskRunError,'source changed'):
            self.client.restore_checkpoint(self.ident,comparison['expected_token'],'partial-index-owned')
        self.assertEqual(staged,checkpoints.git(workspace,'write-tree'));self.assertEqual(old,file.read_bytes())

    def test_partial_redirected_storage_refuses_before_creating_locks(self):
        comparison=self.client.compare_checkpoint(self.ident)
        with patch.object(operation.registry,'register_run',side_effect=ValueError('registry unavailable')):
            with self.assertRaises(ValueError):
                operation.restore(self.state,self.run,self.ident,comparison['expected_token'],'partial-link-owned')
        partial=util.read(self.run/'checkpoint-restores/partial-link-owned.json')
        workspace=Path(partial['workspace']);runs=workspace/'.autocode/runs'
        runs.rename(workspace/'.autocode/preserved-runs')
        outside=self.root/'outside';outside.mkdir();runs.symlink_to(outside,target_is_directory=True)
        with self.assertRaisesRegex(TaskRunError,'operational storage changed'):
            self.client.restore_checkpoint(self.ident,comparison['expected_token'],'partial-link-owned')
        self.assertEqual([],list(outside.iterdir()));self.assertTrue((workspace/'.autocode/preserved-runs').is_dir())


    def test_restore_does_not_replenish_consumed_recovery_or_extension_allowances(self):
        self.state.update(automatic_recoveries_since_resume=3,consecutive_timeout_recoveries=2,
                          milestone_active_seconds={'M1':123},automatic_timeout_recoveries=[{'attempt':'old'}],
                          resolver={'budget_extensions':[{'kind':'max_seconds','idempotency_key':'used'}],
                                    'human_escalations':{'historical':{'status':'consumed'}}})
        self.save();compared=self.client.compare_checkpoint(self.ident)
        child=self.client.restore_checkpoint(self.ident,compared['expected_token'],'spent-allowance-owned')
        restored=util.read(child.run_dir/'state.json')  # owned disposable fixture
        from autocode_recovery_limits import stop_reason
        from autocode_run_records import recovery_count
        self.assertEqual('PAUSED_TIMEOUT_RECOVERY',stop_reason(restored,recovery_count(restored),3)[0])
        self.assertEqual({'M1':123},restored['milestone_active_seconds'])
        self.assertEqual([{'attempt':'old'}],restored['automatic_timeout_recoveries'])
        self.assertEqual(self.state['resolver']['budget_extensions'],restored['resolver']['budget_extensions'])
        self.assertNotIn('human_escalations',restored['resolver'])


class RealCheckpointFlow(unittest.TestCase):
    def test_actual_builder_checkpoint_restores_then_requires_fresh_validation(self):
        fixture=test_subprocess.SubprocessFlow();fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixture.env['AUTOCODE_FIXTURE_MODE']='no-human'
        fixture.launch(['Build greeting','--chat'],2,answers='CLI\nno\n')
        run,state=fixture.saved(); client=TaskRun(fixture.project,run,command=tuple(fixture.entry),env=fixture.env,timeout=60)
        client.approve_plan(state['displayed_goal'])
        for _ in range(3):
            fixture.launch(['--run-dir',str(run),'--resume-paused','--pause-after-stage','--no-chat'],2)
            view=client.status()
            if view['code_checkpoints']['rows']:
                break
        rows=view['code_checkpoints']['rows'];self.assertTrue(rows,view)
        self.assertTrue(rows[-1]['available'],rows[-1])
        old_head=checkpoints.git(fixture.project,'rev-parse','HEAD')
        (fixture.project/'later.txt').write_text('keep later work')
        compared=client.compare_checkpoint(rows[-1]['id'])
        restored=client.restore_checkpoint(rows[-1]['id'],compared['expected_token'],'real-flow-restore')
        self.assertEqual(old_head,checkpoints.git(fixture.project,'rev-parse','HEAD'))
        self.assertEqual('keep later work',(fixture.project/'later.txt').read_text())
        self.assertFalse((restored.workspace/'later.txt').exists())
        self.assertEqual('PAUSED_REQUESTED',restored.status()['status'])
        done=restored.resume_paused()
        self.assertEqual('TASK_COMPLETE',done['status'],done)
        self.assertTrue(done['evidence']['check_replay'],done)
        proof=done['evidence']['check_replay'];self.assertEqual('PASS',proof['verdict'])
        self.assertNotEqual(rows[-1]['source_revision'],proof['source_revision'])
