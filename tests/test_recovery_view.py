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

    def test_quota_pause_offers_exact_inspected_abandonment_before_separate_resume(self):
        state = self.state('PAUSED_BUDGET', stop_reason='Quota restored; inspect the uncertain attempt.',
                           active_stage={'iteration': 4, 'stage': 'terra',
                                         'output': '/run/iterations/004/builder-02.json'},
                           failure_history={'quota': {'count': 1, 'attempts': ['004/builder-02']}})
        before = copy.deepcopy(state)
        view = run_view.view(state)
        self.assertEqual('004/builder-02', view['needs']['abandon_stage'])
        actions = view['recovery']['actions']
        selected = next(row for row in actions if row['kind'] == 'abandon')
        self.assertEqual(view['needs']['abandon_stage'], selected['attempt_id'])
        self.assertIn('Resume is a separate action', selected['effect'])
        self.assertNotIn('resume', [row['kind'] for row in actions])
        self.assertEqual(before, state)

    def test_quota_abandonment_requires_matching_public_need_and_saved_attempt(self):
        state = self.state('PAUSED_BUDGET', active_stage={
            'iteration': 4, 'stage': 'terra', 'output': '/run/iterations/004/builder-02.json'})
        for need in ({'kind': 'resume'}, {'kind': 'resume', 'abandon_stage': ''},
                     {'kind': 'resume', 'abandon_stage': '004/builder-03'},
                     {'kind': 'resume', 'abandon_stage': '003/builder-02'}):
            with self.subTest(need=need):
                self.assertEqual(['inspect', 'feedback'],
                                 [row['kind'] for row in recovery.project(state, need)['actions']])
        for active in ({}, {'iteration': 4}, {'output': '/run/builder-02.json'}):
            with self.subTest(active=active):
                changed = {**state, 'active_stage': active}
                self.assertNotIn('abandon_stage', run_view.view(changed)['needs'])
                self.assertNotIn('abandon', [row['kind'] for row in self.card(changed)['actions']])
                self.assertNotIn('abandon', [row['kind'] for row in recovery.project(
                    changed, {'kind': 'resume', 'abandon_stage': '004/builder-02'})['actions']])

    def test_quota_recovery_preserves_decision_source_stop_and_report_precedence(self):
        active = {'iteration': 4, 'stage': 'terra', 'output': '/run/iterations/004/builder-02.json'}
        cases = [
            ('answer', self.state('PAUSED_BUDGET', active_stage=active,
                                  pending_questions=[{'id': 'q', 'question': 'Which scope?'}])),
            ('review', self.state('PAUSED_BUDGET', active_stage=active,
                                  pending_questions=[{'id': 'review', 'review_criteria': ['C1'],
                                                      'review_token': 'review-token'}])),
            ('approve_plan', self.state('AWAITING_GOAL_APPROVAL', active_stage=active,
                                        displayed_goal='r1:approved')),
            ('recover_source', self.state('PAUSED_JOB_FAILURE', active_stage=active,
                job_failure={'reason': 'Source identity unavailable', 'stage': 'investigate_bug',
                             'attempt_id': '004/builder-02', 'job_retry_token': 'old-token',
                             'archive': '/run/archive', 'source_identity': None,
                             'write_diagnosis': {}, 'unrestored': ['original source capture']})),
        ]
        for kind, state in cases:
            with self.subTest(kind=kind):
                before = copy.deepcopy(state)
                view = run_view.view(state)
                self.assertEqual(kind, view['needs']['kind'])
                self.assertNotIn('abandon', [row['kind'] for row in view['recovery']['actions']])
                # A stale extra hint never overrides the current request kind.
                need = {**view['needs'], 'abandon_stage': '004/builder-02'}
                self.assertNotIn('abandon', [row['kind'] for row in recovery.project(state, need)['actions']])
                self.assertEqual(before, state)
        stopped = self.state('PAUSED_INTERVENTION', active_stage=active,
                             applied_interventions=[{'id': 'stop', 'kind': 'stop'}])
        self.assertEqual(['inspect', 'new_conversation'], [row['kind'] for row in self.card(stopped)['actions']])
        report = self.state('PAUSED_REPEATED_FAILURE', active_stage=active,
            pending_report_repair={'error': 'Check is not supported by an exact executed Validator event',
                                   'attempts': 2, 'latest_rejected': {'iteration': 4, 'output': '/run/validator-01.json'}})
        view = run_view.view(report)
        self.assertEqual('004/builder-02', view['needs']['abandon_stage'])
        self.assertEqual('004/validator-01', view['needs']['retry_report_attempt'])
        self.assertEqual(['inspect', 'retry_report', 'feedback'], [row['kind'] for row in view['recovery']['actions']])

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

    def test_failure_groups_show_the_streak_and_the_authorized_retries(self):
        # #254: additive fields; an entry saved before streaks existed reports its count.
        state = self.state('PAUSED_REPEATED_FAILURE', failure_history={
            'a': {'identity': {'stage': 'sol', 'artifact_hash': 'r'}, 'count': 4, 'streak': 4,
                  'attempts': ['1', '2', '3', '4'], 'last_seen': '2026-10-02'},
            'b': {'identity': {'stage': 'sol', 'artifact_hash': 'r'}, 'count': 2, 'streak': 0,
                  'attempts': ['5', '6'], 'last_seen': '2026-10-01'},
            'legacy': {'identity': {'stage': 'terra', 'artifact_hash': 'q'}, 'count': 3,
                       'attempts': ['7', '8', '9'], 'last_seen': '2026-09-30'}},
            failure_retry_authorizations=[{'kind': 'repeated_failure', 'failure_key': 'a'},
                                          {'kind': 'repeated_failure', 'failure_key': 'a'},
                                          {'kind': 'permission_hold', 'failure_key': None}])
        groups = {row['id']: row for row in self.card(state)['failure_groups']}
        self.assertEqual({'a': (4, 4, 2), 'b': (2, 0, 0), 'legacy': (3, 3, 0)},
                         {key: (row['count'], row['streak'], row['authorized_retries']) for key, row in groups.items()})
        self.assertEqual(['1', '2', '3', '4'], groups['a']['attempts'], 'existing fields are unchanged')

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

    def test_missing_original_job_source_offers_inspection_without_execution(self):
        for status in ('PAUSED_JOB_FAILURE', 'PAUSED_STAGE_ABANDONED'):
            with self.subTest(status=status):
                state = self.state(status, next_stage='investigate_bug', job_failure={
                    'reason': 'Provider stopped', 'stage': 'investigate_bug',
                    'attempt_id': '001/bug-investigation-01', 'job_retry_token': 'old',
                    'archive': '/run/archive', 'source_identity': None,
                    'write_diagnosis': {'unrestored': ['original source capture']},
                    'unrestored': ['original source capture']})
                before = copy.deepcopy(state)
                card = self.card(state)
                self.assertEqual(['inspect', 'feedback'], [row['kind'] for row in card['actions']])
                self.assertIn('original source identity', card['saved_reason'])
                self.assertEqual(before, state)
