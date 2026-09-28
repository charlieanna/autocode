"""Model-free fix verification (autocode_verify).

These tests execute real test suites in scratch Git worktrees; they never launch
a provider. Each negative control is a way a candidate could look fixed without
being fixed, and each must be rejected by execution, not by reading a report.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import autocode_verify as verify  # noqa: E402
import scenario_references as references  # noqa: E402
import task_scenarios as scenarios  # noqa: E402

REFERENCE = references.BUGFIX_REFERENCE
SEED = scenarios.BUGFIX_SEED


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def project_file(project, path):
    return (project.root / path).read_text()


class Project:
    """A committed BUGFIX-01 seed; tests overlay candidate files on the working tree."""

    def __init__(self, files=SEED):
        self.temp = tempfile.TemporaryDirectory(prefix="fix-verify-")
        self.root = Path(self.temp.name).resolve() / "project"
        self.root.mkdir()
        references.write(files, self.root)
        git(self.root, "init", "-q")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
        self.base = git(self.root, "rev-parse", "HEAD")
        self.evidence = Path(self.temp.name) / "evidence"

    def write(self, files):
        references.write(files, self.root)

    def verify(self, **options):
        framework = verify.detect_framework(self.root)
        suite = options.pop("suite_command", None) or framework.suite
        base_suite = verify.baseline(self.root, self.base, self.evidence, framework=framework,
                                     suite_command=suite, timeout=120)
        return verify.verify(self.root, self.base, self.evidence, framework=framework, base_suite=base_suite,
                             timeout=120, **options)

    def close(self):
        self.temp.cleanup()


class VerifyCase(unittest.TestCase):
    def project(self, files=SEED):
        project = Project(files)
        self.addCleanup(project.close)
        return project

    def test_reference_fix_is_proven_by_a_fail_to_pass_flip(self):
        project = self.project()
        project.write(REFERENCE)
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual("derived:unittest", result["commands"]["regression_source"])
        self.assertNotEqual(0, result["checks"]["regression_on_base"]["exit_code"])
        self.assertEqual(0, result["checks"]["regression_on_candidate"]["exit_code"])
        self.assertEqual(["test_greet.py"], result["test_files"])
        self.assertEqual(["greet.py"], result["source_files"])
        self.assertEqual(1, result["stats"]["source_files"])
        # Verification never runs in, or changes, the candidate workspace.
        self.assertEqual([], [line for line in git(project.root, "worktree", "list").splitlines()[1:]])
        self.assertEqual({"greet.py", "test_greet.py"}, set(result["changes"]))

    def test_fix_without_a_regression_test_fails(self):
        project = self.project()
        project.write({"greet.py": REFERENCE["greet.py"]})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("No regression test" in reason for reason in result["failures"]))

    def test_fix_without_a_test_is_only_unverified_when_allowed(self):
        project = self.project()
        project.write({"greet.py": REFERENCE["greet.py"]})
        result = project.verify(allow_no_test=True)
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)

    def test_a_test_that_passes_on_the_unfixed_code_proves_nothing(self):
        project = self.project()
        vacuous = SEED["test_greet.py"].replace(
            "    def test_ada(self):", "    def test_blank(self):\n        self.assertTrue(True)\n\n    def test_ada(self):")
        project.write({"greet.py": REFERENCE["greet.py"], "test_greet.py": vacuous})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("do not reproduce the bug" in reason for reason in result["failures"]), result)

    def test_only_tests_changed_is_not_a_fix(self):
        project = self.project()
        project.write({"test_greet.py": REFERENCE["test_greet.py"]})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("Only test files changed" in reason for reason in result["failures"]))
        self.assertTrue(any("fail on the candidate" in reason for reason in result["failures"]), result)

    def test_removing_an_existing_test_is_rejected(self):
        project = self.project()
        weakened = REFERENCE["test_greet.py"].replace("    def test_two_arg(self):", "    def two_arg_disabled(self):")
        project.write({"greet.py": REFERENCE["greet.py"], "test_greet.py": weakened})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("test_greet.py::test_two_arg" in reason for reason in result["failures"]), result)

    def test_deleting_a_test_file_is_rejected(self):
        files = {**SEED, "tests/test_extra.py": "import unittest\n\nclass T(unittest.TestCase):\n"
                                                "    def test_ok(self):\n        pass\n"}
        project = self.project(files)
        (project.root / "tests/test_extra.py").unlink()
        project.write(REFERENCE)
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("deleted" in reason for reason in result["failures"]), result)

    def test_a_fix_that_breaks_another_test_fails_the_suite(self):
        project = self.project()
        broken = REFERENCE["greet.py"].replace('return f"Hello, {name}"', 'return f"Hi, {name}"')
        project.write({"greet.py": broken, "test_greet.py": REFERENCE["test_greet.py"]})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("fail on the candidate" in reason or "fails on the candidate" in reason
                            for reason in result["failures"]), result)

    def test_pre_existing_failures_do_not_block_when_nothing_new_fails(self):
        flaky = ("import unittest\n\nclass Env(unittest.TestCase):\n"
                 "    def test_needs_network(self):\n        self.fail('no network in CI')\n")
        project = self.project({**SEED, "test_env.py": flaky})
        project.write(REFERENCE)
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual("failing_tests", result["baseline"]["health"])
        self.assertTrue(any("already failed on base" in note for note in result["notes"]), result)

    def test_new_failures_are_named_even_when_base_already_fails(self):
        flaky = ("import unittest\n\nclass Env(unittest.TestCase):\n"
                 "    def test_needs_network(self):\n        self.fail('no network in CI')\n")
        project = self.project({**SEED, "test_env.py": flaky})
        broken = REFERENCE["greet.py"].replace('return f"Hello, {name}"', 'return f"Hi, {name}"')
        project.write({"greet.py": broken, "test_greet.py": REFERENCE["test_greet.py"]})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("test_ada" in reason for reason in result["failures"]), result)

    def test_pre_existing_failure_in_the_changed_test_file_is_not_counted(self):
        """The regression test shares a file with an environment-dependent failure (common upstream)."""
        needs_env = ("\n    def test_needs_secret(self):\n"
                     "        self.assertTrue(__import__('os').environ.get('NO_SUCH_SECRET_XYZ'))\n")
        seed = {**SEED, "test_greet.py": SEED["test_greet.py"].replace("\n\nif __name__", needs_env + "\n\nif __name__")}
        project = self.project(seed)
        project.write({"greet.py": REFERENCE["greet.py"],
                       "test_greet.py": REFERENCE["test_greet.py"].replace("\n\nif __name__", needs_env + "\n\nif __name__")})
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(["test_greet.TestGreet.test_blank_name_rejected"], result["fail_to_pass"])
        self.assertTrue(any("already fail on base were not counted" in note for note in result["notes"]), result)

    def test_a_new_test_the_fix_does_not_fix_is_not_excused(self):
        still_broken = REFERENCE["test_greet.py"].replace(
            "\n\nif __name__",
            "\n    def test_tab_name(self):\n"
            "        proc = subprocess.run([sys.executable, 'greet.py', 'A\\tB'], capture_output=True)\n"
            "        self.assertEqual(3, proc.returncode)\n\n\nif __name__")
        project = self.project()
        project.write({"greet.py": REFERENCE["greet.py"], "test_greet.py": still_broken})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("fail on the candidate: test_greet.TestGreet.test_tab_name" in reason
                            for reason in result["failures"]), result)

    @unittest.skipUnless(verify._python_can_import(sys.executable, "pytest"), "pytest is not installed")
    def test_pytest_projects_use_junit_results(self):
        files = {"pyproject.toml": "[tool.pytest.ini_options]\npythonpath = [\"src\"]\n",
                 "src/calc/__init__.py": "def mean(values):\n    return sum(values) / len(values)\n",
                 "tests/test_calc.py": "from calc import mean\n\n\ndef test_mean():\n    assert mean([1, 2, 3]) == 2\n"}
        project = self.project(files)
        project.write({"src/calc/__init__.py": "def mean(values):\n    if not values:\n"
                                               "        raise ValueError('empty')\n    return sum(values) / len(values)\n",
                       "tests/test_calc.py": files["tests/test_calc.py"] + "\n\ndef test_empty():\n"
                                             "    import pytest\n    with pytest.raises(ValueError):\n        mean([])\n"})
        framework = verify.detect_framework(project.root, python=sys.executable)
        self.assertEqual("pytest", framework.name)
        base_suite = verify.baseline(project.root, project.base, project.evidence, framework=framework,
                                     suite_command=framework.suite, timeout=120)
        result = verify.verify(project.root, project.base, project.evidence, framework=framework,
                               base_suite=base_suite, timeout=120)
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(["tests.test_calc::test_empty"], result["fail_to_pass"])

    def test_scratch_tree_code_wins_over_an_editable_install(self):
        """Review r12: a .pth in a linked venv must not import the user's checkout instead."""
        with tempfile.TemporaryDirectory() as temp:
            tree = Path(temp)
            (tree / "src").mkdir()
            env = verify.test_environment(tree, {"PYTHONPATH": "/elsewhere"})
            self.assertEqual([str(tree / "src"), str(tree), "/elsewhere"], env["PYTHONPATH"].split(os.pathsep))
            self.assertEqual("1", env["CI"])

    def test_generated_version_file_reaches_the_scratch_trees(self):
        """A setuptools-scm/hatch-vcs package imports a git-ignored _version.py that
        exists only where the project was installed. The fix is made in a separate task
        worktree, as in a real run; the proof must still import the package."""
        project = self.project({
            ".gitignore": "src/pkg/_version.py\nbuild/\n",
            "src/pkg/__init__.py": "from ._version import VERSION\n",
            "src/pkg/calc.py": "def mean(values):\n    return sum(values) / len(values)\n",
            "tests/test_calc.py": "import unittest\n\nfrom pkg.calc import mean\n\n\n"
                                  "class Mean(unittest.TestCase):\n    def test_mean(self):\n"
                                  "        self.assertEqual(2, mean([2]))\n"})
        project.write({"src/pkg/_version.py": "VERSION = '1.0'\n",
                       "build/lib/pkg/calc.py": "raise SystemExit('stale build output')\n"})
        task = Path(project.temp.name) / "task"
        git(project.root, "worktree", "add", "-q", "--detach", str(task), project.base)
        self.addCleanup(git, project.root, "worktree", "remove", "--force", str(task))
        references.write({"src/pkg/calc.py": "def mean(values):\n"
                                             "    return sum(values) / len(values) if values else 0\n",
                          "tests/test_calc.py": project_file(project, "tests/test_calc.py")
                          + "\n    def test_empty(self):\n        self.assertEqual(0, mean([]))\n"}, task)
        framework = verify.detect_framework(task)
        base_suite = verify.baseline(task, project.base, project.evidence, framework=framework,
                                     suite_command=framework.suite, timeout=120, dependencies_from=project.root)
        result = verify.verify(task, project.base, project.evidence, framework=framework, base_suite=base_suite,
                               timeout=120, dependencies_from=project.root)
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(["tests.test_calc.Mean.test_empty"], result["fail_to_pass"])
        # The generated file is shared context, not part of the fix; build output is not copied.
        self.assertEqual({"src/pkg/calc.py": "modified", "tests/test_calc.py": "modified"}, result["changes"])
        tree = Path(project.temp.name) / "tree"
        git(project.root, "worktree", "add", "-q", "--detach", str(tree), project.base)
        self.addCleanup(git, project.root, "worktree", "remove", "--force", str(tree))
        self.assertEqual(["src/pkg/_version.py"], verify.copy_generated_sources(project.root, tree))
        self.assertFalse((tree / "build").exists())

    def test_a_suite_that_cannot_start_is_broken_not_failing(self):
        """Review finding 15: command-not-found and no-results runs stop before any model call."""
        receipt = {"timed_out": False, "results": None, "results_expected": False}
        self.assertEqual("broken", verify.suite_health({**receipt, "exit_code": 127}))
        self.assertEqual("failing", verify.suite_health({**receipt, "exit_code": 1}))
        self.assertEqual("broken", verify.suite_health({**receipt, "exit_code": 0, "results_expected": True}))
        self.assertEqual("timeout", verify.suite_health({**receipt, "exit_code": None, "timed_out": True}))

    def test_untracked_new_test_file_counts_as_the_regression_test(self):
        project = self.project()
        new_test = ("import subprocess, sys, unittest\n\nclass Blank(unittest.TestCase):\n"
                    "    def test_blank(self):\n"
                    "        proc = subprocess.run([sys.executable, 'greet.py', ''], capture_output=True)\n"
                    "        self.assertEqual(2, proc.returncode)\n")
        project.write({"greet.py": REFERENCE["greet.py"], "tests/test_blank.py": new_test})
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual({"greet.py": "modified", "tests/test_blank.py": "added"}, result["changes"])

    def test_candidate_workspace_changes_are_detected_after_commit_too(self):
        project = self.project()
        project.write(REFERENCE)
        git(project.root, "add", "-A")
        git(project.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "builder commit")
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)

    def test_builder_regression_command_must_name_a_changed_test(self):
        chosen = verify.select_commands(None, ["tests/test_blank.py"],
                                        reported={"regression_command": "grep -q strip greet.py"})
        self.assertIsNone(chosen["regression"])
        self.assertTrue(chosen["notes"])
        chosen = verify.select_commands(None, ["tests/test_blank.py"],
                                        reported={"regression_command": "python -m pytest tests/test_blank.py"})
        self.assertEqual("builder", chosen["regression_source"])

    def test_explicit_commands_win_over_detection(self):
        framework = verify.Framework("pytest", "python -m pytest -q", python="python")
        chosen = verify.select_commands(framework, ["tests/test_a.py"], suite_command="make check",
                                        regression_command="make one")
        self.assertEqual(("make check", "explicit", "make one", "explicit"),
                         (chosen["suite"], chosen["suite_source"], chosen["regression"], chosen["regression_source"]))

    def test_test_path_classification(self):
        for path in ("tests/test_x.py", "pkg/test_x.py", "x_test.go", "src/a.test.ts", "spec/a_spec.rb",
                     "src/test/java/FooTest.java", "__tests__/a.js", "pkg/testdata/in.txt", "conftest.py",
                     "src/__snapshots__/x.test.ts.snap", "test/unit/helpers.js", "pkg/core/tests/data.json",
                     "TestParser.java"):
            self.assertTrue(verify.is_test_path(path), path)
        for path in ("greet.py", "src/contest.py", "latest.py", "src/protest/x.go", "attestation.rs",
                     "src/Latest.java", "src/Contest.kt", "numpy/testing/utils.py", "django/test/client.py",
                     "api/spec/openapi.yaml"):
            self.assertFalse(verify.is_test_path(path), path)

    def test_framework_detection(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git(root, "init", "-q")
            (root / "go.mod").write_text("module x\n")
            git(root, "add", "-A")
            framework = verify.detect_framework(root)
            self.assertEqual(("go", "go test ./..."), (framework.name, framework.suite))
            self.assertEqual("go test ./pkg/a", framework.targeted(["pkg/a/a_test.go"]))
            package = {"scripts": {"test": "jest"}, "devDependencies": {"jest": "29"}}
            (root / "package.json").write_text(json.dumps(package))
            (root / "go.mod").unlink()
            git(root, "add", "-A")
            framework = verify.detect_framework(root)
            self.assertEqual(("jest", "npm test --silent"), (framework.name, framework.suite))
            self.assertIn("jest src/a.test.js", framework.targeted(["src/a.test.js"]))

    def test_unittest_results_are_parsed_per_test(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "out.log"
            log.write_text(
                "test_a (m.C.test_a) ... ok\n"
                "test_b (m.C.test_b)\nA docstring line ... FAIL\n"
                "test_d (m.C.test_d) ... noisy output from the test\nok\n"
                "test_e (m.C.test_e) ... skipped 'needs network'\n"
                "test_f (m.C.test_f) ... expected failure\n"
                "======================================================================\n"
                "FAIL: test_b (m.C.test_b)\nERROR: test_c (m.C)\n"
                "----------------------------------------------------------------------\n"
                "Ran 6 tests in 0.1s\n\nFAILED (failures=1, errors=1, skipped=1, expected failures=1)\n")
            framework = verify.Framework("unittest", "python -m unittest", python="python")
            results = verify.per_test_results(framework, {"output": str(log)}, Path(temp) / "none.xml")
            self.assertEqual({"passed": ["m.C.test_a", "m.C.test_d"], "failed": ["m.C.test_b", "m.C::test_c"],
                              "skipped": ["m.C.test_e", "m.C.test_f"], "collection_errors": [], "total": 6,
                              "complete": True}, results)

    def test_skipping_a_test_that_passed_on_base_is_a_regression(self):
        """Review r1: break greet(), skip the test that would catch it, add a real regression test."""
        project = self.project()
        broken = REFERENCE["greet.py"].replace('return f"Hello, {name}"', 'return f"Hi, {name}"')
        skipped = REFERENCE["test_greet.py"].replace(
            "    def test_ada(self):", "    @unittest.skip('flaky')\n    def test_ada(self):")
        project.write({"greet.py": broken, "test_greet.py": skipped})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertTrue(any("did not pass on the candidate" in reason and "test_ada" in reason
                            for reason in result["failures"]), result)

    def test_an_import_error_on_base_is_not_a_reproduction(self):
        """Review r11: a no-op helper the test imports makes base fail only at import time."""
        project = self.project()
        noop = SEED["greet.py"].replace("def main(", "def normalize(name):\n    return name\n\n\ndef main(")
        tests = SEED["test_greet.py"].replace("from greet import greet", "from greet import greet, normalize")
        tests = tests.replace("    def test_ada(self):",
                              "    def test_normalize(self):\n        self.assertEqual('x', normalize('x'))\n\n"
                              "    def test_ada(self):")
        project.write({"greet.py": noop, "test_greet.py": tests})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertTrue(any("only fail to import or collect" in reason for reason in result["failures"]), result)
        self.assertEqual([], result["fail_to_pass"])

    def test_builder_chosen_commands_never_make_a_pass(self):
        """Review r3: with no detectable framework, the Builder's own commands prove nothing."""
        files = {"lib.sh": "echo old\n", "tests/test_lib.sh": "sh lib.sh | grep -q old\n"}
        project = self.project(files)
        project.write({"lib.sh": "echo new\n", "tests/test_bug.sh": "sh lib.sh | grep -q new\n"})
        reported = {"test_command": "sh tests/test_bug.sh", "regression_command": "sh tests/test_bug.sh"}
        result = verify.verify(project.root, project.base, project.evidence, reported=reported, timeout=60)
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertEqual(("builder", "builder"), (result["commands"]["regression_source"],
                                                   result["commands"]["suite_source"]))
        self.assertFalse(verify._mentions_tests("grep -q FIXED lib.sh # tests/test_bug.sh", ["tests/test_bug.sh"]))
        self.assertFalse(verify._mentions_tests("true", ["t/t.sh"]))
        self.assertTrue(verify._mentions_tests("python -m pytest tests/test_x.py::test_y", ["tests/test_x.py"]))

    def test_a_run_that_reports_no_results_is_not_a_pass(self):
        """Review r4: product code that exits the test process early with status 0."""
        project = self.project()
        exits = REFERENCE["greet.py"].replace('    return f"Hello, {name}"',
                                              '    import os\n    os._exit(0)')
        project.write({"greet.py": exits, "test_greet.py": REFERENCE["test_greet.py"]})
        result = project.verify()
        self.assertNotEqual(verify.PASS, result["verdict"], result)
        self.assertTrue(any("without reporting any test result" in reason
                            for reason in result["failures"] + result["unverified"]), result)

    def test_non_ascii_and_binary_changes_are_counted(self):
        """Review r8: quoted numstat paths made a 60-line change count as 0 lines."""
        project = self.project({**SEED, "café.py": "x = 1\n"})
        project.write({"café.py": "x = 1\n" + "y = 2\n" * 60, "logo.bin": "\0binary"})
        stats = verify.diff_stats(project.root, project.base, verify.changed_files(project.root, project.base))
        self.assertEqual(60 + verify.BINARY_LINES, stats["source_lines_changed"])
        self.assertEqual(["logo.bin"], stats["binary_files"])
        self.assertEqual(["logo.bin"], stats["non_code_files"])

    def test_scratch_tree_handles_a_directory_that_became_a_file(self):
        project = self.project({**SEED, "data/a.txt": "a\n"})
        (project.root / "data" / "a.txt").unlink()
        (project.root / "data").rmdir()
        (project.root / "data").write_text("now a file\n")
        changes = verify.changed_files(project.root, project.base)
        tree = verify.make_tree(project.root, project.base, project.evidence / "tree", project.root, changes)
        self.addCleanup(verify.remove_tree, project.root, tree)
        self.assertEqual("now a file\n", (tree / "data").read_text())

    @unittest.skipUnless(verify._python_can_import(sys.executable, "pytest"), "pytest is not installed")
    def test_deselecting_a_test_through_config_is_not_a_fix(self):
        """Review r2: no product change, only `addopts = --deselect`."""
        files = {"pytest.ini": "[pytest]\n",
                 "calc.py": "def mean(v):\n    return sum(v) / len(v)\n",
                 "test_calc.py": "from calc import mean\n\n\ndef test_mean():\n    assert mean([2]) == 2\n\n\n"
                                 "def test_empty():\n    assert mean([]) == 0\n"}
        project = self.project(files)
        project.write({"pytest.ini": "[pytest]\naddopts = --deselect test_calc.py::test_empty\n",
                       "test_calc.py": files["test_calc.py"] + "\n\ndef test_more():\n    assert mean([4]) == 4\n"})
        framework = verify.detect_framework(project.root, python=sys.executable)
        base_suite = verify.baseline(project.root, project.base, project.evidence, framework=framework,
                                     suite_command=framework.suite, timeout=120)
        result = verify.verify(project.root, project.base, project.evidence, framework=framework,
                               base_suite=base_suite, timeout=120)
        self.assertEqual(verify.FAIL, result["verdict"], result)


if __name__ == "__main__":
    unittest.main()
