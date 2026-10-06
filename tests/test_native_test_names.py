"""Go tests the user's brief names are proven under those names (#498, tools/autocode_native_test_names.py).

A live Go run was asked for TestFixedReturnsTwo, TestFixedPreservesExisting and TestFixedPreservesCrash.
Its draft declared test_ac1_fixed_returns_two and so on and said in prose that each "resolves to" the
requested test; the final Plan Reviewer offered it for approval, and the runner's matcher bound none of
the three. The runner now refuses that draft before it is installed or approved.
"""
import copy
from pathlib import Path
import shutil
import unittest

import autocode_goal_lifecycle as lifecycle
import autocode_native_test_names as native
import autocode_planning as planning
import autocode_regression as regression
import autocode_test_cases as test_cases
import goal_fixtures
from tests.test_verify import Project

NAMES = ["TestFixedReturnsTwo", "TestFixedPreservesExisting", "TestFixedPreservesCrash"]
BRIEF = ("Fix Fixed in product.go to return 2 instead of 0. Add native Go tests TestFixedReturnsTwo, "
         "TestFixedPreservesExisting and TestFixedPreservesCrash in product_test.go.")
GO_SEED = {"go.mod": "module product\n\ngo 1.16\n",
           "product.go": "package product\n\nfunc Fixed() int { return 0 }\n\nfunc Existing() int { return 7 }\n",
           "existing_test.go": 'package product\n\nimport "testing"\n\nfunc TestExistingIsSeven(t *testing.T) '
                               '{ if Existing() != 7 { t.Fatal("existing changed") } }\n'}
# The issue's draft: the identifiers after test:/guard: differ, and prose claims they map.
ALIASED = ["test: test_ac1_fixed_returns_two — resolves to the native Go case TestFixedReturnsTwo",
           "guard: test_ac2_preserves_existing — resolves to TestFixedPreservesExisting",
           "guard: test_ac3_preserves_crash — resolves to TestFixedPreservesCrash"]
CORRECTED = ["test: TestFixedReturnsTwo", "guard: TestFixedPreservesExisting", "guard: TestFixedPreservesCrash"]
CONVENTION = ["test: test_ac1_fixed_returns_two", "guard: test_ac2_preserves_existing",
              "guard: test_ac3_preserves_crash"]
CRITERIA = ["Given Fixed, when it is called, then it returns 2",
            "Given Existing, when it is called, then it still returns 7",
            "Given Fixed, when it is called twice, then neither call panics"]


def plan(methods):
    body = goal_fixtures.body()
    body["acceptance_criteria"] = [{"id": f"AC{n}", "criterion": criterion, "verification_method": method,
                                    "human_review": False}
                                   for n, (criterion, method) in enumerate(zip(CRITERIA, methods), start=1)]
    body["milestones"][0].update(acceptance_criteria=["AC1", "AC2", "AC3"],
                                 affected_paths=["product.go", "product_test.go"])
    return body


def project(files=GO_SEED):
    return Project(files)


class NamedTests(unittest.TestCase):
    def test_the_issue_brief_names_its_three_tests(self):
        self.assertEqual(NAMES, native.named([BRIEF]))

    def test_a_list_after_a_lead_in_counts_and_test_main_does_not(self):
        text = "Add these Go tests:\n\n- `TestParsesEmpty`\n- `TestParsesOne`\n\nKeep TestMain as it is."
        self.assertEqual(["TestParsesEmpty", "TestParsesOne"], native.named([text]))
        self.assertEqual(["TestA"], native.named(["Write func TestA(t *testing.T) and keep TestMain."]))

    def test_a_test_prefixed_word_not_introduced_as_a_test_asks_for_nothing(self):
        self.assertEqual([], native.named(["Deploy the release to TestNet once it builds."]))
        self.assertEqual([], native.named(["TestFlight builds are out of scope."]))


