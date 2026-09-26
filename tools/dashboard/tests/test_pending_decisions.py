"""Dashboard decisions are a read-only projection of durable user answers."""
import copy
import unittest
from pathlib import Path

from agent_console import LegacyConsole, pending_decisions


class PendingDecisionTests(unittest.TestCase):
    def setUp(self):
        self.question = {'id': 'Q1', 'question': 'Allow the scoped retry?'}
        self.request = {'kind': 'permission', 'decision_needed': self.question['question'],
                        'impact': 'Retry the current step', 'options': ['Allow', 'Deny']}
        self.answer = {'kind': 'permission_answer', 'actor': 'user_cli', 'text': 'Allow',
                       'question_id': 'Q1', 'question': self.question,
                       'contract_token': 'r2:hash', 'request': self.request}
        self.state = {'status': 'WAITING_FOR_USER', 'pending_questions': [self.question],
                      'user_request': self.request, 'answers': {'Q1': self.answer},
                      'user_events': [self.answer],
                      'goal_contract': {'revision': 2, 'hash': 'hash', 'approval_status': 'approved'}}

    def test_view_hides_answered_question_and_request_without_mutating_checkpoint(self):
        before = copy.deepcopy(self.state)
        console = LegacyConsole([], '/unused-runner', lambda: None)
        self.addCleanup(console.pool.shutdown)
        view = console.view(Path('/workspace'), Path('/workspace/run'), self.state)
        self.assertEqual(view['questions'], [])
        self.assertIsNone(view['user_request'])
        self.assertEqual(view['answers']['Q1']['text'], 'Allow')
        self.assertEqual(self.state, before)

    def test_other_unanswered_questions_remain(self):
        other = {'id': 'Q2', 'question': 'Which output format?'}
        self.state['pending_questions'].append(other)
        questions, request = pending_decisions(self.state)
        self.assertEqual(questions, [other])
        self.assertIsNone(request)

    def test_answer_to_old_question_does_not_hide_a_new_review_request(self):
        review = {'kind': 'human_review', 'decision_needed': 'Review the finished interface'}
        self.state['user_request'] = review
        questions, request = pending_decisions(self.state)
        self.assertEqual(questions, [])
        self.assertEqual(request, review)

    def test_exact_repeated_permission_honors_approval_denial_and_conditions(self):
        self.state['pending_questions'] = [{**self.question, 'id': 'new-id'}]
        for answer in ('Allow', 'Deny', 'Only retry within the current workspace'):
            self.answer['text'] = answer
            self.assertEqual(pending_decisions(self.state), ([], None))

    def test_repeated_permission_cannot_expand_scope_or_ignore_provenance(self):
        self.state['pending_questions'] = [{**self.question, 'id': 'new-id'}]
        for key, value in [('user_events', []), ('goal_contract', {'revision': 3, 'hash': 'new', 'approval_status': 'approved'}),
                           ('user_request', {**self.request, 'impact': 'Write outside the workspace'})]:
            state = {**self.state, key: value}
            self.assertEqual(len(pending_decisions(state)[0]), 1)
        self.answer['actor'] = 'model'
        self.assertEqual(len(pending_decisions(self.state)[0]), 1)

    def test_reused_id_does_not_hide_a_different_question(self):
        self.state['pending_questions'] = [{'id': 'Q1', 'question': 'Approve a different task?'}]
        self.assertEqual(len(pending_decisions(self.state)[0]), 1)

    def test_malformed_and_legacy_data(self):
        self.assertEqual(pending_decisions({'pending_questions': [None, 'bad']}), ([], None))
        self.state['answers'] = {'Q1': None}
        self.assertEqual(len(pending_decisions(self.state)[0]), 1)


if __name__ == '__main__':
    unittest.main()
