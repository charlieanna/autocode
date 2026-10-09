"""Public source, review and admission policy for retained lifecycle obligations."""
from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import autocode_conversation as conversation
import autocode_goals as goals
import autocode_risk_obligations as obligations
import autocode_risk_targets as targets
import autocode_util as util

from tests.test_risk_acceptance import OUTBOX, QUEUE, proposal

CATALOG = Path(__file__).resolve().parents[1] / 'scenarios' / 'catalog'
SCENARIOS = {'leasequeue': 'ladder-18-durable-lease-queue',
             'outbox': 'ladder-19-transactional-outbox'}


class RiskFixture:
    """Inputs at the public policy boundary, with original raw reviewer files."""
    def fixture(self, root, *, both=False, task=None):
        root = Path(root)
        workspace = root / 'project'
        workspace.mkdir()
        modules = ['leasequeue', 'outbox'] if both else ['leasequeue']
        for module in modules:
            seed = CATALOG / SCENARIOS[module] / 'seed'
            shutil.copytree(seed / module, workspace / module)
        (workspace / 'tests').mkdir()
        (workspace / 'tests' / 'test_import.py').write_text(
            '\n'.join('import ' + module for module in modules) + '\n')
        state = {'task_id': 'fixture', 'task': task if task is not None else QUEUE,
                'workspace': str(workspace), 'settings': {}, 'answers': {}, 'user_events': [],
                'stages': [], 'status': 'WAITING_FOR_USER'}
        if both and task is None:
            self.feedback(state, OUTBOX)
        return state

    def draft(self):
        return {'acceptance_criteria': [{'id': 'AC-queue', 'criterion': 'Durable queue lifecycle'},
                                        {'id': 'AC-outbox', 'criterion': 'Durable outbox lifecycle'}]}

    def proposals(self, state):
        return [proposal(row, criteria=['AC-queue' if row['protocol'].startswith('lease_') else 'AC-outbox'])
                for row in obligations.inventory(state)]

    def record(self, state, proposals, *, stage='astra_finalize', raw=None):
        directory = Path(state['workspace']).parent / 'review'
        directory.mkdir(exist_ok=True)
        stem = str(len(state['stages']) + 1)
        report = raw if raw is not None else {'risk_observations': proposals, 'summary': 'Independent fixture review'}
        output = directory / (stem + '.json')
        events = directory / (stem + '.jsonl')
        output.write_text(json.dumps(report))
        events.write_text(json.dumps({'type': 'item.completed', 'item': {
            'type': 'agent_message', 'text': json.dumps(report)}}) + '\n')
        record = {'stage': stage, 'role': 'astra', 'route_role': 'plan_reviewer', 'engine': 'fixture',
                  'task_id': state['task_id'], 'exit_code': 0, 'output': str(output), 'events': str(events)}
        state['stages'].append(record)
        return record

    def bind(self, state, *, proposals=None, draft=None, stage='astra_finalize', changes=()):
        proposals = self.proposals(state) if proposals is None else proposals
        record = self.record(state, proposals, stage=stage)
        reviewed = obligations.reviewed_body(state, draft or self.draft(), proposals, record, changes=changes)
        return reviewed, record

    def install(self, state, **kwargs):
        reviewed, record = self.bind(state, **kwargs)
        previous = state.get('goal_contract')
        if previous:
            state.setdefault('contract_history', []).append(copy.deepcopy(previous))
        contract = {'body': reviewed, 'task_id': state['task_id'],
                    'revision': (previous or {}).get('revision', 0) + 1}
        state['goal_contract'] = {**contract, 'hash': util.digest(contract)}
        return reviewed, record

    def feedback(self, state, text):
        state['status'] = 'AWAITING_GOAL_APPROVAL'
        goals.feedback(state, text)
        return state['brief_feedback'][-1]

    def answer(self, state, text, *, ident='Q1', delegated=False):
        state['pending_questions'] = [{'id': ident, 'question': 'Which public API?', 'why': 'Lifecycle contract',
            'options': [], 'proposed_default': text, 'kind': 'decision', 'category': 'behavior', 'delegable': True}]
        goals.answer(state, ident, text, delegated=delegated)


