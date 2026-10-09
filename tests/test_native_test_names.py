"""Go tests the user's brief names are proven under those names (#498, tools/autocode_native_test_names.py).

A live Go run was asked for TestFixedReturnsTwo, TestFixedPreservesExisting and TestFixedPreservesCrash.
Its draft declared test_ac1_fixed_returns_two and so on and said in prose that each "resolves to" the
requested test; the final Plan Reviewer offered it for approval, and the runner's matcher bound none of
the three. The runner now refuses that draft before it is installed or approved.
"""
import copy
import shutil
import unittest
from pathlib import Path
from unittest import mock

import autocode_follow_up as follow_up
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
# Verbatim completed #498 case brief, including its final newline.
ORIGINAL_BRIEF = (
    "Fix Fixed() in product.go to return 2 instead of 0. Preserve Existing()==1 and Crash()==false. "
    "Only product.go and a new fixed_test.go may change. Preserve product_test.go, go.mod, README.md "
    "and .gitignore byte-for-byte. Add TestFixedReturnsTwo, a real regression that fails on original "
    "Fixed()==0 and passes after the fix, plus TestFixedPreservesExisting and TestFixedPreservesCrash "
    "guarding the unchanged public functions. Use those exact native Go function names in the plan "
    "and tests. Full suite: go test -json . ; targeted regression: go test -json -run ^TestFixed . . "
    "No dependencies, network, generated files, human acceptance criteria or external services are "
    "needed. The existing parallel diagnostics must remain untouched and must not execute their "
    "abrupt-exit path while Crash()==false.\n"
)
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

    def test_name_first_regression_requests_include_the_original_literal(self):
        for text, names in [(ORIGINAL_BRIEF, NAMES),
                            ("Add TestA, a real regression that fails before the fix and passes after it, plus "
                             "TestB and TestC guarding existing behavior.", ["TestA", "TestB", "TestC"]),
                            ("Add TestA, a regression test for empty input, plus TestB and TestC.",
                             ["TestA", "TestB", "TestC"]),
                            ("Add TestA, a regression that rejects HTTP or another protocol.", ["TestA"])]:
            with self.subTest(text=text):
                self.assertEqual(names, native.named([text]))

    def test_name_first_negations_examples_alternatives_and_helpers_ask_for_nothing(self):
        for text in ["Do not add TestA, a real regression, plus TestB and TestC.",
                     "For example, add TestA, a real regression, plus TestB and TestC.",
                     "Add TestA, a regression test, or similar.",
                     "Add TestA, a regression test, plus TestB or TestC.",
                     "Add TestCase struct fields to the configuration.",
                     "Add TestConnection, a callable production helper for connection setup."]:
            with self.subTest(text=text):
                self.assertEqual([], native.named([text]))

    def test_a_list_after_a_lead_in_counts_and_test_main_does_not(self):
        text = "Add these Go tests:\n\n- `TestParsesEmpty`\n- `TestParsesOne`\n\nKeep TestMain as it is."
        self.assertEqual(["TestParsesEmpty", "TestParsesOne"], native.named([text]))
        self.assertEqual(["TestA"], native.named(["Write func TestA(t *testing.T) and keep TestMain."]))
        self.assertEqual(["TestA"], native.named(["Write func TestA(t *testing.T) () and keep TestMain."]))

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

    def test_every_name_of_an_inline_described_list_counts(self):
        # Review of #498 (described lists): the same list written inline, each name followed by what it
        # checks, still lost every name after the first.
        for text in ["Add native Go tests: TestFixedReturnsTwo checks that Fixed returns 2, TestFixedPreservesExisting "
                     "checks Existing, and TestFixedPreservesCrash checks that a second call does not panic.",
                     "Add the Go tests TestFixedReturnsTwo for the fix and TestFixedPreservesExisting for Existing; "
                     "TestFixedPreservesCrash covers a second call.",
                     "Add tests TestFixedReturnsTwo (returns 2); TestFixedPreservesExisting (still 7); "
                     "TestFixedPreservesCrash (no panic)."]:
            with self.subTest(text=text):
                self.assertEqual(NAMES, native.named([text]))
        # A description ends the list where its first comma or semicolon is not followed by another name.
        for text in ["Add the test TestA, which must not break TestB and TestC.",
                     "Add the test TestA for Fixed, similar to TestExisting.",
                     "Add the test TestA that checks X or Y.",
                     "Add the test TestA for Fixed, but not TestB.",
                     "Add the test TestA for the parser and make sure TestServer, TestClient still pass."]:
            with self.subTest(text=text):
                self.assertEqual(["TestA"], native.named([text]))

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

    def test_a_func_named_or_called_like_a_test_is_production_code(self):
        # #651 B: "a func named TestConnection" is a test to write only with test context in its sentence; a
        # production function whose name starts with Test is not.
        for text in ["Add a func named TestConnection that returns an error.",
                     "Add a func called TestConnection to product.go.",
                     "Add a func named TestConnection.",
                     # Review of #651: test context elsewhere in the sentence does not outweigh a return value.
                     "To test retries, add a func named TestConnection that returns an error.",
                     "For the tests, add a func called TestServer that returns a fake server.",
                     "For the integration tests, add a func called TestConnection in product.go."]:
            with self.subTest(text=text):
                self.assertEqual([], native.named([text]))
        self.assertEqual(["TestConnectionFails"],
                         native.named(["Add a func named TestConnection; add the test TestConnectionFails."]))
        # Review of #651: the fix for B dropped these, which name a test by its file, a later "test" or a lead-in.
        for text, names in [("Add the test func named TestA.", ["TestA"]),
                            ("Add a func named TestParse in parser_test.go.", ["TestParse"]),
                            ("In parser_test.go, add a func named TestParseEmpty.", ["TestParseEmpty"]),
                            ("Add a func called TestRetry, a regression test for the retry bug.", ["TestRetry"]),
                            ("Write a func called TestParseEmpty for the test suite.", ["TestParseEmpty"]),
                            ("Write a func named TestA that tests the parser.", ["TestA"]),
                            ("Add a Go test: a func named TestA.", ["TestA"]),
                            ("Add these Go tests:\n- a func named TestA that checks X\n- a func named TestB that checks Y",
                             ["TestA", "TestB"])]:
            with self.subTest(text=text):
                self.assertEqual(names, native.named([text]))

    def test_a_subtest_path_is_requested_as_the_user_wrote_it(self):
        # #651 A: a subtest path asked for nothing and ended its list, so the names after it were lost too.
        self.assertEqual(["TestCacheExpiry/expired"], native.named(["Add the Go test TestCacheExpiry/expired."]))
        self.assertEqual(["TestCacheExpiry/expired", "TestCacheRetainsFresh"],
                         native.named(["Add the Go tests TestCacheExpiry/expired and TestCacheRetainsFresh."]))
        self.assertEqual(["TestA/empty", "TestB"], native.named(["Add these tests:\n- TestA/empty: no input\n- TestB"]))
        # Leaving out one subtest withdraws neither its function nor the other subtests.
        self.assertEqual(["TestA/empty", "TestA/one"],
                         native.named(["Add the tests TestA/empty and TestA/one, but not TestA/two."]))
        # Go runs a quoted subtest name's spaces as underscores; TestMain is never a test, nor its paths.
        self.assertEqual(["TestCacheExpiry/expired_value"],
                         native.named(['Add the Go test "TestCacheExpiry/expired value".']))
        self.assertEqual([], native.named(["Add tests TestMain/x."]))
        # Review of #651: a file path in a test's description is not a name, so it does not end the list.
        self.assertEqual(["TestA", "TestB"],
                         native.named(["Add the Go tests TestA checks parsing of TestData/golden.json, TestB checks Y."]))

    def test_a_later_user_message_can_withdraw_a_name(self):
        retract = "Changed my mind: use the default test_<id> naming instead of TestFixedReturnsTwo."
        self.assertEqual(NAMES[1:], native.named([BRIEF, retract]))
        self.assertEqual(NAMES, native.named([BRIEF, retract, "Please keep the test TestFixedReturnsTwo after all."]))
        self.assertEqual(NAMES, native.named([BRIEF, "Do not rename the test TestFixedReturnsTwo."]))

    def test_a_later_message_withdraws_a_subtest_path_or_its_whole_function(self):
        # #651: "instead of" the full subtest path withdrew nothing; only the function name withdrew it.
        ask = "Add the Go tests TestCacheExpiry/expired, TestCacheExpiry/fresh and TestCacheRetainsFresh."
        path = "Changed my mind: use the default test_<id> naming instead of TestCacheExpiry/expired."
        self.assertEqual(["TestCacheExpiry/fresh", "TestCacheRetainsFresh"], native.named([ask, path]))
        self.assertEqual(["TestCacheRetainsFresh"],
                         native.named([ask, "Use the default test_<id> naming rather than TestCacheExpiry."]))
        self.assertEqual(["TestCacheExpiry/expired", "TestCacheExpiry/fresh", "TestCacheRetainsFresh"],
                         native.named([ask, path, "Please keep the test TestCacheExpiry/expired after all."]))
        # Review of #651: narrowing a request to one subtest withdraws the function, not the path asked for.
        narrowed = "Add the Go test TestCacheExpiry/expired instead of TestCacheExpiry."
        self.assertEqual(["TestCacheExpiry/expired"], native.named([narrowed]))
        self.assertEqual(["TestCacheExpiry/expired"], native.named(["Add the Go test TestCacheExpiry.", narrowed]))


