"""A repair of code that cannot be loaded is shown by the tests the project already had (first_suite.loaded).

A real run (Claude models, 2026-11-14): "someone committed amounts.py with merge conflict markers in it, so
nothing imports. Resolve it: keep both sides. The tests already cover both." The fix changed amounts.py
alone and every existing test passed. On the original code the file is a syntax error, so the suite
reports one module it could not load and no test, and an existing test showed a repair only when it "ran
and failed" there. The proof failed twice with "No regression test was added or changed", and the person
was asked to set a regression command.

The same task again, with that fixed: the plan added one new test, in a new file. A new test cannot run on
the original code either, so a bug fix's "test that ran and failed there" could not exist. The proof failed
twice with "the new tests only fail to import or collect"; the Builder then put the imports inside each
test, and preservation stayed unproven, which the person was asked to accept (first_suite.reloaded).

Owner decision, 2026-11-14: existing tests that now pass prove such a fix. The cases run a real regression
proof with unittest in a Git project, with no provider.
"""

from __future__ import annotations

import shutil
import unittest

import autocode_first_suite as first_suite
import autocode_verify as verify

from .test_verify import Project

HEAD = "def parse(text):\n    return -float(text[1:]) if text.startswith('-') else float(text)\n"
THEIRS = "def parse(text):\n    return float(text[:-1]) / 100 if text.endswith('%') else float(text)\n"
CONFLICT = "<<<<<<< HEAD\n" + HEAD + "=======\n" + THEIRS + ">>>>>>> feature/percent\n"
RESOLVED = (
    "def parse(text):\n"
    "    sign = -1 if text.startswith('-') else 1\n"
    "    text = text.lstrip('-')\n"
    "    return sign * (float(text[:-1]) / 100 if text.endswith('%') else float(text))\n"
)
TESTS = (
    "import unittest\n\nfrom amounts import parse\n\n\n"
    "class AmountTests(unittest.TestCase):\n"
    "    def test_a_negative_number(self):\n        self.assertEqual(-3.5, parse('-3.5'))\n\n"
    "    def test_a_percentage(self):\n        self.assertEqual(0.5, parse('50%'))\n"
)
NAMES = ["test_amounts.AmountTests.test_a_negative_number", "test_amounts.AmountTests.test_a_percentage"]
NEW = (
    "import unittest\n\nfrom amounts import parse\n\n\n"
    "class CombinedTests(unittest.TestCase):\n"
    "    def test_a_negative_percentage(self):\n        self.assertEqual(-0.5, parse('-50%'))\n"
)
NEW_NAME = "test_combined.CombinedTests.test_a_negative_percentage"
# What the real run's Builder wrote the second time: the import inside the test, so it runs and fails.
LAZY = (
    "import unittest\n\n\n"
    "class CombinedTests(unittest.TestCase):\n"
    "    def test_a_negative_percentage(self):\n"
    "        from amounts import parse\n\n        self.assertEqual(-0.5, parse('-50%'))\n"
)
OTHER = (
    "import unittest\n\nfrom labels import label\n\n\n"
    "class LabelTests(unittest.TestCase):\n    def test_label(self):\n        self.assertEqual('n=1', label(1))\n"
)
LABELS = "def label(count):\n    return f'n={count}'\n"


