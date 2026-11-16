"""A bug fix whose whole change is test support (autocode_test_support): a fixture, a helper, test data.

The real run this follows (task-kind survey, section 56) fixed the `week` fixture in tests/conftest.py,
every test passed, and the person was asked whether to make an exception to "a fix must change product
code". These tests prove such fixes through autocode_regression.prove in several languages and layouts,
and keep every way of getting a failing test to pass by editing tests failing."""

from __future__ import annotations

import shutil
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

import autocode_protected_oracles as protected_oracles
import autocode_regression as regression
import autocode_test_support as test_support
import autocode_util as util
import autocode_verify as verify

from .test_plan_command_roles import project, run_state

PY = sys.executable
CODE = "def days_between(a, b):\n    return (b - a).days\n"
CONF = (
    "import datetime\nimport pytest\n\n\n@pytest.fixture\ndef week():\n"
    "    start = datetime.date(2024, 3, 1)\n    return start, start + datetime.timedelta(days=6)\n"
)
CONF_FIX = CONF.replace("days=6", "days=7")
TEST = (
    "from dates import days_between\n\n\ndef test_week_is_seven_days(week):\n    assert days_between(*week) == 7\n\n\n"
    "def test_same_day():\n    import datetime\n    d = datetime.date(2024, 1, 1)\n    assert days_between(d, d) == 0\n"
)
PYPROJECT = '[tool.pytest.ini_options]\npythonpath = ["."]\n'
SEED = {"pyproject.toml": PYPROJECT, "dates.py": CODE, "tests/conftest.py": CONF, "tests/test_dates.py": TEST}
PYTEST = f"{PY} -m pytest -q tests"
HELP = (
    "import datetime\n\n\ndef week():\n    start = datetime.date(2024, 3, 1)\n"
    "    return start, start + datetime.timedelta(days=6)\n"
)
UTEST = (
    "import unittest\nfrom dates import days_between\nfrom tests.helpers import week\n\n\n"
    "class T(unittest.TestCase):\n    def test_week_is_seven_days(self):\n        self.assertEqual(7, days_between(*week()))\n\n"
    "    def test_same_day(self):\n        import datetime\n        d = datetime.date(2024, 1, 1)\n"
    "        self.assertEqual(0, days_between(d, d))\n"
)
PTEST = (
    "from dates import days_between\nfrom helpers import week\n\n\n"
    "def test_week_is_seven_days():\n    assert days_between(*week()) == 7\n"
)
DATA_TEST = (
    "import datetime\nimport json\nfrom pathlib import Path\n\nfrom dates import days_between\n\n\n"
    "def test_week_is_seven_days():\n    a, b = json.loads((Path(__file__).parent / 'data' / 'week.json').read_text())\n"
    "    assert days_between(datetime.date.fromisoformat(a), datetime.date.fromisoformat(b)) == 7\n"
)
JS_CODE = "exports.daysBetween = (a, b) => Math.round((b - a) / 86400000);\n"
JS_HELP = "exports.week = () => [new Date(2024, 2, 1), new Date(2024, 2, 7)];\n"
JS_TEST = (
    "const test = require('node:test');\nconst assert = require('node:assert');\n"
    "const { daysBetween } = require('../dates');\nconst { week } = require('./helpers');\n\n"
    "test('a week is seven days', () => {\n  assert.strictEqual(daysBetween(...week()), 7);\n});\n\n"
    "test('same day', () => {\n  const d = new Date(2024, 0, 1);\n  assert.strictEqual(daysBetween(d, d), 0);\n});\n"
)
JS_PKG = '{"name": "dates", "private": true, "scripts": {"test": "node --test test/*.test.js"}}\n'
GO_MOD = "module example.com/dates\n\ngo 1.22\n"
GO_CODE = (
    'package dates\n\nimport "time"\n\nfunc DaysBetween(a, b time.Time) int { return int(b.Sub(a).Hours() / 24) }\n'
)
GO_TEST = (
    'package dates\n\nimport (\n\t"os"\n\t"strings"\n\t"testing"\n\t"time"\n)\n\n'
    'func TestWeek(t *testing.T) {\n\tdata, _ := os.ReadFile("testdata/week.txt")\n\tparts := strings.Fields(string(data))\n'
    '\ta, _ := time.Parse("2006-01-02", parts[0])\n\tb, _ := time.Parse("2006-01-02", parts[1])\n'
    "\tif DaysBetween(a, b) != 7 {\n\t\tt.Fatal(DaysBetween(a, b))\n\t}\n}\n\n"
    "func TestSame(t *testing.T) {\n\tif DaysBetween(time.Time{}, time.Time{}) != 0 {\n\t\tt.Fatal()\n\t}\n}\n"
)
NO_PRODUCT = "Only test files changed; a fix must change product code"


