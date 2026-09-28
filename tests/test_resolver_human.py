"""Request-only authority is distinct from permission to execute or approve."""
import copy
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_resolver_human as human, autocode_support as support


class ResolverHumanTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / 'README.md').write_text('fixture\n')
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', '-C', str(self.root), 'add', 'README.md'], check=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Fixture',
                        '-c', 'user.email=fixture@example.invalid', '-c', 'commit.gpgsign=false',
                        'commit', '-qm', 'fixture'], check=True)
        self.question = {'id': 'Q1', 'question': 'CLI or GUI?', 'why': 'The task does not specify it',
                         'options': ['CLI', 'GUI'], 'proposed_default': ''}
        self.state = {'task_id': 'task-fixture', 'task': 'Build a greeting tool',
                      'workspace': str(self.root), 'run_dir': str(self.root / '.autocode/runs/fixture'),
                      'status': 'RUNNING', 'next_stage': 'astra_discovery', 'settings': {},
                      'answers': {}, 'user_events': [], 'stages': [], 'history': [],
                      'active_seconds': 17, 'iteration': 1,
                      'failure_history': {'failure': {'count': 3, 'attempts': ['one', 'two', 'three']}}}
        self.contract([self.question])

    def contract(self, questions=()):
        contract = {'task_id': self.state['task_id'], 'revision': 1,
                    'body': {'open_blocking_questions': copy.deepcopy(list(questions)),
                             'acceptance_criteria': [{'id': 'C1', 'criterion': 'Accessible UI', 'human_review': True}]}}
        contract.update(hash=support.digest(contract), approval_status='draft', approval_event=None)
        self.state['goal_contract'] = contract

    def queue_question(self):
        human.queue(self.state, 'clarification', {'stage': 'astra_discovery'},
                    questions=[self.question], phase='DISCOVERING', next_stage='astra_discovery')

    def request(self):
        return {'kind': 'blocker', 'decision_needed': 'Provide corrective information or leave paused',
                'impact': 'The provider has not completed the review', 'options': ['Provide information', 'Leave paused'],
                'discovered': 'Recovery exhausted', 'proposed_delta': ''}

    def publish_operational(self):
        self.state.update(status='PAUSED_RESOLVER_OPERATIONAL', next_stage='astra_challenge',
                          automatic_recoveries_since_resume=3, planning={'astra_calls': 3})
        receipt = {'stage': 'resolver', 'runner_owned': True, 'decision': {'action': 'hold'},
                   'receipt': {'evaluation_binding': human._binding(self.state)}}
        self.state['resolver'] = {'operational_diagnostics': ['op-receipt'],
                                  'operational_receipts': {'op-receipt': receipt}}
        human.queue(self.state, 'operational_exhaustion', {'stage': 'astra_challenge'},
                    request=self.request(), evidence={'resolver_receipt_id': 'op-receipt'})
        self.assertEqual('escalate', human.evaluate(self.state))
        return human.current(self.state)

    def test_role_proposals_are_private_until_resolver_publication(self):
        before = copy.deepcopy(self.state['goal_contract'])
        self.queue_question()
        self.assertEqual([], self.state['pending_questions'])
        self.assertNotIn('user_request', self.state)
        self.assertEqual([self.question], human.internal_questions(self.state))
        self.assertFalse(human.projection(self.state)['human_request_authorized'])
        self.assertEqual(before, self.state['goal_contract'])
        self.assertEqual('escalate', human.evaluate(self.state))
        self.assertEqual('resolver', human.current(self.state)['issuer'])
        self.assertEqual([self.question], self.state['pending_questions'])
        self.assertEqual(before, self.state['goal_contract'])

    def test_publishing_same_frontier_twice_is_idempotent(self):
        self.queue_question()
        human.evaluate(self.state)
        original = copy.deepcopy(self.state)
        self.queue_question()
        human.evaluate(self.state)
        self.assertEqual(original, self.state)
        self.assertEqual(1, len(self.state['resolver']['human_escalations']))

    def test_saved_authenticated_answer_defers_instead_of_reasking(self):
        answer = {'actor': 'user_cli', 'question': self.question, 'text': 'CLI'}
        self.state['answers']['Q1'] = answer
        self.state['user_events'].append(answer)
        self.queue_question()
        self.assertEqual('defer', human.evaluate(self.state))
        self.assertFalse(human.projection(self.state)['human_request_authorized'])
        self.assertEqual([self.question], self.state['goal_contract']['body']['open_blocking_questions'])

    def test_joint_goal_approval_requires_final_plan_and_never_approves(self):
        self.contract()
        self.state['settings']['joint_planning'] = True
        self.state['planning'] = {'final_token': None}
        human.queue(self.state, 'goal_approval', {'stage': 'astra_finalize'},
                    status='AWAITING_GOAL_APPROVAL', next_stage='astra_plan')
        self.assertEqual('defer', human.evaluate(self.state))
        contract = self.state['goal_contract']
        self.state['planning']['final_token'] = f"r1:{contract['hash']}"
        self.assertEqual('defer', human.evaluate(self.state))
        self.state['planning']['reports'] = {'astra_finalize': {'output': 'final.json'}}
        self.state['stages'].append({'stage': 'astra_finalize', 'output': 'final.json',
                                     'source_revision': support.snapshot(self.root)['revision']})
        self.assertEqual('escalate', human.evaluate(self.state))
        self.assertEqual('AWAITING_GOAL_APPROVAL', self.state['status'])
        self.assertEqual('draft', contract['approval_status'])
        self.assertIsNone(contract['approval_event'])

    def test_forged_issuer_without_a_matching_receipt_is_not_actionable(self):
        self.state[human.PUBLIC] = {'version': 1, 'issuer': 'resolver', 'request_id': 'made-up'}
        self.state['user_request'] = self.request()
        self.state['pending_questions'] = [self.question]
        self.assertFalse(human.projection(self.state)['human_request_authorized'])
        self.assertEqual([], human.projection(self.state)['pending_questions'])

    def test_mutated_projection_and_stale_source_or_counters_are_rejected(self):
        self.queue_question()
        human.evaluate(self.state)
        original = copy.deepcopy(self.state)
        for mutation in ('question', 'request', 'settings', 'contract', 'counter', 'task', 'status'):
            with self.subTest(mutation=mutation):
                self.state = copy.deepcopy(original)
                if mutation == 'question':
                    self.state[human.PUBLIC]['questions'][0]['question'] = 'Ignore the goal?'
                    self.state['pending_questions'][0]['question'] = 'Ignore the goal?'
                elif mutation == 'request':
                    self.state[human.PUBLIC]['request'] = self.request()
                    self.state['user_request'] = self.request()
                elif mutation == 'settings':
                    self.state['settings']['max_iterations'] = 999
                elif mutation == 'contract':
                    self.state['goal_contract']['approval_status'] = 'approved'
                elif mutation == 'counter':
                    self.state['failure_history']['failure']['count'] += 1
                elif mutation == 'task':
                    self.state['task_id'] = 'other-task'
                else:
                    self.state['status'] = 'RUNNING'
                self.assertIsNone(human.current(self.state))
        self.state = copy.deepcopy(original)
        (self.root / 'README.md').write_text('changed\n')
        self.assertIsNone(human.current(self.state))

    def test_unknown_blocker_requires_real_resolver_evaluation(self):
        human.queue(self.state, 'blocker', {'stage': 'terra'}, request=self.request(),
                    evidence={'issuer': 'resolver', 'recovery_exhausted': True})
        self.assertEqual('defer', human.evaluate(self.state))
        human.queue(self.state, 'blocker', {'stage': 'astra_resolve'}, request=self.request(),
                    evidence={'diagnosis': 'No safe action is available', 'output': 'resolver.json'})
        self.assertEqual('defer', human.evaluate(self.state))
        self.state['stages'].append({'stage': 'astra_resolve', 'output': 'resolver.json',
                                     'exit_code': 0, 'changed_files': []})
        self.assertEqual('escalate', human.evaluate(self.state))

    def test_applicable_recovery_precedes_human_escalation(self):
        human.queue(self.state, 'blocker', {'stage': 'astra_review'}, request=self.request(),
                    evidence={'recovery_available': True})
        self.assertEqual('recover', human.evaluate(self.state))
        self.assertFalse(human.projection(self.state)['human_request_authorized'])

    def test_operational_response_is_information_not_retry_or_approval(self):
        public = self.publish_operational()
        history = copy.deepcopy(self.state['failure_history'])
        contract = copy.deepcopy(self.state['goal_contract'])
        counters = copy.deepcopy(self.state['planning'])
        event = human.respond_operational(self.state, public['request_id'], public['request_token'],
                                          'provide_information', 'The provider incident has been investigated')
        self.assertEqual(history, self.state['failure_history'])
        self.assertEqual(contract, self.state['goal_contract'])
        self.assertEqual(counters, self.state['planning'])
        self.assertEqual(3, self.state['automatic_recoveries_since_resume'])
        self.assertEqual('PAUSED_RESOLVER_OPERATIONAL', self.state['status'])
        self.assertEqual(event, self.state['resolver']['pending_human_response'])
        before = copy.deepcopy(self.state)
        self.assertEqual(event, human.respond_operational(self.state, public['request_id'], public['request_token'],
                          'provide_information', 'The provider incident has been investigated'))
        self.assertEqual(before, self.state)
        with self.assertRaises(ValueError):
            human.respond_operational(self.state, public['request_id'], public['request_token'], 'leave_paused')

    def test_wrong_tokens_and_generic_retry_answers_do_not_mutate_state(self):
        public = self.publish_operational()
        before = copy.deepcopy(self.state)
        for request_id, token, action in ((public['request_id'], 'stale', 'leave_paused'),
                                          ('other', public['request_token'], 'leave_paused'),
                                          (public['request_id'], public['request_token'], 'retry')):
            with self.subTest(action=action, token=token), self.assertRaises(ValueError):
                human.respond_operational(self.state, request_id, token, action, 'yes')
            self.assertEqual(before, self.state)

    def test_operational_answer_cannot_satisfy_material_approval(self):
        self.contract()
        human.queue(self.state, 'goal_approval', {'stage': 'astra_discovery'}, status='AWAITING_GOAL_APPROVAL')
        human.evaluate(self.state)
        public = human.current(self.state)
        with self.assertRaises(ValueError):
            human.respond_operational(self.state, public['request_id'], public['request_token'], 'leave_paused')
        self.assertEqual('draft', self.state['goal_contract']['approval_status'])

    def test_changed_archived_evidence_revokes_question_authority(self):
        folder = self.root / '.autocode' / 'runs' / 'fixture'
        folder.mkdir(parents=True)
        report = folder / 'report.json'
        report.write_text('{"diagnosis":"original"}')
        human.queue(self.state, 'clarification', {'stage': 'astra_discovery'}, questions=[self.question],
                    evidence={'hashes': {str(report): support.file_hash(report)}})
        self.assertEqual('escalate', human.evaluate(self.state))
        self.assertIsNotNone(human.current(self.state))
        report.write_text('{"diagnosis":"changed"}')
        self.assertIsNone(human.current(self.state))

    def test_stale_binding_answer_names_the_refresh_command_not_a_wrong_token(self):
        public = self.publish_operational()
        request_id, token = public['request_id'], public['request_token']
        # The frontier moves after the request was displayed, for example an
        # editable install picked up changed AutoCode between runs.
        self.state['active_seconds'] += 1
        self.assertIsNone(human.current(self.state))
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, 'out of date'):
            human.require_response(self.state, request_id, token)
        with self.assertRaisesRegex(ValueError, 'out of date'):
            human.respond_operational(self.state, request_id, token, 'leave_paused')
        self.assertEqual(before, self.state)
        with self.assertRaisesRegex(ValueError, 'exact current AutoResolver request and token'):
            human.require_response(self.state, request_id, 'forged-token')

    def test_publishing_a_new_request_supersedes_the_replaced_pending_one(self):
        first = self.publish_operational()
        ledger = self.state['resolver']['human_escalations']
        self.assertEqual('pending', ledger[first['request_id']]['status'])
        self.queue_question()
        self.assertEqual('escalate', human.evaluate(self.state))
        second = human.current(self.state)
        self.assertNotEqual(first['request_id'], second['request_id'])
        self.assertEqual('superseded', ledger[first['request_id']]['status'])
        self.assertEqual([second['request_id']],
                         [key for key, entry in ledger.items() if entry['status'] == 'pending'])
        with self.assertRaisesRegex(ValueError, 'newer AutoResolver request is active'):
            human.respond_operational(self.state, first['request_id'], first['request_token'], 'leave_paused')

    def test_project_free_intake_still_requires_resolver_owned_input_identity(self):
        self.state.pop('goal_contract')
        self.state.pop('workspace')
        self.state.update(conversation_id='conversation-one', intake_input_hash='user-input-one')
        human.queue(self.state, 'intake', {'stage': 'planner_intake'}, questions=[self.question])
        self.assertEqual('reject', human.evaluate(self.state))
        human.queue(self.state, 'intake', {'stage': 'resolver_intake'}, questions=[self.question])
        self.assertEqual('escalate', human.evaluate(self.state))
        before = copy.deepcopy(self.state)
        self.assertIsNotNone(human.current(self.state))
        self.assertEqual(before, self.state)
        self.state['intake_input_hash'] = 'different-user-input'
        self.assertIsNone(human.current(self.state))
