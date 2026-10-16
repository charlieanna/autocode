"""Each escalation with what its escalated attempts cost and how it ended (#612): the pure derivation."""

import copy
import datetime as dt
import json
import unittest
from pathlib import Path

import autocode_builder_policy as policy
import autocode_checkpoint_continuation as continuation
import autocode_cost as cost
import autocode_escalation as escalation
import autocode_escalation_view as escalation_view
import autocode_prices as prices
import autocode_route_ladder as route_ladder
import autocode_run_view as run_view
import autocode_util as util

from . import test_checkpoint_continuation as continuation_tests

START = dt.datetime(2026, 10, 16, 9, 0, tzinfo=dt.UTC)
# What one fixture attempt costs on GPT-6 Sol at the dated table: 10 input and 10 output tokens.
SOL = round(prices.estimate("gpt-6-sol", {"input_tokens": 10, "output_tokens": 10}), 6)
HIGH = {"model": "gpt-6-sol", "reasoning_effort": "high", "profile": "GPT-6 Sol High"}
XHIGH = {"model": "gpt-6-sol", "reasoning_effort": "xhigh", "profile": "GPT-6 Sol XHigh"}
MAX = {"model": "gpt-6-sol", "reasoning_effort": "max", "profile": "GPT-6 Sol Max"}


# The view contract's schema for one row (docs/schemas/status-view.schema.json).
SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "docs/schemas/status-view.schema.json").read_text())
ROW = SCHEMA["properties"]["view"]["properties"]["escalations"]


def rows(state):
    """The view's rows for `state`, each checked against the status-view schema."""
    found = escalation_view.rows(state)
    util.validate_schema(found, ROW)
    return found


def at(seconds, start=START):
    return (start + dt.timedelta(seconds=seconds)).isoformat()


def climb(seconds, before, after, *, role="sol", trigger="validation_rework", start=START, **extra):
    """A reasoning_escalations event as autocode_escalation.advance writes it."""
    return {
        "at": at(seconds, start),
        "role": role,
        "trigger": trigger,
        "detail": "FAIL: Version prints 1",
        "previous": before,
        "selected": after,
        "struggle_id": f"iteration:{seconds}",
        **extra,
    }


def attempt(stage, seconds, route, *, role=None, tokens=(10, 10), start=START, **extra):
    """A saved stage record: its route, when it started and the tokens its provider reported."""
    record = {
        "stage": stage,
        "role": role or stage,
        "iteration": 1,
        "started_at": at(seconds, start),
        "launch_route": {"model": route["model"], "reasoning_effort": route["reasoning_effort"]},
        "output": f"/run/iterations/{stage}-{seconds}.json",
        "task_id": "task-1",
        "metrics": {"provider_tokens": {"input_tokens": tokens[0], "output_tokens": tokens[1]}} if tokens else {},
        **extra,
    }
    return record


def verdict(record, result):
    """The validation a Tester attempt produced, as the run saves it."""
    return {"output": record["output"], "verdict": result, "reviewer_role": "sol"}


def unrouted(record):
    """A stage record as a run saved it before stage records kept their launch route (2026-10-01)."""
    return {key: value for key, value in record.items() if key != "launch_route"}


def cost_of(usd, *, reported=None, estimated=None, unpriced=0, unrouted=0, complete=False):
    """A row's `cost` as the view writes it."""
    return {
        "usd": usd,
        "reported_usd": reported,
        "estimated_usd": estimated,
        "unpriced_attempts": unpriced,
        "unrouted_attempts": unrouted,
        "complete": complete,
    }


class NoEscalationTests(unittest.TestCase):
    def test_a_run_without_escalations_lists_none_and_prints_nothing(self):
        state = {"status": "TASK_COMPLETE", "stages": [attempt("sol", 1, HIGH)]}
        self.assertEqual([], rows(state))
        self.assertEqual([], escalation_view.lines([]))
        self.assertEqual([], run_view.view({"status": "RUNNING"})["escalations"])

    def test_ordinary_retries_and_routing_observations_are_not_escalations(self):
        state = {
            "status": "RUNNING",
            "builder_retry_decisions": [
                {"action": action, "at": at(1)} for action in ("retry", "investigate", "pause")
            ],
        }
        self.assertEqual([], rows(state))

    def test_a_damaged_record_is_listed_with_an_unknown_cost_and_never_raises(self):
        state = {
            "status": "RUNNING",
            "reasoning_escalations": [{"role": "sol", "at": "not a time", "selected": XHIGH}, "junk", {"at": at(1)}],
            "builder_retry_decisions": "junk",
            "stages": [attempt("sol", 2, XHIGH), "junk"],
            "validation_archive": [{"validation": "junk"}, "junk"],
        }
        [row] = rows(state)
        self.assertEqual([], row["attempts"])
        self.assertEqual({"usd": None, "complete": False}, {key: row["cost"][key] for key in ("usd", "complete")})

    def test_odd_typed_fields_in_saved_records_never_raise(self):
        """Text fields that hold a list or a dict, and entries that are not records, are skipped or read as unknown;
        the climb is still listed and printed."""
        climbed = attempt("sol", 20, XHIGH)
        checkpoint = attempt("astra_checkpoint", 1, HIGH, role="sol")  # its job depends on the routing setting
        stronger = {"action": "escalate", "at": at(30), "failure": climbed["output"], "failure_class": ["execution"]}
        damaged = {
            "a list output in the validation": {"validation": {"output": [climbed["output"]], "verdict": "PASS"}},
            "a dict output in the validation": {"validation": {"output": {"path": 1}, "verdict": "PASS"}},
            "a list output in an archived validation": {
                "validation_archive": [{"validation": {"output": [climbed["output"]], "verdict": "PASS"}}]
            },
            "a dict output in an archived validation": {
                "validation_archive": [{"validation": {"output": {"path": 1}, "verdict": "FAIL"}}]
            },
            "a list verdict": {"validation": {"output": climbed["output"], "verdict": ["PASS"]}},
            "a list output in a stage record": {
                "stages": [{**climbed, "output": [climbed["output"]]}],
                "validation": verdict(climbed, "PASS"),
            },
            "a list role and a dict task in a stage record": {
                "stages": [climbed, {**climbed, "route_role": ["sol"], "task_id": {"id": 1}, "started_at": at(21)}]
            },
            "a list milestone key": {
                "builder_retry_decisions": [
                    {**stronger, "action": "defer", "milestone_key": {"M": 1}},
                    {**stronger, "milestone_key": ["M1"]},
                ],
                "builder_retries": {"M1": {"initial_route": ["route"]}},
            },
            "a list outcome stamp": {"reasoning_escalations": [climb(10, HIGH, XHIGH, outcome=["passed"])]},
            "routes that are not records": {"reasoning_escalations": [climb(10, ["high"], "xhigh")]},
            "entries that are not records": {
                "stages": [climbed, "junk", 3, None, ["junk"]],
                "active_stage": ["junk"],
                "validation": "junk",
                "validation_archive": ["junk", {"validation": ["junk"]}],
                "reasoning_escalations": [climb(10, HIGH, XHIGH), "junk", None],
                "builder_retry_decisions": [None, "junk"],
                "builder_retries": ["junk"],
            },
            "settings that are a list": {"settings": ["junk"], "stages": [checkpoint, climbed]},
            "a reviewer routing setting that is a list": {"settings": {"workflow": ["junk"]}, "stages": [checkpoint]},
        }
        for name, extra in damaged.items():
            with self.subTest(name):
                state = {
                    "status": "RUNNING",
                    "stages": [attempt("sol", 1, HIGH), climbed],
                    "reasoning_escalations": [climb(10, HIGH, XHIGH)],
                    **extra,
                }
                found = escalation_view.rows(state)
                self.assertIn("effort_climb", [row["kind"] for row in found])
                self.assertEqual(len(found) + 1, len(escalation_view.lines(found)))


