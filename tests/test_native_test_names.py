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
from unittest import mock

import autocode_goal_lifecycle as lifecycle
import autocode_native_test_names as native
import autocode_planning as planning
import autocode_regression as regression
import autocode_test_cases as test_cases
import autocode_test_examples as test_examples
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

    def test_every_name_of_a_described_list_counts(self):
        # Review of #498: only the first of these was seen, so the other two could still be aliased.
        bullets = ("Fix Fixed in product.go to return 2 instead of 0.\n\n"
                   "Add these native Go tests in product_test.go:\n"
                   "- TestFixedReturnsTwo: Fixed() returns 2 for the default product configuration and never 0.\n"
                   "- TestFixedPreservesExisting: Existing() still returns 7 after the change to Fixed.\n"
                   "- TestFixedPreservesCrash: calling Fixed() twice in a row does not panic.\n")
        self.assertEqual(NAMES, native.named([bullets]))
        numbered = ("Add these tests:\n\n1. `TestFixedReturnsTwo` (Fixed returns 2, never 0, for every\n"
                    "   configuration we ship)\n2. `TestFixedPreservesExisting`\n3) TestFixedPreservesCrash")
        self.assertEqual(NAMES, native.named([numbered]))
        inline = ("Add native Go tests in product_test.go: TestFixedReturnsTwo (returns 2 for the default\n"
                  "configuration), TestFixedPreservesExisting (Existing is still 7), and\n"
                  "TestFixedPreservesCrash (no panic on a second call).")
        self.assertEqual(NAMES, native.named([inline]))
        self.assertEqual(["TestFixedReturnsTwo"],
                         native.named(["I'd like a regression test for Fixed named TestFixedReturnsTwo."]))

    def test_an_earlier_clause_does_not_cancel_a_request(self):
        for text in ["Fix Fixed to return 2 instead of 0 and add the tests TestA and TestB.",
                     "Never edit generated files, and add the tests TestA and TestB.",
                     "Don't change the API. Add tests `TestA` and `TestB`.",
                     "Don't forget the tests TestA and TestB."]:
            with self.subTest(text=text):
                self.assertEqual(["TestA", "TestB"], native.named([text]))

    def test_placeholders_examples_negations_and_other_mentions_ask_for_nothing(self):
        # Review of #498: each of these was required as a test the plan must declare.
        for text in ["Fix Fixed, with a regression test in the usual Go style (func TestXxx(t *testing.T)).",
                     "Go test functions must be named TestXxx; add one for Fixed.",
                     "Do not name the test TestFixed; use the project's default test names.",
                     "Don't add the tests TestA and TestB.",
                     "Add a regression test, e.g. TestFixedReturnsTwo or whatever fits.",
                     "Add a regression test TestFixedReturnsTwo or similar.",
                     "Fix Fixed in product.go to return 2, and make sure the tests pass on TestNet.",
                     "Write the test like TestReadAll in Go's io package.",
                     "Like the test TestReadAll in Go's io package, add a test for our reader.",
                     "Test the build on TestFlight too.",
                     "Add tests. TestCase struct fields must stay exported.",
                     "The test table type TestCase needs a new field.",
                     "go test ./... currently fails in TestIntegration because of a timeout",
                     "Add a test for TestHelper misuse.",
                     "Fix Fixed. For example, the test TestA fails today.",
                     "Add tests TestA, TestB, or TestC."]:
            with self.subTest(text=text):
                self.assertEqual([], native.named([text]))

    def test_a_later_user_message_can_withdraw_a_name(self):
        retract = "Changed my mind: use the default test_<id> naming instead of TestFixedReturnsTwo."
        self.assertEqual(NAMES[1:], native.named([BRIEF, retract]))
        self.assertEqual(NAMES, native.named([BRIEF, retract, "Please keep the test TestFixedReturnsTwo after all."]))
        self.assertEqual(NAMES, native.named([BRIEF, "Do not rename the test TestFixedReturnsTwo."]))


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

    def test_annotations_and_subtests_bind(self):
        methods = ["test: TestFixedReturnsTwo — go test -run TestFixedReturnsTwo ./...",
                   "guard: TestFixedPreservesExisting/still_seven (keeps TestFixedReturnsTwo company)",
                   "guard: TestFixedPreservesCrash"]
        self.assertEqual([], native.problems(plan(methods), NAMES))

    def test_a_respelling_of_a_requested_name_is_refused(self):
        # Review of #498: the Go matcher binds test_fixed_returns_two to Test_fixed_returns_two and other
        # variants, so the proof passed although none of the requested identifiers existed.
        snake = ["test: test_fixed_returns_two", "guard: test_fixed_preserves_existing",
                 "guard: test_fixed_preserves_crash"]
        errors = native.problems(plan(snake), NAMES)
        self.assertEqual(3, len(errors), errors)
        self.assertIn('AC1 declares test_fixed_returns_two, a respelling of TestFixedReturnsTwo; keep the '
                      'requested spelling (write "test: TestFixedReturnsTwo")', errors)
        self.assertEqual(["no criterion declares TestFixedPreservesCrash"],
                         native.problems(plan(CORRECTED[:2] + ["guard: TestFixed_Preserves_Crash"]), NAMES))

    def test_criteria_without_a_requested_name_keep_the_default_convention(self):
        body = plan(CORRECTED)
        body["acceptance_criteria"].append({"id": "AC4", "criterion": "Given Fixed, when doubled, then 4",
                                            "verification_method": "test: test_ac4_doubles", "human_review": False})
        self.assertEqual([], native.problems(body, NAMES))
        self.assertEqual([], native.problems(plan(CONVENTION), []))

    def test_mentioning_a_name_another_criterion_declares_is_not_an_alias(self):
        # Review of #498: AC4 was refused, and the suggested fix made one test prove two criteria.
        body = plan(CORRECTED)
        body["acceptance_criteria"].append({
            "id": "AC4", "criterion": "Given Fixed, when doubled, then 4", "human_review": False,
            "verification_method": "test: test_ac4_doubles — written next to TestFixedReturnsTwo in product_test.go"})
        self.assertEqual([], native.problems(body, NAMES))
        body["acceptance_criteria"][3]["verification_method"] = "test: TestFixedReturnsTwo"
        self.assertEqual(["AC1 and AC4 both declare TestFixedReturnsTwo; each criterion needs its own test"],
                         native.problems(body, NAMES))

    def test_a_requested_test_the_validator_checks_is_accounted_for(self):
        # Review of #498: a test that skips without a database can never pass the runner's proof.
        names = ["TestPostgresRoundTrip"]
        validator = plan(["The Validator reads TestPostgresRoundTrip and runs go vet ./..."] + CONVENTION[1:])
        self.assertEqual([], native.problems(validator, names))
        # The Validator route is not a way to map another identifier onto the requested one.
        mapped = plan(["The Validator checks that test_ac1_round_trip resolves to TestPostgresRoundTrip"]
                      + CONVENTION[1:])
        self.assertEqual(["no criterion declares TestPostgresRoundTrip"], native.problems(mapped, names))
        aliased = plan(["test: test_ac1_round_trip — resolves to TestPostgresRoundTrip"] + CONVENTION[1:])
        self.assertEqual(['AC1 declares test_ac1_round_trip but refers to TestPostgresRoundTrip '
                          '(write "test: TestPostgresRoundTrip")'], native.problems(aliased, names))


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

    def test_a_described_list_cannot_alias_its_later_names(self):
        task = ("Fix Fixed in product.go to return 2 instead of 0.\n\nAdd these native Go tests in product_test.go:\n"
                "- TestFixedReturnsTwo: Fixed() returns 2 for the default product configuration and never 0.\n"
                "- TestFixedPreservesExisting: Existing() still returns 7 after the change to Fixed.\n"
                "- TestFixedPreservesCrash: calling Fixed() twice in a row does not panic.\n")
        partly = plan(["test: TestFixedReturnsTwo"] + ALIASED[1:])
        with self.assertRaisesRegex(ValueError, "AC2 declares test_ac2_preserves_existing but refers to "
                                                "TestFixedPreservesExisting"):
            lifecycle.validate_body(self.state(task=task), partly, ready=True)
        lifecycle.validate_body(self.state(task=task), plan(CORRECTED), ready=True)

    def test_briefs_that_name_no_new_test_keep_master_behavior(self):
        for task in ["Fix Fixed in product.go to return 2 instead of 0, with a regression test in the usual Go "
                     "style (func TestXxx(t *testing.T)).",
                     "Fix Fixed in product.go to return 2. Do not name the test TestFixed; use the project's "
                     "default test names.",
                     "Fix Fixed in product.go to return 2. Add a regression test, e.g. TestFixedReturnsTwo or "
                     "whatever fits.",
                     "Fix Fixed in product.go to return 2, and make sure the tests pass on TestNet.",
                     "Fix Fixed in product.go to return 2. Write the test like TestReadAll in Go's io package."]:
            with self.subTest(task=task):
                lifecycle.validate_body(self.state(task=task), plan(CONVENTION), ready=True)

    def test_the_users_own_edit_settles_which_requested_names_stay(self):
        # Review of #498: the user could not withdraw a name; their own --edit-goal was refused.
        feedback = {"id": "F1", "kind": "brief_feedback",
                    "text": "Changed my mind: use the default test_<id> naming for all three."}
        state = self.state(task_id="task-1", version=3, status="RUNNING", user_events=[feedback],
                           brief_feedback=[feedback])
        lifecycle.validate_body(state, plan(CONVENTION), ready=True, origin="user_cli_edit")
        lifecycle.install_draft(state, plan(CONVENTION), origin="user_cli_edit")
        self.assertEqual("user_cli_edit", state["goal_contract"]["origin"])
        # The planners' later drafts keep what the user's edit kept; names it dropped are no longer requested.
        self.assertEqual([], native.requested(state))
        lifecycle.validate_body(state, plan(CONVENTION), ready=True, origin="astra_finalize")
        kept = self.state(goal_contract={"origin": "user_cli_edit", "body": plan(CORRECTED[:1] + CONVENTION[1:])})
        self.assertEqual(["TestFixedReturnsTwo"], native.requested(kept))

    def presented(self, body, origin):
        state = self.state(task_id="task-1", version=3, status="RUNNING")
        lifecycle.migrate(state)
        lifecycle.install_draft(state, body, origin=origin)
        lifecycle.human.evaluate(state)
        lifecycle.present(state)
        return state

    def test_approval_takes_the_users_own_edit_and_refuses_a_saved_alias(self):
        edited = self.presented(plan(CONVENTION), "user_cli_edit")
        lifecycle.approve(edited, edited["displayed_goal"])
        self.assertEqual("approved", edited["goal_contract"]["approval_status"])
        with mock.patch.object(native, "requested", return_value=[]):  # a draft saved before this check
            saved = self.presented(plan(ALIASED), "astra_finalize")
        with self.assertRaisesRegex(ValueError, "AC1 declares test_ac1_fixed_returns_two"):
            lifecycle.approve(saved, saved["displayed_goal"])

    def test_a_skipped_integration_test_can_be_left_to_the_validator(self):
        task = ("Add Store.Save to product.go. Add an integration test TestPostgresRoundTrip that is skipped "
                "unless DATABASE_URL is set; CI has no database.")
        self.assertEqual(["TestPostgresRoundTrip"], native.requested(self.state(task=task)))
        body = plan(["The Validator reads TestPostgresRoundTrip and runs go vet ./..."] + CONVENTION[1:])
        lifecycle.validate_body(self.state(task=task), body, ready=True)

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

    def test_the_builders_test_style_section_does_not_contradict_a_declared_name(self):
        # Review of #498: the same Builder prompt also said "still name each test after its case id".
        state = {"workspace": str(self.go.root), "goal_contract": {"body": plan(CORRECTED)}, "settings": {}}
        self.assertIn("test: TestFixedReturnsTwo -> func TestFixedReturnsTwo", test_cases.builder_note(state))
        style = test_examples.section(self.go.root, {"affected_paths": ["product.go", "product_test.go"]})
        self.assertIn("EXISTING TEST STYLE", style)
        self.assertIn("still name each test after its case id (test_<id>_...), unless the plan declared its test "
                      "name right after test: or guard: (test: TestFixedReturnsTwo): then use that name exactly",
                      " ".join(style.split()))


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
