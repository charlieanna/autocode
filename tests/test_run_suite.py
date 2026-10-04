"""Unit tests for the suite gate's exclusion filtering (tools/run_suite.py).

run_suite.py is what CI and the pre-push hook call instead of a bare
``unittest discover``: it reads suite_exclusions.json, drops exactly the
listed modules from the discovered suite (each with a recorded reason, never
silently), and fails loudly if an exclusion entry no longer matches anything
discovered — so a stale exclusion is caught rather than quietly rotting.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
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


class TestMapSelectionTests(unittest.TestCase):
    SOURCES = {"tests.test_architecture": "import ast\n", "tests.test_cli_flow": "from harness import driver\n",
               "tests.test_other": "import autopilot\n"}
    RUNS = {"tests.test_cli_flow": {"tools/autocode_resolver_runtime.py", "scenarios/harness/driver.py"},
            "tests.test_removed": {"tools/autocode_resolver_runtime.py"}}

    def test_a_test_that_runs_a_changed_module_without_importing_it_is_selected(self):
        # #330: the CLI test drives autocode_resolver_runtime in a subprocess and imports only the harness.
        selected = run_suite.select_tests(["tools/autocode_resolver_runtime.py"], self.SOURCES, self.RUNS)
        self.assertEqual({"tests.test_architecture": "always",
                          "tests.test_cli_flow": "runs tools/autocode_resolver_runtime.py"}, selected)

    def test_a_changed_file_outside_tools_selects_the_tests_that_ran_it(self):
        selected = run_suite.select_tests(["scenarios/harness/driver.py"], self.SOURCES, self.RUNS)
        self.assertEqual("runs scenarios/harness/driver.py", selected["tests.test_cli_flow"])

    def test_without_a_map_selection_is_unchanged(self):
        self.assertEqual({"tests.test_architecture": "always"},
                         run_suite.select_tests(["tools/autocode_resolver_runtime.py"], self.SOURCES))

    def test_the_map_round_trips_and_drops_tests_that_no_longer_exist(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "map.json"
            run_suite.write_map(path, {"tests.test_a": ["tools/a.py"], "tests.test_gone": ["tools/a.py"]},
                                ["tests.test_a", "tests.test_b"])
            run_suite.write_map(path, {"tests.test_b": ["tools/b.py"]}, ["tests.test_a", "tests.test_b"])
            runs, commit = run_suite.load_map(path)
        self.assertEqual({"tests.test_a": {"tools/a.py"}, "tests.test_b": {"tools/b.py"}}, runs)
        self.assertRegex(commit, "^[0-9a-f]{40}$")

    def test_a_missing_map_is_empty(self):
        self.assertEqual(({}, ""), run_suite.load_map(Path("/nonexistent/map.json")))


class TraceHookTests(unittest.TestCase):
    """tools/suite_trace/sitecustomize.py, run in a copy whose repository root is a scratch directory."""

    def test_records_what_subprocesses_run_but_not_import_time_calls_or_hidden_directories(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            hook = root / "tools" / "suite_trace"
            hook.mkdir(parents=True)
            shutil.copy(run_suite.TRACE_HOOK / "sitecustomize.py", hook)
            (root / "lib").mkdir()
            (root / ".hidden").mkdir()
            (root / "lib" / "called.py").write_text("def work():\n    return 1\n")
            (root / "lib" / "imported.py").write_text("def table():\n    return {}\nTABLE = table()\n")
            (root / ".hidden" / "scratch.py").write_text("def work():\n    return 2\n")
            child = (f"import sys; sys.path[:0] = [{str(root / 'lib')!r}, {str(root / '.hidden')!r}]\n"
                     "import called, imported, scratch; called.work(); scratch.work()\n")
            parent = textwrap.dedent(f"""
                import subprocess, sys
                subprocess.run([sys.executable, "-c", {child!r}], cwd={temp!r}, check=True)
            """)
            out = root / "trace"
            out.mkdir()
            env = {**os.environ, "AUTOCODE_SUITE_TRACE": str(out), "PYTHONPATH": str(hook)}
            subprocess.run([sys.executable, "-c", parent], cwd=temp, env=env, check=True)
            self.assertEqual(["lib/called.py"], run_suite.traced_paths(out))

    def test_does_nothing_without_a_trace_directory(self):
        env = {key: value for key, value in os.environ.items() if key != "AUTOCODE_SUITE_TRACE"}
        env["PYTHONPATH"] = str(run_suite.TRACE_HOOK)
        completed = subprocess.run([sys.executable, "-c", "import sys; print(sys.getprofile())"], env=env,
                                   capture_output=True, text=True, check=True)
        self.assertEqual("None", completed.stdout.strip())


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


if __name__ == "__main__":
    unittest.main()
