"""CI Java admission refuses incomplete or wrong toolchains before publishing PATH."""

import contextlib
import io
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

import ci_java
import ci_test_selection


class JavaAdmissionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.home = root / "jdk"
        (self.home / "bin").mkdir(parents=True)
        for name in ("java", "javac"):
            (self.home / "bin" / name).touch()
        self.environment = {
            "JAVA_HOME_17_X64": str(self.home),
            "GITHUB_ENV": str(root / "env"),
            "GITHUB_PATH": str(root / "path"),
        }
        self.run = Mock(
            side_effect=[
                subprocess.CompletedProcess(
                    [], 0, "", "    java.specification.version = 17\nopenjdk version 17.0.16\n"
                ),
                subprocess.CompletedProcess([], 0, "javac 17.0.16\n", ""),
            ]
        )

    def test_installed_java_and_compiler_are_validated_and_recorded_without_downloads(self):
        Path(self.environment["GITHUB_ENV"]).write_text("EXISTING=kept\n")
        Path(self.environment["GITHUB_PATH"]).write_text("/existing/bin\n")
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(self.home.resolve(), ci_java.configure(self.environment, run=self.run))
        self.assertEqual(
            f"EXISTING=kept\nJAVA_HOME={self.home.resolve()}\n", Path(self.environment["GITHUB_ENV"]).read_text()
        )
        self.assertEqual(
            f"/existing/bin\n{self.home.resolve() / 'bin'}\n", Path(self.environment["GITHUB_PATH"]).read_text()
        )
        self.assertIn(f"Using installed JDK: {self.home.resolve()}", output.getvalue())
        self.assertIn("java.specification.version = 17", output.getvalue())
        self.assertIn("openjdk version 17.0.16", output.getvalue())
        self.assertIn("javac 17.0.16", output.getvalue())
        self.assertEqual(["java", "javac"], [Path(call.args[0][0]).name for call in self.run.call_args_list])
        self.assertTrue(
            all(Path(call.args[0][0]).parent == self.home.resolve() / "bin" for call in self.run.call_args_list)
        )

    def test_wrong_or_failed_tools_never_publish_a_toolchain(self):
        for runtime, compiler in [
            ("java.specification.version = 21", "javac 17.0.16"),
            ("java.specification.version = 17", "javac 21.0.1"),
            ("java.specification.version = 170", "javac 17.0.16"),
            ("java.specification.version = 17", "javac 170.0.1"),
            ("", "javac 17.0.16"),
        ]:
            run = Mock(
                side_effect=[
                    subprocess.CompletedProcess([], 0, runtime, ""),
                    subprocess.CompletedProcess([], 0, compiler, ""),
                ]
            )
            with self.subTest(runtime=runtime, compiler=compiler), self.assertRaises(ValueError):
                ci_java.configure(self.environment, run=run)
            self.assertFalse(Path(self.environment["GITHUB_ENV"]).exists())
            self.assertFalse(Path(self.environment["GITHUB_PATH"]).exists())
        self.run.side_effect = subprocess.CalledProcessError(1, ["java"])
        with self.assertRaises(subprocess.CalledProcessError):
            ci_java.configure(self.environment, run=self.run)
        self.assertFalse(Path(self.environment["GITHUB_ENV"]).exists())

    def test_missing_home_or_compiler_fails_before_a_tool_is_run(self):
        for environment in ({}, {**self.environment, "JAVA_HOME_17_X64": str(self.home / "absent")}):
            with self.subTest(environment=environment), self.assertRaises((KeyError, FileNotFoundError)):
                ci_java.configure(environment, run=self.run)
        (self.home / "bin/javac").unlink()
        with self.assertRaises(ValueError):
            ci_java.configure(self.environment, run=self.run)
        self.run.assert_not_called()


class WorkflowAdmissionTests(unittest.TestCase):
    def test_workflow_edits_select_the_installed_toolchain_contract(self):
        self.assertIn("tests.test_ci_java", ci_test_selection.dependencies(".github/workflows/tests.yml"))

    @unittest.skipIf(os.name == "nt", "The Ubuntu Java admission step runs POSIX tools")
    def test_workflow_executes_installed_tools_and_publishes_their_validated_versions(self):
        root = Path(__file__).resolve().parents[1]
        workflow = (root / ".github/workflows/tests.yml").read_text()
        steps = re.findall(r"(?ms)^      - (.*?)(?=^      - |^  [A-Za-z]|\Z)", workflow)
        java_steps = [
            step for step in steps if re.search(r"uses: actions/setup-java@|name: Select .*Java", step.splitlines()[0])
        ]
        self.assertEqual(1, len(java_steps))
        command = re.search(r"(?m)^        run: (.+)$", java_steps[0])
        self.assertIsNotNone(command, "Java admission must use installed tools rather than a release downloader")
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "jdk"
            (home / "bin").mkdir(parents=True)
            for name in ("java", "javac"):
                path = home / "bin" / name
                path.write_text(
                    "#!"
                    + sys.executable
                    + "\nprint("
                    + repr("java.specification.version = 17" if name == "java" else "javac 17.0.16")
                    + ")\n"
                )
                path.chmod(path.stat().st_mode | stat.S_IXUSR)
            env_file, path_file = Path(directory) / "env", Path(directory) / "path"
            environment = {
                **os.environ,
                "JAVA_HOME_17_X64": str(home),
                "GITHUB_ENV": str(env_file),
                "GITHUB_PATH": str(path_file),
                "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", ""),
            }
            result = subprocess.run(
                command.group(1),
                shell=True,
                executable="/bin/bash",
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            self.assertIn("java.specification.version = 17", result.stdout)
            self.assertIn("javac 17.0.16", result.stdout)
            self.assertEqual("JAVA_HOME=" + str(home.resolve()) + "\n", env_file.read_text())
            self.assertEqual(str(home.resolve() / "bin") + "\n", path_file.read_text())

    @unittest.skipIf(os.name == "nt", "The Ubuntu Java admission step runs POSIX tools")
    def test_valid_version_output_with_unsuccessful_tool_exit_is_refused(self):
        root = Path(__file__).resolve().parents[1]
        for failed_tool in ("java", "javac"):
            with self.subTest(tool=failed_tool), tempfile.TemporaryDirectory() as directory:
                home = Path(directory) / "jdk"
                (home / "bin").mkdir(parents=True)
                for name in ("java", "javac"):
                    path = home / "bin" / name
                    version = "java.specification.version = 17" if name == "java" else "javac 17.0.16"
                    path.write_text(
                        "#!"
                        + sys.executable
                        + "\nprint("
                        + repr(version)
                        + ")\nraise SystemExit("
                        + str(int(name == failed_tool))
                        + ")\n"
                    )
                    path.chmod(path.stat().st_mode | stat.S_IXUSR)
                env_file, path_file = Path(directory) / "env", Path(directory) / "path"
                environment = {
                    **os.environ,
                    "JAVA_HOME_17_X64": str(home),
                    "GITHUB_ENV": str(env_file),
                    "GITHUB_PATH": str(path_file),
                }
                result = subprocess.run(
                    [sys.executable, str(root / "tools/ci_java.py")],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                self.assertNotEqual(0, result.returncode, result.stdout + result.stderr)
                self.assertIn("CalledProcessError", result.stderr)
                self.assertFalse(env_file.exists())
                self.assertFalse(path_file.exists())
