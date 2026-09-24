"""Evidence gates and recovery with isolated Git workspaces; no model calls."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from . import test_goals
import autocode as runner
import autocode_goals as goals
import autocode_support as s
import autocode_milestones as m
from goal_fixtures import body, envelope


class MilestoneCheckpointTests(unittest.TestCase):
    setUp = test_goals.GoalTests.setUp
    invoke = test_goals.GoalTests.invoke

    def start(self, human=False):
        draft = body(human=human)
        draft['acceptance_criteria'] += [
            {'id': 'C2', 'criterion': 'Reject invalid input', 'verification_method': 'Execute empty input', 'human_review': False},
            {'id': 'C3', 'criterion': 'Preserve Unicode', 'verification_method': 'Execute Unicode input', 'human_review': human}]
        draft['milestones'][0]['acceptance_criteria'] = ['C1', 'C2']
        draft['milestones'].append({'id': 'M2', 'objective': 'Unicode flow', 'acceptance_criteria': ['C3'], 'depends_on': []})
        goals.install_draft(self.state, draft, origin='test')
        goals.present(self.state)
        goals.approve(self.state, self.state['displayed_goal'])
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
        events.write_text(json.dumps({'type': 'item.completed', 'item': {'id': 'check', 'type': 'command_execution',
            'command': 'execute-milestone', 'exit_code': 0 if passed else 1, 'aggregated_output': str(statuses)}}))
        value = {**envelope(self.state), 'verdict': verdict or ('PASS' if passed else 'FAIL'),
            'checks_run': ['execute-milestone'], 'checks': [{'command': 'execute-milestone', 'exit_code': 0 if passed else 1, 'evidence_ref': 'event:check'}],
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
        goals.install_draft(self.state, draft, origin='test')
        goals.present(self.state)
        goals.approve(self.state, self.state['displayed_goal'])
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

    def test_unverified_whole_flow_cannot_complete_even_when_all_criteria_pass(self):
        self.start()
        self.validate({'C1': 'PASS', 'C2': 'PASS', 'C3': 'PASS'}, flow_status='NOT_VERIFIED')
        decision = self.decision(status='COMPLETE')
        decision['acceptance_criteria'] = [{**c, 'status': 'verified', 'evidence': 'event:check'}
                                           for c in self.state['acceptance_criteria']]
        current = s.snapshot(self.root)
        self.assertTrue(m.evidence_ready(self.state, current))
        self.assertFalse(s.completion_ready(self.state, decision, current))
        self.state['validation']['end_to_end_result']['status'] = 'PASS'
        self.assertTrue(s.completion_ready(self.state, decision, current))

    def test_review_handoff_evaluates_current_gate_without_erasing_history(self):
        self.start()
        self.validate(flow_status='NOT_VERIFIED')
        self.state['milestone_blocker'] = 'Previous evidence gate rejection'
        before = copy.deepcopy(self.state)
        prompt, _ = s.context_packet(self.state, 'astra_review', self.run / 'state.json')
        packet = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
        self.assertTrue(packet['milestone_checkpoint']['current_evidence_ready'])
        self.assertEqual('Previous evidence gate rejection', packet['milestone_checkpoint']['blocker'])
        self.assertEqual(before, self.state)
        self.state['validation']['end_to_end_result']['status'] = 'FAIL'
        prompt, _ = s.context_packet(self.state, 'astra_review', self.run / 'state.json')
        packet = json.loads(prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
        self.assertFalse(packet['milestone_checkpoint']['current_evidence_ready'])

    def test_explicit_checkpoint_answer_preserves_approval_and_requires_current_evidence(self):
        self.start()
        self.validate(flow_status='NOT_VERIFIED')
        choice = 'Reconcile M1 as accepted based on the existing current-revision Sol evidence, then resume at M2.'
        question = {'id': 'decision-checkpoint', 'question': 'Reconcile the checkpoint?',
                    'why': 'The earlier gate rejected advancement', 'options': [choice], 'proposed_default': ''}
        self.state.update(status='WAITING_FOR_USER', phase='WAITING_FOR_USER', next_stage='astra_review',
                          pending_questions=[question], user_request={'kind': 'blocker', 'proposed_delta': '',
                          'discovered': 'The milestone checkpoint remains unaccepted despite passing Sol evidence.',
                          'options': [choice]})
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, 'explicit saved choice'):
            goals.resolve_passing_checkpoint(self.state, question['id'], 'Resume anyway')
        self.assertEqual(before, self.state)
        self.state['validation']['end_to_end_result']['status'] = 'FAIL'
        with self.assertRaisesRegex(ValueError, 'independent evidence'):
            goals.resolve_passing_checkpoint(self.state, question['id'], choice)
        self.state['validation']['end_to_end_result']['status'] = 'NOT_VERIFIED'
        goals.resolve_passing_checkpoint(self.state, question['id'], choice)
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
        self.assertEqual('WAITING_FOR_USER', self.state['status'])
        self.assertEqual(['C1'], self.state['user_request']['criteria'])
        goals.present(self.state)
        goals.approve_review(self.state, 'C1', goals.review_token(self.state), s.snapshot(self.root))
        self.assertEqual('RUNNING', self.state['status'])
        self.assertIn('C3', goals.missing_human_reviews(self.state))
        self.assign('M2')
        self.assertEqual('M2', self.state['current_task']['milestone_id'])

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


if __name__ == '__main__':
    unittest.main()
