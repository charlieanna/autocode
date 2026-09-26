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
from pathlib import Path
import tempfile
import unittest
from . import autocode_checkpoints as checkpoints
from . import autocode_status as status
from .dashboard import dashboard_monitor as monitor


class CheckpointTests(unittest.TestCase):
    def state(self):
        return {'task':'Implement screens','workspace':'/fixture','status':'RUNNING',
            'goal_contract':{'hash':'approved'},
            'current_task':{'id':'task1','milestone_id':'M1','acceptance_criteria':['C1','C2']},
            'acceptance_criteria':[{'id':'C1','criterion':'Mobile text visible'}, {'id':'C2','criterion':'Regression passes'}],
            'active_stage':{'stage':'terra','activity':{'completed_tool_count':21}}}

    def test_durable_checkpoint_before_completion_and_reload(self):
        state=self.state()
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'state.json'; status.persist(path,state)
            saved=json.loads(path.read_text())
            rows={r['id']:r for r in saved['execution_checkpoints']['rows']}
            self.assertEqual('running',rows['implementation']['status'])
            self.assertEqual(21,rows['builder_activity']['completed_tools'])
            self.assertEqual('not_verified',rows['criterion:C1']['status'])
            self.assertEqual(saved['execution_checkpoints'],monitor.snapshot(saved,Path(tmp),detailed=True)['checkpoints'])

    def test_prior_pass_is_not_visible_for_new_candidate_or_contract(self):
        state=self.state(); state.pop('active_stage')
        state['implementation']={'task_id':'task1','contract_hash':'approved','source_revision':'r1'}
        state['validation']={'task_id':'task1','contract_hash':'approved','source_revision':'r1','verdict':'PASS',
                              'criterion_results':[{'id':'C1','status':'PASS','evidence_refs':['receipt']} ]}
        checkpoints.update(state)
        self.assertEqual('verified',state['execution_checkpoints']['rows'][4]['status'])
        state['implementation']['source_revision']='r2'; checkpoints.update(state)
        self.assertEqual('not_verified',state['execution_checkpoints']['rows'][4]['status'])
        state['goal_contract']['hash']='changed'; checkpoints.update(state)
        self.assertEqual('pending',state['execution_checkpoints']['rows'][0]['status'])

    def test_completed_task_progress_retained_when_next_task_starts(self):
        state=self.state(); checkpoints.update(state)
        state['current_task']['id']='task2'; checkpoints.update(state)
        self.assertEqual('task1',state['checkpoint_history'][0]['task_id'])
        checkpoints.update(state)
        self.assertEqual(1,len(state['checkpoint_history']))

    def test_dashboard_refuses_worker_paths_outside_batch(self):
        state=self.state(); state['orchestration_batch']={'id':'batch','workers':[{'run_dir':'/outside','milestone_id':'M1'}]}
        view=monitor.snapshot(state,Path('/fixture/run'),detailed=True)
        self.assertNotIn('checkpoints',view['orchestration_batch']['workers'][0])

    def test_no_progress_is_scoped_to_task_and_contract(self):
        state=self.state(); state.pop('active_stage')
        state['no_progress_reports']=[{'task_id':'old','contract_hash':'approved'}]
        checkpoints.update(state)
        self.assertEqual('pending',state['execution_checkpoints']['rows'][0]['status'])
        state['no_progress_reports'][0]['task_id']='task1'
        checkpoints.update(state)
        self.assertEqual('no_progress',state['execution_checkpoints']['rows'][0]['status'])
        state['goal_contract']['hash']='new'
        checkpoints.update(state)
        self.assertEqual('pending',state['execution_checkpoints']['rows'][0]['status'])

    def test_unbound_validation_never_marks_checkpoints_verified(self):
        state=self.state(); state.pop('active_stage')
        state['status']='TASK_COMPLETE'
        state['validation']={'task_id':'task1','contract_hash':'approved','verdict':'PASS',
            'criterion_results':[{'id':'C1','status':'PASS'}]}
        checkpoints.update(state)
        self.assertFalse(any(r['status']=='verified' for r in state['execution_checkpoints']['rows']))
