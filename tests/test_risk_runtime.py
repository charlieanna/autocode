"""Public planning schemas and risk-proof projections across existing flows."""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import autocode_brief_obligations as brief
import autocode_check_replay as replay
import autocode_planning_artifacts as artifacts
import autocode_risk_obligations as risk
import autocode_run_view as run_view
import autocode_verification_plan as verification_plan
from goal_fixtures import body
from units import autoplanner

from tests.test_risk_obligations import RiskFixture


class RiskRuntimeTests(RiskFixture, unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='risk-runtime-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def state(self, *, task=None, adaptive=False, v2=False, both=False):
        case = self.root / ('case-' + str(len(list(self.root.iterdir()))))
        case.mkdir()
        state = self.fixture(case, task=task, both=both)
        state['settings'].update(joint_planning=True, adaptive_planning=adaptive,
            planning_flow='v2' if v2 else 'v1', engine='codex', roles={
                role: {'engine': 'codex', 'model': 'fixture'}
                for role in ('requirements', 'glm', 'plan_reviewer', 'astra')})
        draft = body()
        draft.update(acceptance_criteria=[{**row, 'human_review': False,
            'verification_method': 'Read the approved public API and its executed behavior'}
            for row in self.draft()['acceptance_criteria']], accepted_assumptions=[])
        draft['milestones'][0]['acceptance_criteria'] = ['AC-queue', 'AC-outbox']
        state['goal_contract'] = {'body': draft, 'revision': 1, 'hash': 'fixture-contract'}
        state['planning'] = {'reports': {}, 'astra_calls': 0, 'final_token': None}
        return state

    def request(self, state, stage):
        if stage in artifacts.PREDECESSOR_STAGES:
            previous = artifacts.PREDECESSOR_STAGES[stage]
            prepared = artifacts.prepare(state, previous,
                {'contract': copy.deepcopy(state['goal_contract']['body']), 'summary': 'Retained predecessor'},
                input_override=(None, None))
            for kind in ('artifact', 'delta'):
                path = self.root / prepared[kind]['path']
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(prepared[kind + '_bytes'])
        return autoplanner.prepare(state, stage, self.root / 'state.json', self.root)

    @staticmethod
    def packet(request):
        return json.loads(request.prompt.split('CURRENT HANDOFF DATA\n', 1)[1])

    def test_default_adaptive_and_v2_reviewers_receive_source_inventory_with_only_binding_fields(self):
        for stage, adaptive, v2 in [('astra_finalize', False, False),
                                    ('astra_challenge', True, False),
                                    ('plan_finalize', False, True)]:
            with self.subTest(stage=stage):
                state = self.state(adaptive=adaptive, v2=v2)
                request = self.request(state, stage)
                strict = autoplanner.s.model_output_schema(request.schema)
                proposal = strict['properties']['risk_observations']['items']
                self.assertEqual({'declaration_id', 'criterion_ids', 'module'}, set(proposal['properties']))
                self.assertFalse(proposal['additionalProperties'])
                self.assertIn('risk_observation_changes', strict['properties'])
                packet = self.packet(request)
                declarations = packet['risk_declaration_inventory']
                self.assertEqual(1, len(declarations))
                declaration = declarations[0]
                self.assertEqual('lease_queue_lifecycle_v1', declaration['protocol'])
                self.assertEqual(['leasequeue'], declaration['allowed_modules'])
                self.assertEqual(hashlib.sha256(state['task'].encode()).hexdigest(), declaration['source_sha256'])
                self.assertEqual(state['task'][slice(*declaration['source_span'])], declaration['source_quote'])
                self.assertIn(autoplanner.RISK_OBSERVATION_REVIEW_RULE, request.prompt)
                if 'contract' in strict['properties']:
                    self.assertNotIn('risk_acceptance', strict['properties']['contract']['properties'])

    def test_planners_and_requirements_cannot_author_risk_binding_or_provenance(self):
        for stage, v2 in [('requirements_gather', False), ('astra_discovery', False),
                          ('glm_revise', False), ('requirements', True), ('plan', True),
                          ('plan_revise', True)]:
            with self.subTest(stage=stage):
                state = self.state(v2=v2)
                request = self.request(state, stage)
                strict = autoplanner.s.model_output_schema(request.schema)
                self.assertNotIn('risk_observations', strict['properties'])
                self.assertNotIn('risk_observation_changes', strict['properties'])
                for field in ('contract', 'requirements'):
                    if strict['properties'].get(field, {}).get('type') == 'object':
                        self.assertNotIn('risk_acceptance', strict['properties'][field]['properties'])
                self.assertNotIn('risk_declaration_inventory', self.packet(request))

    def test_ordinary_tasks_keep_reviewer_schemas_without_empty_risk_fields(self):
        for stage, v2 in [('astra_challenge', False), ('astra_finalize', False), ('plan_finalize', True)]:
            with self.subTest(stage=stage):
                state = self.state(task='Build a local greeting CLI', v2=v2)
                request = self.request(state, stage)
                strict = autoplanner.s.model_output_schema(request.schema)
                self.assertNotIn('risk_observations', strict['properties'])
                self.assertNotIn('risk_observation_changes', strict['properties'])
                self.assertEqual([], self.packet(request)['risk_declaration_inventory'])
                self.assertNotIn(autoplanner.RISK_OBSERVATION_REVIEW_RULE, request.prompt)

    def test_verification_preview_retains_original_inventory_while_current_task_selects_one_protocol(self):
        state = self.state(both=True)
        reviewed, _ = self.install(state, draft=state['goal_contract']['body'])
        state['current_task'] = {'acceptance_criteria': ['AC-outbox']}
        before = copy.deepcopy(state)
        preview = verification_plan.obligations(state)
        lifecycle = preview['lifecycle_risks']
        self.assertEqual({'lease_queue_lifecycle_v1', 'transactional_outbox_lifecycle_v1'},
                         {row['protocol'] for row in lifecycle['inventory']})
        self.assertEqual(reviewed['risk_acceptance'], lifecycle['protected_manifest'])
        self.assertEqual(['transactional_outbox_lifecycle_v1'],
                         [row['protocol'] for row in risk.observations(state)])
        self.assertEqual(before, state, 'A preview or selection must not approve or mutate a plan')

    def test_whole_product_claim_selects_every_risk_but_partial_or_contribution_claims_do_not(self):
        state = self.state(both=True)
        self.install(state, draft=state['goal_contract']['body'])
        state['current_task'] = {'acceptance_criteria': ['AC-outbox']}
        whole = {'criterion_results': [{'id': cid, 'status': 'PASS', 'evidence_refs': ['event:executed']}
                    for cid in ('AC-queue', 'AC-outbox')]}
        final = {'required_checks': [{'relation': 'fully_verify',
                                      'criterion_ids': ['AC-queue', 'AC-outbox']}],
                 'outstanding_criteria': []}
        self.assertTrue(brief.whole_product_claim(state, whole, progressive_context=final))
        self.assertEqual(2, len(risk.observations(state, progressive_context=final,
            all_observations=brief.whole_product_claim(state, whole, progressive_context=final))))
        partial = {**whole, 'criterion_results': whole['criterion_results'][1:]}
        contribution = {**final, 'required_checks': [{'relation': 'contributes_to',
                                                     'criterion_ids': ['AC-queue', 'AC-outbox']}]}
        stale_slice = {**final, 'outstanding_criteria': ['AC-queue']}
        no_evidence = copy.deepcopy(whole); no_evidence['criterion_results'][0]['evidence_refs'] = []
        duplicate = {**whole, 'criterion_results': [*whole['criterion_results'], whole['criterion_results'][0]]}
        for report, context in [(partial, None), (whole, contribution), (whole, stale_slice),
                                (no_evidence, final), (duplicate, final)]:
            with self.subTest(report=report, context=context):
                self.assertFalse(brief.whole_product_claim(state, report, progressive_context=context))
        self.assertEqual([], risk.observations(state, progressive_context=contribution))
        self.assertEqual(2, len(risk.observations(state, progressive_context=contribution, all_observations=True)))

    def test_replay_pins_and_public_view_retain_risk_transcripts_without_replacing_existing_evidence(self):
        risk_receipt = {'verdict': 'PASS', 'source_revision': 'source-one',
            'checks': [{'output': '/fixture/risk-output.json', 'output_sha256': 'r' * 64}],
            'summary': '/fixture/risk-summary.json', 'summary_sha256': 's' * 64}
        saved = {'verdict': 'PASS', 'source_revision': 'source-one',
            'checks': [{'output': '/fixture/check.txt', 'output_sha256': 'c' * 64}],
            'risk_acceptance': risk_receipt, 'brief_acceptance': {
                'checks': [{'output': '/fixture/brief.json', 'output_sha256': 'b' * 64}]}}
        self.assertEqual({'/fixture/check.txt': 'c' * 64, '/fixture/brief.json': 'b' * 64,
                          '/fixture/risk-output.json': 'r' * 64, '/fixture/risk-summary.json': 's' * 64},
                         replay.evidence_pins(saved))
        state = {'validation': {'check_replay': saved}}
        visible = run_view.evidence(state)['check_replay']
        self.assertEqual(risk_receipt, visible['risk_acceptance'])
        visible['risk_acceptance']['checks'][0]['output'] = 'edited-view'
        self.assertEqual('/fixture/risk-output.json', saved['risk_acceptance']['checks'][0]['output'])


if __name__ == '__main__':
    unittest.main()
