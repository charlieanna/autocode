"""Capture provenance guards, separate from independent visual judgment."""
import copy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from tests.visual_capture_fixtures import make_capture
import autocode_visual_evidence as visual
import autocode_util as util
import autocode_contract_identity as identity
import goal_fixtures


class CaptureEvidenceTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=T', '-c', 'user.email=t@example.test',
                        'commit', '-q', '--allow-empty', '-m', 'base'], check=True)
        (self.root / 'greet.py').write_text('print("A")\n')
        self.case = {'id': 'screen.empty', 'route': '/screen', 'state': 'empty',
                     'viewport': {'width': 2, 'height': 1, 'device_scale_factor': 1}}
        self.state = {'workspace': str(self.root), 'settings': {'design_manifest': {
            'manifest_hash': 'a' * 64, 'body': {'cases': [self.case]}}}}
        self.a = make_capture(self.root, 'a' * 64, self.case)

    def verify(self, record):
        return visual.verify(self.state, record['capture_ref'], record['capture_sha256'], case=self.case)

    def test_context_selects_current_b_and_rejects_historical_a_without_removing_it(self):
        old = Path(self.a['candidate_ref']).read_bytes()
        (self.root / 'greet.py').write_text('print("B")\n')
        b = make_capture(self.root, 'a' * 64, self.case)
        context = visual.context(self.state, util.snapshot(self.root))
        self.assertEqual([b['capture_ref']], [row['capture_ref'] for row in context['current']])
        self.assertIsNone(context['visual_acceptance'])
        with self.assertRaisesRegex(ValueError, 'Stale implementation capture'):
            self.verify(self.a)
        self.assertEqual(old, Path(self.a['candidate_ref']).read_bytes())
        self.verify(b)

    def test_changed_source_between_selection_and_report_is_refused(self):
        selected = visual.context(self.state, util.snapshot(self.root))['current'][0]
        (self.root / 'greet.py').write_text('print("after capture")\n')
        with self.assertRaisesRegex(ValueError, 'Stale implementation capture'):
            self.verify(selected)

    def test_missing_manifest_and_tampered_screenshot_script_or_manifest_are_refused(self):
        for kind in ('missing', 'image', 'script', 'manifest'):
            with self.subTest(kind=kind):
                item = make_capture(self.root, 'a' * 64, self.case)
                path = Path(item['capture_ref'])
                body = json.loads(path.read_text())
                if kind == 'missing': path.unlink()
                if kind == 'image': Path(item['candidate_ref']).write_bytes(b'tampered')
                if kind == 'script':
                    config = util.read_object(self.root / body['config_ref'])
                    (self.root / config['fixture']).write_text('// changed script')
                if kind == 'manifest': path.write_text(json.dumps({**body, 'source_revision': 'forged'}))
                with self.assertRaises(ValueError): self.verify(item)

    def test_loaded_asset_mismatch_is_refused_even_with_matching_container_hashes(self):
        path = Path(self.a['capture_ref'])
        body = util.read_object(path)
        browser_path = self.root / body['artifacts']['browser']['path']
        browser = util.read_object(browser_path)
        browser['responses'][0]['sha256'] = '0' * 64
        util.atomic_json(browser_path, browser)
        body['artifacts']['browser']['sha256'] = util.file_hash(browser_path)
        util.atomic_json(path, body)
        with self.assertRaisesRegex(ValueError, 'Served asset differs'):
            self.verify({**self.a, 'capture_sha256': util.file_hash(path)})

    def test_reference_state_route_and_viewport_cannot_be_substituted(self):
        for key, value in (('id', 'another'), ('state', 'filled'), ('route', '/different'),
                           ('viewport', {'width': 3, 'height': 1, 'device_scale_factor': 1})):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'different case'):
                visual.verify(self.state, self.a['capture_ref'], self.a['capture_sha256'], case={**self.case, key: value})
        other = copy.deepcopy(self.state)
        other['settings']['design_manifest']['manifest_hash'] = 'b' * 64
        with self.assertRaisesRegex(ValueError, 'different design'):
            visual.verify(other, self.a['capture_ref'], self.a['capture_sha256'])

    def test_an_additional_historical_image_cannot_hide_beside_a_current_capture(self):
        (self.root / 'greet.py').write_text('print("B")\n')
        b = make_capture(self.root, 'a' * 64, self.case)
        report = {'criterion_results': [{'status': 'PASS', 'evidence_refs': [b['candidate_ref'], self.a['candidate_ref']]}]}
        with self.assertRaisesRegex(ValueError, 'historical images cannot earn acceptance'):
            visual.require_current_image_citations(self.state, report, {b['candidate_ref']})

    def test_malformed_manifest_is_a_clear_refusal_not_an_uncaught_type_error(self):
        path = Path(self.a['capture_ref'])
        body = util.read_object(path)
        body['artifacts'] = None
        util.atomic_json(path, body)
        with self.assertRaisesRegex(ValueError, 'Invalid or incomplete'):
            self.verify({**self.a, 'capture_sha256': util.file_hash(path)})

    def test_native_figma_also_requires_bound_capture_and_actual_image_citation(self):
        self.state['settings'] = {'figma_file': 'https://www.figma.com/design/FILE/Fixture'}
        item = make_capture(self.root, visual.reference_hash(self.state['settings']), self.case)
        report = {'verdict': 'PASS', 'criterion_results': []}
        with self.assertRaisesRegex(ValueError, 'current implementation capture'):
            visual.native_refs(self.state, report)
        report['implementation_captures'] = [item]
        with self.assertRaisesRegex(ValueError, 'cite the captured'):
            visual.native_refs(self.state, report)
        report['criterion_results'] = [{'evidence_refs': [item['candidate_ref']]}]
        self.assertIn(item['candidate_ref'], visual.native_refs(self.state, report))

    def test_native_nonvisual_milestone_can_pass_without_claiming_final_visual_acceptance(self):
        self.state['settings'] = {'figma_file': 'https://www.figma.com/design/FILE/Fixture'}
        self.state['acceptance_criteria'] = [{'id': 'C1'}, {'id': 'C2'}]
        report = {'verdict': 'PASS', 'criterion_results': [{'id': 'C1', 'status': 'PASS', 'evidence_refs': []}]}
        self.assertEqual([], visual.native_refs(self.state, report))
        report['criterion_results'].append({'id': 'C2', 'status': 'PASS', 'evidence_refs': []})
        with self.assertRaisesRegex(ValueError, 'current implementation capture'):
            visual.native_refs(self.state, report)

    def approve_functional_scope(self):
        body = goal_fixtures.body()
        body['scope_exclusions'].append('Visual acceptance')
        goal = {'task_id': 'functional-reference-consumer', 'revision': 1, 'body': body,
                'approval_status': 'approved'}
        goal['hash'] = util.digest({key: goal[key] for key in ('task_id', 'revision', 'body')})
        goal['approval_event'] = {'actor': 'user_cli', 'token': identity.token(goal)}
        self.state.update(goal_contract=goal, user_events=[copy.deepcopy(goal['approval_event'])],
                          acceptance_criteria=copy.deepcopy(body['acceptance_criteria']))
        self.state['settings'] = {'figma_file': 'https://www.figma.com/design/FILE/Fixture'}
        return {'verdict': 'PASS', 'criterion_results': [{'id': 'C1', 'status': 'PASS', 'evidence_refs': []}]}

    def test_explicit_approved_functional_only_scope_does_not_claim_visual_pass(self):
        report = self.approve_functional_scope()
        self.assertEqual([], visual.native_refs(self.state, report))
        report['implementation_captures'] = [{'capture_ref': 'missing.json', 'capture_sha256': 'a' * 64}]
        with self.assertRaises(ValueError):
            visual.native_refs(self.state, report)

    def test_unapproved_or_conflicting_exclusion_never_skips_visual_proof(self):
        for change in ('unapproved', 'changed_body', 'prose', 'strict_visual'):
            with self.subTest(change=change):
                report = self.approve_functional_scope()
                goal = self.state['goal_contract']
                if change == 'unapproved':
                    goal['approval_status'] = 'draft'
                elif change == 'changed_body':
                    goal['body']['required_behaviors'].append('Changed scope')
                else:
                    if change == 'prose':
                        goal['body']['scope_exclusions'][-1] = 'Do not skip visual acceptance'
                    else:
                        goal['body']['constraints'].append('VISUAL_CASE_CRITERIA={"case":["C1"]}')
                    goal['hash'] = util.digest({key: goal[key] for key in ('task_id', 'revision', 'body')})
                    goal['approval_event']['token'] = identity.token(goal)
                    self.state['user_events'] = [copy.deepcopy(goal['approval_event'])]
                with self.assertRaisesRegex(ValueError, 'current implementation capture'):
                    visual.native_refs(self.state, report)