class ExistingGoTests(unittest.TestCase):
    """Only lexical native test declarations exempt a requested name; no Go compiler is needed."""

    def inventory(self, files):
        found = project({"go.mod": GO_SEED["go.mod"], "product.go": "package product\n", **files})
        self.addCleanup(found.close)
        return native.go_identifiers(found.root)

    def test_comments_strings_and_references_are_not_existing_tests(self):
        for content in ["// func TestRequested(t *testing.T) {}\n",
                        "/* func TestRequested(t *testing.T) {} */\n",
                        'const example = "func TestRequested(t *testing.T) {}"\n',
                        'const example = `func TestRequested(t *testing.T) {}`\n',
                        "const TestRequested = 1\nvar _ = TestRequested\n"]:
            with self.subTest(content=content):
                self.assertEqual(set(), self.inventory({"mentions_test.go": "package product\n" + content}))

    def test_only_top_level_native_test_signatures_in_test_files_count(self):
        forms = [("product.go", "func TestConnection() error { return nil }\n"),
                 ("product.go", "func TestRequested(t *testing.T) {}\n")]
        forms += [("helpers_test.go", declaration) for declaration in [
            "func TestRequested() {}\n", "func TestRequested(t testing.T) {}\n",
            "func TestRequested(t *testing.T) error { return nil }\n",
            "func TestRequested(t *testing.T, extra int) {}\n", "func TestRequested(t *testing.B) {}\n",
            "func (suite *Suite) TestRequested(t *testing.T) {}\n"]]
        for path, declaration in forms:
            with self.subTest(path=path, declaration=declaration):
                source = 'package product\nimport "testing"\ntype Suite struct{}\n' + declaration
                self.assertEqual(set(), self.inventory({path: source}))

    def test_real_declarations_survive_comments_strings_and_unnamed_parameters(self):
        for declaration in ["func TestRequested(t *testing.T) {}\n",
                            "func /* name */ TestRequested(\n"
                            "    t /* argument */ * /* pointer */ testing /* package */ . T,\n) {}\n",
                            "func TestRequested(*testing.T) {}\n"]:
            with self.subTest(declaration=declaration):
                source = ('package product\nimport "testing"\n'
                          'const quoted = "/* func TestQuoted(t *testing.T) {} //"\n'
                          'const raw = `func TestRaw(t *testing.T) {} */`\n' + declaration)
                self.assertEqual({"TestRequested"}, self.inventory({"real_test.go": source}))