def contract(command, paths, *, kind="bugfix"):
    """An approved bug-fix plan whose one milestone changes ``paths``, proven by ``command``."""
    method = f"run `{command}`: test_week_is_seven_days fails on the original code and passes with the fix"
    rows = [{"id": "AC1", "criterion": "a week is seven days", "verification_method": method}]
    milestones = [{"id": "M1", "acceptance_criteria": ["AC1"], "affected_paths": sorted(paths)}]
    sealed = {
        "task_id": "task-1",
        "revision": 1,
        "body": {"task_kind": kind, "acceptance_criteria": rows, "milestones": milestones},
    }
    return {**sealed, "hash": util.digest(sealed), "approval_status": "approved"}


class PureTests(unittest.TestCase):
    def test_a_file_defines_the_tests_its_ids_name(self):
        results = {
            "passed": ["tests.test_dates::test_a", "tests/test_x.py::test_b", "tests.helpers.T.test_c"],
            "failed": ["test/dates.test.js::a week"],
        }
        self.assertEqual(["tests.test_dates::test_a"], test_support.defines(["tests/test_dates.py"], results))
        self.assertEqual(["tests/test_x.py::test_b"], test_support.defines(["tests/test_x.py"], results))
        self.assertEqual(["tests.helpers.T.test_c"], test_support.defines(["tests/helpers.py"], results))
        self.assertEqual(["test/dates.test.js::a week"], test_support.defines(["test/dates.test.js"], results))
        self.assertEqual([], test_support.defines(["tests/conftest.py", "tests/data/week.json"], results))
        self.assertEqual([], test_support.defines(["tests/conftest.py"], None))

    def test_support_needs_the_plans_word_complete_results_and_no_test_in_its_files(self):
        suite = {
            "receipt": {"results": {"complete": True, "passed": ["tests.test_dates::test_same_day"], "failed": []}}
        }
        changes = {"tests/conftest.py": "modified"}
        args = (["tests/conftest.py"], [], [], changes, suite)
        self.assertEqual(["tests/conftest.py"], test_support.support(*args, True))
        self.assertEqual([], test_support.support(*args, False))
        self.assertEqual([], test_support.support(["tests/conftest.py"], ["dates.py"], [], changes, suite, True))
        self.assertEqual([], test_support.support(["tests/conftest.py"], [], ["x"], changes, suite, True))
        self.assertEqual(
            [], test_support.support(["tests/conftest.py"], [], [], {"tests/conftest.py": "deleted"}, suite, True)
        )
        incomplete = {"receipt": {"results": {"complete": False, "passed": [], "failed": []}}}
        self.assertEqual([], test_support.support(["tests/conftest.py"], [], [], changes, incomplete, True))
        self.assertEqual([], test_support.support(["tests/test_dates.py"], [], [], changes, suite, True))

    def test_the_plan_changes_test_files_only(self):
        def body(paths, status="approved"):
            return {"approval_status": status, "body": {"milestones": [{"affected_paths": paths}]}}

        def is_test(path):
            return path.startswith("tests/")

        self.assertTrue(test_support.planned(body(["tests/conftest.py", "./tests/data/a.json"]), is_test))
        self.assertFalse(test_support.planned(body(["tests/conftest.py", "dates.py"]), is_test))
        self.assertFalse(test_support.planned(body([]), is_test))
        self.assertFalse(test_support.planned(body(["tests/conftest.py"], "draft"), is_test))
        self.assertFalse(test_support.planned(None, is_test))

    def test_the_check_after_the_candidate_ran(self):
        fail, notes = [], []
        test_support.check(["tests/helpers.py"], {"results": {"passed": ["tests.helpers::test_h"]}}, fail, notes, {})
        self.assertIn("define tests on the candidate (tests.helpers::test_h)", fail[0])
        fail, notes = [], []
        test_support.check(["tests/conftest.py"], {"results": {"passed": []}}, fail, notes, {"fail_to_pass": ["t"]})
        self.assertEqual([], fail)
        self.assertIn("test support the approved plan names (tests/conftest.py)", notes[0])
        test_support.check(["tests/conftest.py"], {"results": {"passed": []}}, fail, notes, proof := {})
        self.assertEqual((1, {}), (len(notes), proof))
        proof = {"fail_to_pass": ["t"]}
        test_support.check(["tests/conftest.py"], {"results": {"passed": []}}, [], [], proof)
        self.assertEqual(["tests/conftest.py"], proof["test_support"])

    def test_the_gate_reads_support_only_from_a_passing_proof_of_this_candidate(self):
        proof = {"verdict": "PASS", "source_revision": "r1", "test_support": ["tests/conftest.py"]}
        self.assertEqual(["tests/conftest.py"], test_support.proven(proof, "r1"))
        self.assertEqual([], test_support.proven(proof, "r2"))
        self.assertEqual([], test_support.proven({**proof, "verdict": "FAIL"}, "r1"))
        self.assertEqual([], test_support.proven(None, "r1"))


