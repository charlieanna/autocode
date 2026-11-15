"""Test files the original code cannot load are left out of the no-regression check, and named.

A project may hold a test file that cannot be loaded before the task begins. The whole suite on the original
code names it; when the change does not touch it and it does not load with the change either, the proof runs
the suite without it and says so (autocode_unloadable_tests). The runner does not matter: RSpec runs nothing
at all beside such a file, unittest and Node run the rest, and each proof used to end UNVERIFIED.
"""

import importlib.util
import shutil
import sys
import unittest
from unittest import mock

import autocode_package_scope as package_scope
import autocode_regression as regression
import autocode_unloadable_tests as unloadable_tests
import autocode_verify as verify
from autocode_test_framework import Framework

from .test_verify import Project

CALC_TEST = (
    "import unittest\nfrom calc import add\nclass Calc(unittest.TestCase):\n"
    " def test_zero(self): self.assertEqual(0, add(0, 0))\n"
)
ADD_TEST = " def test_add(self): self.assertEqual(5, add(2, 3))\n"
# A test of code kept in a repository beside the project, which no copy AutoCode tests in has.
BROKEN_TEST = (
    "import unittest\nimport sibling_checkout\nclass Sibling(unittest.TestCase):\n def test_reads(self): pass\n"
)
PYTHON = {
    "calc.py": "def add(a, b): return a - b\ndef sub(a, b): return a - b\n",
    "test_calc.py": CALC_TEST,
    "test_sub.py": "import unittest\nfrom calc import sub\nclass Sub(unittest.TestCase):\n"
    " def test_sub(self): self.assertEqual(1, sub(3, 2))\n",
    "test_sibling.py": BROKEN_TEST,
}
FIXED = {"calc.py": "def add(a, b): return a + b\ndef sub(a, b): return a - b\n", "test_calc.py": CALC_TEST + ADD_TEST}

BROKEN_SPEC = (
    "require_relative '../../../sibling-checkout/lib/engine'\nRSpec.describe 'engine' do\n  it('x') { }\nend\n"
)
RAILS = {
    "package.json": '{"name": "site", "private": true}\n',
    "backend/Gemfile": "source 'https://rubygems.org'\ngem 'rspec'\n",
    "backend/app/grader.rb": "class Grader\n  def grade\n    :passed\n  end\nend\n",
    "backend/spec/requests/grader_spec.rb": "require_relative '../../app/grader'\nRSpec.describe Grader do\n"
    "  it('test_passes') { expect(Grader.new.grade).to eq(:passed) }\nend\n",
    "backend/spec/services/engine_spec.rb": BROKEN_SPEC,
    "backend/spec/tools/evidence_spec.rb": BROKEN_SPEC,
}
LISTING = {
    "backend/app/listing.rb": "class Listing\n  def rows(found)\n    found.sort\n  end\nend\n",
    "backend/spec/services/listing_spec.rb": "require_relative '../../app/listing'\nRSpec.describe Listing do\n"
    "  it('test_lists_oldest_first') { expect(Listing.new.rows([2, 1])).to eq([1, 2]) }\nend\n",
}
RSPEC = bool(shutil.which("bundle") and shutil.which("rspec"))


def prove(project, *, kind="bugfix", **regression_settings):
    run_dir = project.root / ".autocode" / "runs" / "proof"
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "workspace": str(project.root),
        "base_commit": project.base,
        "iteration": 1,
        "stages": [],
        "history": [],
        "settings": {"regression": {"python": sys.executable, **regression_settings}},
        "goal_contract": {"body": {"task_kind": kind}},
    }
    with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
        return regression.prove(state, project.root, run_dir), state


