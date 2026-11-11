"""A small, clear task's reviews run one level below the saved stage-choice level (owner decision 2026-11-05)."""

import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_review_effort as review_effort

MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"
GLM = "zai-coding-plan/glm-5.3"


def run_state(**changes):
    """A run whose review stages took the person's stage choices at high: the 2026-11-05 slugify shape."""
    state = {
        "iteration": 1,
        "workflow": {"kind": "bugfix", "clarity": "clear", "brief_size": "small"},
        "settings": {
            "roles": {
                "plan_reviewer": {"model": MIMO, "reasoning_effort": "high"},
                "tester": {"model": GLM, "reasoning_effort": "high"},
                "completion": {"model": MIMO, "reasoning_effort": "high"},
                "builder": {"model": MIMO, "reasoning_effort": "high"},
            },
            "effort_origins": {role: "stage_choice" for role in ("plan_reviewer", "tester", "completion", "builder")},
            "declared_variants": {MIMO: ["high", "low", "medium"], GLM: ["high", "low", "max"]},
        },
        "stages": [],
    }
    for key, value in changes.items():
        state[key] = value
    return state


def decide(state, stage, role, configured="high", report_only=False):
    route = state["settings"]["roles"][role]
    return review_effort.decide(state, stage, role, report_only, configured, model=route["model"])


def triage_state():
    """A fresh joint default-flow run whose first Requirements pass is the triage pass (#2940)."""
    state = run_state()
    state["settings"].update(requirements_triage=True, joint_planning=True)
    state["settings"]["roles"]["requirements"] = {"model": MIMO, "reasoning_effort": "high"}
    return state


