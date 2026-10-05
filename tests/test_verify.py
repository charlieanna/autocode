"""Model-free fix verification (autocode_verify).

These tests execute real test suites in scratch Git worktrees; they never launch
a provider. Each negative control is a way a candidate could look fixed without
being fixed, and each must be rejected by execution, not by reading a report.
"""
from __future__ import annotations

import json
import os
import shutil
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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
        # A commit starts `git maintenance run --auto --detach`. On CI's Git 2.55 it repacks once two
        # loose objects share the objects/17 shard, writing packs under .git while cleanup removes it
        # ("Directory not empty"). docs/bugs/git-background-repack-cleanup-race.md
        git(self.root, "config", "maintenance.auto", "false")
        git(self.root, "config", "gc.auto", "0")
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


class ProjectFixtureTests(unittest.TestCase):
    def test_a_commit_starts_no_background_maintenance(self):
        project = Project()
        self.addCleanup(project.close)
        trace = Path(project.temp.name) / "trace2.json"
        (project.root / "extra.txt").write_text("extra\n")
        git(project.root, "add", "extra.txt")
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "extra"],
                       cwd=project.root, check=True, capture_output=True,
                       env={**os.environ, "GIT_TRACE2_EVENT": str(trace)})
        children = [event["argv"] for event in map(json.loads, trace.read_text().splitlines())
                    if event.get("event") == "child_start"]
        self.assertEqual([], [argv for argv in children if {"maintenance", "gc"} & set(argv)], children)


