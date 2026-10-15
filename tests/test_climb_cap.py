"""A role's climb on its effort ladder is priced against the cost cap before its attempt launches (#611)."""

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


def stop(state, stage, role):
    """The stop reason guard() raises before `stage` launches on `role`'s route."""
    try:
        climb_cap.guard(state, stage, role)
    except climb_cap.util.Paused as error:
        assert error.status == "PAUSED_COST_LIMIT", error.status
        return str(error)
    raise AssertionError(f"{stage} launched on {role}'s route without a cost pause")


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


if __name__ == "__main__":
    unittest.main()
