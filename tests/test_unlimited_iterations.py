"""Unlimited iterations is explicit and does not disable other limits."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from . import test_goals, test_autocode, test_subprocess
import autocode as runner
import autocode_configure
import autocode_milestones as milestones
import autocode_planning as planning
import autopilot
import autocode_support as s
from goal_fixtures import assert_operational_wait


class UnlimitedTests(unittest.TestCase):
    setUp=test_autocode.RetrofitTest.setUp

    def args(self,**kwargs):
        return SimpleNamespace(astra_model=None,terra_model=None,sol_model=None,reasoning_effort=None,
            headroom=None,context_soft_tokens=None,rotate_after_input_tokens=None,**kwargs)

    def test_explicit_unlimited_preserves_all_other_settings(self):
        self.state['settings']['limits']={'iteration_ceiling':23,'max_seconds':1800,
                                         'no_progress_batches':3,'stage_timeout_seconds':900,
                                         'idle_timeout_seconds':75,'tool_timeout_seconds':1200}
        original=copy.deepcopy(self.state)
        result=autocode_configure.configure(self.args(unlimited_iterations=True),self.state, planning=planning, milestones=milestones, autopilot=autopilot)
        expected=copy.deepcopy(original['settings']);expected['limits']['iteration_ceiling']=None
        expected['report_repair']={'max_attempts':2}
        expected['provider']='opencode'
        expected['evidence_provenance']={'kind':'unknown','basis':'unavailable',
            'declaration':'No declaration; provider/model names cannot distinguish a fixture from live models'}
        # The explicit flag is recorded as user-owned so AutoResolver never rewrites it.
        expected['budget_origins']={'iteration_ceiling':'user_explicit'}
        expected['roles']['completion']={**expected['roles']['astra'],
            'model':runner.DEFAULT_ROLE_MODELS['completion'],
            'reasoning_effort':runner.opencode.DEFAULT_REASONING_EFFORTS['completion']}
        self.assertEqual(expected,result)
        self.assertEqual(original,self.state)

    def test_saved_token_setting_is_removed_without_mutating_saved_input(self):
        self.state['settings']['limits'] = {'iteration_ceiling': 23, 'max_seconds': 1800,
                                          'max_reported_tokens': 1}
        self.state['settings']['budget_origins'] = {'max_reported_tokens': 'user_explicit'}
        original = copy.deepcopy(self.state)
        settings = autocode_configure.configure(self.args(), self.state,
            planning=planning, milestones=milestones, autopilot=autopilot)
        self.assertNotIn('max_reported_tokens', settings['limits'])
        self.assertNotIn('max_reported_tokens', settings['budget_origins'])
        self.assertEqual(1800, settings['limits']['max_seconds'])
        self.assertEqual(original, self.state)

    def test_omitted_flag_preserves_unlimited_on_resume(self):
        self.state['settings']['limits']={'iteration_ceiling':None}
        self.assertIsNone(autocode_configure.configure(self.args(),self.state, planning=planning, milestones=milestones, autopilot=autopilot)['limits']['iteration_ceiling'])

    def test_new_run_defaults_to_unlimited_iterations(self):
        state={key:value for key,value in self.state.items() if key!='settings'}
        args=SimpleNamespace(engine='codex', provider=None, astra_model=None,terra_model=None,sol_model=None,
            reasoning_effort=None,headroom=None,context_soft_tokens=None,rotate_after_input_tokens=None,
            legacy_iteration_ceiling=None,max_iterations=None,max_seconds=None,no_progress_limit=None)
        with patch.object(s,'local_settings',return_value={}):
            settings=autocode_configure.configure(args,state, planning=planning, milestones=milestones, autopilot=autopilot)
        self.assertIsNone(settings['limits']['iteration_ceiling'])
        self.assertEqual('runner_default',settings['budget_origins']['iteration_ceiling'])

    def test_explicit_ceiling_restores_cap(self):
        self.state['settings']['limits']={'iteration_ceiling':None}
        self.assertEqual(30,autocode_configure.configure(self.args(max_iterations=30),self.state, planning=planning, milestones=milestones, autopilot=autopilot)['limits']['iteration_ceiling'])

    def test_boundary_and_validation(self):
        self.assertFalse(runner.iteration_limit_reached(1000000,None))
        self.assertFalse(runner.iteration_limit_reached(23,23))
        self.assertTrue(runner.iteration_limit_reached(24,23))
        self.assertTrue(runner.iteration_limit_reached(1,0))
        for value in (-1,True,'unlimited'):
            with self.subTest(value=value),self.assertRaises(ValueError):runner.iteration_limit_reached(1,value)


class UnlimitedLoopTests(unittest.TestCase):
    setUp=test_goals.GoalTests.setUp
    draft=test_goals.GoalTests.draft
    approve=test_goals.GoalTests.approve
    invoke=test_goals.GoalTests.invoke

    def test_no_progress_still_pauses_with_unlimited_iterations(self):
        self.approve()
        self.state['settings']['limits'].update(iteration_ceiling=None,no_progress_batches=1)
        self.state.update(iteration=9999,no_progress_batches=1)
        self.assertEqual(2,self.invoke())
        # The no-progress stop now waits on an operational AutoResolver request.
        assert_operational_wait(self,self.state,'PAUSED_NO_PROGRESS')

    def test_unlimited_reaches_dispatch_without_completing(self):
        self.approve()
        self.state['settings']['limits']['iteration_ceiling']=None
        self.state['iteration']=9999
        calls=[]
        def launch(**kwargs):
            calls.append(kwargs)
            raise s.Paused('PAUSED_TEST_DISPATCH','fixture')
        self.assertEqual(2,self.invoke(role=launch))
        self.assertEqual(1,len(calls))
        self.assertEqual('PAUSED_TEST_DISPATCH',self.state['status'])


class UnlimitedSubprocessTests(unittest.TestCase):
    setUp=test_subprocess.SubprocessFlow.setUp
    launch=test_subprocess.SubprocessFlow.launch
    saved=test_subprocess.SubprocessFlow.saved
    new_run_engine_args=('--engine','codex')

    def test_cli_new_run_defaults_to_unlimited(self):
        self.env['AUTOCODE_FIXTURE_MODE']='no-human'
        self.launch(['Build greeting','--chat'],0,answers='CLI\nyes\n')
        _,state=self.saved()
        self.assertIsNone(state['settings']['limits']['iteration_ceiling'])
        self.assertEqual('runner_default',state['settings']['budget_origins']['iteration_ceiling'])

    def test_cli_new_run_bounds_time_while_iterations_stay_unlimited(self):
        self.env['AUTOCODE_FIXTURE_MODE']='no-human'
        self.launch(['Build greeting','--chat'],0,answers='CLI\nyes\n')
        _,state=self.saved()
        limits,origins=state['settings']['limits'],state['settings']['budget_origins']
        self.assertEqual(43200,limits['max_seconds'])
        self.assertEqual(3600,limits['stage_timeout_seconds'])
        # Runner defaults, so AutoResolver may extend each once after verified progress.
        self.assertEqual('runner_default',origins['max_seconds'])
        self.assertEqual('runner_default',origins['stage_timeout_seconds'])
        self.assertEqual('TASK_COMPLETE',state['status'])

    def test_cli_explicit_zero_disables_the_time_limits(self):
        self.env['AUTOCODE_FIXTURE_MODE']='no-human'
        self.launch(['Build greeting','--max-seconds','0','--max-stage-seconds','0','--chat'],0,answers='CLI\nyes\n')
        _,state=self.saved()
        self.assertEqual(0,state['settings']['limits']['max_seconds'])
        self.assertEqual(0,state['settings']['limits']['stage_timeout_seconds'])
        self.assertEqual('user_explicit',state['settings']['budget_origins']['max_seconds'])

    def test_cli_unlimited_is_saved_and_goal_completion_still_works(self):
        self.env['AUTOCODE_FIXTURE_MODE']='no-human'
        self.launch(['Build greeting','--unlimited-iterations','--chat'],0,answers='CLI\nyes\n')
        _,state=self.saved()
        self.assertIsNone(state['settings']['limits']['iteration_ceiling'])
        self.assertEqual('TASK_COMPLETE',state['status'])

    def test_conflicting_flags_fail_before_launch(self):
        result=self.launch(['Build greeting','--max-iterations','2','--unlimited-iterations'],2)
        self.assertIn('cannot be combined',result.stderr)
