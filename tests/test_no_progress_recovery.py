"""Raising an exhausted no-progress bound through the CLI.

A run held at PAUSED_NO_PROGRESS publishes an AutoResolver request. These tests
drive the public commands that answer it: raising the bound on resume, saving it
first, and reasserting it after an informational response. A raised bound must
reach the Builder without resetting the retained count or granting more timeout
recoveries; a bound that still does not admit the count must never launch one.

The held run has the shape of the 2026-10-03 AWS design trial: three planning
timeout recoveries counted as no-progress batches after the plan was approved, and
AutoResolver already evaluating the blocker (docs/bugs/2026-10-05-no-progress-bound-reassertion.md).
"""
import copy
import json
import unittest

import autocode_resolver_human as human
import autocode_support as support
from . import test_subprocess


class NoProgressBoundTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ('--engine', 'codex')

    def hold(self):
        """Approve a plan, stop before the Builder, then hit the no-progress bound of 3."""
        self.env['AUTOCODE_FIXTURE_MODE'] = 'no-human'
        self.launch(['Build greeting', '--chat'], 2, answers='CLI\nno\n')
        self.run, state = self.saved()
        self.launch(['--run-dir', str(self.run), '--approve-goal', state['displayed_goal']], 0)
        self.launch(['--run-dir', str(self.run), '--no-chat', '--pause-after-stage'], 2)
        _, state = self.saved()
        self.assertEqual('terra', state['next_stage'])
        state.update(no_progress_batches=3, automatic_recoveries_since_resume=2,
                     consecutive_timeout_recoveries=0,
                     automatic_timeout_recoveries=[{'stage': 'glm_revise', 'attempt_id': str(i)}
                                                   for i in range(3)])
        support.atomic_json(self.run / 'state.json', state)
        self.args = ['--run-dir', str(self.run), '--no-chat']
        self.launch([*self.args, '--resume-paused'], 2)
        _, held = self.saved()
        # As in the trial, AutoResolver has already evaluated this blocker in the current epoch.
        held['resolver']['attempts'] = {'retained': 2}
        support.atomic_json(self.run / 'state.json', held)
        request = human.current(held)
        self.assertEqual('operational_exhaustion', request['scope'])
        self.assertEqual('PAUSED_NO_PROGRESS', self.origin(held, request)['pause_status'])
        self.assertFalse((self.project / 'greet.py').exists())
        return held, request

    @staticmethod
    def origin(state, request):
        return state['resolver']['human_escalations'][request['request_id']]['identity']['proposal']['origin']

    def view(self):
        return json.loads(self.launch(['--run-dir', str(self.run), '--status'], 0).stdout)['view']

    def inform(self, request):
        self.launch([*self.args, '--resolver-request', request['request_id'],
                     '--resolver-token', request['request_token'], '--resolver-response', 'provide_information',
                     '--resolver-message', 'The finite no-progress bound will be corrected'], 0)
        return self.saved()[1]

    def assert_admitted_without_new_allowance(self, held):
        """The Builder is next; the count, approval and recovery history are those of the hold."""
        _, resumed = self.saved()
        view = self.view()
        self.assertEqual(('RUNNING', 'terra'), (view['status'], view['next_stage']))
        self.assertIsNone(human.current(resumed))
        self.assertEqual(3, resumed['no_progress_batches'])
        self.assertEqual(4, resumed['settings']['limits']['no_progress_batches'])
        self.assertEqual(held['goal_contract'], resumed['goal_contract'])
        self.assertEqual(held['automatic_timeout_recoveries'], resumed['automatic_timeout_recoveries'])
        self.assertEqual(held['automatic_recoveries_since_resume'], resumed['automatic_recoveries_since_resume'])
        self.assertFalse(resumed.get('recovery_grants'))
        self.assertFalse((self.project / 'greet.py').exists())
        return resumed

    def test_raising_the_bound_on_resume_retires_the_request_and_builds(self):
        held, request = self.hold()
        self.launch([*self.args, '--resume-paused', '--no-progress-limit', '4', '--unit', 'autoplanner'], 0)
        resumed = self.assert_admitted_without_new_allowance(held)
        self.assertEqual('superseded', resumed['resolver']['human_escalations'][request['request_id']]['status'])
        self.launch(self.args, 0)
        _, finished = self.saved()
        self.assertEqual('TASK_COMPLETE', finished['status'])
        self.assertTrue((self.project / 'greet.py').is_file())
        self.assertEqual(held['goal_contract'], finished['goal_contract'])
        self.assertEqual(held['automatic_timeout_recoveries'], finished['automatic_timeout_recoveries'])

    def test_a_saved_raise_is_acknowledged_by_a_plain_resume(self):
        held, request = self.hold()
        self.launch([*self.args, '--no-progress-limit', '4'], 2)
        self.assertFalse((self.project / 'greet.py').exists())
        self.launch([*self.args, '--resume-paused', '--unit', 'autoplanner'], 0)
        resumed = self.assert_admitted_without_new_allowance(held)
        self.assertEqual('superseded', resumed['resolver']['human_escalations'][request['request_id']]['status'])

    def test_after_an_informational_response_reasserting_the_saved_bound_admits(self):
        held, request = self.hold()
        answered = self.inform(request)
        self.assertEqual('PAUSED_NO_PROGRESS', answered['status'])
        # Information alone is not an allowance: a plain resume holds and writes nothing.
        before = (self.run / 'state.json').read_bytes()
        self.launch([*self.args, '--resume-paused'], 2)
        self.assertEqual(before, (self.run / 'state.json').read_bytes())
        self.launch([*self.args, '--no-progress-limit', '4'], 2)
        _, corrected = self.saved()
        self.assertEqual('PAUSED_NO_PROGRESS', corrected['status'])
        self.assertIsNone(human.current(corrected))
        self.launch([*self.args, '--resume-paused', '--no-progress-limit', '4', '--unit', 'autoplanner'], 0)
        resumed = self.assert_admitted_without_new_allowance(held)
        self.assertEqual(answered['resolver']['human_responses'], resumed['resolver']['human_responses'])

    def test_reasserting_the_bound_after_a_plain_resume_asked_again_admits(self):
        """The trial's stranded run: information, a saved raise, then a plain resume asked again.

        A plain resume does not acknowledge the saved raise, so it republishes the
        no-progress request. Reasserting the bound used to crash with "Role result belongs
        to another implementation task" and leave the run waiting on that request.
        """
        held, request = self.hold()
        answered = self.inform(request)
        self.launch([*self.args, '--no-progress-limit', '4'], 2)
        self.launch([*self.args, '--resume-paused'], 2)
        _, republished = self.saved()
        again = human.current(republished)
        self.assertNotEqual(request['request_id'], again['request_id'])
        self.assertEqual('PAUSED_NO_PROGRESS', self.origin(republished, again)['pause_status'])
        self.assertFalse((self.project / 'greet.py').exists())
        self.launch([*self.args, '--resume-paused', '--no-progress-limit', '4', '--unit', 'autoplanner'], 0)
        resumed = self.assert_admitted_without_new_allowance(held)
        self.assertEqual('superseded', resumed['resolver']['human_escalations'][again['request_id']]['status'])
        self.assertEqual(answered['resolver']['human_responses'], resumed['resolver']['human_responses'])

    def test_a_bound_that_does_not_admit_the_count_never_launches_the_builder(self):
        held, _ = self.hold()
        for limit, count in ((3, 3), (4, 4), (4, 5), (2, 3)):
            with self.subTest(limit=limit, count=count):
                state = copy.deepcopy(held)
                state['no_progress_batches'] = count
                support.atomic_json(self.run / 'state.json', state)
                self.launch([*self.args, '--resume-paused', '--no-progress-limit', str(limit)], 2)
                _, blocked = self.saved()
                request = human.current(blocked)
                self.assertEqual('operational_exhaustion', request['scope'])
                self.assertEqual('PAUSED_NO_PROGRESS', self.origin(blocked, request)['pause_status'])
                self.assertEqual(count, blocked['no_progress_batches'])
                self.assertEqual(held['goal_contract'], blocked['goal_contract'])
                self.assertEqual('terra', blocked['next_stage'])
                self.assertFalse((self.project / 'greet.py').exists())


if __name__ == '__main__':
    unittest.main()
