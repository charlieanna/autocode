"""A hard plan's Planner and Plan Reviewer run one level higher on the same model (owner decision 2026-11-09)."""

import copy
import unittest
from unittest import mock

import autocode_chat_cost as chat_cost
import autocode_configure as configure
import autocode_plan_difficulty as plan_difficulty
import autocode_review_effort as review_effort

MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"
GLM = "zai-coding-plan/glm-5.3"


def body(milestones=1, paths_per_milestone=2, path="src/app/module{index}.py"):
    """A drafted contract body: ``milestones`` milestones, each touching its own files."""
    rows = [
        {
            "id": f"M{number}",
            "affected_paths": [path.format(index=f"{number}_{count}") for count in range(paths_per_milestone)],
        }
        for number in range(1, milestones + 1)
    ]
    return {"milestones": rows, "initial_task": {"affected_paths": list(rows[0]["affected_paths"]) if rows else []}}


def run_state(draft=None, clarity="clear", **changes):
    """A run whose Planner (glm) and Plan Reviewer took the person's stage choices at high."""
    state = {
        "iteration": 1,
        "workflow": {"kind": "build", "clarity": clarity, "brief_size": "medium"},
        "goal_contract": {"body": draft if draft is not None else body()},
        "planning": {},
        "settings": {
            "roles": {
                "planner": {"model": MIMO, "reasoning_effort": "high"},
                "plan_reviewer": {"model": GLM, "reasoning_effort": "high"},
                "builder": {"model": MIMO, "reasoning_effort": "high"},
            },
            "effort_origins": {role: "stage_choice" for role in ("planner", "plan_reviewer", "builder")},
            "declared_variants": {MIMO: ["low", "medium", "high", "xhigh"], GLM: ["low", "high", "max"]},
            "sensitive_paths": ["*billing*", "*billing*/**", "*.lock"],
        },
        "stages": [],
    }
    state.update(changes)
    return state


def decide(state, stage, role, configured="high", report_only=False, model=None):
    route = state["settings"]["roles"][role]
    return plan_difficulty.decide(state, stage, role, report_only, configured, model=model or route["model"])


def key(choice):
    return (choice["level"], choice.get("raised"), choice["reason"], choice.get("signals"))


class DecideTests(unittest.TestCase):
    def test_a_large_plan_raises_the_plan_reviewer_one_of_its_models_levels(self):
        state = run_state(body(milestones=3))
        for stage in ("review_plan", "finalize_plan"):
            # GLM 5.3 offers low, high and max: the next level it offers above high is max.
            self.assertEqual(("max", True, "hard_plan", ["large_plan"]), key(decide(state, stage, "plan_reviewer")))
        # Ten files in one milestone is large too, and the Planner's revision reads it, on its own model.
        wide = run_state(body(milestones=1, paths_per_milestone=10))
        self.assertEqual(("xhigh", True, "hard_plan", ["large_plan"]), key(decide(wide, "revise_plan", "planner")))

    def test_a_draft_touching_a_sensitive_file_raises_its_review(self):
        state = run_state(body(path="src/billing/charge{index}.py"))
        choice = decide(state, "review_plan", "plan_reviewer")
        self.assertEqual(("max", True, "hard_plan", ["sensitive_paths"]), key(choice))
        self.assertIn("sensitive files", choice["text"])
        # Without a saved list, the signal is not read: no list names nothing.
        del state["settings"]["sensitive_paths"]
        self.assertIsNone(decide(state, "review_plan", "plan_reviewer"))

    def test_a_second_block_raises_the_next_revision_only(self):
        state = run_state(planning={"adaptive": {"challenges": 2, "approved_at": None}})
        self.assertEqual(("xhigh", True, "hard_plan", ["blocked_again"]), key(decide(state, "revise_plan", "planner")))
        self.assertIsNone(decide(state, "review_plan", "plan_reviewer"), "a block is the Planner's signal")
        once = run_state(planning={"adaptive": {"challenges": 1, "approved_at": None}})
        self.assertIsNone(decide(once, "revise_plan", "planner"))
        approved = run_state(planning={"adaptive": {"challenges": 2, "approved_at": "2026-11-09T00:00:00Z"}})
        self.assertIsNone(decide(approved, "revise_plan", "planner"))

    def test_a_vague_request_raises_the_first_draft_but_a_large_draft_is_not_read_before_it_exists(self):
        vague = run_state(clarity="vague")
        self.assertEqual(("xhigh", True, "hard_plan", ["vague_request"]), key(decide(vague, "draft_plan", "planner")))
        # A contract left from an earlier cycle is not this draft: the first draft reads no plan size.
        stale = run_state(body(milestones=5))
        self.assertIsNone(decide(stale, "draft_plan", "planner"))

    def test_an_ordinary_plan_keeps_the_saved_level(self):
        state = run_state()
        for stage, role in plan_difficulty.STAGES.items():
            self.assertIsNone(decide(state, stage, role), stage)

    def test_the_strongest_level_or_unknown_levels_keep_the_saved_level_and_say_why(self):
        state = run_state(body(milestones=3))
        state["settings"]["roles"]["plan_reviewer"]["reasoning_effort"] = "max"
        self.assertEqual(
            ("max", False, "strongest_level", ["large_plan"]),
            key(decide(state, "review_plan", "plan_reviewer", configured="max")),
        )
        state["settings"]["roles"]["plan_reviewer"]["reasoning_effort"] = "high"
        del state["settings"]["declared_variants"]
        self.assertEqual(
            ("high", False, "levels_unknown", ["large_plan"]), key(decide(state, "review_plan", "plan_reviewer"))
        )
        # A level a probe passed counts beside the listing.
        state["settings"]["declared_variants"] = {GLM: ["high"]}
        state["settings"]["certified_variants"] = {GLM: ["xhigh"]}
        self.assertEqual("xhigh", decide(state, "review_plan", "plan_reviewer")["level"])

    def test_a_typed_level_a_climb_a_fallback_model_and_other_stages_are_left_alone(self):
        hard = run_state(body(milestones=3))
        typed = copy.deepcopy(hard)
        typed["settings"]["effort_origins"]["plan_reviewer"] = "user_explicit"
        self.assertIsNone(decide(typed, "review_plan", "plan_reviewer"))
        for origin in ("runner_default", "auto_selected"):
            default = copy.deepcopy(hard)
            default["settings"]["effort_origins"]["plan_reviewer"] = origin
            self.assertTrue(decide(default, "review_plan", "plan_reviewer")["raised"], origin)
        climbed = run_state(body(milestones=3), reasoning_escalations=[{"role": "plan_reviewer"}])
        self.assertIsNone(decide(climbed, "review_plan", "plan_reviewer"))
        self.assertIsNone(decide(hard, "review_plan", "plan_reviewer", model="openai/gpt-6-sol"))
        self.assertIsNone(decide(hard, "review_plan", "plan_reviewer", report_only=True))
        self.assertIsNone(decide(hard, "review_plan", "plan_reviewer", configured="medium"))
        self.assertIsNone(decide(hard, "builder", "builder"))
        self.assertIsNone(decide(hard, "review_plan", "planner"), "the stage's own route only")


