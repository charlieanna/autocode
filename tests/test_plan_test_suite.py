"""A plan whose tests the regression proof would never run is refused when it is drafted (#2889).

In a polyglot repository (a Node suite in the root package.json, a Rails-style backend/ with RSpec specs) a live
plan named every proving test as an RSpec example under backend/spec/. The suite the proof used could not run
them, and that surfaced only after a Builder and a Tester round. tools/autocode_plan_test_suite.py
"""

from __future__ import annotations

import sys
import unittest

import autocode_goal_lifecycle as lifecycle
import autocode_native_test_names as native_test_names
import autocode_plan_test_suite as plan_test_suite
import autocode_verify as verify
import goal_fixtures
from autocode_test_framework import Framework

from tests.test_verify import Project

POLYGLOT = {
    "package.json": '{"name": "site", "scripts": {"test": "node --test frontend/scripts/routing.test.mjs"}}\n',
    "frontend/scripts/routing.mjs": "export const route = () => '/staging';\n",
    "frontend/scripts/routing.test.mjs": "import test from 'node:test';\ntest('routes', () => {});\n",
    "backend/Gemfile": "source 'https://rubygems.org'\ngem 'rspec'\n",
    "backend/app/grader.rb": "class Grader\nend\n",
    "backend/spec/requests/existing_spec.rb": "RSpec.describe 'existing' do\n  it('runs') { }\nend\n",
}
SPEC = "backend/spec/requests/grader_spec.rb"
NODE_TEST = "frontend/scripts/routing.test.mjs"


def plan(*paths):
    body = goal_fixtures.body()
    body["open_blocking_questions"] = []
    body["milestones"][0]["affected_paths"] = ["backend/app/grader.rb", *paths]
    return body


class KindTests(unittest.TestCase):
    def test_a_test_file_is_judged_by_its_name(self):
        for path, kind in (
            ("tests/test_grader.py", "python"),
            ("pkg/grader_test.py", "python"),
            ("pkg/grader_test.go", "go"),
            (SPEC, "ruby"),
            ("web/src/a.test.tsx", "javascript"),
            ("web/test/a.spec.cjs", "javascript"),
            ("tests/conftest.py", None),
            ("backend/spec/spec_helper.rb", None),
            ("test/fixtures/data.json", None),
            ("backend/app/grader.rb", None),
        ):
            with self.subTest(path=path):
                self.assertEqual(kind, plan_test_suite.kind(path))

    def test_the_plan_names_its_tests_in_milestones_and_the_first_task(self):
        body = plan(f"./{SPEC}", "backend/spec/spec_helper.rb", "/abs/x_test.go", "../up/a_spec.rb", "")
        body["initial_task"] = {"affected_paths": [NODE_TEST, "backend/spec/"]}
        self.assertEqual([SPEC, NODE_TEST], plan_test_suite.planned_tests(body))


class CheckTests(unittest.TestCase):
    def repository(self, files=POLYGLOT):
        project = Project(files)
        self.addCleanup(project.close)
        return project

    def state(self, project, **settings):
        return {
            "task": "Fix the grader",
            "workspace": str(project.root),
            "settings": settings,
            "answers": {},
            "user_events": [],
            "brief_feedback": [],
        }

    def test_specs_inside_the_project_with_their_own_suite_are_run_by_it(self):
        project = self.repository()
        lifecycle.validate_body(self.state(project), plan(SPEC), ready=True)

    def test_specs_no_suite_can_run_are_refused_at_install_and_at_approval(self):
        project = self.repository({path: text for path, text in POLYGLOT.items() if path != "backend/Gemfile"})
        state = {**self.state(project), "task_id": "task-1", "version": 3, "status": "RUNNING"}
        refusal = (
            r"The regression proof would never run the plan's tests backend/spec/requests/grader_spec\.rb: it runs "
            r"the plan's tests with one suite, here the node suite `npm test --silent`, which runs JavaScript or "
            r"TypeScript tests, and no project directory around all of the plan's tests has a suite"
        )
        with self.assertRaisesRegex(ValueError, refusal):
            lifecycle.install_draft(state, plan(SPEC), origin="finalize_plan")
        self.assertNotIn("goal_contract", state)
        with self.assertRaisesRegex(ValueError, refusal):
            lifecycle.validate_body(self.state(project), plan(SPEC), ready=True)

    def test_specs_beside_tests_the_root_suite_runs_are_refused(self):
        # The root's suite runs the Node test, so the proof keeps it, and it never runs the spec.
        project = self.repository()
        with self.assertRaisesRegex(ValueError, r"never run the plan's tests backend/spec/requests/grader_spec\.rb:"):
            lifecycle.validate_body(self.state(project), plan(NODE_TEST, SPEC), ready=True)
        lifecycle.validate_body(self.state(project), plan(NODE_TEST), ready=True)

    def test_helpers_and_fixtures_are_not_judged(self):
        project = self.repository({path: text for path, text in POLYGLOT.items() if path != "backend/Gemfile"})
        lifecycle.validate_body(self.state(project), plan("backend/spec/spec_helper.rb"), ready=True)

    def test_the_persons_commands_scope_or_edit_and_a_design_job_are_not_judged(self):
        project = self.repository({path: text for path, text in POLYGLOT.items() if path != "backend/Gemfile"})
        body = plan(SPEC)
        for regression in ({"test_command": "cd backend && rspec"}, {"regression_command": "rspec"}):
            with self.subTest(regression=regression):
                plan_test_suite.check(self.state(project, regression=regression), body)
        plan_test_suite.check(self.state(project), body, origin=native_test_names.USER_EDIT)
        plan_test_suite.check({**self.state(project), "workflow": {"kind": "design"}}, body)
        plan_test_suite.check({**self.state(project), "workspace": None}, body)

    def test_a_runner_of_no_known_kind_may_run_any_test(self):
        found = type("Framework", (), {"name": "make", "suite": "make test"})()
        self.assertEqual([], plan_test_suite.problems(None, found, [SPEC], lambda directory: None))
        self.assertEqual([], plan_test_suite.problems(None, None, [SPEC], lambda directory: None))

    def test_a_package_run_does_not_look_around_the_tests(self):
        project = self.repository()
        node = type("Framework", (), {"name": "node", "suite": "npm test"})()
        detect = lambda directory: None  # noqa: E731
        self.assertEqual([SPEC], plan_test_suite.problems(project.root, node, [SPEC], detect, scoped=True))


