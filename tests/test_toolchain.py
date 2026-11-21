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

# Either kernel backend (#709): Seatbelt reports a denied operation, bubblewrap a read-only
# mount or a path that is not mounted at all.
NATIVE_PROBLEM = containment.sandbox_problem()
DENIED = (
    "Operation not permitted"
    if containment.backend() == containment.SEATBELT
    else "Read-only file system|No such file or directory|Permission denied"
)


@unittest.skipUnless(NATIVE_PROBLEM is None, f"requires a real kernel sandbox: {NATIVE_PROBLEM}")
class PytestToolchainTests(unittest.TestCase):
    # The required Ubuntu suite installs .[test] and the real kernel boundary.
    # The minimal native-only job selects SelectedToolchainTests without pytest.
    def test_pytest_console_runs_real_assertions_and_keeps_file_denials(self):
        console = Path(sys.executable).parent / "pytest"
        self.assertTrue(console.is_file(), "install .[test] on the test interpreter")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            root = home / "project"
            root.mkdir()
            outside = home / "outside"
            outside.write_text("private fixture")
            source = root / "test_good.py"
            contents = (
                "from pathlib import Path\nimport pytest\n"
                "def test_assertion():\n    assert 2 + 3 == 5\n"
                "def test_outside_read_is_denied():\n"
                f"    with pytest.raises(OSError): Path({str(outside)!r}).read_text()\n"
                "def test_source_write_is_denied():\n"
                "    with pytest.raises(OSError): Path(__file__).write_text('changed')\n"
            )
            source.write_text(contents)
            (root / "test_bad.py").write_text("def test_failure():\n    assert 1 == 2\n")
            subprocess.run(["/usr/bin/git", "init", "--quiet", str(root)], check=True)
            selected = toolchain.discover(root, [shlex.join([str(console), "-q", "test_good.py"])], os.environ)
            boundary = containment.prepare(
                root, read_roots=selected["read_roots"], links=selected["links"], environment=os.environ
            )
            for command in selected["probes"]:
                result = subprocess.run(
                    [boundary["shell"], "-c", command], cwd=root, capture_output=True, text=True, timeout=30
                )
                self.assertEqual(0, result.returncode, (command, result.stderr))
            for name, code, message in (("good", 0, "3 passed"), ("bad", 1, "1 failed")):
                command = "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PYTHONDONTWRITEBYTECODE=1 " + shlex.join(
                    [str(console), "-q", "-p", "no:cacheprovider", f"test_{name}.py"]
                )
                result = subprocess.run(
                    [boundary["shell"], "-c", command], cwd=root, capture_output=True, text=True, timeout=30
                )
                self.assertEqual(code, result.returncode, (result.stdout, result.stderr))
                self.assertIn(message, result.stdout)
            self.assertEqual(contents, source.read_text())
            self.assertEqual("private fixture", outside.read_text())