class DecideTests(unittest.TestCase):
    def test_a_small_clear_tasks_plan_review_runs_one_level_below_the_saved_level(self):
        for stage in ("review_plan", "finalize_plan"):
            choice = decide(run_state(), stage, "plan_reviewer")
            self.assertEqual(("medium", "high", True, "small_clear"), _key(choice), stage)
            self.assertEqual(("clear", "small", None), (choice["clarity"], choice["brief_size"], choice["plan_size"]))

    def test_the_completion_reviewer_is_lowered_too(self):
        self.assertEqual(
            ("medium", "high", True, "small_clear"), _key(decide(run_state(), "review_completion", "completion"))
        )

    def test_a_model_without_the_level_just_below_keeps_the_ceiling(self):
        # GLM 5.3 offers low, high and max: high to low would drop two levels.
        choice = decide(run_state(), "tester", "tester")
        self.assertEqual(("high", "high", False, "no_lower_level"), _key(choice))
        self.assertIn(GLM, choice["text"])

    def test_a_model_whose_levels_are_unknown_keeps_the_ceiling(self):
        state = run_state()
        del state["settings"]["declared_variants"]
        state["settings"]["certified_variants"] = {MIMO: ["high"]}
        self.assertEqual(("high", "high", False, "levels_unknown"), _key(decide(state, "review_plan", "plan_reviewer")))

    def test_a_probe_that_passed_the_lower_level_counts_beside_the_listing(self):
        state = run_state()
        state["settings"]["declared_variants"][GLM] = ["high", "max"]
        state["settings"]["certified_variants"] = {GLM: ["medium"]}
        self.assertEqual(("medium", "high", True, "small_clear"), _key(decide(state, "tester", "tester")))

    def test_a_typed_answered_or_runner_level_is_not_a_ceiling(self):
        for origin in ("user_explicit", "runner_default", "auto_selected", None):
            state = run_state()
            state["settings"]["effort_origins"]["plan_reviewer"] = origin
            self.assertIsNone(decide(state, "review_plan", "plan_reviewer"), origin)

    def test_other_stages_report_repairs_and_fallback_models_are_not_considered(self):
        state = run_state()
        self.assertIsNone(decide(state, "builder", "builder"))
        self.assertIsNone(decide(state, "write_acceptance_tests", "tester"))
        self.assertIsNone(decide(state, "review_plan", "plan_reviewer", report_only=True))
        self.assertIsNone(decide(state, "review_completion", "plan_reviewer"), "the stage's own route only")
        fallback = review_effort.decide(state, "review_plan", "plan_reviewer", False, "high", model=GLM)
        self.assertIsNone(fallback, "a fallback route on another model is not the saved ceiling")
        self.assertIsNone(decide(state, "review_plan", "plan_reviewer", configured="medium"))

    def test_a_stage_that_already_ran_for_this_task_runs_at_the_ceiling(self):
        state = run_state(current_task={"id": "task-1"})
        state["stages"] = [{"stage": "review_completion", "task_id": "task-1", "iteration": 1}]
        self.assertEqual("earlier_attempt", decide(state, "review_completion", "completion")["reason"])
        state["stages"] = [
            {"stage": "review_completion_report_repair", "original_stage": "review_completion", "task_id": "task-1"}
        ]
        self.assertEqual("earlier_attempt", decide(state, "review_completion", "completion")["reason"])
        state["stages"] = [{"stage": "review_completion", "task_id": "task-0", "iteration": 1}]
        self.assertTrue(decide(state, "review_completion", "completion")["lowered"], "another task's review")
        # A plan review counts within the iteration; the challenge does not make the finalize a retry.
        state["stages"] = [{"stage": "review_plan", "iteration": 1, "task_id": "task-1"}]
        self.assertEqual("earlier_attempt", decide(state, "review_plan", "plan_reviewer")["reason"])
        self.assertTrue(decide(state, "finalize_plan", "plan_reviewer")["lowered"])
        state["iteration"] = 2
        self.assertTrue(decide(state, "review_plan", "plan_reviewer")["lowered"], "an earlier iteration")

    def test_the_drafted_plans_size_decides_once_it_is_known(self):
        state = run_state(planning={"adaptive": {"size": "medium"}})
        self.assertEqual("not_small_clear", decide(state, "finalize_plan", "plan_reviewer")["reason"])
        state = run_state(planning={"adaptive": {"size": "small"}})
        state["workflow"]["brief_size"] = "medium"
        self.assertTrue(decide(state, "finalize_plan", "plan_reviewer")["lowered"])
        state = run_state()
        state["workflow"]["clarity"] = "vague"
        self.assertEqual("not_small_clear", decide(state, "review_plan", "plan_reviewer")["reason"])
        state = run_state()
        state["workflow"]["brief_size"] = "medium"
        self.assertEqual("not_small_clear", decide(state, "review_plan", "plan_reviewer")["reason"])

    def test_a_climbed_route_and_a_full_plan_review_keep_the_ceiling(self):
        state = run_state(reasoning_escalations=[{"role": "completion"}])
        self.assertEqual("climbed", decide(state, "review_completion", "completion")["reason"])
        self.assertTrue(decide(state, "review_plan", "plan_reviewer")["lowered"], "another role's climb")
        state = run_state()
        state["settings"]["plan_review_full"] = True
        self.assertEqual("full_review", decide(state, "review_plan", "plan_reviewer")["reason"])
        self.assertTrue(decide(state, "review_completion", "completion")["lowered"], "only the plan review is full")

    def test_the_bottom_of_the_scale_stays(self):
        state = run_state()
        state["settings"]["roles"]["plan_reviewer"]["reasoning_effort"] = "low"
        self.assertEqual("no_lower_level", decide(state, "review_plan", "plan_reviewer", "low")["reason"])
        self.assertEqual("xhigh", review_effort.below("max"))
        self.assertIsNone(review_effort.below("thinking"))


