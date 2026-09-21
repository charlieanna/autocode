"""All-role model choices use existing engine, approval and session machinery."""
import copy
import sys
from pathlib import Path
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode as runner
import autocode_opencode as oc
import autocode_support as support
import test_planning


class AllRoleModelTests(unittest.TestCase):
    configure_args = test_planning.PlanningTests.configure_args

    def test_all_overrides_use_opencode_without_a_codex_login_dependency(self):
        models = {'glm':'openai/gpt-5.6-sol','astra':'zai-coding-plan/glm-5.3',
                  'terra':'local/custom:32b','sol':'openai/gpt-6-astra'}
        with patch.object(oc, 'local_settings', return_value={'engine':'opencode'}), \
             patch.object(support, 'local_settings', side_effect=AssertionError('No Codex role selected')):
            settings = runner.configure(self.configure_args(**{role+'_model':model for role,model in models.items()}),
                                        {'workspace':'/fixture','iteration':0})
            self.assertEqual({'opencode'}, set(settings['transport_identities']))
            for role, model in models.items():
                self.assertEqual('opencode', settings['roles'][role]['engine'])
                self.assertEqual(model, settings['roles'][role]['model'])
                self.assertIsNone(settings['roles'][role]['provider'])
            state = {'settings':settings,'sessions':{role:'saved-'+role for role in models},
                     'next_stage':'sol','goal_contract':{'revision':9,'approval_status':'approved'}}
            before = copy.deepcopy(state)
            self.assertEqual(settings, runner.configure(self.configure_args(), state))
            self.assertEqual(before, state)
            with patch.object(oc, 'check_subscription_routes') as guard:
                runner.check_joint_transports(state, Path('/fixture'))
            guard.assert_called_once_with(settings['roles'], Path('/fixture'))

    def test_explicit_override_changes_only_the_selected_role_on_a_new_run(self):
        with patch.object(oc, 'local_settings', return_value={'engine':'opencode'}), \
             patch.object(support, 'local_settings', return_value={'auth_mode':'ChatGPT'}):
            args = self.configure_args(sol_model='zai-coding-plan/glm-5.3')
            settings = runner.configure(args, {'workspace':'/fixture','iteration':0})
        self.assertEqual('opencode', settings['roles']['astra']['engine'])
        self.assertEqual('openai/gpt-6-astra', settings['roles']['astra']['model'])
        self.assertEqual('opencode', settings['roles']['sol']['engine'])
        self.assertEqual('zai-coding-plan/glm-5.3', settings['roles']['glm']['model'])
        self.assertEqual('zai-coding-plan/glm-5.3', settings['roles']['terra']['model'])
        state = {'settings':settings,'sessions':{'astra':'opencode-astra','sol':'opencode-sol'}}
        before = copy.deepcopy(state)
        for override in ({'astra_model':'gpt-6-astra'}, {'sol_model':'gpt-5.6-sol'}):
            with self.assertRaises(ValueError):
                runner.configure(self.configure_args(**override), state)
            self.assertEqual(before, state)


class AllRoleSubprocessTests(unittest.TestCase):
    setUp = test_planning.JointFlow.setUp
    launch = test_planning.JointFlow.launch
    saved = test_planning.JointFlow.saved
    prepare = test_planning.JointFlow.prepare
    new_run_engine_args = ()

    def test_selected_roles_survive_approval_implementation_validation_and_resume(self):
        self.prepare('rework')
        models = {'glm':'openai/gpt-5.6-sol','astra':'zai-coding-plan/glm-5.3',
                  'terra':'openai/gpt-5.6-terra','sol':'openai/gpt-6-astra'}
        flags = [arg for role, model in models.items() for arg in ('--'+role+'-model',model)]
        self.launch(['Build a greeting tool','--no-chat',*flags], 2)
        run, state = self.saved()
        args = ['--run-dir',str(run)]
        self.assertEqual('WAITING_FOR_USER', state['status'])
        self.assertFalse((self.project/'greet.py').exists())
        self.launch([*args,'--answer','Q1=CLI'], 0)
        self.launch([*args,'--no-chat'], 2)
        state = self.saved()[1]
        self.assertEqual('AWAITING_GOAL_APPROVAL', state['status'])
        self.assertFalse((self.project/'greet.py').exists())
        self.launch([*args,'--approve-goal',state['displayed_goal']], 0)
        # Reopen every saved stage boundary. This tests actual resume routing
        # and keeps the harness's 30-second bound per stage, not six stages.
        stages = ['terra','sol','astra_review','terra','sol','astra_review']
        for index, stage in enumerate(stages):
            before_stage = self.saved()[1]
            self.assertEqual(stage, before_stage['next_stage'])
            self.launch([*args,'--no-chat','--pause-after-stage',*(['--resume-paused'] if index else [])],
                        0 if index==len(stages)-1 else 2)
            after_stage = self.saved()[1]
            self.assertEqual(len(before_stage['stages'])+1, len(after_stage['stages']))
        final = self.saved()[1]
        self.assertEqual('COMPLETE', final['phase'])
        for stage in final['stages']:
            self.assertEqual('opencode', stage['engine'])
            self.assertEqual('opencode', stage['command'][0])
            self.assertEqual(models[stage['role']], stage['command'][stage['command'].index('--model')+1])
        builds = [row for row in final['stages'] if row['stage']=='terra']
        audits = [row for row in final['stages'] if row['stage']=='sol']
        self.assertEqual(2, len(builds))
        self.assertEqual(2, len(audits))
        self.assertEqual(final['sessions']['terra'], builds[1]['expected_session'])
        self.assertEqual(final['sessions']['sol'], audits[1]['expected_session'])
        self.assertNotEqual(final['sessions']['sol'], final['sessions']['terra'])
        for stage in final['stages']:
            config = __import__('json').loads(Path(stage['output']).with_suffix('.opencode.json').read_text())
            agent = stage['command'][stage['command'].index('--agent')+1]
            policy = config['agent'][agent]['permission']
            if stage['role']!='terra':
                self.assertEqual('deny', policy['edit'])
            if stage['stage'] in ('astra_discovery','astra_challenge','glm_revise','astra_finalize'):
                self.assertEqual('deny', policy['bash'])
        before = (run/'state.json').read_bytes()
        self.launch([*args,'--status'], 0)
        self.assertEqual(before, (run/'state.json').read_bytes())


if __name__ == '__main__':
    unittest.main()
