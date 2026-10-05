"""Explicit attribution recovery preserves defects and refuses stale frontiers."""
import copy
import unittest

import autocode_args as args
import autocode_finding_scope_repair as repair
import autocode_finding_scope as scope
import autocode_run_view as view


class FindingScopeRepairTests(unittest.TestCase):
    def setUp(self):
        self.milestones = [
            {'id': 'M1', 'acceptance_criteria': ['AC1'], 'depends_on': []},
            {'id': 'M2', 'acceptance_criteria': ['AC10'], 'depends_on': ['M1']},
            {'id': 'M5', 'acceptance_criteria': ['AC15'], 'depends_on': ['M2']},
        ]
        self.rows = [{'id': 'F-stop', 'scope': {'milestone_id': 'M2', 'criteria': ['AC10', 'AC15']},
                      'status': 'open', 'blocking': True, 'source': 'sol', 'severity': 'high',
                      'finding': 'Stop missing', 'opened_in': 'old-report', 'times_reported': 2}]
        self.change = {'id': 'F-stop', 'expected_scope': copy.deepcopy(self.rows[0]['scope']),
                       'new_scope': {'milestone_id': 'M5', 'criteria': ['AC15']},
                       'evidence': 'Reviewed old Stop finding; approved AC15 moved to M5'}
        self.published = {'scope': 'permission', 'request_id': 'request', 'request_token': 'token',
                          'request': {'proposed_delta': 'Operational ledger-metadata reconciliation only: repair attribution'},
                          'questions': [{'id': 'question'}]}
        self.manifest = {'contract_token': 'r17:hash', 'request_id': 'request', 'request_token': 'token',
                         'question_id': 'question', 'changes': [self.change]}
        self.state = {'status': 'WAITING_FOR_USER', 'next_stage': 'astra_review',
                      'goal_contract': {'revision': 17, 'hash': 'hash', 'approval_status': 'approved',
                                        'body': {'milestones': self.milestones}},
                      'findings_ledger': self.rows, 'settings': {'roles': {'terra': 'pinned'}, 'max_seconds': 0},
                      'validation': {'verdict': 'PASS', 'output': 'independent-report'},
                      'regression_proof': {'verdict': 'PASS', 'receipt': 'real-runner'},
                      'pending_questions': [{'id': 'question'}], 'user_events': [{'kind': 'old_event'}]}

    def test_defect_remains_open_and_blocks_its_owner_but_not_unrelated_m1(self):
        self.assertEqual(scope.relevant_blockers(self.rows, {'id': 'M1', 'acceptance_criteria': ['AC1']}, self.milestones), self.rows)
        result = repair.reconcile(self.rows, [self.change], self.milestones)
        self.assertEqual(scope.relevant_blockers(result, {'id': 'M1', 'acceptance_criteria': ['AC1']}, self.milestones), [])
        self.assertEqual(scope.relevant_blockers(result, {'id': 'M5', 'acceptance_criteria': ['AC15']}, self.milestones), result)
        self.assertEqual({k:v for k,v in result[0].items() if k != 'scope'}, {k:v for k,v in self.rows[0].items() if k != 'scope'})

    def test_candidate_preserves_contract_routes_proof_history_and_inputs(self):
        original = copy.deepcopy(self.state)
        result = repair.prepare(self.state, self.manifest, self.published, 'now')
        self.assertEqual(self.state, original)
        for key in ('goal_contract', 'settings', 'validation', 'regression_proof'):
            self.assertEqual(result[key], original[key])
        self.assertEqual(result['user_events'][:-1], original['user_events'])
        self.assertEqual(result['user_events'][-1]['changes'], [self.change])
        self.assertEqual(result['status'], 'RUNNING')
        self.assertEqual(view.view(result)['finding_scope_reconciliations'], result['user_events'][-1:])

    def test_refuses_each_active_or_uncertain_attempt(self):
        for key in ('active_stage', 'active_runner_check', 'runner_check', 'pending_report_repair', 'uncertain_artifacts'):
            state = {**self.state, key: {'retained': True}}
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'active or uncertain'):
                repair.prepare(state, self.manifest, self.published, 'now')

    def test_refuses_stale_contract_request_token_question_and_stage(self):
        for key in ('contract_token', 'request_id', 'request_token', 'question_id'):
            manifest = {**self.manifest, key: 'stale'}
            with self.subTest(key=key), self.assertRaises(ValueError):
                repair.prepare(self.state, manifest, self.published, 'now')
        with self.assertRaises(ValueError):
            repair.prepare({**self.state, 'next_stage': 'terra'}, self.manifest, self.published, 'now')

    def test_refuses_wrong_permission_and_draft_contract(self):
        for published in ({**self.published, 'scope': 'human_review'},
                          {**self.published, 'request': {'proposed_delta': 'Unrelated permission'}}):
            with self.assertRaises(ValueError):
                repair.prepare(self.state, self.manifest, published, 'now')
        state = copy.deepcopy(self.state)
        state['goal_contract']['approval_status'] = 'draft'
        with self.assertRaises(ValueError):
            repair.prepare(state, self.manifest, self.published, 'now')

    def test_refuses_closed_unknown_changed_valid_or_unsupported_scope(self):
        for change in ({**self.change, 'id': 'unknown'},
                       {**self.change, 'expected_scope': {'milestone_id': 'M1', 'criteria': ['AC1']}},
                       {**self.change, 'new_scope': {'milestone_id': 'M5', 'criteria': ['AC10']}},
                       {**self.change, 'new_scope': {'milestone_id': 'M5', 'criteria': ['AC15', 'AC15']}},
                       {**self.change, 'evidence': ''}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                repair.reconcile(self.rows, [change], self.milestones)
        with self.assertRaises(ValueError):
            repair.reconcile([{**self.rows[0], 'status': 'resolved'}], [self.change], self.milestones)
        with self.assertRaises(ValueError):
            repair.reconcile(self.rows, [self.change, self.change], self.milestones)
        with self.assertRaises(ValueError):
            repair.reconcile(self.rows, [self.change], [{'id': 'M2', 'acceptance_criteria': ['AC10', 'AC15']}])

    def test_cli_rejects_combining_reconciliation_with_another_action(self):
        import contextlib
        import io
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            args.parse(None, ['--run-dir', '/tmp/run', '--status', '--reconcile-finding-scopes', '/tmp/spec'], args.DEFAULT_ROLE_MODELS)


if __name__ == '__main__':
    unittest.main()
