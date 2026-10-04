"""Approved executable checks cannot be replaced; their scope must be possible."""
from pathlib import Path
import tempfile
import unittest

import autocode_verification_plan as plan
import autocode_goal_lifecycle as lifecycle
from goal_fixtures import body


class CommandsTests(unittest.TestCase):
    def test_explicit_commands_are_extracted_but_prose_and_test_names_are_not(self):
        for text, expected in (
            ("python3 -m unittest test_greet.py", ["python3 -m unittest test_greet.py"]),
            ("Run `go test ./...` and inspect `balance`.", ["go test ./..."]),
            ("Validator runs `python3 -m unittest` and reads `README.md`.", ["python3 -m unittest"]),
            ("Run `go test ./a` and `go test ./b`.", ["go test ./a", "go test ./b"]),
            ("`go test ./...`", ["go test ./..."]),
            ("Inspect README.md and verify the exact text `python3 -m temperature VALUE UNIT` "
             "without invoking the metavariable template as a command.", []),
            ("Run python3 -m unittest tests.test_greet", ["python3 -m unittest tests.test_greet"]),
            ("test: test_c1_hello", []), ("Execute CLI cases", []),
            # Prose after a plain command (live bugfix-trivial, 2026-09-30): replayed as a command, it never passes.
            ("python3 -m unittest -v passes; Validator reads the diff", []),
            ("Run python3 -m unittest -v via capture and read the diff", []),
            # A sentence after the command (live parallel-diamond, 2026-10-01): unittest read its words as modules.
            ("Run python3 -m unittest integration.test_check from repo root: 2 tests OK.", []),
            ("python3 -m unittest integration.test_check, expect 2 tests OK.", []),
            ("python3 -m unittest tests.test_a tests.test_b", ["python3 -m unittest tests.test_a tests.test_b"]),
            ("pytest tests/test_x.py::test_y -k name", ["pytest tests/test_x.py::test_y -k name"]),
            ("go test ./...", ["go test ./..."]),
            # A sentence after -c (live architecture-two-services, 2026-09-30): NameError when replayed.
            ("Run python3 -c doing a topological sort/DFS over depends_on.", []),
            ("Run `python3 -c doing a topological sort` over the graph", []),
            ("python3 -c: Kahn topological sort over depends_on sorts all nodes (AC6)", []),
            ("sh -c checking each file exists", []),
            ("python3 -c exit", ["python3 -c exit"]),
            ('python3 -c "import a; a.f()" x y', ['python3 -c "import a; a.f()" x y']),
            ("python3 -m unittest discover -s tests -t .", ["python3 -m unittest discover -s tests -t ."]),
            ("python3 -c 'assert f(1, 2) == 3'", ["python3 -c 'assert f(1, 2) == 3'"]),
            ("pytest --verify --output=out.xml tests", ["pytest --verify --output=out.xml tests"])):
            with self.subTest(text=text):
                self.assertEqual(expected, plan.commands(text))

    def test_live_unquoted_verification_prose_stays_with_the_validator(self):
        for method in (
            "Run python3 -m unittest -v after the correction and require a successful exit with the complete suite passing.",
            "Run python3 -m unittest -v and confirm it exits successfully.",
        ):
            with self.subTest(method=method):
                self.assertEqual([], plan.commands(method))
        self.assertEqual(["python3 -m unittest -v"], plan.commands(
            "Run `python3 -m unittest -v` and confirm it exits successfully."))

    def test_declared_zero_exits_keep_the_original_required_commands(self):
        self.assertEqual(["go test ./a", "go test ./b"], plan.commands(
            "Run `go test ./a` and `go test ./b` and confirm exit codes 0/0."))

    def test_exit_expectations_with_ambiguous_assignments_are_refused(self):
        for method in (
            "Run `go test ./a` and `go test ./b` and confirm exit code 2.",
            "Run `go test ./a` and inspect `README.md` then confirm exit code 2.",
            "Run `go test ./a` and confirm exit code 256.",
            "Run `go test ./a` and confirm exit code -1.",
        ):
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, "one status.*per executable command"):
                plan.commands(method)

    def test_later_milestone_commands_are_not_forced_on_the_current_task(self):
        state = {"goal_contract": {"body": {"acceptance_criteria": [
            {"id": "C1", "verification_method": "go test ./first"},
            {"id": "C2", "verification_method": "go test ./later"}]}},
            "current_task": {"acceptance_criteria": ["C1"], "validation_plan": ["go test ./first"]}}
        self.assertEqual(["go test ./first"], plan.approved_commands(state))

    def test_visual_check_alias_is_prescribed_without_admitting_other_task_commands(self):
        command = "autocode visual-check --policy visual-policy.json --policy-sha256 " + "a" * 64
        self.assertEqual([command], plan.commands(command))
        self.assertEqual(["/venv/bin/" + command], plan.commands("Run `/venv/bin/" + command + "`"))
        self.assertEqual([], plan.commands(command + " and inspect the screenshots"))
        self.assertEqual([], plan.commands("autocode ui 'Design a dashboard'"))
        self.assertEqual([], plan.commands("autocode 'Build an application'"))
        state = {"goal_contract": {"body": {"acceptance_criteria": [
            {"id": "visual", "human_review": False, "verification_method": command}]}},
            "current_task": {"acceptance_criteria": ["visual"], "validation_plan": ["python -m unittest"]}}
        self.assertEqual(["python -m unittest", command], plan.approved_commands(state))

    def test_discovery_only_requires_packages_between_start_and_explicit_top(self):
        for command, expected in (
            ("python3 -m unittest discover -s tests -t .", ["tests/__init__.py"]),
            ("python3 -m unittest discover -s src/tests -t src", ["src/tests/__init__.py"]),
            ("python3 -m unittest discover --start-directory=a/b --top-level-directory=.",
             ["a/__init__.py", "a/b/__init__.py"]),
            ("python3 -m unittest discover -s tests", []),
            ("python3 -m unittest discover -s . -t .", [])):
            with self.subTest(command=command):
                self.assertEqual(expected, plan.package_markers(command))


class ScopeTests(unittest.TestCase):
    def test_missing_initializer_must_be_assigned_before_the_plan_is_approved(self):
        with tempfile.TemporaryDirectory() as workspace:
            value = body()
            value["acceptance_criteria"][0]["verification_method"] = "python3 -m unittest discover -s tests -t ."
            value["milestones"][0]["affected_paths"] = ["greet.py", "tests/test_greet.py"]
            state = {"workspace": workspace}
            with self.assertRaisesRegex(ValueError, "requires tests/__init__.py.*outside affected_paths"):
                lifecycle.validate_body(state, value)
            self.assertNotIn("goal_contract", state)
            value["milestones"][0]["affected_paths"].append("tests/__init__.py")
            lifecycle.validate_body(state, value)
            value["milestones"][0]["affected_paths"] = ["greet.py", "tests/"]
            lifecycle.validate_body(state, value)
            Path(workspace, "tests").mkdir()
            Path(workspace, "tests/__init__.py").write_text("")
            value["milestones"][0]["affected_paths"] = ["greet.py", "tests/test_greet.py"]
            lifecycle.validate_body(state, value)
