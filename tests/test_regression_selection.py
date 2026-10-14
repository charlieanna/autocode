"""Literal milestone selectors retain their collector, options and earlier tests."""

import shlex
import unittest

import autocode_regression_selection as selection


class RegressionSelectionTests(unittest.TestCase):
    def widen(self, command, changes=None, *, framework="pytest", test_root=None):
        return selection.widen(
            command,
            changes or {"tests/test_first.py": "added", "tests/test_second.py": "added"},
            framework=framework,
            test_root=test_root,
        )

    def test_pytest_union_preserves_environment_interpreter_plugins_and_filters(self):
        command = (
            "env MODE=proof PYTHONPATH='/support with spaces' '/venv with spaces/bin/python' -m pytest "
            "-p no:cacheprovider -vv -k 'c1 or c2' -m approved --import-mode=importlib tests/test_first.py"
        )
        self.assertEqual(shlex.split(command) + ["tests/test_second.py"], shlex.split(self.widen(command)))

    def test_unittest_module_and_file_aliases_are_not_duplicated(self):
        for selector in ("tests.test_first", "tests/test_first.py", "./tests/test_first.py"):
            with self.subTest(selector=selector):
                command = "python3 -m unittest -v -k c tests.test_first".replace("tests.test_first", selector)
                self.assertEqual(
                    shlex.split(command) + ["tests/test_second.py"],
                    shlex.split(self.widen(command, framework="unittest")),
                )

    def test_a_node_selector_keeps_its_test_and_adds_other_cases_in_the_file(self):
        command = "python3 -m pytest tests/test_first.py::TestFirst::test_c1"
        self.assertEqual(
            shlex.split(command) + ["tests/test_first.py", "tests/test_second.py"],
            shlex.split(self.widen(command)),
        )

    def test_full_suite_and_directory_collection_are_never_narrowed(self):
        for command in (
            "python3 -m pytest -vv",
            "python3 -m pytest .",
            "python3 -m pytest ./tests/",
            "python3 -m unittest -v",
            "python3 -m unittest discover -v -s tests",
        ):
            with self.subTest(command=command):
                framework = "unittest" if "unittest" in command else "pytest"
                self.assertEqual(command, self.widen(command, framework=framework))

    def test_deleted_non_test_and_escaping_paths_are_not_appended(self):
        command = "python3 -m pytest tests/test_first.py"
        changes = {
            "tests/test_first.py": "modified",
            "tests/test_second.py": "deleted",
            "calc.py": "modified",
            "../test_external.py": "added",
            "/test_absolute.py": "added",
            "tests/../test_escape.py": "added",
        }
        self.assertEqual(command, self.widen(command, changes))

    def test_rooted_selection_keeps_workspace_relative_paths_and_excludes_other_roots(self):
        command = "python3 -m pytest components/api/tests/test_first.py"
        changes = {
            "components/api/tests/test_first.py": "modified",
            "components/api/tests/test_second.py": "added",
            "components/other/tests/test_other.py": "added",
        }
        self.assertEqual(
            shlex.split(command) + ["components/api/tests/test_second.py"],
            shlex.split(self.widen(command, changes, test_root="components/api")),
        )

    def test_unknown_arity_shell_programs_and_escaping_selectors_are_unchanged(self):
        for command in (
            "python3 -m pytest --custom-option value tests/test_first.py",
            "python3 -m pytest --pyargs tests.test_first",
            "cd tests && python3 -m pytest test_first.py",
            "python3 -m pytest tests/test_first.py && true",
            "python3 -m pytest ../test_external.py",
            "python3 -m pytest /test_absolute.py",
            "python3 -m pytest -k",
        ):
            with self.subTest(command=command):
                self.assertEqual(command, self.widen(command))

    def test_framework_mismatch_cannot_rewrite_a_collector(self):
        command = "python3 -m pytest tests/test_first.py"
        self.assertEqual(command, self.widen(command, framework="unittest"))
