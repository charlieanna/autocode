"""A plan whose tests the regression proof would never run is refused when it is drafted (#2889).

In a polyglot repository (a Node suite in the root package.json, a Rails-style backend/ with RSpec specs) a live
plan named every proving test as an RSpec example under backend/spec/. The suite the proof used could not run
them, and that surfaced only after a Builder and a Tester round. tools/autocode_plan_test_suite.py
"""

from __future__ import annotations

import unittest

import autocode_goal_lifecycle as lifecycle
import autocode_native_test_names as native_test_names
import autocode_plan_test_suite as plan_test_suite
import goal_fixtures

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