@unittest.skipUnless(NATIVE_PROBLEM is None, f"requires a real kernel sandbox: {NATIVE_PROBLEM}")
class SelectedToolchainTests(unittest.TestCase):
    def test_selected_git_node_and_go_run_without_outside_read_or_source_write(self):
        if not all(shutil.which(name) for name in ("git", "node", "npm", "go")):
            self.skipTest("requires installed Git, Node/npm and Go")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            root = home / "project"
            root.mkdir()
            outside = home / "outside"
            outside.write_text("private fixture")
            source = root / "sum.go"
            source.write_text("package sample\nfunc Sum(a,b int) int { return a+b }\n")
            (root / "go.mod").write_text("module sample\n\ngo 1.24\n")
            (root / "sum_test.go").write_text(
                'package sample\nimport "testing"\nfunc TestSum(t *testing.T) { if Sum(2,3)!=5 { t.Fatal("bad") } }\n'
            )
            (root / "package.json").write_text('{"scripts":{"test":"node check.cjs"}}')
            (root / "check.cjs").write_text(
                'require("node:assert/strict").equal(2+3,5); console.log("node check passed");\n'
            )
            subprocess.run(["/usr/bin/git", "init", "--quiet", str(root)], check=True)
            selected = toolchain.discover(root, ["npm test", "go test ./..."], os.environ)
            boundary = containment.prepare(
                root, read_roots=selected["read_roots"], links=selected["links"], environment=os.environ
            )

            def run(command):
                return subprocess.run(
                    [boundary["shell"], "-c", command], cwd=root, capture_output=True, text=True, timeout=60
                )

            for command in [
                *selected["probes"],
                "npm test",
                "go test ./...",
                'python3 -c "import json, subprocess; assert json.loads(\\"[30]\\")[0] == 30; '
                'assert subprocess.run([\\"go\\", \\"version\\"], capture_output=True).returncode == 0"',
            ]:
                result = run(command)
                self.assertEqual(0, result.returncode, (command, result.stdout, result.stderr))
            for command in (
                "cat " + shlex.quote(str(outside)),
                ": > " + shlex.quote(str(source)),
                ": > " + shlex.quote(str(outside)),
            ):
                result = run(command)
                self.assertNotEqual(0, result.returncode, command)
                self.assertRegex(result.stderr, DENIED)
            self.assertEqual("private fixture", outside.read_text())
            self.assertEqual("package sample\nfunc Sum(a,b int) int { return a+b }\n", source.read_text())

    def test_rustup_s_proxy_is_known_by_its_file_not_its_name(self):
        # Linux rustup installs ~/.cargo/bin/cargo as a hard link of rustup, so its name is cargo (#4017).
        with tempfile.TemporaryDirectory() as directory:
            bin_dir = Path(directory).resolve()
            (bin_dir / "rustup").write_bytes(b"proxy")
            os.link(bin_dir / "rustup", bin_dir / "cargo")
            (bin_dir / "other").write_bytes(b"proxy")
            (bin_dir / "rustup-init").symlink_to(bin_dir / "rustup")
            self.assertTrue(toolchain._rustup_proxy(bin_dir / "cargo"))
            self.assertTrue(toolchain._rustup_proxy((bin_dir / "rustup-init").resolve()))
            self.assertFalse(toolchain._rustup_proxy(bin_dir / "other"))

    def test_cargo_through_rustup_runs_contained_without_the_person_s_cargo_home(self):
        # #4017: every Rust project was refused, since discovery selects cargo for a Cargo.toml.
        candidates = [shutil.which("cargo"), str(Path.home() / ".cargo/bin/cargo")]
        candidates += sorted(str(p) for p in Path("/opt/homebrew/Cellar/rustup").glob("*/libexec/bin/cargo"))
        cargo = next(
            (Path(c) for c in candidates if c and Path(c).is_file() and toolchain._rustup_proxy(Path(c).resolve())),
            None,
        )
        if cargo is None:
            self.skipTest("requires cargo through a native rustup proxy")
        env = {**os.environ, "PATH": f"{cargo.parent}:{os.environ.get('PATH', '')}"}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve() / "crate"
            (root / "src").mkdir(parents=True)
            (root / "Cargo.toml").write_text('[package]\nname = "sample"\nversion = "0.1.0"\nedition = "2021"\n')
            (root / ".gitignore").write_text("/target\n")  # as `cargo new` writes it
            (root / "src/lib.rs").write_text(
                "pub fn sum(a: i32, b: i32) -> i32 { a + b }\n"
                "#[cfg(test)]\nmod tests { #[test] fn adds() { assert_eq!(super::sum(2, 3), 5); } }\n"
            )
            subprocess.run(["/usr/bin/git", "init", "--quiet", str(root)], check=True)
            subprocess.run(["/usr/bin/git", "-C", str(root), "add", "-A"], check=True)
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(root),
                    "-c",
                    "user.name=t",
                    "-c",
                    "user.email=t@example.com",
                    "commit",
                    "-qm",
                    "c",
                ],
                check=True,
            )
            selected = toolchain.discover(root, ["cargo test --offline"], env)
            self.assertEqual(["RUSTUP_HOME"], sorted(selected["environment"]))
            cargo_home = Path(env.get("CARGO_HOME") or Path.home() / ".cargo").resolve()
            for read in selected["read_roots"]:
                with self.subTest(read=read):
                    self.assertFalse(Path(read) == cargo_home or (cargo_home / "credentials.toml").is_relative_to(read))
            developer = [str(p) for p in (Path("/Library/Developer/CommandLineTools"),) if p.is_dir()]
            for allow_write in (True, False):
                with self.subTest(allow_write=allow_write):
                    boundary = containment.prepare(
                        root,
                        allow_write=allow_write,
                        read_roots=[*selected["read_roots"], *developer],
                        links=selected["links"],
                        environment=env,
                        tool_environment=selected["environment"],
                        verification_copy=not allow_write,
                    )
                    cwd = root
                    if not allow_write:  # a readonly stage builds in its verification copy
                        import autocode_verification_copy as copies

                        cwd, _ = copies.execution(
                            boundary["verification_copy"], boundary["verification_copy_sha256"], root
                        )
                    for command in (
                        *selected["probes"],
                        "cargo test --offline",
                        'test "$HOME" != ' + shlex.quote(str(Path.home())),
                    ):
                        result = subprocess.run(
                            [boundary["shell"], "-c", command], cwd=cwd, capture_output=True, text=True, timeout=600
                        )
                        self.assertEqual(0, result.returncode, (command, result.stdout[-2000:], result.stderr[-2000:]))

    def test_tool_found_through_a_symlinked_toolchain_directory_runs_contained(self):
        # A version manager's `current` link, then a link to the real binary: both outside every mount.
        git = shutil.which("git")
        if not git:
            self.skipTest("requires Git")
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            root, versions = base / "project", base / "toolchains" / "v1"
            root.mkdir()
            versions.mkdir(parents=True)
            (versions / "git").symlink_to(Path(git).resolve())
            (base / "toolchains" / "current").symlink_to(versions, target_is_directory=True)
            subprocess.run(["/usr/bin/git", "init", "--quiet", str(root)], check=True)
            env = {"PATH": str(base / "toolchains" / "current") + ":/usr/bin:/bin"}
            selected = toolchain.discover(root, [], env)
            self.assertIn([str(base / "toolchains" / "current"), str(versions)], selected["links"])
            self.assertIn([str(versions / "git"), str(Path(git).resolve())], selected["links"])
            roots = selected["read_roots"]
            developer = Path("/Library/Developer/CommandLineTools")
            if developer.is_dir():
                roots = [*roots, str(developer)]
            boundary = containment.prepare(root, read_roots=roots, links=selected["links"], environment=env)
            for command in [*selected["probes"], "git --version", "command -v git"]:
                result = subprocess.run(
                    [boundary["shell"], "-c", command], cwd=root, capture_output=True, text=True, timeout=20
                )
                self.assertEqual(0, result.returncode, (command, result.stderr))
            self.assertEqual(str(base / "toolchains" / "current" / "git"), result.stdout.strip())

    def test_a_virtualenv_in_a_linked_directory_runs_contained(self):
        # macOS's /tmp is a link to /private/tmp: a venv named through it must run, not pause the run.
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            root = base / "project"
            root.mkdir()
            subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(base / "real" / "venv")], check=True)
            (base / "alias").symlink_to(base / "real", target_is_directory=True)
            env = {"PATH": f"{base / 'alias' / 'venv' / 'bin'}:/usr/bin:/bin"}
            selected = toolchain.discover(root, ["python3 -c 'print(1)'"], env)
            boundary = containment.prepare(
                root, read_roots=selected["read_roots"], links=selected["links"], environment=env
            )
            result = subprocess.run(
                [boundary["shell"], "-c", "python3 -c 'import sys; print(sys.prefix)'"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn(Path(result.stdout.strip()).resolve(), {base / "real" / "venv"})

    def test_linked_worktree_reads_git_metadata_without_main_checkout_source(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory).resolve()
            main, task = base / "main", base / "task"
            main.mkdir()
            subprocess.run(["/usr/bin/git", "init", "--quiet", str(main)], check=True)
            subprocess.run(
                [
                    "/usr/bin/git",
                    "-C",
                    str(main),
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "commit",
                    "--allow-empty",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
            subprocess.run(
                ["/usr/bin/git", "-C", str(main), "worktree", "add", "--quiet", "--detach", str(task)], check=True
            )
            private = main / "unrelated.txt"
            private.write_text("not part of the task")
            selected = toolchain.discover(task, [], os.environ)
            boundary = containment.prepare(
                task, read_roots=selected["read_roots"], links=selected["links"], environment=os.environ
            )
            for command in selected["probes"]:
                result = subprocess.run(
                    [boundary["shell"], "-c", command], cwd=task, capture_output=True, text=True, timeout=20
                )
                self.assertEqual(0, result.returncode, result.stderr)
            denied = subprocess.run(
                [boundary["shell"], "-c", "cat " + shlex.quote(str(private))],
                cwd=task,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertNotEqual(0, denied.returncode)
            self.assertRegex(denied.stderr, DENIED)

    def test_selected_python_has_its_own_stdlib_and_extension_dependencies(self):
        if not shutil.which("python3"):
            self.skipTest("requires Python on PATH")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            selected = toolchain.discover(root, ['python3 -c "import json"'], os.environ)
            boundary = containment.prepare(
                root, read_roots=selected["read_roots"], links=selected["links"], environment=os.environ
            )
            result = subprocess.run(
                [
                    boundary["shell"],
                    "-c",
                    "python3 -c "
                    + shlex.quote(
                        "import json, ssl, sqlite3, ctypes, hashlib; "
                        'assert sqlite3.connect(":memory:").execute("select 2+3").fetchone()[0] == 5; '
                        'print("selected interpreter imports passed")'
                    ),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(0, result.returncode, result.stderr)
            self.assertIn("selected interpreter imports passed", result.stdout)

    def test_relative_path_selects_the_same_git_as_the_task_shell(self):
        git = shutil.which("git")
        if not git:
            self.skipTest("requires Git")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "bin").mkdir()
            (root / "bin/git").symlink_to(Path(git).resolve())
            subprocess.run(["/usr/bin/git", "init", "--quiet", str(root)], check=True)
            env = {**os.environ, "PATH": "bin:/usr/bin:/bin"}
            selected = toolchain.discover(root, [], env)
            self.assertEqual(str(root / "bin/git"), shlex.split(selected["probes"][0])[0])
            roots = [*selected["read_roots"]]
            developer = Path("/Library/Developer/CommandLineTools")
            if developer.is_dir():
                roots.append(str(developer))
            boundary = containment.prepare(root, read_roots=roots, links=selected["links"], environment=env)
            for command in [*selected["probes"], "git status --short"]:
                result = subprocess.run(
                    [boundary["shell"], "-c", command], cwd=root, capture_output=True, text=True, timeout=20
                )
                self.assertEqual(0, result.returncode, result.stderr)

    def test_system_shell_negative_controls_keep_native_containment(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory).resolve()
            root = home / "project"
            root.mkdir()
            source = root / "test_failure.py"
            contents = (
                "import unittest\n"
                "class NegativeControl(unittest.TestCase):\n"
                "    def test_failure(self):\n"
                '        self.fail("EXPECTED_NEGATIVE_CONTROL")\n'
            )
            source.write_text(contents)
            outside = home / "outside"
            outside.write_text("private fixture")
            subprocess.run(["/usr/bin/git", "init", "--quiet", str(root)], check=True)
            env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
            commands = [shlex.join([shell, "-c", "! python3 -m unittest test_failure -v"]) for shell in ("sh", "bash")]
            selected = toolchain.discover(root, commands, env)
            roots = selected["read_roots"]
            developer = Path("/Library/Developer/CommandLineTools")
            if developer.is_dir():
                roots = [*roots, str(developer)]
            boundary = containment.prepare(root, read_roots=roots, links=selected["links"], environment=env)
            for command in [*selected["probes"], *commands]:
                result = subprocess.run(
                    [boundary["shell"], "-c", command], cwd=root, capture_output=True, text=True, timeout=30
                )
                self.assertEqual(0, result.returncode, (command, result.stdout, result.stderr))
                if command in commands:
                    self.assertIn("EXPECTED_NEGATIVE_CONTROL", result.stderr)
                    self.assertIn("FAILED (failures=1)", result.stderr)
            for shell in ("sh", "bash"):
                for denied in ("cat " + shlex.quote(str(outside)), ": > " + shlex.quote(str(source))):
                    command = shlex.join([shell, "-c", denied])
                    result = subprocess.run(
                        [boundary["shell"], "-c", command], cwd=root, capture_output=True, text=True, timeout=10
                    )
                    self.assertNotEqual(0, result.returncode, command)
                    self.assertRegex(result.stderr, DENIED)
            self.assertEqual(contents, source.read_text())
            self.assertEqual("private fixture", outside.read_text())

    def test_project_shell_wrappers_are_rejected_without_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "bin").mkdir()
            marker = root / "WRAPPER_EXECUTED"
            env = {"PATH": "bin:/usr/bin:/bin"}
            for shell in ("sh", "bash"):
                with self.subTest(shell=shell):
                    wrapper = root / "bin" / shell
                    wrapper.write_text("#!/bin/sh\ntouch " + shlex.quote(str(marker)) + "\n")
                    wrapper.chmod(0o755)
                    with self.assertRaisesRegex(RuntimeError, "requires the native system shell"):
                        toolchain.discover(root, [shell + " -c :"], env)
                    self.assertFalse(marker.exists())

    def test_readiness_does_not_execute_project_python_startup_hooks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "sitecustomize.py").write_text('raise SystemExit("PROJECT_STARTUP_HOOK")\n')
            subprocess.run(["/usr/bin/git", "init", "--quiet", str(root)], check=True)
            selected = toolchain.discover(root, [], os.environ)
            boundary = containment.prepare(
                root, read_roots=selected["read_roots"], links=selected["links"], environment=os.environ
            )
            for command in selected["probes"]:
                result = subprocess.run(
                    [boundary["shell"], "-c", "PYTHONPATH=. " + command],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                self.assertEqual(0, result.returncode, result.stderr)
            control = subprocess.run(
                [boundary["shell"], "-c", "PYTHONPATH=. python3 -c 'print(1)'"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertNotEqual(0, control.returncode)
            self.assertIn("PROJECT_STARTUP_HOOK", control.stderr)