class LaunchTests(unittest.TestCase):
    def test_launch_lowers_a_review_and_keeps_a_stage_choice_builder_at_its_level(self):
        state = run_state()
        before = copy.deepcopy(state["settings"])
        route = state["settings"]["roles"]["plan_reviewer"]
        level, choice = review_effort.launch(state, "review_plan", "plan_reviewer", False, route)
        self.assertEqual(("medium", True), (level, choice["lowered"]))
        self.assertEqual(before, state["settings"], "the saved ceiling is never rewritten")
        state.update(
            goal_contract={"approval_status": "approved", "body": {"milestones": [{"id": "m1"}]}},
            current_task={"id": "task-1", "affected_paths": ["slugify.py"]},
        )
        self.assertEqual(
            ("high", None),
            review_effort.launch(state, "builder", "builder", False, state["settings"]["roles"]["builder"]),
        )

    def test_launch_keeps_the_builders_light_first_attempt_for_a_runner_default(self):
        state = run_state(
            goal_contract={"approval_status": "approved", "body": {"milestones": [{"id": "m1"}]}},
            current_task={"id": "task-1", "affected_paths": ["slugify.py"]},
        )
        state["settings"]["effort_origins"]["builder"] = "runner_default"
        self.assertEqual(
            ("low", None),
            review_effort.launch(state, "builder", "builder", False, state["settings"]["roles"]["builder"]),
        )

    def test_the_note_says_the_level_and_why_only_when_lowered(self):
        choice = decide(run_state(), "review_plan", "plan_reviewer")
        self.assertEqual(
            "Running at medium, one level below your high: the task is small and clear.", review_effort.note(choice)
        )
        self.assertIsNone(review_effort.note(decide(run_state(), "tester", "tester")))
        self.assertIsNone(review_effort.note(None))

    def test_launch_bounds_a_format_repair_below_the_saved_level(self):
        # #2868: a repair only fixes the report's format, so it never inherits the stage's
        # full reasoning level; GLM 5.3 offers low, high and max, so high repairs run low.
        state = run_state()
        route = {"model": GLM, "reasoning_effort": "high"}
        level, choice = review_effort.launch(state, "draft_plan", "planner", True, route)
        self.assertEqual(("low", "format_repair", True), (level, choice["reason"], choice["lowered"]))
        self.assertEqual(
            "Running at low, below your high: this attempt only corrects the last answer, it does not redo the work.",
            review_effort.note(choice),
        )

    def test_a_repair_of_the_triage_pass_keeps_the_repair_bound_and_its_reason(self):
        # #2940 review: the runner launches a repair under the original stage name, and the triage
        # pass stays due until a report is accepted, so the triage floor used to replace the repair
        # bound and its reason. MiMo offers low, medium and high: the repair of a saved high runs
        # at medium; the triage pass itself runs at the floor, low.
        state = triage_state()
        route = state["settings"]["roles"]["requirements"]
        level, choice = review_effort.launch(state, "requirements_gather", "requirements", True, route)
        self.assertEqual(("medium", "format_repair", True), (level, choice["reason"], choice["lowered"]))
        level, choice = review_effort.launch(state, "requirements_gather", "requirements", False, route)
        self.assertEqual(("low", "requirements_triage"), (level, choice["reason"]))


