"""`autocode probe` holds the person's cap exactly in every stage (#3855, #3856, #3857).

Every runner here is scripted, in the pattern of tests/test_model_probe*.py: no model is called.
"""

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_measured_routes as measured_routes
import autocode_model_probe as model_probe
import autocode_plan_file as plan_file
import autocode_plan_reviewer_probe as reviewer_probe
import autocode_probe_cli as probe_cli
import autocode_requirements_probe as requirements_probe

from tests.test_plan_reviewer_probe import APPROVED, BLOCKED, contract
from tests.test_plan_reviewer_probe import scripted as scripted_reviews

PLANNER = ("gpt-6.1-sol", "high")
CHECKERS = [("claude-opus-5-5", "high")]


def scripted(cost, *, failing=(), statuses=None, key="cost_usd"):
    """A runner whose every reply passes (but those in ``failing``) and reports ``cost(request_id)`` under ``key``."""
    calls = []

    def run(model, level, request, **options):
        calls.append((model, request.id, options["cap_usd"]))
        value = cost(request.id) if callable(cost) else cost
        return {
            "request": request.id,
            "passed": request.id not in failing,
            "seconds": 20,
            key: value,
            "run_status": (statuses or {}).get(request.id),
        }

    return run, calls


def reviews(spent):
    """A Plan Reviewer runner whose every request reaches approval, passes and reports ``spent``."""
    calls = []

    def run(request, candidates, **kwargs):
        calls.append((request.id, kwargs["cap_usd"]))
        grades = {
            key: reviewer_probe.grade(request, APPROVED, BLOCKED, seconds=(60, 60), cost_usd=0.25) for key in candidates
        }
        return {"request": request.id, "source": {"reached_approval": True}, "grades": grades, "spent_usd": spent}

    return run, calls


def stored(role, model, effort):
    return [
        row
        for row in measured_routes.load()["roles"][role]["results"]
        if row["model"] == model and row["effort"] == effort
    ]


def partial(role):
    return measured_routes.load()["roles"][role]["partial"]


