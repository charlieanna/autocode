"""The Requirements probe runs chosen models through ordinary spec-first tasks (#3630 part 2)."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_measured_routes as measured_routes
import autocode_model_probe as model_probe
import autocode_probe_cli as probe_cli
import autocode_requirements_chat as requirements_chat
import autocode_requirements_probe as probe
import autocode_requirements_review as requirements_review

from tests.test_taskrun import FIXTURE_OPTIONS, HERE


def view(*, questions=(), kind="answer", reply="", requirements=None, attempts=(), workflow=None, spent=0.01):
    return {
        "status": "WAITING_FOR_USER",
        "workflow": workflow,
        "needs": {"kind": kind, "questions": list(questions)},
        "requirements": requirements,
        "chat": {
            "messages": [
                {"id": "initial", "role": "user", "text": "x"},
                {"id": "a", "role": "assistant", "text": reply},
            ]
        },
        "usage": {"accounting": {"attempts": list(attempts)}},
        "run_metrics": {"by_job": [{"job": "Requirements", "spent_usd": spent, "complete": True}]},
    }


def attempt(seconds=10.0, calls=0, stage="requirements_gather"):
    return {
        "stage": stage,
        "duration_seconds": seconds,
        "tool_activity": {"observed_calls": calls},
        "tokens": {"input_tokens": 100, "output_tokens": 20},
    }


def handoff(job="build", clarity="clear", behaviors=("prints JSON",), tests=("--json prints one object",)):
    return {
        "handoff_ref": "abc",
        "intended_outcome": "x",
        "requirements": [{"id": f"R{i}", "text": t, "source_quote": "q"} for i, t in enumerate(behaviors)],
        "proposed_assumptions": [],
        "acceptance_tests": list(tests),
        "conflicts": [],
        "job": job,
        "clarity": clarity,
    }


class RunnerQuestionPrefixTests(unittest.TestCase):
    def test_the_probe_knows_the_questions_autocode_itself_adds(self):
        self.assertEqual(requirements_review.PREFIX, model_probe.REVIEW_QUESTION_PREFIX)
        self.assertEqual(requirements_chat.OUTCOME_PREFIX, model_probe.RUNNER_OUTCOME_PREFIX)


class ReportFromViewTests(unittest.TestCase):
    def test_a_clear_handoff_is_not_a_question_from_the_model(self):
        report, measured = model_probe.report_from_view(
            view(
                questions=[{"id": "requirements-review-1", "question": "Does this match?"}],
                reply="Here is what I understood.",
                requirements=handoff(),
                attempts=[attempt(30.5, 2)],
            )
        )
        self.assertEqual([], report["open_questions"])
        self.assertEqual(("build", "clear"), (report["job"], report["clarity"]))
        self.assertEqual(["prints JSON"], report["required_behaviors"])
        self.assertEqual(
            {"seconds": 30.5, "tool_calls": 2, "tokens": 120, "cost_usd": 0.01, "report_repairs": 0}, measured
        )
        self.assertTrue(probe.grade("R8", report, **measured)["passed"])

    def test_the_runners_outcome_question_counts_as_asking_but_not_as_the_models_wording(self):
        report, measured = model_probe.report_from_view(
            view(
                questions=[{"id": "outcome-abc", "question": "What would you like me to help you build?"}],
                reply="Hi! What can I do for you?",
                requirements=handoff(job="none", clarity="vague", behaviors=(), tests=()),
                attempts=[attempt(5, 0)],
            )
        )
        result = probe.grade("R1", report, **measured)
        self.assertTrue(result["asked"])
        self.assertTrue(result["passed"], result["failed"])

    def test_a_repaired_report_counts_against_the_model(self):
        report, measured = model_probe.report_from_view(
            view(reply="ok", requirements=handoff(job="review"), attempts=[attempt(), attempt()])
        )
        self.assertEqual(1, measured["report_repairs"])
        self.assertEqual(["first_report_accepted"], probe.grade("R6", report, **measured)["failed"])

    def test_no_report_fails_and_other_stages_are_not_counted(self):
        report, measured = model_probe.report_from_view(
            {"status": "PAUSED_INVALID_OUTPUT", "usage": {"accounting": {"attempts": [attempt(stage="draft_plan")]}}}
        )
        self.assertIsNone(report)
        self.assertIsNone(measured["seconds"])
        self.assertFalse(probe.grade("R8", report, **measured)["passed"])


class EstimateTests(unittest.TestCase):
    def test_estimate_counts_the_quick_and_full_passes(self):
        result = model_probe.estimate([("openai/gpt-5.6-sol", "medium"), ("x/unpriced-1", None)])
        self.assertEqual(6, result["quick_calls"])
        self.assertEqual(18, result["calls"])
        self.assertIsNotNone(result["models"][0]["max_usd"])
        self.assertIsNone(result["models"][1]["max_usd"])
        self.assertIsNone(result["max_usd"], "an unpriced model makes the total unknown, never zero")


def scripted(passing, cost=0.1):
    """A runner whose model passes the requests in ``passing``."""
    calls = []

    def run(model, level, request, **options):
        calls.append((model, level, request.id, options["cap_usd"]))
        return {"request": request.id, "passed": request.id in passing, "seconds": 20, "cost_usd": cost}

    return run, calls


class RunProbeTests(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        patcher = patch.dict("os.environ", {"XDG_CONFIG_HOME": home.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_model_that_fails_the_quick_pass_stops_there(self):
        run, calls = scripted({"R1", "R2"})
        outcome = model_probe.run_probe([("x/m-1", "low")], cap_usd=5, runner=run)
        self.assertEqual(["R1", "R2", "R8"], [call[2] for call in calls])
        self.assertFalse(outcome["ranking"][0]["qualified"])
        self.assertTrue(measured_routes.measured("requirements"))

    def test_a_passing_model_runs_all_nine_and_qualifies(self):
        run, calls = scripted({r.id for r in probe.REQUESTS})
        outcome = model_probe.run_probe([("x/m-1", "low")], cap_usd=5, runner=run)
        self.assertEqual(9, len(calls))
        self.assertEqual(["R1", "R2", "R8"], [call[2] for call in calls[:3]])
        self.assertTrue(outcome["ranking"][0]["qualified"])
        self.assertEqual(["x/m-1"], [e["model"] for e in measured_routes.entries("requirements")])

    def test_each_run_gets_what_is_left_and_the_cap_stops_the_probe(self):
        run, calls = scripted({r.id for r in probe.REQUESTS}, cost=0.5)
        outcome = model_probe.run_probe([("x/m-1", "low"), ("x/m-2", "low")], cap_usd=2, runner=run)
        self.assertEqual([2, 1.5, 1.0, 0.5], [call[3] for call in calls])
        self.assertEqual("the probe reached its $2.00 cap", outcome["stopped"])
        self.assertEqual([], outcome["results"], "a model cut off in its full pass is not recorded")
        self.assertEqual(["x/m-1", "x/m-2"], [row["model"] for row in outcome["unmeasured"]])
        self.assertFalse(measured_routes.measured("requirements"))

    def test_a_cap_is_required(self):
        with self.assertRaises(ValueError):
            model_probe.run_probe([("x/m-1", "low")], cap_usd=0, runner=scripted(set())[0])


class ProbeCliTests(unittest.TestCase):
    def test_without_a_cap_it_only_estimates(self):
        with patch.object(model_probe, "run_probe") as run, patch("sys.stdout") as out:
            self.assertEqual(0, probe_cli.cli(["--measure", "openai/gpt-5.6-sol", "medium", "--json"]))
        run.assert_not_called()
        printed = json.loads("".join(call.args[0] for call in out.write.call_args_list))
        self.assertFalse(printed["ran"])
        self.assertEqual(9, printed["estimate"]["calls"])

    def test_with_a_cap_it_runs_with_the_chosen_provider(self):
        outcome = {
            "results": [],
            "ranking": [],
            "spent_usd": 0,
            "unpriced_replies": 0,
            "stopped": None,
            "unmeasured": [],
        }
        with patch.object(model_probe, "run_probe", return_value=outcome) as run, patch("sys.stdout"):
            code = probe_cli.cli(
                [
                    "--measure",
                    "zai-coding-plan/glm-5.3",
                    "low",
                    "--measure",
                    "x/m",
                    "default",
                    "--max-cost-usd",
                    "3",
                    "--provider",
                    "kilocode",
                ]
            )
        self.assertEqual(0, code)
        run.assert_called_once_with(
            [("zai-coding-plan/glm-5.3", "low"), ("x/m", None)], cap_usd=3.0, options=("--provider", "kilocode")
        )


class ThroughTheCliTests(unittest.TestCase):
    """One request through the real `autocode` CLI, on the offline fixture provider."""

    def test_a_request_runs_as_a_spec_first_task_and_stops_after_requirements(self):
        root = Path(tempfile.mkdtemp(prefix="probe-cli-"))
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
        graded = model_probe.run_request("gpt-5.6-sol", "high", "R8", options=FIXTURE_OPTIONS, env=env, cap_usd=1)
        self.assertIsNone(graded["error"])
        self.assertEqual("WAITING_FOR_USER", graded["run_status"])
        self.assertEqual(0, graded["report_repairs"])
        self.assertIsInstance(graded["seconds"], float)
        self.assertIsInstance(graded["cost_usd"], float)
        self.assertIn("job", graded["checks"])


class FixtureWorkspaceTests(unittest.TestCase):
    def test_the_probe_project_is_a_committed_copy(self):
        with tempfile.TemporaryDirectory() as parent:
            workspace = model_probe.prepare_workspace(parent)
            status = subprocess.run(
                ["git", "-C", str(workspace), "status", "--porcelain"], capture_output=True, text=True, check=True
            )
            self.assertEqual("", status.stdout)
            self.assertTrue((workspace / "totals.py").is_file())


if __name__ == "__main__":
    unittest.main()
