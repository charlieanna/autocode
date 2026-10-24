"""The Planner's one stronger attempt after a revised plan is blocked again (autocode_planner_escalation)."""

import json
import unittest

import autocode_planner_escalation as escalation
import autocode_route_ladder as route_ladder

from . import test_planning


def settings(planner="zai-coding-plan/glm-5.3", reviewer="openai/gpt-6-sol", **extra):
    base = {
        "engine": "opencode",
        "roles": {
            "glm": {"engine": "opencode", "provider": None, "model": planner, "reasoning_effort": "high"},
            "plan_reviewer": {"engine": "opencode", "provider": None, "model": reviewer, "reasoning_effort": "high"},
        },
        "route_ladders": route_ladder.configure_ladders(),
    }
    base.update(extra)
    return base


class Decide(unittest.TestCase):
    def test_a_glm_planner_reviewed_by_sol_moves_to_opus(self):
        self.assertEqual(
            {"model": "anthropic/claude-opus-5-5", "reasoning_effort": "high", "profile": "Claude Opus 5.5 High"},
            escalation.decide(settings()),
        )

    def test_an_opus_reviewer_sends_the_planner_to_sol_instead(self):
        self.assertEqual("openai/gpt-6-sol", escalation.decide(settings(reviewer="anthropic/claude-opus-5-5"))["model"])

    def test_no_target_outside_the_reviewers_family_is_a_stay(self):
        # Only GPT models are served; the GPT-6 Sol reviewer would grade its own family's plan.
        ladders = route_ladder.configure_ladders(["openai/gpt-6-sol", "zai-coding-plan/glm-5.3"])
        self.assertEqual({"stay": "no_target"}, escalation.decide(settings(route_ladders=ladders)))

    def test_a_gateway_spelling_is_kept_as_the_provider_lists_it(self):
        ladders = route_ladder.configure_ladders(["kilo/anthropic/claude-opus-5-5", "kilo/z-ai/glm-5.3"])
        self.assertEqual("kilo/anthropic/claude-opus-5-5", escalation.decide(settings(route_ladders=ladders))["model"])

    def test_launch_keeps_only_the_rungs_the_provider_offers(self):
        served = route_ladder.serve(settings(), ["kilo/anthropic/claude-opus-5-5", "openai/gpt-6-sol"])
        self.assertEqual(
            [
                ["kilo/anthropic/claude-opus-5-5", "high", "Claude Opus 5.5 High"],
                ["openai/gpt-6-sol", "high", "GPT-6 Sol High"],
            ],
            served["route_ladders"]["planner"],
        )

    def test_saved_accounts_decide_what_is_permitted(self):
        selection = {"candidates": {"plan_reviewer": [{"model": "openai/gpt-6-sol"}]}}
        self.assertEqual({"stay": "no_target"}, escalation.decide(settings(route_selection=selection)))
        selection["candidates"]["builder"] = [{"model": "kilo/anthropic/claude-opus-5-5", "levels": ["high"]}]
        self.assertEqual(
            "kilo/anthropic/claude-opus-5-5", escalation.decide(settings(route_selection=selection))["model"]
        )

    def test_the_claude_provider_never_lets_opus_check_opus(self):
        claude = settings(planner="claude-sonnet-5-5", reviewer="claude-opus-5-5", provider="claude")
        claude["route_ladders"] = route_ladder.configure_ladders(["claude-sonnet-5-5", "claude-opus-5-5"])
        self.assertEqual({"stay": "no_target"}, escalation.decide(claude))
        claude["roles"]["plan_reviewer"]["model"] = "claude-sonnet-5-5"
        claude["roles"]["glm"]["model"] = "claude-haiku-4-5-20251001"
        self.assertEqual("claude-opus-5-5", escalation.decide(claude)["model"])

    def test_stays(self):
        pinned = settings()
        pinned["roles"]["glm"]["model_pinned"] = True
        custom = settings()
        custom["roles"]["glm"]["provider"] = "custom"
        codex = settings()
        codex["roles"]["glm"]["engine"] = "codex"
        cases = {
            "pinned": pinned,
            "other_provider": custom,
            "engine": codex,
            "already_strong": settings(planner="anthropic/claude-opus-5-5"),
            "fixed_profile": settings(conversation_profile="continuous-v1"),
            "no_route": {"roles": {}},
        }
        for reason, case in cases.items():
            with self.subTest(reason):
                self.assertEqual({"stay": reason}, escalation.decide(case))
        self.assertEqual({"stay": "climbed"}, escalation.decide(settings(), [{"role": "glm"}]))

    def test_a_run_saved_before_planner_rungs_existed_never_climbs(self):
        saved = settings()
        saved["route_ladders"].pop("planner")
        self.assertEqual({"stay": "no_target"}, escalation.decide(saved))


