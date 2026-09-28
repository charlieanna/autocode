"""Producer staging tests: no providers, repository mutations or publication side effects."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner, autopilot, autocode_goals as goals
import autocode_milestones as milestones, autocode_resolver_human as human
import autocode_support as support, autocode_workflow as workflow
from goal_fixtures import body, envelope
from units import autoresolver


class ResolverProducerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run = self.root / '.autocode'
        self.run.mkdir()
        self.snapshot = patch.object(support, 'snapshot', return_value={'revision': 'source-one'})
        self.snapshot.start()
        self.addCleanup(self.snapshot.stop)
        self.state = {'version': 3, 'task_id': 'goal-one', 'task': 'Build greeting',
                      'workspace': str(self.root), 'run_dir': str(self.run), 'iteration': 1,
                      'settings': {}, 'status': 'RUNNING', 'next_stage': 'astra_discovery',
                      'answers': {}, 'user_events': [], 'stages': [], 'history': []}

    def request(self, kind='blocker'):
        return {'kind': kind, 'discovered': 'A failing path needs investigation',
                'impact': 'The greeting cannot meet the approved criterion',
                'decision_needed': 'How should this failing path be handled?',
                'options': ['Investigate', 'Leave paused'], 'proposed_delta': ''}

    def ready(self, *, human_review=False):
        goals.install_draft(self.state, body(human=human_review), origin='fixture')
        # Simulate the external serialized publication boundary, never a producer.
        human.evaluate(self.state)
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state['goal_contract']))
        self.state.pop(human.PUBLIC, None)
        decision = self.decision('CONTINUE')
        goals.assign_task(self.state, decision, {'revision': 'source-one'})
        self.state['next_stage'] = 'astra_review'

    def decision(self, status='BLOCKED', kind='blocker'):
        return {**envelope(self.state), 'status': status, 'user_request': self.request(kind),
                'acceptance_criteria': copy.deepcopy(self.state['acceptance_criteria']),
                'next_objective': 'Fix the greeting', 'affected_paths': ['greet.py'],
                'next_task': {'kind': 'implement', 'milestone_id': 'M1',
                              'requirements': ['Preserve the greeting'], 'acceptance_criteria': ['C1'],
                              'validation_plan': ['Check empty and nonempty names']},
                'evidence': ['Saved failing check'], 'agreed_limitations': []}

    def record(self, stage, value):
        output = self.run / f'{stage}-{len(self.state["stages"])}.json'
        support.atomic_json(output, value)
        return {'stage': stage, 'output': str(output), 'source_revision': 'source-one',
                'task_id': self.state.get('current_task', {}).get('id'), 'exit_code': 0,
                'changed_files': [], 'iteration': 1}

    def apply(self, stage, value):
        record = self.record(stage, value)
        autopilot.apply_result(runner, self.state, stage, value, record, self.root, self.root)
        return record

    def assert_private(self):
        self.assertEqual('RESOLVER_PENDING', self.state['status'])
        self.assertEqual([], self.state['pending_questions'])
        self.assertNotIn('user_request', self.state)
        self.assertIsNone(human.current(self.state))

    def test_draft_questions_stay_sealed_and_unissued_until_external_publication(self):
        draft = body(questions=True)
        goals.install_draft(self.state, draft, origin='astra_discovery')
        contract = copy.deepcopy(self.state['goal_contract'])
        self.assert_private()
        self.assertEqual(draft['open_blocking_questions'], human.internal_questions(self.state))
        with patch.object(human, 'evaluate', side_effect=AssertionError('Renderer cannot publish')):
            rendered = goals.present(self.state)
        self.assertIn('Unissued proposal', rendered)
        self.assertNotIn('Answer ID:', rendered)
        self.assertNotIn('Approval token:', rendered)
        self.assertNotIn('displayed_goal', self.state)
        self.assertEqual(contract, self.state['goal_contract'])
        self.assertTrue(goals.sealed(contract))
        self.assertEqual('escalate', human.evaluate(self.state))
        self.assertIn('Answer ID: Q1', goals.present(self.state))

    def test_intermediate_joint_drafts_and_migration_do_not_queue_approval(self):
        self.state['settings']['joint_planning'] = True
        for origin, next_stage in (('glm_draft', 'astra_challenge'), ('glm_revise', 'astra_finalize')):
            goals.install_draft(self.state, body(), origin=origin)
            self.assertEqual('RUNNING', self.state['status'])
            self.assertEqual(next_stage, self.state['next_stage'])
            self.assertNotIn(human.PRIVATE, self.state)
        final = self.record('astra_finalize', {'contract': body()})
        goals.install_draft(self.state, body(), origin='astra_finalize', record=final)
        self.assert_private()
        self.assertEqual('defer', human.evaluate(self.state))
        self.state['planning']['final_token'] = goals.token(self.state['goal_contract'])
        self.state['planning']['reports']['astra_finalize'] = {'output': final['output']}
        self.state['stages'].append(final)
        self.assertEqual('escalate', human.evaluate(self.state))
        self.assertIn('Approval token:', goals.present(self.state))
        self.state['version'] = 2
        goals.migrate(self.state)
        self.assertEqual('RUNNING', self.state['status'])
        self.assertNotIn(human.PRIVATE, self.state)
        self.assertNotIn(human.PUBLIC, self.state)

    def test_joint_discovery_checks_private_questions(self):
        self.state['settings']['joint_planning'] = True
        draft = body(questions=True)
        draft.update(technical_approach=[], milestones=[])
        value = {'contract': draft, 'summary': 'Need user intent', 'code_refs': [],
                 'alternatives': [], 'uncertainties': [], 'contract_changes': [],
                 'conflict_resolutions': [], 'requirement_trace': []}
        self.apply('astra_discovery', value)
        self.assert_private()
        self.assertNotIn('planning', self.state)
        self.assertEqual('astra_discovery', self.state['next_stage'])

    def test_protected_requests_queue_without_execution_or_diagnosis(self):
        self.ready()
        original = copy.deepcopy(self.state)
        for kind in ('permission', 'goal_change'):
            with self.subTest(kind=kind):
                self.state = copy.deepcopy(original)
                record = self.apply('astra_review', self.decision(kind=kind))
                self.assert_private()
                proposal = self.state[human.PRIVATE]
                self.assertEqual(kind, proposal['scope'])
                self.assertEqual(record['output'], proposal['origin']['output'])
                self.assertNotIn('resolution_request', self.state)
                self.assertTrue(goals.approved(self.state))

    def test_exact_authenticated_permission_reuse_is_retained(self):
        self.ready()
        request = self.request('permission')
        answer = {'kind': 'permission_answer', 'actor': 'user_cli', 'request': copy.deepcopy(request),
                  'text': 'No, keep the excluded file unchanged', 'contract_token': goals.token(self.state['goal_contract'])}
        self.state['answers']['permission-one'] = answer
        self.state['user_events'].append(answer)
        goals.wait_for_user(self.state, request, origin={'stage': 'astra_review'})
        self.assertEqual('RUNNING', self.state['status'])
        self.assertEqual(answer['text'], self.state['permission_reuse_context']['answer'])
        self.assertNotIn(human.PRIVATE, self.state)
        with self.assertRaisesRegex(support.Paused, 'already returned'):
            goals.wait_for_user(self.state, request)

    def test_ordinary_blockers_require_real_diagnosis_before_human_proposal(self):
        self.ready()
        original = copy.deepcopy(self.state)
        for kind in ('blocker', 'clarification'):
            with self.subTest(kind=kind):
                self.state = copy.deepcopy(original)
                record = self.apply('astra_review', self.decision(kind=kind))
                self.assertEqual('astra_resolve', self.state['next_stage'])
                self.assertEqual('RUNNING', self.state['status'])
                self.assertNotIn(human.PRIVATE, self.state)
                self.assertEqual(record['output'], self.state['resolution_request']['review_output'])
                value = {**self.decision(kind=kind), 'diagnosis': 'Inspected saved failure; no permitted repair remains'}
                record = self.apply('astra_resolve', value)
                self.assert_private()
                proposal = self.state[human.PRIVATE]
                self.assertEqual('blocker', proposal['scope'])
                self.assertEqual('astra_resolve', proposal['origin']['stage'])
                self.assertEqual(value['diagnosis'], proposal['evidence']['diagnosis'])
                self.assertEqual(support.file_hash(Path(record['output'])), proposal['evidence']['hashes'][record['output']])
                self.assertEqual('escalate', human.evaluate(self.state))
                with self.assertRaisesRegex(support.Paused, 'already received'):
                    autopilot.queue_resolution(self.state, self.decision(), self.record('astra_review', self.decision()))

    def test_final_only_reports_remain_unaccepted_source_reports(self):
        self.ready()
        self.state['settings']['workflow'] = {'mode': workflow.FINAL_MODE}
        original = copy.deepcopy(self.state)
        for stage in ('terra', 'sol'):
            with self.subTest(stage=stage):
                self.state = copy.deepcopy(original)
                value = {**envelope(self.state), 'user_request': self.request(), 'checks': ['Unvalidated claim']}
                criteria = copy.deepcopy(self.state['acceptance_criteria'])
                self.apply(stage, value)
                request = self.state['resolution_request']
                self.assertEqual(value, request['source_report'])
                self.assertEqual('source_report_not_accepted_review', request['provenance'])
                self.assertNotIn('review', request)
                self.assertNotIn('validation', self.state)
                self.assertEqual(criteria, self.state['acceptance_criteria'])
                self.assertEqual('astra_resolve', self.state['next_stage'])

    def test_missing_task_stale_output_and_repeated_diagnosis_fail_closed(self):
        self.ready()
        original = copy.deepcopy(self.state)
        for mutation in ('task', 'source', 'output', 'failure', 'limit'):
            with self.subTest(mutation=mutation):
                self.state = copy.deepcopy(original)
                value = self.decision()
                record = self.record('astra_review', value)
                if mutation == 'task':
                    self.state.pop('current_task')
                elif mutation == 'source':
                    record['source_revision'] = 'stale'
                elif mutation == 'output':
                    Path(record['output']).unlink()
                elif mutation == 'failure':
                    self.state['failure_history'] = {'failed': {'count': 3, 'identity': {
                        'stage': 'astra_resolve', 'artifact_hash': 'source-one'}}}
                else:
                    self.state['stages'] = [{'stage': 'astra_resolve', 'source_revision': 'source-one'}] * 3
                before = copy.deepcopy(self.state)
                with self.assertRaises(support.Paused):
                    autopilot.apply_result(runner, self.state, 'astra_review', value, record, self.root, self.root)
                self.assertEqual(before, self.state)

    def test_resolver_diagnosis_cannot_change_findings_or_review_verdict(self):
        self.ready()
        self.apply('astra_review', self.decision())
        before = copy.deepcopy(self.state.get('findings_ledger'))
        criteria = copy.deepcopy(self.state['acceptance_criteria'])
        value = {**self.decision(), 'diagnosis': 'No permitted repair',
                 'findings': [{'id': 'forged', 'severity': 'high', 'finding': 'Made up', 'evidence': 'None'}]}
        value['acceptance_criteria'][0]['status'] = 'verified'
        self.apply('astra_resolve', value)
        self.assertEqual(before, self.state.get('findings_ledger'))
        self.assertEqual(criteria, self.state['acceptance_criteria'])
        self.assertNotIn('validation', self.state)

    def test_human_review_proposals_bind_artifact_token_and_criteria(self):
        self.ready(human_review=True)
        evidence = self.root / 'validation.json'
        support.atomic_json(evidence, {'verdict': 'PASS'})
        self.state['validation'] = {'verdict': 'PASS', 'source_revision': 'source-one', 'evidence_hashes': {
            str(evidence): support.file_hash(evidence)}}
        token = goals.review_token(self.state)
        with patch.object(support, 'completion_ready', return_value=True):
            value = self.decision('COMPLETE')
            value['user_request'] = envelope(self.state)['user_request']
            self.apply('astra_review', value)
        self.assert_private()
        self.assertEqual(token, self.state[human.PRIVATE]['evidence']['review_token'])
        self.assertEqual(['C1'], self.state[human.PRIVATE]['evidence']['criteria'])
        self.assertNotIn('Review token', goals.present(self.state))
        self.assertEqual('escalate', human.evaluate(self.state))
        self.assertIn('Review token', goals.present(self.state))
        milestones.handle_gate(self.state, support.Paused('PAUSED_MILESTONE_HUMAN_REVIEW', 'Human review required'),
                               {'revision': 'source-one'})
        self.assert_private()
        self.assertEqual(token, self.state[human.PRIVATE]['evidence']['review_token'])

    def test_resolver_evidence_mutation_and_writes_rejected(self):
        self.ready()
        self.apply('astra_review', self.decision())
        value = {**self.decision(), 'diagnosis': 'Inspected failure'}
        record = self.record('astra_resolve', value)
        record['changed_files'] = ['greet.py']
        with self.assertRaisesRegex(support.Paused, 'unchanged'):
            autoresolver.validate(self.state, value, record, self.root)
        record['changed_files'] = []
        path = next(iter(self.state['resolution_request']['evidence_hashes']))
        Path(path).write_text('changed')
        with self.assertRaisesRegex(support.Paused, 'evidence changed'):
            autoresolver.validate(self.state, value, record, self.root)

    def test_completion_cannot_queue_human_review_without_passing_evidence(self):
        self.ready(human_review=True)
        before = copy.deepcopy(self.state)
        value = self.decision('COMPLETE')
        value['user_request'] = envelope(self.state)['user_request']
        with self.assertRaisesRegex(support.Paused, 'independent evidence'):
            self.apply('astra_review', value)
        self.assertEqual(before, self.state)

    def test_stale_receipt_removes_display_controls_without_republication(self):
        goals.install_draft(self.state, body(), origin='astra_discovery')
        human.evaluate(self.state)
        self.assertIn('Approval token:', goals.present(self.state))
        self.state['settings']['changed'] = True
        with patch.object(human, 'evaluate', side_effect=AssertionError('Presentation cannot publish')):
            rendered = goals.present(self.state)
        self.assertNotIn('Approval token:', rendered)
        self.assertNotIn('Resolver request token:', rendered)
        self.assertNotIn('displayed_goal', self.state)
