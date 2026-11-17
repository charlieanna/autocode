"""The Planner probe: its requests, its grader and its runs through the task-run interface (#3630)."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_measured_routes as measured_routes
import autocode_model_probe as model_probe
import autocode_planner_probe as planner_probe
import autocode_route_selector as route_selector

from tests.test_taskrun import FIXTURE_OPTIONS, HERE


def plan(*, files=("totals.py",), deliverables="- A fix in totals.py.", criteria=None, milestones=1):
    return {
        "review_markdown": f"Fix it.\n\n## Deliverables\n\n{deliverables}\n\n## Required behaviors\n\n- x in other.py\n",
        "acceptance_criteria": criteria
        or [
            {"id": "AC1", "criterion": "average([]) returns 0.0", "verification_method": "test: test_empty"},
            {"id": "AC2", "criterion": "average([2, 4]) is 3.0", "verification_method": "guard: test_average"},
        ],
        "milestones": [{"id": f"M{n}", "objective": "x", "affected_paths": list(files)} for n in range(milestones)],
    }


def graded(request, shown, **measured):
    measured = {"reached_approval": True, "review_rounds": 1, "report_repairs": 0, "seconds": 300.0, **measured}
    return planner_probe.grade(request, shown, **measured)


class GradeTests(unittest.TestCase):
    def test_a_good_plan_passes(self):
        result = graded("P3", plan())
        self.assertTrue(result["passed"], result["failed"])
        self.assertEqual(["totals.py"], result["changed_files"])

    def test_a_plan_that_edits_the_forbidden_test_file_fails(self):
        result = graded("P5", plan(files=("totals.py", "tests/test_totals.py")))
        self.assertEqual(["keeps_forbidden_files"], result["failed"])

    def test_the_deliverables_count_as_changed_files(self):
        shown = plan(files=(), deliverables="- A fix in `totals.py`.\n- A new test in test_totals.py.")
        self.assertEqual(["keeps_forbidden_files"], graded("P5", shown)["failed"])
        self.assertTrue(graded("P3", shown)["passed"])

    def test_a_file_named_outside_the_deliverables_is_not_a_change(self):
        # "other.py" appears under Required behaviors only.
        self.assertNotIn("other.py", graded("P3", plan())["changed_files"])

    def test_escaped_markdown_deliverables_are_counted(self):
        review = plan(files=(), deliverables=r"- tests/\_\_init\_\_\.py")
        self.assertIn("__init__.py", planner_probe.changed_files(review))

    def test_a_criterion_checked_by_reading_fails_but_a_test_command_or_unchanged_file_does_not(self):
        criteria = [
            {"id": "AC1", "criterion": "returns 0.0", "verification_method": "test: test_empty"},
            {"id": "AC2", "criterion": "the suite passes", "verification_method": "python3 -m unittest -q"},
            {"id": "AC3", "criterion": "test_totals.py stays byte-for-byte unchanged", "verification_method": "diff"},
            {"id": "AC4", "criterion": "the code is tidy", "verification_method": "inspection of totals.py"},
        ]
        result = graded("P5", plan(criteria=criteria))
        self.assertEqual(["every_criterion_tested"], result["failed"])
        self.assertEqual(["AC4"], result["untested"])

    def test_p4_needs_the_cache_or_the_report_to_change(self):
        self.assertEqual(["changes_needed_files"], graded("P4", plan(files=("totals.py",)))["failed"])
        self.assertTrue(graded("P4", plan(files=("cache.py",)))["passed"])
        self.assertTrue(graded("P4", plan(files=("report.py",)))["passed"])

    def test_a_small_fix_gets_a_small_plan(self):
        self.assertEqual(["small_plan"], graded("P3", plan(milestones=3))["failed"])
        self.assertTrue(graded("P2", plan(files=("report.py",), milestones=3))["passed"])

    def test_repairs_review_rounds_and_time_count(self):
        self.assertEqual(["first_report_accepted"], graded("P3", plan(), report_repairs=1)["failed"])
        self.assertEqual(["first_report_accepted"], graded("P3", plan(), report_repairs=None)["failed"])
        self.assertEqual(["reviewer_approved"], graded("P3", plan(), review_rounds=3)["failed"])
        self.assertEqual(["time"], graded("P3", plan(), seconds=1801)["failed"])

    def test_no_plan_fails_everything(self):
        result = graded("P3", None, reached_approval=False)
        self.assertEqual(list(planner_probe.CHECKS), result["failed"])

    def test_the_requests_match_the_project(self):
        self.assertEqual(["P1"], [r.id for r in planner_probe.REQUESTS if r.broken_tax])
        self.assertTrue(set(planner_probe.QUICK) <= set(planner_probe.BY_ID))
        broken, _fixed = planner_probe.FIXED_TAX
        self.assertIn(broken, (model_probe.FIXTURE / "totals.py").read_text(encoding="utf-8"))


def attempt(stage, *, rejected=False, model="m"):
    return {"stage": stage, "rejected": rejected, "model": model, "duration_seconds": 10.0}


def view(attempts, *, kind="approve_plan", shown=None, planner_usd=0.2, spent=0.6):
    return {
        "status": "WAITING_FOR_USER",
        "needs": {"kind": kind},
        "displayed_plan": shown if shown is not None else plan(),
        "usage": {"accounting": {"attempts": list(attempts)}},
        "run_metrics": {
            "by_job": [{"job": "Planner", "spent_usd": planner_usd}, {"job": "Plan Reviewer", "spent_usd": 0.3}]
        },
        "cost": {"spent_usd": spent},
    }


class PlanFromViewTests(unittest.TestCase):
    def test_rounds_repairs_and_the_planners_own_cost(self):
        shown, measured = model_probe.plan_from_view(
            view(
                [
                    attempt("requirements_gather"),
                    attempt("draft_plan"),
                    attempt("draft_plan_report_repair"),
                    attempt("review_plan"),
                    attempt("revise_plan", rejected=True),
                    attempt("review_plan"),
                ]
            )
        )
        self.assertIsNotNone(shown)
        self.assertEqual({"reached_approval": True, "review_rounds": 2, "report_repairs": 2, "cost_usd": 0.2}, measured)

    def test_a_plan_not_waiting_for_approval_is_not_graded(self):
        shown, measured = model_probe.plan_from_view(view([attempt("draft_plan")], kind="answer"))
        self.assertIsNone(shown)
        self.assertFalse(measured["reached_approval"])
        self.assertIsNone(model_probe.plan_from_view({})[1]["report_repairs"], "no Planner attempt is unobserved")


def scripted(passing, cost=0.5):
    calls = []

    def run(model, level, request, **options):
        calls.append((model, request.id, options["checker"], options["requirements"]))
        return {
            "request": request.id,
            "passed": request.id in passing,
            "seconds": 200,
            "cost_usd": 0.1,
            "run_cost_usd": cost,
        }

    return run, calls


class RunPlannerProbeTests(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        patcher = patch.dict("os.environ", {"XDG_CONFIG_HOME": home.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_each_candidate_gets_the_first_checker_of_another_family(self):
        run, calls = scripted({r.id for r in planner_probe.REQUESTS})
        checkers = [("claude-opus-5-5", "high"), ("gpt-6.1-sol", "high")]
        outcome = model_probe.run_probe(
            [("gpt-6.1-sol", "high"), ("claude-opus-5-5", "medium"), ("gemini-3.8-flash", None)],
            cap_usd=50,
            stage="planner",
            checkers=checkers,
            requirements=("zai-coding-plan/glm-5.3", "low"),
            runner=run,
        )
        by_model = {model: checker for model, _request, checker, _req in calls}
        self.assertEqual(("claude-opus-5-5", "high"), by_model["gpt-6.1-sol"])
        self.assertEqual(("gpt-6.1-sol", "high"), by_model["claude-opus-5-5"])
        self.assertEqual(("claude-opus-5-5", "high"), by_model["gemini-3.8-flash"])
        self.assertEqual(["P2", "P5"], [request for model, request, *_ in calls if model == "gpt-6.1-sol"][:2])
        self.assertEqual(21, len(calls))
        self.assertTrue(all(row["qualified"] for row in outcome["ranking"]))
        self.assertEqual(10.5, outcome["spent_usd"], "the whole run's cost counts against the cap")
        self.assertEqual(
            {"gpt-6.1-sol", "claude-opus-5-5", "gemini-3.8-flash"},
            {entry["model"] for entry in route_selector.qualified("planner")},
            "the measured Planners are the ones selection and the quota fallback choose from",
        )

    def test_a_candidate_no_checker_may_review_is_not_measured(self):
        run, calls = scripted(set())
        outcome = model_probe.run_probe(
            [("claude-sonnet-5-5", "high")],
            cap_usd=5,
            stage="planner",
            checkers=[("claude-opus-5-5", "high")],
            runner=run,
        )
        self.assertEqual([], calls)
        self.assertEqual("no Plan Reviewer of another family", outcome["unmeasured"][0]["reason"])
        self.assertFalse(measured_routes.measured("planner"))

    def test_a_planner_probe_needs_a_checker(self):
        with self.assertRaises(ValueError):
            model_probe.run_probe([("x/m", None)], cap_usd=5, stage="planner", runner=scripted(set())[0])

    def test_failing_the_quick_pass_stops_the_model_and_does_not_qualify(self):
        run, calls = scripted({"P2"})
        outcome = model_probe.run_probe(
            [("glm-5", "low")], cap_usd=5, stage="planner", checkers=[("claude-opus-5-5", "high")], runner=run
        )
        self.assertEqual(["P2", "P5"], [request for _m, request, *_ in calls])
        self.assertFalse(outcome["ranking"][0]["qualified"])
        self.assertEqual((), route_selector.qualified("planner"), "no measured Planner qualified: the person chooses")


class WorkspaceTests(unittest.TestCase):
    def test_the_fixed_copy_has_no_tax_bug(self):
        with tempfile.TemporaryDirectory() as parent:
            text = (model_probe.prepare_workspace(parent, fixed_tax=True) / "totals.py").read_text(encoding="utf-8")
        self.assertIn(planner_probe.FIXED_TAX[1], text)
        self.assertNotIn(planner_probe.FIXED_TAX[0] + "\n", text)


class FakeRun:
    """Scripted views for the probe's way past Requirements; records what it answered."""

    def __init__(self, views):
        self.views = list(views)
        self.answers = []

    def answer(self, question_id, text, *, resolver_token=None):
        self.answers.append({question_id: text})

    def answer_many(self, answers, *, resolver_token=None):
        self.answers.append(dict(answers))

    def advance_until_input(self):
        return self.views.pop(0)