class ProblemsTests(unittest.TestCase):
    """Pure: a body against the requested names, with no workspace or Go toolchain."""

    def test_the_issue_draft_is_refused_criterion_by_criterion(self):
        errors = native.problems(plan(ALIASED), NAMES)
        self.assertEqual(3, len(errors), errors)
        self.assertIn('AC1 declares test_ac1_fixed_returns_two but refers to TestFixedReturnsTwo '
                      '(write "test: TestFixedReturnsTwo")', errors)
        self.assertIn('(write "guard: TestFixedPreservesCrash")', errors[2])

    def test_a_requested_name_no_criterion_declares_is_refused(self):
        self.assertEqual(["no criterion declares " + ", ".join(NAMES)], native.problems(plan(CONVENTION), NAMES))
        dropped = plan(CORRECTED)
        dropped["acceptance_criteria"][2]["verification_method"] = "The Validator runs go test ./..."
        self.assertEqual(["no criterion declares TestFixedPreservesCrash"], native.problems(dropped, NAMES))

    def test_the_corrected_draft_binds_every_requested_test(self):
        self.assertEqual([], native.problems(plan(CORRECTED), NAMES))
        cases = test_cases.plan_cases(plan(CORRECTED))
        ran = ["product::" + name for name in NAMES]
        self.assertEqual({"AC1": [ran[0]], "AC2": [ran[1]], "AC3": [ran[2]]},
                         test_cases.match_cases(cases, ran, framework="go"))

    def test_annotations_subtests_and_the_proofs_own_go_spelling_still_bind(self):
        methods = ["test: TestFixedReturnsTwo — go test -run TestFixedReturnsTwo ./...",
                   "guard: TestFixedPreservesExisting/still_seven (keeps TestFixedReturnsTwo company)",
                   "guard: test_fixed_preserves_crash"]
        self.assertEqual([], native.problems(plan(methods), NAMES))

    def test_criteria_without_a_requested_name_keep_the_default_convention(self):
        body = plan(CORRECTED)
        body["acceptance_criteria"].append({"id": "AC4", "criterion": "Given Fixed, when doubled, then 4",
                                            "verification_method": "test: test_ac4_doubles", "human_review": False})
        self.assertEqual([], native.problems(body, NAMES))
        self.assertEqual([], native.problems(plan(CONVENTION), []))


class DraftValidationTests(unittest.TestCase):
    """Through the runner's own draft and approval check (autocode_goal_lifecycle.validate_body); no Go needed."""

    @classmethod
    def setUpClass(cls):
        cls.go = project()
        cls.python = project({"calc.py": "def add(a, b):\n    return a + b\n",
                              "test_calc.py": "import unittest\n\n\nclass CalcTests(unittest.TestCase):\n"
                                              "    def test_add(self):\n        self.assertTrue(True)\n"})

    @classmethod
    def tearDownClass(cls):
        cls.go.close()
        cls.python.close()

    def state(self, root=None, task=BRIEF, **extra):
        return {"task": task, "workspace": str(root or self.go.root), "settings": {}, "answers": {},
                "user_events": [], "brief_feedback": [], **extra}

    def test_the_issue_draft_cannot_be_installed_or_approved(self):
        state = self.state(task_id="task-1", version=3, status="RUNNING")
        with self.assertRaisesRegex(ValueError, r"does not prove the Go tests the user asked for by name: "
                                                r"AC1 declares test_ac1_fixed_returns_two but refers to "
                                                r"TestFixedReturnsTwo .*binds nothing"):
            lifecycle.install_draft(state, plan(ALIASED), origin="astra_finalize")
        self.assertNotIn("goal_contract", state)
        with self.assertRaisesRegex(ValueError, "AC3 declares test_ac3_preserves_crash"):
            lifecycle.validate_body(self.state(), plan(ALIASED), ready=True)

    def test_the_corrected_draft_is_accepted(self):
        lifecycle.validate_body(self.state(), plan(CORRECTED), ready=True)

    def test_a_brief_without_native_names_keeps_the_lowercase_convention(self):
        task = "Fix Fixed in product.go to return 2 instead of 0, with regression tests."
        lifecycle.validate_body(self.state(task=task), plan(CONVENTION), ready=True)

    def test_a_python_project_is_unaffected(self):
        task = "Add a unittest class TestParser with tests TestParserEmpty and TestParserOne."
        state = self.state(self.python.root, task=task)
        self.assertEqual("unittest", native.proof_framework(state))
        self.assertEqual([], native.requested(state))
        lifecycle.validate_body(state, plan(CONVENTION), ready=True)

    def test_an_existing_test_the_brief_mentions_is_not_a_new_case(self):
        task = "The test TestExistingIsSeven must keep passing; fix Fixed to return 2."
        self.assertEqual([], native.requested(self.state(task=task)))
        lifecycle.validate_body(self.state(task=task), plan(CONVENTION), ready=True)

    def test_the_users_answers_count_and_a_delegated_default_does_not(self):
        task = "Fix Fixed in product.go to return 2 instead of 0."
        answer = {"text": "Name the test TestFixedReturnsTwo.", "kind": "user"}
        self.assertEqual(["TestFixedReturnsTwo"], native.requested(self.state(task=task, answers={"Q1": answer})))
        delegated = {**answer, "kind": "delegated"}
        self.assertEqual([], native.requested(self.state(task=task, answers={"Q1": delegated})))

    def test_a_go_test_command_makes_the_proof_go_without_detection(self):
        state = self.state(self.python.root, settings={"regression": {"test_command": "go test ./..."}})
        self.assertEqual("go", native.proof_framework(state))

    def test_a_design_job_or_a_diagnosis_driven_proof_requests_nothing(self):
        self.assertEqual([], native.requested(self.state(workflow={"kind": "design"})))
        diagnosis = {"outcome": "reproduced", "test_cases": [{"id": "T1", "given": "g", "when": "w", "then": "t"}]}
        self.assertEqual([], native.requested(self.state(investigation=diagnosis)))


