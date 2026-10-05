"""Evidence gates and recovery with isolated Git workspaces; no model calls."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from . import test_goals
import autocode as runner
import autocode_stage_context as stage_context
import autocode_completion as completion_gate
import autocode_goals as goals
import autocode_goal_lifecycle as lifecycle
import autocode_support as s
import autocode_milestones as m
import autocode_findings as findings
from goal_fixtures import body, envelope


class MilestoneCheckpointTests(unittest.TestCase):
    setUp = test_goals.GoalTests.setUp
    invoke = test_goals.GoalTests.invoke

    def start(self, human=False, dependent=False):
        draft = body(human=human)
        draft['acceptance_criteria'] += [
            {'id': 'C2', 'criterion': 'Reject invalid input', 'verification_method': 'Execute empty input', 'human_review': False},
            {'id': 'C3', 'criterion': 'Preserve Unicode', 'verification_method': 'Execute Unicode input', 'human_review': human}]
        draft['milestones'][0]['acceptance_criteria'] = ['C1', 'C2']
        draft['milestones'].append({'id': 'M2', 'objective': 'Unicode flow', 'acceptance_criteria': ['C3'],
                                    'depends_on': ['M1'] if dependent else []})
        lifecycle.install_draft(self.state, draft, origin='test')
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, self.state['displayed_goal'])
        self.state['settings']['milestone_checkpoints'] = copy.deepcopy(m.DEFAULTS)
        self.assign()

    def decision(self, milestone='M1', status='CONTINUE'):
        return {**envelope(self.state), 'status': status,
            'acceptance_criteria': [{**c, 'status': 'unverified', 'evidence': ''} for c in self.state['acceptance_criteria']],
            'next_objective': 'Deliver ' + milestone, 'affected_paths': ['greet.py'], 'plan': ['Complete the milestone'],
            'next_task': {'kind': 'implement', 'milestone_id': milestone, 'requirements': ['Real learner flow'],
                          'acceptance_criteria': ['C1', 'C2'] if milestone == 'M1' else ['C3'],
                          'validation_plan': ['Execute valid and invalid input']},
            'evidence': ['Independent failure receipt'], 'blocker': '', 'agreed_limitations': []}

    def assign(self, milestone='M1', status='CONTINUE', **changes):
        decision = self.decision(milestone, status)
        decision.update(changes)
        output = self.run / f'astra-{len(self.state["stages"])}.json'
        output.write_text(json.dumps(decision))
        record = {'output': str(output), 'source_revision': s.snapshot(self.root)['revision']}
        runner.apply_result(self.state, 'astra_review', decision, record, self.root, self.run)
        if status == 'REWORK':
            self.assertEqual('astra_resolve', self.state['next_stage'])
            runner.apply_result(self.state, 'astra_resolve', {**decision, 'diagnosis': 'Independent checks failed'},
                                record, self.root, self.run)

    def validate(self, statuses=None, verdict=None, flow_status=None):
        statuses = statuses or {'C1': 'PASS', 'C2': 'PASS', 'C3': 'NOT_VERIFIED'}
        criterion_ids = m.scope(self.state)['acceptance_criteria']
        passed = all(statuses.get(cid) == 'PASS' for cid in criterion_ids)
        n = sum(r.get('role') == 'sol' for r in self.state['stages'])
        events = self.run / f'sol-{n}.jsonl'
        # A real command with the fixture's outcome: the runner re-runs PASS checks (autocode_check_replay).
        check = 'true' if passed else 'false'
        events.write_text(json.dumps({'type': 'item.completed', 'item': {'id': 'check', 'type': 'command_execution',
            'command': check, 'exit_code': 0 if passed else 1, 'aggregated_output': str(statuses)}}))
        value = {**envelope(self.state), 'verdict': verdict or ('PASS' if passed else 'FAIL'),
            'checks_run': [check], 'checks': [{'command': check, 'exit_code': 0 if passed else 1, 'evidence_ref': 'event:check'}],
            'findings': [], 'unverified_criteria': [cid for cid, status in statuses.items() if status == 'NOT_VERIFIED'],
            'criterion_results': [{'id': cid, 'status': status, 'evidence_refs': ['event:check']} for cid, status in statuses.items()],
            'end_to_end_result': {'status': flow_status or ('PASS' if passed else 'FAIL'), 'summary': 'Executed current outcome; later work may remain', 'evidence_refs': ['event:check']}}
        record = {'role': 'sol', 'stage': 'sol', 'events': str(events), 'output': str(events), 'source_revision': s.snapshot(self.root)['revision']}
        runner.apply_result(self.state, 'sol', value, record, self.root, self.run)

    def test_dependent_milestone_waits_for_accepted_prerequisites(self):
        draft = body()
        draft['acceptance_criteria'] += [
            {'id': 'C3', 'criterion': 'Preserve Unicode', 'verification_method': 'Execute Unicode input', 'human_review': False},
            {'id': 'C4', 'criterion': 'Document usage', 'verification_method': 'Run --help', 'human_review': False}]
        draft['milestones'] = [
            {'id': 'M1', 'objective': 'Greeting flow', 'acceptance_criteria': ['C1'], 'depends_on': []},
            {'id': 'M2', 'objective': 'Unicode flow', 'acceptance_criteria': ['C3'], 'depends_on': ['M3']},
            {'id': 'M3', 'objective': 'Usage help', 'acceptance_criteria': ['C4'], 'depends_on': []}]
        lifecycle.install_draft(self.state, draft, origin='test')
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, self.state['displayed_goal'])
        self.state['settings']['milestone_checkpoints'] = copy.deepcopy(m.DEFAULTS)

        def assign(milestone, criteria):
            decision = self.decision(milestone)
            decision['next_task']['acceptance_criteria'] = criteria
            runner.apply_result(self.state, 'astra_review', decision, {'output': 'astra.json'}, self.root, self.run)

        assign('M1', ['C1'])
        self.validate({'C1': 'PASS', 'C3': 'NOT_VERIFIED', 'C4': 'NOT_VERIFIED'})
        with self.assertRaisesRegex(ValueError, 'M2 cannot start until its prerequisites are accepted: M3'):
            assign('M2', ['C3'])
        self.assertEqual('M1', self.state['current_task']['milestone_id'])
        assign('M3', ['C4'])
        self.assertEqual('M3', self.state['current_task']['milestone_id'])
        self.validate({'C1': 'PASS', 'C3': 'NOT_VERIFIED', 'C4': 'PASS'})
        assign('M2', ['C3'])
        self.assertEqual('M2', self.state['current_task']['milestone_id'])
        self.assertEqual(['M1', 'M3'], m.summary(self.state)['accepted_milestones'])

    def test_cannot_advance_without_sol_even_when_astra_claims_success(self):
        self.start()
        task = copy.deepcopy(self.state['current_task'])
        self.assign('M2')
        self.assertEqual(task, self.state['current_task'])
        self.assertEqual('sol', self.state['next_stage'])
        self.assertEqual('RUNNING', self.state['status'])

    def test_failed_criterion_blocks_advancement_despite_source_edits(self):
        self.start()
        (self.root / 'greet.py').write_text('# many changed files are not passing evidence')
        self.validate({'C1': 'PASS', 'C2': 'FAIL'})
        self.assign('M2')
        self.assertEqual('M1', self.state['current_task']['milestone_id'])
        self.assertEqual('astra_review', self.state['next_stage'])

    def test_pass_advances_with_later_criteria_unverified_and_retains_receipt(self):
        self.start()
        self.validate(flow_status='NOT_VERIFIED')
        old_key = m.key(self.state)
        self.assign('M2')
        self.assertEqual('M2', self.state['current_task']['milestone_id'])
        self.assertEqual('terra', self.state['next_stage'])
        self.assertTrue(self.state['milestone_progress'][old_key]['accepted'])
        self.assertEqual('sol', self.state['milestone_progress'][old_key]['accepted_validation']['reviewer_role'])

    def test_later_milestone_finding_does_not_deadlock_passing_prerequisite(self):
        self.start(dependent=True)
        m1_task = self.state['current_task']
        self.state['current_task'] = {'id': 'future-review', 'milestone_id': 'M2'}
        findings.record_decision(self.state, {'findings': [{
            'severity': 'high', 'finding': 'Unicode flow drops composed characters',
            'evidence': 'review:unicode', 'blocking': True}]}, {'output': 'future-review.json'})
        self.state['current_task'] = m1_task
        finding = findings.blocking_entries(self.state)[0]
        self.assertEqual({'milestone_id': 'M2', 'criteria': ['C3']}, finding['scope'])

        self.validate(flow_status='NOT_VERIFIED')
        self.assertTrue(m.evidence_ready(self.state, s.snapshot(self.root)))
        m1_key = m.key(self.state)
        self.assign('M2')
        self.assertTrue(self.state['milestone_progress'][m1_key]['accepted'])
        self.assertEqual('M2', self.state['current_task']['milestone_id'])
        self.assertEqual([finding['id']], [row['id'] for row in findings.blocking_entries(self.state)])

        self.validate({'C1': 'PASS', 'C2': 'PASS', 'C3': 'PASS'})
        self.assertFalse(m.evidence_ready(self.state, s.snapshot(self.root)))
        complete = self.decision(status='COMPLETE')
        complete['acceptance_criteria'] = [{**c, 'status': 'verified', 'evidence': 'event:check'}
                                           for c in self.state['acceptance_criteria']]
        self.assertFalse(completion_gate.completion_ready(self.state, complete, s.snapshot(self.root)))
        no_findings = copy.deepcopy(self.state)
        no_findings['findings_ledger'] = []
        self.assertTrue(completion_gate.completion_ready(no_findings, complete, s.snapshot(self.root)))

    def test_current_milestone_finding_blocks_with_specific_diagnostics(self):
        self.start()
        findings.record_decision(self.state, {'findings': [{
            'severity': 'high', 'finding': 'Greeting fails on empty input',
            'evidence': 'review:empty-input', 'blocking': True}]}, {'output': 'm1-review.json'})
        finding_id = findings.blocking_entries(self.state)[0]['id']
        self.validate(flow_status='NOT_VERIFIED')
        self.assign('M2')
        self.assertEqual('M1', self.state['current_task']['milestone_id'])
        self.assertIn(finding_id, self.state['milestone_blocker'])
        self.assertIn('M1: C1, C2', self.state['milestone_blocker'])

    def test_old_gate_permission_request_is_superseded_only_after_fresh_pass(self):
        self.start(dependent=True)
        self.validate(flow_status='NOT_VERIFIED')
        current = s.snapshot(self.root)
        self.state['milestone_blocker'] = 'Current milestone needs independent passing evidence before advancement'
        request = {'kind': 'permission',
                   'decision_needed': 'Authorize an operator/runner-owned registration of M1 as accepted',
                   'impact': 'M2 cannot start while M1 is unaccepted',
                   'options': ['Mark M1 accepted'],
                   'proposed_delta': 'No change to the approved goal; runner-owned milestone-acceptance state: record M1 as accepted'}
        lifecycle.wait_for_user(self.state, request, origin={
            'stage': 'astra_review', 'task_id': self.state['current_task']['id'],
            'source_revision': current['revision']}, next_stage='astra_review')
        runner.normalize_human_boundary(self.state, self.run)
        self.assertEqual('RUNNING', self.state['status'])
        self.assertEqual([], self.state['pending_questions'])
        self.assertFalse(m.progress(self.state)['accepted'])
        retired = [row for row in self.state['resolver']['human_escalations'].values()
                   if row['status'] == 'superseded'
                   and row['identity']['proposal']['request'] == request]
        self.assertEqual(1, len(retired))
        self.assertEqual('runner', self.state['user_events'][-1]['actor'])
        self.assign('M2')
        self.assertTrue(self.state['milestone_progress'][f"{self.state['goal_contract']['hash']}:M1"]['accepted'])

    def test_unrelated_permission_request_stays_for_the_human(self):
        self.start()
        self.validate(flow_status='NOT_VERIFIED')
        request = {'kind': 'permission', 'decision_needed': 'Allow deployment to staging?',
                   'impact': 'Deployment needs account access', 'options': ['Allow', 'Do not allow'],
                   'proposed_delta': 'Deploy to staging'}
        lifecycle.wait_for_user(self.state, request, origin={'stage': 'astra_review'}, next_stage='astra_review')
        runner.normalize_human_boundary(self.state, self.run)
        self.assertEqual('WAITING_FOR_USER', self.state['status'])
        self.assertEqual(request, self.state['user_request'])

    def test_unverified_whole_flow_cannot_complete_even_when_all_criteria_pass(self):
        self.start()
        self.validate({'C1': 'PASS', 'C2': 'PASS', 'C3': 'PASS'}, flow_status='NOT_VERIFIED')
        decision = self.decision(status='COMPLETE')
        decision['acceptance_criteria'] = [{**c, 'status': 'verified', 'evidence': 'event:check'}
                                           for c in self.state['acceptance_criteria']]
        current = s.snapshot(self.root)
        self.assertTrue(m.evidence_ready(self.state, current))
        self.assertFalse(completion_gate.completion_ready(self.state, decision, current))
        self.state['validation']['end_to_end_result']['status'] = 'PASS'
        self.assertTrue(completion_gate.completion_ready(self.state, decision, current))

    def test_final_validation_may_recheck_accepted_milestones(self):
        # Live ladder-20 (Claude models, 2026-09-30), twice: the last milestone's validation left out, or
        # did not pass, the accepted milestone's criteria, so completion was refused, and the Completion
        # Owner's request to validate every criterion was rejected as outside the milestone. Deadlock.
        self.start()
        self.validate(flow_status='NOT_VERIFIED')
        self.assign('M2')
        self.validate({'C3': 'PASS'})
        complete = self.decision('M2', status='COMPLETE')
        complete['acceptance_criteria'] = [{**c, 'status': 'verified', 'evidence': 'event:check'}
                                           for c in self.state['acceptance_criteria']]
        with self.assertRaises(s.Paused) as refused:
            runner.apply_result(self.state, 'astra_review', complete, {'output': 'astra.json'}, self.root, self.run)
        self.assertIn('no passing result for C1, C2', str(refused.exception))
        self.assertIn('kind=validate', str(refused.exception))
        # A validation may include accepted milestones' criteria; an implementation may not.
        with self.assertRaisesRegex(ValueError, 'Task must belong to an approved milestone'):
            self.assign('M2', next_task={**self.decision('M2')['next_task'], 'acceptance_criteria': ['C1', 'C3']})
        final = self.decision('M2')
        self.assign('M2', next_task={**final['next_task'], 'kind': 'validate', 'acceptance_criteria': ['C1', 'C2', 'C3']})
        self.assertEqual(('M2', ['C1', 'C2', 'C3'], 'sol'), (self.state['current_task']['milestone_id'],
                         self.state['current_task']['acceptance_criteria'], self.state['next_stage']))
        self.validate({'C1': 'PASS', 'C2': 'PASS', 'C3': 'PASS'})
        complete = {**self.decision('M2', status='COMPLETE'), 'acceptance_criteria': complete['acceptance_criteria']}
        runner.apply_result(self.state, 'astra_review', complete, {'output': 'astra.json'}, self.root, self.run)
        self.assertEqual('TASK_COMPLETE', self.state['status'])

    def test_review_handoff_evaluates_current_gate_without_erasing_history(self):
        self.start()
        self.validate(flow_status='NOT_VERIFIED')
        self.state['milestone_blocker'] = 'Previous evidence gate rejection'
        before = copy.deepcopy(self.state)
        prompt, _ = stage_context.context_packet(self.state, 'astra_review', self.run / 'state.json')
        packet = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
        self.assertTrue(packet['milestone_checkpoint']['current_evidence_ready'])
        self.assertEqual('Previous evidence gate rejection', packet['milestone_checkpoint']['blocker'])
        self.assertEqual(before, self.state)
        self.state['validation']['end_to_end_result']['status'] = 'FAIL'
        prompt, _ = stage_context.context_packet(self.state, 'astra_review', self.run / 'state.json')
        packet = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
        self.assertFalse(packet['milestone_checkpoint']['current_evidence_ready'])

    def test_explicit_checkpoint_answer_preserves_approval_and_requires_current_evidence(self):
        self.start()
        self.validate(flow_status='NOT_VERIFIED')
        choice = 'Reconcile M1 as accepted based on the existing current-revision Validator evidence, then resume at M2.'
        question = {'id': 'decision-checkpoint', 'question': 'Reconcile the checkpoint?',
                    'why': 'The earlier gate rejected advancement', 'options': [choice], 'proposed_default': ''}
        self.state.update(status='WAITING_FOR_USER', phase='WAITING_FOR_USER', next_stage='astra_review',
                          pending_questions=[question], user_request={'kind': 'blocker', 'proposed_delta': '',
                          'discovered': 'The milestone checkpoint remains unaccepted despite passing Validator evidence.',
                          'options': [choice]})
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, 'explicit saved choice'):
            lifecycle.resolve_passing_checkpoint(self.state, question['id'], 'Resume anyway')
        self.assertEqual(before, self.state)
        self.state['validation']['end_to_end_result']['status'] = 'FAIL'
        with self.assertRaisesRegex(ValueError, 'independent evidence'):
            lifecycle.resolve_passing_checkpoint(self.state, question['id'], choice)
        self.state['validation']['end_to_end_result']['status'] = 'NOT_VERIFIED'
        lifecycle.resolve_passing_checkpoint(self.state, question['id'], choice)
        self.assertTrue(goals.approved(self.state))
        self.assertEqual('RUNNING', self.state['status'])
        self.assertEqual('astra_review', self.state['next_stage'])
        self.assertEqual('checkpoint_answer', self.state['answers'][question['id']]['kind'])
        self.assertFalse(self.state['milestone_progress'][m.key(self.state)]['accepted'])

    def test_full_scope_and_known_flow_failure_still_require_passing_flow(self):
        self.start()
        self.validate(flow_status='FAIL')
        self.assign('M2')
        self.assertEqual('M1', self.state['current_task']['milestone_id'])
        self.validate({'C1': 'PASS', 'C2': 'PASS', 'C3': 'PASS'}, flow_status='NOT_VERIFIED')
        self.state['current_task']['milestone_ids'] = ['M1', 'M2']
        self.state['validation']['milestone_results'] = [
            {'milestone_id': mid, 'status': 'PASS', 'summary': 'Validated', 'evidence_refs': ['event:check']}
            for mid in ('M1', 'M2')]
        self.assertFalse(m.evidence_ready(self.state, s.snapshot(self.root)))
        self.state['validation']['end_to_end_result']['status'] = 'PASS'
        self.assertTrue(m.evidence_ready(self.state, s.snapshot(self.root)))

    def test_entire_milestone_must_pass_even_if_last_task_only_covers_one_criterion(self):
        self.start()
        self.state['current_task']['acceptance_criteria'] = ['C1']
        self.validate({'C1': 'PASS', 'C2': 'NOT_VERIFIED'}, verdict='FAIL')
        self.assign('M2')
        self.assertEqual('M1', self.state['current_task']['milestone_id'])

    def test_tampered_evidence_stale_source_wrong_task_and_self_check_cannot_advance(self):
        for mode in ('evidence', 'source', 'task', 'writer', 'blocking'):
            with self.subTest(mode=mode):
                self.start(); self.validate()
                val = self.state['validation']
                if mode == 'evidence': Path(next(iter(val['evidence_hashes']))).write_text('altered')
                if mode == 'source': (self.root / 'change.py').write_text('changed')
                if mode == 'task': val['task_id'] = 'different'
                if mode == 'writer': val['reviewer_role'] = 'terra'
                if mode == 'blocking': val['findings'] = [{'severity': 'medium', 'blocking': True}]
                self.assign('M2')
                self.assertEqual('M1', self.state['current_task']['milestone_id'])

    def test_repeated_failed_checks_allow_one_changed_replan_then_stop(self):
        self.start()
        for i in range(3):
            (self.root / 'greet.py').write_text(f'# attempt {i}')
            self.validate({'C1': 'FAIL', 'C2': 'FAIL'})
        self.assign(status='REWORK')  # Same approach does not reset the counter.
        self.assertEqual('astra_review', self.state['next_stage'])
        self.assertEqual(0, m.progress(self.state)['replans'])
        self.assign(status='REWORK', next_objective='Isolate empty input first with a smaller regression fixture')
        self.assertEqual('terra', self.state['next_stage'])
        self.assertEqual(1, m.progress(self.state)['replans'])
        for i in range(3): self.validate({'C1': 'FAIL', 'C2': 'FAIL'})
        self.assign(status='REWORK', next_objective='Another attempt')
        self.assertEqual('PAUSED_MILESTONE_STALLED', self.state['status'])

    def test_unbounded_replans_still_require_a_changed_approach(self):
        self.start()
        self.state['settings']['milestone_checkpoints']['max_replans'] = None
        for cycle in range(3):
            for _ in range(3):
                self.validate({'C1': 'FAIL', 'C2': 'FAIL'})
            self.assign(status='REWORK', next_objective=f'Changed approach {cycle + 1}')
            self.assertEqual(cycle + 1, m.progress(self.state)['replans'])
            self.assertEqual('RUNNING', self.state['status'])

    def test_new_passing_criterion_counts_as_progress_but_oscillation_does_not(self):
        self.start()
        self.validate({'C1': 'PASS', 'C2': 'FAIL'})
        self.validate({'C1': 'FAIL', 'C2': 'PASS'})
        self.assertEqual(0, m.progress(self.state)['reviews_without_progress'])
        self.validate({'C1': 'PASS', 'C2': 'FAIL'})
        self.assertEqual(1, m.progress(self.state)['reviews_without_progress'])
        m.observe_validation(self.state, s.snapshot(self.root))
        self.assertEqual(1, m.progress(self.state)['reviews_without_progress'])

    def test_budget_charges_rejected_attempts_once_and_allows_validation(self):
        self.start()
        record = {'role': 'terra', 'task_id': self.state['current_task']['id'], 'duration_seconds': 5401}
        runner.account_stage(self.state, record); runner.account_stage(self.state, record)
        self.assertEqual(5401, m.progress(self.state)['seconds'])
        with self.assertRaises(s.Paused): m.dispatch_guard(self.state, 'terra')
        m.dispatch_guard(self.state, 'sol'); m.dispatch_guard(self.state, 'astra_review')
        self.validate(); self.assign('M2')
        self.assertEqual(0, m.progress(self.state)['seconds'])

    def test_human_review_blocks_only_current_milestone_and_resumes_after_approval(self):
        self.start(human=True); self.validate(); self.assign('M2')
        lifecycle.human.evaluate(self.state)  # the runner's writer boundary publishes the review request
        self.assertEqual('WAITING_FOR_USER', self.state['status'])
        self.assertEqual(['C1'], self.state['user_request']['criteria'])
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        goals.approve_review(self.state, 'C1', goals.review_token(self.state), s.snapshot(self.root))
        self.assertEqual('RUNNING', self.state['status'])
        self.assertIn('C3', goals.missing_human_reviews(self.state))
        self.assign('M2')
        self.assertEqual('M2', self.state['current_task']['milestone_id'])

    def test_review_only_permission_becomes_evidence_bound_review(self):
        self.start(human=True)
        self.validate()
        finding_record = {'stage': 'astra_review', 'output': str(self.run / 'earlier-review.json')}
        findings.record_decision(self.state, {'status': 'REWORK', 'findings': [
            {'severity': 'high', 'finding': 'M1 check was missing', 'evidence': 'earlier review'}],
            'finding_dispositions': []}, finding_record)
        finding_id = findings.open_entries(self.state, 'astra')[0]['id']
        request = {'kind': 'permission', 'decision_needed': 'Perform the C1 human review via the runner',
                   'impact': 'The approved milestone requires human review', 'options': ['Review it'],
                   'proposed_delta': 'No change to the approved goal, scope or product behavior.'}
        decision = {**self.decision(status='BLOCKED'), 'findings': [], 'user_request': request,
                    'finding_dispositions': [{'id': finding_id, 'disposition': 'resolved',
                                              'evidence': 'Independent checks now pass'}]}
        output = self.run / 'review-only.json'
        output.write_text(json.dumps(decision))
        findings.record_decision(self.state, decision, {'stage': 'astra_review', 'output': str(output)})
        self.assertEqual([], findings.open_entries(self.state, 'astra'))
        lifecycle.wait_for_user(self.state, request, origin={'stage': 'astra_review', 'output': str(output)})
        lifecycle.human.evaluate(self.state)
        self.assertEqual('human_review', lifecycle.human.current(self.state)['scope'])
        self.assertEqual(['C1'], self.state['user_request']['criteria'])
        self.assertEqual({}, self.state['human_reviews'])

    def test_stale_validation_retries_before_requesting_human_review(self):
        self.start(human=True)
        self.validate()
        request = {'kind': 'permission', 'decision_needed': 'Perform the C1 human review via the runner',
                   'impact': 'The approved milestone requires human review', 'options': ['Review it'],
                   'proposed_delta': 'No change to the approved goal, scope or product behavior.'}
        decision = {**self.decision(status='BLOCKED'), 'findings': [], 'user_request': request}
        output = self.run / 'stale-review.json'
        output.write_text(json.dumps(decision))
        stale = self.run / 'state.json'
        stale.write_text('{}')
        self.state['validation']['evidence_hashes'][str(stale)] = s.file_hash(stale)
        stale.write_text('{"updated":true}')
        lifecycle.wait_for_user(self.state, request, origin={'stage': 'astra_review', 'output': str(output)})
        self.assertEqual(('RUNNING', 'sol'), (self.state['status'], self.state['next_stage']))
        self.assertFalse(self.state.get('pending_questions'))

    def test_published_review_permission_recovers_without_user_answer(self):
        self.start(human=True)
        self.validate()
        request = {'kind': 'permission', 'decision_needed': 'Perform the C1 human review via the runner',
                   'impact': 'The approved milestone requires human review', 'options': ['Review it'],
                   'proposed_delta': 'No change to the approved goal, scope or product behavior.'}
        decision = {**self.decision(status='BLOCKED'), 'findings': [], 'user_request': request}
        output = self.run / 'published-review.json'
        output.write_text(json.dumps(decision))
        stale = self.run / 'state.json'
        stale.write_text('{}')
        self.state['validation']['evidence_hashes'][str(stale)] = s.file_hash(stale)
        stale.write_text('{"updated":true}')
        self.state['run_dir'] = str(self.run.resolve())
        lifecycle.human.queue(self.state, 'permission', {'stage': 'astra_review', 'output': str(output)},
                          request=request, questions=[{'id': 'old', 'question': request['decision_needed'],
                                                      'why': request['impact'], 'options': request['options']}],
                          next_stage='astra_review')
        lifecycle.human.evaluate(self.state)
        old = lifecycle.human.current(self.state)['request_id']
        self.assertTrue(m.route_review_only_request_preview(self.state, request,
                        {'stage': 'astra_review', 'output': str(output)}))
        self.assertFalse(m.fresh_validation(self.state, s.snapshot(self.root)))
        self.assertIn('C1', goals.missing_human_reviews(self.state))
        preview = copy.deepcopy(self.state)
        origin = preview['resolver']['human_escalations'][old]['identity']['proposal']['origin']
        self.assertIsNone(m.route_review_only_request(preview, request, origin))
        self.assertFalse(self.state.get('active_stage'))
        self.assertFalse(self.state.get('uncertain_artifacts'))
        runner.normalize_human_boundary(self.state, self.run)
        self.assertEqual(('RUNNING', 'sol'), (self.state['status'], self.state['next_stage']))
        self.assertEqual('superseded', self.state['resolver']['human_escalations'][old]['status'])
        self.assertIsNone(lifecycle.human.current(self.state))
        self.assertFalse(self.state.get('answers'))

    def test_material_permission_remains_a_human_decision(self):
        self.start(human=True)
        self.validate()
        request = {'kind': 'permission', 'decision_needed': 'Change the C1 acceptance criterion',
                   'impact': 'The approved product behavior would change', 'options': ['Allow change'],
                   'proposed_delta': 'Change the approved goal and product behavior.'}
        decision = {**self.decision(status='BLOCKED'), 'findings': [], 'user_request': request}
        output = self.run / 'material-permission.json'
        output.write_text(json.dumps(decision))
        lifecycle.wait_for_user(self.state, request, origin={'stage': 'astra_review', 'output': str(output)})
        lifecycle.human.evaluate(self.state)
        self.assertEqual('permission', lifecycle.human.current(self.state)['scope'])

    def test_operator_activation_preserves_contract_and_routes_existing_work_to_sol(self):
        self.start()
        self.state['settings'].pop('milestone_checkpoints')
        self.state['settings']['workflow'] = {'mode': 'glm_final_audit_v2'}
        self.state['status'] = 'PAUSED_REQUESTED'
        contract = copy.deepcopy(self.state['goal_contract'])
        self.assertEqual(0, self.invoke('--milestone-checkpoints', '--show-goal'))
        self.assertTrue(m.enabled(self.state))
        self.assertNotIn('workflow', self.state['settings'])
        self.assertEqual(contract, self.state['goal_contract'])
        self.assertEqual('sol', self.state['next_stage'])

    def test_activation_refuses_uncertain_or_active_requests_and_status_is_read_only(self):
        self.start()
        before = copy.deepcopy(self.state)
        m.summary(self.state)
        self.assertEqual(before, self.state)
        for field in ('active_stage', 'pending_report_repair', 'uncertain_artifacts'):
            candidate = copy.deepcopy(self.state); candidate[field] = {'pending': True}
            with self.assertRaises(ValueError): m.activate(candidate)

    def test_queue_does_not_touch_active_state_and_activation_is_durable(self):
        self.start()
        self.state['settings'].pop('milestone_checkpoints')
        self.state['settings']['workflow'] = {'mode': 'glm_final_audit_v2'}
        s.atomic_json(self.run / 'state.json', self.state)
        before = (self.run / 'state.json').read_bytes()
        with s.workspace_lock(self.root):
            request = m.queue_activation(self.run, 7200)
            self.assertEqual(request, m.queue_activation(self.run, 7200))
        self.assertEqual(before, (self.run / 'state.json').read_bytes())
        with self.assertRaises(ValueError): m.queue_activation(self.run, 1200)
        self.assertTrue(m.apply_queued_activation(self.state, self.run))
        self.assertEqual('RUNNING', self.state['status'])
        self.assertEqual('sol', self.state['next_stage'])
        self.assertFalse((self.run / 'pause-requested').exists())
        self.assertEqual(7200, self.state['settings']['milestone_checkpoints']['max_seconds'])
        self.assertFalse(m.apply_queued_activation(self.state, self.run))
        self.assertEqual([request['id']], self.state['milestone_activation_requests'])

    def test_preexisting_pause_survives_queued_activation(self):
        self.start()
        (self.run / 'pause-requested').write_text('operator requested pause')
        m.queue_activation(self.run)
        m.apply_queued_activation(self.state, self.run)
        self.assertEqual('operator requested pause', (self.run / 'pause-requested').read_text())

    def test_queued_activation_cannot_discard_an_unreconciled_request(self):
        self.start()
        m.queue_activation(self.run)
        self.state['active_stage'] = {'pid': 123}
        with self.assertRaises(ValueError): m.apply_queued_activation(self.state, self.run)
        self.assertTrue((self.run / 'milestone-checkpoints-requested.json').exists())
        self.assertTrue((self.run / 'pause-requested').exists())

    def test_queued_upgrade_defers_until_report_only_repair_is_complete(self):
        self.start()
        self.state['settings'].pop('milestone_checkpoints')
        m.queue_activation(self.run)
        self.state['pending_report_repair'] = {'original': {'stage': 'terra'}}
        self.assertFalse(m.apply_queued_activation(self.state, self.run))
        self.assertFalse(m.enabled(self.state))
        self.assertTrue(m.owns_pause(self.run))
        self.state.pop('pending_report_repair')
        self.assertTrue(m.apply_queued_activation(self.state, self.run))
        self.assertTrue(m.enabled(self.state))

    def test_explicit_resume_repairs_old_report_then_continues_with_sol(self):
        self.start()
        self.state['settings'].pop('milestone_checkpoints')
        self.state.update(status='PAUSED_INVALID_OUTPUT', pending_report_repair={'original': {'stage': 'terra'}})
        m.queue_activation(self.run)
        def repair(state, run_dir, workspace):
            state.pop('pending_report_repair')
        dispatched = []
        def stop_at_sol(**kwargs):
            dispatched.append(kwargs['role'])
            raise s.Paused('PAUSED_TEST', 'fixture stop')
        with patch.object(runner, 'execute_report_repair', side_effect=repair) as repaired:
            self.assertEqual(2, self.invoke('--resume-paused', role=stop_at_sol))
            repaired.assert_called_once()
        self.assertEqual(['sol'], dispatched)
        self.assertEqual('PAUSED_TEST', self.state['status'])
        self.assertEqual('sol', self.state['next_stage'])

    def test_final_only_routing_cannot_bypass_checkpoints(self):
        self.start()
        self.state['settings']['workflow'] = {'mode': 'glm_final_audit_v2'}
        with self.assertRaises(s.Paused): m.dispatch_guard(self.state, 'terra')

    def test_repeating_validation_cannot_bypass_the_stalled_review_limit(self):
        self.start()
        for _ in range(3): self.validate({'C1': 'FAIL', 'C2': 'FAIL'})
        task = self.decision()['next_task']
        task['kind'] = 'validate'
        for _ in range(3): self.assign(next_task=task)
        self.assertEqual('PAUSED_MILESTONE_REPLAN', self.state['status'])
        self.assertEqual('astra_review', self.state['next_stage'])

    def test_exhausted_budget_does_not_consume_a_replan_that_cannot_run(self):
        self.start()
        for _ in range(3): self.validate({'C1': 'FAIL', 'C2': 'FAIL'})
        m.progress(self.state)['seconds'] = 5400
        self.assign(status='REWORK', next_objective='Smaller independent reproduction')
        self.assertEqual('PAUSED_MILESTONE_BUDGET', self.state['status'])
        self.assertEqual(0, m.progress(self.state)['replans'])


class PendingReplanThroughTheCLI(unittest.TestCase):
    """Issue #459: a stalled milestone's Completion Owner is told the gate it must pass.

    Live feature-stock-refusals run 8soi9a5s (2026-10-05): M1 stalled (needs_replan, replans 0 of 1),
    and the Plan Reviewer, following the general rule to answer CONTINUE with a validate task when
    work only needs revalidation, did so three times; the gate refused each one until the run paused
    PAUSED_MILESTONE_REPLAN. The fake provider below answers each dispatched stage from a script."""
    setUp = test_goals.GoalTests.setUp
    invoke = test_goals.GoalTests.invoke
    start = MilestoneCheckpointTests.start
    assign = MilestoneCheckpointTests.assign
    decision = MilestoneCheckpointTests.decision
    validate = MilestoneCheckpointTests.validate

    GENERAL_RULE = 'with CONTINUE when existing work only needs Validator revalidation'

    def stall(self):
        self.start()
        for _ in range(3):
            self.validate({'C1': 'FAIL', 'C2': 'FAIL'})
        self.assertEqual(('RUNNING', 'astra_review'), (self.state['status'], self.state['next_stage']))

    def revalidation(self, status):
        # The live proposal: no source edits, a changed capture, evidence cited.
        decision = self.decision(status=status)
        decision.update(next_objective='Revalidate M1 without source edits, capturing the flow without rm')
        decision['next_task'].update(kind='validate', requirements=['No source edits; capture the flow without rm'],
                                     validation_plan=['Capture valid and empty input with one python3 -c command'])
        return decision

    def provider(self, script):
        """Record every dispatched prompt; answer from script[stage], and stop before any other stage."""
        prompts = []

        def answer(**request):
            state, stage = request['state'], request['state']['next_stage']
            prompts.append((stage, request['prompt']))
            if not script.get(stage):
                raise s.Paused('PAUSED_TEST', f'fixture stops before {stage}')
            value = script[stage].pop(0)
            output = self.run / f'{stage}-{len(prompts)}.json'
            output.write_text(json.dumps(value))
            return value, {'role': request['role'], 'stage': stage, 'output': str(output), 'exit_code': 0,
                           'source_revision': s.snapshot(self.root)['revision'], 'changed_files': [],
                           'task_id': state['current_task']['id'], 'iteration': state['iteration'],
                           'duration_seconds': 1}
        return prompts, answer

    def checkpoint(self):
        self.assertEqual(0, self.invoke('--status'))
        return json.loads(self.stdout)['milestone_checkpoint']['current']

    def assert_states_the_gate(self, prompt):
        instruction = prompt.split('CURRENT HANDOFF DATA\n', 1)[0]
        self.assertIn('MILESTONE REPLAN REQUIRED', instruction)
        self.assertIn('only with status REWORK, nonempty evidence and a changed approach', instruction)
        self.assertIn('return REWORK with\nnext_task.kind=validate', instruction)
        self.assertIn('replan 1 of 1', instruction)
        self.assertNotIn(self.GENERAL_RULE, instruction.replace('\n', ' '))

    def test_review_prompt_states_the_replan_gate_and_continue_validate_still_pauses(self):
        self.stall()
        prompts, answer = self.provider({'astra_review': [self.revalidation('CONTINUE') for _ in range(3)]})
        self.assertEqual(2, self.invoke('--no-chat', role=answer))
        # As live: three refused reviews, then the Investigator before the pause (stopped here by the fixture).
        self.assertEqual(['astra_review'] * 3 + ['investigate_stuck'], [stage for stage, _ in prompts])
        for _, prompt in prompts[:3]:
            self.assert_states_the_gate(prompt)
        # The gate is unchanged: a CONTINUE is never accepted while the replan is required.
        self.assertEqual('PAUSED_MILESTONE_REPLAN', self.state['status'])
        current = self.checkpoint()
        self.assertEqual((True, 0, 3), (current['needs_replan'], current['replans'], current['rejected_advances']))

    def test_evidence_backed_rework_with_a_changed_approach_is_accepted(self):
        self.stall()
        rework = self.revalidation('REWORK')
        diagnosis = {**rework, 'diagnosis': 'The flow is unverified because its capture was blocked; '
                     'revalidate it with an rm-free capture.'}
        prompts, answer = self.provider({'astra_review': [rework], 'astra_resolve': [diagnosis]})
        self.assertEqual(2, self.invoke('--no-chat', role=answer))
        # The Resolver plans the repair from the same review prompt, so it is told the gate too.
        self.assertEqual(['astra_review', 'astra_resolve', 'sol'], [stage for stage, _ in prompts])
        for _, prompt in prompts[:2]:
            self.assert_states_the_gate(prompt)
        self.assertNotIn('MILESTONE REPLAN REQUIRED', prompts[2][1])
        self.assertEqual(('PAUSED_TEST', 'sol'), (self.state['status'], self.state['next_stage']))
        self.assertEqual(('validate', rework['next_objective']),
                         (self.state['current_task']['kind'], self.state['current_task']['objective']))
        current = self.checkpoint()
        self.assertEqual((False, 1), (current['needs_replan'], current['replans']))


if __name__ == '__main__':
    unittest.main()