class VerifyCase(unittest.TestCase):
    def project(self, files=SEED):
        project = Project(files)
        self.addCleanup(project.close)
        return project

    @unittest.skipUnless(shutil.which('node'), 'Node is required for named Node proof')
    def test_node_named_case_flip_preserves_original_custom_suite(self):
        import autocode_regression as regression
        seed = {'package.json': '{"scripts":{"test":"node tests/check.cjs"}}',
                'app.cjs': 'module.exports = n => n + 1;\n',
                'tests/check.cjs': "require('node:assert/strict').equal(require('../app.cjs')(1),2);\n"}
        project = self.project(seed)
        project.write({'app.cjs': 'module.exports = n => n === 2 ? 4 : n + 1;\n',
                       'tests/cases.cjs': "const {test}=require('node:test');"
                       "const assert=require('node:assert/strict'); const app=require('../app.cjs');"
                       "test('test_c1_two',()=>assert.equal(app(2),4));"
                       "test('test_c2_one',()=>assert.equal(app(1),2));\n"})
        result = project.verify()
        regression.check_cases(result, [{'id': 'C1', 'text': 'two gives four', 'test_name': 'test_c1_two'},
                                       {'id': 'C2', 'text': 'one stays two', 'test_name': 'test_c2_one',
                                        'kind': 'preserve'}])
        self.assertEqual(verify.PASS, result['verdict'], result)
        self.assertEqual('derived:node', result['commands']['regression_source'])
        self.assertEqual('npm test --silent', result['commands']['suite'])
        self.assertEqual(['tests/cases.cjs::test_c1_two'], result['fail_to_pass'])
        self.assertEqual(['tests/cases.cjs::test_c2_one'], result['pass_to_pass'])
        self.assertEqual(seed['tests/check.cjs'], project_file(project, 'tests/check.cjs'))
        for source in ('module.exports = n => n + 1; // still broken\n',
                       'module.exports = n => n === 2 ? 4 : 0; // breaks the protected suite\n'):
            with self.subTest(source=source):
                project.write({'app.cjs': source})
                rejected = project.verify()
                self.assertEqual(verify.FAIL, rejected['verdict'], rejected)

    def test_ignored_vendor_reaches_scratch_probe_without_sharing_writes(self):
        project = self.project({**SEED, '.gitignore': 'vendor/\n'})
        project.write({'vendor/example/resource.txt': 'offline'})
        probe = ("python3 -c \"from pathlib import Path; p=Path('vendor/example/resource.txt'); "
                 "assert p.read_text() == 'offline'; p.write_text('scratch edit')\"")
        result = verify.scratch_run(project.root, project.evidence, command=probe)
        self.assertEqual(0, result['exit_code'], result)
        self.assertEqual('offline', project_file(project, 'vendor/example/resource.txt'))
        self.assertEqual({}, verify.changed_files(project.root, project.base))

    def test_new_vendor_source_is_not_copied_into_the_unfixed_baseline(self):
        seed = {
            'app.py': "from pathlib import Path\ndef value():\n    path = Path('vendor/value.txt')\n"
                      "    return int(path.read_text()) if path.exists() else 0\n",
            'test_app.py': "import unittest\nfrom app import value\nclass ValueTests(unittest.TestCase):\n"
                           "    def test_smoke(self):\n        self.assertIsInstance(value(), int)\n",
        }
        for ignored in (False, True):
            with self.subTest(force_tracked_under_ignore=ignored):
                project = self.project({**seed, '.gitignore': 'vendor/\n' if ignored else ''})
                project.write({'vendor/value.txt': '1\n', 'test_app.py': seed['test_app.py']
                               + '    def test_restores_value(self):\n        self.assertEqual(1, value())\n'})
                if ignored:
                    git(project.root, 'add', '-f', 'vendor/value.txt')
                    project.write({'vendor/offline.txt': 'ignored dependency\n'})
                result = project.verify(dependencies_from=project.root)
                self.assertEqual(verify.PASS, result['verdict'], result['failures'])
                self.assertIn('test_app.ValueTests.test_restores_value', result['fail_to_pass'])

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

    def test_linked_task_inherits_project_dependencies_for_parent_and_child_tests(self):
        project = self.project({**SEED, ".gitignore": ".venv/\n"})
        environment = project.root / ".venv"
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(environment)], check=True,
                       capture_output=True, text=True)
        python = environment / "bin" / "python"
        site = Path(subprocess.check_output(
            [str(python), "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"], text=True).strip())
        (site / "worktree_test_dependency.py").write_text("value = 42\n")
        task = Path(project.temp.name) / "linked-task"
        git(project.root, "worktree", "add", "--detach", str(task), project.base)
        extra = ("\nclass Dependencies(unittest.TestCase):\n"
                 "    def test_project_dependency_is_available(self):\n"
                 "        import worktree_test_dependency\n"
                 "        self.assertEqual(42, worktree_test_dependency.value)\n"
                 "    def test_fixture_child_uses_the_same_environment(self):\n"
                 "        import subprocess\n"
                 "        child = subprocess.run(['python3', '-c', "
                 "'import worktree_test_dependency; assert worktree_test_dependency.value == 42'], "
                 "capture_output=True, text=True)\n"
                 "        self.assertEqual(0, child.returncode, child.stderr)\n")
        candidate = {**REFERENCE, "test_greet.py": REFERENCE["test_greet.py"] + extra}
        references.write(candidate, task)
        framework = verify.detect_framework(task)
        self.assertEqual(str(python), framework.python)
        result = verify.verify(task, project.base, project.evidence, framework=framework,
                               dependencies_from=task, timeout=120)
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertIn("test_greet.Dependencies.test_fixture_child_uses_the_same_environment",
                      result["pass_to_pass"])
        self.assertEqual(0, result["checks"]["suite_on_candidate"]["exit_code"])
        self.assertFalse((task / ".venv").exists(), "verification must not modify the task checkout")

    def test_a_task_local_environment_overrides_the_main_checkout(self):
        project = self.project()
        for root in (project.root, Path(project.temp.name) / "linked-task"):
            if root != project.root:
                git(project.root, "worktree", "add", "--detach", str(root), project.base)
            (root / ".venv" / "bin").mkdir(parents=True)
            (root / ".venv" / "bin" / "python").symlink_to(sys.executable)
        task = Path(project.temp.name) / "linked-task"
        self.assertEqual(str(task / ".venv" / "bin" / "python"), verify.python_for(task))

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

    def test_preserve_only_coverage_may_add_a_test_that_passes_on_the_base(self):
        project = self.project()
        extra = project_file(project, "test_greet.py").replace(
            "    def test_ada(self):\n        self.assertEqual(greet(\"Ada\"), \"Hello, Ada\")\n",
            "    def test_ada(self):\n        self.assertEqual(greet(\"Ada\"), \"Hello, Ada\")\n\n"
            "    def test_ada_still_greets(self):\n        self.assertEqual(greet(\"Ada\"), \"Hello, Ada\")\n")
        project.write({"test_greet.py": extra})
        result = project.verify(new_behavior=True, preserve_only=True)
        self.assertEqual(verify.PASS, result["verdict"], result["failures"] + result["unverified"])
        self.assertTrue(any(name.endswith("test_ada_still_greets") for name in result["pass_to_pass"]),
                        result["pass_to_pass"])

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

    def test_partial_or_timed_out_baseline_is_not_a_fail_to_pass_proof(self):
        project = self.project()
        project.write(REFERENCE)
        framework = verify.detect_framework(project.root)
        for timed_out in (True, False):
            with self.subTest(timed_out=timed_out):
                def run(_framework, command, tree, out, label, **kwargs):
                    base = label == 'regression-on-base'
                    return {'command': command, 'exit_code': None if base and timed_out else 1 if base else 0,
                            'timed_out': base and timed_out, 'results_expected': True, 'output': 'unused', 'tail': '',
                            'results': {'passed': [] if base else ['test_greet.Case.test_empty'],
                                        'failed': ['test_greet.Case.test_empty'] if base else [],
                                        'skipped': [], 'collection_errors': [], 'total': 1, 'complete': not base}}
                with mock.patch.object(verify, 'run_suite', side_effect=run):
                    result = verify.verify(project.root, project.base, project.evidence, framework=framework)
                self.assertEqual(verify.UNVERIFIED, result['verdict'], result)
                self.assertTrue(any('base' in reason for reason in result['unverified']), result)

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

    def test_a_new_projects_untracked_files_are_detected_and_ignored_ones_are_not(self):
        # AutoCode never commits: in a new project the Builder's code and tests are all untracked.
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            git(root, "init", "-q")
            (root / "README.md").write_text("seed\n")
            git(root, "add", "-A")
            git(root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-q", "-m", "seed")
            (root / ".autocode").mkdir()
            (root / ".autocode" / ".gitignore").write_text("*\n")
            (root / ".autocode" / "test_runner_owned.py").write_text("import unittest\n")
            self.assertIsNone(verify.detect_framework(root, python=sys.executable))
            (root / "greet.py").write_text("print('hi')\n")
            (root / "test_greet.py").write_text("import unittest\n")
            framework = verify.detect_framework(root, python=sys.executable)
            self.assertEqual("unittest", framework.name)
            self.assertIn("-m unittest discover", framework.suite)

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


GO_SEED = {"go.mod": "module pager\n\ngo 1.21\n",
           "pager.go": "package pager\n\nfunc PageCount(total, size int) int {\n\treturn total / size\n}\n",
           "pager_test.go": "package pager\n\nimport \"testing\"\n\n"
                            "func TestExisting(t *testing.T) {\n\tif PageCount(10, 5) != 2 {\n\t\tt.Fatal(\"10/5\")\n\t}\n}\n"}
GO_FIX = {"pager.go": "package pager\n\nfunc PageCount(total, size int) int {\n\treturn (total + size - 1) / size\n}\n",
          "pager_test.go": GO_SEED["pager_test.go"]
          + "\nfunc Test_t1_partial_page_counts(t *testing.T) {\n\tif PageCount(11, 5) != 3 {\n\t\tt.Fatal(\"11/5\")\n\t}\n}\n"}


class SuitePreservationTests(unittest.TestCase):
    def test_suite_health_does_not_erase_real_regression_evidence(self):
        suite_test = ('import unittest\nfrom calc import keep\n'
                      'class Existing(unittest.TestCase):\n'
                      ' def test_existing(self): self.assertEqual(9, keep())\n')
        broken_import = 'import no_such_autocode_479_driver\n' + suite_test
        failing_test = suite_test.replace('assertEqual(9', 'assertEqual(8')
        skipped_test = suite_test.replace(' def test_existing',
                                         ' @unittest.skip("optional service")\n def test_existing')
        cases = [
            ('collection_only', broken_import, None, 9, 'UNVERIFIED'),
            ('passing_plus_collection', suite_test, broken_import, 9, 'UNVERIFIED'),
            ('all_preexisting_failures', failing_test, None, 9, 'UNVERIFIED'),
            ('all_skipped', skipped_test, None, 9, 'UNVERIFIED'),
            ('healthy', suite_test, None, 9, 'PASS'),
            ('passing_plus_preexisting_failure', suite_test, failing_test, 9, 'PASS'),
            ('named_regression_with_collection', suite_test, broken_import, 0, 'FAIL'),
        ]
        for name, primary, optional, kept, expected in cases:
            with self.subTest(name=name):
                files = {'calc.py': 'def add(a,b): return a-b\ndef keep(): return 9\n',
                         'test_db.py': primary}
                if optional:
                    files['test_optional.py'] = optional
                project = Project(files)
                try:
                    framework = verify.detect_framework(project.root, python=sys.executable)
                    suite = shlex.quote(sys.executable) + ' -m unittest -v test_db'
                    if optional:
                        suite += ' test_optional'
                    baseline = verify.baseline(project.root, project.base, project.evidence,
                                               framework=framework, suite_command=suite, timeout=30)
                    project.write({'calc.py': f'def add(a,b): return a+b\ndef keep(): return {kept}\n',
                                   'test_calc.py': 'import unittest\nfrom calc import add\n'
                                   'class Addition(unittest.TestCase):\n'
                                   ' def test_add(self): self.assertEqual(5,add(2,3))\n'})
                    result = verify.verify(project.root, project.base, project.evidence,
                                           framework=framework, suite_command=suite, base_suite=baseline,
                                           regression_command=shlex.quote(sys.executable) + ' -m unittest -v test_calc',
                                           timeout=30)
                    self.assertEqual(['test_calc.Addition.test_add'], result['fail_to_pass'])
                    self.assertEqual(expected, result['verdict'], result['failures'] + result['unverified'])
                    if expected == 'UNVERIFIED':
                        self.assertTrue(result['unverified'])
                        self.assertFalse(result['failures'])
                    elif expected == 'FAIL':
                        self.assertTrue(any('test_db.Existing.test_existing' in reason
                                            for reason in result['failures']), result)
                    for path, original in files.items():
                        if path != 'calc.py':
                            self.assertEqual(original, (project.root / path).read_text())
                finally:
                    project.close()


class GoResultTests(unittest.TestCase):
    """Go's per-test results come from `go test -json` (a live Go port could not be proven without them)."""

    def events(self, *rows):
        return "\n".join(json.dumps(row) for row in rows)

    def test_tests_subtests_skips_and_a_package_that_did_not_build(self):
        text = self.events(
            {"Action": "start", "Package": "m/a"},
            {"Action": "run", "Package": "m/a", "Test": "TestOk"},
            {"Action": "pass", "Package": "m/a", "Test": "TestOk"},
            {"Action": "run", "Package": "m/a", "Test": "TestTable"},
            {"Action": "run", "Package": "m/a", "Test": "TestTable/case_1"},
            {"Action": "fail", "Package": "m/a", "Test": "TestTable/case_1"},
            {"Action": "fail", "Package": "m/a", "Test": "TestTable"},
            {"Action": "run", "Package": "m/a", "Test": "TestLater"},
            {"Action": "skip", "Package": "m/a", "Test": "TestLater"},
            {"Action": "fail", "Package": "m/a"},
            {"Action": "output", "Package": "m/b", "Output": "m/b/b_test.go:3: undefined: New\n"},
            {"Action": "fail", "Package": "m/b"}) + "\n# m/b\nplain compiler output\n"
        results = verify._go_results(text)
        self.assertEqual(["m/a::TestOk"], results["passed"])
        self.assertEqual(["m/a::TestLater"], results["skipped"])
        self.assertEqual(["m/a::TestTable", "m/a::TestTable/case_1", "m/b::[build failed]"], results["failed"])
        self.assertEqual(["m/b::[build failed]"], results["collection_errors"])
        self.assertTrue(results["complete"])

    def test_a_test_that_never_ended_makes_the_results_incomplete_and_no_events_give_none(self):
        text = self.events({"Action": "run", "Package": "m", "Test": "TestHangs"}, {"Action": "fail", "Package": "m"})
        self.assertFalse(verify._go_results(text)["complete"])
        self.assertIsNone(verify._go_results("go: command not found\n"))

    def test_the_runner_asks_go_for_json_only_on_a_plain_go_test_command(self):
        go = verify.Framework("go", "go test ./...")
        self.assertEqual("go test -json ./...", verify._with_results(go, "go test ./...", "x.xml"))
        self.assertEqual("go test -json -run X .", verify._with_results(go, "go test -run X .", "x.xml"))
        self.assertEqual("go test -json ./...", verify._with_results(go, "go test -json ./...", "x.xml"))
        for command in ("cd sub && go test ./...", "go vet ./...", "go test ./... | tee log"):
            self.assertEqual(command, verify._with_results(go, command, "x.xml"))
            self.assertFalse(verify.expects_results(go, command))
        self.assertTrue(verify.expects_results(go, "go test ./..."))
        # A test id carries the test's own name, so an English case matches it (T1 -> Test_t1_...).
        import autocode_test_cases as test_cases
        self.assertEqual({"T1": ["pager::Test_t1_partial_page_counts"]},
                         test_cases.match_cases([{"id": "T1"}], ["pager::TestExisting", "pager::Test_t1_partial_page_counts"]))


@unittest.skipUnless(shutil.which("go"), "needs a Go toolchain")
class GoVerifyTests(unittest.TestCase):
    """Real Go modules through the runner's proof: base with the new tests, then the candidate."""

    def project(self, files):
        project = Project(files)
        self.addCleanup(project.close)
        return project

    def test_a_go_bug_fix_is_proven_by_the_test_that_fails_before_and_passes_after(self):
        project = self.project(GO_SEED)
        project.write(GO_FIX)
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result["failures"] + result["unverified"])
        self.assertEqual(["pager::Test_t1_partial_page_counts"], result["fail_to_pass"])
        self.assertIn("pager::TestExisting", result["pass_to_pass"])

    def test_a_go_test_that_also_passes_before_the_fix_does_not_reproduce_the_bug(self):
        project = self.project(GO_SEED)
        project.write({**GO_FIX, "pager_test.go": GO_FIX["pager_test.go"].replace("PageCount(11, 5) != 3",
                                                                                  "PageCount(10, 5) != 2")})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("do not reproduce the bug" in failure for failure in result["failures"]), result["failures"])

    def test_a_go_feature_test_that_cannot_build_on_base_proves_new_behavior(self):
        project = self.project(GO_SEED)
        project.write({"pager.go": GO_SEED["pager.go"] + "\nfunc Pages(total, size int) int { return PageCount(total, size) }\n",
                       "pager_test.go": GO_SEED["pager_test.go"]
                       + "\nfunc Test_c2_pages(t *testing.T) {\n\tif Pages(10, 5) != 2 {\n\t\tt.Fatal(\"pages\")\n\t}\n}\n"})
        result = project.verify(new_behavior=True)
        self.assertEqual(verify.PASS, result["verdict"], result["failures"] + result["unverified"])
        self.assertIn("pager::Test_c2_pages", result["fail_to_pass"])
        # The same change is not a bug reproduction: on base the new test only fails to build.
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertTrue(any("fail to import or collect" in failure for failure in result["failures"]), result["failures"])


