"""A role's climb on its effort ladder is priced against the cost cap before its attempt launches (#611)."""

import unittest

import autocode_climb_cap as climb_cap
import autocode_escalation as escalation

# 1,000 input and 200 output tokens at GPT-6 Sol's $2 / $10 per million (tools/autocode_prices.json) cost $0.004.
TOKENS = {"input_tokens": 1000, "output_tokens": 200}


def route(effort):
    return {"engine": "codex", "provider": None, "model": "gpt-6-sol", "reasoning_effort": effort}


def tested(effort, tokens=TOKENS, **extra):
    """A finished Tester attempt on GPT-6 Sol at `effort`."""
    return {
        "stage": "sol",
        "role": "sol",
        "launch_route": route(effort),
        "metrics": {"provider_tokens": tokens},
        **extra,
    }


def climbed(cap, tokens=TOKENS):
    """A run whose Tester checked once on GPT-6 Sol High, failed, and climbed to XHigh, under a cost cap of `cap`."""
    state = {
        "settings": {"engine": "codex", "roles": {"sol": route("high")}, "limits": {"max_cost_usd": cap}},
        "sessions": {"sol": "tester-session"},
        "stages": [tested("high", tokens)],
    }
    event = escalation.advance(state, "sol", trigger="validation_rework", detail="FAIL: Version prints 1")
    assert event and event["selected"]["profile"] == "GPT-6 Sol XHigh", event
    return state


class ClimbCapTests(unittest.TestCase):
    def test_a_climb_projected_past_the_cap_pauses_before_its_attempt_launches(self):
        # Spent: the High attempt, $0.004. Projected: the same tokens on the climbed route, $0.004. $0.008 > $0.006.
        state = climbed(0.006)
        with self.assertRaises(climb_cap.util.Paused) as stop:
            climb_cap.guard(state, "sol", "sol")
        self.assertEqual("PAUSED_COST_LIMIT", stop.exception.status)
        self.assertEqual(
            "The Tester's climb to GPT-6 Sol XHigh (gpt-6-sol at xhigh effort) did not launch: projected at $0.004, "
            "the last Tester attempt's 1,000 input and 200 output tokens at gpt-6-sol's price, it would take the "
            "spend from $0.004 to $0.008, past the $0.006 cost cap. AutoCode never raises the cap; resume with "
            "--max-cost-usd 0.01 or more (0 for no cap) to make this attempt",
            str(stop.exception),
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
        # A report repair or a dry run on the climbed route is not its attempt.
        for row in (tested("xhigh", report_only=True), tested("xhigh", dry_run=True)):
            with self.subTest(row=row), self.assertRaises(climb_cap.util.Paused):
                climb_cap.guard({**state, "stages": [*state["stages"], row]}, "sol", "sol")
        # Once the climbed attempt has launched, the stage-boundary check governs the spend.
        launched = {**state, "stages": [*state["stages"], tested("xhigh")]}
        self.assertIsNone(climb_cap.pending(launched, "sol"))
        climb_cap.guard(launched, "sol", "sol")
        # An attempt without a climb is not priced here, nor is another role's launch.
        unclimbed = {**state, "reasoning_escalations": []}
        climb_cap.guard(unclimbed, "sol", "sol")
        climb_cap.guard(state, "astra_review", "completion")
        # A route the person set after the climb is theirs, not the climb's.
        chosen = climbed(0.006)
        chosen["settings"]["roles"]["sol"]["reasoning_effort"] = "max"
        self.assertIsNone(climb_cap.pending(chosen, "sol"))
        climb_cap.guard(chosen, "sol", "sol")

    def test_a_climb_without_a_projection_never_pauses(self):
        # No token counts, no earlier attempt, or a parallel Builder's partial view of the spend.
        for name, state in (
            ("no token counts", climbed(0.000001, tokens={})),
            ("no earlier attempt", {**climbed(0.000001), "stages": []}),
            ("parallel Builder", {**climbed(0.000001), "parent_run": "/runs/parent"}),
        ):
            with self.subTest(name):
                climb_cap.guard(state, "sol", "sol")


if __name__ == "__main__":
    unittest.main()