class ListingTests(unittest.TestCase):
    LISTING = {
        "candidates": [
            {"model": MIMO, "levels": ["low", "high", "xhigh"]},
            {"model": GLM, "levels": ["high", "max"]},
        ]
    }

    def listed(self, origins, **settings_changes):
        settings = run_state()["settings"]
        del settings["declared_variants"]
        settings["effort_origins"] = origins
        settings.update(settings_changes)
        with mock.patch("autocode_route_discovery.discover", return_value=self.LISTING) as discover:
            configure._list_review_ceilings(settings, object(), "/fixture")
        return settings.get("declared_variants"), discover.call_count

    def test_a_new_run_reads_the_levels_of_a_stage_choice_planner_and_plan_reviewer(self):
        declared, calls = self.listed(
            {"planner": "stage_choice", "plan_reviewer": "stage_choice", "builder": "stage_choice"}
        )
        self.assertEqual(({MIMO: ["low", "high", "xhigh"], GLM: ["high", "max"]}, 1), (declared, calls))

    def test_selected_routes_reuse_the_selection_listing_and_fixed_or_typed_routes_read_nothing(self):
        selection = {"candidates": {"planner": [{"model": MIMO, "levels": ["high", "xhigh"]}], "builder": []}}
        origins = {"planner": "auto_selected", "plan_reviewer": "runner_default", "builder": "user_explicit"}
        declared, calls = self.listed(origins, route_selection=selection)
        self.assertEqual(({MIMO: ["high", "xhigh"]}, 0), (declared, calls))
        # A run that opted out of selection keeps its fixed routes and makes no listing call.
        self.assertEqual((None, 0), self.listed({"planner": "runner_default", "plan_reviewer": "runner_default"}))
        self.assertEqual(
            (None, 0), self.listed(dict.fromkeys(("planner", "plan_reviewer", "builder"), "user_explicit"))
        )


class LaunchTests(unittest.TestCase):
    def test_launch_passes_the_raised_level_and_a_raise_wins_over_a_small_tasks_lowered_review(self):
        # A small, clear plan would lower its review (autocode_review_effort); one touching billing is raised.
        state = run_state(body(path="src/billing/charge{index}.py"))
        state["workflow"]["brief_size"] = "small"
        route = state["settings"]["roles"]["plan_reviewer"]
        level, choice = review_effort.launch(state, "review_plan", "plan_reviewer", False, route)
        self.assertEqual(("max", True), (level, choice["raised"]))
        self.assertEqual(
            "Running at max, one level above your high: the plan is hard (it touches sensitive files).",
            review_effort.note(choice),
        )
        # Nothing hard: the small-task rule decides as before.
        plain = run_state()
        plain["workflow"]["brief_size"] = "small"
        plain["settings"]["declared_variants"][GLM] = ["low", "medium", "high", "max"]
        level, choice = review_effort.launch(plain, "review_plan", "plan_reviewer", False, route)
        self.assertEqual(("medium", True), (level, choice["lowered"]))

    def test_the_usage_details_say_why_an_attempt_ran_higher(self):
        choice = decide(run_state(body(milestones=3)), "review_plan", "plan_reviewer")
        lines = chat_cost._attempt({"job": "Plan Reviewer", "effort_choice": choice, "finished_at": "now"}, 1)
        self.assertIn("  One level above your high: the plan is hard (a large plan)", lines)
        self.assertIsNone(plan_difficulty.note({"raised": False}))
        self.assertIsNone(plan_difficulty.note({"raised": True, "level": "max"}))


if __name__ == "__main__":
    unittest.main()