class RouteAnswerFitTests(unittest.TestCase):
    """A route answer moved the saved level to the offered model's nearest level: the launch says so (#3120)."""

    def moved(self, role, effort="high", saved="medium", model=GLM):
        state = run_state()
        state["settings"]["roles"][role] = {"model": model, "reasoning_effort": effort}
        state["settings"]["effort_fits"] = {
            role: {"from": saved, "to": effort, "reason": "route_answer", "model": model}
        }
        return state, state["settings"]["roles"][role]

    def test_a_stage_no_other_rule_considers_records_the_move_as_its_choice(self):
        state, route = self.moved("requirements")
        level, choice = review_effort.launch(state, "requirements_gather", "requirements", False, route)
        self.assertEqual(
            ("high", "route_answer_fit", "medium", GLM), (level, choice["reason"], choice["saved"], choice["model"])
        )
        self.assertEqual(
            f"Running at high: {GLM} does not offer medium, so this stage runs at high, the nearest level it offers.",
            review_effort.note(choice),
        )
        self.assertIsNone(review_effort.note({"reason": "route_answer_fit"}), "no text: nothing to say")

    def test_a_review_keeps_its_own_rule_and_carries_the_move_beside_it(self):
        state, route = self.moved("tester")
        state["workflow"]["clarity"] = "vague"
        level, choice = review_effort.launch(state, "tester", "tester", False, route)
        self.assertEqual(("high", "not_small_clear"), (level, choice["reason"]))
        self.assertEqual(("high", "medium"), (choice["route_answer_fit"]["level"], choice["route_answer_fit"]["saved"]))

    def test_a_launch_on_another_model_or_level_records_no_move(self):
        state, route = self.moved("requirements", model=MIMO)
        state["settings"]["effort_fits"]["requirements"]["model"] = GLM
        self.assertEqual(
            ("high", None), review_effort.launch(state, "requirements_gather", "requirements", False, route)
        )


class RunnerLaunchTests(unittest.TestCase):
    def test_a_format_repair_launches_below_the_saved_level_and_records_why(self):
        # #2868: the discovery repair of a one-comma JSON slip reasoned 63,994 tokens into
        # the 64k cap at the stage's saved high (run 20261107-115132-chat-0ff9ff). The repair
        # launches at the highest level the model offers below the saved one, and the saved
        # route stays untouched.
        import autocode as runner

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            schema = root / "schema.json"
            schema.write_text('{"type": "object", "properties": {}}')
            state = run_state(version=2, next_stage="draft_plan")
            state["settings"].update(engine="opencode", provider="kilocode", joint_planning=True)
            state["settings"]["roles"]["planner"] = {"model": GLM, "reasoning_effort": "high"}
            state["settings"]["roles"]["builder"]["model"] = "fixture/builder"  # a checker never grades its own model
            with mock.patch.object(
                runner.provider_launch, "prepare", return_value=(["provider"], {}, {}, {})
            ) as prepare:
                _result, record = runner.run_role(
                    role="planner",
                    prompt="Repair the rejected report's format",
                    sandbox="read-only",
                    workspace=root,
                    run_dir=root / "run",
                    state=state,
                    schema=schema,
                    model=GLM,
                    allow_write=False,
                    dry_run=True,
                    report_only=True,
                )
            self.assertEqual("low", prepare.call_args.kwargs["effort"])
            self.assertEqual("low", record["reasoning_effort"])
            choice = record["effort_choice"]
            self.assertEqual(
                ("low", "high", True, "format_repair"),
                (choice["level"], choice["ceiling"], choice["lowered"], choice["reason"]),
            )
            self.assertEqual("high", record["launch_route"]["reasoning_effort"], "the saved route stays the ceiling")
            self.assertEqual("high", state["settings"]["roles"]["planner"]["reasoning_effort"])

    def test_the_runner_records_a_triage_pass_repair_as_a_format_repair(self):
        # #2940 review: a fresh run's malformed first Requirements report is repaired under
        # requirements_gather while the triage pass is still due; the repair launches at the repair
        # bound and its recorded effort_choice says it is a repair, not the triage pass.
        import autocode as runner

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            schema = root / "schema.json"
            schema.write_text('{"type": "object", "properties": {}}')
            state = triage_state()
            state.update(version=2, next_stage="requirements_gather")
            state["settings"].update(engine="opencode", provider="kilocode")
            with mock.patch.object(
                runner.provider_launch, "prepare", return_value=(["provider"], {}, {}, {})
            ) as prepare:
                _result, record = runner.run_role(
                    role="requirements",
                    prompt="Repair the rejected report's format",
                    sandbox="read-only",
                    workspace=root,
                    run_dir=root / "run",
                    state=state,
                    schema=schema,
                    model=MIMO,
                    allow_write=False,
                    dry_run=True,
                    report_only=True,
                )
            self.assertEqual("medium", prepare.call_args.kwargs["effort"])
            choice = record["effort_choice"]
            self.assertEqual(
                ("medium", "high", True, "format_repair"),
                (choice["level"], choice["ceiling"], choice["lowered"], choice["reason"]),
            )

    def test_the_runner_launches_a_small_clear_review_below_its_ceiling_and_records_why(self):
        import autocode as runner

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            schema = root / "schema.json"
            schema.write_text('{"type": "object", "properties": {}}')
            state = run_state(version=2, next_stage="review_completion", current_task={"id": "task-1"})
            state["settings"].update(engine="opencode", provider="kilocode")
            state["settings"]["roles"]["task_planner"] = {"model": MIMO, "reasoning_effort": "high"}
            state["settings"]["roles"]["builder"]["model"] = "fixture/builder"  # a checker never grades its own model
            launched = []
            for _attempt in (1, 2):
                with mock.patch.object(
                    runner.provider_launch, "prepare", return_value=(["provider"], {}, {}, {})
                ) as prepare:
                    _result, record = runner.run_role(
                        role="task_planner",
                        prompt="Review the finished task",
                        sandbox="read-only",
                        workspace=root,
                        run_dir=root / "run",
                        state=state,
                        schema=schema,
                        model=MIMO,
                        allow_write=False,
                        dry_run=True,
                    )
                launched.append(
                    (prepare.call_args.kwargs["effort"], record["reasoning_effort"], record["effort_choice"])
                )
                self.assertEqual(
                    "high", record["launch_route"]["reasoning_effort"], "the saved route stays the ceiling"
                )
                state["stages"].append(record)
            (first, ran, choice), (second, again, kept) = launched
            self.assertEqual(
                ("medium", "medium", "small_clear", "high"), (first, ran, choice["reason"], choice["ceiling"])
            )
            self.assertEqual(("high", "high", "earlier_attempt"), (second, again, kept["reason"]), "a second look")
            self.assertEqual("high", state["settings"]["roles"]["completion"]["reasoning_effort"])