class ProofTests(unittest.TestCase):
    def project(self, files):
        project = Project(files)
        self.addCleanup(project.close)
        return project

    def test_the_projects_own_tests_show_the_repair_of_code_that_could_not_load(self):
        project = self.project({"amounts.py": CONFLICT, "test_amounts.py": TESTS})
        project.write({"amounts.py": RESOLVED})
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(NAMES, result["fail_to_pass"])
        self.assertEqual(NAMES, result["not_run_on_base"])
        self.assertEqual([], result["test_files"])
        self.assertTrue(
            any(
                note.startswith("No test changed, and the original code could not be loaded by its tests (")
                and note.endswith("run and pass with the change: " + ", ".join(NAMES))
                for note in result["notes"]
            ),
            result["notes"],
        )
        self.assertIn(
            "Every test of the original code failed there and passes with the change; nothing passing was lost",
            result["notes"],
        )

    def test_the_tests_that_passed_on_the_original_code_are_kept(self):
        files = {"amounts.py": CONFLICT, "test_amounts.py": TESTS, "labels.py": LABELS, "test_labels.py": OTHER}
        project = self.project(files)
        project.write({"amounts.py": RESOLVED})
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(NAMES, result["fail_to_pass"])
        self.assertEqual(["test_labels.LabelTests.test_label"], result["pass_to_pass"])

    def test_a_resolution_that_leaves_one_of_those_tests_failing_shows_no_repair(self):
        project = self.project({"amounts.py": CONFLICT, "test_amounts.py": TESTS})
        project.write({"amounts.py": HEAD})  # one side only: the percentage test fails
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertIn("no test that fails on the original code passes with the change", " ".join(result["failures"]))

    def test_another_module_that_still_cannot_load_shows_no_repair(self):
        files = {"amounts.py": CONFLICT, "test_amounts.py": TESTS, "labels.py": CONFLICT, "test_labels.py": OTHER}
        project = self.project(files)
        project.write({"amounts.py": RESOLVED})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertIsNone(result["fail_to_pass"])

    def test_a_change_that_also_edits_the_tests_is_not_this_repair(self):
        project = self.project({"amounts.py": CONFLICT, "test_amounts.py": TESTS})
        project.write({"amounts.py": RESOLVED, "test_amounts.py": TESTS.replace("50%", "25%").replace("0.5", "0.25")})
        result = project.verify()
        self.assertNotEqual(verify.PASS, result["verdict"], result)
        self.assertIsNone(result["fail_to_pass"])


class AddedTestTests(unittest.TestCase):
    """The change also adds a test file; the project's own test files are as they were."""

    def project(self, files):
        project = Project(files)
        self.addCleanup(project.close)
        return project

    def test_a_new_test_that_cannot_run_on_the_original_code_either_is_proved(self):
        project = self.project({"amounts.py": CONFLICT, "test_amounts.py": TESTS})
        project.write({"amounts.py": RESOLVED, "test_combined.py": NEW})
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(sorted([*NAMES, NEW_NAME]), result["fail_to_pass"])
        self.assertEqual(sorted([*NAMES, NEW_NAME]), result["not_run_on_base"])
        self.assertEqual(["test_combined.py"], result["test_files"])
        notes = " | ".join(result["notes"])
        self.assertIn("could not be loaded by the tests the project already had, so no test can run", notes)
        self.assertIn("and the change modifies none of them: the tests that could not run there and pass", notes)
        self.assertIn("Every test of the original code failed there and passes with the change", notes)

    def test_a_new_test_that_runs_and_fails_on_the_original_code_leaves_nothing_unproven(self):
        project = self.project({"amounts.py": CONFLICT, "test_amounts.py": TESTS})
        project.write({"amounts.py": RESOLVED, "test_combined.py": LAZY})
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(sorted([*NAMES, NEW_NAME]), result["fail_to_pass"])
        self.assertEqual(NAMES, result["not_run_on_base"])

    def test_a_resolution_that_leaves_an_existing_test_failing_is_refused(self):
        project = self.project({"amounts.py": CONFLICT, "test_amounts.py": TESTS})
        project.write({"amounts.py": HEAD, "test_combined.py": NEW.replace("-0.5", "-50.0").replace("-50%", "-50")})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertIn("test_amounts.AmountTests.test_a_percentage", " ".join(result["failures"]))
        self.assertFalse(result["fail_to_pass"])

    def test_another_module_that_still_cannot_load_is_refused(self):
        files = {"amounts.py": CONFLICT, "test_amounts.py": TESTS, "labels.py": CONFLICT, "test_labels.py": OTHER}
        project = self.project(files)
        project.write({"amounts.py": RESOLVED, "test_combined.py": NEW})
        result = project.verify()
        self.assertNotEqual(verify.PASS, result["verdict"], result)
        self.assertFalse(result["fail_to_pass"])

    def test_code_that_never_had_a_test_is_not_proved_by_the_changes_tests_alone(self):
        project = self.project({"amounts.py": CONFLICT, "README.md": "amounts\n"})
        project.write({"amounts.py": RESOLVED, "test_combined.py": NEW})
        result = project.verify()
        self.assertNotEqual(verify.PASS, result["verdict"], result)
        self.assertFalse(result["fail_to_pass"])

    def test_a_change_that_edits_one_of_the_projects_tests_is_not_this_repair(self):
        project = self.project({"amounts.py": CONFLICT, "test_amounts.py": TESTS})
        edited = TESTS.replace("50%", "25%").replace("0.5", "0.25")
        project.write({"amounts.py": RESOLVED, "test_amounts.py": edited, "test_combined.py": NEW})
        result = project.verify()
        self.assertNotEqual(verify.PASS, result["verdict"], result)

    def test_code_that_loads_still_needs_a_test_that_ran_and_failed(self):
        # The original code loads here: a new test that cannot import a name the fix adds shows no bug.
        project = self.project({"amounts.py": HEAD, "test_amounts.py": TESTS.split("    def test_a_percentage")[0]})
        added = RESOLVED + "\n\ndef percent(text):\n    return parse(text + '%')\n"
        new = NEW.replace("import parse", "import percent").replace("parse('-50%')", "percent('-50')")
        project.write({"amounts.py": added, "test_combined.py": new})
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertEqual([], result["fail_to_pass"])


