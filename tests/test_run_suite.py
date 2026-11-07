"""Unit tests for the suite gate's exclusion filtering (tools/run_suite.py).

run_suite.py is what CI and the pre-push hook call instead of a bare
``unittest discover``: it reads suite_exclusions.json, drops exactly the
listed modules from the discovered suite (each with a recorded reason, never
silently), and fails loudly if an exclusion entry no longer matches anything
discovered — so a stale exclusion is caught rather than quietly rotting.
"""

import compileall
import contextlib
import io
import json
import re
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock, Mock, call, patch

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
        suite = _suite("tools.test_a.CaseA.test_one", "tools.test_a.CaseA.test_two", "tools.test_b.CaseB.test_three")
        kept, matched, unmatched = run_suite.filter_excluded(suite, {"tools.test_a": "reason"})
        self.assertEqual(["tools.test_b.CaseB.test_three"], [test.id() for test in run_suite.iter_tests(kept)])
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
        self.assertEqual(["tools.test_a_extra.CaseB.test_two"], [test.id() for test in run_suite.iter_tests(kept)])
        self.assertEqual({"tools.test_a"}, matched)


class CountAndIterTests(unittest.TestCase):
    def test_count_and_iter_agree_on_a_nested_suite(self):
        inner = _suite("tools.test_a.CaseA.test_one")
        outer = unittest.TestSuite([inner, _suite("tools.test_b.CaseB.test_two")])
        self.assertEqual(2, run_suite.count_tests(outer))
        self.assertEqual(
            ["tools.test_a.CaseA.test_one", "tools.test_b.CaseB.test_two"],
            [test.id() for test in run_suite.iter_tests(outer)],
        )


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
        self.assertEqual(
            {"tests.test_architecture", "tests.test_review_job", "tests.test_review_job_proof"},
            set(self.select("tools/autocode_review_job.py")),
        )
        self.assertEqual(
            {"tests.test_architecture", "tests.test_review_job", "tests.test_units"},
            set(self.select("tools/units/autoreview.py")),
        )
        self.assertEqual(
            {"tests.test_architecture", "tests.test_opencode"}, set(self.select("tools/providers/opencode.py"))
        )

    def test_a_changed_test_runs_itself(self):
        self.assertEqual("changed", self.select("tests/test_other.py")["tests.test_other"])

    def test_a_changed_data_file_runs_the_tests_that_mention_it(self):
        self.assertIn("tests.test_prompts", self.select("tools/prompts/builder.md"))

    def test_docs_and_the_dashboard_run_only_the_architecture_test(self):
        self.assertEqual(
            {"tests.test_architecture": "always"},
            self.select("docs/workflow.md", "tools/dashboard/app.js", "scenarios/run.py"),
        )

    def test_suite_and_workflow_changes_select_controls_instead_of_everything(self):
        sources = {**self.SOURCES, "tests.test_run_suite": "", "tests.test_ci_scope": ""}
        for path in (
            "tools/run_suite.py",
            "tests/__init__.py",
            "tests/suite_exclusions.json",
            ".github/workflows/tests.yml",
            "pyproject.toml",
        ):
            with self.subTest(path=path):
                selected = run_suite.select_tests(["docs/x.md", path], sources)
                self.assertIn("tests.test_run_suite", selected)
                self.assertIn("tests.test_ci_scope", selected)
                self.assertNotIn("tests.test_other", selected)

    def test_a_changed_test_helper_selects_its_direct_consumers(self):
        selected = run_suite.select_tests(
            ["tests/cli_fixture.py"],
            {"tests.test_consumer": "from tests.cli_fixture import start\n", "tests.test_other": ""},
        )
        self.assertEqual({"tests.test_consumer": run_suite.REQUIRED + "tests/cli_fixture.py"}, selected)

    def test_changed_subprocess_provider_fixture_selects_its_cli_consumer(self):
        selected = run_suite.select_tests(
            ["tests/offline_provider.py"],
            {"tests.test_public_cli": "PROVIDER = ROOT / 'offline_provider.py'\n", "tests.test_other": ""},
        )
        self.assertEqual({"tests.test_public_cli": run_suite.REQUIRED + "tests/offline_provider.py"}, selected)

    def test_changed_shared_test_selects_relative_import_consumers_even_if_slow(self):
        sources = {
            "tests.test_subprocess": "",
            "tests.test_completion_status": "from . import test_subprocess\n",
            "tests.test_other_cli": "from .test_subprocess import fixture\n",
        }
        selected = run_suite.select_tests(["tests/test_subprocess.py"], sources)
        self.assertEqual(set(sources), set(selected))
        self.assertEqual(
            (selected, []),
            run_suite.drop_slow(selected, {"tests.test_completion_status": "20s", "tests.test_other_cli": "20s"}),
        )

    def test_an_unknown_runtime_module_selects_small_public_consumers(self):
        sources = {"tests.test_cli_surface": "", "tests.test_taskrun_seam": "", "tests.test_unrelated": ""}
        selected = run_suite.select_tests(["tools/new_domain_helper.py"], sources)
        self.assertEqual({"tests.test_cli_surface", "tests.test_taskrun_seam"}, set(selected))

    def test_rename_paths_include_removed_source_and_new_test_names(self):
        completed = [
            mock.Mock(stdout=b"base\n"),
            mock.Mock(stdout=b"tools/old name.py\0tools/new name.py\0"),
            mock.Mock(stdout=b"tests/new helper.py\0"),
        ]
        with mock.patch.object(run_suite.subprocess, "run", side_effect=completed) as git:
            self.assertEqual(
                ["tests/new helper.py", "tools/new name.py", "tools/old name.py"],
                run_suite.changed_paths("origin/master"),
            )
        self.assertIn("--no-renames", git.call_args_list[1].args[0])
        self.assertIn("-z", git.call_args_list[1].args[0])

    def test_package_modules_map_to_dotted_names(self):
        self.assertEqual("units.autoreview", run_suite.tools_module("tools/units/autoreview.py"))
        self.assertEqual("providers", run_suite.tools_module("tools/providers/__init__.py"))
        self.assertIsNone(run_suite.tools_module("tools/dashboard/server.py"))
        self.assertIsNone(run_suite.tools_module("docs/cli.md"))