class RiskObligationsTests(RiskFixture, unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix='risk-policy-')
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def state(self, **kwargs):
        return self.fixture(self.root, **kwargs)

    def test_ordinary_task_has_no_extra_approval_or_command_gate(self):
        state = self.state(task='Build a useful to-do CLI')
        draft = self.draft()
        self.assertEqual(draft, obligations.reviewed_body(state, draft, [], {}))
        obligations.validate_body(state, draft, ready=True)
        self.assertEqual([], obligations.commands(state))
        with self.assertRaisesRegex(ValueError, 'invent'):
            obligations.reviewed_body(state, {**draft, 'risk_acceptance': {}}, [], {})

    def test_actual_blank_imported_packages_are_captured_before_any_builder(self):
        state = self.state(both=True)
        reviewed, record = self.install(state)
        wrapper = reviewed['risk_acceptance']
        artifact = Path(wrapper['targets']['artifact'])
        self.assertEqual(util.file_hash(artifact), wrapper['targets']['sha256'])
        retained = targets.verify(util.read_object(artifact))
        self.assertEqual({'leasequeue', 'outbox'}, {row['module'] for row in retained['targets']})
        self.assertEqual({'initial_public_import'}, {row['kind'] for row in retained['targets']})
        self.assertTrue(all('class ' not in row['text'] for row in retained['files']
                            if row['path'].endswith('__init__.py')))
        self.assertEqual(record['output'], wrapper['review']['output'])
        obligations.validate_body(state, reviewed, ready=True)

    def test_one_human_task_with_both_apis_binds_the_correct_distinct_protocols(self):
        state = self.state(both=True, task=QUEUE + '\n\n' + OUTBOX)
        declarations = obligations.inventory(state)
        self.assertEqual({'lease_queue_lifecycle_v1', 'transactional_outbox_lifecycle_v1'},
                         {row['protocol'] for row in declarations})
        self.assertEqual({'task:0'}, {row['source_id'] for row in declarations})
        reviewed, _ = self.install(state)
        observations = reviewed['risk_acceptance']['manifest']['observations']
        self.assertEqual({'leasequeue': 'lease_queue_lifecycle_v1',
                          'outbox': 'transactional_outbox_lifecycle_v1'},
                         {row['target']['module']: row['protocol'] for row in observations})
        obligations.validate_body(state, reviewed, ready=True)

    def test_only_authenticated_human_events_can_originate_obligations(self):
        state = self.state(task='Choose a public API')
        state['goal_contract'] = {'task_id': state['task_id'], 'revision': 1, 'hash': 'model-contract',
            'body': {'acceptance_criteria': [{'id': 'AC-model', 'criterion': QUEUE}]}}
        event = {'kind': 'brief_feedback', 'actor': 'astra', 'id': 'model', 'text': QUEUE}
        state['brief_feedback'] = [event, {**event, 'id': 'unpaired', 'actor': 'user_cli'}]
        state['user_events'] = [event, {**event, 'id': 'different', 'actor': 'user_cli'}]
        state['answers'] = {'Q1': {'kind': 'answer', 'actor': 'user_cli', 'question_id': 'Q1', 'text': QUEUE}}
        self.assertEqual([], obligations.inventory(state))
        self.answer(state, QUEUE, ident='delegated', delegated=True)
        self.assertEqual([], obligations.inventory(state))
        self.answer(state, QUEUE, ident='Q2')
        self.assertEqual(['answer:Q2'], [row['source_id'] for row in obligations.inventory(state)])
        event = self.feedback(state, OUTBOX)
        self.assertCountEqual(['answer:Q2', 'feedback:' + event['id']],
                              [row['source_id'] for row in obligations.inventory(state)])

    def test_conversation_handoff_ignores_assistant_lifecycle_promises(self):
        stamp = '2026-10-05T00:00:00Z'
        messages = [{'id': 'u1', 'role': 'user', 'speaker': 'You', 'text': QUEUE, 'created_at': stamp,
                     'status': 'saved', 'client_request_id': 'req', 'logical_turn_id': 'turn', 'in_reply_to': None},
                    {'id': 'a1', 'role': 'assistant', 'speaker': 'Planner', 'text': OUTBOX, 'created_at': stamp,
                     'status': 'saved', 'client_request_id': None, 'logical_turn_id': 'reply', 'in_reply_to': 'turn'}]
        document = {'id': 'a' * 32, 'title': 'API', 'created_at': stamp, 'messages': messages,
                    'drafts': [], 'requirements': {'revisions': [], 'provenance': []},
                    'configured_routes': {'requirements_gatherer': {'engine': 'codex', 'provider': 'codex',
                                                                   'model': 'fixture', 'reasoning_effort': 'high'}}}
        text = conversation.task_text(conversation.handoff_from_document(document))
        rows = obligations.inventory(self.state(both=True, task=text))
        self.assertEqual(['LeaseQueue'], [row['class_name'] for row in rows])
        self.assertEqual('conversation_user', obligations.sources({'task': text})[0]['kind'])

    def test_declared_risk_without_initial_public_module_cannot_be_approved(self):
        state = self.state()
        shutil.rmtree(Path(state['workspace']) / 'leasequeue')
        obligations.validate_body(state, self.draft())
        with self.assertRaisesRegex(ValueError, 'before approval'):
            obligations.validate_body(state, self.draft(), ready=True)
        with self.assertRaises(ValueError):
            self.bind(state)

    def test_omitting_any_declared_observation_is_rejected_before_approval(self):
        state = self.state(both=True)
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            self.bind(state, proposals=self.proposals(state)[:1])
        with self.assertRaisesRegex(ValueError, 'before approval'):
            obligations.validate_body(state, self.draft(), ready=True)

    def test_planner_cannot_originate_a_wrapper_with_reviewer_shaped_files(self):
        state = self.state()
        with self.assertRaisesRegex(ValueError, 'independent Plan Reviewer'):
            self.bind(state, stage='glm_revise')

    def test_raw_reviewer_report_must_contain_the_actual_proposals(self):
        state = self.state()
        proposals = self.proposals(state)
        record = self.record(state, proposals, raw={'risk_observations': []})
        with self.assertRaisesRegex(ValueError, 'provenance is missing or changed'):
            obligations.reviewed_body(state, self.draft(), proposals, record)

    def test_missing_or_linked_original_reviewer_files_are_not_admitted(self):
        state = self.state()
        for field in ('events', 'output'):
            for fault in ('missing', 'symlink'):
                with self.subTest(field=field, fault=fault):
                    proposals = self.proposals(state)
                    record = self.record(state, proposals)
                    path = Path(record[field])
                    if fault == 'missing':
                        path.unlink()
                    else:
                        link = path.with_suffix('.link'); link.symlink_to(path)
                        record[field] = str(link)
                    with self.assertRaisesRegex(ValueError, 'actual Plan Reviewer'):
                        obligations.reviewed_body(state, self.draft(), proposals, record)

    def test_bound_reviewer_report_and_event_bytes_remain_pinned(self):
        state = self.state()
        reviewed, record = self.install(state)
        for field in ('events', 'output'):
            path = Path(record[field]); original = path.read_bytes()
            with self.subTest(field=field):
                path.write_bytes(original + b'changed')
                with self.assertRaisesRegex(ValueError, 'provenance is missing or changed'):
                    obligations.validate_body(state, reviewed, ready=True)
                path.write_bytes(original)

    def test_candidate_added_module_cannot_retarget_the_retained_initial_inventory(self):
        state = self.state()
        reviewed, _ = self.install(state)
        retained = copy.deepcopy(util.read_object(reviewed['risk_acceptance']['targets']['artifact']))
        (Path(state['workspace']) / 'decoy.py').write_text('class CorrectQueue: pass\n')
        (Path(state['workspace']) / 'leasequeue' / '__init__.py').write_text('class LeaseQueue: pass\n')
        (Path(state['workspace']) / 'tests' / 'test_import.py').write_text('import decoy\n')
        self.assertEqual(['leasequeue'], obligations.inventory(state)[0]['allowed_modules'])
        rows = self.proposals(state)
        rows[0]['module'] = 'decoy'
        with self.assertRaisesRegex(ValueError, 'original public inventory'):
            self.bind(state, proposals=rows)
        self.assertEqual(retained, util.read_object(reviewed['risk_acceptance']['targets']['artifact']))
        obligations.validate_body(state, reviewed, ready=True)

    def test_retained_target_file_or_pin_tampering_is_rejected(self):
        state = self.state()
        reviewed, _ = self.install(state)
        artifact = Path(reviewed['risk_acceptance']['targets']['artifact'])
        original = artifact.read_bytes()
        artifact.write_bytes(original + b' ')
        with self.assertRaisesRegex(ValueError, 'inventory is missing or changed'):
            obligations.validate_body(state, reviewed, ready=True)
        artifact.write_bytes(original)
        changed = copy.deepcopy(reviewed)
        changed['risk_acceptance']['targets']['sha256'] = '0' * 64
        with self.assertRaises(ValueError): obligations.validate_body(state, changed, ready=True)

    def test_manifest_edit_and_unknown_or_removed_criterion_cannot_weaken_observations(self):
        state = self.state()
        rows = self.proposals(state); rows[0]['criterion_ids'] = ['invented']
        with self.assertRaisesRegex(ValueError, 'unknown acceptance criterion'):
            self.bind(state, proposals=rows)
        reviewed, _ = self.install(state)
        changed = copy.deepcopy(reviewed); changed['acceptance_criteria'] = []
        with self.assertRaisesRegex(ValueError, 'unknown acceptance criterion'):
            obligations.validate_body(state, changed, ready=True)
        changed = copy.deepcopy(reviewed); changed['risk_acceptance']['manifest']['observations'] = []
        with self.assertRaises(ValueError): obligations.validate_body(state, changed, ready=True)

    def test_planner_omission_carries_deep_copy_but_authoring_or_editing_refuses(self):
        state = self.state()
        reviewed, _ = self.install(state)
        carried = obligations.prepare_body(state, self.draft(), 'glm_revise')
        self.assertEqual(reviewed['risk_acceptance'], carried['risk_acceptance'])
        self.assertIsNot(reviewed['risk_acceptance'], carried['risk_acceptance'])
        carried['risk_acceptance']['review']['stage'] = 'terra'
        with self.assertRaisesRegex(ValueError, 'cannot replace'):
            obligations.prepare_body(state, carried, 'glm_revise')
        with self.assertRaisesRegex(ValueError, 'before approval'):
            obligations.validate_body(state, self.draft(), ready=True)

    def test_new_human_source_can_be_pending_in_draft_but_requires_review_before_approval(self):
        state = self.state(both=True, task=QUEUE)
        reviewed, _ = self.install(state)
        self.feedback(state, OUTBOX)
        carried = obligations.prepare_body(state, self.draft(), 'glm_revise')
        obligations.validate_body(state, carried)
        with self.assertRaises(ValueError): obligations.validate_body(state, carried, ready=True)
        renewed, _ = self.install(state)
        self.assertEqual(2, len(renewed['risk_acceptance']['manifest']['observations']))
        obligations.validate_body(state, renewed, ready=True)
        self.assertNotEqual(reviewed['risk_acceptance']['manifest']['hash'], renewed['risk_acceptance']['manifest']['hash'])

    def test_edited_original_task_invalidates_the_bound_source_inventory(self):
        state = self.state()
        reviewed, _ = self.install(state)
        state['task'] += ' A changed human source.'
        with self.assertRaises(ValueError): obligations.validate_body(state, reviewed, ready=True)

    def test_current_task_selects_due_cases_but_completion_selects_all(self):
        state = self.state(both=True)
        self.install(state)
        state['current_task'] = {'acceptance_criteria': ['AC-queue']}
        selected = obligations.observations(state)
        self.assertEqual(['lease_queue_lifecycle_v1'], [row['protocol'] for row in selected])
        self.assertEqual(2, len(obligations.observations(state, all_observations=True)))
        contributes = {'required_checks': [{'relation': 'contributes_to', 'criterion_ids': ['AC-queue']}]}
        self.assertEqual([], obligations.commands(state, progressive_context=contributes))
        fully = {'required_checks': [{'relation': 'fully_verify', 'criterion_ids': ['AC-outbox']}]}
        self.assertEqual(['transactional_outbox_lifecycle_v1'], [row['protocol'] for row in
            obligations.observations(state, progressive_context=fully)])

    def test_genuine_human_amendment_requires_exact_prior_observation_and_new_source(self):
        state = self.state()
        reviewed, _ = self.install(state)
        old = reviewed['risk_acceptance']['manifest']['observations'][0]
        event = self.feedback(state, 'Change LeaseQueue: ' + QUEUE + ' Keep the database local.')
        declaration = next(row for row in obligations.inventory(state) if row['source_id'] == 'feedback:' + event['id'])
        change = {'previous_hash': old['hash'], 'declaration_id': declaration['id'],
                  'source_event_id': declaration['source_id']}
        row = proposal(declaration, criteria=['AC-queue'])
        with self.assertRaises(ValueError): self.bind(state, proposals=[row])
        with self.assertRaises(ValueError): self.bind(state, proposals=[row], changes=[{**change, 'source_event_id': 'task:0'}])
        renewed, _ = self.install(state, proposals=[row], changes=[change])
        observations = renewed['risk_acceptance']['manifest']['observations']
        self.assertEqual([declaration['id']], [item['declaration']['id'] for item in observations])
        self.assertEqual([change], renewed['risk_acceptance']['amendments'])
        obligations.validate_body(state, renewed, ready=True)


if __name__ == '__main__':
    unittest.main()