GO_MOD = "module example.com/amounts\n\ngo 1.21\n"
GO_CONFLICT = (
    "package amounts\n\n<<<<<<< HEAD\nfunc Double(n int) int { return n * 2 }\n=======\n"
    "func Triple(n int) int { return n * 3 }\n>>>>>>> feature\n"
)
GO_RESOLVED = "package amounts\n\nfunc Double(n int) int { return n * 2 }\n\nfunc Triple(n int) int { return n * 3 }\n"
GO_TESTS = (
    'package amounts\n\nimport "testing"\n\n'
    'func TestDouble(t *testing.T) {\n\tif Double(2) != 4 {\n\t\tt.Fatal("double")\n\t}\n}\n\n'
    'func TestTriple(t *testing.T) {\n\tif Triple(2) != 6 {\n\t\tt.Fatal("triple")\n\t}\n}\n'
)
GO_NEW = (
    'package amounts\n\nimport "testing"\n\n'
    'func TestBoth(t *testing.T) {\n\tif Double(Triple(1)) != 6 {\n\t\tt.Fatal("both")\n\t}\n}\n'
)
GO_NAMES = ["example.com/amounts::TestDouble", "example.com/amounts::TestTriple"]


@unittest.skipUnless(shutil.which("go"), "Go is required for native suite proof")
class GoPackageTests(unittest.TestCase):
    """Go runs an added test file with the rest of its package, and reports a package that does not build."""

    def project(self, files):
        project = Project(files)
        self.addCleanup(project.close)
        return project

    def test_a_package_that_did_not_build_is_repaired_by_its_own_tests(self):
        project = self.project({"go.mod": GO_MOD, "amounts.go": GO_CONFLICT, "amounts_test.go": GO_TESTS})
        project.write({"amounts.go": GO_RESOLVED})
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(GO_NAMES, result["fail_to_pass"])

    def test_a_new_test_file_in_the_same_package_is_proved(self):
        project = self.project({"go.mod": GO_MOD, "amounts.go": GO_CONFLICT, "amounts_test.go": GO_TESTS})
        project.write({"amounts.go": GO_RESOLVED, "both_test.go": GO_NEW})
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(sorted([*GO_NAMES, "example.com/amounts::TestBoth"]), result["fail_to_pass"])

    def test_a_package_that_never_had_a_test_is_not_proved_by_the_changes_tests_alone(self):
        project = self.project({"go.mod": GO_MOD, "amounts.go": GO_CONFLICT})
        project.write({"amounts.go": GO_RESOLVED, "both_test.go": GO_NEW})
        result = project.verify()
        self.assertNotEqual(verify.PASS, result["verdict"], result)
        self.assertFalse(result["fail_to_pass"])