class UnloadableProofTests(unittest.TestCase):
    """The proof, with the runners' own commands."""

    def project(self, seed, change):
        project = Project(seed)
        self.addCleanup(project.close)
        project.write(change)
        return project

    def test_a_test_file_the_original_code_cannot_load_is_left_out_and_named(self):
        proof, _state = prove(self.project(PYTHON, FIXED))
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(["test_calc.Calc.test_add"], proof["fail_to_pass"])
        fallback = proof["suite_fallback"]
        self.assertEqual(
            (unloadable_tests.DECISION, unloadable_tests.UNLOADABLE, ["test_sibling.py"]),
            (fallback["decision"], fallback["whole_suite_outcome"], fallback["left_out"]),
        )
        self.assertIn("unittest discover", fallback["whole_suite"])
        # The suite that ran names the files the whole suite ran, and not the one it could not load.
        self.assertEqual(fallback["command"], proof["commands"]["suite"])
        self.assertTrue(
            proof["commands"]["suite"].endswith("-m unittest -v test_calc.py test_sub.py"), proof["commands"]
        )
        self.assertIn(fallback["reason"], proof["notes"])
        self.assertEqual(
            [
                "the no-regression check left out 1 test file that cannot be loaded on the original code or with "
                "the change, so no test of theirs ran: test_sibling.py"
            ],
            proof["review_reasons"],
        )

    def test_a_test_the_rest_of_the_suite_passed_must_still_pass(self):
        broken = {**FIXED, "calc.py": "def add(a, b): return a + b\ndef sub(a, b): return a + b\n"}
        proof, _state = prove(self.project(PYTHON, broken))
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn("Tests that pass on base fail on the candidate: test_sub.Sub.test_sub", proof["failures"])
        self.assertEqual(["test_sibling.py"], proof["suite_fallback"]["left_out"])

    def test_a_file_the_change_makes_loadable_is_not_left_out(self):
        # Adding the code the test wanted is that test's own repair: the whole suite stays, and the tests
        # that could not run before prove it (autocode_first_suite.loaded).
        seed = {**PYTHON, "calc.py": FIXED["calc.py"]}
        proof, _state = prove(self.project(seed, {"sibling_checkout.py": "VALUE = 1\n"}))
        self.assertIsNone(proof.get("suite_fallback"), proof)
        self.assertIn("unittest discover", proof["commands"]["suite"])
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(["test_sibling.Sibling.test_reads"], proof["fail_to_pass"])

    def test_a_file_the_change_touches_is_not_left_out(self):
        proof, _state = prove(
            self.project(PYTHON, {**FIXED, "test_sibling.py": BROKEN_TEST + " def test_more(self): pass\n"})
        )
        self.assertIsNone(proof.get("suite_fallback"), proof)
        self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)
        self.assertIn("The base suite has collection errors", " ".join(proof["unverified"]))

    def test_a_file_the_change_breaks_is_not_excused(self):
        # test_sub.py loads on the original code. A change that stops it loading is judged as before.
        change = {**FIXED, "test_sub.py": "import unittest\nimport sibling_checkout\n"}
        proof, _state = prove(self.project(PYTHON, change))
        self.assertNotEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(["test_sibling.py"], proof["suite_fallback"]["left_out"])

    def test_a_persons_test_command_is_never_replaced(self):
        command = f"{sys.executable} -m unittest discover -v"
        proof, _state = prove(self.project(PYTHON, FIXED), test_command=command)
        self.assertIsNone(proof.get("suite_fallback"), proof)
        self.assertEqual(command, proof["commands"]["suite"])
        self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)

    def test_a_later_proof_of_the_run_runs_the_original_code_no_more(self):
        project = self.project(PYTHON, FIXED)
        fixed = {"reuse_supported": True, "runtime": "the same for every proof of this test"}
        with (
            mock.patch.object(verify, "baseline_identity", return_value=fixed),
            mock.patch.object(verify, "baseline", wraps=verify.baseline) as original,
        ):
            first, state = prove(project)
            self.assertEqual(2, original.call_count, "the whole suite, then the suite without the file")
            project.write({"calc.py": FIXED["calc.py"] + "# reviewed\n"})
            run_dir = project.root / ".autocode" / "runs" / "proof"
            with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
                second = regression.prove(state, project.root, run_dir)
            self.assertEqual(2, original.call_count)
        self.assertEqual((verify.PASS, verify.PASS), (first["verdict"], second["verdict"]), second)
        self.assertEqual(first["suite_fallback"]["command"], second["commands"]["suite"])
        self.assertNotEqual(first["source_revision"], second["source_revision"])

    @unittest.skipUnless(RSPEC, "Bundler and RSpec run backend/'s suite")
    def test_a_change_that_only_adds_files_is_proven_beside_specs_that_cannot_load(self):
        # RSpec runs no example when one spec file cannot be loaded, and a change that adds a service and its
        # spec has no existing test near it: the proof ended UNVERIFIED and the Tester failed the run for it.
        proof, _state = prove(self.project(RAILS, LISTING), kind="build")
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(["spec/services/listing_spec.rb::Listing test_lists_oldest_first"], proof["fail_to_pass"])
        left_out = ["backend/spec/services/engine_spec.rb", "backend/spec/tools/evidence_spec.rb"]
        self.assertEqual(left_out, proof["suite_fallback"]["left_out"])
        self.assertEqual(
            "cd backend && bundle exec rspec --exclude-pattern spec/services/engine_spec.rb,spec/tools/evidence_spec.rb",
            proof["commands"]["suite"],
        )
        self.assertIn("left out 2 test files", proof["review_reasons"][0])

    @unittest.skipUnless(RSPEC, "Bundler and RSpec run backend/'s suite")
    def test_an_example_the_other_specs_passed_must_still_pass(self):
        broken = {**LISTING, "backend/app/grader.rb": "class Grader\n  def grade\n    :failed\n  end\nend\n"}
        proof, _state = prove(self.project(RAILS, broken), kind="build")
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn("spec/requests/grader_spec.rb::Grader test_passes", " ".join(proof["failures"]))

    @unittest.skipUnless(importlib.util.find_spec("pytest"), "pytest runs the suite")
    def test_a_pytest_module_that_cannot_load_is_left_out_too(self):
        path = "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parents[1]))\n"
        zero = path + "from calc import add\n\ndef test_zero():\n    assert add(0, 0) == 0\n"
        seed = {
            "calc.py": "def add(a, b):\n    return a - b\n",
            "tests/test_calc.py": zero,
            "tests/test_sibling.py": "import sibling_checkout\n\ndef test_reads():\n    pass\n",
            "tests/unit/test_more.py": "def test_more():\n    assert True\n",
        }
        change = {
            "calc.py": "def add(a, b):\n    return a + b\n",
            "tests/test_calc.py": zero + "\ndef test_add():\n    assert add(2, 3) == 5\n",
        }
        proof, _state = prove(self.project(seed, change))
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(["tests/test_sibling.py"], proof["suite_fallback"]["left_out"])
        self.assertIn("--continue-on-collection-errors", proof["suite_fallback"]["whole_suite"])
        self.assertTrue(
            proof["commands"]["suite"].endswith(
                "-m pytest -q -p no:cacheprovider tests/test_calc.py tests/unit/test_more.py"
            ),
            proof["commands"],
        )

    @unittest.skipUnless(shutil.which("node"), "Node runs the suite")
    def test_a_node_test_file_that_cannot_load_is_left_out_too(self):
        seed = {
            "package.json": '{"name": "site", "scripts": {"test": "node --test"}}\n',
            "calc.mjs": "export const add = (a, b) => a - b;\n",
            "calc.test.mjs": "import test from 'node:test';\nimport assert from 'node:assert/strict';\n"
            "import { add } from './calc.mjs';\ntest('test_zero', () => assert.equal(add(0, 0), 0));\n",
            "sibling.test.mjs": "import test from 'node:test';\nimport '../sibling-checkout/engine.mjs';\n"
            "test('test_reads', () => {});\n",
        }
        change = {
            "calc.mjs": "export const add = (a, b) => a + b;\n",
            "calc.test.mjs": seed["calc.test.mjs"] + "test('test_add', () => assert.equal(add(2, 3), 5));\n",
        }
        proof, _state = prove(self.project(seed, change))
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(["sibling.test.mjs"], proof["suite_fallback"]["left_out"])
        self.assertEqual("node --test calc.test.mjs", proof["commands"]["suite"])