class VendoredDependencyCopyTests(unittest.TestCase):
    def test_linked_scratch_vendor_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, tree = Path(temporary) / 'source', Path(temporary) / 'tree'
            (source / 'vendor').mkdir(parents=True)
            tree.mkdir()
            (tree / 'vendor').symlink_to(source / 'vendor', target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                verify.copy_vendored_dependencies(source, tree)

    def test_copies_complete_ignored_vendor_as_independent_files(self):
        project = Project({**SEED, '.gitignore': 'vendor/\n'})
        self.addCleanup(project.close)
        source, tree = project.root, project.evidence
        project.write({'vendor/modules.txt': 'offline', 'vendor/native/library.so': 'native'})
        verify.copy_vendored_dependencies(source, tree)
        self.assertFalse((tree / 'vendor').is_symlink())
        self.assertEqual('offline', (tree / 'vendor/modules.txt').read_text())
        self.assertEqual('native', (tree / 'vendor/native/library.so').read_text())
        (tree / 'vendor/modules.txt').write_text('scratch')
        self.assertEqual('offline', (source / 'vendor/modules.txt').read_text())

    def test_missing_dependencies_are_optional_and_existing_base_vendor_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, tree = Path(temporary) / 'source', Path(temporary) / 'tree'
            source.mkdir()
            tree.mkdir()
            verify.copy_vendored_dependencies(None, tree)
            verify.copy_vendored_dependencies(source, tree)
            self.assertEqual([], list(tree.iterdir()))
            (source / 'vendor').mkdir()
            (tree / 'vendor').mkdir()
            (source / 'vendor/modules.txt').write_text('candidate')
            (tree / 'vendor/modules.txt').write_text('baseline')
            verify.copy_vendored_dependencies(source, tree)
            self.assertEqual('baseline', (tree / 'vendor/modules.txt').read_text())

    def test_linked_vendor_root_is_rejected_without_copying_external_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            source, tree, outside = (Path(temporary) / name for name in ('source', 'tree', 'outside'))
            for path in (source, tree, outside):
                path.mkdir()
            (outside / 'secret.txt').write_text('private')
            (source / 'vendor').symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                verify.copy_vendored_dependencies(source, tree)
            self.assertEqual([], list(tree.iterdir()))