class EffortClimbTests(unittest.TestCase):
    def test_a_climb_that_converted_lists_its_attempts_until_the_pass_and_their_cost(self):
        failed = attempt("sol", 1, HIGH)
        climbed = attempt("sol", 20, XHIGH, iteration=2)
        later = attempt("sol", 40, XHIGH, iteration=3)  # the next milestone's Tester, still on the climbed rung
        state = {
            "status": "RUNNING",
            "stages": [failed, climbed, later],
            "reasoning_escalations": [climb(10, HIGH, XHIGH, outcome="passed")],
            "validation": verdict(later, "FAIL"),
            "validation_archive": [{"validation": verdict(failed, "FAIL")}, {"validation": verdict(climbed, "PASS")}],
        }
        [row] = rows(state)
        self.assertEqual(
            {
                "kind": "effort_climb",
                "role": "sol",
                "stage": "sol",
                "job": "Tester",
                "at": at(10),
                "trigger": "validation_rework",
                "struggle_id": "iteration:10",
                "task_id": "task-1",
                "from": HIGH,
                "to": XHIGH,
                "deferred_by_parallel_builder": False,
                "inherited": False,
                "outcome": "passed",
                "outcome_basis": "validation",
            },
            {key: row[key] for key in row if key not in ("detail", "attempts", "cost")},
        )
        self.assertEqual([at(20)], [row["started_at"] for row in row["attempts"]])
        self.assertEqual(
            cost_of(SOL, estimated=SOL, complete=True),
            row["cost"],
        )
        self.assertEqual(
            [
                "Escalations: 1 (1 passed).",
                f"  Tester: GPT-6 Sol High → GPT-6 Sol XHigh after validation rework; 1 attempt, {cost.usd(SOL)}; passed.",
            ],
            escalation_view.lines(rows(state)),
        )

    def test_a_climb_the_role_had_to_climb_past_failed_again_and_the_next_rung_converted(self):
        """record_outcome stamps every open climb at a validation PASS; the rung that did not end the struggle
        is shown as failed again, and each climb is charged only its own attempts."""
        first = attempt("sol", 20, XHIGH, iteration=2)
        second = attempt("sol", 40, MAX, iteration=3, tokens=(20, 20))
        state = {
            "status": "TASK_COMPLETE",
            "stages": [attempt("sol", 1, HIGH), first, second],
            "reasoning_escalations": [
                climb(10, HIGH, XHIGH, outcome="passed"),
                climb(30, XHIGH, MAX, outcome="passed"),
            ],
            "validation": verdict(second, "PASS"),
            "validation_archive": [{"validation": verdict(first, "FAIL")}],
        }
        found = rows(state)
        self.assertEqual(
            [("failed_again", "later_escalation", [at(20)]), ("passed", "validation", [at(40)])],
            [(row["outcome"], row["outcome_basis"], [one["started_at"] for one in row["attempts"]]) for row in found],
        )
        doubled = round(prices.estimate("gpt-6-sol", {"input_tokens": 20, "output_tokens": 20}), 6)
        self.assertEqual([SOL, doubled], [row["cost"]["usd"] for row in found])
        self.assertEqual("Escalations: 2 (1 passed, 1 failed again).", escalation_view.lines(found)[0])

    def test_a_climb_whose_attempt_failed_or_was_blocked_failed_again(self):
        for result in ("FAIL", "BLOCKED"):
            with self.subTest(result):
                climbed = attempt("sol", 20, XHIGH, iteration=2)
                state = {
                    "status": "RUNNING",
                    "stages": [attempt("sol", 1, HIGH), climbed],
                    "reasoning_escalations": [climb(10, HIGH, XHIGH)],
                    "validation": verdict(climbed, result),
                }
                [row] = rows(state)
                self.assertEqual(("failed_again", "validation"), (row["outcome"], row["outcome_basis"]))
                self.assertEqual(SOL, row["cost"]["usd"])

    def test_a_validation_of_work_from_before_the_climb_is_not_its_outcome(self):
        """The previous milestone's PASS and the FAIL that set the climb off judged work on the old rung."""
        earlier, failed = attempt("sol", 1, HIGH), attempt("sol", 5, HIGH, iteration=2)
        state = {
            "status": "RUNNING",
            "stages": [earlier, failed],
            "reasoning_escalations": [climb(10, HIGH, XHIGH)],
            "validation": verdict(failed, "FAIL"),
            "validation_archive": [{"validation": verdict(earlier, "PASS")}],
        }
        [row] = rows(state)
        self.assertEqual(("running", "run_status", []), (row["outcome"], row["outcome_basis"], row["attempts"]))

    def test_an_attempt_on_another_route_after_the_climb_is_not_its_attempt(self):
        """A person pinned the Tester back to its own route after the climb: that attempt, and its PASS, are not the
        climb's, also once the runner has stamped every open escalation `passed` at that PASS. When the climbed attempt
        had failed its own check, the climb failed again."""
        for own in (None, "FAIL"):
            with self.subTest(climbed_attempt=own or "not validated"):
                climbed, pinned = attempt("sol", 20, XHIGH), attempt("sol", 30, HIGH, iteration=2)
                state = {
                    "status": "RUNNING",
                    "stages": [attempt("sol", 1, HIGH), climbed, pinned],
                    "reasoning_escalations": [climb(10, HIGH, XHIGH)],
                    "validation": verdict(pinned, "PASS"),
                }
                if own:
                    state["validation_archive"] = [{"validation": verdict(climbed, own)}]
                expected = ("failed_again", "validation") if own else ("running", "run_status")
                [row] = rows(state)
                self.assertEqual(expected, (row["outcome"], row["outcome_basis"]))
                route_ladder.record_outcome(state, "passed")  # what the runner writes at that PASS (autopilot)
                [row] = rows(state)
                self.assertEqual([at(20)], [one["started_at"] for one in row["attempts"]])
                self.assertEqual((*expected, SOL), (row["outcome"], row["outcome_basis"], row["cost"]["usd"]))
                self.assertFalse(escalation_view.lines([row])[1].endswith("; passed."))

    def test_the_runners_pass_stamp_counts_only_while_the_role_ran_on_the_climbed_route(self):
        """The Completion Reviewer's work is judged by no validation; the runner stamps its open climb `passed` when a
        review CONTINUEs at a validation PASS. That stamp is its outcome when the review was on the climbed route, not
        when the reviewer had been moved to another route, or ran nothing since the climb."""
        medium = {"model": "gpt-6-sol", "reasoning_effort": "medium", "profile": "GPT-6 Sol Medium"}
        glm = {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "high"}
        tester = attempt("sol", 3, HIGH)
        rejected = attempt("astra_review", 5, medium, role="astra", route_role="completion")
        for name, reviews, expected in (
            ("on the climbed route", [attempt("astra_review", 20, HIGH, role="astra", route_role="completion")], 1),
            ("moved to another route", [attempt("astra_review", 20, glm, role="astra", route_role="completion")], 0),
            ("nothing since the climb", [], 0),
        ):
            with self.subTest(name):
                state = {
                    "status": "RUNNING",
                    "stages": [attempt("terra", 1, glm), tester, rejected, *reviews],
                    "reasoning_escalations": [climb(10, medium, HIGH, role="completion", trigger="rejected_output")],
                    "validation": verdict(tester, "PASS"),
                }
                route_ladder.record_outcome(state, "passed")
                [row] = rows(state)
                self.assertEqual(
                    ("passed", "recorded") if expected else ("running", "run_status"),
                    (row["outcome"], row["outcome_basis"]),
                )
                self.assertEqual(expected, len(row["attempts"]))

    def test_another_roles_validation_is_not_a_completion_reviewer_or_resolver_climbs_work(self):
        """The reviewer's output is rejected and it climbs; its climbed review sends the work back; the Builder reworks
        and the Tester passes; the next review on the climbed rung is rejected too and it climbs again. The Tester's
        PASS judged the Builder's rework, not the review: the first climb failed again, and both of its reviews are
        its attempts."""
        sol = "gpt-6-sol"
        medium = {"model": sol, "reasoning_effort": "medium", "profile": "GPT-6 Sol Medium"}
        glm = {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "medium"}
        for role, stage in (("completion", "astra_review"), ("resolver", "astra_resolve")):
            with self.subTest(role):

                def review(seconds, route, stage=stage, role=role):
                    return attempt(stage, seconds, route, role="astra", route_role=role)

                tester = attempt("sol", 7, HIGH)
                state = {
                    "status": "RUNNING",
                    "stages": [
                        review(3, medium),  # rejected: climbs at 4
                        review(5, HIGH),  # sends the work back
                        attempt("terra", 6, glm),
                        tester,
                        review(8, HIGH),  # rejected again: climbs at 9
                        review(10, MAX),
                    ],
                    "reasoning_escalations": [
                        climb(4, medium, HIGH, role=role, trigger="rejected_output"),
                        climb(9, HIGH, MAX, role=role, trigger="rejected_output"),
                    ],
                    "validation": verdict(tester, "PASS"),
                }
                found = rows(state)
                self.assertEqual(
                    [("failed_again", "later_escalation", [at(5), at(8)]), ("running", "run_status", [at(10)])],
                    [
                        (row["outcome"], row["outcome_basis"], [one["started_at"] for one in row["attempts"]])
                        for row in found
                    ],
                )
                self.assertEqual(round(2 * SOL, 6), found[0]["cost"]["usd"])

    def test_under_reviewer_routing_the_checkpoint_climb_is_judged_by_its_own_validations(self):
        """Under reviewer routing (glm_first_v1) the checkpoint on the completion route validates, and runs the
        Tester's job. Its climbed checkpoint fails its own check and consults the Tester, whose PASS is not its work;
        the review on the climbed route and the next checkpoint are its attempts too, and the runner's stamp at the
        Tester's PASS does not make it passed."""
        medium = {"model": "gpt-6-sol", "reasoning_effort": "medium", "profile": "GPT-6 Sol Medium"}

        def checkpoint(seconds):
            return attempt("astra_checkpoint", seconds, HIGH, role="astra", route_role="completion")

        first, consult, second = checkpoint(20), attempt("sol", 30, HIGH), checkpoint(60)
        review = attempt("astra_review", 40, HIGH, role="astra", route_role="completion")
        state = {
            "status": "RUNNING",
            "settings": {"workflow": {"mode": "glm_first_v1"}},
            "stages": [
                first,
                consult,
                review,
                attempt("terra", 50, {"model": "glm", "reasoning_effort": "high"}),
                second,
            ],
            "reasoning_escalations": [climb(10, medium, HIGH, role="completion", trigger="rejected_output")],
            "validation_archive": [{"validation": verdict(first, "FAIL")}, {"validation": verdict(consult, "PASS")}],
            "validation": verdict(second, "FAIL"),
        }
        route_ladder.record_outcome(state, "passed")
        [row] = rows(state)
        self.assertEqual(("astra_checkpoint", "Tester"), (row["stage"], row["job"]))
        self.assertEqual(("failed_again", "validation"), (row["outcome"], row["outcome_basis"]))
        self.assertEqual([at(20), at(40), at(60)], [one["started_at"] for one in row["attempts"]])
        self.assertEqual(cost_of(round(3 * SOL, 6), estimated=round(3 * SOL, 6), complete=True), row["cost"])
        del state["settings"]
        self.assertEqual("Completion Reviewer", rows(state)[0]["job"])

    def test_dry_runs_and_the_runners_own_stages_are_not_attempts(self):
        climbed = attempt("sol", 20, XHIGH)
        for name, extra in {
            "a dry run": {"stages": [climbed, attempt("sol", 30, XHIGH, dry_run=True)]},
            "a running dry run": {"stages": [climbed], "active_stage": attempt("sol", 30, XHIGH, dry_run=True)},
            "the runner's own stage": {"stages": [climbed, attempt("sol", 30, XHIGH, runner_owned=True)]},
        }.items():
            with self.subTest(name):
                [row] = rows({"status": "RUNNING", "reasoning_escalations": [climb(10, HIGH, XHIGH)], **extra})
                self.assertEqual([at(20)], [one["started_at"] for one in row["attempts"]])
                self.assertEqual(cost_of(SOL, estimated=SOL, complete=True), row["cost"])

    def test_a_partial_token_count_leaves_the_cost_a_floor(self):
        climbed = attempt("sol", 20, XHIGH)
        climbed["metrics"]["provider_tokens_partial"] = True
        state = {"status": "RUNNING", "stages": [climbed], "reasoning_escalations": [climb(10, HIGH, XHIGH)]}
        [row] = rows(state)
        self.assertEqual(cost_of(SOL, estimated=SOL), row["cost"])
        self.assertIn(f"1 attempt, at least {cost.usd(SOL)}; still running.", escalation_view.lines([row])[1])

    def test_a_climb_on_a_run_saved_before_launch_routes_has_an_unknown_cost_never_zero(self):
        """Climbs were recorded from 2026-09-22, launch routes on stage records from 2026-10-01. The Tester's record
        after the climb cannot be shown to be on the climbed rung, so it is counted, not listed or judged, and the
        cost is unknown rather than a complete zero."""
        old = dt.datetime(2026, 9, 25, 9, 0, tzinfo=dt.UTC)
        climbed = unrouted(attempt("sol", 20, XHIGH, start=old, iteration=2))
        state = {
            "status": "RUNNING",
            "stages": [unrouted(attempt("sol", 1, HIGH, start=old)), climbed],
            "reasoning_escalations": [climb(10, HIGH, XHIGH, start=old)],
            "validation": verdict(climbed, "FAIL"),
        }
        [row] = rows(state)
        self.assertEqual(([], cost_of(None, unrouted=1)), (row["attempts"], row["cost"]))
        self.assertEqual(
            ("sol", "Tester", "running", "run_status"), (row["stage"], row["job"], row["outcome"], row["outcome_basis"])
        )
        self.assertEqual(
            "  Tester: GPT-6 Sol High → GPT-6 Sol XHigh after validation rework; 1 attempt with no recorded route, "
            "cost unknown; still running.",
            escalation_view.lines([row])[1],
        )
        # The run was resumed on a version that records routes: that attempt is priced, as a floor.
        resumed = attempt("sol", 30, XHIGH, start=old, iteration=3)
        state["stages"].append(resumed)
        [row] = rows(state)
        self.assertEqual([resumed["started_at"]], [one["started_at"] for one in row["attempts"]])
        self.assertEqual(cost_of(SOL, estimated=SOL, unrouted=1), row["cost"])
        self.assertIn(
            f"; 1 attempt, 1 attempt with no recorded route, at least {cost.usd(SOL)}; still running.",
            escalation_view.lines([row])[1],
        )

    def test_an_escalation_with_no_usage_record_has_an_unknown_cost_never_zero(self):
        climbed = attempt("sol", 20, XHIGH, tokens=None)
        state = {"status": "RUNNING", "stages": [climbed], "reasoning_escalations": [climb(10, HIGH, XHIGH)]}
        [row] = rows(state)
        self.assertEqual([None], [one["cost_usd"] for one in row["attempts"]])
        self.assertEqual(
            cost_of(None, unpriced=1),
            row["cost"],
        )
        self.assertEqual(("running", "run_status"), (row["outcome"], row["outcome_basis"]))
        self.assertIn("1 attempt, cost unknown; still running.", escalation_view.lines([row])[1])

    def test_a_reported_cost_is_used_as_reported_beside_an_estimate(self):
        reported = attempt("sol", 20, XHIGH, metrics={"provider_cost_usd": 0.5, "provider_tokens": {}})
        estimated = attempt("sol", 21, XHIGH)
        state = {
            "status": "RUNNING",
            "stages": [reported, estimated],
            "reasoning_escalations": [climb(10, HIGH, XHIGH)],
        }
        [row] = rows(state)
        self.assertEqual(["reported", "estimated"], [one["basis"] for one in row["attempts"]])
        self.assertEqual(
            {"usd": round(0.5 + SOL, 6), "reported_usd": 0.5, "estimated_usd": SOL, "unpriced_attempts": 0},
            {key: row["cost"][key] for key in ("usd", "reported_usd", "estimated_usd", "unpriced_attempts")},
        )

    def test_a_climb_that_never_launched_before_the_run_paused_cost_nothing(self):
        """The cost cap stopped the climbed attempt before it launched (#611)."""
        state = {
            "status": "PAUSED_COST_LIMIT",
            "stages": [attempt("sol", 1, HIGH)],
            "reasoning_escalations": [climb(10, HIGH, XHIGH)],
        }
        [row] = rows(state)
        self.assertEqual(([], 0.0, True), (row["attempts"], row["cost"]["usd"], row["cost"]["complete"]))
        self.assertEqual(("paused", "run_status"), (row["outcome"], row["outcome_basis"]))
        self.assertIn("after validation rework; not launched; the run paused.", escalation_view.lines([row])[1])

    def test_the_running_attempt_counts_and_leaves_the_cost_a_floor(self):
        done = attempt("sol", 20, XHIGH)
        running = attempt("sol", 30, XHIGH, iteration=2)
        del running["metrics"]
        state = {
            "status": "RUNNING",
            "stages": [done],
            "active_stage": running,
            "reasoning_escalations": [climb(10, HIGH, XHIGH)],
        }
        [row] = rows(state)
        self.assertEqual([False, True], [one["active"] for one in row["attempts"]])
        self.assertEqual((SOL, False), (row["cost"]["usd"], row["cost"]["complete"]))
        self.assertIn(f"2 attempts, at least {cost.usd(SOL)}; still running.", escalation_view.lines([row])[1])

    def test_a_climb_is_for_the_stage_that_struggled_and_finished_runs_passed(self):
        """A Completion Reviewer climb after a rejected report; the run then completed."""
        reviewer = {"model": "gpt-6-sol", "reasoning_effort": "medium", "profile": "GPT-6 Sol Medium"}
        state = {
            "status": "TASK_COMPLETE",
            "stages": [
                attempt("astra_review", 1, reviewer, role="astra", route_role="completion", task_id="task-7"),
                attempt("astra_review", 20, HIGH, role="astra", route_role="completion"),
                attempt("sol", 21, HIGH),  # another role on the climbed route is not this climb's attempt
            ],
            "reasoning_escalations": [climb(10, reviewer, HIGH, role="completion", trigger="rejected_output")],
        }
        [row] = rows(state)
        self.assertEqual(
            ("completion", "astra_review", "Completion Reviewer", "task-7"),
            (row["role"], row["stage"], row["job"], row["task_id"]),
        )
        self.assertEqual(["astra_review"], [one["stage"] for one in row["attempts"]])
        self.assertEqual(("passed", "run_status"), (row["outcome"], row["outcome_basis"]))


