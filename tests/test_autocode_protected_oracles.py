"""Original-oracle binding, execution, revisions and immutable history."""
import copy
import json
import os
from pathlib import Path
import shlex
import shutil
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
                if name == 'renamed':
                    # Zero-test discovery exits 5 on current 3.12 patch releases too.
                    self.assertIn(latest['candidate']['exit_code'], (0, 5))
                    self.assertIn('Ran 0 tests', latest['candidate']['tail'])
                else:
                    self.assertEqual(0, latest['candidate']['exit_code'])
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



LINKED = '''import os
import unittest
from layout import height
class Layout(unittest.TestCase):
    def test_required_height(self):
        self.assertEqual(height(), 40)
    def test_runs_through_the_original_link(self):
        self.assertEqual('../a/real_test.py', os.readlink('tests/b/link_test.py'))
        self.assertFalse(os.path.islink('tests/a'))
'''
WEAKENED = LINKED.replace('height(), 40', 'height(), 20')


class LinkedOracleTests(unittest.TestCase):
    """A tracked test that is a relative link inside the repository (#307).

    The original test asserts its own link and directory, so a replay that
    restored a copy or wrote through a replaced directory would report more
    than the two height failures.
    """

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name).resolve() / 'project'
        for name in ('tests/__init__.py', 'tests/a/__init__.py', 'tests/b/__init__.py'):
            (self.project / name).parent.mkdir(parents=True, exist_ok=True)
            (self.project / name).write_text('')
        (self.project / 'layout.py').write_text('def height():\n    return 20\n')
        (self.project / 'tests/a/real_test.py').write_text(LINKED)
        self.link = self.project / 'tests/b/link_test.py'
        self.link.symlink_to('../a/real_test.py')
        subprocess.run(['git', 'init', '-q', str(self.project)], check=True)
        subprocess.run(['git', '-C', str(self.project), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.project), '-c', 'user.name=Fixture',
                        '-c', 'user.email=f@example.test', 'commit', '-qm', 'linked original'], check=True)
        self.run = self.project / '.autocode/runs/protected'
        self.command = shlex.join([sys.executable, '-B', '-m', 'unittest', 'discover',
                                   '-s', 'tests', '-p', '*_test.py', '-t', '.', '-v'])
        self.bind()

    def bind(self):
        self.state = {'settings': {}}
        guard.reconcile(self.state, self.state['settings'], SimpleNamespace(run_dir=None), self.project, self.run,
                        is_test_path=verify.is_test_path, discover_command=lambda: self.command)

    @property
    def binding(self):
        return self.state['settings']['protected_tests']

    def execute(self):
        return guard.replay(self.state, self.project, self.run / 'replays', verify.scratch_run, timeout=60)

    def rejected(self):
        with self.assertRaisesRegex(ValueError, 'original gate did not pass'):
            self.execute()
        receipts = list((self.run / 'replays').glob('protected-tests/*/receipt.json'))
        return json.loads(max(receipts, key=lambda path: path.stat().st_mtime).read_text())

    def assert_original_height_failures_only(self, receipt, failures=2):
        self.assertEqual(1, receipt['original']['exit_code'])
        self.assertIn('20 != 40', receipt['original']['tail'])
        self.assertIn(f'FAILED (failures={failures})\n', receipt['original']['tail'])

    def test_existing_internal_link_is_bound_as_the_link_and_its_target(self):
        files = self.binding['files']
        regular = files['tests/a/real_test.py']
        self.assertEqual({'sha256', 'size', 'mode'}, set(regular))
        self.assertEqual({'symlink': '../a/real_test.py', 'target': regular}, files['tests/b/link_test.py'])
        bundle = Path(self.binding['root'])
        self.assertEqual('../a/real_test.py', os.readlink(bundle / 'tests/b/link_test.py'))
        self.assertFalse((bundle / 'tests/a/real_test.py').is_symlink())
        self.assertEqual(LINKED, (bundle / 'tests/b/link_test.py').read_text())
        self.assertEqual([], self.execute()['changed_tests'])

    def test_real_fix_passes_the_original_gate_with_the_link_restored_as_a_link(self):
        (self.project / 'layout.py').write_text('def height():\n    return 40\n')
        (self.project / 'tests/a/real_test.py').write_text(
            LINKED + '    def test_positive(self):\n        self.assertGreater(height(), 0)\n')
        result = self.execute()
        self.assertEqual('PASS', result['verdict'])
        self.assertEqual(['tests/a/real_test.py', 'tests/b/link_test.py'], result['changed_tests'])
        self.assertIn('Ran 6 tests', result['candidate']['tail'])
        self.assertEqual(0, result['original']['exit_code'], result['original']['tail'])
        self.assertIn('Ran 4 tests', result['original']['tail'])

    def test_weakened_link_target_fails_the_original_gate(self):
        (self.project / 'tests/a/real_test.py').write_text(WEAKENED)
        receipt = self.rejected()
        self.assertEqual(['tests/a/real_test.py', 'tests/b/link_test.py'], receipt['changed_tests'])
        self.assertEqual(0, receipt['candidate']['exit_code'])
        self.assert_original_height_failures_only(receipt)

    def test_retargeted_link_fails_the_original_gate(self):
        (self.project / 'tests/a/weak_test.py').write_text(WEAKENED)
        self.link.unlink(); self.link.symlink_to('../a/weak_test.py')
        receipt = self.rejected()
        self.assertEqual(['tests/b/link_test.py'], receipt['changed_tests'])
        self.assert_original_height_failures_only(receipt)

    def test_link_replaced_by_a_weakened_copy_is_restored_as_the_original_link(self):
        self.link.unlink(); self.link.write_text(WEAKENED)
        receipt = self.rejected()
        self.assertEqual(['tests/b/link_test.py'], receipt['changed_tests'])
        self.assert_original_height_failures_only(receipt)

    def test_replacement_link_into_source_cannot_write_through(self):
        source = self.project / 'layout.py'; original = source.read_bytes()
        self.link.unlink(); self.link.symlink_to(source)
        receipt = self.rejected()
        self.assertEqual(['tests/b/link_test.py'], receipt['changed_tests'])
        self.assert_original_height_failures_only(receipt)
        self.assertEqual(original, source.read_bytes())

    def test_replacement_directory_link_cannot_redirect_restoration(self):
        decoy = self.project / 'decoy'
        shutil.copytree(self.project / 'tests/a', decoy)
        (decoy / 'real_test.py').write_text(WEAKENED)
        shutil.rmtree(self.project / 'tests/a'); (self.project / 'tests/a').symlink_to('../decoy')
        receipt = self.rejected()
        self.assertEqual(['tests/a/__init__.py', 'tests/a/real_test.py', 'tests/b/link_test.py'],
                         receipt['changed_tests'])
        self.assert_original_height_failures_only(receipt)
        self.assertEqual(WEAKENED, (decoy / 'real_test.py').read_text())

    def test_link_target_outside_the_test_inventory_is_restored_from_the_bundle(self):
        shared = self.project / 'shared/height_case.py'
        shared.parent.mkdir()
        shared.write_text('import unittest\nfrom layout import height\nclass Shared(unittest.TestCase):\n'
                          '    def test_positive(self):\n        self.assertGreater(height(), 0)\n')
        (self.project / 'tests/b/case_test.py').symlink_to('../../shared/height_case.py')
        (self.project / 'layout.py').write_text('def height():\n    return 40\n')
        self.bind()
        self.assertNotIn('shared/height_case.py', self.binding['files'])
        self.assertEqual(shared.read_text(), (Path(self.binding['root']) / 'shared/height_case.py').read_text())
        shared.write_text(shared.read_text().replace('assertGreater(height(), 0)', 'assertEqual(height(), 41)'))
        receipt = self.rejected()
        self.assertEqual(['tests/b/case_test.py'], receipt['changed_tests'])
        self.assertEqual(1, receipt['candidate']['exit_code'])
        self.assertEqual(0, receipt['original']['exit_code'], receipt['original']['tail'])
        self.assertIn('Ran 5 tests', receipt['original']['tail'])

    def test_retained_link_and_target_cannot_be_changed(self):
        bundle = Path(self.binding['root'])
        link, target = bundle / 'tests/b/link_test.py', bundle / 'tests/a/real_test.py'
        for change in ('retarget', 'target'):
            with self.subTest(change=change):
                if change == 'retarget':
                    link.unlink(); link.symlink_to('../a/__init__.py')
                else:
                    target.write_text(WEAKENED)
                with self.assertRaisesRegex(ValueError, 'bundle changed'):
                    self.execute()
                self.assertFalse(guard.ready(self.state, 'current'))
                link.unlink(); link.symlink_to('../a/real_test.py'); target.write_text(LINKED)

    def test_unsafe_links_are_refused_before_a_run_binds(self):
        (self.project.parent / 'outside_test.py').write_text(LINKED)
        cases = {
            'outside the root': {'tests/b/out_test.py': '../../../outside_test.py'},
            'absolute': {'tests/b/abs_test.py': str(self.project / 'tests/a/real_test.py')},
            'dangling': {'tests/b/gone_test.py': '../a/missing_test.py'},
            'self cycle': {'tests/b/loop_test.py': 'loop_test.py'},
            'cycle': {'tests/b/x_test.py': 'y_test.py', 'tests/b/y_test.py': 'x_test.py'},
            'chain leaving the root': {'tests/b/hop_test.py': '../../hop.py', 'hop.py': '../outside_test.py'},
            'directory': {'tests/c': 'a'},
            'through a directory link': {'lib': 'tests/a', 'tests/b/via_test.py': '../../lib/real_test.py'},
            'non-canonical': {'tests/b/dot_test.py': '../b/../a/real_test.py'},
        }
        for case, links in cases.items():
            with self.subTest(case=case):
                for name, target in links.items():
                    (self.project / name).symlink_to(target)
                try:
                    with self.assertRaisesRegex(ValueError, 'Protected test'):
                        self.bind()
                    self.assertNotIn('protected_tests', self.state['settings'])
                finally:
                    for name in links:
                        (self.project / name).unlink()


if __name__ == '__main__':
    unittest.main()