class DropSlowTests(unittest.TestCase):
    def test_a_slow_module_selected_for_a_changed_source_is_left_out(self):
        kept, skipped = run_suite.drop_slow(
            {"tests.test_fast": "tests tools/a.py", "tests.test_slow": "tests tools/a.py"}, {"tests.test_slow": "106 s"}
        )
        self.assertEqual({"tests.test_fast": "tests tools/a.py"}, kept)
        self.assertEqual(["tests.test_slow"], skipped)

    def test_a_slow_module_that_itself_changed_still_runs(self):
        kept, skipped = run_suite.drop_slow({"tests.test_slow": "changed"}, {"tests.test_slow": "106 s"})
        self.assertEqual({"tests.test_slow": "changed"}, kept)
        self.assertEqual([], skipped)

    def test_an_explicit_public_consumer_runs_even_if_slow(self):
        selected = {"tests.test_slow": run_suite.REQUIRED + "tools/autocode.py"}
        self.assertEqual((selected, []), run_suite.drop_slow(selected, {"tests.test_slow": "106 s"}))

    def test_all_fast_runs_every_module_but_the_unchanged_slow_ones(self):
        selected = run_suite.select_all(
            ["tests.test_fast", "tests.test_slow", "tests.test_changed_slow"], {"tests.test_changed_slow": "changed"}
        )
        kept, skipped = run_suite.drop_slow(selected, {"tests.test_slow": "106 s", "tests.test_changed_slow": "20 s"})
        self.assertEqual({"tests.test_fast": "all fast", "tests.test_changed_slow": "changed"}, kept)
        self.assertEqual(["tests.test_slow"], skipped)

    def test_the_slow_list_names_only_real_test_modules(self):
        slow = run_suite.load_exclusions(run_suite.DEFAULT_SLOW_PATH)
        self.assertTrue(slow)
        real = set(run_suite.test_modules({})) | set(run_suite.scenario_modules())
        self.assertEqual([], sorted(set(slow) - real))


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


