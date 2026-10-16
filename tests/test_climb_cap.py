"""A role's climb on its effort ladder is checked against the cost cap, the active-time budget and, for the
Builder, the milestone budget before its attempt launches (#611)."""

import unittest

import autocode_builder_policy as builder_policy
import autocode_climb_cap as climb_cap
import autocode_escalation as escalation
import autocode_util as util

# 1,000 input and 200 output tokens at GPT-6 Sol's $2 / $10 per million (tools/autocode_prices.json) cost $0.004.
TOKENS = {"input_tokens": 1000, "output_tokens": 200}
# When the attempts these runs made before any climb started.
EARLIER = "2000-01-01T00:00:00+00:00"


def route(effort, model="gpt-6-sol", engine="codex"):
    return {"engine": engine, "provider": None, "model": model, "reasoning_effort": effort}


def attempt(launch_route, tokens=TOKENS, *, stage="sol", role="sol", started_at=None, **extra):
    """A finished attempt on `launch_route`, started now unless `started_at` says when."""
    return {
        "stage": stage,
        "role": role,
        "launch_route": dict(launch_route),
        "metrics": {"provider_tokens": tokens},
        "started_at": started_at or util.now(),
        **extra,
    }


def tested(effort, tokens=TOKENS, **extra):
    """A finished Tester attempt on GPT-6 Sol at `effort`."""
    return attempt(route(effort), tokens, **extra)


def reviewed(effort, tokens=TOKENS, **extra):
    """A finished Completion Reviewer attempt on GPT-6 Sol at `effort`, saved as role astra on route completion."""
    return attempt(route(effort), tokens, stage="astra_review", role="astra", route_role="completion", **extra)


def climbed(cap, tokens=TOKENS):
    """A run whose Tester checked once on GPT-6 Sol High, failed, and climbed to XHigh, under a cost cap of `cap`."""
    state = {
        "settings": {"engine": "codex", "roles": {"sol": route("high")}, "limits": {"max_cost_usd": cap}},
        "sessions": {"sol": "tester-session"},
        "stages": [tested("high", tokens, started_at=EARLIER)],
    }
    event = escalation.advance(state, "sol", trigger="validation_rework", detail="FAIL: Version prints 1")
    assert event and event["selected"]["profile"] == "GPT-6 Sol XHigh", event
    return state


def stop(state, stage, role, status="PAUSED_COST_LIMIT"):
    """The stop reason guard() raises before `stage` launches on `role`'s route."""
    try:
        climb_cap.guard(state, stage, role)
    except climb_cap.util.Paused as error:
        assert error.status == status, error.status
        return str(error)
    raise AssertionError(f"{stage} launched on {role}'s route without a {status} pause")


def timed(budget, *, took=600, used=650, origin="user_explicit", stage_limit=3600):
    """A run whose Tester checked once on GPT-6 Sol High for `took` seconds, failed, and climbed to XHigh, with
    `used` seconds of active time so far under an active-time budget of `budget` seconds that `origin` set."""
    state = climbed(None)
    state["settings"]["limits"] = {"max_seconds": budget, "stage_timeout_seconds": stage_limit}
    state["settings"]["budget_origins"] = {"max_seconds": origin}
    state["stages"][-1]["duration_seconds"] = took
    state["active_seconds"] = used
    return state


def built(budget, *, took=600, used=650, origin="user_explicit"):
    """A run whose Builder built milestone M1 once on GPT-6 Sol Medium for `took` seconds, failed, and climbed to
    High, with `used` seconds counted on M1 so far under a milestone budget of `budget` seconds that `origin` set.
    Its Tester checked once on GPT-6 Sol High for as long and climbed to XHigh too."""
    builder = attempt(route("medium"), stage="terra", role="terra", started_at=EARLIER, duration_seconds=took)
    state = {
        "settings": {
            "engine": "codex",
            "roles": {"terra": route("medium"), "sol": route("high")},
            "limits": {"stage_timeout_seconds": 3600},
            "milestone_checkpoints": {"enabled": True, "max_seconds": budget},
            "budget_origins": {"milestone_max_seconds": origin},
        },
        "goal_contract": {"hash": "contract"},
        "current_task": {"id": "task1", "milestone_id": "M1", "contract_hash": "contract"},
        "milestone_progress": {"contract:M1": {"id": "M1", "contract_hash": "contract", "seconds": used}},
        "stages": [builder, tested("high", started_at=EARLIER, duration_seconds=took)],
    }
    for role, rung in (("terra", "GPT-6 Sol High"), ("sol", "GPT-6 Sol XHigh")):
        event = escalation.advance(state, role, trigger="validation_rework", detail="FAIL: Version prints 1")
        assert event and event["selected"]["profile"] == rung, event
    return state


