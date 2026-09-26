"""Builder-first routing uses fake providers; tests never contact subscription services."""
import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from . import test_autocode, test_subprocess
import autocode as runner
import autocode_support as s
import autocode_goals as goals
import autocode_workflow as workflow
from goal_fixtures import approve_fixture, envelope


class WorkflowTests(unittest.TestCase):
    setUp = test_autocode.RetrofitTest.setUp

    def enable(self):
        self.state['settings'].pop('workflow',None)
        approve_fixture(self.state, goals)
        self.state['status'] = 'PAUSED_REQUESTED'
        workflow.activate(self.state, approval_source='explicit user approval fixture')
        self.state['status'] = 'RUNNING'

    def record(self):
        events = self.run/'validation.jsonl'
        events.write_text(json.dumps({'type':'item.completed','item':{'type':'command_execution',
            'id':'check','command':'test-greeting','exit_code':0,'aggregated_output':'passed'}})+'\n')
        return {'stage':'astra_checkpoint','role':'astra','iteration':5,'events':str(events),
            'source_revision':s.snapshot(self.root)['revision'],'changed_files':[],
            'output':str(self.run/'checkpoint.json')}

    def report(self):
        common=envelope(self.state)
        validation={**common,'verdict':'PASS','findings':[],'checks_run':['test-greeting'],
            'unverified_criteria':[], 'checks':[{'command':'test-greeting','exit_code':0,'evidence_ref':'event:check'}],
            'end_to_end_result':{'status':'PASS','summary':'Independent execution','evidence_refs':['event:check']},
            'criterion_results':[{'id':'C1','status':'PASS','evidence_refs':['event:check']}]}
        decision={**common,'status':'COMPLETE','acceptance_criteria':[
            {**c,'status':'verified','evidence':'Independent event:check'} for c in self.state['acceptance_criteria']],
            'evidence':['event:check'],'next_objective':'','blocker':'','plan':['Done'],
            'affected_paths':[], 'agreed_limitations':[],
            'next_task':{'kind':'none','milestone_id':'','requirements':[],'acceptance_criteria':[],'validation_plan':[]}}
        return {'validation':validation,'decision':decision,
                'consult_sol':{'requested':False,'question':'','reason':''}}

    def apply(self, report=None, record=None):
        runner.apply_result(self.state,'astra_checkpoint',report or self.report(),record or self.record(),self.root,self.run)

    def test_activation_preserves_completed_work_contract_sessions_and_models(self):
        approve_fixture(self.state,goals)
        self.state.update(status='PAUSED_REQUESTED',next_stage='sol')
        before=copy.deepcopy(self.state)
        workflow.activate(self.state,approval_source='user said yes')
        self.assertEqual('astra_checkpoint',self.state['next_stage'])
        for key in ('goal_contract','sessions','stages','history','acceptance_criteria','iteration'):
            self.assertEqual(before[key],self.state[key])
        self.assertEqual(before['settings']['roles'],self.state['settings']['roles'])
        self.assertFalse(self.state['settings']['headroom']['enabled'])

    def test_no_approval_or_changed_goal_rejects_policy(self):
        self.enable()
        self.state['user_events'].pop()
        with self.assertRaises(s.Paused):workflow.guard(self.state)
        self.enable()
        self.state['goal_contract']['hash']='changed'
        with self.assertRaises(s.Paused):workflow.guard(self.state)

    def test_active_or_unreconciled_stage_cannot_migrate(self):
        for field in ('active_stage','pending_report_repair','uncertain_artifacts'):
            with self.subTest(field=field):
                self.enable();self.state['status']='PAUSED_REQUESTED';self.state[field]={'pending':True}
                with self.assertRaises(ValueError):workflow.activate(self.state,approval_source='yes')
                self.state.pop(field)

    def test_legacy_policy_unchanged(self):
        self.assertEqual('sol',workflow.review_stage(self.state))

    def test_rollback_preserves_work_but_requires_sol_revalidation(self):
        self.enable();self.apply()
        stages=copy.deepcopy(self.state['stages']);sessions=copy.deepcopy(self.state['sessions'])
        workflow.rollback(self.state)
        self.assertEqual('sol',self.state['next_stage'])
        self.assertNotEqual('TASK_COMPLETE',self.state['status'])
        self.assertEqual(stages,self.state['stages']);self.assertEqual(sessions,self.state['sessions'])
        self.assertNotIn('validation',self.state)

    def test_rollback_cannot_interrupt_active_worker(self):
        self.enable()
        with self.assertRaises(ValueError):workflow.rollback(self.state)

    def test_independent_checkpoint_completes_with_one_receipt(self):
        self.enable();self.apply()
        self.assertEqual('TASK_COMPLETE',self.state['status'])
        self.assertEqual('astra',self.state['validation']['reviewer_role'])
        self.assertEqual(1,len(self.state['stages']))
        self.assertEqual(1,len(self.state['history']))

    def test_missing_evidence_does_not_partially_accept_validation(self):
        self.enable();report=self.report();report['validation']['checks'][0]['evidence_ref']='event:missing'
        report['validation']['checks'][0]['command']='never-executed-command'
        before=copy.deepcopy(self.state)
        with self.assertRaises(ValueError):self.apply(report)
        self.assertEqual(before,self.state)

    def test_stale_code_unverified_criterion_and_failed_check_cannot_complete(self):
        for mode in ('stale','unverified','failed','wrong-role'):
            self.enable();report=self.report();record=self.record()
            if mode=='stale': record['source_revision']='old'
            if mode=='unverified':report['validation']['criterion_results'][0]['status']='NOT_VERIFIED'
            if mode=='failed':report['validation']['checks'][0]['exit_code']=1
            if mode=='wrong-role':record['role']='terra'
            with self.subTest(mode=mode),self.assertRaises((s.Paused,ValueError)):
                self.apply(report,record)

    def test_blocking_findings_cannot_complete(self):
        self.enable();report=self.report()
        report['validation']['findings']=[{'severity':'high','blocking':True,'finding':'Gap','evidence':'event:check',
            'reproduction_steps':['Run test'],'expected':'Good','actual':'Bad','why_it_matters':'Required','suggested_correction':'Fix'}]
        with self.assertRaises(s.Paused):self.apply(report)

    def test_human_review_still_required(self):
        self.enable()
        # Use native draft/approval so no test relies on an unsealed contract.
        body=copy.deepcopy(self.state['goal_contract']['body']);body['acceptance_criteria'][0]['human_review']=True
        goals.install_draft(self.state,body,origin='test');goals.present(self.state)
        goals.approve(self.state,goals.token(self.state['goal_contract']))
        self.state['settings'].pop('workflow');self.state['status']='PAUSED_REQUESTED'
        workflow.activate(self.state,approval_source='yes');self.state['status']='RUNNING'
        self.apply()
        self.assertEqual('WAITING_FOR_USER',self.state['status'])

    def test_targeted_sol_consultation_preserves_task_and_cannot_complete(self):
        self.enable();report=self.report()
        report['consult_sol']={'requested':True,'question':'Check unusual boundary','reason':'Unresolved ambiguity'}
        with self.assertRaises(ValueError):self.apply(report)
        report['decision'].update(status='CONTINUE',next_objective='Check boundary')
        report['decision']['next_task']['kind']='validate'
        current=copy.deepcopy(self.state.get('current_task'))
        self.apply(report)
        self.assertEqual('sol',self.state['next_stage'])
        self.assertEqual(current,self.state.get('current_task'))
        self.assertEqual('RUNNING',self.state['status'])

    def test_context_contains_approved_policy_actual_diff_and_exact_contract(self):
        self.enable();self.state['diff_ref']='exact.diff'
        prompt,_=s.context_packet(self.state,'astra_checkpoint',self.run/'state.json')
        data=json.loads(prompt.split('CURRENT HANDOFF DATA\n',1)[1])
        self.assertEqual('exact.diff',data['diff_ref'])
        self.assertEqual(self.state['goal_contract'],data['goal_contract'])
        self.assertIn('ONE call',prompt)
        self.assertNotIn('MILESTONE HANDOFF POLICY v1',prompt)