class StrongerAttemptTests(unittest.TestCase):
    """The Builder retry policy's own decisions, made by autocode_builder_policy. Each failure it is given is the
    output path of the attempt that failed, as the runner gives it (autocode_builder_failure.route)."""

    GLM = {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "medium"}
    STRONG = {"model": "openai/gpt-6-sol", "reasoning_effort": "xhigh"}
    CHECKER = {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "high"}

    def state(self):
        return {
            "settings": {
                "engine": "opencode",
                "builder_retry": dict(policy.DEFAULTS),
                "roles": {
                    "terra": {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "medium"},
                    "sol": {"model": "openai/gpt-6-sol", "reasoning_effort": "high"},
                    "completion": {"model": "openai/gpt-6-sol", "reasoning_effort": "medium"},
                },
            },
            "goal_contract": {"hash": "approved"},
            "current_task": {"id": "task1", "milestone_id": "M1"},
            "status": "RUNNING",
        }

    @staticmethod
    def decided_row(state, action):
        return next(row for row in reversed(state["builder_retry_decisions"]) if row["action"] == action)

    @classmethod
    def decided(cls, state, action):
        return dt.datetime.fromisoformat(cls.decided_row(state, action)["at"])

    def escalate(self, seconds, milestone, failure, **extra):
        """A stronger-attempt decision as autocode_builder_policy records it, made from `failure`'s attempt."""
        return {
            "at": at(seconds),
            "action": "escalate",
            "milestone_key": milestone,
            "failure": failure["output"],
            "failure_class": "execution",
            "selected_model": self.STRONG["model"],
            "selected_effort": self.STRONG["reasoning_effort"],
            **extra,
        }

    def failed_stronger_attempt(self, state):
        """Two GLM Builder attempts fail and the stronger attempt fails its Tester's check too; returns when the
        stronger attempt was decided, and its attempt and that check."""
        now = dt.datetime.now(dt.UTC)
        failed = [attempt("terra", -60, self.GLM, start=now), attempt("terra", -30, self.GLM, start=now)]
        state["stages"] = list(failed)
        self.assertEqual("retry", policy.failure(state, failed[0]["output"], "f"))
        self.assertEqual("escalate", policy.failure(state, failed[1]["output"], "f"))
        escalated = self.decided(state, "escalate")
        strong = attempt("terra", 1, self.STRONG, start=escalated, tokens=(1000, 500))
        check = attempt("sol", 2, self.CHECKER, start=escalated)
        state["stages"] += [strong, check]
        state["validation"] = verdict(check, "FAIL")
        return escalated, strong, check

    def paused_stronger_attempt(self, state):
        """The stronger attempt fails (failed_stronger_attempt) and the run pauses at the Builder's retry limit;
        returns when the stronger attempt was decided."""
        escalated, strong, _ = self.failed_stronger_attempt(state)
        self.assertEqual("pause", policy.failure(state, strong["output"], "f"))
        return escalated

    def test_the_stronger_attempt_that_failed_again_ends_in_the_retry_limit_pause(self):
        state = self.state()
        self.paused_stronger_attempt(state)
        [row] = rows(json.loads(json.dumps(state)))
        self.assertEqual(
            {
                "kind": "stronger_attempt",
                "role": "terra",
                "stage": "terra",
                "job": "Builder",
                "trigger": "execution_failure",
                "task_id": "task-1",
                "from": {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "medium", "profile": None},
                "to": {"model": "openai/gpt-6-sol", "reasoning_effort": "xhigh", "profile": None},
                "deferred_by_parallel_builder": False,
                "inherited": False,
                "outcome": "paused",
                "outcome_basis": "recorded",
            },
            {key: row[key] for key in row if key not in ("at", "detail", "struggle_id", "attempts", "cost")},
        )
        self.assertEqual(["terra"], [one["stage"] for one in row["attempts"]])
        expected = prices.estimate("openai/gpt-6-sol", {"input_tokens": 1000, "output_tokens": 500})
        self.assertEqual(round(expected, 6), row["cost"]["usd"])
        self.assertIn(
            "Builder: zai-coding-plan/glm-5.3 at medium → openai/gpt-6-sol at xhigh after execution failure; 1 attempt",
            escalation_view.lines([row])[1],
        )

    def test_a_pass_after_a_replan_is_not_credited_to_the_paused_stronger_attempt(self):
        """The pause names replanning as the way on. The revised contract is a new milestone key, so the Builder is
        back on its own route; that route's attempt passing is not a validation of the stronger attempt's work."""
        state = self.state()
        escalated = self.paused_stronger_attempt(state)
        state["validation_archive"] = [{"validation": state.pop("validation")}]
        state.update(goal_contract={"hash": "replanned"}, status="RUNNING")
        policy.guard(state)
        self.assertEqual(self.GLM, state["settings"]["roles"]["terra"])
        rebuilt = attempt("terra", 30, self.GLM, start=escalated)
        check = attempt("sol", 31, self.CHECKER, start=escalated)
        state["stages"] += [rebuilt, check]
        state.update(validation=verdict(check, "PASS"), status="TASK_COMPLETE")
        [row] = rows(json.loads(json.dumps(state)))
        self.assertEqual(("paused", "recorded"), (row["outcome"], row["outcome_basis"]))
        self.assertEqual([at(1, escalated)], [one["started_at"] for one in row["attempts"]])
        self.assertTrue(escalation_view.lines([row])[1].endswith("; the run paused."))

    def test_a_pass_after_a_replan_that_did_not_pause_is_not_the_stronger_attempts_pass(self):
        """The stronger attempt's check failed and the failure was classified as the plan's (`replan`, no pause). The
        revised contract puts the Builder back on its own route, whose work passes, and the runner stamps every open
        escalation `passed` at that PASS. The stronger attempt's own work failed: it failed again."""
        state = self.state()
        escalated, strong, check = self.failed_stronger_attempt(state)
        self.assertEqual("replan", policy.failure(state, check["output"], "f", classification="plan"))
        state["validation_archive"] = [{"validation": state.pop("validation")}]
        state["goal_contract"] = {"hash": "replanned"}
        policy.guard(state)
        self.assertEqual(self.GLM, state["settings"]["roles"]["terra"])
        rebuilt = attempt("terra", 30, self.GLM, start=escalated)
        recheck = attempt("sol", 31, self.CHECKER, start=escalated)
        state["stages"] += [rebuilt, recheck]
        state["validation"] = verdict(recheck, "PASS")
        route_ladder.record_outcome(state, "passed")
        self.assertEqual("passed", self.decided_row(state, "escalate")["outcome"])
        [row] = rows(json.loads(json.dumps(state)))
        self.assertEqual(("failed_again", "validation"), (row["outcome"], row["outcome_basis"]))
        self.assertEqual([strong["started_at"]], [one["started_at"] for one in row["attempts"]])
        self.assertTrue(escalation_view.lines([row])[1].endswith("; failed again."))

    def test_a_tester_climb_whose_own_checks_failed_is_not_passed_by_the_moved_testers_pass(self):
        """The Tester climbs (autocode_escalation.advance) and both of its climbed checks fail the Builder's work. The
        Builder's retry policy makes the stronger attempt and moves the Tester off the strong model's family
        (swap_checkers); the moved Tester passes the stronger attempt, and the runner stamps every open escalation
        `passed`. That PASS was of work on a route the Tester was moved to: the climb failed again."""
        state = self.state()
        policy.guard(state)
        now = dt.datetime.now(dt.UTC)

        def route(role):
            return dict(state["settings"]["roles"][role])

        first = attempt("sol", -95, route("sol"), start=now)
        state["stages"] = [attempt("terra", -100, route("terra"), start=now), first]
        state["validation_archive"] = [{"validation": verdict(first, "FAIL")}]
        event = escalation.advance(state, "sol", trigger="validation_rework", detail="FAIL", struggle_id="iteration:1")
        event["at"] = at(-90, now)
        climbed = []
        for seconds, action in ((-80, "retry"), (-60, "escalate")):
            check = attempt("sol", seconds + 5, route("sol"), start=now)
            climbed.append(check)
            state["stages"] += [attempt("terra", seconds, route("terra"), start=now), check]
            state["validation_archive"].append({"validation": verdict(check, "FAIL")})
            self.assertEqual(action, policy.failure(state, check["output"], "f"))
        self.assertEqual(self.CHECKER["model"], route("sol")["model"])  # swap_checkers moved the Tester
        escalated = self.decided(state, "escalate")
        strong = attempt("terra", 1, self.STRONG, start=escalated)
        check = attempt("sol", 2, route("sol"), start=escalated)
        state["stages"] += [strong, check]
        state["validation"] = verdict(check, "PASS")
        route_ladder.record_outcome(state, "passed")
        self.assertEqual("passed", state["reasoning_escalations"][0]["outcome"])
        tester, stronger = rows(json.loads(json.dumps(state)))
        self.assertEqual(
            [
                ("effort_climb", "failed_again", "validation", [one["started_at"] for one in climbed]),
                ("stronger_attempt", "passed", "validation", [strong["started_at"]]),
            ],
            [
                (row["kind"], row["outcome"], row["outcome_basis"], [one["started_at"] for one in row["attempts"]])
                for row in (tester, stronger)
            ],
        )
        self.assertTrue(escalation_view.lines([tester])[1].endswith("; failed again."))

    def test_another_milestones_stronger_attempt_and_pass_end_the_attempts_but_not_the_outcome(self):
        """Each milestone gets one stronger attempt on the same route: the next one's attempts are its own, its
        Tester's PASS is its own, and the earlier attempt keeps the pause the runner recorded on it rather than
        counting as failed again or passed."""
        escalate = self.escalate
        first, second = (
            attempt("terra", 5, self.GLM, task_id="task-M1"),
            attempt("terra", 25, self.GLM, task_id="task-M2"),
        )
        check = attempt("sol", 50, self.CHECKER, task_id="task-M2")
        state = {
            "status": "RUNNING",
            "builder_retries": {"M1": {"initial_route": self.GLM}},
            "builder_retry_decisions": [escalate(10, "M1", first, outcome="paused"), escalate(30, "M2", second)],
            "stages": [
                first,
                attempt("terra", 20, self.STRONG, task_id="task-M1"),
                second,
                attempt("terra", 40, self.STRONG, task_id="task-M2"),
                check,
            ],
            "validation": verdict(check, "PASS"),
        }
        found = rows(state)
        self.assertEqual(
            [("paused", "recorded", [at(20)], "task-M1"), ("passed", "validation", [at(40)], "task-M2")],
            [
                (row["outcome"], row["outcome_basis"], [one["started_at"] for one in row["attempts"]], row["task_id"])
                for row in found
            ],
        )
        self.assertEqual([self.GLM["model"]] * 2, [row["from"]["model"] for row in found])

    def test_the_stronger_attempt_ends_the_builders_climb_on_the_same_or_another_route(self):
        """A Builder on GPT-6 Sol climbs its effort ladder after a rejected output, its climbed attempts fail their
        checks and its retry policy makes the stronger attempt (GPT-6 Sol XHigh), which passes. The climb failed
        again: the Builder was moved past its rung. Each attempt is charged to one escalation only, also when the
        climb reached the stronger attempt's own route."""
        sol = "openai/gpt-6-sol"
        profiles = {"medium": "GPT-6 Sol Medium", "high": "GPT-6 Sol High", "xhigh": "GPT-6 Sol XHigh"}
        for low, high in (("high", "xhigh"), ("medium", "high")):
            with self.subTest(climb=f"{low} to {high}"):
                state = self.state()
                state["settings"]["roles"]["terra"] = {"model": sol, "reasoning_effort": low}
                policy.guard(state)
                now = dt.datetime.now(dt.UTC)
                route = {"model": sol, "reasoning_effort": high}
                state["reasoning_escalations"] = [
                    climb(
                        -90,
                        {"model": sol, "reasoning_effort": low, "profile": profiles[low]},
                        {**route, "profile": profiles[high]},
                        role="terra",
                        trigger="rejected_output",
                        start=now,
                    )
                ]
                state["settings"]["roles"]["terra"] = dict(route)
                built = [attempt("terra", seconds, route, start=now) for seconds in (-80, -60)]
                checks = [attempt("sol", seconds, self.CHECKER, start=now) for seconds in (-75, -55)]
                state["stages"] = [attempt("terra", -100, {"model": sol, "reasoning_effort": low}, start=now)]
                state["stages"] += [built[0], checks[0], built[1], checks[1]]
                state["validation_archive"] = [{"validation": verdict(check, "FAIL")} for check in checks]
                # The Tester's FAIL is the evidence that sends the Builder's work back (autocode_rework_policy).
                self.assertEqual("retry", policy.failure(state, checks[0]["output"], "f"))
                self.assertEqual("escalate", policy.failure(state, checks[1]["output"], "f"))
                escalated = self.decided(state, "escalate")
                strong = attempt("terra", 1, self.STRONG, start=escalated)
                check = attempt("sol", 2, self.CHECKER, start=escalated)
                state["stages"] += [strong, check]
                state["validation"] = verdict(check, "PASS")
                route_ladder.record_outcome(state, "passed")  # the runner stamps every open escalation at a PASS
                climbed, stronger = rows(json.loads(json.dumps(state)))
                self.assertEqual(
                    [
                        ("effort_climb", "failed_again", "later_escalation", [one["started_at"] for one in built]),
                        ("stronger_attempt", "passed", "validation", [strong["started_at"]]),
                    ],
                    [
                        (
                            row["kind"],
                            row["outcome"],
                            row["outcome_basis"],
                            [one["started_at"] for one in row["attempts"]],
                        )
                        for row in (climbed, stronger)
                    ],
                )
                self.assertEqual(({**route, "profile": None}, "task-1"), (stronger["from"], stronger["task_id"]))
                spent = sum(one["cost_usd"] for row in (climbed, stronger) for one in row["attempts"])
                self.assertEqual(
                    cost.view({"stages": [*built, strong]})["estimated_usd"],
                    round(spent, 6),
                )
                self.assertEqual(
                    f"  Builder: {profiles[low]} → {profiles[high]} after rejected output; 2 attempts, "
                    f"{cost.usd(climbed['cost']['usd'])}; failed again.",
                    escalation_view.lines([climbed])[1],
                )

    def test_a_parallel_builders_stronger_attempt_is_listed_once_the_parent_makes_it(self):
        parent = self.state()
        worker = copy.deepcopy(parent)
        worker["parent_run"] = "/runs/parent"
        failed = [
            attempt("terra", seconds, self.GLM, task_id="task1") | {"output": f"/w1/iterations/builder-0{n}.json"}
            for n, seconds in ((1, -60), (2, -40))
        ]
        policy.failure(worker, failed[0]["output"], "f")
        self.assertEqual("defer", policy.failure(worker, failed[1]["output"], "f"))
        policy.guard(parent)
        policy.adopt(parent, worker)
        # The worker's deferral alone launches nothing on a stronger route: it is not an escalation yet.
        self.assertEqual([], rows(parent))
        parent = json.loads(json.dumps(parent))
        policy.guard(parent)
        self.assertEqual(["retry", "defer", "escalate"], [row["action"] for row in parent["builder_retry_decisions"]])
        escalated = self.decided(parent, "escalate")
        # The parent holds both workers' Builder attempts (autocode_dispatch.account_workers): M1's, which failed, and
        # M2's, on another route, the later one started after M1's last failure. The stronger attempt is for M1's
        # task, made from M1's attempt.
        parent["stages"] = [
            {**record, "started_at": at(seconds, escalated), "worker_milestone": "M1"}
            for record, seconds in zip(failed, (-60, -40), strict=True)
        ]
        parent["stages"].append(
            attempt(
                "terra", -10, self.CHECKER, start=escalated, task_id="task2", output="/w2/iterations/builder-01.json"
            )
            | {"worker_milestone": "M2"}
        )
        parent["stages"].append(attempt("terra", 1, self.STRONG, start=escalated, task_id="task1"))
        sol = attempt("sol", 2, self.CHECKER, start=escalated, task_id="task1")
        parent["stages"].append(sol)
        parent["validation"] = verdict(sol, "PASS")
        [row] = rows(parent)
        self.assertTrue(row["deferred_by_parallel_builder"])
        self.assertEqual(
            ("stronger_attempt", "passed", "validation", "task1", {**self.GLM, "profile": None}),
            (row["kind"], row["outcome"], row["outcome_basis"], row["task_id"], row["from"]),
        )
        self.assertEqual([at(1, escalated)], [one["started_at"] for one in row["attempts"]])
        self.assertEqual(SOL, row["cost"]["usd"])

    def test_a_parallel_builders_own_stronger_attempt_is_not_the_parents(self):
        """The parent's stronger attempt for M1 paused and the run was replanned. A parallel Builder for M5 then made its
        own stronger attempt (no checker shares the strong model's family, so it did not defer): that decision is in its
        worker run, and the parent holds only its stage records (autocode_dispatch.account_workers). They are not the
        parent's stronger attempt's attempts, and the parent's Tester PASS of the integrated batch is not its work."""
        parent = self.state()
        parent["settings"]["roles"].update(sol=dict(self.CHECKER), completion=dict(self.CHECKER))
        escalated = self.paused_stronger_attempt(parent)
        parent["validation_archive"] = [{"validation": parent.pop("validation")}]
        parent.update(goal_contract={"hash": "replanned"}, current_task={"id": "task5", "milestone_id": "M5"})
        policy.guard(parent)
        # As autocode_dispatch prepares a worker: the parent's settings and contract, and the parent run named.
        worker = copy.deepcopy({key: parent[key] for key in ("settings", "goal_contract")})
        worker.update(parent_run="/runs/parent", current_task={"id": "task-w5", "milestone_id": "M5"}, status="RUNNING")
        policy.guard(worker)
        failed = [
            attempt("terra", seconds, self.GLM, start=escalated, task_id="task-w5", output=f"/w5/builder-0{n}.json")
            for n, seconds in ((1, 100), (2, 120))
        ]
        worker["stages"] = list(failed)
        self.assertEqual(["retry", "escalate"], [policy.failure(worker, row["output"], "f") for row in failed])
        strong = attempt(
            "terra",
            200,
            self.STRONG,
            start=escalated,
            task_id="task-w5",
            output="/w5/builder-03.json",
            tokens=(500, 500),
        )
        parent["stages"] += [{**record, "worker_milestone": "M5"} for record in (*failed, strong)]
        check = attempt("sol", 300, self.CHECKER, start=escalated, task_id="task5")
        parent["stages"].append(check)
        parent["validation"] = verdict(check, "PASS")
        route_ladder.record_outcome(parent, "passed")
        [row] = rows(json.loads(json.dumps(parent)))
        self.assertEqual(("task-1", "paused", "recorded"), (row["task_id"], row["outcome"], row["outcome_basis"]))
        self.assertEqual([at(1, escalated)], [one["started_at"] for one in row["attempts"]])
        expected = prices.estimate("openai/gpt-6-sol", {"input_tokens": 1000, "output_tokens": 500})
        self.assertEqual(cost_of(round(expected, 6), estimated=round(expected, 6), complete=True), row["cost"])

    def test_the_failed_attempt_a_testers_output_names_is_the_builders_for_that_task(self):
        """Another milestone's Builder, on another route, started after this milestone's Builder; the failure the
        decision names is the Tester's output for this milestone's task, so the stronger attempt was made from this
        milestone's Builder attempt."""
        built = attempt("terra", 5, self.GLM, task_id="task-M1")
        other = attempt("terra", 8, self.CHECKER, task_id="task-M2")
        check = attempt("sol", 9, self.CHECKER, task_id="task-M1")
        decision = self.escalate(10, "M1", check)
        [row] = rows({"status": "RUNNING", "stages": [built, other, check], "builder_retry_decisions": [decision]})
        self.assertEqual(
            ("terra", "task-M1", {**self.GLM, "profile": None}), (row["stage"], row["task_id"], row["from"])
        )

    def test_a_stronger_attempt_made_from_an_attempt_off_the_climbed_route_does_not_end_the_climb(self):
        """The Builder climbed, a person pinned it back to its own route, and its retry policy made the stronger
        attempt from the pinned attempt: the climb did not fail again by it."""
        climbed, pinned = attempt("terra", 20, XHIGH), attempt("terra", 30, HIGH)
        state = {
            "status": "RUNNING",
            "stages": [attempt("terra", 1, HIGH), climbed, pinned],
            "reasoning_escalations": [climb(10, HIGH, XHIGH, role="terra", trigger="rejected_output")],
            "builder_retry_decisions": [self.escalate(40, "M1", pinned)],
        }
        climb_row, stronger = rows(state)
        self.assertEqual(
            ("effort_climb", "running", "run_status", [at(20)]),
            (
                climb_row["kind"],
                climb_row["outcome"],
                climb_row["outcome_basis"],
                [one["started_at"] for one in climb_row["attempts"]],
            ),
        )
        self.assertEqual(("stronger_attempt", "high"), (stronger["kind"], stronger["from"]["reasoning_effort"]))

    def test_only_a_defer_for_its_milestone_made_before_it_marks_the_stronger_attempt(self):
        built = attempt("terra", 5, self.GLM)
        state = {
            "status": "RUNNING",
            "stages": [built],
            "builder_retry_decisions": [
                {"action": "defer", "milestone_key": "M2", "at": at(6)},
                self.escalate(10, "M1", built),
                {"action": "defer", "milestone_key": "M1", "at": at(60)},
            ],
        }
        [row] = rows(state)
        self.assertFalse(row["deferred_by_parallel_builder"])

    def test_a_pass_saved_after_the_stronger_attempt_was_made_does_not_undo_it(self):
        """The Builder's climbed attempt failed and its retry policy made the stronger attempt from it. A Tester PASS
        of that attempt's work saved after the decision does not make the climb passed: the stronger attempt is the
        first decisive record after the climb."""
        climbed, check = attempt("terra", 20, XHIGH), attempt("sol", 30, self.CHECKER)
        state = {
            "status": "RUNNING",
            "stages": [attempt("terra", 1, HIGH), climbed, check],
            "reasoning_escalations": [climb(10, HIGH, XHIGH, role="terra", trigger="rejected_output")],
            "builder_retry_decisions": [self.escalate(25, "M1", climbed)],
            "validation": verdict(check, "PASS"),
        }
        climb_row, _ = rows(state)
        self.assertEqual(
            ("failed_again", "later_escalation", [at(20)]),
            (climb_row["outcome"], climb_row["outcome_basis"], [one["started_at"] for one in climb_row["attempts"]]),
        )

    def test_a_stronger_attempt_made_before_a_restored_checkpoint_has_an_unknown_cost(self):
        """A checkpoint continuation (`autocode checkpoint --restore`) carries the original run's escalation records,
        but not its stage records: the attempt ran, and was paid for, in the original run."""
        state = self.state()
        escalated = self.paused_stronger_attempt(state)
        del state["goal_contract"]
        original = continuation_tests.approved_run(continuation_tests.PROJECT, **json.loads(json.dumps(state)))
        [before] = rows(original)
        self.assertEqual((1, True, False), (len(before["attempts"]), before["cost"]["complete"], before["inherited"]))
        workspace = continuation_tests.PROJECT / ".autocode/worktrees/restored"
        # As autocode_checkpoint_cli.restore calls it, at the time the restore was asked for.
        child = continuation.create(
            original,
            {"id": "checkpoint-restored", "commit": "c" * 40},
            workspace,
            workspace / ".autocode/runs/restored",
            "autocode/restored",
            continuation_tests.PROJECT,
            "operation-restored",
            at=at(60, escalated),
        )
        self.assertEqual([], child["stages"])
        self.assertGreater(run_view.view(child)["cost"]["inherited_usd"], 0)
        [row] = rows(child)
        self.assertEqual(
            ("terra", "Builder", True, [], "paused"),
            (row["role"], row["job"], row["inherited"], row["attempts"], row["outcome"]),
        )
        self.assertEqual(
            cost_of(None),
            row["cost"],
        )
        self.assertEqual(
            "  Builder: zai-coding-plan/glm-5.3 at medium → openai/gpt-6-sol at xhigh after execution failure; made "
            "before the restored checkpoint, cost unknown; the run paused.",
            escalation_view.lines([row])[1],
        )


