"""Unit tests for the suite gate's exclusion filtering (tools/run_suite.py).

run_suite.py is what CI and the pre-push hook call instead of a bare
``unittest discover``: it reads suite_exclusions.json, drops exactly the
listed modules from the discovered suite (each with a recorded reason, never
silently), and fails loudly if an exclusion entry no longer matches anything
discovered — so a stale exclusion is caught rather than quietly rotting.
"""
import contextlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode_test_setup as test_setup
import run_suite


class _StubTest(unittest.TestCase):
    """A test whose id() is an arbitrary fixed string, for exercising the
    filter without loading real modules."""

    def __init__(self, test_id):
        super().__init__()
        self._stub_id = test_id

    def id(self):
        return self._stub_id

    def runTest(self):
        pass


def _suite(*test_ids):
    suite = unittest.TestSuite()
    for test_id in test_ids:
        suite.addTest(_StubTest(test_id))
    return suite


class LoadExclusionsTests(unittest.TestCase):
    def test_reads_module_to_reason_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "exclusions.json"
            path.write_text(json.dumps({"tools.test_x": "reason x"}))
            self.assertEqual({"tools.test_x": "reason x"}, run_suite.load_exclusions(path))

    def test_missing_file_means_no_exclusions(self):
        self.assertEqual({}, run_suite.load_exclusions(Path("/nonexistent/exclusions.json")))

    def test_rejects_a_non_string_reason(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "exclusions.json"
            path.write_text(json.dumps({"tools.test_x": 1}))
            with self.assertRaises(ValueError):
                run_suite.load_exclusions(path)

    def test_rejects_a_non_object_document(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "exclusions.json"
            path.write_text(json.dumps(["tools.test_x"]))
            with self.assertRaises(ValueError):
                run_suite.load_exclusions(path)


class FilterExcludedTests(unittest.TestCase):
    def test_drops_every_test_whose_module_is_excluded(self):
        suite = _suite("tools.test_a.CaseA.test_one", "tools.test_a.CaseA.test_two",
                        "tools.test_b.CaseB.test_three")
        kept, matched, unmatched = run_suite.filter_excluded(suite, {"tools.test_a": "reason"})
        self.assertEqual(["tools.test_b.CaseB.test_three"],
                          [test.id() for test in run_suite.iter_tests(kept)])
        self.assertEqual({"tools.test_a"}, matched)
        self.assertEqual(set(), unmatched)

    def test_reports_an_exclusion_entry_that_matched_nothing_as_unmatched(self):
        suite = _suite("tools.test_a.CaseA.test_one")
        kept, matched, unmatched = run_suite.filter_excluded(suite, {"tools.test_missing": "stale"})
        self.assertEqual(1, run_suite.count_tests(kept))
        self.assertEqual(set(), matched)
        self.assertEqual({"tools.test_missing"}, unmatched)

    def test_keeps_everything_when_no_exclusions_are_given(self):
        suite = _suite("tools.test_a.CaseA.test_one", "tools.test_b.CaseB.test_two")
        kept, matched, unmatched = run_suite.filter_excluded(suite, {})
        self.assertEqual(2, run_suite.count_tests(kept))
        self.assertEqual(set(), matched)
        self.assertEqual(set(), unmatched)

    def test_matches_only_the_exact_module_not_a_prefix_collision(self):
        # tools.test_a_extra must not be swept up by an exclusion for tools.test_a.
        suite = _suite("tools.test_a.CaseA.test_one", "tools.test_a_extra.CaseB.test_two")
        kept, matched, unmatched = run_suite.filter_excluded(suite, {"tools.test_a": "reason"})
        self.assertEqual(["tools.test_a_extra.CaseB.test_two"],
                          [test.id() for test in run_suite.iter_tests(kept)])
        self.assertEqual({"tools.test_a"}, matched)


class CountAndIterTests(unittest.TestCase):
    def test_count_and_iter_agree_on_a_nested_suite(self):
        inner = _suite("tools.test_a.CaseA.test_one")
        outer = unittest.TestSuite([inner, _suite("tools.test_b.CaseB.test_two")])
        self.assertEqual(2, run_suite.count_tests(outer))
        self.assertEqual(["tools.test_a.CaseA.test_one", "tools.test_b.CaseB.test_two"],
                          [test.id() for test in run_suite.iter_tests(outer)])


class SelectTestsTests(unittest.TestCase):
    SOURCES = {
        "tests.test_architecture": "import ast\n",
        "tests.test_review_job": "from units import autoreview\nimport autocode_review_job\n",
        "tests.test_review_job_proof": "import json\n",
        "tests.test_units": "from units import autoreview\n",
        "tests.test_opencode": "from providers import opencode\n",
        "tests.test_prompts": "PROMPT = 'tools/prompts/builder.md'\n",
        "tests.test_other": "import autopilot\n",
    }

    def select(self, *changed):
        return run_suite.select_tests(list(changed), self.SOURCES)

    def test_a_changed_module_runs_its_named_tests_and_direct_importers(self):
        self.assertEqual({"tests.test_architecture", "tests.test_review_job", "tests.test_review_job_proof"},
                         set(self.select("tools/autocode_review_job.py")))
        self.assertEqual({"tests.test_architecture", "tests.test_review_job", "tests.test_units"},
                         set(self.select("tools/units/autoreview.py")))
        self.assertEqual({"tests.test_architecture", "tests.test_opencode"},
                         set(self.select("tools/providers/opencode.py")))

    def test_a_changed_test_runs_itself(self):
        self.assertEqual("changed", self.select("tests/test_other.py")["tests.test_other"])

    def test_a_changed_data_file_runs_the_tests_that_mention_it(self):
        self.assertIn("tests.test_prompts", self.select("tools/prompts/builder.md"))

    def test_docs_and_the_dashboard_run_only_the_architecture_test(self):
        self.assertEqual({"tests.test_architecture": "always"},
                         self.select("docs/workflow.md", "tools/dashboard/app.js", "scenarios/run.py"))

    def test_a_change_to_the_suite_machinery_runs_everything(self):
        for path in ("tools/run_suite.py", "tests/__init__.py", "tests/suite_exclusions.json",
                     ".github/workflows/tests.yml", "pyproject.toml"):
            self.assertIsNone(self.select("docs/x.md", path), path)

    def test_package_modules_map_to_dotted_names(self):
        self.assertEqual("units.autoreview", run_suite.tools_module("tools/units/autoreview.py"))
        self.assertEqual("providers", run_suite.tools_module("tools/providers/__init__.py"))
        self.assertIsNone(run_suite.tools_module("tools/dashboard/server.py"))
        self.assertIsNone(run_suite.tools_module("docs/cli.md"))


class DropSlowTests(unittest.TestCase):
    def test_a_slow_module_selected_for_a_changed_source_is_left_out(self):
        kept, skipped = run_suite.drop_slow({"tests.test_fast": "tests tools/a.py", "tests.test_slow": "tests tools/a.py"},
                                            {"tests.test_slow": "106 s"})
        self.assertEqual({"tests.test_fast": "tests tools/a.py"}, kept)
        self.assertEqual(["tests.test_slow"], skipped)

    def test_a_slow_module_that_itself_changed_still_runs(self):
        kept, skipped = run_suite.drop_slow({"tests.test_slow": "changed"}, {"tests.test_slow": "106 s"})
        self.assertEqual({"tests.test_slow": "changed"}, kept)
        self.assertEqual([], skipped)

    def test_all_fast_runs_every_module_but_the_unchanged_slow_ones(self):
        selected = run_suite.select_all(["tests.test_fast", "tests.test_slow", "tests.test_changed_slow"],
                                        {"tests.test_changed_slow": "changed"})
        kept, skipped = run_suite.drop_slow(selected, {"tests.test_slow": "106 s", "tests.test_changed_slow": "20 s"})
        self.assertEqual({"tests.test_fast": "all fast", "tests.test_changed_slow": "changed"}, kept)
        self.assertEqual(["tests.test_slow"], skipped)

    def test_the_slow_list_names_only_real_test_modules(self):
        slow = run_suite.load_exclusions(run_suite.DEFAULT_SLOW_PATH)
        self.assertTrue(slow)
        self.assertEqual([], sorted(set(slow) - set(run_suite.test_modules({}))))


class HarnessClassesTests(unittest.TestCase):
    def test_the_split_covers_every_harness_test_once(self):
        loader = unittest.defaultTestLoader
        whole = [test.id() for test in run_suite.iter_tests(loader.loadTestsFromName(run_suite.HARNESS))]
        units = run_suite.harness_tests()
        self.assertGreater(len(units), 1)
        split = [test.id() for name in units for test in run_suite.iter_tests(loader.loadTestsFromName(name))]
        self.assertEqual(sorted(whole), sorted(split))

    def test_a_harness_that_fails_to_load_runs_whole(self):
        original = run_suite.HARNESS
        run_suite.HARNESS = "scenarios.no_such_harness"
        try:
            self.assertEqual(["scenarios.no_such_harness"], run_suite.harness_tests())
        finally:
            run_suite.HARNESS = original


class _FlushWatch(io.StringIO):
    """Stands in for stdout or stderr: remembers what had been flushed, and signals once ``marker`` has."""

    def __init__(self, marker):
        super().__init__()
        self.marker = marker
        self.flushed = ""
        self.marker_flushed = threading.Event()

    def flush(self):
        super().flush()
        self.flushed = self.getvalue()
        if self.marker in self.flushed:
            self.marker_flushed.set()


class _SerialFixtures:
    """Test cases for the one-process run; not a TestCase itself, so discovery never runs them."""

    class Fails(unittest.TestCase):
        def test_fails(self):
            self.fail("boom-545")

    class RunsNext(unittest.TestCase):
        def test_runs_next(self):
            sys.stderr.write("later-545\n")


class PromptFailureOutputTests(unittest.TestCase):
    """A failed module's output is printed, and flushed, as soon as it finishes, while other modules
    still run, so a slow or hung one cannot hide it (#545). The end of the run shows each failure again
    beside the summary. Counting, the per-module rows and the final summary are unchanged."""

    FAILURE = "AssertionError: boom-545"
    TRACEBACK = f"Traceback (most recent call last):\n  File \"x.py\", line 1\n{FAILURE}\n"
    # What `python -m unittest` writes after its last test when tests.test_bad fails.
    SECTION = (f"{'=' * 70}\nFAIL: test_x (tests.test_bad.C.test_x)\n{'-' * 70}\n{TRACEBACK}\n"
               f"{'-' * 70}\nRan 1 test in 0.001s\n\nFAILED (failures=1)\n")
    RESULT_LINES = {1: "F\n", 2: "test_x (tests.test_bad.C.test_x) ... FAIL\n\n"}
    # Seconds the still-running module waits for the failure to appear; reached only if it never does.
    GUARD = 10

    def run_parallel(self, verbosity):
        out = _FlushWatch(self.FAILURE)
        seen_while_running = []

        def run_module(module, verbosity):
            if module == "tests.test_slow":
                # Completes only once the other module's failure is visible, so it is still running then.
                out.marker_flushed.wait(self.GUARD)
                seen_while_running.append(out.flushed)
                return {"module": module, "ok": True, "seconds": 0.0, "tests": 2, "output": "later-545\n"}
            return {"module": module, "ok": False, "seconds": 0.0, "tests": 1,
                    "output": self.RESULT_LINES[verbosity] + self.SECTION}

        with mock.patch.object(run_suite, "run_module", run_module), contextlib.redirect_stdout(out):
            ok = run_suite.run_parallel(["tests.test_slow", "tests.test_bad"], 2, verbosity)
        return ok, seen_while_running, out.getvalue()

    def test_a_failed_module_is_shown_while_another_module_still_runs(self):
        for verbosity in (1, 2):
            with self.subTest(verbosity=verbosity):
                ok, seen_while_running, output = self.run_parallel(verbosity)
                self.assertIn("FAIL    0.0s  tests.test_bad (1 tests)\n", seen_while_running[0])
                self.assertIn(f"{'=' * 70}\nFAIL: tests.test_bad\n{'=' * 70}\n"
                              f"{self.RESULT_LINES[verbosity]}{self.SECTION}", seen_while_running[0])
                self.assertNotIn("tests.test_slow", seen_while_running[0])
                self.assertFalse(ok)
                self.assertIn("ok      0.0s  tests.test_slow (2 tests)\n", output)
                self.assertEqual(verbosity > 1, "later-545" in output)
                self.assertRegex(output, r"\n\nRan 3 tests in 2 modules, 2 at a time, in \d+s: "
                                         r"1 module\(s\) FAILED: tests\.test_bad\n$")

    def test_the_end_of_the_run_shows_each_failure_again_beside_the_summary(self):
        # A CI log opens at its end; a failure that finished early must not end up buried there.
        for verbosity in (1, 2):
            with self.subTest(verbosity=verbosity):
                _, _, output = self.run_parallel(verbosity)
                after_the_last_module = output[output.index("tests.test_slow (2 tests)"):]
                self.assertRegex(after_the_last_module, re.escape(self.SECTION) + r"\n\nRan 3 tests in 2 modules")
                self.assertEqual(2, output.count(self.FAILURE))
                self.assertEqual(1, output.count(self.RESULT_LINES[verbosity]))  # per-test results once

    def test_each_failure_last_appears_with_only_its_own_traceback(self):
        # AutoCode's verifier reads a unittest failure's traceback as the text up to the next FAIL:/ERROR:
        # header (autocode_test_setup.failure_details), so the last copy must not run into later output.
        for verbosity in (1, 2):
            with self.subTest(verbosity=verbosity):
                _, _, output = self.run_parallel(verbosity)
                found = list(test_setup.failure_details(output))
                self.assertEqual({("test_x", "tests.test_bad.C.test_x")}, {(name, owner) for name, owner, _ in found})
                details = [detail for _, _, detail in found]
                self.assertIn(self.TRACEBACK, details[-1])
                self.assertNotIn("later-545", details[-1])
                self.assertNotIn("tests.test_slow", details[-1])

    def test_passing_modules_still_pass(self):
        for verbosity in (1, 2):
            with self.subTest(verbosity=verbosity):
                out = _FlushWatch(self.FAILURE)
                rows = {"tests.test_a": 1, "tests.test_b": 2}
                with mock.patch.object(run_suite, "run_module", lambda module, verbosity: {
                        "module": module, "ok": True, "seconds": 0.0, "tests": rows[module],
                        "output": f"{module} output\n"}), contextlib.redirect_stdout(out):
                    ok = run_suite.run_parallel(list(rows), 2, verbosity)
                output = out.getvalue()
                self.assertTrue(ok)
                self.assertNotIn("FAIL", output)
                self.assertEqual(verbosity > 1, "tests.test_a output" in output)
                self.assertRegex(output, r"\n\nRan 3 tests in 2 modules, 2 at a time, in \d+s: OK\n$")

    def test_the_one_process_run_keeps_unittest_s_own_report(self):
        # `--jobs 1 --verbosity 2` is the per-test result stream AutoCode's verifier parses
        # (docs/bugs/saved-verification-commands.md): a traceback must not run into a later test's output.
        for verbosity in (1, 2):
            with self.subTest(verbosity=verbosity):
                err = io.StringIO()
                suite = unittest.TestSuite([_SerialFixtures.Fails("test_fails"),
                                            _SerialFixtures.RunsNext("test_runs_next")])
                with mock.patch.object(run_suite, "discover", return_value=suite), \
                        contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                    code = run_suite.main(["--jobs", "1", "--verbosity", str(verbosity),
                                           "--exclusions", "/nonexistent/exclusions.json"])
                output = err.getvalue()
                self.assertEqual(1, code)
                self.assertIn("later-545", output)
                details = [detail for name, _, detail in test_setup.failure_details(output)]
                self.assertEqual(1, len(details))
                self.assertIn(self.FAILURE, details[0])
                self.assertNotIn("later-545", details[0])
                self.assertRegex(output, r"\nRan 2 tests in .*\n\nFAILED \(failures=1\)\n$")


if __name__ == "__main__":
    unittest.main()
