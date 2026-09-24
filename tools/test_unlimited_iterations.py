"""Unlimited iterations is explicit and does not disable other limits."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from . import test_goals, test_autocode, test_subprocess
import autocode as runner
import autocode_support as s


class UnlimitedTests(unittest.TestCase):
    setUp=test_autocode.RetrofitTest.setUp

    def args(self,**kwargs):
        return SimpleNamespace(astra_model=None,terra_model=None,sol_model=None,reasoning_effort=None,
            headroom=None,context_soft_tokens=None,rotate_after_input_tokens=None,**kwargs)

    def test_explicit_unlimited_preserves_all_other_settings(self):
        self.state['settings']['limits']={'iteration_ceiling':23,'max_seconds':1800,'max_reported_tokens':100,
                                         'no_progress_batches':3,'stage_timeout_seconds':900,
                                         'idle_timeout_seconds':75,'tool_timeout_seconds':1200}
        original=copy.deepcopy(self.state)
        result=runner.configure(self.args(unlimited_iterations=True),self.state)
        expected=copy.deepcopy(original['settings']);expected['limits']['iteration_ceiling']=None
        expected['report_repair']={'max_attempts':2}
        expected['provider']='opencode'
        expected['roles']['completion']={**expected['roles']['astra'],
            'model':runner.DEFAULT_ROLE_MODELS['completion'],
            'reasoning_effort':runner.opencode.DEFAULT_REASONING_EFFORTS['completion']}
        self.assertEqual(expected,result)
        self.assertEqual(original,self.state)

    def test_omitted_flag_preserves_unlimited_on_resume(self):
        self.state['settings']['limits']={'iteration_ceiling':None}
        self.assertIsNone(runner.configure(self.args(),self.state)['limits']['iteration_ceiling'])

    def test_explicit_ceiling_restores_cap(self):
        self.state['settings']['limits']={'iteration_ceiling':None}
        self.assertEqual(30,runner.configure(self.args(max_iterations=30),self.state)['limits']['iteration_ceiling'])

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
        self.assertEqual('PAUSED_NO_PROGRESS',self.state['status'])

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

    def test_cli_unlimited_is_saved_and_goal_completion_still_works(self):
        self.env['AUTOCODE_FIXTURE_MODE']='no-human'
        self.launch(['Build greeting','--unlimited-iterations','--chat'],0,answers='CLI\nyes\n')
        _,state=self.saved()
        self.assertIsNone(state['settings']['limits']['iteration_ceiling'])
        self.assertEqual('TASK_COMPLETE',state['status'])

    def test_conflicting_flags_fail_before_launch(self):
        result=self.launch(['Build greeting','--max-iterations','2','--unlimited-iterations'],2)
        self.assertIn('cannot be combined',result.stderr)