class LinesTests(unittest.TestCase):
    """What `--status` prints for a person."""

    def test_the_count_line_names_each_outcome_in_words(self):
        climbed = attempt("sol", 20, XHIGH)
        ran = {"stages": [climbed], "reasoning_escalations": [climb(10, HIGH, XHIGH)]}
        unlaunched = {"reasoning_escalations": [climb(10, HIGH, XHIGH)]}
        states = {
            "Escalations: 1 (1 passed).": {"status": "RUNNING", **ran, "validation": verdict(climbed, "PASS")},
            "Escalations: 1 (1 failed again).": {"status": "RUNNING", **ran, "validation": verdict(climbed, "FAIL")},
            "Escalations: 1 (1 paused).": {"status": "PAUSED_COST_LIMIT", **unlaunched},
            "Escalations: 1 (1 still running).": {"status": "RUNNING", **unlaunched},
        }
        found = []
        for header, state in states.items():
            with self.subTest(header):
                [row] = rows(state)
                self.assertEqual(header, escalation_view.lines([row])[0])
                found.append(row)
        self.assertEqual(
            "Escalations: 5 (2 passed, 1 failed again, 1 paused, 1 still running).",
            escalation_view.lines([found[0], *found])[0],
        )
        # A row from another version whose outcome this one does not know.
        mixed = escalation_view.lines([found[0], {**found[0], "outcome": "lost"}])
        self.assertEqual("Escalations: 2 (1 passed, 1 with an unknown outcome).", mixed[0])
        self.assertTrue(mixed[2].endswith("; outcome unknown."), mixed[2])

    def test_a_job_no_table_names_is_never_shown_by_its_code_name(self):
        """A climb saved under the Resolver's old route key (`astra`, before #1345) with no stage record to name its
        job. A row without a job is named from its stage or role through autocode_roles, else `Unknown job`."""
        astra = {"model": "openai/gpt-6-astra", "reasoning_effort": "high", "profile": "GPT-6 Astra High"}
        climbed = {**astra, "reasoning_effort": "xhigh", "profile": "GPT-6 Astra XHigh"}
        [row] = rows({"status": "RUNNING", "reasoning_escalations": [climb(10, astra, climbed, role="astra")]})
        self.assertEqual(("astra", None, None), (row["role"], row["stage"], row["job"]))
        self.assertTrue(escalation_view.lines([row])[1].startswith("  Unknown job: GPT-6 Astra High → "))
        for role, stage, job in (
            ("sol", None, "Tester"),
            ("terra", None, "Builder"),
            ("completion", None, "Completion Reviewer"),
            ("resolver", None, "Resolver"),
            ("sol", "astra_checkpoint", "Completion Reviewer"),
            ("glm", "glm_custom", "Unknown job"),
        ):
            with self.subTest(role=role, stage=stage):
                line = escalation_view.lines([{**row, "role": role, "stage": stage, "job": None}])[1]
                self.assertTrue(line.startswith(f"  {job}: "), line)

    def test_rows_of_another_shape_are_printed_without_raising(self):
        self.assertEqual(
            [
                "Escalations: 1 (1 with an unknown outcome).",
                "  Unknown job: an unknown model → an unknown model after an unrecorded trigger; attempts unknown, cost "
                "unknown; outcome unknown.",
            ],
            escalation_view.lines(
                [
                    {
                        "outcome": ["passed"],
                        "role": ["sol"],
                        "attempts": 5,
                        "cost": {"usd": "1", "unrouted_attempts": [1]},
                        "trigger": {"kind": 1},
                        "from": [],
                        "to": None,
                    },
                    "junk",
                ]
            ),
        )
        self.assertEqual([], escalation_view.lines({"escalations": []}))


if __name__ == "__main__":
    unittest.main()
