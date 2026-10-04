"""Real CLI captures retain a run's exact bytes after entering a scratch copy."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from autocode_output_policy import account, environment, view

CLI = Path(__file__).resolve().parents[1] / 'tools/autocode.py'


class WorkspaceOutputTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        parent = Path(temporary.name).resolve()
        self.workspace = parent / 'project'
        self.scratch = self.workspace / '.autocode/investigation/copy'
        self.scratch.mkdir(parents=True)
        self.outside = parent / 'outside'
        self.outside.mkdir()
        self.settings = {'output_transport': {'mode': 'conservative'}}
        self.clean_env = {k: v for k, v in os.environ.items()
                          if not k.startswith('AUTOCODE_OUTPUT_') and k != 'AUTOCODE_CAPTURE_CONTEXT'}
        self.env = {**self.clean_env, **environment(self.settings, self.workspace, 'attempt-one')}

    def cli(self, *args, cwd=None, env=None, code=0):
        result = subprocess.run([sys.executable, str(CLI), *map(str, args)],
                                cwd=cwd or self.scratch, env=self.env if env is None else env,
                                capture_output=True)
        self.assertEqual(result.returncode, code, result.stderr.decode(errors='replace'))
        return result.stdout

    def capture(self, name, *, env=None):
        return json.loads(self.cli('capture', '--output', '.autocode/' + name, '--', sys.executable,
                                  '-c', "import sys; sys.stdout.buffer.write(b'exact\\x00\\xff\\r\\n'); sys.exit(7)",
                                  env=env, code=7))

    def test_capture_retrieve_and_account_use_one_store_across_directories(self):
        original = b'exact\x00\xff\r\n'
        first = self.capture('one.json')
        self.assertNotIn('fallback', first['summary'])
        expected_sha = hashlib.sha256(original).hexdigest()
        self.assertEqual(first['exact_output']['sha256'], expected_sha)
        blob = self.workspace / '.autocode/output/blobs' / (expected_sha + '.bin')
        self.assertEqual(Path(first['exact_output']['path']), blob)
        self.assertEqual(blob.read_bytes(), original)
        self.assertEqual(Path(first['full_output']).read_bytes(), original)
        for cwd in (self.scratch, self.workspace):
            self.assertEqual(self.cli('output', 'retrieve', expected_sha, '--raw', cwd=cwd), original)
        other_env = {**self.env, 'AUTOCODE_OUTPUT_ATTEMPT': 'attempt-two'}
        second = self.capture('two.json', env=other_env)
        self.assertEqual(second['exact_output'], first['exact_output'])
        self.assertNotEqual(second['full_output'], first['full_output'])
        self.assertFalse((self.scratch / '.autocode/output').exists())
        self.assertEqual(json.loads(self.cli('output', 'status'))['operations'], 4)
        records = [{'events': name} for name in ('attempt-one', 'attempt-two', 'no-operations')]
        state = {'workspace': str(self.workspace), 'settings': self.settings, 'stages': records}
        for record in records:
            account(state, record)
        self.assertEqual([r['metrics']['output_transport']['operations'] for r in records], [3, 1, 0])
        counts = view(state)
        self.assertEqual(counts['operations'], 4)
        self.assertEqual(counts['retrieval_calls'], 2)
        self.assertEqual(counts['raw_bytes'], len(original) * 4)
        self.assertGreater(counts['displayed_bytes'], len(original) * 4)
        self.assertIsNone(counts['cost_savings_usd'])
        receipt = (self.scratch / '.autocode/one.json').read_bytes()
        self.cli('capture', '--output', '.autocode/one.json', '--', sys.executable,
                 '-c', "raise SystemExit('must not execute')", code=2)
        self.assertEqual((self.scratch / '.autocode/one.json').read_bytes(), receipt)
        blob.write_bytes(b'corrupted')
        self.cli('output', 'retrieve', expected_sha, '--raw', code=2)

    def test_binding_does_not_expand_source_reads_or_allow_external_cwd(self):
        parent_file = self.workspace / 'parent.txt'
        parent_file.write_text('parent source')
        self.cli('output', 'read', parent_file, code=2)
        self.cli('output', 'status', cwd=self.outside, code=2)
        self.cli('output', '--store', self.outside / '.autocode/output', 'status', code=2)
        # Even an explicit store under the external CWD cannot adopt an unrelated run binding.
        self.cli('output', '--store', self.outside / '.autocode/output', 'status', cwd=self.outside, code=2)
        relative_env = {**self.env, 'AUTOCODE_OUTPUT_WORKSPACE': '../..'}
        self.cli('output', 'status', env=relative_env, code=2)

    def test_external_symlink_cannot_redirect_workspace_or_store(self):
        link = self.workspace / 'external-copy'
        link.symlink_to(self.outside, target_is_directory=True)
        self.cli('output', 'status', cwd=link, code=2)
        (self.workspace / '.autocode/output').symlink_to(self.outside, target_is_directory=True)
        self.cli('output', 'status', code=2)
        receipt = self.capture('fallback.json')
        self.assertEqual(receipt['summary']['fallback'], 'complete_original')
        self.assertEqual(receipt['summary']['compression_error'], 'ValueError')
        self.assertNotIn('exact_output', receipt)
        self.assertEqual(list(self.outside.iterdir()), [])

    def test_standalone_commands_keep_the_current_directory_boundary(self):
        self.cli('output', '--store', self.workspace / '.autocode/output', 'status',
                 env=self.clean_env, code=2)
        receipt = self.capture('standalone.json', env=self.clean_env)
        self.assertNotIn('fallback', receipt['summary'])
        self.assertTrue(Path(receipt['exact_output']['path']).is_relative_to(self.scratch / '.autocode/output'))
        bound_without_store = {k: v for k, v in self.env.items() if k != 'AUTOCODE_OUTPUT_STORE'}
        bound = self.capture('bound-default.json', env=bound_without_store)
        self.assertTrue(Path(bound['exact_output']['path']).is_relative_to(self.workspace / '.autocode/output'))
        self.assertFalse(Path(bound['exact_output']['path']).is_relative_to(self.scratch))