def asking(*questions, stage="requirements_gather", scope="clarification"):
    return {
        "needs": {"kind": "answer", "resolver_scope": scope, "resolver_token": "t", "questions": list(questions)},
        "usage": {"accounting": {"attempts": [{"stage": stage}]}},
    }


class ThroughRequirementsTests(unittest.TestCase):
    TEXT = planner_probe.BY_ID["P3"].text

    def test_the_confirmation_is_plan_and_requirements_questions_take_its_default(self):
        approval = {"needs": {"kind": "approve_plan"}}
        run = FakeRun(
            [
                asking(
                    {"id": "outcome-1", "question": "What would you like?"},
                    {"id": "q-format", "question": "Rounding?", "proposed_default": "two decimals"},
                    {"id": "q-keys", "question": "Keys?", "options": ["total, average"]},
                    {"id": "q-open", "question": "Anything else?"},
                ),
                approval,
            ]
        )
        first = asking({"id": "requirements-review-1", "question": "Does this match?"})
        self.assertIs(approval, model_probe._through_requirements(run, first, self.TEXT))
        self.assertEqual(
            [
                {"requirements-review-1": "plan"},
                {
                    "outcome-1": self.TEXT,
                    "q-format": "two decimals",
                    "q-keys": "total, average",
                    "q-open": model_probe.DEFAULT_ANSWER,
                },
            ],
            run.answers,
        )

    def test_a_planner_question_is_left_for_the_grade(self):
        run = FakeRun([])
        question = asking({"id": "q1", "question": "Which currency codes?"}, stage="draft_plan")
        self.assertIs(question, model_probe._through_requirements(run, question, self.TEXT))
        self.assertEqual([], run.answers)

    def test_it_stops_answering_after_a_bounded_number_of_rounds(self):
        again = asking({"id": "outcome-2", "question": "What would you like?"})
        run = FakeRun([again] * model_probe.REQUIREMENTS_ANSWER_ROUNDS)
        self.assertIs(again, model_probe._through_requirements(run, again, self.TEXT))
        self.assertEqual(model_probe.REQUIREMENTS_ANSWER_ROUNDS, len(run.answers))


class ThroughTheCliTests(unittest.TestCase):
    """One Planner request through the real `autocode` CLI, on the offline fixture provider.

    The fixture provider's Requirements never names an outcome on a spec-first run, so the run cannot
    reach a plan here; this proves the probe's flags start a real run, the probe answers through the
    public interface, and a run that shows no plan is graded as no plan, not as an error.
    """

    def test_a_request_starts_a_real_run_and_a_run_without_a_plan_fails_every_check(self):
        root = Path(tempfile.mkdtemp(prefix="planner-probe-cli-"))
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
        result = model_probe.run_planner_request(
            "gpt-5.6-sol", "high", "P3", checker=("gpt-6-astra", None), options=FIXTURE_OPTIONS, env=env, cap_usd=1
        )
        self.assertIsNone(result["error"])
        self.assertEqual("WAITING_FOR_USER", result["run_status"])
        self.assertEqual(list(planner_probe.CHECKS), result["failed"])
        self.assertIsInstance(result["run_cost_usd"], float)
        self.assertEqual(["gpt-6-astra", None], result["checker"])


if __name__ == "__main__":
    unittest.main()
