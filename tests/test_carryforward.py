"""Revision reuse with real source snapshots and pinned offline Validator evidence."""
# path bootstrap: runtime in tools/, fakes in tests/fakes/
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / 'tools'
_FAKES = _ROOT / 'tests' / 'fakes'
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import copy
import unittest
from pathlib import Path

from . import test_milestone_checkpoints as fixtures
import autocode_carryforward as cf
import autocode_goals as goals
import autocode_milestones as m
import autocode_support as s


class CarryForwardTests(unittest.TestCase):
    setUp = fixtures.MilestoneCheckpointTests.setUp
    start = fixtures.MilestoneCheckpointTests.start
    decision = fixtures.MilestoneCheckpointTests.decision
    assign = fixtures.MilestoneCheckpointTests.assign
    validate = fixtures.MilestoneCheckpointTests.validate

    def accept_fixture(self, milestone='M1', full=False, roots=None):
        before = s.snapshot(self.root)
        path = 'greet.py' if milestone == 'M1' else 'unicode.py'
        (self.root / path).write_text('print("Hello")\n')
        after = s.snapshot(self.root)
        task = self.state['current_task']
        task['affected_paths'] = roots or [path]
        prefix = self.run / f"{self.state['goal_contract']['revision']}-{milestone}"
        for suffix, value in (('before', before), ('after', after)):
            s.atomic_json(Path(str(prefix) + f'.{suffix}.json'), value)
        output = Path(str(prefix) + '.terra.json')
        output.write_text('{}')
        record = {'role': 'terra', 'stage': 'terra', 'task_id': task['id'],
                  'contract_hash': task['contract_hash'], 'output': str(output),
                  'before_ref': str(prefix) + '.before.json', 'after_ref': str(prefix) + '.after.json',
                  'source_revision': after['revision'], 'changed_files': s.changed_paths(before, after)}
        self.state['stages'].append(record)
        self.validate({'C1': 'PASS', 'C2': 'PASS', 'C3': 'PASS' if full or milestone == 'M2' else 'NOT_VERIFIED'},
                      flow_status='PASS')
        sol = self.state['stages'][-1]
        sol.update(task_id=task['id'], contract_hash=task['contract_hash'], changed_files=[],
                   before_ref=record['after_ref'], after_ref=record['after_ref'])
        self.assertTrue(m.evidence_ready(self.state, after))
        m.accept(self.state, after)
        row = self.state['milestone_progress'][m.key(self.state)]
        self.assertIn('reuse_manifest', row, row.get('carry_forward_unavailable'))
        return copy.deepcopy(row)

    def revise(self, edit=None, approve=True):
        draft = copy.deepcopy(self.state['goal_contract']['body'])
        draft['milestones'].reverse()
        if edit:
            edit(draft)
        goals.install_draft(self.state, draft, origin='test')
        self.assertEqual(set(), m.accepted_ids(self.state))
        self.assertEqual([], cf.carry(self.state, s.snapshot(self.root)))
        if approve:
            goals.present(self.state)
            goals.approve(self.state, self.state['displayed_goal'])

    def test_reorder_carries_only_after_approval_with_original_lineage_and_restart(self):
        self.start()
        old = self.accept_fixture()
        self.revise(approve=False)
        self.assertNotIn('validation', self.state)
        goals.present(self.state)
        goals.approve(self.state, self.state['displayed_goal'])
        self.assertEqual({'M1'}, m.accepted_ids(self.state))
        new = self.state['milestone_progress'][self.state['goal_contract']['hash'] + ':M1']
        self.assertEqual(old, self.state['milestone_progress'][old['contract_hash'] + ':M1'])
        self.assertEqual(old['accepted_validation'], new['accepted_validation'])
        self.assertEqual(old['contract_hash'], new['carried_from']['from_contract_hash'])
        self.assertTrue(new['carried_from']['final_integration_required'])
        self.assertNotIn('validation', self.state)
        before = copy.deepcopy(self.state)
        self.assertEqual([], cf.carry(self.state, s.snapshot(self.root)))
        self.assertEqual(before, self.state)
        s.atomic_json(self.run / 'state.json', self.state)
        self.state = s.read(self.run / 'state.json')
        self.assertEqual({'M1'}, m.accepted_ids(self.state))
        self.assign('M2')
        self.assertEqual('M2', self.state['current_task']['milestone_id'])

    def test_unrelated_criterion_and_source_changes_do_not_rebuild_m1(self):
        self.start(); self.accept_fixture()
        (self.root / 'unrelated.txt').write_text('New M2 work')
        self.revise(lambda body: body['acceptance_criteria'][2].update(verification_method='New Unicode test'))
        self.assertEqual({'M1'}, m.accepted_ids(self.state))

    def test_material_contract_changes_revalidate(self):
        self.start(); self.accept_fixture()
        baseline = copy.deepcopy(self.state)
        edits = {
            'criterion': lambda b: b['acceptance_criteria'][0].update(criterion='Different behavior'),
            'method': lambda b: b['acceptance_criteria'][0].update(verification_method='Different verification'),
            'human': lambda b: b['acceptance_criteria'][0].update(human_review=True),
            'objective': lambda b: next(m for m in b['milestones'] if m['id'] == 'M1').update(objective='Different objective'),
            'ownership': lambda b: next(m for m in b['milestones'] if m['id'] == 'M1').update(affected_paths=['another.py']),
            'dependency': lambda b: next(m for m in b['milestones'] if m['id'] == 'M1').update(depends_on=['M2']),
            'constraint': lambda b: b['constraints'].append('New global constraint'),
        }
        for name, edit in edits.items():
            with self.subTest(name=name):
                self.state = copy.deepcopy(baseline)
                self.revise(edit)
                self.assertEqual(set(), m.accepted_ids(self.state))
                self.assertEqual('revalidate', self.state['milestone_carry_forward'][-1]['outcomes'][0]['result'])

    def test_source_edits_deletions_modes_and_owned_additions_revalidate(self):
        self.start()
        (self.root / 'package').mkdir()
        self.accept_fixture(roots=['greet.py', 'package'])
        baseline = copy.deepcopy(self.state)
        source = self.root / 'greet.py'
        for mode in ('edit', 'delete', 'mode', 'addition'):
            with self.subTest(mode=mode):
                self.state = copy.deepcopy(baseline)
                source.write_text('print("Hello")\n'); source.chmod(0o644)
                extra = self.root / 'package/new.py'
                if extra.exists(): extra.unlink()
                if mode == 'edit': source.write_text('different')
                if mode == 'delete': source.unlink()
                if mode == 'mode': source.chmod(0o755)
                if mode == 'addition': extra.write_text('new owned source')
                self.revise()
                self.assertEqual(set(), m.accepted_ids(self.state))

    def test_missing_or_tampered_evidence_and_manifest_revalidate(self):
        self.start(); old = self.accept_fixture()
        baseline = copy.deepcopy(self.state)
        proof = Path(next(iter(old['reuse_manifest']['evidence_hashes'])))
        original = proof.read_bytes()
        for mode in ('missing', 'tampered', 'manifest', 'validation', 'approval'):
            with self.subTest(mode=mode):
                self.state = copy.deepcopy(baseline)
                proof.write_bytes(original)
                row = self.state['milestone_progress'][m.key(self.state)]
                if mode == 'missing': proof.unlink()
                if mode == 'tampered': proof.write_text('different')
                if mode == 'manifest': row['reuse_manifest']['changed_files'].clear()
                if mode == 'validation': row['accepted_validation']['verdict'] = 'FAIL'
                if mode == 'approval': self.state['user_events'].clear()
                self.revise()
                self.assertEqual(set(), m.accepted_ids(self.state))

    def test_legacy_acceptance_stays_accepted_but_cannot_be_reconstructed(self):
        self.start(); self.validate(); self.assign('M2')
        row = next(r for r in self.state['milestone_progress'].values() if r['id'] == 'M1')
        self.assertTrue(row['accepted'])
        self.assertNotIn('reuse_manifest', row)
        self.assertTrue(row['carry_forward_unavailable'])
        self.revise()
        self.assertEqual(set(), m.accepted_ids(self.state))

    def test_missing_stage_snapshot_disables_reuse_without_blocking_acceptance(self):
        self.start(); self.accept_fixture()
        del self.state['stages'][-1]['before_ref']
        m.accept(self.state, s.snapshot(self.root))
        self.assertNotIn('reuse_manifest', m.progress(self.state))
        self.assertTrue(m.progress(self.state)['accepted'])

    def test_missing_task_history_and_invalid_snapshot_disable_reuse(self):
        self.start(); self.accept_fixture()
        baseline = copy.deepcopy(self.state)
        prior = copy.deepcopy(self.state['current_task']); prior['id'] += '-prior'
        self.state.setdefault('task_archive', []).append(prior)
        manifest, reason = cf.capture(self.state, m.progress(self.state), s.snapshot(self.root))
        self.assertIsNone(manifest)
        self.assertIn('Stage history', reason)
        self.state = baseline
        path = self.state['stages'][-1]['before_ref']
        snapshot = s.read(path); snapshot['files']['fabricated.py'] = 'unknown'
        s.atomic_json(path, snapshot)
        manifest, reason = cf.capture(self.state, m.progress(self.state), s.snapshot(self.root))
        self.assertIsNone(manifest)
        self.assertIn('snapshot integrity', reason)

    def test_batch_human_review_and_ambiguous_paths_cannot_capture(self):
        self.start(); self.accept_fixture()
        baseline = copy.deepcopy(self.state)
        for mode in ('batch', 'human', 'paths'):
            with self.subTest(mode=mode):
                self.state = copy.deepcopy(baseline)
                row = m.progress(self.state)
                if mode == 'batch': row['accepted_batch'] = ['M1', 'M2']
                if mode == 'human': self.state['goal_contract']['body']['acceptance_criteria'][0]['human_review'] = True
                if mode == 'paths': self.state['current_task']['affected_paths'] = ['**/*.py']
                manifest, reason = cf.capture(self.state, row, s.snapshot(self.root))
                self.assertIsNone(manifest)
                self.assertTrue(reason)

    def test_redundant_assignment_rejected_and_explicit_rework_revokes(self):
        self.start(); self.accept_fixture(); self.revise()
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, 'already carried forward'):
            self.assign('M1')
        self.assertEqual(before, self.state)
        self.assign('M1', status='REWORK')
        self.assertEqual(set(), m.accepted_ids(self.state))
        self.assertTrue(m.progress(self.state)['carry_revoked'])

    def test_source_drift_after_approval_allows_repair_and_hides_acceptance(self):
        self.start(); self.accept_fixture(); self.revise()
        (self.root / 'greet.py').write_text('changed after approval')
        self.assertEqual(set(), m.accepted_ids(self.state))
        self.assign('M1')
        self.assertFalse(m.progress(self.state)['accepted'])

    def test_dependency_closure_on_reuse_and_later_source_drift(self):
        self.start()
        # Make the dependency part of an actually approved source contract.
        draft = copy.deepcopy(self.state['goal_contract']['body'])
        draft['milestones'][1]['depends_on'] = ['M1']
        goals.install_draft(self.state, draft, origin='test'); goals.present(self.state)
        goals.approve(self.state, self.state['displayed_goal']); self.assign()
        self.accept_fixture(); self.assign('M2'); self.accept_fixture('M2')
        baseline = copy.deepcopy(self.state)
        self.revise()
        self.assertEqual({'M1', 'M2'}, m.accepted_ids(self.state))
        (self.root / 'greet.py').write_text('Changed prerequisite')
        self.assertEqual(set(), m.accepted_ids(self.state))
        self.state = baseline
        self.revise()
        self.assertEqual(set(), m.accepted_ids(self.state))

    def test_chained_reuse_requires_fresh_validation(self):
        self.start(); self.accept_fixture(); self.revise()
        self.assertEqual({'M1'}, m.accepted_ids(self.state))
        self.revise()
        self.assertEqual(set(), m.accepted_ids(self.state))

    def test_final_completion_requires_new_revision_sol_and_full_integration(self):
        self.start(); old = self.accept_fixture(full=True); self.revise()
        decision = self.decision(status='COMPLETE')
        decision['acceptance_criteria'] = [{**c, 'status': 'verified', 'evidence': 'event:check'}
                                           for c in self.state['acceptance_criteria']]
        self.state['validation'] = copy.deepcopy(old['accepted_validation'])
        self.assertFalse(s.completion_ready(self.state, decision, s.snapshot(self.root)))
        self.state.pop('validation')
        self.assign('M2')
        self.validate({'C1': 'PASS', 'C2': 'PASS', 'C3': 'PASS'}, flow_status='NOT_VERIFIED')
        decision.update(task_id=self.state['current_task']['id'])
        self.assertFalse(s.completion_ready(self.state, decision, s.snapshot(self.root)))
        self.validate({'C1': 'PASS', 'C2': 'PASS', 'C3': 'PASS'}, flow_status='PASS')
        self.assertTrue(s.completion_ready(self.state, decision, s.snapshot(self.root)))

    def test_joint_initial_task_cannot_replay_carried_implementation(self):
        self.start(); self.accept_fixture()
        initial = {'objective': 'Build CLI', 'affected_paths': ['greet.py'], 'kind': 'implement',
                   'milestone_id': 'M1', 'requirements': ['Greet'], 'acceptance_criteria': ['C1'],
                   'validation_plan': ['Run tests']}
        self.revise(lambda body: body.update(initial_task=initial), approve=False)
        self.state['settings']['joint_planning'] = True
        goals.present(self.state)
        self.state['planning'] = {'final_token': self.state['displayed_goal']}
        goals.approve(self.state, self.state['displayed_goal'])
        self.assertEqual({'M1'}, m.accepted_ids(self.state))
        self.assertEqual('astra_review', self.state['next_stage'])
        self.assertNotIn('current_task', self.state)

    def test_context_summary_exposes_provenance_without_full_manifest(self):
        self.start(); self.accept_fixture(); self.revise()
        self.assign('M1', next_task={**self.decision()['next_task'], 'kind': 'validate'})
        summary = m.summary(self.state)
        self.assertNotIn('reuse_manifest', summary['current'])
        self.assertEqual('carried', summary['carry_forward']['outcomes'][0]['result'])


if __name__ == '__main__':
    unittest.main()