class ListingTests(unittest.TestCase):
    def test_a_new_run_saves_the_levels_its_stage_choice_review_models_offer(self):
        import autocode_configure as configure

        listing = {
            "candidates": [
                {"model": MIMO, "levels": ["high", "low", "medium"]},
                {"model": GLM, "levels": ["high", "low", "max"]},
                {"model": "other/model", "levels": ["low"]},
            ]
        }
        settings = run_state()["settings"]
        del settings["declared_variants"]
        settings["effort_origins"]["tester"] = "user_explicit"  # GLM serves only a typed level: not a ceiling
        with mock.patch("autocode_route_discovery.discover", return_value=listing) as discover:
            configure._list_review_ceilings(settings, object(), "/fixture")
        self.assertEqual({MIMO: ["high", "low", "medium"]}, settings["declared_variants"])
        discover.assert_called_once()
        nobody = run_state()["settings"]
        del nobody["declared_variants"]
        # Typed levels are exact: neither a review ceiling nor a hard plan's raise reads a listing for them.
        nobody["effort_origins"] = dict.fromkeys(nobody["effort_origins"], "user_explicit")
        with mock.patch("autocode_route_discovery.discover", side_effect=RuntimeError("no listing")) as discover:
            configure._list_review_ceilings(nobody, object(), "/fixture")
            self.assertNotIn("declared_variants", nobody)
            discover.assert_not_called()
            unread = settings | {"declared_variants": {}}
            configure._list_review_ceilings(unread, object(), "/fixture")
            discover.assert_called_once()
            self.assertEqual({}, unread["declared_variants"], "an unreadable listing saves nothing, never stops a run")


def _key(choice):
    return choice["level"], choice["ceiling"], choice["lowered"], choice["reason"]


if __name__ == "__main__":
    unittest.main()
