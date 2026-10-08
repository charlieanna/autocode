"""A blocker AutoResolver asks a person names its response, and a contract decision its path (#675).

A live run (feature-timesheet-by-project, 2026-10-07) proved a contradiction in the approved
criteria and asked the person to approve a correction. The status view said `answer`, the CLI
refused --answer, --resolver-response was accepted and then every resume held without naming
--edit-goal, the one path that applied the decision. The view logic is tested without processes;
the CLI path with real processes and the fake provider, no sleeps.
"""
import copy
import json
import unittest

import autocode as runner
import autocode_recovery_limits as recovery_limits
import autocode_resolver_human as human
import autocode_run_view as run_view
from . import test_resolver_human, test_subprocess

COMMAND = recovery_limits.RESPONSE_COMMAND
ADVICE = recovery_limits.CONTRACT_DECISION_ADVICE


def blocker_request():
    return {'kind': 'blocker',
            'decision_needed': 'Approve correcting the AC1 total-row string from ten spaces to twelve?',
            'impact': 'Two acceptance tests fail; no implementation can satisfy both the string and the layout rule',
            'options': ['Approve the correction', 'Keep the string and revise the layout requirement'],
            'discovered': 'The approved AC1 string contradicts the approved unchanged renderer',
            'proposed_delta': ''}


def publish_blocker(state):
    """Publish a Resolver diagnosis that asks a person, as the live run did."""
    human.queue(state, 'blocker', {'stage': 'astra_resolve'}, request=blocker_request(),
                evidence={'diagnosis': 'Proven contract contradiction', 'output': 'iterations/001/resolver-01.json'})
    state['stages'].append({'stage': 'astra_resolve', 'output': 'iterations/001/resolver-01.json',
                            'exit_code': 0, 'changed_files': []})
    assert human.evaluate(state) == 'escalate'
    return human.current(state)


class BlockerNeedTests(unittest.TestCase):
    setUp = test_resolver_human.ResolverHumanTests.setUp
    contract = test_resolver_human.ResolverHumanTests.contract
    queue_question = test_resolver_human.ResolverHumanTests.queue_question
    request = test_resolver_human.ResolverHumanTests.request
    publish_operational = test_resolver_human.ResolverHumanTests.publish_operational

    def needs(self):
        return run_view.view(self.state)['needs']

    def test_a_blocker_names_the_response_as_its_action(self):
        self.state['status'] = 'PAUSED_RESOLVER'
        publish_blocker(self.state)
        need = self.needs()
        self.assertEqual(('answer', 'blocker', COMMAND), (need['kind'], need['resolver_scope'], need['action']))
        self.assertTrue(need['resolver_request_id'] and need['resolver_token'])

    def test_an_operational_request_names_the_same_response(self):
        self.contract()
        self.publish_operational()
        need = self.needs()
        self.assertEqual(('answer', 'operational_exhaustion', COMMAND),
                         (need['kind'], need['resolver_scope'], need['action']))

    def test_a_requirements_question_keeps_its_answer_path(self):
        self.queue_question()
        self.assertEqual('escalate', human.evaluate(self.state))
        need = self.needs()
        self.assertEqual(('answer', 'clarification'), (need['kind'], need['resolver_scope']))
        self.assertNotIn('action', need)

    def test_information_on_a_blocker_names_the_contract_path_and_holds(self):
        self.state['status'] = 'PAUSED_RESOLVER'
        public = publish_blocker(self.state)
        contract = copy.deepcopy(self.state['goal_contract'])
        human.respond_operational(self.state, public['request_id'], public['request_token'],
                                  'provide_information', 'Approved: correct AC1 to twelve spaces')
        self.assertEqual('PAUSED_RESOLVER', self.state['status'])
        self.assertTrue(self.state['stop_reason'].startswith(human.RESPONSE_RECEIVED))
        self.assertIn(ADVICE, self.state['stop_reason'])
        self.assertIn('--edit-goal body.json', self.state['stop_reason'])
        self.assertEqual(contract, self.state['goal_contract'], 'information never edits the contract')
        need = self.needs()
        self.assertEqual('resume', need['kind'])
        self.assertIn('--edit-goal body.json', need['reason'])
        human.review_operational_response(self.state)
        self.assertEqual('blocker', human.held_response_scope(self.state))

    def test_leaving_a_blocker_paused_asks_for_nothing(self):
        self.state['status'] = 'PAUSED_RESOLVER'
        public = publish_blocker(self.state)
        human.respond_operational(self.state, public['request_id'], public['request_token'], 'leave_paused')
        self.assertEqual(human.RESPONSE_RECEIVED, self.state['stop_reason'])

    def test_operational_information_is_not_given_the_contract_path(self):
        # An exhausted operational recovery is re-evaluated (#486); a contract edit is not its path.
        self.contract()
        public = self.publish_operational()
        human.respond_operational(self.state, public['request_id'], public['request_token'],
                                  'provide_information', 'The provider incident has been investigated')
        self.assertEqual(human.RESPONSE_RECEIVED, self.state['stop_reason'])
        human.review_operational_response(self.state)
        self.assertEqual('operational_exhaustion', human.held_response_scope(self.state))
        self.assertEqual('', human.contract_decision_advice('operational_exhaustion'))

    def test_no_held_response_has_no_scope(self):
        self.assertIsNone(human.held_response_scope(self.state))
        self.assertIsNone(human.held_response_scope({'resolver': {'human_response_frontier': {'request_id': 'x'}}}))