class ScenarioModulesTests(unittest.TestCase):
    """#1088: the scenarios/ test modules besides the harness ran nowhere, and their expectations drifted."""

    def test_every_scenarios_test_module_runs_and_the_harness_only_once(self):
        modules = run_suite.scenario_modules()
        files = sorted(f"scenarios.{path.stem}" for path in run_suite.SCENARIOS_DIR.glob("test_*.py"))
        catalog = "scenarios.catalog.progressive-learning-journey.test_cli"
        self.assertEqual(sorted([*files, catalog]), sorted([*modules, run_suite.HARNESS]))
        self.assertIn("scenarios.test_adversarial_recovery", modules)

    def test_progressive_catalog_cases_load_and_split_without_running_the_cli(self):
        module = "scenarios.catalog.progressive-learning-journey.test_cli"
        self.assertIn(module, run_suite.scenario_modules())
        units = run_suite.split_module(module)
        self.assertEqual(16, len(units))
        self.assertEqual(len(units), len(set(units)))
        for name in units:
            with self.subTest(name=name):
                self.assertTrue(name.startswith(f"{module}.ProgressiveCLI.test_"))
                case = list(run_suite.iter_tests(unittest.defaultTestLoader.loadTestsFromName(name)))
                self.assertEqual([name], [test.id() for test in case])

    def test_progressive_catalog_respects_existing_slow_selection(self):
        module = "scenarios.catalog.progressive-learning-journey.test_cli"
        slow = run_suite.load_exclusions(run_suite.DEFAULT_SLOW_PATH)
        self.assertIn(module, slow)
        modules, skipped = run_suite.scenario_selection(["tools/autocode.py", "docs/cli.md"], slow)
        self.assertNotIn(module, modules)
        self.assertIn(module, skipped)
        for changed in (None, ["scenarios/catalog/progressive-learning-journey/test_cli.py"]):
            with self.subTest(changed=changed):
                modules, skipped = run_suite.scenario_selection(changed, slow)
                self.assertIn(module, modules)
                self.assertNotIn(module, skipped)

    def test_a_failing_catalog_case_makes_the_scenario_gate_fail(self):
        module = "test_catalog_enrollment_fault"
        with tempfile.TemporaryDirectory(prefix="suite-catalog-") as directory:
            root = Path(directory)
            (root / f"{module}.py").write_text(
                "import unittest\n"
                "class CatalogTests(unittest.TestCase):\n"
                "    def test_fault(self):\n"
                "        self.fail('retained catalog failure')\n"
            )
            with (
                # Not this interpreter's own trees: compiling them here would write their bytecode mid-run (#999).
                mock.patch.object(run_suite, "compile_runtime"),
                mock.patch.object(run_suite, "REPO_ROOT", root),
                mock.patch.object(run_suite, "scenario_modules", return_value=[module]),
                mock.patch.object(run_suite, "harness_tests", return_value=[]),
                mock.patch.object(sys, "path", [str(root), *sys.path]),
                mock.patch.dict(sys.modules),
                contextlib.redirect_stdout(io.StringIO()) as printed,
            ):
                self.assertEqual(1, run_suite.main(["--scenario-harness", "--jobs", "1"]))
            self.assertIn(f"{module}.CatalogTests.test_fault", printed.getvalue())
            self.assertIn("retained catalog failure", printed.getvalue())
            self.assertIn("Ran 1 tests in 1 processes", printed.getvalue())

    def test_the_split_covers_every_test_of_a_scenario_module_once(self):
        module = "scenarios.test_adversarial_runner"
        loader = unittest.defaultTestLoader
        whole = [test.id() for test in run_suite.iter_tests(loader.loadTestsFromName(module))]
        units = run_suite.split_module(module)
        self.assertGreater(len(units), 1)
        self.assertEqual(sorted(whole), sorted(units))

    def test_shared_harness_changes_run_every_scenario_but_suite_changes_do_not(self):
        slow = {"scenarios.test_slow": "200 s"}
        modules = ["scenarios.test_slow", "scenarios.test_fast"]
        with mock.patch.object(run_suite, "scenario_modules", return_value=modules):
            for changed in (
                None,
                ["tools/autocode.py", "scenarios/harness/fake_codex.py"],
            ):
                with self.subTest(changed=changed):
                    self.assertEqual((modules, []), run_suite.scenario_selection(changed, slow))
            for changed in (
                ["tools/run_suite.py"],
                [".github/workflows/tests.yml"],
                ["tools/autocode.py", "docs/cli.md"],
            ):
                with self.subTest(changed=changed):
                    self.assertEqual(([], modules), run_suite.scenario_selection(changed, slow))

    def test_changed_slow_scenario_runs_without_enrolling_the_lying_matrix(self):
        modules = ["scenarios.test_adversarial_recovery", "scenarios.test_lying_matrix"]
        with mock.patch.object(run_suite, "scenario_modules", return_value=modules):
            self.assertEqual(
                ([modules[0]], [modules[1]]),
                run_suite.scenario_selection(
                    ["scenarios/test_adversarial_recovery.py"], {module: "slow" for module in modules}
                ),
            )

    def test_process_changes_select_lifecycle_without_other_adversarial_modules(self):
        modules = [
            "scenarios.test_adversarial_lifecycle",
            "scenarios.test_adversarial_evidence",
            "scenarios.test_lying_matrix",
        ]
        with mock.patch.object(run_suite, "scenario_modules", return_value=modules):
            self.assertEqual(
                ([modules[0]], modules[1:]), run_suite.scenario_selection(["tools/autocode_session_boundary.py"], {})
            )

    def test_a_tools_fixture_a_scenario_copies_selects_only_that_scenario(self):
        # scenarios/test_contained_opencode.py copies tools/fake_opencode.py and runs it; no import names it (#709).
        modules = ["scenarios.test_contained_opencode", "scenarios.test_adversarial_lifecycle"]
        with mock.patch.object(run_suite, "scenario_modules", return_value=modules):
            self.assertEqual(([modules[0]], modules[1:]), run_suite.scenario_selection(["tools/fake_opencode.py"], {}))

    def test_runtime_policy_selects_evidence_and_planning_black_box_consumers(self):
        modules = [
            "scenarios.test_adversarial_evidence",
            "scenarios.test_adversarial_planning",
            "scenarios.test_lying_matrix",
        ]
        cases = {
            "tools/autocode_report_identity.py": modules[0],
            "tools/autocode_planning_advice.py": modules[1],
            "tools/autocode_stuck_job.py": modules[1],
        }
        with mock.patch.object(run_suite, "scenario_modules", return_value=modules):
            for path, expected in cases.items():
                with self.subTest(path=path):
                    selected, skipped = run_suite.scenario_selection([path], {expected: "slow"})
                    self.assertEqual([expected], selected)
                    self.assertIn(modules[2], skipped)

    def test_proof_policy_selects_trap_replay_scoring_without_other_harness_runs(self):
        targets = [
            "scenarios.test_harness.StockRefusalsRunTests.test_reference",
            "scenarios.test_harness.HybridRunTests.test_routes",
            "scenarios.test_harness.DriverFlagTests.test_flags",
            "scenarios.test_harness.UnrelatedSlowTests.test_other",
        ]
        with mock.patch.object(run_suite, "split_module", return_value=targets):
            for path in ("tools/autocode_regression.py", "tools/autocode_verify.py"):
                with self.subTest(path=path):
                    self.assertEqual(targets[:3], run_suite.harness_tests([path]))

    def test_runtime_consumer_paths_and_test_targets_still_exist(self):
        for path, targets in run_suite.SCENARIO_RUNTIME_CONSUMERS.items():
            with self.subTest(path=path):
                self.assertTrue((run_suite.REPO_ROOT / path).is_file(), path)
            for target in targets:
                with self.subTest(target=target):
                    cases = list(run_suite.iter_tests(unittest.defaultTestLoader.loadTestsFromName(target)))
                    self.assertTrue(cases, target)
                    self.assertFalse(any("._FailedTest." in case.id() for case in cases), target)

    def test_each_scenario_fixture_is_a_tools_script_its_scenario_module_names(self):
        # A renamed fixture or scenario module fails here instead of quietly dropping the selection.
        modules = run_suite.scenario_modules()
        for fixture, module in run_suite.SCENARIO_FIXTURES.items():
            with self.subTest(fixture=fixture):
                self.assertTrue((run_suite.REPO_ROOT / fixture).is_file())
                self.assertIn(module, modules)
                source = (run_suite.REPO_ROOT / (module.replace(".", "/") + ".py")).read_text()
                self.assertIn(f'"{fixture}"', source)

    def test_suite_controls_focus_harness_but_master_keeps_all_cases(self):
        cases = [
            "scenarios.test_harness.DriverFlagTests.test_flags",
            "scenarios.test_harness.DriverTimeoutTests.test_refusal",
            "scenarios.test_harness.FakeRunTests.test_full_build",
        ]
        with mock.patch.object(run_suite, "split_module", return_value=cases):
            self.assertEqual(cases, run_suite.harness_tests())
            self.assertEqual([cases[0]], run_suite.harness_tests([".github/workflows/tests.yml"]))
            self.assertEqual(cases[:2], run_suite.harness_tests(["tools/autocode_process.py"]))

    def test_the_scenario_run_starts_the_modules_tests_before_the_harness(self):
        ran = []

        def run_parallel(units, _jobs, _verbosity, _unit):
            ran.extend(units)
            return True

        with tempfile.TemporaryDirectory() as temp:
            slow = Path(temp) / "slow.json"
            slow.write_text(json.dumps({"scenarios.test_slow": "200 s"}))
            with (
                # Not the checkout's own tools/: compiling it here would write its bytecode mid-run (#999).
                mock.patch.object(run_suite, "compile_runtime"),
                mock.patch.object(run_suite, "DEFAULT_SLOW_PATH", slow),
                mock.patch.object(
                    run_suite, "scenario_modules", return_value=["scenarios.test_slow", "scenarios.test_fast"]
                ),
                mock.patch.object(run_suite, "changed_paths", return_value=["scenarios/test_fast.py"]),
                mock.patch.object(
                    run_suite, "split_module", side_effect=lambda module: [f"{module}.T.a", f"{module}.T.b"]
                ),
                mock.patch.object(run_suite, "harness_tests", return_value=["scenarios.test_harness.H.h"]),
                mock.patch.object(run_suite, "run_parallel", side_effect=run_parallel),
                contextlib.redirect_stdout(io.StringIO()) as printed,
            ):
                self.assertEqual(0, run_suite.main(["--scenario-harness", "--changed", "origin/master"]))
        self.assertEqual(["scenarios.test_fast.T.a", "scenarios.test_fast.T.b", "scenarios.test_harness.H.h"], ran)
        self.assertIn("Left out 1 scenario module(s)", printed.getvalue())
        self.assertIn("scenarios.test_slow", printed.getvalue())