class RuleTests(unittest.TestCase):
    """first_suite.unloaded and first_suite.loaded, over a suite's saved results."""

    def results(self, **groups):
        return {"complete": True, "passed": [], "failed": [], "skipped": [], "collection_errors": [], **groups}

    def base(self, command="suite", **groups):
        return {"command": command, "receipt": {"results": self.results(**groups)}}

    def test_only_the_proofs_own_suite_names_what_could_not_load(self):
        base = self.base(failed=["::m", "t1"], collection_errors=["::m"])
        self.assertEqual(["::m"], first_suite.unloaded(base, "suite"))
        self.assertEqual([], first_suite.unloaded(base, "another suite"))
        self.assertEqual([], first_suite.unloaded(None, "suite"))
        incomplete = self.base(failed=["::m"], collection_errors=["::m"])
        incomplete["receipt"]["results"]["complete"] = False
        self.assertEqual([], first_suite.unloaded(incomplete, "suite"))

    def test_a_parser_that_tells_hooks_from_modules_names_the_modules(self):
        # Node and Vitest report a failed hook as a collection error too; only ``uncollected`` never loaded.
        base = self.base(failed=["::m", "hook"], collection_errors=["::m", "hook"], uncollected=["::m"])
        self.assertEqual(["::m"], first_suite.unloaded(base, "suite"))

    def test_only_tests_that_never_ran_on_the_base_are_loaded(self):
        base = self.results(passed=["kept"], failed=["::m", "failing"], skipped=["skipped"], collection_errors=["::m"])
        candidate = self.results(passed=["kept", "failing", "skipped", "m::one", "m::two"])
        self.assertEqual(["m::one", "m::two"], first_suite.loaded(base, candidate, ["::m"]))

    def test_a_new_failure_or_a_module_that_cannot_load_on_the_candidate_shows_nothing(self):
        base = self.results(failed=["::m", "failing"], collection_errors=["::m"])
        self.assertEqual(
            ["m::one"], first_suite.loaded(base, self.results(passed=["m::one"], failed=["failing"]), ["::m"])
        )  # a test that already ran and failed may still fail
        self.assertEqual([], first_suite.loaded(base, self.results(passed=["m::one"], failed=["m::two"]), ["::m"]))
        still = self.results(passed=["m::one"], failed=["::n"], collection_errors=["::n"])
        self.assertEqual([], first_suite.loaded(base, still, ["::m"]))
        self.assertEqual([], first_suite.loaded(base, self.results(passed=["m::one"], uncollected=["::n"]), ["::m"]))

    def test_every_module_the_base_could_not_load_must_have_a_test_that_passes_now(self):
        base = self.results(failed=["::m", "::n"], collection_errors=["::m", "::n"])
        # The candidate's suite no longer holds ``n``: its tests are not shown to pass.
        self.assertEqual([], first_suite.loaded(base, self.results(passed=["m::one", "extra::new"]), ["::m", "::n"]))
        both = self.results(passed=["m::one", "n::two", "extra::new"])
        self.assertEqual(["extra::new", "m::one", "n::two"], first_suite.loaded(base, both, ["::m", "::n"]))

    def test_a_module_needs_a_test_the_change_did_not_add(self):
        base = self.results(failed=["::m"], collection_errors=["::m"])
        candidate = self.results(passed=["m::old", "m::new", "extra::new"])
        added = ["m::new", "extra::new"]
        self.assertEqual(["extra::new", "m::new", "m::old"], first_suite.loaded(base, candidate, ["::m"], added))
        # Every test of the module is the change's own: the project had none that could show the repair.
        self.assertEqual([], first_suite.loaded(base, candidate, ["::m"], [*added, "m::old"]))

    def test_a_modules_tests_are_known_by_each_runners_ids(self):
        for module, test, other in (
            ("::tests.test_amounts", "tests.test_amounts::test_one", "tests.test_amounts_more::test_one"),
            ("unittest.loader._FailedTest.test_amounts", "test_amounts.T.test_one", "test_amounts_more.T.test_one"),
            ("src/a.test.js::[collection]", "src/a.test.js::adds", "src/a.test.jsx::adds"),
            ("example.com/m/pkg::[build failed]", "example.com/m/pkg::TestOne", "example.com/m/pkg/sub::TestOne"),
        ):
            with self.subTest(module=module):
                base = self.results(failed=[module], collection_errors=[module])
                self.assertEqual([test], first_suite.loaded(base, self.results(passed=[test]), [module]))
                self.assertEqual([], first_suite.loaded(base, self.results(passed=[other]), [module]))


if __name__ == "__main__":
    unittest.main()