class ProofTests(unittest.TestCase):
    """Real proofs: the project's own failing tests must pass with the change, and nothing passing may fail."""

    def prove(self, seed, change, command=PYTEST, planned=None):
        made = project(self, seed, ())
        made.write(change)
        state = run_state(made, contract(command, planned if planned is not None else change))
        with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
            proof = regression.prove(state, made.root, made.root.parent / "run")
        return proof["verdict"], proof["failures"], proof

    def test_a_fixture_fix_is_proven_by_the_tests_it_feeds(self):
        verdict, failures, proof = self.prove(SEED, {"tests/conftest.py": CONF_FIX})
        self.assertEqual(("PASS", []), (verdict, failures))
        self.assertEqual(["tests.test_dates::test_week_is_seven_days"], proof["fail_to_pass"])
        self.assertTrue(any("test support the approved plan names (tests/conftest.py)" in n for n in proof["notes"]))

    def test_so_is_a_unittest_helper_module(self):
        seed = {"dates.py": CODE, "tests/__init__.py": "", "tests/helpers.py": HELP, "tests/test_dates.py": UTEST}
        change = {"tests/helpers.py": HELP.replace("days=6", "days=7")}
        verdict, failures, _ = self.prove(seed, change, f"{PY} -m unittest discover -s tests -t .")
        self.assertEqual(("PASS", []), (verdict, failures))

    def test_so_is_a_test_data_file(self):
        seed = {**SEED, "tests/data/week.json": '["2024-03-01", "2024-03-07"]\n', "tests/test_dates.py": DATA_TEST}
        del seed["tests/conftest.py"]
        verdict, failures, _ = self.prove(seed, {"tests/data/week.json": '["2024-03-01", "2024-03-08"]\n'})
        self.assertEqual(("PASS", []), (verdict, failures))

    @unittest.skipUnless(shutil.which("node"), "node --test")
    def test_so_is_a_node_test_helper(self):
        seed = {"package.json": JS_PKG, "dates.js": JS_CODE, "test/helpers.js": JS_HELP, "test/dates.test.js": JS_TEST}
        verdict, failures, _ = self.prove(seed, {"test/helpers.js": JS_HELP.replace("2, 7)", "2, 8)")}, "npm test")
        self.assertEqual(("PASS", []), (verdict, failures))

    @unittest.skipUnless(shutil.which("go"), "go test")
    def test_so_is_go_testdata(self):
        seed = {
            "go.mod": GO_MOD,
            "dates.go": GO_CODE,
            "dates_test.go": GO_TEST,
            "testdata/week.txt": "2024-03-01 2024-03-07\n",
        }
        verdict, failures, _ = self.prove(seed, {"testdata/week.txt": "2024-03-01 2024-03-08\n"}, "go test ./...")
        self.assertEqual(("PASS", []), (verdict, failures))

    def test_a_conftest_function_named_like_a_test_is_no_test(self):
        change = {"tests/conftest.py": CONF_FIX + "\n\ndef test_in_conftest():\n    assert True\n"}
        self.assertEqual(("PASS", []), self.prove(SEED, change)[:2])

    def test_a_test_edited_with_the_fixture_is_still_refused(self):
        change = {"tests/conftest.py": CONF_FIX, "tests/test_dates.py": TEST.replace("== 0", "== 0  # edited")}
        self.assertEqual(("FAIL", [NO_PRODUCT]), self.prove(SEED, change)[:2])

    def test_a_test_weakened_in_place_is_still_refused(self):
        self.assertEqual(
            ("FAIL", [NO_PRODUCT]), self.prove(SEED, {"tests/test_dates.py": TEST.replace("== 7", "== 6")})[:2]
        )

    def test_a_plan_that_names_product_code_too_keeps_the_rule(self):
        change = {"tests/conftest.py": CONF_FIX}
        planned = {**change, "dates.py": CODE}
        self.assertEqual(("FAIL", [NO_PRODUCT]), self.prove(SEED, change, planned=planned)[:2])

    def test_a_helper_fix_that_breaks_a_passing_test_fails(self):
        day = "\n\n@pytest.fixture\ndef day():\n    return datetime.date({}, 1, 1)\n"
        seed = {**SEED, "tests/conftest.py": CONF + day.format(2024)}
        seed["tests/test_dates.py"] = TEST + "\n\ndef test_day(day):\n    assert day.year == 2024\n"
        verdict, failures, _ = self.prove(seed, {"tests/conftest.py": CONF_FIX + day.format(2023)})
        self.assertEqual("FAIL", verdict)
        self.assertIn("Tests that pass on base fail on the candidate: tests.test_dates::test_day", failures)

    def test_a_helper_that_gains_a_collected_test_fails(self):
        seed = {"pyproject.toml": PYPROJECT + 'python_files = ["*.py"]\n', "dates.py": CODE}
        seed.update({"tests/helpers.py": HELP, "tests/test_dates.py": PTEST})
        change = {"tests/helpers.py": HELP.replace("days=6", "days=7") + "\n\ndef test_helper():\n    assert True\n"}
        verdict, failures, _ = self.prove(seed, change)
        self.assertEqual("FAIL", verdict)
        self.assertIn("define tests on the candidate (tests.helpers::test_helper)", failures[0])

    def test_a_helper_edit_that_fixes_nothing_fails(self):
        verdict, failures, _ = self.prove(SEED, {"tests/conftest.py": CONF + "\n# touched\n"})
        self.assertEqual("FAIL", verdict)
        self.assertIn("no test that fails on the original code passes with the change", failures[0])


