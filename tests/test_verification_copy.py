"""Real CLI capture in a kernel-protected build tree; no simulated provider."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest

import autocode_tool_containment as containment
import autocode_toolchain as toolchain
import autocode_verification_copy as copies


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