class ModuleTimeoutTests(unittest.TestCase):
    """#1681: a hung module, and a server it started, end at --module-timeout instead of holding the run."""

    def test_a_hung_module_and_its_child_are_ended_and_reported_with_what_they_printed(self):
        with tempfile.TemporaryDirectory(prefix="suite-timeout-") as directory:
            root = Path(directory)
            (root / "test_hangs.py").write_text(
                "import subprocess, sys, time, unittest\n"
                "class Hangs(unittest.TestCase):\n"
                "    def test_server(self):\n"
                "        child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
                "        print('child', child.pid, flush=True)\n"
                "        time.sleep(120)\n"
            )
            started = time.monotonic()
            with mock.patch.object(run_suite, "REPO_ROOT", root):
                result = run_suite.run_module("test_hangs", 1, timeout=2)
            self.assertLess(time.monotonic() - started, 30, "a child holding the module's output cannot hold the run")
        self.assertEqual((False, True), (result["ok"], result["timed_out"]))
        self.assertIn("still running after 2s", result["output"])
        child = int(re.search(r"^child (\d+)$", result["output"], re.MULTILINE).group(1))
        if Path("/proc").is_dir():  # Linux: ended means gone, or a zombie no reaper has collected yet
            self.assertTrue(self.ended(child), f"process {child} was still alive after the module's tree was ended")

    @staticmethod
    def ended(pid, within=10.0):
        """Whether `pid` is gone or a zombie. The kill is asynchronous: a process that has been sent SIGKILL can
        still be running for a moment on a loaded host, after the module it belonged to was reaped."""
        deadline = time.monotonic() + within
        while True:
            try:
                state = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()[0]
            except FileNotFoundError:
                return True
            if state == "Z":
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.02)

    def test_a_timed_out_module_is_a_named_failure_in_its_row_and_the_summary(self):
        def run_module(module, _verbosity, timeout):
            self.assertEqual(5, timeout)
            hung = module == "tests.test_b"
            return {"module": module, "ok": not hung, "timed_out": hung, "seconds": 0.0, "tests": 1, "output": module}

        with mock.patch.object(run_suite, "run_module", run_module), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertFalse(run_suite.run_parallel(["tests.test_a", "tests.test_b"], 2, 1, timeout=5))
        lines = out.getvalue().splitlines()
        self.assertIn("FAIL    0.0s  tests.test_b (1 tests, timed out)", lines)
        self.assertIn(
            "Ran 2 tests in 2 modules, 2 at a time, in 0s: 1 module(s) FAILED: tests.test_b (1 timed out: tests.test_b)",
            lines,
        )