class BlockerNextActionCLITests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ('--engine', 'codex')

    def blocker_checkpoint(self):
        """A built run whose Resolver diagnosis asks a person, saved as the CLI would find it."""
        self.env['AUTOCODE_FIXTURE_MODE'] = 'no-human'
        self.env['AUTOCODE_FIXTURE_QUOTA_STAGE'] = 'astra_review'
        self.launch(['Build greeting', '--chat'], 2, answers='CLI\nyes\n')
        run, paused = self.saved()
        self.launch(['--run-dir', str(run), '--abandon-stage', runner.attempt_id(paused['active_stage'])], 0)
        self.run, stopped = self.saved()
        stopped.update(status='PAUSED_RESOLVER', phase='PAUSED_OR_BLOCKED',
                       stop_reason='AutoResolver could not resolve the contradiction')
        publish_blocker(stopped)
        runner.write_json(self.run / 'state.json', stopped)
        return self.status()

    def status(self):
        return json.loads(self.launch(['--run-dir', str(self.run), '--status'], 0).stdout)['view']

    def test_the_view_the_refusal_and_the_hold_all_name_the_way_forward(self):
        view = self.blocker_checkpoint()
        need = view['needs']
        self.assertEqual(('answer', 'blocker', COMMAND), (need['kind'], need['resolver_scope'], need['action']))
        question = need['questions'][0]['id']
        state_file = self.run / 'state.json'
        before = state_file.read_bytes()
        result = self.launch(['--run-dir', str(self.run), '--resolver-token', need['resolver_token'],
                              '--answer', question + '=Approve the correction'], 2)
        self.assertIn('Use --resolver-response', result.stderr)
        self.assertIn(COMMAND, result.stderr)
        self.assertEqual(before, state_file.read_bytes(), 'a refused answer changes nothing')
        self.launch(['--run-dir', str(self.run), '--resolver-request', need['resolver_request_id'],
                     '--resolver-token', need['resolver_token'], '--resolver-response', 'provide_information',
                     '--resolver-message', 'Approved: correct AC1 to twelve spaces', '--no-chat'], 0)
        held = self.status()
        self.assertEqual(('PAUSED_RESOLVER', 'resume'), (held['status'], held['needs']['kind']))
        self.assertIn('--edit-goal body.json', held['needs']['reason'])
        probe = self.root / 'launches.jsonl'
        self.env['AUTOCODE_REGISTRY_LAUNCH_PROBE'] = str(probe)
        result = self.launch(['--run-dir', str(self.run), '--resume-paused', '--no-chat'], 2)
        self.assertIn('retained the human guidance', result.stdout)
        self.assertIn('--edit-goal body.json', result.stdout)
        self.assertIn('--approve-goal', result.stdout)
        self.assertFalse(probe.exists(), 'information launches no provider')


if __name__ == '__main__':
    unittest.main()
