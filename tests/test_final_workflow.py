"""Final-only routing regression tests. All provider requests are offline fixtures."""
# path bootstrap: runtime in tools/, fakes in tests/fakes/
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / 'tools'
_FAKES = _ROOT / 'tests' / 'fakes'
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import copy
import json
import unittest
try:
    from . import test_workflow
except ImportError:
    import test_workflow
import autocode as runner
import autocode_support as s
import autocode_workflow as w
from goal_fixtures import envelope


class FinalWorkflowTests(unittest.TestCase):
    setUp=test_workflow.WorkflowTests.setUp
    record=test_workflow.WorkflowTests.record
    report=test_workflow.WorkflowTests.report
    apply=test_workflow.WorkflowTests.apply

    def enable(self):
        test_workflow.WorkflowTests.enable(self)
        self.state['status']='PAUSED_REQUESTED'
        w.activate_final(self.state,approval_source='Explicit final-only approval')
        self.state['status']='RUNNING'

    def implementation(self,action='CONTINUE'):
        value={**envelope(self.state),'summary':'Changes tested','changed_files':[],
            'commands_run':['test-greeting'],'results':['self-check'],'remaining_risks':[],
            'evidence_refs':[str(self.evidence)],'addressed_requirements':['C1'],
            'untested_behavior':[],'recommended_checks':[]}
        value['continuation']={'action':action,'plan':['Approved task'],'reason':'Concrete debugging issue',
            'question':'Why does the boundary fail?', 'self_assessment':self.report()['validation'],
            'next_task':{'kind':'implement','objective':'Next approved Builder batch','affected_paths':['greet.py'],
                'milestone_id':'M1','requirements':['Preserve greeting'],'acceptance_criteria':['C1'],
                'validation_plan':['Run greeting tests']}}
        return value

    def implement(self,value=None):
        record={**self.record(),'role':'terra','stage':'terra','after_ref':'saved-after','changed_files':['greet.py']}
        runner.apply_result(self.state,'terra',value or self.implementation(),record,self.root,self.run)

    def test_continue_goes_directly_to_glm_and_retains_goal(self):
        self.enable();goal=copy.deepcopy(self.state['goal_contract']);iteration=self.state['iteration']
        self.implement()
        self.assertEqual('terra',self.state['next_stage'])
        self.assertEqual('Next approved Builder batch',self.state['current_task']['objective'])
        self.assertEqual(goal,self.state['goal_contract'])
        self.assertEqual(iteration+1,self.state['iteration'])

    def test_unknown_criteria_cannot_expand_next_task(self):
        self.enable();value=self.implementation();value['continuation']['next_task']['acceptance_criteria']=['Invented']
        before=copy.deepcopy(self.state)
        with self.assertRaises(ValueError):self.implement(value)
        self.assertEqual(before,self.state)

    def test_self_check_is_not_independent_and_cannot_complete(self):
        self.enable();self.implement(self.implementation('REQUEST_FINAL_AUDIT'))
        self.assertEqual('astra_checkpoint',self.state['next_stage'])
        self.assertNotIn('validation',self.state)
        self.assertFalse(s.completion_ready(self.state,self.report()['decision'],s.snapshot(self.root)))
        self.assertFalse(self.state['final_audit_request']['independent'])
        self.apply()
        self.assertEqual('TASK_COMPLETE',self.state['status'])
        self.assertTrue(self.state['validation']['final_audit'])

    def test_incomplete_or_missing_self_evidence_never_calls_astra(self):
        for mutation in ('untested','criterion','command'):
            self.enable();value=self.implementation('REQUEST_FINAL_AUDIT')
            if mutation=='untested':value['untested_behavior']=['Missing cases']
            if mutation=='criterion':value['continuation']['self_assessment']['criterion_results'][0]['status']='NOT_VERIFIED'
            if mutation=='command':value['continuation']['self_assessment']['checks'][0]['command']='not-executed'
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):self.implement(value)
            self.assertNotIn('final_audit_request',self.state)

    def test_astra_dispatch_requires_fresh_explicit_final_request(self):
        self.enable()
        with self.assertRaises(s.Paused):w.dispatch_guard(self.state,'astra_checkpoint',self.root)
        self.implement(self.implementation('REQUEST_FINAL_AUDIT'))
        w.dispatch_guard(self.state,'astra_checkpoint',self.root)
        self.evidence.write_text('changed')
        # Change actual source, not just ignored run-scoped evidence.
        (self.root/'new-code.py').write_text('# changed source')
        with self.assertRaises(s.Paused):w.dispatch_guard(self.state,'astra_checkpoint',self.root)

    def test_sol_is_explicit_and_returns_directly_to_glm(self):
        self.enable()
        with self.assertRaises(s.Paused):w.dispatch_guard(self.state,'sol',self.root)
        self.implement(self.implementation('ESCALATE_SOL'))
        self.assertEqual('sol',self.state['next_stage'])
        record={**self.record(),'role':'sol','stage':'sol'}
        runner.apply_result(self.state,'sol',self.report()['validation'],record,self.root,self.run)
        self.assertEqual('terra',self.state['next_stage'])
        self.assertNotIn('validation',self.state)
        self.assertEqual(1,len(self.state['consultation_reports']))

    def test_empty_escalation_rejected(self):
        self.enable();value=self.implementation('ESCALATE_SOL');value['continuation']['question']=''
        with self.assertRaises(ValueError):self.implement(value)

    def test_material_question_waits_for_user_not_gpt(self):
        self.enable();value=self.implementation('WAITING_FOR_USER')
        value['user_request'].update(kind='permission',discovered='Needs external access',impact='Cannot continue',
            decision_needed='Approve external access?',options=['Approve','Decline'])
        self.implement(value)
        self.assertEqual('WAITING_FOR_USER',self.state['status'])

    def test_final_audit_cannot_dispatch_sol(self):
        self.enable();self.implement(self.implementation('REQUEST_FINAL_AUDIT'))
        report=self.report();report['consult_sol'].update(requested=True,question='Ask Sol',reason='Investigate')
        with self.assertRaises(ValueError):self.apply(report)

    def test_rejected_final_audit_returns_to_glm(self):
        self.enable();self.implement(self.implementation('REQUEST_FINAL_AUDIT'))
        report=self.report();report['validation']['verdict']='FAIL'
        report['validation']['criterion_results'][0]['status']='FAIL'
        report['decision'].update(status='REWORK',next_objective='Repair required CLI boundary',
            next_task={'kind':'implement','milestone_id':'M1','requirements':['Fix boundary'],
                       'acceptance_criteria':['C1'],'validation_plan':['Run boundary test']})
        self.apply(report)
        self.assertEqual('terra',self.state['next_stage'])