class ModuleEnvironmentTests(unittest.TestCase):
    def test_parallel_module_and_children_do_not_write_dependency_bytecode(self):
        with tempfile.TemporaryDirectory(prefix="suite-bytecode-") as directory:
            root = Path(directory)
            (root / "dependency.py").write_text("VALUE = 42\n")
            (root / "test_environment.py").write_text(
                "import os, subprocess, sys, unittest\n"
                "import dependency\n"
                "class EnvironmentTests(unittest.TestCase):\n"
                "    def test_runtime(self):\n"
                "        self.assertTrue(sys.dont_write_bytecode)\n"
                "        self.assertEqual(os.environ['SUITE_TEST_MARKER'], 'retained')\n"
                "        child = subprocess.run([sys.executable, '-c', "
                "'import dependency, sys; assert sys.dont_write_bytecode'], check=True)\n"
            )
            with (
                mock.patch.object(run_suite, "REPO_ROOT", root),
                mock.patch.dict("os.environ", PYTHONDONTWRITEBYTECODE="0", SUITE_TEST_MARKER="retained"),
            ):
                result = run_suite.run_module("test_environment", 1)
            self.assertTrue(result["ok"], result["output"])
            self.assertEqual(result["tests"], 1)
            self.assertFalse((root / "__pycache__").exists())

    def test_a_cli_a_test_launches_reads_the_runtime_bytecode_instead_of_compiling_it(self):
        # #999: with bytecode writes off, every CLI process a test launched compiled tools/ from source, most
        # of a `--status` query's CPU, which a loaded parallel run stretched past the test's 10-second limit.
        with tempfile.TemporaryDirectory(prefix="suite-runtime-bytecode-") as directory:
            root = Path(directory)
            (root / "tools").mkdir()
            (root / "tools" / "runtime.py").write_text("VALUE = 42\n")
            (root / "test_cli_launch.py").write_text(
                "import subprocess, sys, unittest\n"
                "CLI = '''\n"
                "import sys\n"
                "from importlib.machinery import SourceFileLoader\n"
                "compiled = []\n"
                "original = SourceFileLoader.source_to_code\n"
                "def recorded(self, data, path, *args, **kwargs):\n"
                "    compiled.append(path)\n"
                "    return original(self, data, path, *args, **kwargs)\n"
                "SourceFileLoader.source_to_code = recorded\n"
                "sys.path.insert(0, 'tools')\n"
                "import runtime\n"
                "assert sys.dont_write_bytecode and runtime.VALUE == 42, 'runtime not imported with writes off'\n"
                "assert not compiled, f'compiled from source: {compiled}'\n"
                "'''\n"
                "class CliTests(unittest.TestCase):\n"
                "    def test_cli(self):\n"
                "        child = subprocess.run([sys.executable, '-c', CLI], capture_output=True, text=True)\n"
                "        self.assertEqual(0, child.returncode, child.stderr)\n"
            )
            with (
                mock.patch.object(run_suite, "REPO_ROOT", root),
                # Not this interpreter's own trees: compiling them here would write their bytecode mid-run.
                mock.patch.object(run_suite, "interpreter_roots", return_value=[]),
                mock.patch.object(run_suite, "discover", return_value=unittest.TestSuite()),
                mock.patch.object(run_suite, "test_modules", return_value=["test_cli_launch"]),
                mock.patch.object(run_suite, "DEFAULT_SLOW_PATH", root / "no-slow.json"),
                mock.patch.dict("os.environ", PYTHONDONTWRITEBYTECODE="1"),
                contextlib.redirect_stdout(io.StringIO()) as printed,
            ):
                code = run_suite.main(["--exclusions", str(root / "no-exclusions.json"), "--jobs", "2"])
            self.assertEqual(0, code, printed.getvalue())
            self.assertIn("Ran 1 tests in 1 modules", printed.getvalue())

    def test_the_interpreter_s_own_trees_outside_the_checkout_are_compiled_once_each(self):
        # #999: verification hashes these trees as dependency bytes, and a process started with -E or -I rewrote a
        # stale stdlib cache during a check replay. The checkout, a directory in it and one around it are not.
        with tempfile.TemporaryDirectory(prefix="suite-interpreter-bytecode-") as directory:
            root = Path(directory).resolve()
            repo, stdlib = root / "repo", root / "stdlib"
            for tree in (repo / "tools", repo / "scenarios", stdlib / "lib-dynload"):
                tree.mkdir(parents=True)
                (tree / "module.py").write_text("VALUE = 42\n")
            entries = [repo / "tools", repo, repo / "scenarios", root, stdlib, stdlib / "lib-dynload", root / "x.zip"]
            with (
                mock.patch.object(run_suite, "REPO_ROOT", repo),
                mock.patch.object(sys, "path", [str(entry) for entry in entries]),
                mock.patch.object(run_suite.compileall, "compile_dir", wraps=compileall.compile_dir) as compile_dir,
            ):
                run_suite.compile_runtime()
            self.assertEqual([str(repo / "tools"), str(stdlib)], [call.args[0] for call in compile_dir.call_args_list])
            self.assertTrue(list((repo / "tools" / "__pycache__").glob("module.*.pyc")))
            self.assertTrue(list((stdlib / "lib-dynload" / "__pycache__").glob("module.*.pyc")))
            self.assertFalse((repo / "scenarios" / "__pycache__").exists())


