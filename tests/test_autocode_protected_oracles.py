"""Original-oracle binding, execution, revisions and immutable history."""
import copy
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest

import autocode_protected_oracles as guard
import autocode_verify as verify
from .test_protected_oracles_cli import ORIGINAL, VARIANTS

class OriginalOracleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name).resolve() / 'project'
        self.project.mkdir()
        (self.project / 'layout.py').write_text('def height():\n    return 20\n')
        (self.project / 'test_layout.py').write_text(ORIGINAL)
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        subprocess.run(['git', '-C', str(self.project), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.project), '-c', 'user.name=Fixture',
                        '-c', 'user.email=f@example.test', 'commit', '-qm', 'original'], check=True)
        self.run = self.project / '.autocode/runs/protected'
        self.command = shlex.join([sys.executable, '-B', '-m', 'unittest', 'discover', '-v'])
        self.state = {'settings': {}}
        self.new_args = SimpleNamespace(run_dir=None)
        self.configure(self.new_args)

    def configure(self, args):
        return guard.reconcile(self.state, self.state['settings'], args, self.project, self.run,
            is_test_path=verify.is_test_path, discover_command=lambda: self.command)

    @property
    def binding(self):
        return self.state['settings']['protected_tests']

    def execute(self):
        return guard.replay(self.state, self.project, self.run / 'replays', verify.scratch_run, timeout=10)

    def test_all_weakening_variants_execute_original_assertion(self):
        original_hash = self.binding['binding_hash']
        for name, variant in VARIANTS.items():
            with self.subTest(name=name):
                (self.project / 'test_layout.py').write_text(variant)
                with self.assertRaisesRegex(ValueError, 'original gate did not pass'):
                    self.execute()
                receipts = list((self.run / 'replays').glob('protected-tests/*/receipt.json'))
                latest = json.loads(max(receipts, key=lambda path:path.stat().st_mtime).read_text())
                self.assertEqual(5 if name == 'renamed' and sys.version_info >= (3, 14) else 0, latest['candidate']['exit_code'])
                self.assertEqual(1, latest['original']['exit_code'])
                self.assertIn('20 != 40', latest['original']['tail'])
                self.assertEqual(original_hash, latest['binding_hash'])
        self.assertEqual(len(VARIANTS), len(list((self.run / 'replays').glob('protected-tests/*/receipt.json'))))

    def test_real_fix_and_meaningful_added_coverage_pass_both_suites(self):
        (self.project / 'test_layout.py').write_text(ORIGINAL + '\n    def test_positive(self):\n        self.assertGreater(height(), 0)\n')
        (self.project / 'layout.py').write_text('def height():\n    return 40\n')
        result = self.execute()
        self.assertEqual('PASS', result['verdict'])
        self.assertEqual(0, result['candidate']['exit_code'])
        self.assertEqual(0, result['original']['exit_code'])
        self.assertIn('Ran 2 tests', result['candidate']['tail'])
        self.assertIn('Ran 1 test', result['original']['tail'])

    def test_completion_requires_the_unchanged_executed_receipt(self):
        (self.project / 'test_layout.py').write_text(ORIGINAL + '\n    def test_positive(self):\n        self.assertGreater(height(), 0)\n')
        (self.project / 'layout.py').write_text('def height():\n    return 40\n')
        result = self.execute()
        self.state['validation'] = {'check_replay': {'protected_tests': result}}
        self.assertTrue(guard.ready(self.state, result['source_revision']))
        Path(result['receipt']).write_text('{}\n')
        self.assertFalse(guard.ready(self.state, result['source_revision']))

    def test_restart_retains_binding_and_failed_receipts(self):
        (self.project / 'test_layout.py').write_text(VARIANTS['expected_value'])
        with self.assertRaises(ValueError):
            self.execute()
        failed = {path: path.read_bytes() for path in (self.run / 'replays').glob('protected-tests/*/receipt.json')}
        self.state = json.loads(json.dumps(self.state))
        self.configure(SimpleNamespace(run_dir=self.run))
        with self.assertRaises(ValueError):
            self.execute()
        self.assertTrue(all(path.read_bytes() == data for path, data in failed.items()))
        self.assertEqual(2, len(list((self.run / 'replays').glob('protected-tests/*/receipt.json'))))

    def proposal(self):
        path = self.project / '.autocode/revision.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps({'previous_hash': self.binding['binding_hash'], 'reason': 'User changes the layout height requirement',
            'files': guard.inventory(self.project, verify.is_test_path), 'command': self.command}))
        return path

    def test_explicit_user_revision_preserves_old_binding_and_fresh_proof(self):
        old = copy.deepcopy(self.binding)
        (self.project / 'test_layout.py').write_text(VARIANTS['expected_value'])
        self.state.update(status='PAUSED_REQUESTED', next_stage='sol')
        self.configure(SimpleNamespace(run_dir=self.run, resume_paused=True, revise_protected_tests=self.proposal()))
        self.assertNotEqual(old['binding_hash'], self.binding['binding_hash'])
        self.assertEqual(ORIGINAL, (Path(old['root']) / 'test_layout.py').read_text())
        self.assertEqual('user_cli', self.state['user_events'][0]['actor'])
        self.assertEqual(old, self.state['user_events'][0]['previous'])
        self.assertFalse(guard.ready(self.state, 'old-validation'))
        self.assertEqual('PASS', self.execute()['verdict'])

    def test_revision_needs_reconciled_pause_and_exact_old_and_current_inputs(self):
        for field, value in [('status', 'RUNNING'), ('next_stage', 'astra_review'), ('active_stage', {'stage':'sol'}),
                             ('pending_report_repair', {'stage':'sol'}), ('uncertain_artifacts', ['old']),
                             ('active_runner_check', {'stage':'regression_proof'})]:
            state = copy.deepcopy(self.state)
            self.state.update(status='PAUSED_REQUESTED', next_stage='sol')
            self.state[field] = value
            with self.assertRaises(ValueError):
                self.configure(SimpleNamespace(run_dir=self.run, resume_paused=True, revise_protected_tests=self.proposal()))
            self.state = state
        self.state.update(status='PAUSED_REQUESTED', next_stage='sol')
        for field, value in [('previous_hash', 'stale'), ('files', {}), ('reason', ''), ('command', '')]:
            proposal = self.proposal()
            data = json.loads(proposal.read_text()); data[field] = value; proposal.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                self.configure(SimpleNamespace(run_dir=self.run, resume_paused=True, revise_protected_tests=proposal))

    def test_retained_test_tampering_is_not_permission_to_rebind(self):
        (Path(self.binding['root']) / 'test_layout.py').write_text(VARIANTS['unconditional_pass'])
        with self.assertRaisesRegex(ValueError, 'bundle changed'):
            self.execute()
        self.assertFalse(guard.ready(self.state, 'current'))

    def test_replacement_symlink_cannot_write_through_to_source(self):
        target = self.project / 'layout.py'; original = target.read_bytes()
        path = self.project / 'test_layout.py'; path.unlink(); path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(original, target.read_bytes())
        receipts = list((self.run / 'replays').glob('protected-tests/*/receipt.json'))
        self.assertEqual(1, len(receipts))
        self.assertEqual(1, json.loads(receipts[0].read_text())['original']['exit_code'])

    def test_dirty_and_untracked_tests_are_bound_at_start(self):
        (self.project / 'test_layout.py').write_text(VARIANTS['expected_value'])
        (self.project / 'test_extra.py').write_text('import unittest\nclass Extra(unittest.TestCase):\n    def test_no_zero(self):\n        self.assertNotEqual(20, 0)\n')
        self.state = {'settings': {}}
        self.configure(self.new_args)
        self.assertEqual({'test_layout.py', 'test_extra.py'}, set(self.binding['files']))
        self.assertEqual(VARIANTS['expected_value'], (Path(self.binding['root']) / 'test_layout.py').read_text())

    def test_old_saved_runs_are_not_bound_after_a_builder_edit(self):
        self.state = {'settings': {}}
        self.configure(SimpleNamespace(run_dir=self.run))
        self.assertNotIn('protected_tests', self.state['settings'])


if __name__ == '__main__':
    unittest.main()
