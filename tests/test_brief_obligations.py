"""Public planning and original-brief policy controls; no provider is launched."""
from __future__ import annotations

import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import autocode_brief_obligations as brief
import autocode_conversation as conversation
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autopilot
from units import autoplanner
from goal_fixtures import body


TASK = ('Commands: `todo.py add TEXT` appends a to-do and exits 0; '
        '`todo.py list` prints every to-do as `ID TEXT [open|done]` one per line and exits 0; '
        '`todo.py complete ID` marks the to-do done and exits 0.')


class BriefObligationsTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.record_count = 0

    def test_full_criterion_claim_selects_whole_product_observations(self):
        state = {'goal_contract': {'body': {'acceptance_criteria': [{'id': 'C1'}, {'id': 'C2'}]}}}
        full = {'criterion_results': [{'id': 'C1', 'status': 'PASS', 'evidence_refs': ['execution:1']},
                                     {'id': 'C2', 'status': 'PASS', 'evidence_refs': ['execution:2']}]}
        self.assertTrue(brief.whole_product_claim(state, full))
        self.assertFalse(brief.whole_product_claim(state, {'criterion_results': full['criterion_results'][1:]}))
        partial = copy.deepcopy(full)
        partial['criterion_results'][0]['status'] = 'NOT_VERIFIED'
        self.assertFalse(brief.whole_product_claim(state, partial))
        missing = copy.deepcopy(full)
        missing['criterion_results'][0]['evidence_refs'] = []
        self.assertFalse(brief.whole_product_claim(state, missing))
        self.assertFalse(brief.whole_product_claim({}, full))
        human = {'goal_contract': {'body': {'acceptance_criteria': [{'id': 'C1', 'human_review': True}]}}}
        self.assertFalse(brief.whole_product_claim(human, full))
        context = {'required_checks': [{'relation': 'contributes_to', 'criterion_ids': ['C1', 'C2']}],
                   'outstanding_criteria': []}
        self.assertFalse(brief.whole_product_claim(state, full, progressive_context=context))
        context['required_checks'][0]['relation'] = 'fully_verify'
        self.assertTrue(brief.whole_product_claim(state, full, progressive_context=context))
        context['outstanding_criteria'] = ['C2']
        self.assertFalse(brief.whole_product_claim(state, full, progressive_context=context))

    def state(self, *, task=TASK, adaptive=False, v2=False):
        return {'version': 3, 'task_id': 'task', 'task': task, 'workspace': str(self.root),
                'settings': {'joint_planning': True, 'adaptive_planning': adaptive,
                             'planning_flow': 'v2' if v2 else 'default',
                             'roles': {'plan_reviewer': {}}},
                'answers': {}, 'user_events': [], 'stages': [], 'acceptance_criteria': [],
                'status': 'WAITING_FOR_USER'}

    def contract_body(self, task=TASK, *, initial=False, second=False):
        draft = body()
        draft['required_behaviors'] = [task]
        draft['deliverables'] = ['todo.py']
        draft['acceptance_criteria'][0]['criterion'] = task
        draft['milestones'][0]['affected_paths'] = ['todo.py']
        if second:
            draft['acceptance_criteria'].append({**draft['acceptance_criteria'][0], 'id': 'C2'})
            draft['milestones'][0]['acceptance_criteria'].append('C2')
        if initial:
            draft['initial_task'] = {'objective': 'Deliver the CLI', 'affected_paths': ['todo.py'],
                'kind': 'implement', 'milestone_id': 'M1', 'requirements': ['Deliver the CLI'],
                'acceptance_criteria': ['C1'], 'validation_plan': ['Run CLI regression tests']}
        return draft

    def proposal(self, declaration, *, criterion='C1', probe='brief-probe'):
        return {'declaration_id': declaration['id'], 'criterion_ids': [criterion],
                'steps': [{'argv': ['add', probe]}, {'argv': ['list']}], 'observe_step': 1,
                'bindings': [{'placeholder': 'TEXT', 'step': 0, 'argument': 1}]}

    def reviewer_record(self, state, report, *, stage='astra_finalize'):
        """Persist the raw fixture report first; policy derives every evidence hash itself."""
        self.record_count += 1
        stem = f'{stage}-{self.record_count}'
        output = self.root / (stem + '.json')
        output.write_text(json.dumps(report))
        events = self.root / (stem + '.jsonl')
        events.write_text(json.dumps({'type': 'item.completed', 'item': {
            'type': 'agent_message', 'text': json.dumps(report)}}) + '\n')
        record = {'stage': stage, 'role': 'astra', 'route_role': 'plan_reviewer',
                  'engine': 'fixture', 'task_id': state['task_id'], 'exit_code': 0,
                  'output': str(output), 'events': str(events)}
        state['stages'].append(record)
        return record

    def reviewed(self, state, *, draft=None, proposals=None, changes=(), stage='astra_finalize'):
        draft = draft or self.contract_body()
        if proposals is None:
            proposals = [self.proposal(row) for row in brief.inventory(state)]
        report = {'contract': draft, 'brief_observations': proposals,
                  'brief_observation_changes': list(changes), 'summary': 'Fixture review'}
        record = self.reviewer_record(state, report, stage=stage)
        result = brief.reviewed_body(state, draft, proposals, record, changes=changes)
        return result, record

    def install_reviewed(self, state, **kwargs):
        reviewed, record = self.reviewed(state, **kwargs)
        lifecycle.install_draft(state, reviewed, origin=record['stage'], record=record)
        return reviewed, record

    def feedback(self, state, text):
        state['status'] = 'AWAITING_GOAL_APPROVAL'
        goals.feedback(state, text)
        return state['brief_feedback'][-1]

    def answer(self, state, text, *, question='Q1', delegated=False):
        state['pending_questions'] = [{'id': question, 'question': 'Which output?', 'why': 'Observable behavior',
            'options': [], 'proposed_default': text, 'kind': 'decision', 'category': 'behavior', 'delegable': True}]
        goals.answer(state, question, text, delegated=delegated)
        return state['answers'][question]

    def amend(self, state, literal):
        old = state['goal_contract']['body']['brief_acceptance']['manifest']['observations'][0]
        event = self.feedback(state, 'Replace ' + old['declaration']['literal'] + ' with: ' +
                              TASK.replace('ID TEXT [open|done]', literal))
        declaration = next(row for row in brief.inventory(state) if row['source_id'] == 'feedback:' + event['id'])
        change = {'previous_hash': old['hash'], 'declaration_id': declaration['id'],
                  'source_event_id': declaration['source_id']}
        reviewed, record = self.install_reviewed(state, proposals=[self.proposal(declaration)], changes=[change])
        return declaration, reviewed, record

    def test_unstructured_task_remains_exact_source_and_unsupported_task_is_compatible(self):
        state = self.state()
        declaration = brief.inventory(state)[0]
        self.assertEqual('task:0', declaration['source_id'])
        self.assertEqual('ID TEXT [open|done]', declaration['literal'])
        self.assertEqual(TASK[slice(*declaration['span'])], declaration['quote'])
        unrelated = self.state(task='Build a useful CLI')
        draft = self.contract_body()
        self.assertEqual(draft, brief.reviewed_body(unrelated, draft, [], {}))
        brief.validate_body(unrelated, draft, ready=True)
        self.assertEqual([], brief.commands(unrelated))

    def test_conversation_envelope_scans_only_genuine_user_messages(self):
        stamp = '2026-10-05T00:00:00Z'
        messages = [{'id': 'u1', 'role': 'user', 'speaker': 'You', 'text': TASK, 'created_at': stamp,
                     'status': 'saved', 'client_request_id': 'request1', 'logical_turn_id': 'turn1', 'in_reply_to': None},
                    {'id': 'a1', 'role': 'assistant', 'speaker': 'Planner', 'text': TASK.replace('todo.py', 'invented.py'),
                     'created_at': stamp, 'status': 'saved', 'client_request_id': None,
                     'logical_turn_id': 'reply1', 'in_reply_to': 'turn1'}]
        document = {'id': 'a' * 32, 'title': 'CLI', 'created_at': stamp, 'messages': messages,
                    'drafts': [], 'requirements': {'revisions': [], 'provenance': []},
                    'configured_routes': {'requirements_gatherer': {'engine': 'codex', 'provider': 'codex',
                                                                   'model': 'fixture', 'reasoning_effort': 'high'}}}
        task = conversation.task_text(conversation.handoff_from_document(document))
        declarations = brief.inventory(self.state(task=task))
        self.assertEqual(['todo.py'], [row['program'] for row in declarations])
        self.assertEqual('conversation_user', declarations[0]['source_kind'])

    def test_model_criteria_and_unpaired_or_agent_events_cannot_create_sources(self):
        state = self.state(task='Build a CLI')
        state['goal_contract'] = {'body': self.contract_body(TASK)}
        event = {'kind': 'brief_feedback', 'actor': 'astra', 'id': 'agent', 'text': TASK}
        state['brief_feedback'] = [event, {**event, 'actor': 'user_cli', 'id': 'unpaired'}]
        state['user_events'] = [event, {**event, 'actor': 'user_cli', 'id': 'not-feedback'}]
        state['answers'] = {'Q1': {'kind': 'answer', 'actor': 'user_cli', 'question_id': 'Q1', 'text': TASK}}
        self.assertEqual([], brief.inventory(state))

    def test_actual_answer_is_a_source_and_delegated_model_default_is_not(self):
        state = self.state(task='Choose CLI output')
        self.answer(state, TASK, delegated=True)
        self.assertEqual([], brief.inventory(state))
        self.answer(state, TASK, question='Q2')
        declaration = brief.inventory(state)[0]
        self.assertEqual(('answer:Q2', 'user_answer'), (declaration['source_id'], declaration['source_kind']))

    def test_public_feedback_and_applied_intervention_preserve_human_event_order(self):
        state = self.state(task='Choose CLI output')
        feedback = self.feedback(state, TASK)
        self.answer(state, TASK.replace('todo.py', 'answer.py'))
        goals.apply_intervention_feedback(state, {'id': 'intent-1', 'text': TASK.replace('todo.py', 'queued.py'),
                                                  'observed_goal_token': ''}, {'applied_at': 'now'})
        human_sources = [row for row in brief.sources(state) if row['kind'] != 'task']
        expected = ['feedback:' + feedback['id'], 'answer:Q1', 'feedback:intervention-intent-1']
        self.assertEqual(expected, [row['id'] for row in human_sources])
        self.assertEqual(['user_feedback', 'user_answer', 'user_intervention'],
                         [row['kind'] for row in human_sources])
        # Compiler inventory has deterministic ID order; replacement authority uses source chronology.
        self.assertCountEqual(expected, [row['source_id'] for row in brief.inventory(state)])

    def test_only_reviewer_stage_can_bind_even_with_real_fixture_files(self):
        state = self.state()
        proposals = [self.proposal(brief.inventory(state)[0])]
        report = {'brief_observations': proposals}
        record = self.reviewer_record(state, report, stage='glm_revise')
        with self.assertRaisesRegex(ValueError, 'Only the independent Plan Reviewer'):
            brief.reviewed_body(state, self.contract_body(), proposals, record)

    def test_reviewer_binding_requires_both_original_files_and_rejects_symlinks(self):
        for field in ('events', 'output'):
            for fault in ('missing', 'symlink'):
                with self.subTest(field=field, fault=fault):
                    state = self.state()
                    proposal = self.proposal(brief.inventory(state)[0])
                    record = self.reviewer_record(state, {'brief_observations': [proposal]})
                    path = Path(record[field])
                    if fault == 'missing':
                        path.unlink()
                    else:
                        link = path.with_suffix('.link'); link.symlink_to(path)
                        record[field] = str(link)
                    with self.assertRaisesRegex(ValueError, 'actual Plan Reviewer'):
                        brief.reviewed_body(state, self.contract_body(), [proposal], record)

    def test_a_proposal_absent_from_the_raw_reviewer_report_cannot_be_installed(self):
        state = self.state()
        proposal = self.proposal(brief.inventory(state)[0])
        record = self.reviewer_record(state, {'brief_observations': []})
        with self.assertRaisesRegex(ValueError, 'provenance is missing or changed'):
            brief.reviewed_body(state, self.contract_body(), [proposal], record)

    def test_both_report_and_event_bytes_are_pinned_after_binding(self):
        for field in ('events', 'output'):
            with self.subTest(field=field):
                state = self.state()
                reviewed, record = self.reviewed(state)
                Path(record[field]).write_text('changed evidence')
                with self.assertRaisesRegex(ValueError, 'provenance is missing or changed'):
                    brief.validate_body(state, reviewed, ready=True)

    def test_removing_a_linked_criterion_cannot_hide_a_retained_observation(self):
        state = self.state()
        reviewed, _ = self.install_reviewed(state)
        revised = copy.deepcopy(reviewed)
        revised['acceptance_criteria'] = []
        with self.assertRaisesRegex(ValueError, 'unknown acceptance criterion'):
            brief.validate_body(state, revised, ready=True)

    def test_unknown_criterion_id_cannot_link_an_original_brief_observation(self):
        state = self.state()
        proposals = [self.proposal(brief.inventory(state)[0], criterion='missing')]
        with self.assertRaisesRegex(ValueError, 'unknown acceptance criterion'):
            self.reviewed(state, proposals=proposals)

    def test_approval_and_command_admission_require_review_of_every_supported_declaration(self):
        state = self.state()
        draft = self.contract_body()
        brief.validate_body(state, draft)
        for admit in (lambda: brief.validate_body(state, draft, ready=True),
                      lambda: brief.reviewed_body(state, draft, [], self.reviewer_record(state, {'brief_observations': []}))):
            with self.assertRaises(ValueError):
                admit()
        lifecycle.install_draft(state, draft, origin='glm_draft')
        with self.assertRaisesRegex(ValueError, 'independent Plan Reviewer'):
            brief.commands(state)

    def test_planner_omission_preserves_wrapper_but_authoring_or_tampering_refuses(self):
        state = self.state()
        reviewed, _ = self.install_reviewed(state)
        previous = copy.deepcopy(reviewed['brief_acceptance'])
        draft = copy.deepcopy(reviewed); draft.pop('brief_acceptance')
        lifecycle.install_draft(state, draft, origin='glm_revise')
        self.assertEqual(previous, state['goal_contract']['body']['brief_acceptance'])
        forged = copy.deepcopy(reviewed); forged['brief_acceptance']['manifest']['hash'] = 'forged'
        with self.assertRaisesRegex(ValueError, 'cannot replace'):
            lifecycle.install_draft(state, forged, origin='glm_revise')
        unreviewed = self.state()
        with self.assertRaisesRegex(ValueError, 'cannot replace'):
            lifecycle.install_draft(unreviewed, reviewed, origin='glm_draft')

    def test_new_genuine_source_allows_pending_draft_but_blocks_approval_until_review(self):
        state = self.state()
        reviewed, _ = self.install_reviewed(state)
        previous = copy.deepcopy(reviewed['brief_acceptance'])
        self.feedback(state, TASK.replace('todo.py', 'other.py'))
        draft = copy.deepcopy(reviewed); draft.pop('brief_acceptance')
        lifecycle.install_draft(state, draft, origin='glm_revise')
        brief.validate_body(state, state['goal_contract']['body'])
        self.assertEqual(previous, state['goal_contract']['body']['brief_acceptance'])
        with self.assertRaises(ValueError):
            brief.validate_body(state, state['goal_contract']['body'], ready=True)
        with self.assertRaises(ValueError):
            brief.commands(state, all_observations=True)
        self.install_reviewed(state)
        self.assertEqual(2, len(brief.observations(state, all_observations=True)))

    def test_pending_draft_exception_cannot_hide_changed_original_source(self):
        state = self.state()
        reviewed, _ = self.install_reviewed(state)
        state['task'] = TASK.replace('[open|done]', '[pending|done]')
        with self.assertRaisesRegex(ValueError, 'source inventory changed'):
            brief.validate_body(state, reviewed)

    def test_fresh_reviewer_cannot_change_an_unchanged_observation_probe(self):
        state = self.state()
        self.install_reviewed(state)
        changed = self.proposal(brief.inventory(state)[0], probe='different-probe')
        with self.assertRaisesRegex(ValueError, 'authenticated replacement'):
            self.reviewed(state, proposals=[changed])

    def test_actual_feedback_can_replace_only_the_exact_preceding_output_obligation(self):
        state = self.state()
        reviewed, _ = self.install_reviewed(state)
        old = reviewed['brief_acceptance']['manifest']['observations'][0]
        declaration, revised, _ = self.amend(state, 'ID TEXT [pending|done]')
        wrapper = revised['brief_acceptance']
        self.assertEqual([old['declaration']['id']], wrapper['manifest']['inactive_declaration_ids'])
        self.assertEqual(declaration['id'], wrapper['manifest']['observations'][0]['declaration']['id'])
        brief.validate_body(state, revised, ready=True)

    def test_unknown_or_nonamending_human_source_cannot_authorize_replacement(self):
        for text, source_id in ((TASK.replace('[open|done]', '[pending|done]'), None),
                                ('Replace [open|done]: ' + TASK.replace('[open|done]', '[pending|done]'), 'invented')):
            with self.subTest(text=text, source_id=source_id):
                state = self.state()
                reviewed, _ = self.install_reviewed(state)
                old = reviewed['brief_acceptance']['manifest']['observations'][0]
                event = self.feedback(state, text)
                new = next(row for row in brief.inventory(state) if row['source_id'] == 'feedback:' + event['id'])
                changes = [{'previous_hash': old['hash'], 'declaration_id': new['id'],
                            'source_event_id': source_id or new['source_id']}]
                with self.assertRaisesRegex(ValueError, 'amendment|explicitly replace'):
                    self.reviewed(state, proposals=[self.proposal(new)], changes=changes)

    def test_chronological_amendment_chain_can_advance_but_cannot_point_backwards(self):
        state = self.state()
        self.install_reviewed(state)
        first, _, _ = self.amend(state, 'ID TEXT [pending|done]')
        _, revised, _ = self.amend(state, 'ID TEXT [queued|done]')
        old = revised['brief_acceptance']['manifest']['observations'][0]
        changes = [{'previous_hash': old['hash'], 'declaration_id': first['id'], 'source_event_id': first['source_id']}]
        with self.assertRaisesRegex(ValueError, 'actual human amendment'):
            self.reviewed(state, proposals=[self.proposal(first)], changes=changes)
        self.assertEqual(2, len(revised['brief_acceptance']['amendments']))
        brief.validate_body(state, revised, ready=True)

    def test_selected_task_observations_do_not_replace_completion_all_observations(self):
        task = TASK + '; ' + TASK.replace('todo.py', 'other.py')
        state = self.state(task=task)
        declarations = brief.inventory(state)
        proposals = [self.proposal(row, criterion='C' + str(i + 1)) for i, row in enumerate(declarations)]
        self.install_reviewed(state, draft=self.contract_body(task, second=True), proposals=proposals)
        state['current_task'] = {'acceptance_criteria': ['C1']}
        self.assertEqual([declarations[0]['id']], [row['declaration']['id'] for row in brief.observations(state)])
        self.assertEqual(1, len(brief.commands(state)))
        self.assertEqual(2, len(brief.observations(state, all_observations=True)))
        self.assertEqual(2, len(brief.commands(state, all_observations=True)))

    def test_contribution_only_progressive_check_does_not_admit_a_full_observation(self):
        state = self.state()
        self.install_reviewed(state)
        contribution = {'required_checks': [{'relation': 'contributes_to', 'criterion_ids': ['C1']}]}
        self.assertEqual([], brief.observations(state, progressive_context=contribution))
        self.assertEqual([], brief.commands(state, progressive_context=contribution))
        full = {'required_checks': [{'relation': 'fully_verify', 'criterion_ids': ['C1']}]}
        self.assertEqual(1, len(brief.commands(state, progressive_context=full)))
        self.assertEqual(1, len(brief.commands(state, progressive_context=contribution, all_observations=True)))

    def planning_state(self, *, adaptive=False, v2=False):
        state = self.state(adaptive=adaptive, v2=v2)
        lifecycle.install_draft(state, self.contract_body(initial=adaptive), origin='plan' if v2 else 'glm_draft')
        state.setdefault('planning', {'astra_calls': 0, 'reports': {}, 'final_token': None})
        state['planning']['reports']['plan_review' if v2 else 'astra_challenge'] = {'report': {'concerns': []}}
        return state

    def final_report(self, state, *, v2=False):
        report = {'contract': self.contract_body(initial=True), 'summary': 'Reviewed', 'decisions': [],
                  'brief_observations': [self.proposal(brief.inventory(state)[0])], 'brief_observation_changes': []}
        if not v2:
            report.update(contract_changes=[], conflict_resolutions=[], requirement_trace=[])
        return report

    def test_ordinary_final_review_installs_bound_observations_and_refuses_omitted_coverage(self):
        state = self.planning_state()
        report = self.final_report(state)
        autopilot.apply_planning(state, 'astra_finalize', report, self.reviewer_record(state, report))
        lifecycle.validate_body(state, state['goal_contract']['body'], ready=True)
        self.assertEqual('astra_finalize', state['goal_contract']['body']['brief_acceptance']['review']['stage'])
        state = self.planning_state()
        incomplete = self.final_report(state); incomplete['brief_observations'] = []
        previous = copy.deepcopy(state['goal_contract'])
        with self.assertRaisesRegex(ValueError, 'Every supported'):
            autopilot.apply_planning(state, 'astra_finalize', incomplete, self.reviewer_record(state, incomplete))
        self.assertEqual(previous, state['goal_contract'])

    def test_v2_final_review_installs_bound_observations(self):
        state = self.planning_state(v2=True)
        report = self.final_report(state, v2=True)
        record = self.reviewer_record(state, report, stage='plan_finalize')
        autopilot.apply_planning(state, 'plan_finalize', report, record)
        lifecycle.validate_body(state, state['goal_contract']['body'], ready=True)
        self.assertEqual('plan_finalize', state['goal_contract']['body']['brief_acceptance']['review']['stage'])

    def test_adaptive_challenge_binds_observations_before_early_final_approval(self):
        state = self.planning_state(adaptive=True)
        report = {'summary': 'Reviewed', 'concerns': [], 'brief_observations': [self.proposal(brief.inventory(state)[0])],
                  'brief_observation_changes': []}
        autopilot.apply_planning(state, 'astra_challenge', report, self.reviewer_record(state, report, stage='astra_challenge'))
        lifecycle.validate_body(state, state['goal_contract']['body'], ready=True)
        self.assertEqual('astra_challenge', state['goal_contract']['body']['brief_acceptance']['review']['stage'])
        self.assertEqual(goals.token(state['goal_contract']), state['planning']['final_token'])

    def test_nonjoint_generation_preserves_a_parseable_handoff_json_suffix(self):
        state = self.state(task='Build greeting')
        state['settings']['joint_planning'] = False
        state['settings']['roles'] = {'astra': {'engine': 'codex', 'model': 'fixture'}}
        with patch.object(autoplanner.stage_context.support, 'snapshot',
                          return_value={'head': 'fixture', 'revision': 'fixture', 'files': {}}):
            request = autoplanner.prepare(state, 'astra_discovery', self.root / 'state.json', self.root)
        instructions, encoded_packet = request.prompt.split('CURRENT HANDOFF DATA\n', 1)
        self.assertEqual('Build greeting', json.loads(encoded_packet)['task'])
        self.assertIn('brief_acceptance in a contract is runner-owned', instructions)
        self.assertNotIn('brief_acceptance', request.schema['properties']['contract']['properties'])
        self.assertEqual((len(request.prompt.encode()) + 3) // 4, request.metrics['estimated_prompt_tokens'])

    def test_unrelated_task_reviewer_schema_and_prompt_omit_brief_fields(self):
        state = self.state(task='Build greeting')
        lifecycle.install_draft(state, self.contract_body('Build greeting'), origin='glm_draft')
        for stage in ('astra_challenge', 'astra_finalize'):
            with self.subTest(stage=stage):
                request = autoplanner.prepare(state, stage, self.root / 'state.json', self.root)
                strict = autoplanner.s.model_output_schema(request.schema)
                self.assertNotIn('brief_observations', strict['properties'])
                self.assertNotIn('brief_observation_changes', strict['properties'])
                self.assertNotIn(autoplanner.BRIEF_OBSERVATION_REVIEW_RULE, request.prompt)
                packet = json.loads(request.prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
                self.assertEqual([], packet['brief_declaration_inventory'])

    def test_generation_schema_omits_protected_wrapper_and_packet_inventory_is_reviewer_only(self):
        state = self.planning_state()
        for stage in ('astra_discovery', 'glm_revise', 'astra_finalize'):
            with self.subTest(stage=stage):
                request = autoplanner.prepare(state, stage, self.root / 'state.json', self.root)
                strict = autoplanner.s.model_output_schema(request.schema)
                self.assertNotIn('brief_acceptance', strict['properties']['contract']['properties'])
                packet = json.loads(request.prompt.split('CURRENT HANDOFF DATA\n', 1)[1])
                self.assertEqual(stage == 'astra_finalize', 'brief_declaration_inventory' in packet)
        self.assertIn('brief_acceptance', goals.BODY_SCHEMA['properties'])


if __name__ == '__main__':
    unittest.main()