class _Watched(io.StringIO):
    """stdout that releases ``event`` once ``needle`` has been written."""

    def __init__(self, needle, event):
        super().__init__()
        self.needle, self.event = needle, event

    def write(self, text):
        written = super().write(text)
        if self.needle in self.getvalue():
            self.event.set()
        return written


class PromptFailureOutputTests(unittest.TestCase):
    """#545: a finished failing module's output is printed while other modules are still running."""

    def run_with_pending_module(self, verbosity):
        printed = threading.Event()
        seen_while_pending = []

        def run_module(module, _verbosity):
            if module == "tests.test_slow":
                # Finishes only after the failure's traceback is out, or after the bound if it never comes.
                seen_while_pending.append(printed.wait(10))
                return {"module": module, "ok": True, "seconds": 0.0, "tests": 4, "output": "slow output\n"}
            return {
                "module": module,
                "ok": False,
                "seconds": 0.0,
                "tests": 2,
                "output": "Traceback: AssertionError in test_broken\n",
            }

        out = _Watched("Traceback: AssertionError in test_broken", printed)
        with mock.patch.object(run_suite, "run_module", run_module), contextlib.redirect_stdout(out):
            ok = run_suite.run_parallel(["tests.test_slow", "tests.test_broken"], 2, verbosity)
        return ok, seen_while_pending, out.getvalue()

    def test_a_failure_is_printed_while_another_module_is_still_running(self):
        for verbosity in (1, 2):
            with self.subTest(verbosity=verbosity):
                ok, seen_while_pending, output = self.run_with_pending_module(verbosity)
                self.assertEqual([True], seen_while_pending)
                self.assertFalse(ok)
                # Once when the module finishes, before the slow module's row, and once more beside the summary.
                slow_row = output.index("ok      0.0s  tests.test_slow")
                self.assertEqual(1, output[:slow_row].count("Traceback: AssertionError in test_broken"))
                self.assertEqual(1, output[slow_row:].count("Traceback: AssertionError in test_broken"))
                self.assertLess(output.index("FAIL: tests.test_broken"), slow_row)
                self.assertIn("Ran 6 tests in 2 modules, 2 at a time", output)
                self.assertTrue(output.rstrip().endswith("1 module(s) FAILED: tests.test_broken"))
                self.assertEqual(verbosity > 1, "slow output" in output)

    def test_every_module_runs_and_all_failures_are_counted(self):
        def run_module(module, _verbosity):
            return {"module": module, "ok": module != "tests.test_c", "seconds": 0.0, "tests": 1, "output": module}

        with mock.patch.object(run_suite, "run_module", run_module), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertFalse(run_suite.run_parallel(["tests.test_a", "tests.test_b", "tests.test_c"], 2, 1))
            self.assertTrue(run_suite.run_parallel(["tests.test_a", "tests.test_b"], 2, 1))
        lines = out.getvalue().splitlines()
        self.assertIn("Ran 3 tests in 3 modules, 2 at a time, in 0s: 1 module(s) FAILED: tests.test_c", lines)
        self.assertIn("Ran 2 tests in 2 modules, 2 at a time, in 0s: OK", lines)


