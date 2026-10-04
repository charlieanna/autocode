"""Public recovery descriptions do not grant authority or omit a next step."""
import ast
import copy
from pathlib import Path
import unittest

import autocode_recovery_view as recovery
import autocode_run_view as run_view


class RecoveryViewTests(unittest.TestCase):
    def state(self, status='PAUSED_REQUESTED', **fields):
        return {'status': status, 'iteration': 4, 'next_stage': 'terra',
                'current_task': {'id': 't', 'milestone_id': 'M1', 'kind': 'implement'},
                'settings': {'builder_retry': {'enabled': True}, 'roles': {
                    'terra': {'model': 'p/builder', 'reasoning_effort': 'high', 'model_pinned': True}}},
                'goal_contract': {'hash': 'approved', 'revision': 1}, 'stages': [], **fields}

    def card(self, state):
        return run_view.view(state)['recovery']

    def test_every_declared_stop_and_future_status_has_three_parts_and_a_next_step(self):
        # Coverage grows automatically with the runtime's status vocabulary.
        statuses = {'PAUSED_FUTURE_UNKNOWN', 'RESOLVER_PENDING'}
        for path in Path(__file__).resolve().parents[1].joinpath('tools').glob('*.py'):
            if path.name == 'autocode_recovery_view.py':
                continue
            for node in ast.walk(ast.parse(path.read_text())):
                if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                        and node.value.isupper() and not node.value.endswith('_')
                        and node.value.startswith(('PAUSED_', 'BLOCKED', 'FAILED', 'WAITING_', 'AWAITING_'))):
                    statuses.add(node.value)
        self.assertGreater(len(statuses), 50)
        for status in statuses:
            with self.subTest(status=status):
                state = self.state(status)
                before = copy.deepcopy(state)
                result = self.card(state)
                for field in ('what_happened', 'retained', 'actions'):
                    self.assertTrue(result[field])
                self.assertIn('inspect', [row['kind'] for row in result['actions']])
                self.assertTrue({'feedback', 'decision', 'new_conversation'} & {row['kind'] for row in result['actions']})
                self.assertNotIn('terra', result['what_happened'])
                self.assertEqual(before, state)
        for status in recovery.ACTIVE:
            self.assertIsNone(self.card(self.state(status)))

    def test_token_changes_with_attempt_settings_contract_or_history_but_not_poll_time(self):
        state = self.state()
        original = recovery.token(state)
        state['updated_at'] = 'later'
        self.assertEqual(original, recovery.token(state))
        for field, value in [('workspace', '/other'), ('run_dir', '/other/run'), ('status', 'RUNNING'), ('iteration', 5), ('current_task', {'id': 'different'}),
                             ('settings', {'roles': {'terra': {'model': 'other'}}}),
                             ('goal_contract', {'hash': 'new'}), ('stages', [{'finished_at': 'now'}]),
                             ('user_events', [{'kind': 'feedback'}]), ('failure_history', {'new': {'count': 1}})]:
            changed = {**state, field: value}
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'pause changed'):
                recovery.require_token(changed, original)
        recovery.require_token(state, original)
        recovery.require_token(state, None)  # ordinary CLI callers retain compatibility
        with self.assertRaisesRegex(ValueError, 'pause changed'):
            recovery.require_token(state, '')

    def test_exact_serial_builder_action_preserves_pins_history_and_scope(self):
        state = self.state('PAUSED_BUILDER_RETRY_LIMIT')
        key = recovery.builder_policy.key(state)
        state['builder_retries'] = {key: {'action': 'pause', 'failures': ['failure-a', 'failure-b']}}
        before = copy.deepcopy(state)
        action = next(row for row in self.card(state)['actions'] if row['kind'] == 'retry_builder')
        self.assertEqual(['M1'], action['milestone_ids'])
        self.assertEqual('retry_builder:M1', action['id'])
        self.assertNotIn('stronger', action['effect'])
        self.assertEqual(before, state)
        for field, value in [('active_stage', {'stage': 'terra'}), ('active_runner_check', {'stage': 'regression_proof'}),
                             ('pending_report_repair', {'stage': 'terra'}), ('parent_run', '/parent')]:
            with self.subTest(field=field):
                self.assertNotIn('retry_builder', [row['kind'] for row in self.card({**state, field: value})['actions']])

    def test_reconciled_operator_stop_never_offers_any_execution_or_feedback(self):
        state = self.state('PAUSED_INTERVENTION', applied_interventions=[{'id': 'stop', 'kind': 'stop'}])
        card = self.card(state)
        self.assertEqual('stopped', card['category'])
        self.assertEqual(['inspect', 'new_conversation'], [row['kind'] for row in card['actions']])

    def test_interruption_is_exact_abandon_without_resume_and_missing_attempt_has_no_guess(self):
        state = self.state('PAUSED_PROVIDER_UNCERTAIN', active_stage={
            'iteration': 4, 'stage': 'terra', 'output': '/run/iterations/004/builder-02.json'})
        actions = self.card(state)['actions']
        selected = next(row for row in actions if row['kind'] == 'abandon')
        self.assertEqual('004/builder-02', selected['attempt_id'])
        self.assertIn('Resume is a separate action', selected['effect'])
        self.assertNotIn('resume', [row['kind'] for row in actions])
        state['active_stage'] = {}
        self.assertEqual(['inspect', 'feedback'], [row['kind'] for row in self.card(state)['actions']])

    def test_saved_internal_question_does_not_claim_human_authority_or_answer_it(self):
        state = self.state('WAITING_FOR_USER', pending_questions=[{'id': 'q', 'question': 'Which format?'}])
        card = self.card(state)
        self.assertEqual('request', card['category'])
        self.assertEqual('Review checkpoint', card['title'])
        self.assertNotIn('needed', card['what_happened'])
        self.assertEqual(['inspect', 'decision', 'feedback'], [row['kind'] for row in card['actions']])

    def test_repeated_failure_groups_keep_sources_counts_and_missing_details_honest(self):
        state = self.state('PAUSED_REPEATED_FAILURE', failure_history={
            'a': {'identity': {'stage': 'terra', 'artifact_hash': 'first'}, 'count': 3,
                  'attempts': ['1','2','3'], 'last_error': 'assertion failed', 'last_seen': '2026-10-01'},
            'b': {'identity': {'stage': 'sol', 'artifact_hash': 'second'}, 'count': 1,
                  'attempts': ['4'], 'last_seen': '2026-10-02'}})
        result = self.card(state)
        self.assertEqual(['b','a'], [row['id'] for row in result['failure_groups']])
        self.assertEqual('Builder', result['failure_groups'][1]['role'])
        self.assertEqual(3, result['failure_groups'][1]['count'])
        self.assertIsNone(result['failure_groups'][0]['last_reason'])
        self.assertIn('retry_failed_stage', [row['kind'] for row in result['actions']])

    def test_parallel_retry_targets_only_failed_members_and_never_a_running_batch(self):
        state = self.state('PAUSED_ORCHESTRATOR_WORKER', next_stage='orchestrator', orchestration_batch={
            'status': 'BUILDING', 'contract_hash': 'approved', 'workers': [
                {'milestone_id': 'M1', 'status': 'BUILT'}, {'milestone_id': 'M2', 'status': 'INTERRUPTED'},
                {'milestone_id': 'M3', 'status': 'SERIAL_ESCALATION'}]})
        actions = [row for row in self.card(state)['actions'] if row['kind'] == 'retry_builder']
        self.assertEqual([['M2']], [row['milestone_ids'] for row in actions])
        state['orchestration_batch']['workers'][0]['status'] = 'RUNNING'
        self.assertFalse(any(row['kind'] == 'retry_builder' for row in self.card(state)['actions']))

    def test_job_report_and_dependency_actions_are_specific(self):
        state = self.state('PAUSED_JOB_FAILURE')
        selected = recovery.project(state, {'kind':'retry_job','job_retry_token':'job-token'})['actions'][1]
        self.assertEqual(('retry_job', 'job-token'), (selected['kind'], selected['job_retry_token']))
        selected = recovery.project(self.state('PAUSED_REPEATED_FAILURE'), {'retry_report_attempt':'004/validator-01'})['actions'][1]
        self.assertEqual(('retry_report', '004/validator-01'), (selected['kind'], selected['attempt_id']))
        actions = self.card(self.state('WAITING_FOR_DEPENDENCY'))['actions']
        self.assertEqual(['inspect', 'feedback'], [row['kind'] for row in actions])