class ClimbCapTests(unittest.TestCase):
    def test_a_climb_projected_past_the_cap_pauses_before_its_attempt_launches(self):
        # Spent: the High attempt, $0.004. Projected: the same tokens on the climbed route, $0.004. $0.008 > $0.006.
        state = climbed(0.006)
        self.assertEqual(
            "The Tester's climb to GPT-6 Sol XHigh (gpt-6-sol at xhigh effort) did not launch: projected at $0.004, "
            "the last Tester attempt's 1,000 input and 200 output tokens at gpt-6-sol's price, it would take the "
            "spend from $0.004 to $0.008, past the $0.006 cost cap. AutoCode never raises the cap; resume with "
            "--max-cost-usd 0.01 or more (0 for no cap) to make this attempt",
            stop(state, "sol", "sol"),
        )
        # The climb stands: a cap of at least the total, or none, lets that attempt launch.
        self.assertEqual(route("xhigh"), state["settings"]["roles"]["sol"])
        for cap in (0.008, 0.01, 0):
            with self.subTest(cap=cap):
                state["settings"]["limits"]["max_cost_usd"] = cap
                climb_cap.guard(state, "sol", "sol")

    def test_only_a_climb_whose_attempt_has_not_launched_is_priced(self):
        state = climbed(0.006)
        event, last = climb_cap.pending(state, "sol")
        self.assertEqual(("GPT-6 Sol XHigh", route("high")), (event["selected"]["profile"], last["launch_route"]))
        # A report repair or a dry run on the climbed rung is not its attempt, nor is one made before the climb.
        for row in (
            tested("xhigh", report_only=True),
            tested("xhigh", dry_run=True),
            tested("xhigh", started_at="1999-12-31T00:00:00+00:00"),
        ):
            with self.subTest(row=row):
                stop({**state, "stages": [row, *state["stages"]]}, "sol", "sol")
        # Once the climbed attempt has launched, the stage-boundary check governs the spend. An attempt on the rung
        # whose start cannot be read counts as launched: a guess never pauses the run.
        undated = tested("xhigh")
        del undated["started_at"]
        for row in (tested("xhigh"), undated):
            with self.subTest(row=row):
                launched = {**state, "stages": [*state["stages"], row]}
                self.assertIsNone(climb_cap.pending(launched, "sol"))
                climb_cap.guard(launched, "sol", "sol")
        # An attempt without a climb is not priced here.
        climb_cap.guard({**state, "reasoning_escalations": []}, "sol", "sol")
        # A route the person set after the climb is theirs, not the climb's.
        chosen = climbed(0.006)
        chosen["settings"]["roles"]["sol"]["reasoning_effort"] = "max"
        self.assertIsNone(climb_cap.pending(chosen, "sol"))
        climb_cap.guard(chosen, "sol", "sol")

    def test_a_rung_restored_after_its_climbed_attempt_ran_is_not_a_climb_again(self):
        # M1: the Tester fails on GPT-6 Sol High, climbs to XHigh, and the XHigh attempt runs.
        high = route("high", "openai/gpt-6-sol", "opencode")
        state = {
            "settings": {
                "engine": "opencode",
                "roles": {
                    "terra": route("high", "zai-coding-plan/glm-5.3", "opencode"),
                    "sol": dict(high),
                    "completion": route("medium", "openai/gpt-6-sol", "opencode"),
                },
                "builder_retry": dict(builder_policy.DEFAULTS),
                "limits": {},
            },
            "goal_contract": {"hash": "contract"},
            "current_task": {"milestone_id": "M1"},
            "stages": [attempt(high, started_at=EARLIER)],
        }
        escalation.advance(state, "sol", trigger="validation_rework", detail="FAIL")
        state["stages"].append(attempt(state["settings"]["roles"]["sol"]))
        # M2: the Builder's stronger attempt runs GPT-6 Sol, so the Tester moves to GLM to check it.
        state["current_task"] = {"milestone_id": "M2"}
        lane = builder_policy.lane(state)
        builder_policy.swap_checkers(state, lane, state["settings"]["builder_retry"], "openai/gpt-6-sol")
        state["stages"].append(attempt(state["settings"]["roles"]["sol"]))
        # M3: the next milestone's lane puts the Tester back on the climbed rung, whose attempt ran in M1.
        state["current_task"] = {"milestone_id": "M3"}
        builder_policy.lane(state)
        self.assertEqual(route("xhigh", "openai/gpt-6-sol", "opencode"), state["settings"]["roles"]["sol"])
        state["settings"]["limits"]["max_cost_usd"] = round(climb_cap.cost.spent(state) + 0.002, 6)
        self.assertIsNone(climb_cap.pending(state, "sol"))
        climb_cap.guard(state, "sol", "sol")

    def test_the_completion_reviewer_is_priced_from_its_attempts_on_its_route(self):
        state = {
            "settings": {
                "engine": "codex",
                "roles": {"completion": route("medium")},
                "limits": {"max_cost_usd": 0.006},
            },
            "stages": [reviewed("medium", started_at=EARLIER)],
        }
        escalation.advance(state, "completion", trigger="rejected_output", detail="invalid report")
        self.assertIn(
            "The Completion Reviewer's climb to GPT-6 Sol High (gpt-6-sol at high effort) did not launch: projected "
            "at $0.004, the last Completion Reviewer attempt's 1,000 input and 200 output tokens",
            stop(state, "astra_review", "completion"),
        )

    def test_the_resolvers_climb_is_priced_on_its_own_route_role(self):
        """The Resolver's stages run on route role "resolver", which climbs GPT-6 Astra (#1345); the build loop
        passes that route role to guard(), so the climb is priced from the last Resolver attempt."""
        astra = route("high", "gpt-6-astra")
        state = {
            "settings": {
                "engine": "codex",
                "roles": {"astra": dict(astra), "resolver": dict(astra)},
                "limits": {"max_cost_usd": 0.03},
            },
            "stages": [attempt(astra, stage="astra_resolve", role="astra", route_role="resolver", started_at=EARLIER)],
        }
        escalation.advance(state, "resolver", trigger="rejected_output", detail="invalid report")
        # 1,000 input and 200 output tokens at GPT-6 Astra's $10 / $50 per million cost $0.02.
        self.assertIn(
            "The Resolver's climb to GPT-6 Astra XHigh (gpt-6-astra at xhigh effort) did not launch: projected at "
            "$0.02, the last Resolver attempt's 1,000 input and 200 output tokens at gpt-6-astra's price, it would "
            "take the spend from $0.02 to $0.04, past the $0.03 cost cap",
            stop(state, "astra_resolve", "resolver"),
        )
        self.assertIn("The Resolver's climb to GPT-6 Astra XHigh", stop(state, "astra_diagnose", "resolver"))

    def test_a_rejected_task_plan_leaves_no_astra_climb_to_price(self):
        """Before #1345 the per-task Planner's astra route climbed GPT-6 Astra on a rejected report, and this cap
        paused its next attempt; the astra route is on no ladder now, so that attempt launches on its route."""
        astra = route("high", "gpt-6-astra")
        state = {
            "settings": {"engine": "codex", "roles": {"astra": dict(astra)}, "limits": {"max_cost_usd": 0.03}},
            "stages": [attempt(astra, stage="astra_plan", role="astra", started_at=EARLIER)],
        }
        escalation.advance(state, "astra", trigger="rejected_output", detail="invalid task plan")
        # Spent $0.02; a climbed attempt projected at another $0.02 would pass the $0.03 cap.
        climb_cap.guard(state, "astra_plan", "astra")
        self.assertEqual(astra, state["settings"]["roles"]["astra"])

    def test_each_role_is_priced_from_its_own_climb_and_attempt(self):
        # The Tester climbs High -> XHigh, then the Completion Reviewer Medium -> High.
        tester, reviewer = {"input_tokens": 100, "output_tokens": 20}, {"input_tokens": 200, "output_tokens": 40}
        state = {
            "settings": {
                "engine": "codex",
                "roles": {"sol": route("high"), "completion": route("medium")},
                "limits": {"max_cost_usd": 0.0013},
            },
            "stages": [tested("high", tester, started_at=EARLIER), reviewed("medium", reviewer, started_at=EARLIER)],
        }
        escalation.advance(state, "sol", trigger="validation_rework")
        escalation.advance(state, "completion", trigger="rejected_output")
        # Spent: the Tester's $0.0004 and the Completion Reviewer's $0.0008.
        self.assertIn(
            "The Tester's climb to GPT-6 Sol XHigh (gpt-6-sol at xhigh effort) did not launch: projected at $0.0004, "
            "the last Tester attempt's 100 input and 20 output tokens at gpt-6-sol's price, it would take the spend "
            "from $0.0012 to $0.0016",
            stop(state, "sol", "sol"),
        )
        self.assertIn(
            "The Completion Reviewer's climb to GPT-6 Sol High (gpt-6-sol at high effort) did not launch: projected "
            "at $0.0008, the last Completion Reviewer attempt's 200 input and 40 output tokens",
            stop(state, "astra_review", "completion"),
        )
        # The Completion Reviewer's climbed attempt runs; the Tester's has not.
        state["stages"].append(reviewed("high", {"input_tokens": 0, "output_tokens": 0}))
        climb_cap.guard(state, "astra_review", "completion")
        self.assertIn("The Tester's climb to GPT-6 Sol XHigh", stop(state, "sol", "sol"))

    def test_a_second_climb_is_priced_from_the_attempt_on_the_rung_before_it(self):
        # High (100 and 20 tokens) climbs to XHigh, whose attempt (300 and 60 tokens) fails too and climbs to Max.
        state = climbed(0.002, tokens={"input_tokens": 100, "output_tokens": 20})
        state["stages"].append(tested("xhigh", {"input_tokens": 300, "output_tokens": 60}))
        escalation.advance(state, "sol", trigger="validation_rework", detail="FAIL again")
        self.assertIn(
            "The Tester's climb to GPT-6 Sol Max (gpt-6-sol at max effort) did not launch: projected at $0.0012, "
            "the last Tester attempt's 300 input and 60 output tokens at gpt-6-sol's price, it would take the spend "
            "from $0.0016 to $0.0028, past the $0.002 cost cap",
            stop(state, "sol", "sol"),
        )

    def test_a_climb_without_a_projection_never_pauses(self):
        # No token counts, no earlier attempt, a climb without a readable time, or a parallel Builder's partial view.
        undated = climbed(0.000001)
        undated["reasoning_escalations"][-1]["at"] = "unknown"
        for name, state in (
            ("no token counts", climbed(0.000001, tokens={})),
            ("no earlier attempt", {**climbed(0.000001), "stages": []}),
            ("climb time unreadable", undated),
            ("parallel Builder", {**climbed(0.000001), "parent_run": "/runs/parent"}),
        ):
            with self.subTest(name):
                climb_cap.guard(state, "sol", "sol")