class ParallelFailureOutputTests(unittest.TestCase):
    class Output(StringIO):
        def __init__(self):
            super().__init__()
            self.flushed_snapshots = []

        def flush(self):
            self.flushed_snapshots.append(self.getvalue())
            super().flush()

    def _run_controlled(self, rows, modules, completion_order, verbosity, unit="modules", before_second=None):
        output = self.Output()
        fake_run_module = Mock(side_effect=lambda name, level: rows[name])
        futures = {name: Mock(name=name) for name in modules}
        for name, future in futures.items():
            future.result.side_effect = lambda name=name: fake_run_module(name, verbosity)
        pool = MagicMock()
        pool.__enter__.return_value = pool
        pool.submit.side_effect = lambda function, name, level: futures[name]

        def complete_in_order(submitted):
            self.assertEqual([futures[name] for name in modules], submitted)
            yield futures[completion_order[0]]
            self.assertEqual([call(completion_order[0], verbosity)], fake_run_module.call_args_list)
            if before_second is not None:
                before_second(output)
            for name in completion_order[1:]:
                yield futures[name]

        with (
            patch.object(run_suite, "ThreadPoolExecutor", return_value=pool) as executor,
            patch.object(run_suite, "as_completed", side_effect=complete_in_order),
            patch.object(run_suite, "run_module", fake_run_module),
            patch.object(run_suite.time, "monotonic", side_effect=[100.0, 108.0]),
            redirect_stdout(output),
        ):
            result = run_suite.run_parallel(modules, 2, verbosity, unit)
        executor.assert_called_once_with(2)
        self.assertEqual([call(fake_run_module, name, verbosity) for name in modules], pool.submit.call_args_list)
        self.assertEqual([call(name, verbosity) for name in completion_order], fake_run_module.call_args_list)
        return result, output.getvalue()

    def test_failure_details_are_flushed_before_other_completions(self):
        rows = {
            "tests.later": {
                "module": "tests.later",
                "ok": True,
                "seconds": 2.0,
                "tests": 3,
                "output": "successful output marker",
            },
            "tests.first": {
                "module": "tests.first",
                "ok": False,
                "seconds": 1.0,
                "tests": 2,
                "output": "first stdout\nTraceback FIRST",
            },
            "tests.second": {
                "module": "tests.second",
                "ok": False,
                "seconds": 3.0,
                "tests": 5,
                "output": "second stdout\nTraceback SECOND",
            },
        }

        def before_second(output):
            self.assertIn("FAIL: tests.first", output.getvalue())
            self.assertIn("first stdout\nTraceback FIRST", output.getvalue())
            self.assertTrue(any("Traceback FIRST" in snapshot for snapshot in output.flushed_snapshots))
            self.assertNotIn("Ran 10 tests", output.getvalue())

        for verbosity in (1, 2):
            with self.subTest(verbosity=verbosity):
                result, output = self._run_controlled(
                    rows,
                    ["tests.later", "tests.first", "tests.second"],
                    ["tests.first", "tests.later", "tests.second"],
                    verbosity,
                    before_second=before_second,
                )
                self.assertFalse(result)
                immediate, summary = output.split("The failures again", 1)
                for name, marker in (("tests.first", "Traceback FIRST"), ("tests.second", "Traceback SECOND")):
                    for phase in (immediate, summary):
                        self.assertEqual(1, phase.count("FAIL: " + name))
                        self.assertEqual(1, phase.count(marker))
                self.assertEqual(verbosity > 1, "successful output marker" in output)
                self.assertIn(
                    "Ran 10 tests in 3 modules, 2 at a time, in 8s: 2 module(s) FAILED: tests.first, tests.second",
                    output,
                )

    def test_all_successful_completions_keep_totals_and_success_verdict(self):
        rows = {
            "tests.alpha": {
                "module": "tests.alpha",
                "ok": True,
                "seconds": 2.0,
                "tests": 2,
                "output": "alpha success detail",
            },
            "tests.beta": {
                "module": "tests.beta",
                "ok": True,
                "seconds": 1.0,
                "tests": 7,
                "output": "beta success detail",
            },
        }
        for verbosity in (1, 2):
            with self.subTest(verbosity=verbosity):
                result, output = self._run_controlled(
                    rows, ["tests.alpha", "tests.beta"], ["tests.beta", "tests.alpha"], verbosity, unit="processes"
                )
                self.assertTrue(result)
                self.assertNotIn("FAIL:", output)
                self.assertEqual(verbosity > 1, "alpha success detail" in output)
                self.assertEqual(verbosity > 1, "beta success detail" in output)
                self.assertIn("Ran 9 tests in 2 processes, 2 at a time, in 8s: OK", output)