class ProbeBudgetTests(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory()
        self.addCleanup(home.cleanup)
        patcher = patch.dict("os.environ", {"XDG_CONFIG_HOME": home.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_ac1_unpriced_reply_is_charged_its_allowance_and_stops_the_probe(self):
        run, calls = scripted(None)
        outcome = model_probe.run_probe([("x/m-1", "low"), ("x/m-2", "low")], cap_usd=2, runner=run)
        self.assertEqual([("x/m-1", "R1", 2.0)], calls)
        self.assertEqual(2.0, outcome["spent_usd"])
        self.assertEqual(1, outcome["unpriced_replies"])
        self.assertIs(True, outcome["spend_uncertain"])
        self.assertEqual(
            "the probe reached its $2.00 cap: a reply had no price on record and was charged its whole $2.00 allowance",
            outcome["stopped"],
        )
        self.assertEqual([], outcome["results"])
        self.assertEqual(["x/m-1", "x/m-2"], [row["model"] for row in outcome["unmeasured"]])

    def test_ac2_every_stage_charges_an_unpriced_reply(self):
        stages = {
            "planner": {"checkers": CHECKERS},
            "builder": {"planner": PLANNER, "checkers": CHECKERS},
            "tester": {},
            "completion": {},
        }
        for stage, extra in stages.items():
            with self.subTest(stage=stage):
                run, calls = scripted(None, key="run_cost_usd")
                outcome = model_probe.run_probe([("glm-5", "low")], cap_usd=3, stage=stage, runner=run, **extra)
                self.assertEqual([3.0], [cap for _model, _request, cap in calls])
                self.assertEqual(3.0, outcome["spent_usd"])
                self.assertEqual(1, outcome["unpriced_replies"])
                self.assertIs(True, outcome["spend_uncertain"])

    def test_ac3_plan_reviewer_charges_an_unpriced_request(self):
        run, calls = reviews(None)
        outcome = model_probe.run_reviewer_probe(
            [("claude-opus-5-5", "medium")], cap_usd=3, planner=PLANNER, runner=run
        )
        self.assertEqual([("P4", 3.0)], calls)
        self.assertEqual(3.0, outcome["spent_usd"])
        self.assertEqual(1, outcome["unpriced_replies"])
        self.assertIs(True, outcome["spend_uncertain"])
        self.assertEqual(
            "the probe reached its $3.00 cap: a reply had no price on record and was charged its whole $3.00 allowance",
            outcome["stopped"],
        )

    def test_ac4_known_charges_count_and_a_nan_cost_is_unknown(self):
        run, calls = scripted(lambda request: 0.5 if request == "R1" else None)
        outcome = model_probe.run_probe([("x/m-1", "low")], cap_usd=2, runner=run)
        self.assertEqual([2.0, 1.5], [cap for _model, _request, cap in calls])
        self.assertEqual(2.0, outcome["spent_usd"])
        self.assertEqual(1, outcome["unpriced_replies"])

        run, calls = scripted(float("nan"))
        outcome = model_probe.run_probe([("x/m-1", "low")], cap_usd=2, runner=run)
        self.assertEqual(1, len(calls))
        self.assertEqual(2.0, outcome["spent_usd"])
        self.assertEqual(1, outcome["unpriced_replies"])
        self.assertIs(True, outcome["spend_uncertain"])

    def test_ac6_summary_says_spend_is_uncertain(self):
        run, _calls = scripted(None)
        argv = ["--measure", "x/m-1", "low", "--max-cost-usd", "2"]
        out = io.StringIO()
        with patch.object(model_probe, "run_request", run), contextlib.redirect_stdout(out):
            self.assertEqual(0, probe_cli.cli(argv))
        text = out.getvalue()
        self.assertEqual("Spent at most $2.00; spend is uncertain.", text.splitlines()[0])
        self.assertIn("1 reply had no price on record and was counted at its whole allowance.", text.splitlines())
        self.assertNotIn("not counted", text)

        out = io.StringIO()
        with patch.object(model_probe, "run_request", run), contextlib.redirect_stdout(out):
            self.assertEqual(0, probe_cli.cli([*argv, "--json"]))
        printed = json.loads(out.getvalue())
        self.assertIs(True, printed["spend_uncertain"])
        self.assertEqual(2.0, printed["spent_usd"])

    def test_ac7_cli_rejects_a_non_finite_or_non_positive_cap(self):
        outcome = {
            "results": [],
            "ranking": [],
            "spent_usd": 0,
            "unpriced_replies": 0,
            "stopped": None,
            "unmeasured": [],
        }
        for value in ("nan", "NaN", "inf", "Infinity", "-inf", "1e400", "-1", "0", "abc"):
            with self.subTest(value=value):
                err = io.StringIO()
                with (
                    patch.object(model_probe, "run_probe", return_value=outcome) as run,
                    contextlib.redirect_stdout(io.StringIO()),
                    contextlib.redirect_stderr(err),
                ):
                    try:
                        code = probe_cli.cli(["--measure", "x/m", "low", "--max-cost-usd", value])
                    except SystemExit as stop:
                        code = stop.code
                self.assertEqual(2, code)
                self.assertIn("--max-cost-usd", err.getvalue())
                run.assert_not_called()
        with (
            patch.object(model_probe, "run_probe", return_value=outcome) as run,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(0, probe_cli.cli(["--measure", "x/m", "low", "--max-cost-usd", "0.5"]))
        run.assert_called_once()
        self.assertEqual(0.5, run.call_args.kwargs["cap_usd"])

    def test_ac8_probe_functions_reject_a_non_finite_cap(self):
        run, calls = scripted(0.1)
        for cap in (float("nan"), float("inf")):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                model_probe.run_probe([("x/m-1", "low")], cap_usd=cap, runner=run)
        reviewer, reviewed = reviews(0.1)
        with self.assertRaises(ValueError):
            model_probe.run_reviewer_probe(
                [("claude-opus-5-5", "medium")], cap_usd=float("nan"), planner=PLANNER, runner=reviewer
            )
        self.assertEqual([], calls)
        self.assertEqual([], reviewed)

    def test_ac9_child_cap_rounds_down_and_refuses_below_a_cent(self):
        def cap_text(flags):
            return flags[flags.index("--max-cost-usd") + 1]

        for cap, text in ((0.016, "0.01"), (0.019, "0.01"), (0.29, "0.29"), (1.0, "1.00")):
            with self.subTest(cap=cap):
                self.assertEqual(text, cap_text(model_probe._planner_flags(("x/p", None), ("y/c", None), None, cap)))
        self.assertEqual("0.01", cap_text(model_probe._check_flags("tester", ("x/m", None), 0.016)[0]))
        with self.assertRaises(ValueError):
            model_probe._planner_flags(("x/p", None), ("y/c", None), None, 0.004)
        with self.assertRaises(ValueError):
            model_probe._check_flags("completion", ("x/m", None), 0.009)
        with (
            patch.object(
                model_probe.taskrun.TaskRun, "start", side_effect=AssertionError("a run was started")
            ) as start,
            self.assertRaises(ValueError),
        ):
            model_probe.run_request("x/m", "low", "R8", cap_usd=0.004)
        start.assert_not_called()

    def test_ac10_probe_launches_no_run_below_a_cent(self):
        run, calls = scripted(0.01)
        outcome = model_probe.run_probe([("x/m-1", "low")], cap_usd=0.016, runner=run)
        self.assertEqual([0.01], [cap for _model, _request, cap in calls])
        self.assertEqual(0.01, outcome["spent_usd"])
        self.assertEqual(
            "the probe reached its $0.016 cap: $0.006 remains, less than the $0.01 a run accepts", outcome["stopped"]
        )

    def test_ac11_budget_stop_keeps_the_complete_qualification(self):
        run, _calls = scripted(0.1)
        model_probe.run_probe([("x/m-1", "low")], cap_usd=50, runner=run)
        for cap, requests in ((0.2, ["R1", "R2"]), (0.1, ["R1"])):
            with self.subTest(cap=cap):
                run, calls = scripted(0.1)
                outcome = model_probe.run_probe([("x/m-1", "low")], cap_usd=cap, runner=run)
                self.assertEqual(requests, [request for _model, request, _cap in calls])
                self.assertEqual([], outcome["results"])
                entries = measured_routes.entries("requirements")
                self.assertEqual(["x/m-1"], [entry["model"] for entry in entries])
                self.assertIn("9/9 requests at low", entries[0]["evidence"])
                self.assertEqual([9], [row["summary"]["total"] for row in stored("requirements", "x/m-1", "low")])
                attempts = partial("requirements")
                self.assertEqual([("x/m-1", "low")], [(row["model"], row["effort"]) for row in attempts])
                self.assertEqual(len(requests), attempts[0]["summary"]["total"])

    def test_ac12_plan_reviewer_budget_stop_keeps_the_qualification(self):
        candidate = [("claude-opus-5-5", "medium")]
        run, _calls = scripted_reviews({"claude-opus-5-5"})
        model_probe.run_reviewer_probe(candidate, cap_usd=50, planner=PLANNER, runner=run)

        run, calls = scripted_reviews({"claude-opus-5-5"})
        model_probe.run_reviewer_probe(candidate, cap_usd=2, planner=PLANNER, runner=run)
        self.assertEqual(["P4"], [request for request, *_ in calls])
        entries = measured_routes.entries("plan_reviewer")
        self.assertEqual(["claude-opus-5-5"], [entry["model"] for entry in entries])
        self.assertIn("7/7 requests at medium", entries[0]["evidence"])
        attempts = partial("plan_reviewer")
        self.assertEqual([("claude-opus-5-5", "medium")], [(row["model"], row["effort"]) for row in attempts])
        self.assertEqual(1, attempts[0]["summary"]["total"])

        capped = []

        def paused(request, candidates, **kwargs):
            capped.append(request.id)
            return {
                "request": request.id,
                "source": {"reached_approval": False, "run_status": "PAUSED_COST_LIMIT"},
                "grades": {},
                "spent_usd": 1.0,
            }

        outcome = model_probe.run_reviewer_probe(candidate, cap_usd=5, planner=PLANNER, runner=paused)
        self.assertEqual(["P4"], capped)
        self.assertEqual("a run stopped at its $5.00 cost cap", outcome["stopped"])
        entries = measured_routes.entries("plan_reviewer")
        self.assertEqual(["claude-opus-5-5"], [entry["model"] for entry in entries])
        self.assertIn("7/7 requests at medium", entries[0]["evidence"])

    def test_a_reviewer_run_paused_at_its_cost_cap_keeps_every_candidate_it_reviewed_partial(self):
        candidates = [("claude-opus-5-5", "medium"), ("claude-sonnet-5-5", "medium")]
        reviewed, _calls = scripted_reviews({"claude-sonnet-5-5"})  # opus false-blocks: it fails its quick pass
        calls = []

        def run(request, active, **kwargs):
            calls.append(request.id)
            outcome = reviewed(request, active, **kwargs)
            if request.id == "P5":
                outcome["run_status"] = "PAUSED_COST_LIMIT"
            return outcome

        outcome = model_probe.run_reviewer_probe(candidates, cap_usd=50, planner=PLANNER, runner=run)
        self.assertEqual(["P4", "P5"], calls)
        self.assertEqual("a run stopped at its $48.00 cost cap", outcome["stopped"])
        self.assertEqual([], outcome["results"])
        self.assertEqual(
            [("claude-opus-5-5", 2), ("claude-sonnet-5-5", 2)],
            [(row["model"], row["summary"]["total"]) for row in outcome["partial"]],
        )

    def test_a_candidate_that_failed_its_quick_pass_is_a_result_when_the_cap_stops_a_later_request(self):
        candidates = [("claude-opus-5-5", "medium"), ("claude-sonnet-5-5", "medium")]
        run, _calls = scripted_reviews({"claude-opus-5-5"})
        model_probe.run_reviewer_probe(candidates[:1], cap_usd=50, planner=PLANNER, runner=run)
        self.assertEqual([7], [row["summary"]["total"] for row in stored("plan_reviewer", *candidates[0])])

        run, calls = scripted_reviews({"claude-sonnet-5-5"})  # opus false-blocks: it fails its quick pass
        outcome = model_probe.run_reviewer_probe(candidates, cap_usd=6, planner=PLANNER, runner=run)
        self.assertEqual(["P4", "P5", "P1"], [request for request, *_ in calls])
        self.assertEqual("the probe reached its $6.00 cap", outcome["stopped"])
        self.assertEqual(
            [("claude-opus-5-5", 2)], [(row["model"], row["summary"]["total"]) for row in outcome["results"]]
        )
        self.assertEqual(
            [("claude-sonnet-5-5", 3)], [(row["model"], row["summary"]["total"]) for row in outcome["partial"]]
        )
        self.assertEqual([("claude-opus-5-5", False)], [(row["model"], row["qualified"]) for row in outcome["ranking"]])
        self.assertEqual(
            [2], [row["summary"]["total"] for row in stored("plan_reviewer", *candidates[0])], "the failure counts"
        )
        self.assertEqual([], measured_routes.entries("plan_reviewer"))

    def test_ac13_run_cost_cap_stop_keeps_the_qualification(self):
        run, _calls = scripted(0.1, key="run_cost_usd")
        model_probe.run_probe([("claude-opus-5-5", "medium")], cap_usd=50, stage="tester", runner=run)

        run, calls = scripted(0.1, key="run_cost_usd", failing={"K2"}, statuses={"K2": "PAUSED_COST_LIMIT"})
        outcome = model_probe.run_probe([("claude-opus-5-5", "medium")], cap_usd=5, stage="tester", runner=run)
        self.assertEqual([("K1", 5.0), ("K2", 4.9)], [(request, cap) for _model, request, cap in calls])
        self.assertEqual("a run stopped at its $4.90 cost cap", outcome["stopped"])
        entries = measured_routes.entries("tester")
        self.assertEqual(["claude-opus-5-5"], [entry["model"] for entry in entries])
        self.assertIn("5/5 requests at medium", entries[0]["evidence"])
        attempts = partial("tester")
        self.assertEqual([("claude-opus-5-5", "medium")], [(row["model"], row["effort"]) for row in attempts])
        self.assertEqual(2, attempts[0]["summary"]["total"])

    def test_ac14_genuine_quick_failure_still_replaces_the_qualification(self):
        run, _calls = scripted(0.1)
        model_probe.run_probe([("x/m-1", "low")], cap_usd=50, runner=run)
        self.assertEqual([9], [row["summary"]["total"] for row in stored("requirements", "x/m-1", "low")])

        run, calls = scripted(0.1, failing={"R2"}, statuses={"R2": "WAITING_FOR_USER"})
        outcome = model_probe.run_probe([("x/m-1", "low")], cap_usd=5, runner=run)
        self.assertEqual(["R1", "R2", "R8"], [request for _model, request, _cap in calls])
        self.assertEqual(
            [(2, 3)],
            [(row["summary"]["passed"], row["summary"]["total"]) for row in stored("requirements", "x/m-1", "low")],
        )
        self.assertEqual([], measured_routes.entries("requirements"))
        self.assertFalse(outcome["ranking"][0]["qualified"])
        self.assertEqual(len(requirements_probe.QUICK), len(calls))

    def test_a_negative_or_unusable_amount_is_unknown(self):
        run, calls = scripted(-0.5)
        outcome = model_probe.run_probe([("x/m-1", "low")], cap_usd=1, runner=run)
        self.assertEqual(1, len(calls), "a negative cost is no price: the reply is charged its whole allowance")
        self.assertEqual((1.0, 1), (outcome["spent_usd"], outcome["unpriced_replies"]))
        for cap in (-1, True, "2"):
            with self.subTest(cap=cap), self.assertRaises(ValueError):
                model_probe.run_probe([("x/m-1", "low")], cap_usd=cap, runner=run)
        for cap in (float("nan"), -0.5):
            with self.subTest(child_cap=cap), self.assertRaises(ValueError):
                model_probe._planner_flags(("x/p", None), ("y/c", None), None, cap)

    def test_a_reviewer_run_paused_at_its_cost_cap_ends_the_probe(self):
        calls = []

        def run(request, candidates, **kwargs):
            calls.append(request.id)
            grades = {key: reviewer_probe.grade(request, APPROVED, BLOCKED, seconds=(60, 60)) for key in candidates}
            return {
                "request": request.id,
                "source": {"reached_approval": True, "run_status": "WAITING_FOR_USER"},
                "grades": grades,
                "spent_usd": 0.5,
                "run_status": "PAUSED_COST_LIMIT",
            }

        outcome = model_probe.run_reviewer_probe(
            [("claude-opus-5-5", "medium")], cap_usd=5, planner=PLANNER, runner=run
        )
        self.assertEqual(["P4"], calls)
        self.assertEqual("a run stopped at its $5.00 cost cap", outcome["stopped"])
        self.assertEqual([], outcome["results"])
        self.assertEqual([1], [row["summary"]["total"] for row in outcome["partial"]])

    def test_a_reviewer_run_paused_at_its_cost_cap_on_the_last_request_is_a_partial_attempt(self):
        last = model_probe._order(reviewer_probe)[-1].id
        reviewed, _calls = scripted_reviews({"claude-opus-5-5"})
        calls = []

        def run(request, candidates, **kwargs):
            calls.append(request.id)
            if request.id != last:
                return reviewed(request, candidates, **kwargs)
            return {
                "request": request.id,
                "source": {"reached_approval": False, "run_status": "PAUSED_COST_LIMIT"},
                "grades": {},
                "spent_usd": 0.1,
            }

        outcome = model_probe.run_reviewer_probe(
            [("claude-opus-5-5", "medium")], cap_usd=50, planner=PLANNER, runner=run
        )
        self.assertEqual("P7", last)
        self.assertEqual([request.id for request in model_probe._order(reviewer_probe)], calls)
        self.assertEqual(f"a run stopped at its ${50 - 6 * 2.0:.2f} cost cap", outcome["stopped"])
        self.assertEqual([], outcome["results"])
        self.assertEqual(
            [("claude-opus-5-5", "medium", 6)],
            [(row["model"], row["effort"], row["summary"]["total"]) for row in outcome["partial"]],
        )
        self.assertEqual(["claude-opus-5-5"], [row["model"] for row in outcome["unmeasured"]])
        self.assertEqual([], measured_routes.load()["roles"]["plan_reviewer"]["results"])

    def test_a_first_request_below_a_cent_launches_nothing_and_says_what_remains(self):
        run, calls = scripted(0.1)
        outcome = model_probe.run_probe([("x/m-1", "low")], cap_usd=0.005, runner=run)
        self.assertEqual([], calls)
        self.assertEqual(
            "the probe reached its $0.005 cap: $0.005 remains, less than the $0.01 a run accepts", outcome["stopped"]
        )
        self.assertEqual(
            (0.0, 0, False), (outcome["spent_usd"], outcome["unpriced_replies"], outcome["spend_uncertain"])
        )
        self.assertEqual([], outcome["results"])
        self.assertEqual(["x/m-1"], [row["model"] for row in outcome["unmeasured"]])

    def test_the_summary_reads_older_outcomes_and_names_partial_attempts(self):
        outcome = {
            "results": [],
            "ranking": [],
            "spent_usd": 1.5,
            "unpriced_replies": 2,
            "stopped": None,
            "unmeasured": [],
            "partial": [
                {"model": "x/m", "effort": None, "summary": {"total": 2}},
                {"model": "x/n", "effort": "low", "summary": {"total": 1}},
            ],
        }
        out = io.StringIO()
        with patch.object(model_probe, "run_probe", return_value=outcome), contextlib.redirect_stdout(out):
            self.assertEqual(0, probe_cli.cli(["--measure", "x/m", "default", "--max-cost-usd", "3"]))
        lines = out.getvalue().splitlines()
        self.assertEqual("Spent at most $1.50; spend is uncertain.", lines[0])
        self.assertEqual("2 replies had no price on record and were counted at their whole allowance.", lines[1])
        self.assertIn(
            "  partial attempt kept apart: x/m at its default level, 2 requests before the stop;"
            " its earlier result, if any, is kept",
            lines,
        )
        self.assertIn(
            "  partial attempt kept apart: x/n at low, 1 request before the stop; its earlier result, if any, is kept",
            lines,
        )

    def test_a_free_reply_is_a_known_charge_and_spend_is_certain(self):
        run, calls = scripted(0.0)
        outcome = model_probe.run_probe([("x/m-1", "low"), ("x/m-2", "low")], cap_usd=1, runner=run)
        self.assertEqual(2 * len(requirements_probe.REQUESTS), len(calls))
        self.assertEqual({1.0}, {cap for _model, _request, cap in calls}, "a reply that cost nothing spends nothing")
        self.assertEqual(
            (0.0, 0, False, None),
            tuple(outcome[key] for key in ("spent_usd", "unpriced_replies", "spend_uncertain", "stopped")),
        )
        self.assertEqual(["x/m-1", "x/m-2"], [row["model"] for row in outcome["results"]])
        self.assertEqual(([], []), (outcome["unmeasured"], outcome["partial"]))

    def test_the_summary_states_a_certain_spend_plainly(self):
        run, _calls = scripted(0.25)
        argv = ["--measure", "x/m-1", "low", "--max-cost-usd", "5"]
        out = io.StringIO()
        with patch.object(model_probe, "run_request", run), contextlib.redirect_stdout(out):
            self.assertEqual(0, probe_cli.cli(argv))
        self.assertEqual("Spent $2.25.", out.getvalue().splitlines()[0])
        self.assertNotIn("no price on record", out.getvalue())
        out = io.StringIO()
        with patch.object(model_probe, "run_request", run), contextlib.redirect_stdout(out):
            self.assertEqual(0, probe_cli.cli([*argv, "--json"]))
        printed = json.loads(out.getvalue())
        self.assertEqual(
            (False, 2.25, 0), (printed["spend_uncertain"], printed["spent_usd"], printed["unpriced_replies"])
        )

    def test_a_zero_cap_is_refused_by_every_probe(self):
        run, calls = scripted(0.1)
        with self.assertRaises(ValueError):
            model_probe.run_probe([("x/m-1", "low")], cap_usd=0, runner=run)
        reviewer, reviewed = reviews(0.1)
        with self.assertRaises(ValueError):
            model_probe.run_reviewer_probe(
                [("claude-opus-5-5", "medium")], cap_usd=0.0, planner=PLANNER, runner=reviewer
            )
        self.assertEqual(([], []), (calls, reviewed))

    def test_a_cap_spent_to_the_cent_gives_no_remainder_reason(self):
        run, calls = scripted(0.25, key="run_cost_usd")
        outcome = model_probe.run_probe([("claude-opus-5-5", "medium")], cap_usd=1, stage="tester", runner=run)
        self.assertEqual([1.0, 0.75, 0.5, 0.25], [cap for _model, _request, cap in calls])
        self.assertEqual("the probe reached its $1.00 cap", outcome["stopped"])
        self.assertEqual((1.0, False), (outcome["spent_usd"], outcome["spend_uncertain"]))

    def test_an_unapproved_plan_is_skipped_with_its_source_and_its_charge_counts(self):
        run, _calls = scripted_reviews({"claude-opus-5-5"}, unreached={"P3"})
        outcome = model_probe.run_reviewer_probe(
            [("claude-opus-5-5", "medium")], cap_usd=50, planner=PLANNER, runner=run
        )
        self.assertEqual(
            [{"request": "P3", "reached_approval": False, "run_status": "WAITING_FOR_USER"}], outcome["skipped"]
        )
        self.assertEqual(6 * 2.0 + 1.0, outcome["spent_usd"])
        self.assertEqual([6], [row["summary"]["total"] for row in outcome["results"]])
        self.assertEqual([], outcome["partial"])

    def test_record_keeps_each_models_latest_partial_attempt_apart(self):
        for model, cap in (("x/m-1", 0.1), ("x/m-2", 0.2), ("x/m-1", 0.3)):
            run, _calls = scripted(0.1)
            outcome = model_probe.run_probe([(model, "low")], cap_usd=cap, runner=run)
        self.assertEqual([("x/m-1", 3)], [(row["model"], row["summary"]["total"]) for row in outcome["partial"]])
        self.assertEqual(
            [("x/m-1", 3), ("x/m-2", 2)],
            [(row["model"], row["summary"]["total"]) for row in partial("requirements")],
            "another model's partial attempt is kept; the same model's is replaced by its latest",
        )
        self.assertEqual([], measured_routes.load()["roles"]["requirements"]["results"])

        before = measured_routes.path().read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            measured_routes.record("requirements", [], partial=[{"model": "x/m-3", "effort": "low"}])
        self.assertEqual(before, measured_routes.path().read_text(encoding="utf-8"))

        document = measured_routes.load()
        document["roles"]["requirements"]["probe"] = "an older probe"
        measured_routes.path().write_text(json.dumps(document), encoding="utf-8")
        run, _calls = scripted(0.1)
        model_probe.run_probe([("x/m-2", "low")], cap_usd=0.1, runner=run)
        self.assertEqual(
            [("x/m-2", 1)],
            [(row["model"], row["summary"]["total"]) for row in partial("requirements")],
            "an older probe version's partial attempts are dropped",
        )


class ReviewerRequestStatusTests(unittest.TestCase):
    """run_reviewer_request reports its run's last status, so the probe sees a review that paused at its cap."""

    class Run:
        def __init__(self, status):
            self.final = status

        def export_plan(self, path):
            Path(path).write_text(plan_file.export(contract(), revision=3, approved=False), encoding="utf-8")

        def status(self):
            return {"status": self.final, "cost": {"spent_usd": 0.5}, "routes": {}, "planning_calls": {"used": 1}}

        def set_stage_model(self, *_args):
            pass

        def load_plan_file(self, _path):
            pass

        def advance_with_planning_cap(self, _cap):
            return {"plan_review": APPROVED, "cost": {"spent_usd": 0.5}}

    def request(self, run, view):
        return model_probe.run_reviewer_request(
            "P5",
            [("claude-opus-5-5", "medium")],
            planner=PLANNER,
            command=(sys.executable, "-c", "pass"),
            source=lambda _workspace, _request: (run, view),
        )

    def test_a_reviewed_plan_reports_the_runs_last_status(self):
        outcome = self.request(
            self.Run("PAUSED_COST_LIMIT"), {"status": "WAITING_FOR_USER", "needs": {"kind": "approve_plan"}}
        )
        self.assertEqual("PAUSED_COST_LIMIT", outcome["run_status"])
        self.assertEqual(0.5, outcome["spent_usd"])
        self.assertEqual([("claude-opus-5-5", "medium")], list(outcome["grades"]))

    def test_a_plan_that_never_reached_approval_reports_its_status(self):
        outcome = self.request(None, {"status": "PAUSED_COST_LIMIT", "needs": {}})
        self.assertEqual("PAUSED_COST_LIMIT", outcome["run_status"])
        self.assertEqual({}, outcome["grades"])

    def test_a_plan_that_never_reached_approval_returns_its_request_source_and_spend(self):
        view = {"status": "PAUSED_COST_LIMIT", "needs": {}, "cost": {"spent_usd": 0.75}}
        self.assertEqual(
            {
                "request": "P5",
                "source": {"reached_approval": False, "run_status": "PAUSED_COST_LIMIT", "error": None},
                "grades": {},
                "spent_usd": 0.75,
                "run_status": "PAUSED_COST_LIMIT",
            },
            self.request(None, view),
        )

    def test_a_reviewed_plan_returns_its_request_source_and_spend(self):
        outcome = self.request(
            self.Run("WAITING_FOR_USER"), {"status": "WAITING_FOR_USER", "needs": {"kind": "approve_plan"}}
        )
        self.assertEqual("P5", outcome["request"])
        self.assertEqual({"reached_approval": True, "run_status": "WAITING_FOR_USER", "error": None}, outcome["source"])
        self.assertEqual((0.5, "WAITING_FOR_USER"), (outcome["spent_usd"], outcome["run_status"]))


if __name__ == "__main__":
    unittest.main()