class FinalSubprocessTests(test_workflow.WorkflowSubprocessTests):
    def prepare(self,mode):
        run=super().prepare(mode)
        _,state=self.saved()
        w.activate_final(state,approval_source='explicit final-only fixture approval')
        s.atomic_json(run/'state.json',state)
        return run

    def test_same_run_implementation_review_completion_without_sol(self):
        run=self.prepare('no-human')
        self.launch(['--run-dir',str(run),'--resume-paused','--no-chat'],0)
        _,state=self.saved()
        self.assertEqual('COMPLETE',state['phase'])
        self.assertEqual(['terra','terra','astra_checkpoint'],[r['stage'] for r in state['stages'] if r['stage'] in ('terra','sol','astra_checkpoint')])
        before=(run/'state.json').read_bytes()
        self.launch(['--run-dir',str(run),'--no-chat'],0)
        self.assertEqual(before,(run/'state.json').read_bytes())

    def test_rework_returns_to_glm_then_independent_checkpoint(self):
        # Explicit consultation is followed by implementation, never a Plan Reviewer milestone call.
        run=self.prepare('sol-escalation')
        self.launch(['--run-dir',str(run),'--resume-paused','--no-chat'],0)
        _,state=self.saved()
        self.assertEqual(['terra','sol','terra','astra_checkpoint'],[r['stage'] for r in state['stages'] if r['stage'] in ('terra','sol','astra_checkpoint')])