class ClimbTimeTests(unittest.TestCase):
    """The same climbs, projected against the active-time budget you set with --max-seconds."""

    def test_a_climb_projected_past_the_time_budget_pauses_before_its_attempt_launches(self):
        # Used: 650 s. Projected: the High attempt's 600 s. 1,250 s > the 1,000 s budget.
        state = timed(1000)
        self.assertEqual(
            "The Tester's climb to GPT-6 Sol XHigh (gpt-6-sol at xhigh effort) did not launch: projected at 600 s of "
            "active time, the last Tester attempt's duration, it would take the run's active time from 650 s to "
            "1,250 s, past the 1,000 s active-time budget you set. AutoCode never raises a budget you set; resume "
            "with --max-seconds 1250 or more (0 for no limit) to make this attempt",
            stop(state, "sol", "sol", "PAUSED_TIME_LIMIT"),
        )
        # The climb stands: a budget of at least the total, or none, lets that attempt launch.
        self.assertEqual(route("xhigh"), state["settings"]["roles"]["sol"])
        for budget in (1250, 5000, 0):
            with self.subTest(budget=budget):
                state["settings"]["limits"]["max_seconds"] = budget
                climb_cap.guard(state, "sol", "sol")

    def test_the_projection_is_the_last_attempts_duration_held_to_the_stage_time_limit(self):
        settings = {"limits": {"stage_timeout_seconds": 3600}}
        self.assertEqual(
            {"seconds": 42.5, "held": None}, climb_cap.projected_seconds(settings, {"duration_seconds": 42.5})
        )
        # The stage time limit stops an attempt, so one that ran past it (a timeout's teardown) is held to it.
        self.assertEqual(
            {"seconds": 3600, "held": 3600}, climb_cap.projected_seconds(settings, {"duration_seconds": 3700})
        )
        self.assertEqual(
            {"seconds": 3700, "held": None},
            climb_cap.projected_seconds({"limits": {"stage_timeout_seconds": 0}}, {"duration_seconds": 3700}),
        )
        for record in ({}, {"duration_seconds": None}, {"duration_seconds": -1}, {"duration_seconds": True}, None):
            with self.subTest(record=record):
                self.assertIsNone(climb_cap.projected_seconds(settings, record))
        self.assertIn(
            "projected at 3,600 s of active time, the last Tester attempt's duration, held to the 3,600 s stage time "
            "limit, it would take the run's active time from 650 s to 4,250 s, past the 1,000 s active-time budget",
            stop(timed(1000, took=3700), "sol", "sol", "PAUSED_TIME_LIMIT"),
        )

    def test_the_arithmetic_and_when_the_attempt_fits(self):
        self.assertEqual(
            {"projected": 600, "held": None, "used": 650, "total": 1250, "budget": 1000},
            climb_cap.time_crossing(timed(1000), timed(1000)["stages"][-1]),
        )
        # A total at the budget fits; the stage-boundary check then pauses after the attempt, as before.
        self.assertIsNone(climb_cap.time_crossing(timed(1250), timed(1250)["stages"][-1]))
        climb_cap.guard(timed(1250), "sol", "sol")
        # Fractions are shown to one place and the budget to resume with is rounded up to a whole second.
        short = timed(1, took=0.37, used=0.81)
        self.assertIn(
            "projected at 0.4 s of active time, the last Tester attempt's duration, it would take the run's active "
            "time from 0.8 s to 1.2 s, past the 1 s active-time budget you set. AutoCode never raises a budget you "
            "set; resume with --max-seconds 2 or more",
            stop(short, "sol", "sol", "PAUSED_TIME_LIMIT"),
        )

    def test_only_a_budget_you_set_is_projected(self):
        # No budget, a disabled one, or one AutoResolver may extend at the stage boundary (the runner's default,
        # or one delegated with --autoresolver-managed-limits), and a run saved without its budget's origin.
        for name, state in (
            ("runner default", timed(1000, origin="runner_default")),
            ("delegated", timed(1000, origin="resolver_delegated")),
            ("disabled", timed(0)),
            ("no origin", {**timed(1000), "settings": {**timed(1000)["settings"], "budget_origins": {}}}),
        ):
            with self.subTest(name):
                self.assertIsNone(climb_cap.budget_seconds(state["settings"]))
                climb_cap.guard(state, "sol", "sol")
        self.assertEqual(1000, climb_cap.budget_seconds(timed(1000)["settings"]))

    def test_a_climb_without_a_time_projection_never_pauses(self):
        undated = timed(1000)
        del undated["stages"][-1]["duration_seconds"]
        # The launched XHigh attempt took 600 s: were it still taken for pending, 650 s + 600 s would pass the budget.
        launched = timed(1000)
        launched["stages"].append(tested("xhigh", duration_seconds=600))
        for name, state in (
            ("no duration", undated),
            ("climbed attempt launched", launched),
            ("parallel Builder", {**timed(1000), "parent_run": "/runs/parent"}),
        ):
            with self.subTest(name):
                climb_cap.guard(state, "sol", "sol")

    def test_the_cost_stop_comes_first_when_both_budgets_would_be_crossed(self):
        state = timed(1000)
        state["settings"]["limits"]["max_cost_usd"] = 0.006
        self.assertIn("past the $0.006 cost cap", stop(state, "sol", "sol"))
        state["settings"]["limits"]["max_cost_usd"] = 0.01
        self.assertIn("past the 1,000 s active-time budget", stop(state, "sol", "sol", "PAUSED_TIME_LIMIT"))


