import shlex
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_test_environment as test_environment
import autocode_verify as verify
from autocode_python_tests import parse, verbose_unittest
from autocode_verification_schedule import collection_kind


class DeclaredPythonEnvironmentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        self.python = self.root / "external environment/bin/python"
        self.python.parent.mkdir(parents=True)
        self.python.symlink_to(sys.executable)

    def test_literal_external_virtualenv_keeps_its_symlink_path(self):
        command = shlex.join(
            ["env", "PYTHONPATH=/support", "PYTEST_PLUGINS=bootstrap", str(self.python), "-m", "pytest", "tests"]
        )
        selected = test_environment.python_for_test_command(self.project, command)
        self.assertEqual(str(self.python), selected)
        self.assertNotEqual(str(self.python.resolve()), selected)

    def test_workspace_relative_interpreter_is_based_on_the_project(self):
        python = self.project / "runtime/bin/python3"
        python.parent.mkdir(parents=True)
        python.symlink_to(sys.executable)
        self.assertEqual(
            str(python), test_environment.python_for_test_command(self.project, "./runtime/bin/python3 -m pytest tests")
        )

    def test_explicit_current_directory_interpreters_are_not_bare_names(self):
        for name in ("python", "python3"):
            with self.subTest(name=name):
                python = self.project / name
                python.symlink_to(sys.executable)
                self.assertEqual(
                    str(python), test_environment.python_for_test_command(self.project, f"./{name} -m pytest tests")
                )
                self.assertIsNone(test_environment.python_for_test_command(self.project, f"{name} -m pytest tests"))

    def test_quiet_unittest_uses_the_same_literal_interpreter(self):
        command = shlex.join([str(self.python), "-m", "unittest", "-q", "tests.test_one"])
        self.assertEqual(str(self.python), test_environment.python_for_test_command(self.project, command))

    def test_unsupported_or_bare_commands_leave_normal_discovery_to_the_caller(self):
        marker = self.root / "must-not-execute"
        for command in (
            "go test ./...",
            "python3 -m pytest",
            "env PATH=/elsewhere python3 -m pytest",
            f"{shlex.quote(str(self.python))} -B -m pytest",
            f"{shlex.quote(str(self.python))} -m pytest; touch {marker}",
            "env -i python3 -m pytest",
            "env X=$(echo proof) python3 -m pytest",
            None,
        ):
            with self.subTest(command=command):
                self.assertIsNone(test_environment.python_for_test_command(self.project, command))
        self.assertFalse(marker.exists())

    def test_missing_and_nonexecutable_paths_are_not_selected(self):
        missing = self.root / "missing/bin/python"
        self.assertIsNone(
            test_environment.python_for_test_command(self.project, shlex.join([str(missing), "-m", "pytest"]))
        )
        disabled = self.root / "disabled/bin/python"
        disabled.parent.mkdir(parents=True)
        disabled.write_text("not an executable\n")
        disabled.chmod(0o644)
        self.assertIsNone(
            test_environment.python_for_test_command(self.project, shlex.join([str(disabled), "-m", "pytest"]))
        )


class PythonTestCommandTests(unittest.TestCase):
    def test_targeted_pytest_keeps_interpreter_environment_and_collection_options(self):
        command = (
            "env PYTHONPATH='/support with spaces' PYTEST_PLUGINS=bootstrap "
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 '/venv with spaces/bin/python' -m pytest "
            "-q -o 'python_files=test*.py' -o minversion= -p no:cacheprovider tests/old"
        )
        invocation = parse(command)
        self.assertEqual("pytest", collection_kind(command))
        self.assertEqual("/venv with spaces/bin/python", invocation.python)
        expected = shlex.split(command)[:-1] + ["tests/new.py"]
        self.assertEqual(expected, shlex.split(invocation.targeted(["tests/new.py"])))

    def test_unsupported_option_runs_unchanged_suite_instead_of_guessing_its_argument(self):
        command = "env MODE=proof python3 -m pytest --custom-option value tests/"
        self.assertEqual(shlex.split(command), shlex.split(parse(command).targeted(["test_new.py"])))

    def test_env_unittest_keeps_literal_environment_and_verbose_results(self):
        invocation = parse("env CONFIG=proof python3 -m unittest discover -v")
        self.assertEqual("env CONFIG=proof python3 -m unittest -v test_new.py", invocation.targeted(["test_new.py"]))

    def test_shell_programs_and_env_options_are_not_exact_collectors(self):
        for command in (
            "env -i python3 -m pytest",
            'env -S "python3 -m pytest"',
            "env X=$(echo proof) python3 -m pytest",
            "python3 -m pytest && true",
            "env X=1 python3 -m pytest; true",
            'env X=1 bash -c "python3 -m pytest"',
            "env X=1 python3 -m unittest",
            "env X=1 python3 -m pytest\ntrue",
        ):
            with self.subTest(command=command):
                self.assertIsNone(parse(command))
                self.assertIsNone(collection_kind(command))

    def test_a_quiet_unittest_command_is_run_with_names_and_discover_stays_first(self):
        self.assertIsNone(parse("python3 -m unittest tests.test_verify -q"))
        self.assertEqual(
            "python3 -m unittest -v tests.test_verify", verbose_unittest("python3 -m unittest tests.test_verify -q")
        )
        self.assertEqual(
            "python3 -m unittest discover -v -s tests", verbose_unittest("python3 -m unittest -q discover -s tests")
        )
        self.assertEqual(
            "python3 -m unittest discover -v -s tests", verbose_unittest("python3 -m unittest discover -q -s tests")
        )
        self.assertEqual("env X=1 python3 -m unittest -v", verbose_unittest("env X=1 python3 -m unittest"))
        self.assertEqual(
            "python3 -m unittest -v tests.test_verify", verbose_unittest("python3 -m unittest -v tests.test_verify")
        )
        self.assertEqual("python3 -m unittest -q && true", verbose_unittest("python3 -m unittest -q && true"))

    def test_a_quiet_unittest_suite_names_every_test(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "test_one.py").write_text(
                "import unittest\n\nclass T(unittest.TestCase):\n"
                "    def test_one(self):\n        self.assertTrue(True)\n"
            )
            evidence = root / "evidence"
            evidence.mkdir()
            command = f"{sys.executable} -m unittest -q test_one"
            framework = verify.Framework("unittest", command, python=sys.executable)
            receipt = verify.run_suite(framework, command, root, evidence, "quiet", timeout=60)
            self.assertEqual(0, receipt["exit_code"], Path(receipt["output"]).read_text())
            self.assertNotIn(" -q", receipt["command"])
            self.assertIn(" -v ", receipt["command"])
            self.assertEqual(["test_one.T.test_one"], receipt["results"]["passed"])
            self.assertTrue(receipt["results"]["complete"])
            self.assertEqual("passing", verify.suite_health(receipt))
