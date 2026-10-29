"""Stability of named proof (#700) and report-only diff coverage of the proving tests (#701)."""

import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_diff_coverage as diff_coverage
import autocode_proof_stability as stability
import autocode_regression as regression
import autocode_run_view as run_view
import autocode_verify as verify

CATALOG = Path(__file__).resolve().parents[1] / "scenarios" / "catalog"
CASES = [{"id": "C1", "text": "answer is 2", "test_name": "test_c1_answers_two"}]


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def project(test, scenario, variant):
    """The scenario's seed committed as the base, with ``variant`` laid over it uncommitted."""
    temporary = tempfile.TemporaryDirectory(prefix="proof-stability-")
    test.addCleanup(temporary.cleanup)
    root = Path(temporary.name) / "project"
    shutil.copytree(CATALOG / scenario / "seed", root)
    git(root, "init", "-q")
    git(root, "config", "maintenance.auto", "false")
    git(root, "config", "gc.auto", "0")
    git(root, "add", "-A")
    git(root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
    base = git(root, "rev-parse", "HEAD")
    for path in (CATALOG / scenario / variant).rglob("*"):
        if path.is_file() and "__pycache__" not in path.parts:
            target = root / path.relative_to(CATALOG / scenario / variant)
            target.parent.mkdir(parents=True, exist_ok=True)
            # A fresh mtime: a same-size file with the seed's checkout time looks unchanged to git's index.
            shutil.copyfile(path, target)
    return root, base, Path(temporary.name) / "evidence"


def receipt(passed=(), failed=(), complete=True):
    return {
        "exit_code": 0,
        "timed_out": False,
        "results": {"complete": complete, "passed": list(passed), "failed": list(failed)},
    }


class JudgingTests(unittest.TestCase):
    def test_only_a_pass_and_a_fail_of_the_same_test_disagree(self):
        self.assertEqual("unstable", stability.classify(["pass", "fail", "pass"]))
        self.assertEqual("unstable", stability.classify(["fail", "missing", "pass"]))
        self.assertEqual("pass", stability.classify(["pass", "pass", "pass"]))
        self.assertEqual("fail", stability.classify(["fail", "fail", "fail"]))
        # A run that did not report the test proves nothing either way.
        self.assertEqual("unrepeated", stability.classify(["pass", "missing", "missing"]))

    def test_a_repeat_that_missed_the_test_leaves_its_single_run_standing(self):
        selected = {"t.T::test_a": "restore"}
        unstable, unrepeated = stability.judge(
            selected, [receipt(["t.T::test_a"]), receipt(complete=False), receipt()], [receipt(failed=["t.T::test_a"])]
        )
        self.assertEqual([], unstable)
        self.assertEqual(["t.T::test_a"], unrepeated)
        self.assertIn("single run stands", stability.record(unstable, unrepeated)["note"])

    def test_a_feature_test_absent_on_the_original_code_is_neither_unstable_nor_unrepeated(self):
        selected = {"t.T::test_a": "restore"}
        runs = [receipt(["t.T::test_a"])] * 3
        self.assertEqual(([], []), stability.judge(selected, runs, [receipt(), receipt(), receipt()]))

    def test_base_flakiness_counts_for_test_criteria_only(self):
        flaky_base = [
            receipt(failed=["t::a", "t::g"]),
            receipt(passed=["t::a", "t::g"]),
            receipt(failed=["t::a", "t::g"]),
        ]
        stable = [receipt(passed=["t::a", "t::g"])] * 3
        unstable, _ = stability.judge({"t::a": "restore", "t::g": "preserve"}, stable, flaky_base)
        self.assertEqual([("t::a", "base")], [(row["test"], row["side"]) for row in unstable])

    def test_judging_uses_a_copy_and_leaves_the_recorded_receipt_alone(self):
        first = receipt(["t::a", "t::b"])
        judged = stability.without(first, [{"test": "t::a"}])
        self.assertEqual(["t::b"], judged["results"]["passed"])
        self.assertEqual(["t::a", "t::b"], first["results"]["passed"])
        self.assertIs(first, stability.without(first, []))

    def test_repeat_runs_only_the_narrowed_proving_tests_on_each_side(self):
        calls = []

        def run(command, side, label):
            calls.append((side, label, command))
            return receipt(["tests.test_x.X::test_c1_answers_two"]) if side == "candidate" else receipt()

        unstable, record = stability.repeat(
            "unittest",
            "python3 -m unittest -v tests/test_x.py",
            CASES,
            receipt(["tests.test_x.X::test_c1_answers_two", "tests.test_x.X::test_other"]),
            receipt(failed=["tests.test_x.X::test_c1_answers_two"]),
            run,
            base_tree=True,
        )
        self.assertEqual([], unstable)
        self.assertEqual(3, record["repeats"])
        self.assertEqual(
            ["stability-candidate-2", "stability-base-2", "stability-candidate-3", "stability-base-3"],
            [label for _, label, _ in calls],
        )
        self.assertTrue(all(command.endswith("tests.test_x.X.test_c1_answers_two") for *_, command in calls))

    def test_nothing_repeats_without_a_named_case_or_a_selector(self):
        def run(*_):
            raise AssertionError("must not run")

        first = receipt(["t::test_c1_answers_two"])
        self.assertEqual(
            ([], None), stability.repeat("unittest", "python3 -m unittest t", [], first, None, run, base_tree=False)
        )
        unstable, record = stability.repeat("custom", "make test", CASES, first, None, run, base_tree=False)
        self.assertEqual([], unstable)
        self.assertIn("cannot select", record["note"])

    def test_narrow_selects_the_proving_tests_and_never_the_whole_suite(self):
        self.assertEqual(
            "python3 -m unittest -v tests.test_clocked.ClockedTests.test_c1_answers_two",
            stability.narrow(
                "unittest",
                "python3 -m unittest -v tests/test_clocked.py",
                ["tests.test_clocked.ClockedTests::test_c1_answers_two"],
            ),
        )
        self.assertEqual(
            "python3 -m pytest -q tests -k 'test_a or test_b'",
            stability.narrow(
                "pytest", "python3 -m pytest -q tests", ["tests/test_x.py::test_a[1]", "tests/test_x.py::test_b"]
            ),
        )
        self.assertEqual(
            "go test -run '^(TestA)$' ./...", stability.narrow("go", "go test ./...", ["example.com/m::TestA/sub"])
        )
        self.assertIn("--test-name-pattern", stability.narrow("node", "node --test test/a.test.js", ["a::adds"]))
        self.assertTrue(stability.narrow("vitest", "npx vitest run", ["a.test.ts::adds"]).endswith("-t adds"))
        for framework, command in (
            ("unittest", "python3 -m unittest tests; rm -rf /"),
            ("go", "go test -run TestA ./..."),
            ("custom", "make test"),
        ):
            with self.subTest(command=command):
                self.assertIsNone(stability.narrow(framework, command, ["t::test_a"]))
        self.assertIsNone(stability.narrow("unittest", "python3 -m unittest -v tests/test_x.py", []))


class UnittestDiscoverySelectionTests(unittest.TestCase):
    def test_selected_discovered_tests_keep_their_import_root_and_exclude_other_tests(self):
        for start, top in (("tests", None), ("components/gateway", None), ("tests", ".")):
            with self.subTest(start=start, top=top), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                directory = root / start
                directory.mkdir(parents=True)
                (directory / "__init__.py").write_text("")
                (directory / "test_selected.py").write_text(
                    "import unittest\nclass Selected(unittest.TestCase):\n"
                    " def test_target(self): self.assertEqual(2 + 2, 4)\n"
                    " def test_unselected(self): self.fail('must not run')\n"
                )
                owner = "tests.test_selected" if top else "test_selected"
                test = owner + ".Selected::test_target"
                command = shlex.join(
                    [
                        sys.executable,
                        "-m",
                        "unittest",
                        "discover",
                        "-v",
                        "-s",
                        start,
                        "-k",
                        "test_unselected",
                        *(["-t", top] if top else []),
                    ]
                )
                narrowed = stability.narrow("unittest", command, [test])
                result = subprocess.run(shlex.split(narrowed), cwd=root, capture_output=True, text=True)
                self.assertEqual(0, result.returncode, result.stderr)
                observed = verify._unittest_results(result.stderr)
                self.assertEqual([test.replace("::", ".")], [name.replace("::", ".") for name in observed["passed"]])
                self.assertEqual(1, observed["total"])


class DiffCoverageTests(unittest.TestCase):
    DIFF = (
        "diff --git a/app.py b/app.py\n+++ b/app.py\n@@ -0,0 +1,4 @@\n+def live():\n+    return 1\n"
        "+# note\n+\n"
        "diff --git a/cold.py b/cold.py\n+++ b/cold.py\n@@ -3,0 +4,2 @@\n+x = 1\n+y = 2\n"
    )

    def test_hunks_report_unexecuted_statements_and_never_call_an_unmeasured_file_covered(self):
        body = diff_coverage.intersect(diff_coverage.hunks(self.DIFF), {"app.py": {1}}, {"app.py": {2}})
        self.assertEqual([{"path": "app.py", "start": 1, "end": 4, "uncovered_lines": [2]}], body["uncovered"])
        self.assertEqual(["cold.py"], body["unmeasured"])
        self.assertEqual([None], [hunk["covered"] for hunk in body["files"][1]["hunks"]])
        covered = diff_coverage.intersect(diff_coverage.hunks(self.DIFF), {"app.py": {1, 2}, "cold.py": {4, 5}}, {})
        self.assertEqual(([], []), (covered["uncovered"], covered["unmeasured"]))

    def test_a_go_profile_names_files_by_module_path(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "go.mod").write_text("module example.com/shop\n\ngo 1.22\n")
            executed, missing = diff_coverage.parse_go_cover(
                "mode: set\nexample.com/shop/cart/cart.go:3.10,5.2 2 1\nexample.com/shop/cart/cart.go:6.2,8.3 1 0\n",
                folder,
            )
        self.assertEqual({"cart/cart.go": {3, 4, 5}}, executed)
        self.assertEqual({"cart/cart.go": {6, 7, 8}}, missing)

    def test_istanbul_and_coverage_py_reports_become_checkout_relative(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            istanbul = {
                str(root / "src/a.js"): {
                    "statementMap": {
                        "0": {"start": {"line": 1}, "end": {"line": 1}},
                        "1": {"start": {"line": 2}, "end": {"line": 3}},
                    },
                    "s": {"0": 4, "1": 0},
                }
            }
            self.assertEqual(({"src/a.js": {1}}, {"src/a.js": {2, 3}}), diff_coverage.parse_istanbul(istanbul, root))
            report = {"files": {"pkg/a.py": {"executed_lines": [1, 2], "missing_lines": [5]}}}
            self.assertEqual(({"pkg/a.py": {1, 2}}, {"pkg/a.py": {5}}), diff_coverage.parse_coverage_json(report, root))
            missing = diff_coverage.finish("coverage.py", root / "absent.json", root, self.DIFF)
            self.assertFalse(missing["collected"])
            (root / "broken.json").write_text("{")
            self.assertFalse(diff_coverage.finish("coverage.py", root / "broken.json", root, self.DIFF)["collected"])

    def test_no_tool_and_no_product_hunks_cost_no_test_run(self):
        def run(*_):
            raise AssertionError("must not run")

        framework = verify.Framework("custom", "make test")
        self.assertFalse(
            diff_coverage.collect(framework, "make test", ".", "evidence", lambda: self.DIFF, run)["collected"]
        )
        self.assertIn(
            "no product code", diff_coverage.collect(framework, "make test", ".", "evidence", lambda: "", run)["note"]
        )


class ProofRunTests(unittest.TestCase):
    """The flaky-clock scenario through the real proof: its broken proving test passes once per tree."""

    def prove(self, variant):
        root, base, evidence = project(self, "proof-flaky-clock", variant)
        framework = verify.detect_framework(root)
        base_suite = verify.baseline(
            root, base, evidence, framework=framework, suite_command=framework.suite, timeout=60
        )
        return verify.verify(
            root, base, evidence, framework=framework, base_suite=base_suite, timeout=60, new_behavior=True, cases=CASES
        )

    def test_a_stable_proving_test_still_passes_after_its_repeats(self):
        result = self.prove("reference")
        self.assertEqual("PASS", result["verdict"], result["failures"] + result["unverified"])
        self.assertIn("test_c1_answers_two", " ".join(result["fail_to_pass"]))
        self.assertEqual(([], None), (result["proof_stability"]["unstable"], result["proof_stability"]["next_step"]))
        self.assertIsNone(result["unstable"])
        self.assertTrue({"stability-candidate-3", "stability-base-3"} <= set(result["checks"]))

    def test_a_proving_test_that_disagrees_with_itself_is_unstable_and_cannot_complete(self):
        result = self.prove("broken/time-dependent")
        self.assertEqual("FAIL", result["verdict"])
        unstable = result["proof_stability"]["unstable"]
        self.assertEqual([("candidate", "pass")], [(row["side"], row["runs"][0]) for row in unstable], unstable)
        self.assertIn("fail", unstable[0]["runs"])
        self.assertIn("Fix the unstable test", result["proof_stability"]["next_step"])
        self.assertTrue(any("Unstable proving tests do not count" in reason for reason in result["failures"]))
        # The first candidate run is kept as recorded, with its lucky pass.
        self.assertIn(unstable[0]["test"], result["checks"]["regression_on_candidate"]["results"]["passed"])
        state = {
            "goal_contract": {
                "body": {
                    "acceptance_criteria": [
                        {"id": "C1", "criterion": "answer is 2", "verification_method": "test: test_c1_answers_two"}
                    ]
                }
            },
            "regression_proof": {key: result.get(key) for key in regression.SUMMARY_KEYS},
        }
        self.assertFalse(regression.complete(state, result["source_revision"]))
        self.assertFalse(regression.complete(state, result["source_revision"], require_changed_code=False))
        shown = run_view.evidence(state)
        self.assertEqual(unstable, shown["proof_stability"]["unstable"])
        self.assertEqual(unstable, shown["regression_proof"]["unstable"])
        # Coverage may still be collected when the project has the tool. It is
        # an execution observation, not authority to pass an unstable proof.
        self.assertEqual(result["diff_coverage"], shown["diff_coverage"])
        self.assertEqual("FAIL", shown["regression_proof"]["verdict"])


@unittest.skipIf(
    subprocess.run([sys.executable, "-c", "import coverage"], capture_output=True).returncode, "no coverage.py"
)
class CollectedCoverageTests(unittest.TestCase):
    """With coverage.py in the project's interpreter, the untested helper's lines are reported."""

    def coverage(self, variant):
        root, base, evidence = project(self, "feature-timesheet-by-project", variant)
        framework = verify.detect_framework(root, python=sys.executable)
        base_suite = verify.baseline(
            root, base, evidence, framework=framework, suite_command=framework.suite, timeout=90
        )
        names = [
            "test_rows_per_week_and_project",
            "test_combines_with_project_filter",
            "test_render_uses_the_usual_columns",
        ]
        cases = [{"id": f"C{index}", "test_name": name} for index, name in enumerate(names, 1)]
        result = verify.verify(
            root, base, evidence, framework=framework, base_suite=base_suite, timeout=90, new_behavior=True, cases=cases
        )
        self.assertTrue(result["diff_coverage"]["collected"], result["diff_coverage"])
        return [row for row in result["diff_coverage"]["uncovered"] if row["path"].endswith("report.py")]

    def test_the_reference_covers_report_and_the_untested_helper_does_not(self):
        self.assertEqual([], self.coverage("reference"))
        self.assertTrue(self.coverage("broken/untested-helper"))


if __name__ == "__main__":
    unittest.main()