class PlanSentBackTwice(unittest.TestCase):
    """Through the CLI with the fake provider, whose Plan Reviewer blocks every draft it challenges."""

    # The joint-planning fixture, without inheriting its tests.
    setUp = test_planning.JointFlow.setUp
    saved = test_planning.JointFlow.saved
    launch = test_planning.JointFlow.launch
    prepare = test_planning.JointFlow.prepare
    new_run_engine_args = ()

    def plan(self, review_call_limit=None, *launch):
        self.prepare()
        if review_call_limit is not None:
            # A new run's allowance from the user's config.toml (#1565); the fixture's XDG_CONFIG_HOME.
            config = self.root / "config" / "autocode" / "config.toml"
            config.parent.mkdir(parents=True, exist_ok=True)
            config.write_text(f"plan_review_call_limit = {review_call_limit}\n")
        self.launch(["Build a greeting tool", "--no-chat", *launch], 2)
        run, _ = self.saved()
        self.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)
        self.launch(["--run-dir", str(run), "--no-chat"], 2)
        return self.saved()

    def planning_stages(self, state):
        """The planning stages after the draft that answered the Requirements question, with their launch models."""
        rows = [row for row in state["stages"] if row["stage"] != "requirements_gather"]
        drafts = [index for index, row in enumerate(rows) if row["stage"] == "astra_discovery"]
        return [(row["stage"], (row.get("launch_route") or {}).get("model")) for row in rows[drafts[-1] :]]

    def test_the_second_blocked_challenge_moves_the_next_revision_to_a_stronger_model(self):
        # The fake provider lists GPT, GLM and MiMo models but no Claude one, so the launch keeps only the
        # GPT-6 Sol rung; a MiMo Plan Reviewer leaves it outside the reviewer's family.
        run, state = self.plan(3, "--plan-reviewer-model", "xiaomi-token-plan-sgp/mimo-v2.6-pro")
        self.assertEqual(
            [["openai/gpt-6-sol", "high", "GPT-6 Sol High"]], state["settings"]["route_ladders"]["planner"]
        )
        self.assertEqual("AWAITING_GOAL_APPROVAL", state["status"])
        stages = [stage for stage, _model in self.planning_stages(state)]
        self.assertEqual(
            ["astra_discovery", "astra_challenge", "glm_revise", "astra_challenge", "glm_revise", "astra_finalize"],
            stages,
        )
        revisions = [model for stage, model in self.planning_stages(state) if stage == "glm_revise"]
        self.assertEqual(["zai-coding-plan/glm-5.3", "openai/gpt-6-sol"], revisions)
        [event] = [row for row in state["reasoning_escalations"] if row["role"] == "glm"]
        self.assertEqual("plan_sent_back", event["trigger"])
        self.assertIn("challenge 2", event["detail"])
        self.assertEqual("openai/gpt-6-sol", state["settings"]["roles"]["glm"]["model"])
        rotated = [row for row in state.get("session_rotations", []) if row["role"] == "glm"]
        self.assertEqual(1, len(rotated))
        self.assertNotEqual(rotated[0]["old_session"], state["sessions"].get("glm"))
        view = json.loads(self.launch(["--run-dir", str(run), "--status", "--json"], 0).stdout)["view"]
        self.assertEqual(1, sum(view["escalation_outcomes"].values()))

    def test_under_the_default_allowance_the_planner_never_climbs(self):
        _run, state = self.plan()
        self.assertEqual("AWAITING_GOAL_APPROVAL", state["status"])
        self.assertEqual(
            ["astra_discovery", "astra_challenge", "glm_revise", "astra_finalize"],
            [stage for stage, _model in self.planning_stages(state)],
        )
        self.assertFalse([row for row in state.get("reasoning_escalations", []) if row["role"] == "glm"])
        self.assertEqual("zai-coding-plan/glm-5.3", state["settings"]["roles"]["glm"]["model"])


if __name__ == "__main__":
    unittest.main()
