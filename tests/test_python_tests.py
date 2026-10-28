import os
import shlex
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_test_environment as test_environment
import autocode_verify as verify
from autocode_python_tests import (
    empty_package_marker,
    parse,
    prefers_pytest,
    pytest_classname,
    unittest_results,
    verbose_unittest,
)
from autocode_verification_schedule import collection_kind


class PythonProofIdentityTests(unittest.TestCase):
    def test_only_empty_package_markers_have_no_test_or_collection_behavior(self):
        for source in ("", "\n # package marker\n"):
            self.assertTrue(empty_package_marker("tests/__init__.py", source))
        for path, source in (
            ("tests/test_guard.py", ""),
            ("tests/__init__.py", "import math\n"),
            ("tests/__init__.py", "def load_tests(loader, tests, pattern): return tests\n"),
            ("tests/__init__.py", "def test_guard(): assert True\n"),
            ("tests/__init__.py", '"package documentation"\n'),
            ("tests/__init__.py", "not valid python ?"),
        ):
            with self.subTest(path=path, source=source):
                self.assertFalse(empty_package_marker(path, source))

    def test_junit_prefix_must_name_the_physical_tree_and_an_existing_module(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / ".proof-output" / "scratch-run" / "candidate"
            module = root / "repos" / "history-service" / "tests" / "test_history.py"
            module.parent.mkdir(parents=True)
            module.write_text("class HistoryTests: pass\n")
            name = "repos.history-service.tests.test_history.HistoryTests"
            prefix = root.relative_to(Path(temp)).as_posix().replace("/", ".") + "."
            self.assertEqual(name, pytest_classname(prefix + name, root))
            self.assertEqual(name, pytest_classname(name, root))
            self.assertEqual(prefix + name, pytest_classname(prefix + name, None))
            missing = prefix + "tests.test_missing.Tests"
            self.assertEqual(missing, pytest_classname(missing, root))
            outside = Path(temp) / "external.py"
            outside.write_text("class Tests: pass\n")
            linked = root / "tests/test_external.py"
            linked.parent.mkdir(parents=True)
            linked.symlink_to(outside)
            foreign = prefix + "tests.test_external.Tests"
            self.assertEqual(foreign, pytest_classname(foreign, root))

    def test_real_project_prefix_and_ambiguous_module_identities_are_not_stripped(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "again" / "again"
            for name in ("test_fix.py", "again/test_fix.py", "scratch/candidate/test_fix.py"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("class Tests: pass\n")
            for name in ("again.again.test_fix.Tests", "scratch.candidate.test_fix.Tests"):
                self.assertEqual(name, pytest_classname(name, root))


class DeclaredTestStyleTests(unittest.TestCase):
    def test_real_pytest_declarations_select_the_collector_that_runs_them(self):
        for source in (
            "def test_positive():\n assert 1 == 1\n",
            "class TestTotals:\n def test_positive(self):\n  assert 1 == 1\n",
            "import unittest\nclass Legacy(unittest.TestCase):\n def test_old(self): pass\n"
            "def test_positive():\n assert 1 == 1\n",
        ):
            with self.subTest(source=source):
                self.assertTrue(prefers_pytest({"tests/test_totals.py": source}))

    def test_comments_helpers_unittest_and_collection_hooks_do_not_switch_collectors(self):
        for files in (
            {"tests/test_totals.py": "# def test_example(): pass\nVALUE = 'def test_fake(): pass'\n"},
            {"helpers.py": "def test_helper(): pass\n"},
            {"tests.py": "def test_helper(): pass\n"},
            {"test_totals.py": "import unittest\nclass TestTotals(unittest.TestCase):\n def test_sum(self): pass\n"},
            {"test_totals.py": "def test_sum(): pass\ndef load_tests(loader, tests, pattern): return tests\n"},
            {"test_totals.py": "def test_sum(): pass\n", "tests/__init__.py": "from hooks import load_tests\n"},
        ):
            with self.subTest(files=files):
                self.assertFalse(prefers_pytest(files))


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


class UnittestResultParserTests(unittest.TestCase):
    def test_component_runs_sum_summary_counts_while_ordinary_runs_use_the_last(self):
        text = (
            "test_one (first.Case.test_one) ... ok\n"
            "Ran 1 test in 0.001s\n"
            "OK\n"
            "test_two (second.Case.test_two) ... ok\n"
            "test_three (second.Case.test_three) ... ok\n"
            "Ran 2 tests in 0.002s\n"
            "OK\n"
        )
        ordinary = unittest_results(text)
        component = unittest_results(text, component=True)
        self.assertEqual(2, ordinary["total"])
        self.assertEqual(3, component["total"])
        self.assertEqual(["first.Case.test_one", "second.Case.test_three", "second.Case.test_two"], component["passed"])
        self.assertTrue(component["complete"])

    def test_missing_or_truncated_run_summary_does_not_establish_results(self):
        output = "test_one (suite.Case.test_one) ... ok\n"
        for text in (output, output + "Ran 1 test in"):
            for component in (False, True):
                with self.subTest(text=text, component=component):
                    self.assertIsNone(unittest_results(text, component=component))

    def test_summary_with_unfinished_named_test_remains_incomplete(self):
        text = (
            "test_done (suite.Case.test_done) ... ok\n"
            "test_unfinished (suite.Case.test_unfinished) ... \n"
            "Ran 2 tests in 0.001s\n"
        )
        results = unittest_results(text)
        self.assertEqual(2, results["total"])
        self.assertEqual(["suite.Case.test_done"], results["passed"])
        self.assertEqual([], results["failed"])
        self.assertEqual([], results["skipped"])
        self.assertFalse(results["complete"])

    def test_ensure_python_shim_links_only_python_and_verifies_target_and_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            shim_root = Path(tmp) / "shims"
            shim_dir = test_environment.ensure_python_shim(sys.executable, root=shim_root)
            self.assertIsNotNone(shim_dir)
            shim_path = Path(shim_dir)
            self.assertTrue((shim_path / "python").is_symlink())
            self.assertFalse((shim_path / "python3").exists())
            self.assertEqual(sys.executable, os.readlink(shim_path / "python"))

            # Replace the link with a wrong target and confirm ensure_python_shim repairs it.
            (shim_path / "python").unlink()
            (shim_path / "python").symlink_to(Path(tmp) / "wrong-python")
            repaired = test_environment.ensure_python_shim(sys.executable, root=shim_root)
            self.assertEqual(shim_dir, repaired)
            self.assertEqual(sys.executable, os.readlink(shim_path / "python"))

            # Refuse a directory whose owner does not match os.getuid().
            if hasattr(os, "getuid"):
                with mock.patch("os.getuid", return_value=os.getuid() + 1):
                    self.assertIsNone(test_environment.ensure_python_shim(sys.executable, root=shim_root))

    def test_test_environment_injects_shim_when_bare_python_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "empty_proj"
            project.mkdir()
            empty_bin = Path(tmp) / "empty_bin"
            empty_bin.mkdir()
            temp_root = Path(tmp) / "tmp"
            temp_root.mkdir()
            with mock.patch("tempfile.gettempdir", return_value=str(temp_root)):
                env = verify.test_environment(project, env={"PATH": str(empty_bin)})
            self.assertIn("PATH", env)
            python_which = shutil.which("python", path=env["PATH"])
            self.assertIsNotNone(python_which)
            self.assertEqual(Path(sys.executable).resolve(), Path(python_which).resolve())
            self.assertIsNone(shutil.which("python3", path=env["PATH"]))