def run_of(passed=(), failed=(), unloaded=(), *, complete=True, command="suite"):
    results = {
        "passed": list(passed),
        "failed": [*failed, *unloaded],
        "skipped": [],
        "collection_errors": list(unloaded),
        "uncollected": list(unloaded),
        "complete": complete,
    }
    return {
        "command": command,
        "receipt": {"command": command, "exit_code": 1, "output": "out.log", "results": results},
    }


class SelectionTests(unittest.TestCase):
    def test_each_runners_name_for_a_file_it_could_not_load_is_read_as_that_file(self):
        tests = ["spec/a_spec.rb", "test_broken.py", "tests/test_deep.py", "pkg/tests/test_other.py"]
        for identity, expected in (
            ("spec/a_spec.rb::[collection]", "spec/a_spec.rb"),
            ("./spec/a_spec.rb::[collection]", "spec/a_spec.rb"),
            ("unittest.loader._FailedTest.test_broken", "test_broken.py"),
            ("unittest.loader._FailedTest.tests.test_deep", "tests/test_deep.py"),
            # unittest names a module it was given as a file by its last part alone.
            ("unittest.loader._FailedTest.test_deep", "tests/test_deep.py"),
            ("unittest.loader.ModuleImportFailure.pkg.tests.test_other", "pkg/tests/test_other.py"),
            ("::tests.test_deep", "tests/test_deep.py"),
        ):
            with self.subTest(identity=identity):
                self.assertEqual([expected], unloadable_tests.files([identity], tests))

    def test_an_id_that_names_no_single_file_leaves_nothing_out(self):
        tests = ["a/test_same.py", "b/test_same.py", "spec/a_spec.rb"]
        for identity in (
            "unittest.loader._FailedTest.test_same",  # two files
            "unittest.loader._FailedTest.test_gone",  # none
            "example.com/svc::[build failed]",  # a Go package, not a file
            "amounts (lib test)::[build failed]",  # a Cargo target
            "spec/a_spec.rb::Grader test_runs",  # a test that ran
        ):
            with self.subTest(identity=identity):
                self.assertIsNone(unloadable_tests.files([identity, "spec/a_spec.rb::[collection]"], tests))

    def test_a_package_suite_names_files_relative_to_its_directory(self):
        tests = ["backend/spec/a_spec.rb", "spec/a_spec.rb"]
        self.assertEqual(
            ["backend/spec/a_spec.rb"], unloadable_tests.files(["spec/a_spec.rb::[collection]"], tests, "backend")
        )

    def framework(self):
        return Framework("unittest", "python -m unittest discover -v", python="python")

    def test_the_suite_without_them_names_the_files_the_whole_suite_ran(self):
        whole = run_of(
            passed=["test_calc.Calc.test_zero", "tests.test_fine.Fine.test_runs"],
            unloaded=["unittest.loader._FailedTest.test_broken"],
        )
        # fixtures/test_sample.py is a test file by its name, and no file the whole suite runs.
        tests = ["fixtures/test_sample.py", "test_broken.py", "test_calc.py", "tests/test_fine.py"]
        chosen = unloadable_tests.choose(self.framework(), whole, tests, {"calc.py": "modified"})
        self.assertEqual(
            {
                "command": "python -m unittest -v test_calc.py tests/test_fine.py",
                "left_out": ["test_broken.py"],
                "probe": "python -m unittest -v test_broken.py",
                "directory": None,
            },
            chosen,
        )

    def test_nothing_is_chosen_without_a_file_to_leave_out_or_a_command_that_does(self):
        whole = run_of(passed=["test_calc.Calc.test_zero"], unloaded=["unittest.loader._FailedTest.test_broken"])
        tests = ["test_broken.py", "test_calc.py"]
        choose = unloadable_tests.choose
        self.assertIsNotNone(choose(self.framework(), whole, tests, {}))
        self.assertIsNone(choose(None, whole, tests, {}))
        self.assertIsNone(choose(self.framework(), run_of(passed=["test_calc.Calc.test_zero"]), tests, {}))
        self.assertIsNone(choose(self.framework(), {"command": "suite", "receipt": {"results": None}}, tests, {}))
        for status in ("modified", "deleted"):
            self.assertIsNone(choose(self.framework(), whole, tests, {"test_broken.py": status}), status)
        # Every file of the suite is one it could not load: nothing is left to run.
        nothing = run_of(unloaded=["unittest.loader._FailedTest.test_broken", "unittest.loader._FailedTest.test_calc"])
        self.assertIsNone(choose(self.framework(), nothing, tests, {}))
        # A runner that is given no file by name (a generic JUnit report) has no such command.
        self.assertIsNone(choose(Framework("junit", "mvn test"), whole, tests, {}))
        many = [f"test_{number:04d}.py" for number in range(400)]
        ran = [f"test_{number:04d}.Case.test_runs" for number in range(400)]
        long = run_of(passed=ran, unloaded=["unittest.loader._FailedTest.test_broken"])
        self.assertIsNone(choose(self.framework(), long, [*many, "test_broken.py"], {}), "a command nobody can read")

    def test_rspec_leaves_files_out_with_its_own_option_however_large_the_suite(self):
        rspec = Framework("rspec", "bundle exec rspec", runner="bundle exec rspec")
        many = [f"spec/models/m{number:04d}_spec.rb" for number in range(400)]
        whole = run_of(unloaded=["spec/a_spec.rb::[collection]", "spec/tools/b_spec.rb::[collection]"])
        chosen = unloadable_tests.choose(rspec, whole, [*many, "spec/a_spec.rb", "spec/tools/b_spec.rb"], {})
        self.assertEqual("bundle exec rspec --exclude-pattern spec/a_spec.rb,spec/tools/b_spec.rb", chosen["command"])
        self.assertEqual("bundle exec rspec spec/a_spec.rb spec/tools/b_spec.rb", chosen["probe"])
        packaged = package_scope.PackageFramework(rspec, "backend")
        self.assertEqual(
            "cd backend && bundle exec rspec --exclude-pattern spec/a_spec.rb",
            packaged.without(["backend/spec/a_spec.rb"], ["backend/spec/b_spec.rb"]),
        )
        # A name RSpec would read as a pattern is given no pattern: the other files are named instead.
        self.assertEqual("bundle exec rspec spec/b_spec.rb", rspec.without(["spec/a[1]_spec.rb"], ["spec/b_spec.rb"]))

    def test_the_files_must_still_not_load_with_the_change(self):
        choice = {"left_out": ["test_broken.py", "tests/test_deep.py"], "directory": None}
        both = ["unittest.loader._FailedTest.test_broken", "unittest.loader._FailedTest.test_deep"]
        still = unloadable_tests.still
        self.assertTrue(still(run_of(unloaded=both)["receipt"], choice))
        self.assertFalse(still(run_of(unloaded=both[:1])["receipt"], choice), "one of them is not named")
        self.assertFalse(
            still(run_of(passed=["tests.test_deep.Deep.test_runs"], unloaded=both[:1])["receipt"], choice),
            "one of them loads now",
        )
        self.assertFalse(still({**run_of(unloaded=both)["receipt"], "timed_out": True}, choice))
        self.assertFalse(still({"exit_code": 1, "results": None}, choice))
        self.assertFalse(still(None, choice))

    def test_leaving_a_file_out_must_not_lose_a_test_that_ran(self):
        whole = run_of(passed=["a.A.test_one", "b.B.test_two"], unloaded=["unittest.loader._FailedTest.c"])
        keeps = unloadable_tests.keeps
        self.assertTrue(keeps(whole, run_of(passed=["a.A.test_one", "b.B.test_two", "d.D.test_three"])))
        self.assertFalse(keeps(whole, run_of(passed=["a.A.test_one"])), "a test the whole suite passed is gone")
        self.assertFalse(keeps(whole, run_of(passed=["a.A.test_one", "b.B.test_two"], complete=False)))
        self.assertFalse(
            keeps(whole, run_of(passed=["a.A.test_one", "b.B.test_two"], unloaded=["unittest.loader._FailedTest.e"]))
        )
        self.assertFalse(keeps(run_of(unloaded=["spec/a_spec.rb::[collection]"]), run_of()), "nothing passed")
        self.assertFalse(keeps(whole, {"command": "suite", "receipt": {"results": None}}))

    def test_the_record_names_the_files_and_shortens_a_long_list(self):
        whole = run_of(unloaded=["x"], command="bundle exec rspec")
        left_out = [f"spec/s{number:02d}_spec.rb" for number in range(12)]
        fallback = unloadable_tests.record(
            {"command": "bundle exec rspec --exclude-pattern x", "left_out": left_out}, whole
        )
        self.assertEqual(
            ("bundle exec rspec", "out.log", left_out),
            (fallback["whole_suite"], fallback["whole_suite_output"], fallback["left_out"]),
        )
        self.assertIn("could not load 12 test files on the original code", fallback["reason"])
        self.assertTrue(fallback["reason"].endswith("spec/s09_spec.rb and 2 more"), fallback["reason"])
        self.assertTrue(unloadable_tests.review_reason(fallback).endswith("spec/s09_spec.rb and 2 more"))


if __name__ == "__main__":
    unittest.main()