if __name__ == "__main__":
    unittest.main()


MINITEST = {
    "lib/pager.rb": "def page_count(total, size) = total / size\n",
    "test/pager_test.rb": "require 'minitest/autorun'\nclass PagerTest < Minitest::Test\n  def test_exact\n  end\nend\n",
}


def named(method, *paths):
    body = plan(*paths)
    body["acceptance_criteria"][0]["verification_method"] = method
    return body


class NamedCasesTests(unittest.TestCase):
    """A test: or guard: criterion on a runner that reports only an exit code is refused when it is drafted.

    A live Ruby Minitest bug fix (2026-11-13) planned test: criteria, built and tested them, and then ended
    with the proof unable to match any test to them; the Resolver asked the person how to prove it.
    """

    def state(self, files):
        project = Project(files)
        self.addCleanup(project.close)
        return CheckTests.state(self, project)

    def test_a_named_criterion_on_an_exit_code_runner_goes_back_to_the_planner(self):
        refusal = (
            r"Criteria C1 name a test after test: or guard:, but this project's tests run with the minitest suite "
            r"`ruby -Ilib -Itest -e .*`, which reports only whether the whole run passed.*Write each of them without "
            r"test: or guard:, as the command that runs its test file"
        )
        for method in ("test: test_partial_page_counted", "guard: test_exact"):
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, refusal):
                lifecycle.validate_body(self.state(MINITEST), named(method, "test/pager_test.rb"), ready=True)

    def test_test_code_no_runner_reads_is_judged_as_an_exit_code_runner(self):
        shell = {"pager.sh": "page_count() { echo 1; }\n", "tests/test_pager.sh": ". ./pager.sh\n"}
        with self.assertRaisesRegex(ValueError, r"this project's tests run with no runner AutoCode reads, which"):
            lifecycle.validate_body(self.state(shell), named("test: test_partial", "tests/test_pager.sh"), ready=True)

    def test_the_same_criterion_as_the_command_that_runs_its_test_file_is_accepted(self):
        method = "`ruby test/pager_test.rb` fails on the original code and passes with the fix"
        lifecycle.validate_body(self.state(MINITEST), named(method, "test/pager_test.rb"), ready=True)

    def test_a_runner_with_per_test_results_or_none_detected_keeps_named_criteria(self):
        python = {"pager.py": "def page_count(total, size):\n    return total // size\n", "tests/test_pager.py": ""}
        lifecycle.validate_body(self.state(python), named("test: test_partial", "tests/test_pager.py"), ready=True)
        # A new project has no runner yet; the proof detects its suite again on the changed code.
        lifecycle.validate_body(self.state({"tests/fixtures/x.json": "{}"}), named("test: test_partial"), ready=True)
        lifecycle.validate_body(self.state({"README.md": "new\n"}), named("test: test_partial"), ready=True)

    def test_a_suite_around_the_planned_tests_with_per_test_results_proves_them(self):
        # A root `make test` beside a backend/ with its own RSpec suite: the proof runs the specs there.
        project = Project({**POLYGLOT, "package.json": "{}\n", "Makefile": "test:\n\ttrue\n"})
        self.addCleanup(project.close)
        make = Framework("make", "make test")
        detect = lambda directory: verify.detect_framework(directory, python=sys.executable)  # noqa: E731
        body = named("test: test_partial", SPEC)
        self.assertIsNone(plan_test_suite.unnamed_cases(str(project.root), make, [SPEC], body, detect))
        self.assertIn(
            "make suite `make test`", plan_test_suite.unnamed_cases(str(project.root), make, [], body, detect)
        )