class ProblemsTests(unittest.TestCase):
    """Pure: a body against the requested names, with no workspace or Go toolchain."""

    def test_the_issue_draft_is_refused_criterion_by_criterion(self):
        errors = native.problems(plan(ALIASED), NAMES)
        self.assertEqual(3, len(errors), errors)
        self.assertIn('AC1 declares test_ac1_fixed_returns_two but refers to TestFixedReturnsTwo '
                      '(write "test: TestFixedReturnsTwo")', errors)
        self.assertIn('(write "guard: TestFixedPreservesCrash")', errors[2])

    def test_a_declaration_the_runner_cannot_read_is_refused_with_the_form_it_reads(self):
        # The matcher reads no name from "TestFixedReturnsTwo." or "TestA: what it checks", so the case would
        # be proven under its criterion id; the refusal says what follows a name.
        errors = native.problems(plan(["test: TestFixedReturnsTwo.", "guard: TestFixedPreservesExisting: still 7",
                                       "guard: TestFixedPreservesCrash"]), NAMES)
        self.assertEqual(2, len(errors), errors)
        self.assertIn('AC1 declares no test name the runner reads (a name stands alone after the mark, or is '
                      'followed by " — " or a parenthesis) but refers to TestFixedReturnsTwo (write '
                      '"test: TestFixedReturnsTwo")', errors)

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

    def test_a_requested_subtest_path_is_named_in_the_refusal_and_accounted_for_by_its_function(self):
        # #651: the refusal said write "test: TestCacheExpiry" while the planning rule asks for the path given.
        names = ["TestCacheExpiry/expired"]
        aliased = plan(["test: test_ac1_cache_expiry — resolves to TestCacheExpiry/expired"] + CONVENTION[1:])
        self.assertEqual(['AC1 declares test_ac1_cache_expiry but refers to TestCacheExpiry/expired '
                          '(write "test: TestCacheExpiry/expired")'], native.problems(aliased, names))
        self.assertEqual(["no criterion declares TestCacheExpiry/expired"], native.problems(plan(CONVENTION), names))
        for declared in ("test: TestCacheExpiry/expired", "test: TestCacheExpiry"):
            with self.subTest(declared=declared):
                self.assertEqual([], native.problems(plan([declared] + CONVENTION[1:]), names))
        # Two paths of one function: one criterion is told one path, and a duplicate declaration is reported once.
        paths = ["TestCacheExpiry/expired", "TestCacheExpiry/fresh"]
        self.assertEqual(['AC1 declares test_ac1_cache_expiry but refers to TestCacheExpiry/expired '
                          '(write "test: TestCacheExpiry/expired")', "no criterion declares TestCacheExpiry/fresh"],
                         native.problems(aliased, paths))
        twice = plan(["test: TestCacheExpiry", "test: TestCacheExpiry", CONVENTION[2]])
        self.assertEqual(["AC1 and AC2 both declare TestCacheExpiry; each criterion needs its own test"],
                         native.problems(twice, paths))

    def test_a_sibling_subtest_path_does_not_account_for_a_requested_one(self):
        # Review of #651: declaring one subtest of a function accounted for every requested path of it, so the
        # #498 prose alias passed for the second path and the runner bound that criterion to nothing.
        names = ["TestCacheExpiry/expired", "TestCacheExpiry/fresh", "TestCacheRetainsFresh"]
        sibling = plan(["test: TestCacheExpiry/expired", "test: test_ac2_cache_fresh — resolves to TestCacheExpiry/fresh",
                        "test: TestCacheRetainsFresh"])
        self.assertEqual(['AC2 declares test_ac2_cache_fresh but refers to TestCacheExpiry/fresh '
                          '(write "test: TestCacheExpiry/fresh")'], native.problems(sibling, names))
        ran = ["product::TestCacheExpiry/expired", "product::TestCacheExpiry/fresh", "product::TestCacheRetainsFresh"]
        self.assertEqual([], test_cases.match_cases(test_cases.plan_cases(sibling), ran, framework="go")["AC2"])
        # A withdrawn path, or any other sibling, accounts for nothing and is no respelling of the requested one.
        self.assertEqual(["no criterion declares TestA/empty, TestA/one"],
                         native.problems(plan(["test: TestA/two"] + CONVENTION[1:]), ["TestA/empty", "TestA/one"]))
        self.assertEqual(["no criterion declares TestCacheExpiry/expired"],
                         native.problems(plan(["test: TestCacheExpiry/other"] + CONVENTION[1:]), names[:1]))
        # The path itself, a path under it, or its whole test function accounts for it.
        for declared in ("TestCacheExpiry/expired", "TestCacheExpiry/expired/nil", "TestCacheExpiry"):
            with self.subTest(declared=declared):
                self.assertEqual([], native.problems(plan([f"test: {declared}"] + CONVENTION[1:]), names[:1]))
        self.assertEqual([], native.problems(plan(["test: TestCacheExpiry/expired_value"] + CONVENTION[1:]),
                                             native.named(['Add the Go test "TestCacheExpiry/expired value".'])))

    def test_a_refusal_names_the_subtest_path_the_criterion_refers_to(self):
        # Review of #651: a criterion was matched by the test function alone, so it was told another path.
        names = ["TestFixed/empty", "TestFixed/one"]
        swapped = plan(["test: test_ac1_x — resolves to TestFixed/one", "test: test_ac2_y — resolves to TestFixed/empty",
                        CONVENTION[2]])
        self.assertEqual(['AC1 declares test_ac1_x but refers to TestFixed/one (write "test: TestFixed/one")',
                          'AC2 declares test_ac2_y but refers to TestFixed/empty (write "test: TestFixed/empty")'],
                         native.problems(swapped, names))
        other = plan(["test: test_ac1_x", "guard: TestCacheRetainsFresh — unlike the existing TestCacheExpiry/fresh case",
                      CONVENTION[2]])
        self.assertEqual(["no criterion declares TestCacheExpiry/expired"],
                         native.problems(other, ["TestCacheExpiry/expired", "TestCacheRetainsFresh"]))
        # A path the criterion names comes first, though it also names the function.
        both = plan(["test: test_ac1_x — resolves to TestFixed/one in the TestFixed table"] + CONVENTION[1:])
        self.assertEqual(['AC1 declares test_ac1_x but refers to TestFixed/one (write "test: TestFixed/one")',
                          "no criterion declares TestFixed/empty"], native.problems(both, names))
        # Prose naming the whole function still refers to a path requested under it, one per criterion.
        function = plan(["test: test_ac1_x — resolves to TestCacheExpiry"] + CONVENTION[1:])
        self.assertEqual(['AC1 declares test_ac1_x but refers to TestCacheExpiry/expired '
                          '(write "test: TestCacheExpiry/expired")'], native.problems(function, ["TestCacheExpiry/expired"]))
        self.assertEqual(['AC1 declares test_ac1_x but refers to TestCacheExpiry/expired '
                          '(write "test: TestCacheExpiry/expired")', "no criterion declares TestCacheExpiry/fresh"],
                         native.problems(function, ["TestCacheExpiry/expired", "TestCacheExpiry/fresh"]))

    def test_the_validator_route_for_a_subtest_path_names_it_or_its_function(self):
        names = ["TestPostgres/round_trip"]
        for method in ("The Validator checks that TestPostgres skips without a database",
                       "The Validator checks that TestPostgres/round_trip skips without a database"):
            with self.subTest(method=method):
                self.assertEqual([], native.problems(plan([method] + CONVENTION[1:]), names))
        sibling = plan(["The Validator checks that TestPostgres/delete skips without a database"] + CONVENTION[1:])
        self.assertEqual(["no criterion declares TestPostgres/round_trip"], native.problems(sibling, names))

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

    def test_a_validator_criterion_does_not_license_the_issue_alias(self):
        # The issue's draft plus one criterion leaving the requested names to the Validator: the marked
        # criteria still claim in prose to resolve to tests the runner's proof never binds to them.
        body = plan(ALIASED)
        body["acceptance_criteria"].append({
            "id": "AC4", "criterion": "Given the suite, when it runs, then the requested tests exist",
            "human_review": False, "verification_method": "The Validator checks that TestFixedReturnsTwo, "
                                                          "TestFixedPreservesExisting and TestFixedPreservesCrash exist"})
        errors = native.problems(body, NAMES)
        self.assertEqual(3, len(errors), errors)
        self.assertIn('AC1 declares test_ac1_fixed_returns_two but refers to TestFixedReturnsTwo '
                      '(write "test: TestFixedReturnsTwo")', errors)
        # One Validator criterion per requested name accounts for it; the marked criteria are still aliases.
        body = plan(ALIASED)
        for n, name in enumerate(NAMES, start=4):
            body["acceptance_criteria"].append({"id": f"AC{n}", "criterion": f"Given the suite, then {name} exists",
                                                "human_review": False,
                                                "verification_method": f"The Validator checks that {name} exists"})
        errors = native.problems(body, NAMES)
        self.assertEqual(3, len(errors), errors)
        self.assertIn('AC1 declares test_ac1_fixed_returns_two but refers to TestFixedReturnsTwo, which only an '
                      'ordinary criterion leaves to the Validator (write "test: TestFixedReturnsTwo", or drop the '
                      'reference)', errors)

    def test_the_alias_in_a_marked_criterions_own_text_is_refused_too(self):
        # The issue's prose moved from the verification method into the criterion, next to a Validator
        # criterion for the name: the marked criterion still proves another identifier.
        body = plan(CONVENTION[:1] + CORRECTED[1:])
        body["acceptance_criteria"][0]["criterion"] += " (test_ac1_fixed_returns_two resolves to TestFixedReturnsTwo)"
        body["acceptance_criteria"].append({"id": "AC4", "criterion": "Given the suite, then the test exists",
                                            "human_review": False,
                                            "verification_method": "The Validator checks that TestFixedReturnsTwo exists"})
        self.assertEqual(['AC1 declares test_ac1_fixed_returns_two but refers to TestFixedReturnsTwo, which only an '
                          'ordinary criterion leaves to the Validator (write "test: TestFixedReturnsTwo", or drop the '
                          'reference)'], native.problems(body, NAMES))

    def test_a_validator_criterion_names_one_requested_test(self):
        # As the planning rule and docs say: an ordinary criterion accounts for a requested test when it names
        # that test and no other.
        names = ["TestPostgresRoundTrip", "TestPostgresDelete"]
        both = plan(["The Validator checks that TestPostgresRoundTrip and TestPostgresDelete skip without a database"]
                    + CONVENTION[1:])
        self.assertEqual(["no criterion declares TestPostgresRoundTrip, TestPostgresDelete"],
                         native.problems(both, names))
        each = plan(["The Validator checks that TestPostgresRoundTrip skips without a database",
                     "The Validator checks that TestPostgresDelete skips without a database", CONVENTION[2]])
        self.assertEqual([], native.problems(each, names))


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

    def test_the_original_literal_requests_all_three_and_refuses_the_aliased_draft(self):
        state = self.state(task=ORIGINAL_BRIEF.strip(), task_id="task-1", version=3, status="RUNNING")
        self.assertEqual(NAMES, native.requested(state))
        with self.assertRaisesRegex(ValueError, "AC1 declares test_ac1_fixed_returns_two"):
            lifecycle.install_draft(state, plan(ALIASED), origin="astra_finalize")
        self.assertNotIn("goal_contract", state)
        lifecycle.validate_body(state, plan(CORRECTED), ready=True)

    def test_a_callable_production_api_is_not_a_requested_test(self):
        found = project({**GO_SEED, "product.go": "package product\nfunc TestConnection() error { return nil }\n"})
        self.addCleanup(found.close)
        for task in ["implement func TestConnection() error in product.go; this is a callable production API",
                     "Add a func named TestConnection that returns an error, in product.go."]:  # #651 B
            with self.subTest(task=task):
                state = self.state(found.root, task=task)
                self.assertEqual([], native.named([task]))
                self.assertEqual([], native.requested(state))
                lifecycle.validate_body(state, plan(CONVENTION), ready=True)

    def test_a_separate_native_test_is_requested_until_a_genuine_declaration_exists(self):
        found = project({**GO_SEED, "product.go": "package product\nfunc TestConnection() error { return nil }\n"})
        self.addCleanup(found.close)
        task = "Add a regression test TestConnectionBehavior for the callable production API."
        state = self.state(found.root, task=task)
        self.assertEqual(["TestConnectionBehavior"], native.requested(state))
        found.write({"connection_test.go": 'package product\nimport "testing"\n'
                                          'func TestConnectionBehavior(t *testing.T) {}\n'})
        self.assertEqual([], native.requested(state))

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

    def test_a_subtest_of_a_new_test_is_requested_and_of_an_existing_one_is_not(self):
        # #651 A: a subtest path asked for nothing, so the issue's prose alias passed again.
        task = "Add the Go test TestCacheExpiry/expired."
        self.assertEqual(["TestCacheExpiry/expired"], native.requested(self.state(task=task)))
        aliased = plan(["test: test_ac1_cache_expiry — resolves to TestCacheExpiry/expired"] + CONVENTION[1:])
        with self.assertRaisesRegex(ValueError, r'AC1 declares test_ac1_cache_expiry but refers to TestCacheExpiry/'
                                                r'expired \(write "test: TestCacheExpiry/expired"\)'):
            lifecycle.validate_body(self.state(task=task), aliased, ready=True)
        lifecycle.validate_body(self.state(task=task), plan(["test: TestCacheExpiry/expired"] + CONVENTION[1:]),
                                ready=True)
        # Subtest names live in strings: one under a test the project already declares asks for nothing.
        self.assertEqual([], native.requested(self.state(task="Add the Go test TestExistingIsSeven/after_fix.")))
        # Review of #651: another subtest of the function does not license the alias for a requested path.
        task = "Add the Go tests TestCacheExpiry/expired, TestCacheExpiry/fresh and TestCacheRetainsFresh."
        with self.assertRaisesRegex(ValueError, r'AC2 declares test_ac2_cache_fresh but refers to TestCacheExpiry/fresh'):
            lifecycle.validate_body(self.state(task=task), plan(
                ["test: TestCacheExpiry/expired", "test: test_ac2_cache_fresh — resolves to TestCacheExpiry/fresh",
                 "test: TestCacheRetainsFresh"]), ready=True)
        lifecycle.validate_body(self.state(task=task), plan(
            ["test: TestCacheExpiry/expired", "test: TestCacheExpiry/fresh", "test: TestCacheRetainsFresh"]), ready=True)

    def test_a_func_named_in_a_test_file_cannot_be_aliased(self):
        # Review of #651: "a func named TestParse in parser_test.go" requested nothing, so the alias passed again.
        task = "Add a func named TestParseEmpty in product_test.go."
        self.assertEqual(["TestParseEmpty"], native.requested(self.state(task=task)))
        with self.assertRaisesRegex(ValueError, r"AC1 declares test_ac1_parse_empty but refers to TestParseEmpty"):
            lifecycle.validate_body(self.state(task=task), plan(
                ["test: test_ac1_parse_empty — resolves to TestParseEmpty"] + CONVENTION[1:]), ready=True)

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

    def test_an_inline_described_list_cannot_alias_its_later_names(self):
        task = ("Fix Fixed in product.go to return 2 instead of 0. Add native Go tests: TestFixedReturnsTwo checks "
                "that Fixed returns 2, TestFixedPreservesExisting checks Existing, and TestFixedPreservesCrash checks "
                "that a second call does not panic.")
        with self.assertRaisesRegex(ValueError, "AC2 declares test_ac2_preserves_existing but refers to "
                                                "TestFixedPreservesExisting"):
            lifecycle.validate_body(self.state(task=task), plan(["test: TestFixedReturnsTwo"] + ALIASED[1:]), ready=True)
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

    def test_an_edit_of_an_earlier_design_jobs_plan_settles_nothing_for_the_build(self):
        # #651 D: "Build it." after a proposed design plans afresh (autocode_follow_up.plan_afresh), which archives
        # the design job's contracts; the user's edit of that design plan is not their word on the build's tests.
        state = self.state(task="Design how Fixed() in product.go should behave.", task_id="task-1", version=3,
                           status="TASK_COMPLETE", workflow={"kind": "design"}, design_review={"mode": "propose"},
                           goal_contract={"origin": "user_cli_edit", "revision": 2, "hash": "abc", "task_id": "task-1",
                                          "body": plan(CONVENTION)})
        follow_up.accept(state, "Build it. " + BRIEF.split(". ", 1)[1], self.go.root, "2026-10-07T00:00:00Z")
        state["workflow"] = {"kind": "build"}  # as recognize_workflow decides for "Build it."
        follow_up.plan_afresh(state)
        self.assertEqual(NAMES, native.requested(state))
        with self.assertRaisesRegex(ValueError, "AC1 declares test_ac1_fixed_returns_two"):
            lifecycle.validate_body(state, plan(ALIASED), ready=True)
        # The user's own edit of the build's plan still settles which names stay.
        lifecycle.install_draft(state, plan(CORRECTED[:1] + CONVENTION[1:]), origin="user_cli_edit")
        self.assertEqual(NAMES[:1], native.requested(state))

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

    def test_the_rule_asks_for_a_requested_subtest_path_as_the_user_wrote_it(self):
        # #651 A: a brief naming a subtest path got no rule at all.
        text = self.prompt("glm_revise", task="Add the Go test TestCacheExpiry/expired.")
        self.assertIn("NATIVE TEST NAMES:", text)
        rule = text.split("NATIVE TEST NAMES:", 1)[1].split("\n", 1)[0]
        self.assertIn("the user asked for the Go tests TestCacheExpiry/expired.", rule)
        self.assertIn('("test: TestCacheExpiry/expired"), keeping the user\'s spelling and any subtest path', rule)

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
