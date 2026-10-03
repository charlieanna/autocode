"""Protected assertions survive Builder edits through the supported CLI."""
import json
import subprocess
import unittest
from pathlib import Path

from . import test_bugfix_workflow as fixture

ORIGINAL = '''import unittest
from layout import height
class Layout(unittest.TestCase):
    def test_required_height(self):
        self.assertEqual(height(), 40)
'''
VARIANTS = {
    'expected_value': ORIGINAL.replace('height(), 40', 'height(), 20'),
    'equally_wrong': ORIGINAL.replace('height(), 40', 'height(), height()'),
    'removed_assertion': ORIGINAL.replace('self.assertEqual(height(), 40)', 'height()'),
    'skipped': ORIGINAL.replace('    def test_required_height', '    @unittest.skip("later")\n    def test_required_height'),
    'renamed': ORIGINAL.replace('def test_required_height', 'def check_required_height'),
    'unconditional_pass': ORIGINAL.replace('self.assertEqual(height(), 40)', 'self.assertTrue(True)'),
}

class ProtectedOracleCLI(unittest.TestCase):
    new_run_engine_args = ()
    launch = fixture.BugfixWorkflow.launch
    saved = fixture.BugfixWorkflow.saved
    prepare = fixture.BugfixWorkflow.prepare
    draft = fixture.BugfixWorkflow.draft
    builder_writes = fixture.BugfixWorkflow.builder_writes

    def setUp(self):
        fixture.BugfixWorkflow.setUp(self)
        self.env['AUTOCODE_FIXTURE_TASK_KIND'] = 'build'
        (self.project / 'layout.py').write_text('def height():\n    return 20\n')
        (self.project / 'test_layout.py').write_text(ORIGINAL)
        subprocess.run(['git', '-C', str(self.project), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(self.project), '-c', 'user.name=Fixture',
                        '-c', 'user.email=f@example.test', 'commit', '-qm', 'protected failing layout oracle'], check=True)

    def execute(self, test, *, height=20):
        self.builder_writes({'greet.py': fixture.references.BUGFIX_REFERENCE['greet.py'],
                             'layout.py': f'def height():\n    return {height}\n', 'test_layout.py': test})
        run, approved = self.draft()
        self.launch(['--run-dir', str(run), '--approve-goal', approved['displayed_goal']], 0)
        result = self.launch(['--run-dir', str(run), '--no-chat'], 0 if height == 40 else 2)
        final = self.saved()[1]
        if height != 40:
            self.assertNotEqual('TASK_COMPLETE', final['status'])
            receipts = list((run / 'check-replay').glob('*/protected-tests/*/receipt.json'))
            self.assertTrue(receipts, result.stdout)
            self.assertTrue(any(json.loads(path.read_text())['original']['exit_code'] == 1 for path in receipts))
            self.assertEqual(ORIGINAL, (Path(final['settings']['protected_tests']['root']) / 'test_layout.py').read_text())
        return run, final, result

    def test_changed_expected_value_does_not_replace_the_original_gate(self):
        self.execute(VARIANTS['expected_value'])

    def test_two_equally_wrong_values_do_not_replace_the_original_gate(self):
        self.execute(VARIANTS['equally_wrong'])

    def test_removed_assertion_does_not_replace_the_original_gate(self):
        self.execute(VARIANTS['removed_assertion'])

    def test_skipped_case_does_not_replace_the_original_gate(self):
        self.execute(VARIANTS['skipped'])

    def test_renamed_case_does_not_replace_the_original_gate(self):
        self.execute(VARIANTS['renamed'])

    def test_unconditional_pass_does_not_replace_the_original_gate(self):
        self.execute(VARIANTS['unconditional_pass'])

    def test_saved_pause_can_explicitly_revise_gate_without_erasing_original(self):
        self.builder_writes({'greet.py': fixture.references.BUGFIX_REFERENCE['greet.py'],
                             'layout.py': 'def height():\n    return 20\n',
                             'test_layout.py': VARIANTS['expected_value']})
        run, approved = self.draft()
        self.launch(['--run-dir', str(run), '--approve-goal', approved['displayed_goal']], 0)
        for _ in range(3):
            args = ['--run-dir', str(run), '--pause-after-stage', '--no-chat']
            if self.saved()[1]['status'].startswith('PAUSED_'):
                args.append('--resume-paused')
            self.launch(args, 2)
            if self.saved()[1]['next_stage'] == 'sol':
                break
        before = self.saved()[1]
        self.assertEqual('sol', before['next_stage'])
        binding = before['settings']['protected_tests']
        import hashlib
        files = {name: {'sha256': hashlib.sha256((self.project/name).read_bytes()).hexdigest(),
                       'size': (self.project/name).stat().st_size,
                       'mode': (self.project/name).stat().st_mode & 0o777} for name in binding['files']}
        proposal = self.project / '.autocode/revision.json'
        proposal.write_text(json.dumps({'previous_hash': binding['binding_hash'], 'files': files,
            'command': binding['command'], 'reason': 'Operator explicitly approves height 20 for this fixture'}))
        self.launch(['--run-dir', str(run), '--resume-paused', '--no-chat', '--revise-protected-tests', str(proposal)], 0)
        final = self.saved()[1]
        self.assertEqual('TASK_COMPLETE', final['status'])
        self.assertNotEqual(binding['binding_hash'], final['settings']['protected_tests']['binding_hash'])
        self.assertEqual(before['goal_contract'], final['goal_contract'])
        self.assertEqual(ORIGINAL, (Path(binding['root'])/'test_layout.py').read_text())
        self.assertEqual('user_cli', [event for event in final['user_events'] if event['kind']=='protected_tests_revised'][0]['actor'])

    def test_real_fix_with_added_coverage_completes(self):
        extra = ORIGINAL + '\n    def test_positive_height(self):\n        self.assertGreater(height(), 0)\n'
        run, final, _ = self.execute(extra, height=40)
        self.assertEqual('TASK_COMPLETE', final['status'])
        self.assertEqual('PASS', final['validation']['check_replay']['protected_tests']['verdict'])
        self.assertEqual(ORIGINAL, (Path(final['settings']['protected_tests']['root']) / 'test_layout.py').read_text())


if __name__ == '__main__':
    unittest.main()