SCRIPT = {"hello.py": 'print("Hello, world!")\n', "README.md": "# hello\n"}
SHELL_CHECK = 'test "$(python3 hello.py --shout)" = "HELLO, WORLD!"'


def paths(method, *affected, first_task=()):
    body = named(method)
    body["milestones"][0]["affected_paths"] = list(affected)
    if first_task:
        body["initial_task"] = {"affected_paths": list(first_task)}
    return body


class UntestedCodeTests(unittest.TestCase):
    """A plan that changes code in a project with no tests, and plans no test, goes back to the Planner.

    A live run asked to add an option to a script (2026-11-13) planned the script alone with shell commands
    as its criteria. The proof failed twice with "No regression test was added or changed", the Resolver
    asked the person to allow a test file, and the run finished after 20 stages.
    """

    refusal = (
        r"This plan changes code \(hello\.py\) and plans no test, and the project has no tests yet\. Every change "
        r"to code needs a test that fails on the original code and passes with the change.*name a new test file "
        r"in the affected_paths of the milestone that changes the code"
    )

    def state(self, files=SCRIPT, **more):
        project = Project(files)
        self.addCleanup(project.close)
        return {**CheckTests.state(self, project), **more}

    def test_code_alone_in_a_project_with_no_tests_is_refused_when_drafted(self):
        for body in (paths(SHELL_CHECK, "hello.py"), paths(SHELL_CHECK, "./hello.py", "README.md")):
            with self.subTest(paths=body["milestones"][0]["affected_paths"]):
                with self.assertRaisesRegex(ValueError, self.refusal):
                    lifecycle.validate_body(self.state(), body, ready=True)
        for kind in ("build", "bugfix"):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, self.refusal):
                plan_test_suite.check(self.state(workflow={"kind": kind}), paths(SHELL_CHECK, "hello.py"))

    def test_a_planned_test_file_or_test_directory_is_enough(self):
        for body in (
            paths(SHELL_CHECK, "hello.py", "tests/test_hello.py"),
            paths(SHELL_CHECK, "hello.py", "test_hello.py"),
            paths(SHELL_CHECK, "hello.py", "tests/"),
            paths(SHELL_CHECK, "hello.py", first_task=["tests/test_hello.py"]),
        ):
            with self.subTest(paths=body["milestones"][0]["affected_paths"]):
                plan_test_suite.check(self.state(), body)

    def test_a_named_criterion_says_a_test_is_planned(self):
        lifecycle.validate_body(self.state(), paths("test: test_ac1_shout", "hello.py"), ready=True)

    def test_a_change_to_no_code_owes_no_test(self):
        site = {"index.html": "<h1>Pager</h1>\n", "styles.css": ".primary { color: #888; }\n"}
        lifecycle.validate_body(self.state(site), paths("`grep -q 0a66c2 styles.css`", "styles.css"), ready=True)
        lifecycle.validate_body(self.state(), paths("`grep -q Usage README.md`", "README.md"), ready=True)
        lifecycle.validate_body(self.state(), paths("`grep -q Usage README.md`"), ready=True)

    def test_a_project_that_has_tests_is_not_judged(self):
        # Its tests may guard the change, or already hold the failing test (first_suite.existing_repair).
        tested = {**SCRIPT, "tests/test_hello.py": "import unittest\n"}
        lifecycle.validate_body(self.state(tested), paths("`python3 -m unittest`", "hello.py"), ready=True)
        self.assertIsNone(plan_test_suite.untested_code(self.state(tested)["workspace"], paths("x", "hello.py")))

    def test_a_review_a_design_and_the_persons_own_commands_are_not_judged(self):
        body = paths(SHELL_CHECK, "hello.py")
        for kind in ("review", "discuss", "design"):
            with self.subTest(kind=kind):
                plan_test_suite.check(self.state(workflow={"kind": kind}), body)
        plan_test_suite.check(self.state(settings={"regression": {"test_command": "python3 hello.py"}}), body)
        plan_test_suite.check(self.state(), body, origin=native_test_names.USER_EDIT)
