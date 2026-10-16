"""Each escalation with what its escalated attempts cost and how it ended (#612): the pure derivation."""

import copy
import datetime as dt
import json
import unittest
from pathlib import Path

import autocode_builder_policy as policy
import autocode_cost as cost
import autocode_escalation_view as escalation_view
import autocode_prices as prices
import autocode_run_view as run_view
import autocode_util as util

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


def climb(seconds, before, after, *, role="sol", trigger="validation_rework", **extra):
    """A reasoning_escalations event as autocode_escalation.advance writes it."""
    return {
        "at": at(seconds),
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
                "outcome": "passed",
                "outcome_basis": "validation",
            },
            {key: row[key] for key in row if key not in ("detail", "attempts", "cost")},
        )
        self.assertEqual([at(20)], [row["started_at"] for row in row["attempts"]])
        self.assertEqual(
            {"usd": SOL, "reported_usd": None, "estimated_usd": SOL, "unpriced_attempts": 0, "complete": True},
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

    def test_a_climb_whose_attempt_failed_its_validation_again(self):
        climbed = attempt("sol", 20, XHIGH, iteration=2)
        state = {
            "status": "RESOLVER_PENDING",
            "stages": [attempt("sol", 1, HIGH), climbed],
            "reasoning_escalations": [climb(10, HIGH, XHIGH)],
            "validation": verdict(climbed, "FAIL"),
        }
        [row] = rows(state)
        self.assertEqual(("failed_again", "validation"), (row["outcome"], row["outcome_basis"]))
        self.assertEqual(SOL, row["cost"]["usd"])

    def test_an_escalation_with_no_usage_record_has_an_unknown_cost_never_zero(self):
        climbed = attempt("sol", 20, XHIGH, tokens=None)
        state = {"status": "RUNNING", "stages": [climbed], "reasoning_escalations": [climb(10, HIGH, XHIGH)]}
        [row] = rows(state)
        self.assertEqual([None], [one["cost_usd"] for one in row["attempts"]])
        self.assertEqual(
            {"usd": None, "reported_usd": None, "estimated_usd": None, "unpriced_attempts": 1, "complete": False},
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
    """The Builder retry policy's own decisions, made by autocode_builder_policy."""

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
    def decided(state, action):
        return dt.datetime.fromisoformat(
            next(row for row in reversed(state["builder_retry_decisions"]) if row["action"] == action)["at"]
        )

    def test_the_stronger_attempt_that_failed_again_ends_in_the_retry_limit_pause(self):
        state = self.state()
        glm = {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "medium"}
        state["stages"] = [attempt("terra", -60, glm, start=dt.datetime.now(dt.UTC))]
        policy.failure(state, "e1", "f")
        self.assertEqual("escalate", policy.failure(state, "e2", "f"))
        escalated = self.decided(state, "escalate")
        strong = {"model": "openai/gpt-6-sol", "reasoning_effort": "xhigh"}
        state["stages"] += [
            attempt("terra", 1, strong, start=escalated, tokens=(1000, 500)),
            attempt("sol", 2, {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "high"}, start=escalated),
        ]
        self.assertEqual("pause", policy.failure(state, "e3", "f"))
        [row] = rows(json.loads(json.dumps(state)))
        self.assertEqual(
            {
                "kind": "stronger_attempt",
                "role": "terra",
                "stage": "terra",
                "job": "Builder",
                "trigger": "execution_failure",
                "from": {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "medium", "profile": None},
                "to": {"model": "openai/gpt-6-sol", "reasoning_effort": "xhigh", "profile": None},
                "deferred_by_parallel_builder": False,
                "outcome": "paused",
                "outcome_basis": "recorded",
            },
            {key: row[key] for key in row if key not in ("at", "detail", "struggle_id", "task_id", "attempts", "cost")},
        )
        self.assertEqual(["terra"], [one["stage"] for one in row["attempts"]])
        expected = prices.estimate("openai/gpt-6-sol", {"input_tokens": 1000, "output_tokens": 500})
        self.assertEqual(round(expected, 6), row["cost"]["usd"])
        self.assertIn(
            "Builder: zai-coding-plan/glm-5.3 at medium → openai/gpt-6-sol at xhigh after execution failure; 1 attempt",
            escalation_view.lines([row])[1],
        )

    def test_another_milestones_stronger_attempt_ends_the_attempts_but_not_the_outcome(self):
        """Each milestone gets one stronger attempt on the same route: the next one's attempts are its own, and
        the earlier attempt keeps the pause the runner recorded on it rather than counting as failed again."""
        strong = {"model": "openai/gpt-6-sol", "reasoning_effort": "xhigh"}

        def escalate(seconds, milestone, **extra):
            return {
                "at": at(seconds),
                "action": "escalate",
                "milestone_key": milestone,
                "failure": {"binding": {"task_id": f"task-{milestone}"}},
                "selected_model": strong["model"],
                "selected_effort": strong["reasoning_effort"],
                **extra,
            }

        glm = {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "medium"}
        state = {
            "status": "RUNNING",
            "builder_retries": {"M1": {"initial_route": glm}},
            "builder_retry_decisions": [escalate(10, "M1", outcome="paused"), escalate(30, "M2")],
            "stages": [attempt("terra", 20, strong), attempt("terra", 40, strong)],
        }
        first, second = rows(state)
        self.assertEqual(
            [("paused", "recorded", [at(20)], "task-M1"), ("running", "run_status", [at(40)], "task-M2")],
            [
                (row["outcome"], row["outcome_basis"], [one["started_at"] for one in row["attempts"]], row["task_id"])
                for row in (first, second)
            ],
        )
        self.assertEqual(glm["model"], first["from"]["model"])

    def test_a_parallel_builders_stronger_attempt_is_listed_once_the_parent_makes_it(self):
        parent = self.state()
        worker = copy.deepcopy(parent)
        worker["parent_run"] = "/runs/parent"
        policy.failure(worker, "e1", "f")
        self.assertEqual("defer", policy.failure(worker, "e2", "f"))
        policy.guard(parent)
        policy.adopt(parent, worker)
        # The worker's deferral alone launches nothing on a stronger route: it is not an escalation yet.
        self.assertEqual([], rows(parent))
        parent = json.loads(json.dumps(parent))
        policy.guard(parent)
        self.assertEqual(["retry", "defer", "escalate"], [row["action"] for row in parent["builder_retry_decisions"]])
        escalated = self.decided(parent, "escalate")
        strong = {"model": "openai/gpt-6-sol", "reasoning_effort": "xhigh"}
        # The worker's Builder attempts the parent imported ran before the parent's stronger attempt.
        parent["stages"] = [
            attempt("terra", -30, {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "medium"}, start=escalated)
            | {"worker_milestone": "M1"},
            attempt("terra", 1, strong, start=escalated),
        ]
        sol = attempt("sol", 2, {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "high"}, start=escalated)
        parent["stages"].append(sol)
        parent["validation"] = verdict(sol, "PASS")
        [row] = rows(parent)
        self.assertTrue(row["deferred_by_parallel_builder"])
        self.assertEqual(
            ("stronger_attempt", "passed", "validation"), (row["kind"], row["outcome"], row["outcome_basis"])
        )
        self.assertEqual([parent["stages"][1]["started_at"]], [one["started_at"] for one in row["attempts"]])
        self.assertEqual(SOL, row["cost"]["usd"])


if __name__ == "__main__":
    unittest.main()