class WorkflowSubprocessTests(unittest.TestCase):
    setUp=test_subprocess.SubprocessFlow.setUp
    launch=test_subprocess.SubprocessFlow.launch
    saved=test_subprocess.SubprocessFlow.saved
    new_run_engine_args=('--engine','codex')

    def prepare(self,mode):
        self.env['AUTOCODE_FIXTURE_MODE']=mode
        self.launch(['Build greeting','--chat'],2,answers='CLI\nno\n')
        run,state=self.saved()
        self.launch(['--run-dir',str(run),'--approve-goal',state['displayed_goal'],'--no-chat'],0)
        self.launch(['--run-dir',str(run),'--pause-after-stage','--no-chat'],2)
        run,state=self.saved()
        self.assertEqual('terra',state['next_stage'])
        # This suite exercises preservation of a legacy opt-in workflow.
        state['settings'].pop('milestone_checkpoints', None)
        workflow.activate(state,approval_source='explicit test approval')
        s.atomic_json(run/'state.json',state)
        return run

    def test_same_run_implementation_review_completion_without_sol(self):
        run=self.prepare('no-human')
        self.launch(['--run-dir',str(run),'--resume-paused','--no-chat'],0)
        _,state=self.saved()
        self.assertEqual('COMPLETE',state['phase'])
        self.assertEqual(['terra','astra_checkpoint'],[r['stage'] for r in state['stages'] if r['stage'] in ('terra','sol','astra_review','astra_checkpoint')])
        before=(run/'state.json').read_bytes()
        self.launch(['--run-dir',str(run),'--no-chat'],0)
        self.assertEqual(before,(run/'state.json').read_bytes())

    def test_rework_returns_to_glm_then_independent_checkpoint(self):
        run=self.prepare('rework')
        self.launch(['--run-dir',str(run),'--resume-paused','--no-chat'],0)
        _,state=self.saved()
        stages=[r['stage'] for r in state['stages'] if r['stage'] in ('terra','sol','astra_review','astra_checkpoint')]
        self.assertEqual(['terra','astra_checkpoint','terra','astra_checkpoint'],stages)

    def test_provider_quota_keeps_checkpoint_and_does_not_call_sol(self):
        run=self.prepare('no-human');self.env['AUTOCODE_FIXTURE_QUOTA_STAGE']='astra_checkpoint'
        self.launch(['--run-dir',str(run),'--resume-paused','--no-chat'],2)
        _,state=self.saved()
        self.assertNotEqual('COMPLETE',state['phase'])
        self.assertEqual('astra_checkpoint',state['next_stage'])
        self.assertNotIn('sol',[r['stage'] for r in state['stages']])