class PlannerPromptTests(unittest.TestCase):
    """Drafting and review stages are told the requested names, so the first draft need not be sent back."""

    @classmethod
    def setUpClass(cls):
        cls.go = project()

    @classmethod
    def tearDownClass(cls):
        cls.go.close()

    def prompt(self, stage, task=BRIEF):
        state = {"version": 3, "task_id": "task-1", "task": task, "workspace": str(self.go.root),
                 "settings": {"joint_planning": True, "roles": {"plan_reviewer": {}}},
                 "answers": {}, "user_events": [], "acceptance_criteria": []}
        return planning.context(state, stage, Path("/run/state.json"))[0]

    def test_planning_and_review_stages_name_the_requested_tests(self):
        for stage in ("astra_discovery", "astra_challenge", "glm_revise", "astra_finalize"):
            with self.subTest(stage=stage):
                text = self.prompt(stage)
                rule = text.split("NATIVE TEST NAMES:", 1)[1].split("\n", 1)[0]
                self.assertIn(", ".join(NAMES), rule)
                self.assertIn('"test: TestFixedReturnsTwo"', rule)

    def test_no_rule_without_requested_names_or_before_planning(self):
        self.assertNotIn("NATIVE TEST NAMES", self.prompt("glm_revise", task="Fix Fixed to return 2."))
        self.assertNotIn("NATIVE TEST NAMES", self.prompt("requirements_gather"))

    def test_the_builder_is_told_to_use_a_declared_native_name(self):
        self.assertIn("test: TestFixedReturnsTwo -> func TestFixedReturnsTwo", test_cases.BUILDER_NOTE)


@unittest.skipUnless(shutil.which("go"), "requires real Go compiler")
class GoProofTests(unittest.TestCase):
    """The accepted plan's names bind the real Go outcomes in the runner's regression proof."""

    def test_the_corrected_plan_proves_all_three_requested_tests(self):
        found = project()
        self.addCleanup(found.close)
        body = plan(CORRECTED)
        lifecycle.validate_body({"task": BRIEF, "workspace": str(found.root), "settings": {}, "answers": {},
                                 "user_events": []}, copy.deepcopy(body), ready=True)
        found.write({"product.go": GO_SEED["product.go"].replace("return 0", "return 2"),
                     "product_test.go": 'package product\n\nimport "testing"\n\n'
                     'func TestFixedReturnsTwo(t *testing.T) { if Fixed() != 2 { t.Fatal("not 2") } }\n\n'
                     'func TestFixedPreservesExisting(t *testing.T) { if Existing() != 7 { t.Fatal("changed") } }\n\n'
                     'func TestFixedPreservesCrash(t *testing.T) { Fixed(); Fixed() }\n'})
        state = {"base_commit": found.base, "settings": {}, "iteration": 1, "stages": [], "history": [],
                 "goal_contract": {"body": {"task_kind": "build", "acceptance_criteria": body["acceptance_criteria"],
                                            "milestones": [{"id": "M1"}]}}}
        run = found.root / ".autocode/runs/proof-fixture"
        run.mkdir(parents=True)
        proof = regression.prove(state, found.root, run)
        self.assertEqual("PASS", proof["verdict"], proof)
        self.assertEqual({"AC1": ["product::TestFixedReturnsTwo"], "AC2": ["product::TestFixedPreservesExisting"],
                          "AC3": ["product::TestFixedPreservesCrash"]}, proof["case_tests"])


if __name__ == "__main__":
    unittest.main()
