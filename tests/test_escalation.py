"""Automatic reasoning/model escalation tests; no provider calls."""

import sys
import unittest
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode_escalation as escalation
import autocode_route_ladder as route_ladder


class EscalationTests(unittest.TestCase):
    def state(self, role, model, effort, *, engine="opencode", provider=None):
        return {
            "settings": {
                "engine": engine,
                "roles": {role: {"engine": engine, "provider": provider, "model": model, "reasoning_effort": effort}},
            },
            "sessions": {role: "old-session"},
        }

    def test_exact_role_ladders(self):
        expected = {
            "resolver": ["GPT-6 Astra High", "GPT-6 Astra XHigh", "GPT-6 Astra Max"],
            "terra": ["GPT-6 Sol Medium", "GPT-6 Sol High", "GPT-6 Sol XHigh", "GPT-6 Sol Max"],
            "sol": ["GPT-6 Sol High", "GPT-6 Sol XHigh", "GPT-6 Sol Max"],
            "completion": ["GPT-6 Sol Medium", "GPT-6 Sol High", "GPT-6 Sol Max"],
        }
        self.assertEqual(expected, {role: [row[2] for row in ladder] for role, ladder in escalation.LADDERS.items()})

    def test_advance_changes_one_rung_and_rotates_session(self):
        state = self.state("resolver", "openai/gpt-6-astra", "high")
        event = escalation.advance(state, "resolver", trigger="rejected_output", detail="bad report")
        self.assertEqual("GPT-6 Astra XHigh", event["selected"]["profile"])
        self.assertEqual("xhigh", state["settings"]["roles"]["resolver"]["reasoning_effort"])
        self.assertEqual({}, state["sessions"])
        self.assertEqual("old-session", state["session_rotations"][0]["old_session"])
        self.assertEqual([event], state["reasoning_escalations"])

    def test_the_resolver_route_climbs_astra_high_xhigh_max_then_stops(self):
        """The Resolver stages run on route role "resolver" (#1345); both escalation call
        sites pass that route role, so its ladder must be keyed by it."""
        state = self.state("resolver", "openai/gpt-6-astra", "high")
        state["sessions"]["astra"] = "planner-session"
        first = escalation.advance(state, "resolver", trigger="rejected_output", detail="bad report")
        self.assertEqual("resolver", first["role"])
        self.assertEqual("GPT-6 Astra High", first["previous"]["profile"])
        self.assertEqual("GPT-6 Astra XHigh", first["selected"]["profile"])
        self.assertEqual(
            {"model": "openai/gpt-6-astra", "reasoning_effort": "xhigh"},
            {key: state["settings"]["roles"]["resolver"][key] for key in ("model", "reasoning_effort")},
        )
        # The Resolver's session rotates; the Planner's, on the astra route, is untouched.
        self.assertEqual({"astra": "planner-session"}, state["sessions"])
        self.assertEqual("resolver", state["session_rotations"][0]["role"])
        state["sessions"]["resolver"] = "xhigh-session"
        second = escalation.advance(state, "resolver", trigger="abandoned_attempt")
        self.assertEqual("GPT-6 Astra Max", second["selected"]["profile"])
        self.assertEqual("max", state["settings"]["roles"]["resolver"]["reasoning_effort"])
        self.assertNotIn("resolver", state["sessions"])
        state["sessions"]["resolver"] = "max-session"
        self.assertIsNone(escalation.advance(state, "resolver", trigger="rejected_output"))
        self.assertEqual("max", state["settings"]["roles"]["resolver"]["reasoning_effort"])
        self.assertEqual("max-session", state["sessions"]["resolver"])
        self.assertEqual([first, second], state["reasoning_escalations"])

    def test_the_astra_route_does_not_climb_gpt_6_astra(self):
        """The per-task Planner (astra_plan) runs on the astra route: #120 keeps it off the
        Astra ladder, so a rejected Planner report no longer raises a later Resolver's start."""
        for ladders in (None, route_ladder.configure_ladders(None)):
            with self.subTest(persisted=ladders is not None):
                state = self.state("astra", "openai/gpt-6-astra", "high")
                if ladders is not None:
                    state["settings"]["route_ladders"] = ladders
                before = deepcopy(state)
                self.assertIsNone(escalation.advance(state, "astra", trigger="rejected_output"))
                self.assertEqual(before, state)

    def test_a_saved_run_with_an_astra_keyed_ladder_climbs_the_resolver_instead(self):
        """Runs saved before #1345 persisted the Astra ladder under "astra"; it is read as the
        Resolver's, without migrating state.json, and the astra route stops climbing."""
        saved = {
            "effort": {
                "astra": [
                    ["openai/gpt-6-astra", "high", "GPT-6 Astra High"],
                    ["openai/gpt-6-astra", "xhigh", "GPT-6 Astra XHigh"],
                    ["openai/gpt-6-astra", "max", "GPT-6 Astra Max"],
                ],
                "sol": [["openai/gpt-6-sol", "high", "GPT-6 Sol High"], ["openai/gpt-6-sol", "max", "GPT-6 Sol Max"]],
            }
        }
        state = self.state("resolver", "openai/gpt-6-astra", "high")
        state["settings"]["roles"]["astra"] = {
            "engine": "opencode",
            "model": "openai/gpt-6-astra",
            "reasoning_effort": "high",
        }
        state["settings"]["route_ladders"] = deepcopy(saved)
        self.assertIsNone(escalation.advance(state, "astra", trigger="rejected_output"))
        self.assertEqual("high", state["settings"]["roles"]["astra"]["reasoning_effort"])
        event = escalation.advance(state, "resolver", trigger="rejected_output")
        self.assertEqual("GPT-6 Astra XHigh", event["selected"]["profile"])
        self.assertEqual("xhigh", state["settings"]["roles"]["resolver"]["reasoning_effort"])
        self.assertEqual(saved, state["settings"]["route_ladders"])  # read, never rewritten
        # A saved run whose provider served no Astra persisted the ladder empty: still no climb.
        state = self.state("resolver", "openai/gpt-6-astra", "high")
        state["settings"]["route_ladders"] = {"effort": {**saved["effort"], "astra": []}}
        self.assertIsNone(escalation.advance(state, "resolver", trigger="rejected_output"))
        self.assertEqual("high", state["settings"]["roles"]["resolver"]["reasoning_effort"])

    def test_rung_and_profile_read_the_same_ladder_as_advance(self):
        resolver = {"model": "openai/gpt-6-astra", "reasoning_effort": "xhigh"}
        self.assertEqual(1, escalation.rung("resolver", resolver))
        self.assertEqual("GPT-6 Astra XHigh", escalation.profile("resolver", resolver))
        self.assertIsNone(escalation.rung("astra", resolver))
        self.assertIsNone(escalation.profile("astra", resolver))
        # A saved run's persisted, serveability-filtered ladder decides, as it does for advance().
        unserved = {"route_ladders": route_ladder.configure_ladders(["openai/gpt-6-sol"])}
        self.assertIsNone(escalation.rung("resolver", resolver, unserved))
        self.assertIsNone(escalation.profile("resolver", resolver, unserved))
        legacy = {
            "route_ladders": {"effort": {"astra": [list(row) for row in route_ladder.EFFORT_LADDERS["resolver"]]}}
        }
        self.assertEqual("GPT-6 Astra XHigh", escalation.profile("resolver", resolver, legacy))
        self.assertIsNone(escalation.profile("astra", resolver, legacy))

    def test_second_sol_failure_climbs_to_sol_max(self):
        state = self.state("sol", "openai/gpt-6-sol", "xhigh")
        event = escalation.advance(state, "sol", trigger="validation_rework")
        self.assertEqual("openai/gpt-6-sol", state["settings"]["roles"]["sol"]["model"])
        self.assertEqual("max", state["settings"]["roles"]["sol"]["reasoning_effort"])
        self.assertEqual("GPT-6 Sol Max", event["selected"]["profile"])

    def test_only_the_resolver_climbs_astra_and_glm_roles_never_escalate(self):
        """Astra is only for the Resolver (user 2026-09-28); GLM roles, including the default
        Builder, are on no ladder (its stronger attempt is the Builder retry policy's)."""
        cases = [
            # The per-task Planner's route (astra_plan) and the old Plan Reviewer/Completion
            # route of runs saved without their own roles: not the Resolver's route role.
            ("astra", "openai/gpt-6-astra", "high"),
            ("sol", "openai/gpt-6-astra", "high"),
            ("completion", "openai/gpt-6-astra", "medium"),
            ("sol", "zai-coding-plan/glm-5.3", "high"),
            ("completion", "zai-coding-plan/glm-5.3", "medium"),
            ("terra", "zai-coding-plan/glm-5.3", "medium"),
            ("plan_reviewer", "openai/gpt-6-sol", "high"),
        ]
        for role, model, effort in cases:
            with self.subTest(role=role, model=model):
                state = self.state(role, model, effort)
                self.assertIsNone(escalation.advance(state, role, trigger="validation_rework"))
                self.assertEqual(model, state["settings"]["roles"][role]["model"])
        self.assertEqual(
            {"resolver"},
            {role for role, ladder in escalation.LADDERS.items() if any("astra" in row[0] for row in ladder)},
        )

    def test_same_failed_iteration_advances_only_one_rung(self):
        state = self.state("terra", "openai/gpt-6-sol", "medium")
        escalation.advance(state, "terra", trigger="no_progress", struggle_id="iteration:7")
        self.assertIsNone(escalation.advance(state, "terra", trigger="validation_rework", struggle_id="iteration:7"))
        self.assertEqual("high", state["settings"]["roles"]["terra"]["reasoning_effort"])
        self.assertEqual(1, len(state["reasoning_escalations"]))

    def test_codex_routes_keep_bare_model_names(self):
        state = self.state("completion", "gpt-6-sol", "high", engine="codex")
        escalation.advance(state, "completion", trigger="rejected_output")
        self.assertEqual("gpt-6-sol", state["settings"]["roles"]["completion"]["model"])
        self.assertEqual("max", state["settings"]["roles"]["completion"]["reasoning_effort"])

    def test_custom_provider_custom_profile_and_final_rung_are_stable(self):
        cases = [
            self.state("terra", "other/model", "medium"),
            self.state("terra", "openai/gpt-6-sol", "medium", provider="custom"),
            self.state("terra", "openai/gpt-6-sol", "max"),
            # MiMo is no longer on any ladder (user 2026-09-27): a MiMo route never escalates.
            self.state("terra", "xiaomi-token-plan-sgp/mimo-v2.6-pro", "medium"),
        ]
        for state in cases:
            before = state["settings"]["roles"]["terra"].copy()
            self.assertIsNone(escalation.advance(state, "terra", trigger="no_progress"))
            self.assertEqual(before, state["settings"]["roles"]["terra"])
            self.assertEqual("old-session", state["sessions"]["terra"])

    def test_pinned_role_keeps_requested_model_after_a_struggle(self):
        state = self.state("resolver", "openai/gpt-6-astra", "high")
        state["settings"]["roles"]["resolver"]["model_pinned"] = True
        self.assertIsNone(escalation.advance(state, "resolver", trigger="rejected_output"))
        self.assertEqual("openai/gpt-6-astra", state["settings"]["roles"]["resolver"]["model"])
        self.assertEqual("high", state["settings"]["roles"]["resolver"]["reasoning_effort"])
        self.assertEqual("old-session", state["sessions"]["resolver"])

    def test_continuous_profile_suppresses_promotion_without_changing_legacy_routes(self):
        for role, model, effort in (("terra", "openai/gpt-6-sol", "high"), ("resolver", "openai/gpt-6-astra", "high")):
            with self.subTest(role=role):
                state = self.state(role, model, effort)
                state["settings"]["conversation_profile"] = "continuous-v1"
                before = deepcopy(state)
                self.assertIsNone(escalation.advance(state, role, trigger="rejected_output"))
                self.assertEqual(before, state)
                # The same saved role outside the opt-in profile keeps its ladder.
                del state["settings"]["conversation_profile"]
                self.assertIsNotNone(escalation.advance(state, role, trigger="rejected_output"))
                self.assertEqual("xhigh", state["settings"]["roles"][role]["reasoning_effort"])

    def test_planning_roles_without_ladders_never_escalate(self):
        cases = [
            self.state("requirements_planner", "zai-coding-plan/glm-5.3", None),
            self.state("technical_planner", "zai-coding-plan/glm-5.3", None),
            self.state("plan_reviewer", "cursor-acp/claude-opus-5-5-high", None),
        ]
        cases[-1]["settings"]["roles"]["plan_reviewer"]["model_pinned"] = True
        for state in cases:
            role = next(iter(state["settings"]["roles"]))
            with self.subTest(role=role):
                before = state["settings"]["roles"][role].copy()
                self.assertIsNone(escalation.advance(state, role, trigger="rejected_output"))
                self.assertEqual(before, state["settings"]["roles"][role])
                self.assertEqual("old-session", state["sessions"][role])


if __name__ == "__main__":
    unittest.main()
