"""Public CLI recovery and exact-byte guarantees for issue #214."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CLI = Path(__file__).resolve().parents[1] / 'tools/autocode.py'


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {k: v for k, v in os.environ.items() if not k.startswith('AUTOCODE_OUTPUT_')}

    def cli(self, *args, code=0):
        result = subprocess.run([sys.executable, str(CLI), *map(str, args)], cwd=self.root,
                                env=self.env, capture_output=True)
        self.assertEqual(result.returncode, code, result.stderr.decode(errors='replace'))
        return result.stdout

    def read(self, *args):
        return json.loads(self.cli('output', *args))

    def test_sections_unchanged_changed_and_retrieval_after_restart(self):
        original = b''.join(f'line {i}\r\n'.encode() for i in range(1, 301))
        source = self.root / 'file with spaces.txt'
        source.write_bytes(original)
        first = self.read('read', source, '--start-line', 21, '--end-line', 30)
        self.assertEqual(first['content'].encode(), b''.join(original.splitlines(keepends=True)[20:30]))
        self.assertEqual(first['omitted_sections'], [{'start_line': 1, 'end_line': 20}, {'start_line': 31, 'end_line': 300}])
        sha = first['original']['sha256']
        self.assertEqual(sha, hashlib.sha256(original).hexdigest())
        # Each CLI is a new process: no in-memory cache is necessary on resume.
        same = self.read('read', source, '--known-sha256', sha)
        self.assertTrue(same['unchanged'])
        self.assertEqual(same['content'], '')
        source.write_bytes(b'changed\n')
        changed = self.read('read', source, '--known-sha256', sha)
        self.assertFalse(changed['unchanged'])
        self.assertEqual(changed['content'], 'changed\n')
        self.assertEqual(self.cli('output', 'retrieve', sha, '--raw'), original)
        self.assertEqual(self.cli('output', 'retrieve', sha, '--raw', '--start-line', 300), b'line 300\r\n')
        self.assertEqual(self.read('status')['retrieval_calls'], 2)

    def test_raw_mode_and_binary_exactness(self):
        original = b'a\x00\xff\r\n' * 300
        path = self.root / 'binary'
        path.write_bytes(original)
        first = self.read('--mode', 'raw', 'read', path)
        import base64
        self.assertEqual(base64.b64decode(first['content']), original)
        self.assertEqual(self.cli('output', 'retrieve', first['original']['sha256'], '--raw'), original)

    def test_corruption_falls_back_to_complete_read_and_retrieval_fails(self):
        original = b'abc\n' * 300
        path = self.root / 'source'
        path.write_bytes(original)
        first = self.read('read', path)
        Path(first['original']['path']).write_bytes(b'corrupted')
        next_read = self.read('read', path, '--known-sha256', first['original']['sha256'])
        self.assertEqual(next_read['fallback'], 'complete_original')
        self.assertEqual(next_read['content'].encode(), original)
        self.cli('output', 'retrieve', first['original']['sha256'], '--raw', code=2)

    def test_partial_write_is_ignored_and_symlink_cannot_redirect_store(self):
        root = self.root / '.autocode/output'
        (root / 'blobs').mkdir(parents=True)
        (root / 'blobs/.capture-interrupted').write_bytes(b'partial')
        (root / 'operations').mkdir()
        (root / 'operations/incomplete.json').write_text('{')
        (root / 'operations/invalid.json').write_text('[]')
        path = self.root / 'file'
        path.write_text('retained')
        first = self.read('read', path)
        self.assertEqual(self.cli('output', 'retrieve', first['original']['sha256'], '--raw'), b'retained')
        self.assertEqual(self.read('status')['unreadable_records'], 2)
        bad = self.root / '.autocode/bad'
        bad.mkdir()
        (bad / 'blobs').symlink_to(self.root, target_is_directory=True)
        fallback = self.read('--store', bad, 'read', path)
        self.assertEqual(fallback['fallback'], 'complete_original')

    def capture(self, filename, command, *options, code=0):
        return json.loads(self.cli('capture', '--output', '.autocode/' + filename, *options, '--', *command, code=code))

    def test_mixed_failure_diagnostics_preserved_and_passing_noise_retrievable(self):
        (self.root / 'test_fixture.py').write_text('''import unittest
class Fixture(unittest.TestCase):
    def test_failure_a(self): self.assertEqual(7, 9)
    def test_failure_b(self): self.assertEqual(7, 9)
for i in range(100):
    setattr(Fixture, f'test_pass_{i}', lambda self: self.assertTrue(True))
''')
        command = [sys.executable, '-m', 'unittest', '-v', 'test_fixture']
        filtered = self.capture('filtered.json', command, code=1)
        original = Path(filtered['full_output']).read_bytes()
        self.assertEqual(filtered['summary']['filter'], 'python-unittest-v1')
        self.assertEqual(filtered['summary']['content'].count('Traceback (most recent call last):'), 2)
        self.assertEqual(filtered['summary']['content'].count('AssertionError: 7 != 9'), 2)
        self.assertIn('FAILED (failures=2)', filtered['summary']['content'])
        self.assertEqual(self.cli('output', 'retrieve', filtered['full_output_sha256'], '--raw'), original)
        lines = original.decode().splitlines(keepends=True)
        omitted = {n for section in filtered['summary']['omitted_sections'] for n in range(section['start_line'], section['end_line'] + 1)}
        expected = ''.join(line for n, line in enumerate(lines, 1) if n not in omitted)
        actual = ''.join(line for line in filtered['summary']['content'].splitlines(keepends=True) if not line.startswith('[AutoCode omitted'))
        self.assertEqual(actual, expected)
        raw = self.capture('raw.json', command, '--mode', 'raw', code=1)
        self.assertEqual(raw['summary']['content'].encode(), Path(raw['full_output']).read_bytes())

    def test_identical_display_still_executes_fresh_command_and_unique_receipt(self):
        command = [sys.executable, '-c', "from pathlib import Path; p=Path('counter'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1'); print('same')"]
        first = self.capture('one.json', command)
        second = self.capture('two.json', command, '--known-output-sha256', first['full_output_sha256'])
        self.assertEqual((self.root / 'counter').read_text(), '2')
        self.assertEqual(second['summary']['filter'], 'verified-identical')
        self.assertNotEqual(first['full_output'], second['full_output'])
        self.cli('capture', '--output', '.autocode/two.json', '--', *command, code=2)
        self.assertEqual((self.root / 'counter').read_text(), '2')

    def test_binary_capture_and_unknown_command_are_raw(self):
        data = b'\xff\x00\r\n'
        receipt = self.capture('binary.json', [sys.executable, '-c', "import sys; sys.stdout.buffer.write(b'\\xff\\x00\\r\\n'); sys.exit(3)"], code=3)
        self.assertEqual(receipt['summary']['encoding'], 'base64')
        self.assertEqual(self.cli('output', 'retrieve', receipt['full_output_sha256'], '--raw'), data)

    def test_log_name_cannot_overwrite_the_original_with_a_receipt(self):
        self.cli('capture', '--output', '.autocode/same.log', '--', sys.executable, '-c', "print('proof')", code=2)
        self.assertFalse((self.root / '.autocode/same.log').exists())

    @unittest.skipUnless(os.name == 'posix', 'AutoCode capture supports POSIX hosts')
    def test_killed_capture_retains_partial_bytes_and_cannot_reuse_its_filename(self):
        script = "import os, signal, sys; sys.stdout.buffer.write(b'executed before crash\\n'); sys.stdout.flush(); os.kill(os.getppid(), signal.SIGKILL)"
        self.cli('capture', '--output', '.autocode/interrupted.json', '--', sys.executable, '-c', script, code=-9)
        self.assertFalse((self.root / '.autocode/interrupted.json').exists())
        raw = self.root / '.autocode/interrupted.log'
        self.assertEqual(raw.read_bytes(), b'executed before crash\n')
        self.cli('capture', '--output', '.autocode/interrupted.json', '--', sys.executable, '-c', "print('new')", code=2)
        self.assertEqual(raw.read_bytes(), b'executed before crash\n')


if __name__ == '__main__':
    unittest.main()