class ClimbMilestoneTests(unittest.TestCase):
    """A Builder's climb, projected against the milestone budget you set with --max-milestone-seconds."""

    def test_a_builder_climb_projected_past_the_milestone_budget_pauses_before_its_attempt_launches(self):
        # Used: 650 s on M1. Projected: the Medium attempt's 600 s. 1,250 s > the 1,000 s milestone budget.
        state = built(1000)
        self.assertEqual(
            "The Builder's climb to GPT-6 Sol High (gpt-6-sol at high effort) did not launch: projected at 600 s of "
            "active time, the last Builder attempt's duration, it would take this milestone's active time from 650 s "
            "to 1,250 s, past the 1,000 s milestone budget you set. AutoCode never raises a budget you set; resume "
            "with --max-milestone-seconds 1250 or more (0 for no limit), which also restarts this milestone's count, "
            "to make this attempt",
            stop(state, "terra", "terra", "PAUSED_MILESTONE_BUDGET"),
        )
        # The climb stands: a budget that admits the total, none, or a restarted count lets that attempt launch.
        self.assertEqual(route("high"), state["settings"]["roles"]["terra"])
        for budget, used in ((1250, 650), (0, 650), (1000, 0)):
            with self.subTest(budget=budget, used=used):
                climb_cap.guard(built(budget, used=used), "terra", "terra")
        # The projection is held to the stage time limit.
        self.assertIn(
            "projected at 3,600 s of active time, the last Builder attempt's duration, held to the 3,600 s stage time "
            "limit, it would take this milestone's active time from 0 s to 3,600 s, past the 1,000 s milestone budget "
            "you set. AutoCode never raises a budget you set; resume with --max-milestone-seconds 3600 or more",
            stop(built(1000, took=3700, used=0), "terra", "terra", "PAUSED_MILESTONE_BUDGET"),
        )

    def test_the_milestone_budget_gates_only_the_builder(self):
        # The Tester's climb, with the same milestone numbers: that budget gates writer launches only.
        state = built(1000)
        self.assertIsNotNone(climb_cap.pending(state, "sol"))
        climb_cap.guard(state, "sol", "sol")
        self.assertEqual(
            {"projected": 600, "held": None, "used": 650, "total": 1250, "budget": 1000},
            climb_cap.milestone_crossing(state, state["stages"][-1]),
        )

    def test_only_a_milestone_budget_you_set_on_the_current_milestone_is_projected(self):
        disabled = built(1000)
        disabled["settings"]["milestone_checkpoints"]["enabled"] = False
        progressive = built(1000)
        progressive["progressive"] = {"version": 1, "budget": {"run_limit": 3600}}
        revised = built(1000)
        revised["current_task"]["contract_hash"] = "superseded"
        unassigned = built(1000)
        unassigned["current_task"]["milestone_id"] = "M2"
        launched = built(1000)
        launched["stages"].append(attempt(route("high"), stage="terra", role="terra", duration_seconds=600))
        for name, state in (
            ("runner default", built(1000, origin="runner_default")),
            ("delegated", built(1000, origin="resolver_delegated")),
            ("no limit", built(0)),
            ("checkpoints off", disabled),
            ("progressive slice budget", progressive),
            ("task on another contract", revised),
            ("no row for the current milestone", unassigned),
            ("climbed attempt launched", launched),
            ("parallel Builder", {**built(1000), "parent_run": "/runs/parent"}),
        ):
            with self.subTest(name):
                climb_cap.guard(state, "terra", "terra")
        self.assertEqual(1000, climb_cap.milestone_budget_seconds(built(1000)["settings"]))
        self.assertNotIn("contract:M2", unassigned["milestone_progress"], "the check never creates a milestone row")

    def test_the_cost_and_run_time_stops_come_before_the_milestone_stop(self):
        # Spent: the Builder's and the Tester's $0.004 each. Projected: another $0.004; $0.012 (shown as $0.01) > $0.01.
        state = built(1000)
        state["settings"]["limits"].update(max_cost_usd=0.01, max_seconds=1000)
        state["settings"]["budget_origins"]["max_seconds"] = "user_explicit"
        state["active_seconds"] = 650
        self.assertIn("from $0.008 to $0.01, past the $0.01 cost cap", stop(state, "terra", "terra"))
        state["settings"]["limits"]["max_cost_usd"] = 0.02
        self.assertIn("past the 1,000 s active-time budget", stop(state, "terra", "terra", "PAUSED_TIME_LIMIT"))
        state["settings"]["limits"]["max_seconds"] = 0
        self.assertIn("past the 1,000 s milestone budget", stop(state, "terra", "terra", "PAUSED_MILESTONE_BUDGET"))


if __name__ == "__main__":
    unittest.main()
