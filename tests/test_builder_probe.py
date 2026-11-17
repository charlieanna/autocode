"""The Builder probe: its tasks and held-back tests, its grader and its runs (#3630 step 3)."""

import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

import autocode_builder_probe as builder_probe
import autocode_measured_routes as measured_routes
import autocode_model_probe as model_probe
import autocode_planner_probe as planner_probe
import autocode_probe_cli as probe_cli
import autocode_route_selector as route_selector

from tests.test_taskrun import FIXTURE_OPTIONS, HERE

# A reference solution for each task, as (file, old, new) edits: the held-back tests must fail on the
# project as the request starts and pass once the task is done (scenarios/README.md: an oracle, a
# reference and a broken variant).
REFERENCE = {
    "B1": [
        (
            "totals.py",
            "    return total(amounts) / len(amounts)",
            "    return total(amounts) / len(amounts) if amounts else 0.0",
        )
    ],
    "B2": [("totals.py", planner_probe.FIXED_TAX[0], planner_probe.FIXED_TAX[1])],
    "B3": [
        ("report.py", "import sys\n", "import json\nimport sys\n"),
        (
            "report.py",
            "    path = argv[1]\n    amounts = cache.get(path, lambda: load(path))\n",
            "    as_json = '--json' in argv\n    path = [a for a in argv[1:] if a != '--json'][0]\n"
            "    amounts = cache.get(path, lambda: load(path))\n"
            "    if as_json:\n"
            "        print(json.dumps({'total': totals.total(amounts), 'average': totals.average(amounts),"
            " 'with_tax': totals.total_with_tax(amounts)}))\n"
            "        return 0\n",
        ),
    ],
    "B4": [
        (
            "report.py",
            "        for line in handle:\n            if line.strip():\n                amounts = amounts + [float(line)]\n",
            "        for number, line in enumerate(handle, 1):\n"
            "            text = line.strip()\n"
            "            if not text or text.startswith('#'):\n"
            "                continue\n"
            "            try:\n"
            "                amounts = amounts + [float(text)]\n"
            "            except ValueError:\n"
            "                print(f'line {number}: not a number', file=sys.stderr)\n"
            "                sys.exit(1)\n",
        ),
    ],
    "B5": [
        ("report.py", "import sys\n", "import os\nimport sys\n"),
        (
            "report.py",
            "cache.get(path, lambda: load(path))",
            "cache.get((path, os.stat(path).st_mtime_ns), lambda: load(path))",
        ),
    ],
}


def solve(workspace, request):
    for name, old, new in REFERENCE[request]:
        target = Path(workspace) / name
        text = target.read_text(encoding="utf-8")
        assert old in text, (request, name, old)
        target.write_text(text.replace(old, new, 1), encoding="utf-8")


class HeldBackTests(unittest.TestCase):
    """Each held-back test separates a done task from an undone one, on the project the request gets."""

    def test_each_task_fails_before_and_passes_with_its_reference(self):
        for request in builder_probe.REQUESTS:
            with self.subTest(request.id), tempfile.TemporaryDirectory() as parent:
                workspace = model_probe.prepare_workspace(parent, fixed_tax=not request.broken_tax)
                files = {"test_probe_held_back.py": request.tests}
                before = model_probe.run_tests(workspace, files, parent)
                self.assertFalse(before["ok"], before["output"])
                solve(workspace, request.id)
                after = model_probe.run_tests(workspace, files, parent)
                self.assertTrue(after["ok"], after["output"])
                self.assertGreater(after["ran"], 0)
                project = model_probe.run_tests(workspace, None, parent)
                self.assertTrue(project["ok"], project["output"])
                self.assertEqual(3, project["ran"])

    def test_the_held_back_tests_never_enter_the_project(self):
        with tempfile.TemporaryDirectory() as parent:
            workspace = model_probe.prepare_workspace(parent, fixed_tax=True)
            base = model_probe._git(workspace, "rev-parse", "HEAD").strip()
            model_probe.run_tests(workspace, {"test_probe_held_back.py": builder_probe.BY_ID["B1"].tests}, parent)
            self.assertEqual([], model_probe.changed_files(workspace, base))
            solve(workspace, "B1")
            (workspace / "notes.txt").write_text("new\n", encoding="utf-8")
            self.assertEqual(["notes.txt", "totals.py"], model_probe.changed_files(workspace, base))


