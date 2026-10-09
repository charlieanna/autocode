"""Real selected-tool execution and denial controls; no provider/model simulation."""
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_tool_containment as containment
import autocode_toolchain as toolchain


@unittest.skipUnless(sys.platform == 'darwin', 'macOS toolchain containment')
class SelectedToolchainTests(unittest.TestCase):
    def test_selected_git_node_and_go_run_without_outside_read_or_source_write(self):
        if not all(shutil.which(name) for name in ('git', 'node', 'npm', 'go')):
            self.skipTest('requires installed Git, Node/npm and Go')
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            root = home / 'project'
            root.mkdir()
            outside = home / 'outside'
            outside.write_text('private fixture')
            source = root / 'sum.go'
            source.write_text('package sample\nfunc Sum(a,b int) int { return a+b }\n')
            (root / 'go.mod').write_text('module sample\n\ngo 1.24\n')
            (root / 'sum_test.go').write_text('package sample\nimport "testing"\nfunc TestSum(t *testing.T) { if Sum(2,3)!=5 { t.Fatal("bad") } }\n')
            (root / 'package.json').write_text('{"scripts":{"test":"node check.cjs"}}')
            (root / 'check.cjs').write_text('require("node:assert/strict").equal(2+3,5); console.log("node check passed");\n')
            subprocess.run(['/usr/bin/git', 'init', '--quiet', str(root)], check=True)
            selected = toolchain.discover(root, ['npm test', 'go test ./...'], os.environ)
            boundary = containment.prepare(root, read_roots=selected['read_roots'], environment=os.environ)
            def run(command):
                return subprocess.run([boundary['shell'], '-c', command], cwd=root,
                                      capture_output=True, text=True, timeout=60)
            for command in [*selected['probes'], 'npm test', 'go test ./...',
                            'python3 -c \"import json, subprocess; assert json.loads(\\\"[30]\\\")[0] == 30; '
                            'assert subprocess.run([\\\"go\\\", \\\"version\\\"], capture_output=True).returncode == 0\"']:
                result = run(command)
                self.assertEqual(0, result.returncode, (command, result.stdout, result.stderr))
            for command in ('cat ' + shlex.quote(str(outside)), ': > ' + shlex.quote(str(source)),
                            ': > ' + shlex.quote(str(outside))):
                result = run(command)
                self.assertNotEqual(0, result.returncode, command)
                self.assertIn('Operation not permitted', result.stderr)
            self.assertEqual('private fixture', outside.read_text())
            self.assertEqual('package sample\nfunc Sum(a,b int) int { return a+b }\n', source.read_text())

    def test_linked_worktree_reads_git_metadata_without_main_checkout_source(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            main, task = base / 'main', base / 'task'
            main.mkdir()
            subprocess.run(['/usr/bin/git', 'init', '--quiet', str(main)], check=True)
            subprocess.run(['/usr/bin/git', '-C', str(main), '-c', 'user.name=Fixture',
                            '-c', 'user.email=fixture@example.invalid', 'commit', '--allow-empty',
                            '-qm', 'fixture'], check=True)
            subprocess.run(['/usr/bin/git', '-C', str(main), 'worktree', 'add', '--quiet',
                            '--detach', str(task)], check=True)
            private = main / 'unrelated.txt'
            private.write_text('not part of the task')
            selected = toolchain.discover(task, [], os.environ)
            boundary = containment.prepare(task, read_roots=selected['read_roots'], environment=os.environ)
            for command in selected['probes']:
                result = subprocess.run([boundary['shell'], '-c', command], cwd=task,
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(0, result.returncode, result.stderr)
            denied = subprocess.run([boundary['shell'], '-c', 'cat ' + shlex.quote(str(private))],
                                    cwd=task, capture_output=True, text=True, timeout=20)
            self.assertNotEqual(0, denied.returncode)
            self.assertIn('Operation not permitted', denied.stderr)

    def test_selected_python_has_its_own_stdlib_and_extension_dependencies(self):
        if not shutil.which('python3'):
            self.skipTest('requires Python on PATH')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            selected = toolchain.discover(root, ['python3 -c "import json"'], os.environ)
            boundary = containment.prepare(root, read_roots=selected['read_roots'], environment=os.environ)
            result = subprocess.run([boundary['shell'], '-c',
                'python3 -c ' + shlex.quote('import json, ssl, sqlite3, ctypes, hashlib; '
                'assert sqlite3.connect(":memory:").execute("select 2+3").fetchone()[0] == 5; '
                'print("selected interpreter imports passed")')], cwd=root,
                capture_output=True, text=True, timeout=20)
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn('selected interpreter imports passed', result.stdout)

    def test_relative_path_selects_the_same_git_as_the_task_shell(self):
        git = shutil.which('git')
        if not git:
            self.skipTest('requires Git')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'bin').mkdir()
            (root / 'bin/git').symlink_to(Path(git).resolve())
            subprocess.run(['/usr/bin/git', 'init', '--quiet', str(root)], check=True)
            env = {**os.environ, 'PATH': 'bin:/usr/bin:/bin'}
            selected = toolchain.discover(root, [], env)
            self.assertEqual(str(root / 'bin/git'), shlex.split(selected['probes'][0])[0])
            roots = [*selected['read_roots']]
            developer = Path('/Library/Developer/CommandLineTools')
            if developer.is_dir():
                roots.append(str(developer))
            boundary = containment.prepare(root, read_roots=roots, environment=env)
            for command in [*selected['probes'], 'git status --short']:
                result = subprocess.run([boundary['shell'], '-c', command], cwd=root,
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(0, result.returncode, result.stderr)

    def test_system_shell_negative_controls_keep_native_containment(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            root = home / 'project'
            root.mkdir()
            source = root / 'test_failure.py'
            contents = ('import unittest\n'
                        'class NegativeControl(unittest.TestCase):\n'
                        '    def test_failure(self):\n'
                        '        self.fail("EXPECTED_NEGATIVE_CONTROL")\n')
            source.write_text(contents)
            outside = home / 'outside'
            outside.write_text('private fixture')
            subprocess.run(['/usr/bin/git', 'init', '--quiet', str(root)], check=True)
            env = {'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'}
            commands = [shlex.join([shell, '-c', '! python3 -m unittest test_failure -v'])
                        for shell in ('sh', 'bash')]
            selected = toolchain.discover(root, commands, env)
            roots = selected['read_roots']
            developer = Path('/Library/Developer/CommandLineTools')
            if developer.is_dir():
                roots = [*roots, str(developer)]
            boundary = containment.prepare(root, read_roots=roots, environment=env)
            for command in [*selected['probes'], *commands]:
                result = subprocess.run([boundary['shell'], '-c', command], cwd=root,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(0, result.returncode, (command, result.stdout, result.stderr))
                if command in commands:
                    self.assertIn('EXPECTED_NEGATIVE_CONTROL', result.stderr)
                    self.assertIn('FAILED (failures=1)', result.stderr)
            for shell in ('sh', 'bash'):
                for denied in ('cat ' + shlex.quote(str(outside)),
                               ': > ' + shlex.quote(str(source))):
                    command = shlex.join([shell, '-c', denied])
                    result = subprocess.run([boundary['shell'], '-c', command], cwd=root,
                                            capture_output=True, text=True, timeout=10)
                    self.assertNotEqual(0, result.returncode, command)
                    self.assertIn('Operation not permitted', result.stderr)
            self.assertEqual(contents, source.read_text())
            self.assertEqual('private fixture', outside.read_text())

    def test_project_shell_wrappers_are_rejected_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'bin').mkdir()
            marker = root / 'WRAPPER_EXECUTED'
            env = {'PATH': 'bin:/usr/bin:/bin'}
            for shell in ('sh', 'bash'):
                with self.subTest(shell=shell):
                    wrapper = root / 'bin' / shell
                    wrapper.write_text('#!/bin/sh\ntouch ' + shlex.quote(str(marker)) + '\n')
                    wrapper.chmod(0o755)
                    with self.assertRaisesRegex(RuntimeError, 'requires the native system shell'):
                        toolchain.discover(root, [shell + ' -c :'], env)
                    self.assertFalse(marker.exists())

    def test_readiness_does_not_execute_project_python_startup_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / 'sitecustomize.py').write_text('raise SystemExit("PROJECT_STARTUP_HOOK")\n')
            subprocess.run(['/usr/bin/git', 'init', '--quiet', str(root)], check=True)
            selected = toolchain.discover(root, [], os.environ)
            boundary = containment.prepare(root, read_roots=selected['read_roots'], environment=os.environ)
            for command in selected['probes']:
                result = subprocess.run([boundary['shell'], '-c', 'PYTHONPATH=. ' + command], cwd=root,
                                        capture_output=True, text=True, timeout=30)
                self.assertEqual(0, result.returncode, result.stderr)
            control = subprocess.run([boundary['shell'], '-c', "PYTHONPATH=. python3 -c 'print(1)'"], cwd=root,
                                     capture_output=True, text=True, timeout=30)
            self.assertNotEqual(0, control.returncode)
            self.assertIn('PROJECT_STARTUP_HOOK', control.stderr)