class OriginalGateTests(unittest.TestCase):
    """The original-test gate overlays the original test files on the candidate. Before, it put the original,
    broken fixture back too, so a proven fixture fix could never pass it, and the Investigator asked the
    person to exempt the fixture (task-kind survey, section 56)."""

    def gate(self, change, *, proven=True):
        made = project(self, SEED, ())
        run_dir = made.root.parent / "run"
        state = run_state(made, contract(PYTEST, change))
        protected_oracles.reconcile(
            {},
            state["settings"],
            SimpleNamespace(run_dir=None, revise_protected_tests=None),
            made.root,
            run_dir,
            is_test_path=verify.is_test_path,
            discover_command=lambda: PYTEST,
        )
        made.write(change)
        with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
            regression.prove(state, made.root, run_dir)
        if not proven:
            state["regression_proof"] = {**state["regression_proof"], "source_revision": "an earlier candidate"}
        return protected_oracles.replay(state, made.root, run_dir, verify.scratch_run, timeout=300)

    def test_a_proven_fixture_fix_keeps_its_fixture_under_the_original_tests(self):
        result = self.gate({"tests/conftest.py": CONF_FIX})
        self.assertEqual(("PASS", ["tests/conftest.py"]), (result["verdict"], result["test_support"]))
        self.assertEqual(0, result["original"]["exit_code"])

    def test_a_proof_of_another_candidate_puts_the_original_fixture_back(self):
        with self.assertRaisesRegex(ValueError, "original gate did not pass"):
            self.gate({"tests/conftest.py": CONF_FIX}, proven=False)

    def test_a_test_edited_to_pass_is_still_put_back(self):
        with self.assertRaisesRegex(ValueError, "original gate did not pass"):
            self.gate({"tests/conftest.py": CONF_FIX, "tests/test_dates.py": TEST.replace("== 7", "== 6")})


if __name__ == "__main__":
    unittest.main()
