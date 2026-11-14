"""When a started run's stage models may change, and what the status view and list offer (pure)."""

import unittest

import autocode_role_route as role_route
import autocode_run_stage_models as stage_models
import autocode_run_view as run_view


def state(status="PAUSED_BUDGET", **extra):
    roles = {
        "builder": {"model": "gpt-5.6-terra", "reasoning_effort": "medium"},
        "tester": {"model": "gpt-5.6-sol", "reasoning_effort": None},
        "stuck_investigator": {"model": "gpt-6-astra"},  # not a stage a person chooses
    }
    return {"status": status, "settings": {"engine": "codex", "roles": roles}, **extra}


class RunStageModelsTests(unittest.TestCase):
    def test_only_a_run_between_stages_may_change_and_the_view_says_why_not(self):
        offer = stage_models.offer(state(), run_dir="/runs/r")
        self.assertEqual(
            {"changeable": True, "reason": None, "next_command": ["stages", "list", "--run-dir", "/runs/r", "--json"]},
            offer,
        )
        for busy in (
            state("RUNNING"),
            state(active_stage={"stage": "builder"}),
            state(active_runner_check={"stage": "tester"}),
        ):
            with self.subTest(busy=busy.get("status")):
                offer = stage_models.offer(busy, run_dir="/runs/r")
                self.assertEqual((False, stage_models.BUSY), (offer["changeable"], offer["reason"]))
                self.assertEqual(stage_models.BUSY, stage_models.refusal(busy, "builder", "gpt-6-astra", "high"))
        self.assertIsNone(stage_models.offer({"status": "RUNNING"}, run_dir="/runs/r"), "no routes before a run starts")
        self.assertIsNone(stage_models.offer(state(), run_dir=None))

    def test_uncertain_stopped_check_does_not_claim_a_stage_is_working(self):
        stopped = state("PAUSED_VERIFICATION_UNCERTAIN", active_runner_check={"stage": "regression_proof"})
        offer = stage_models.offer(stopped, run_dir="/runs/r")
        self.assertFalse(offer["changeable"])
        self.assertEqual(
            "A previous check has not been confirmed stopped. Models cannot be changed while this run is paused.",
            offer["reason"],
        )
        self.assertEqual(offer["reason"], stage_models.refusal(stopped, "builder", "gpt-6-astra", "high"))

    def test_the_status_view_carries_the_offer(self):
        self.assertEqual(
            stage_models.offer(state(), run_dir="/r"), run_view.view(state(), run_dir="/r")["stage_models"]
        )
        self.assertIsNone(run_view.view(state())["stage_models"])

    def test_the_list_shows_the_runs_own_routes_and_names_the_flag_on_this_run(self):
        listed = stage_models.view(state(), "/runs/r", "/ws")
        self.assertEqual(["builder", "tester"], [stage["id"] for stage in listed["stages"]])
        builder, tester = listed["stages"]
        self.assertEqual({"model": "gpt-5.6-terra", "effort": "medium"}, builder["chosen"])
        self.assertEqual({"model": "gpt-5.6-sol", "effort": None}, tester["chosen"])
        self.assertEqual(
            ["--run-dir", "/runs/r", "--workspace", "/ws", "--stage-model", "builder", "MODEL", "EFFORT"],
            builder["set"]["next_command"],
        )
        self.assertIsNone(builder["reset"])
        self.assertIn(
            "the Tester checks this stage's work and runs gpt-5.6-sol",
            {option["model"]: option["unavailable"] for option in builder["options"]}["gpt-5.6-sol"],
        )
        self.assertEqual(("/runs/r", True), (listed["run_dir"], listed["changeable"]))

    def test_a_refusal_names_the_runs_stages_and_an_unchanged_route(self):
        self.assertIn("its stages are builder, tester", stage_models.refusal(state(), "planner", "m", "high"))
        self.assertIn(
            "already runs gpt-5.6-sol at default", stage_models.refusal(state(), "tester", "gpt-5.6-sol", "default")
        )
        self.assertIsNone(stage_models.refusal(state(), "tester", "gpt-5.6-sol", "high"), "a new level is a change")
        self.assertEqual("Name the model without spaces", stage_models.refusal(state(), "builder", "a b", "high"))

    def test_a_route_the_person_changed_between_stages_is_their_choice(self):
        moved = [{"role": "builder", "to": "gpt-6-astra", "via": "stage_models"}]
        route = {"model": "gpt-6-astra"}
        self.assertEqual(role_route.USER_EXPLICIT, role_route.origin({}, "builder", route, assignments=moved))
        quota = [{**moved[0], "via": "answer"}]
        self.assertEqual("quota_assigned", role_route.origin({}, "builder", route, assignments=quota))


if __name__ == "__main__":
    unittest.main()