def graded(request="B1", **measured):
    measured = {
        "run_status": "TASK_COMPLETE",
        "held_back": {"ok": True, "ran": 2, "output": ""},
        "project_tests": {"ok": True, "ran": 3, "output": ""},
        "changed_files": ["totals.py"],
        "builder_attempts": 1,
        "seconds": 200.0,
        "cost_usd": 0.4,
        **measured,
    }
    return builder_probe.grade(request, **measured)


class GradeTests(unittest.TestCase):
    def test_a_completed_task_whose_tests_pass_passes(self):
        result = graded()
        self.assertTrue(result["passed"], result["failed"])
        self.assertIsNone(result["held_back_output"])

    def test_each_check_fails_on_its_own(self):
        cases = {
            "completed": {"run_status": "WAITING_FOR_USER"},
            "held_back_tests_pass": {"held_back": {"ok": False, "ran": 2, "output": "FAIL: test_empty"}},
            "project_tests_pass": {"project_tests": None},
            "time": {"seconds": builder_probe.TIME_LIMIT_SECONDS + 1},
        }
        for check, measured in cases.items():
            with self.subTest(check):
                self.assertEqual([check], graded(**measured)["failed"])
        self.assertIn("FAIL: test_empty", graded(**cases["held_back_tests_pass"])["held_back_output"])

    def test_tests_that_ran_nothing_do_not_pass(self):
        self.assertIn("held_back_tests_pass", graded(held_back={"ok": True, "ran": 0, "output": ""})["failed"])

    def test_editing_the_forbidden_test_file_fails(self):
        result = graded("B2", changed_files=["totals.py", "test_totals.py"])
        self.assertEqual(["keeps_forbidden_files"], result["failed"])

    def test_send_backs_are_recorded_not_graded(self):
        result = graded(builder_attempts=3)
        self.assertTrue(result["passed"])
        self.assertEqual(3, result["builder_attempts"])


def scripted(passing):
    """A runner that passes the requests in ``passing`` and records each call."""
    calls = []

    def run(model, level, request, *, checker, planner, requirements=None, cap_usd=None, **_options):
        calls.append((model, request.id, checker, planner))
        result = graded(request.id, run_status="TASK_COMPLETE" if request.id in passing else "PAUSED_BUDGET")
        result["run_cost_usd"] = 1.5
        return result

    return run, calls