class FailureReportTests(unittest.TestCase):
    """What the end of a parallel run shows again for a failed module (#545)."""

    REPORT = (
        f"{'=' * 70}\nFAIL: test_x (tests.test_a.A.test_x)\n{'-' * 70}\nTraceback (most recent call last):\n"
        f"AssertionError: 1 != 2\n\n{'-' * 70}\nRan 2 tests in 0.001s\n\nFAILED (failures=1)\n"
    )

    def test_unittest_s_failure_report_without_the_per_test_results(self):
        output = "test_x (tests.test_a.A.test_x) ... FAIL\ntest_y (tests.test_a.A.test_y) ... ok\n\n" + self.REPORT
        self.assertEqual(self.REPORT, run_suite.failure_report(output))
        self.assertEqual(self.REPORT, run_suite.failure_report("F.\n" + self.REPORT))

    def test_an_unexpected_success_alone_is_a_report(self):
        report = (
            f"{'=' * 70}\nUNEXPECTED SUCCESS: test_x (tests.test_a.A.test_x)\n\n{'-' * 70}\n"
            "Ran 1 test in 0.001s\n\nFAILED (unexpected successes=1)\n"
        )
        self.assertEqual(report, run_suite.failure_report("u\n" + report))

    def test_output_with_no_report_is_shown_whole(self):
        crashed = "test_x (tests.test_a.A.test_x) ... Fatal Python error: Segmentation fault\n"
        self.assertEqual(crashed, run_suite.failure_report(crashed))


if __name__ == "__main__":
    unittest.main()


class UnprivilegedTestsTests(unittest.TestCase):
    """A cloud container's root shell holds CAP_SYS_ADMIN and CAP_SYS_PTRACE; CI never does (#668)."""

    def test_either_capability_effective_or_permitted_is_privileged(self):
        none = "CapInh:\t0000000000000000\nCapPrm:\t0000000000000000\nCapEff:\t0000000000000000\n"
        self.assertFalse(run_suite.privileged(none))
        self.assertTrue(run_suite.privileged(none.replace("CapEff:\t0000000000000000", "CapEff:\t000001ffffffffff")))
        self.assertTrue(run_suite.privileged(none.replace("CapPrm:\t0000000000000000", "CapPrm:\t0000000000080000")))
        self.assertTrue(run_suite.privileged(none.replace("CapPrm:\t0000000000000000", "CapPrm:\t0000000000200000")))
        self.assertFalse(run_suite.privileged("CapPrm:\t000001fffed7ffff\nCapEff:\t000001fffed7ffff\n"))

    def test_a_privileged_runner_starts_each_module_without_them(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="Ran 1 test in 0.0s\n")
        for prefix in (run_suite.DROP_PRIVILEGED, ()):
            with (
                self.subTest(prefix=prefix),
                mock.patch.object(run_suite, "_PREFIX", prefix),
                mock.patch.object(run_suite.subprocess, "run", return_value=completed) as run,
            ):
                run_suite.run_module("tests.test_x", 1)
                command = run.call_args.args[0]
                self.assertEqual([*prefix, sys.executable, "-m", "unittest", "tests.test_x"], command)

    def test_the_prefix_is_settled_before_parallel_workers_start(self):
        # Workers that each found it unset would each print the notice.
        settled = []

        def run_module(module, verbosity):
            settled.append(run_suite._PREFIX is not None)
            return {"module": module, "ok": True, "seconds": 0.0, "tests": 1, "output": ""}

        with (
            mock.patch.object(run_suite, "_PREFIX", None),
            mock.patch.object(run_suite, "privileged", return_value=False),
            mock.patch.object(run_suite, "run_module", run_module),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertTrue(run_suite.run_parallel(["tests.test_a", "tests.test_b"], 2, 1))
        self.assertEqual([True, True], settled)
