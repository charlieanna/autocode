"""Real CLI capture in a kernel-protected build tree; no simulated provider."""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_source_snapshot as source_snapshot
import autocode_tool_containment as containment
import autocode_toolchain as toolchain
import autocode_verification_copy as copies


class VerificationSourceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name).resolve()
        self.root = self.home / 'project'
        self.root.mkdir()
        self.env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': str(self.home),
                    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}
        self.init_git()
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@localhost',
                 '-c', 'commit.gpgsign=false', 'commit', '--allow-empty', '-qm', 'original')
        self.run_dir = self.root / '.autocode/runs/fixture'
        self.run_dir.mkdir(parents=True)

    def git(self, *args, root=None):
        return subprocess.run(['/usr/bin/git', *args], cwd=root or self.root,
            env=self.env, check=True, capture_output=True, timeout=30).stdout

    def init_git(self, root=None):
        self.git('init', '-q', root=root)
        self.git('config', 'maintenance.auto', 'false', root=root)
        self.git('config', 'gc.auto', '0', root=root)

    def index(self, root):
        entries = {}
        for record in self.git('ls-files', '--stage', '-z', root=root).split(b'\0'):
            if not record:
                continue
            metadata, path = record.split(b'\t', 1)
            mode, blob, stage = metadata.decode('ascii').split(' ')
            self.assertEqual('0', stage)
            name = os.fsdecode(path)
            self.assertNotIn(name, entries)
            entries[name] = (mode, blob)
        return entries

    def allocate(self):
        result = copies.allocate(self.root, self.run_dir)
        tree, _ = copies.execution(result['manifest'], result['sha256'], self.root)
        return tree

    def test_copy_preserves_binary_modes_links_and_git_attribute_normalization(self):
        binary_name = 'quoted" slash\\ tab\t newline\n unicode-λ.bin'
        binary = b'\0\xffblob\ndata 4\ndone\ncommit refs/heads/other\n'
        (self.root / binary_name).write_bytes(binary)
        (self.root / 'text.txt').write_bytes(b'first\r\nsecond\r\n')
        (self.root / '.gitattributes').write_text('*.bin -text\ntext.txt text eol=lf\n')
        executable = self.root / 'script.sh'
        executable.write_text('#!/bin/sh\necho copied\n')
        executable.chmod(0o755)
        (self.root / 'relative-link').symlink_to(binary_name)
        (self.root / 'absolute-link').symlink_to(executable)
        (self.root / 'missing-link').symlink_to('missing-target')
        before = source_snapshot.snapshot(self.root)
        tree = self.allocate()
        self.assertEqual(binary, (tree / binary_name).read_bytes())
        self.assertEqual(b'first\r\nsecond\r\n', (tree / 'text.txt').read_bytes())
        self.assertEqual(binary_name, os.readlink(tree / 'relative-link'))
        self.assertEqual(str(tree / 'script.sh'), os.readlink(tree / 'absolute-link'))
        self.assertEqual('missing-target', os.readlink(tree / 'missing-link'))
        self.assertEqual(str(executable), os.readlink(self.root / 'absolute-link'))
        entries = self.index(tree)
        self.assertEqual('100755', entries['script.sh'][0])
        self.assertEqual('120000', entries['relative-link'][0])
        self.assertEqual(binary, self.git('cat-file', 'blob', entries[binary_name][1], root=tree))
        self.assertEqual(b'first\nsecond\n', self.git('cat-file', 'blob', entries['text.txt'][1], root=tree))
        # Compare with ordinary Git staging of identical physical copy inputs.
        ordinary = self.home / 'ordinary'
        ordinary.mkdir()
        for name in before['files']:
            original, target = tree / name, ordinary / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if original.is_symlink():
                target.symlink_to(os.readlink(original))
            else:
                shutil.copy2(original, target)
        self.init_git(root=ordinary)
        self.git('add', '-f', '--all', root=ordinary)
        self.assertEqual(self.index(ordinary), entries)
        self.assertEqual(b'1\n', self.git('rev-list', '--count', 'HEAD', root=tree))
        self.assertEqual(self.git('symbolic-ref', 'HEAD', root=tree),
                         self.git('for-each-ref', '--format=%(refname)', root=tree))
        self.assertEqual(before, source_snapshot.snapshot(self.root))

    def test_deleted_input_stays_absent_and_empty_copy_has_one_baseline_commit(self):
        removed = self.root / 'removed.txt'
        removed.write_text('removed current source\n')
        self.git('add', 'removed.txt')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@localhost',
                 '-c', 'commit.gpgsign=false', 'commit', '-qm', 'tracked input')
        removed.unlink()
        before = source_snapshot.snapshot(self.root)
        tree = self.allocate()
        self.assertFalse((tree / 'removed.txt').exists())
        self.assertEqual({}, self.index(tree))
        self.assertEqual(b'1\n', self.git('rev-list', '--count', 'HEAD', root=tree))
        self.assertEqual(before, source_snapshot.snapshot(self.root))

    def test_large_regular_input_does_not_require_whole_file_read_bytes(self):
        payload = b'\0binary\xff\n' * (400 * 1024)
        large = self.root / 'large.bin'
        large.write_bytes(payload)
        original_read = Path.read_bytes
        def bounded_input(path):
            if path.name == 'large.bin':
                raise AssertionError('Copied regular source must be streamed')
            return original_read(path)
        with mock.patch.object(Path, 'read_bytes', bounded_input):
            tree = self.allocate()
        entries = self.index(tree)
        self.assertEqual(payload, self.git('cat-file', 'blob', entries['large.bin'][1], root=tree))
        self.assertEqual(payload, large.read_bytes())

    def test_changed_copy_is_rejected_before_packing_and_only_own_control_is_removed(self):
        original = self.root / 'source.txt'
        original.write_text('authenticated original\n')
        existing = self.run_dir / ('tool-containment-' + 'f' * 32)
        existing.mkdir()
        sentinel = existing / 'existing-evidence.bin'
        sentinel.write_bytes(b'existing other control\0evidence')
        before = source_snapshot.snapshot(self.root)
        actual_temporary_file = tempfile.TemporaryFile
        def changed_copy(*args, **kwargs):
            copied = Path(kwargs['dir']) / 'verification/source.txt'
            copied.write_text('changed copied source\n')
            return actual_temporary_file(*args, **kwargs)
        with mock.patch.object(copies.tempfile, 'TemporaryFile', side_effect=changed_copy):
            with self.assertRaisesRegex(ValueError, 'Source changed while packing'):
                self.allocate()
        self.assertEqual('authenticated original\n', original.read_text())
        self.assertEqual(before, source_snapshot.snapshot(self.root))
        self.assertEqual([existing], list(self.run_dir.iterdir()))
        self.assertEqual(b'existing other control\0evidence', sentinel.read_bytes())


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('go'), 'requires macOS and Go')
class VerificationCopyTests(unittest.TestCase):
    def test_capture_builds_current_source_and_rejects_source_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / 'project'
            root.mkdir()
            (root / 'go.mod').write_text('module example\n\ngo 1.24\n')
            source = root / 'main.go'
            source.write_text('package main\nimport "fmt"\nfunc main(){fmt.Println("bound source")}\n')
            (root / 'main_test.go').write_text('package main\nimport "testing"\nfunc TestValue(t *testing.T){if 2+3!=5{t.Fatal("bad")}}\n')
            (root / 'alias.go.txt').symlink_to('main.go')
            for args in [('init', '-q'), ('add', '.'), ('-c', 'user.name=Test', '-c',
                         'user.email=test@localhost', 'commit', '-qm', 'source')]:
                subprocess.run(['/usr/bin/git', *args], cwd=root, check=True, capture_output=True)
            selected = toolchain.discover(root, ['go test .', shlex.join([sys.executable, '-V'])], os.environ)
            tools = Path(containment.__file__).parent
            boundary = containment.prepare(root, environment=os.environ,
                read_roots=[*selected['read_roots'], tools], verification_copy=True)
            scratch = Path(boundary['scratch'])
            tree = scratch / 'verification'
            entry = 'import sys; sys.path.insert(0, ' + repr(str(tools)) + '); import autocode_capture_command as c; sys.exit(c.cli())'
            def capture(name, command):
                argv = [sys.executable, '-c', entry, '--output', str(scratch / (name + '.json')),
                        '--', '/bin/sh', '-c', command]
                return subprocess.run([boundary['shell'], '-c', shlex.join(argv)], cwd=root,
                                      text=True, capture_output=True, timeout=90)
            command = 'go build -o app . && ./app && go test -v .'
            result = capture('build', command)
            self.assertEqual(0, result.returncode, result.stderr + result.stdout)
            receipt = json.loads(result.stdout)
            self.assertEqual(['/bin/sh', '-c', command], receipt['command'])
            self.assertIn('bound source', Path(receipt['full_output']).read_text())
            self.assertIn('--- PASS: TestValue', Path(receipt['full_output']).read_text())
            self.assertEqual(boundary['verification_copy_sha256'], receipt['verification_copy']['manifest_sha256'])
            self.assertTrue((tree / 'app').is_file())
            self.assertFalse((root / 'app').exists())
            for command in [': > ' + shlex.quote(str(tree / 'main.go')),
                            'rm ' + shlex.quote(str(tree / 'main_test.go')),
                            'rm ' + shlex.quote(str(tree / 'alias.go.txt'))]:
                denied = subprocess.run([boundary['shell'], '-c', command], cwd=root,
                                        text=True, capture_output=True, timeout=10)
                self.assertNotEqual(0, denied.returncode)
                self.assertIn('Operation not permitted', denied.stderr)
            original = source.read_text()
            source.write_text(original + '// changed after copy\n')
            rejected = capture('drift', 'echo SHOULD_NOT_RUN')
            self.assertEqual(2, rejected.returncode, rejected.stderr)
            self.assertIn('Verification source changed', rejected.stderr)
            self.assertFalse((scratch / 'drift.log').exists())
            source.write_text(original)
            (tree / 'main.go').write_text('supervisor tampered copy')
            with self.assertRaisesRegex(ValueError, 'Verification input changed'):
                copies.execution(boundary['verification_copy'], boundary['verification_copy_sha256'], root)