class RunBuilderProbeTests(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        patcher = patch.dict("os.environ", {"XDG_CONFIG_HOME": home.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_each_candidate_gets_a_checker_of_another_family_than_it_and_the_planner(self):
        run, calls = scripted({r.id for r in builder_probe.REQUESTS})
        planner = ("zai-coding-plan/glm-5.3", "high")
        checkers = [("claude-opus-5-5", "medium"), ("zai-coding-plan/glm-5.3", "high"), ("gpt-6.1-sol", "high")]
        outcome = model_probe.run_probe(
            [("gpt-6.1-sol", "medium"), ("claude-sonnet-5-5", "medium")],
            cap_usd=50,
            stage="builder",
            planner=planner,
            checkers=checkers,
            runner=run,
        )
        by_model = {model: checker for model, _request, checker, _planner in calls}
        self.assertEqual(("claude-opus-5-5", "medium"), by_model["gpt-6.1-sol"])
        self.assertEqual(("gpt-6.1-sol", "high"), by_model["claude-sonnet-5-5"], "never the Planner's family")
        self.assertEqual({planner}, {p for *_rest, p in calls})
        self.assertEqual(["B1", "B3"], [request for model, request, *_ in calls if model == "gpt-6.1-sol"][:2])
        self.assertEqual(10, len(calls))
        self.assertEqual(15.0, outcome["spent_usd"], "the whole run's cost counts against the cap")
        self.assertEqual(
            {"gpt-6.1-sol", "claude-sonnet-5-5"},
            {entry["model"] for entry in route_selector.qualified("builder")},
            "the measured Builders are the ones selection and the quota fallback choose from",
        )

    def test_a_candidate_no_checker_may_check_is_not_measured(self):
        run, calls = scripted(set())
        outcome = model_probe.run_probe(
            [("claude-sonnet-5-5", "medium")],
            cap_usd=5,
            stage="builder",
            planner=("gpt-6.1-sol", "high"),
            checkers=[("claude-opus-5-5", "high"), ("gpt-6-sol", "high")],
            runner=run,
        )
        self.assertEqual([], calls)
        self.assertEqual("no checker of another family than it and the Planner", outcome["unmeasured"][0]["reason"])
        self.assertFalse(measured_routes.measured("builder"))

    def test_a_builder_probe_needs_a_planner_and_a_checker(self):
        run = scripted(set())[0]
        with self.assertRaises(ValueError):
            model_probe.run_probe([("x/m", None)], cap_usd=5, stage="builder", checkers=[("y/n", None)], runner=run)
        with self.assertRaises(ValueError):
            model_probe.run_probe([("x/m", None)], cap_usd=5, stage="builder", planner=("y/n", None), runner=run)

    def test_failing_the_quick_pass_stops_the_model_and_does_not_qualify(self):
        run, calls = scripted({"B1"})
        outcome = model_probe.run_probe(
            [("glm-5", "low")],
            cap_usd=5,
            stage="builder",
            planner=("gpt-6.1-sol", "high"),
            checkers=[("claude-opus-5-5", "high")],
            runner=run,
        )
        self.assertEqual(["B1", "B3"], [request for _m, request, *_ in calls])
        self.assertFalse(outcome["ranking"][0]["qualified"])
        self.assertEqual((), route_selector.qualified("builder"), "no measured Builder qualified: the person chooses")


class EstimateTests(unittest.TestCase):
    def test_the_estimate_counts_every_task_and_names_the_other_stages(self):
        estimate = model_probe.estimate([("zai-coding-plan/glm-5.3", "high")], "builder")
        self.assertEqual(5, estimate["calls"])
        self.assertEqual(2, estimate["quick_calls"])
        self.assertIsNotNone(estimate["max_usd"])
        self.assertIn("the Tester and the Completion Reviewer are extra", estimate["basis"])


class CliTests(unittest.TestCase):
    def cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = probe_cli.cli(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_a_builder_probe_needs_its_planner_and_checker(self):
        code, _out, err = self.cli("--stage", "builder", "--measure", "gpt-6.1-sol", "medium")
        self.assertEqual(2, code)
        self.assertIn("--stage builder needs --checker", err)
        code, _out, err = self.cli(
            "--stage", "builder", "--measure", "gpt-6.1-sol", "medium", "--checker", "claude-opus-5-5", "high"
        )
        self.assertEqual(2, code)
        self.assertIn("need --planner", err)

    def test_a_checker_of_the_planners_family_is_refused(self):
        code, _out, err = self.cli(
            "--stage",
            "builder",
            "--measure",
            "gpt-6.1-sol",
            "medium",
            "--planner",
            "claude-sonnet-5-5",
            "high",
            "--checker",
            "claude-opus-5-5",
            "high",
        )
        self.assertEqual(2, code)
        self.assertIn("no --checker may check gpt-6.1-sol", err)
        self.assertIn("than the Planner", err)

    def test_without_a_cap_it_only_estimates(self):
        code, out, _err = self.cli(
            "--stage",
            "builder",
            "--measure",
            "gpt-6.1-sol",
            "medium",
            "--planner",
            "zai-coding-plan/glm-5.3",
            "high",
            "--checker",
            "claude-opus-5-5",
            "high",
        )
        self.assertEqual(0, code)
        self.assertIn("Measuring these models for the Builder", out)
        self.assertIn("Nothing has run", out)


class ThroughTheCliTests(unittest.TestCase):
    """One Builder request through the real `autocode` CLI, on the offline fixture provider.

    The fixture provider's Requirements never names an outcome on a spec-first run, so the run cannot
    reach a plan here; this proves the probe's flags (every pinned route included) start a real run,
    and that a run that builds nothing is graded by the held-back tests, which fail, not as an error.
    """

    def test_a_request_starts_a_real_run_and_an_unbuilt_task_fails_its_tests(self):
        root = Path(tempfile.mkdtemp(prefix="builder-probe-cli-"))
        self.addCleanup(shutil.rmtree, root, True)
        bindir = root / "bin"
        bindir.mkdir()
        shutil.copy2(HERE / "live_fixture_provider.py", bindir / "codex")
        (bindir / "codex").chmod(0o755)
        env = {
            **os.environ,
            "PATH": f"{bindir}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
            "AUTOCODE_HOME": str(root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        result = model_probe.run_builder_request(
            "gpt-5.6-terra",
            "medium",
            "B1",
            planner=("gpt-5.6-sol", "high"),
            checker=("gpt-6-astra", None),
            options=FIXTURE_OPTIONS,
            env=env,
            cap_usd=1,
        )
        self.assertIsNone(result["error"])
        self.assertEqual("WAITING_FOR_USER", result["run_status"])
        self.assertEqual(["completed", "held_back_tests_pass", "time"], result["failed"])
        self.assertIn("ZeroDivisionError", result["held_back_output"])
        self.assertEqual([], result["changed_files"])
        self.assertEqual(["gpt-6-astra", None], result["checker"])


if __name__ == "__main__":
    unittest.main()
