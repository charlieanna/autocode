"""Cost attribution uses the cap ledger, preserving unknowns and historical spend."""

import copy
import unittest

import autocode_cost as cost


def record(stage, amount=None, **fields):
    return {"stage": stage, "metrics": {"provider_cost_usd": amount}, **fields}


class CostBreakdownTests(unittest.TestCase):
    def test_process_outcomes_repeats_and_inherited_cost_reconcile_without_guessing_waste(self):
        state = {
            "settings": {"limits": {"max_cost_usd": 5}},
            "stages": [
                record("astra_plan", 0.5, exit_code=1),
                record("astra_plan", 0.75, exit_code=0),
                record("investigate_stuck", 0.25, exit_code=0),
                record("terra", 1, exit_code=0),
                record("sol", 0.25, interrupted=True, exit_code=1),
                record("astra_review", 0.25, exit_code=0),
            ],
            "inherited_spend": {"usd": 2, "unpriced_stages": 1},
        }
        before = copy.deepcopy(state)
        view = cost.view(state)
        detail = view["breakdown"]
        self.assertEqual(5, view["spent_usd"])
        self.assertEqual((5, 5), cost.exceeded(state))
        for key in ("by_stage", "by_process_outcome"):
            self.assertEqual(3, sum(row["spent_usd"] for row in detail[key]))
            self.assertEqual(6, sum(row["stages"] for row in detail[key]))
        planner = detail["by_stage"][0]
        self.assertEqual((2, 1.25, 1), (planner["stages"], planner["spent_usd"], planner["failed_stages"]))
        by_outcome = {row["process_outcome"]: row for row in detail["by_process_outcome"]}
        self.assertEqual(0.5, by_outcome["failed"]["spent_usd"])
        self.assertEqual(0.25, by_outcome["interrupted"]["spent_usd"])
        self.assertEqual(2.25, by_outcome["completed"]["spent_usd"])
        self.assertIn("Planner 2 attempts: $1.25 (1 failed)", cost.line(view))
        self.assertIn("Tester 1 attempt: $0.25 (1 interrupted)", cost.line(view))
        self.assertNotIn("unproductive", cost.line(view))
        self.assertEqual(before, state)

    def test_estimates_unknowns_partial_and_zero_cost_stay_distinct(self):
        known = record("terra", model="anthropic/claude-opus-5-5", exit_code=0)
        known["metrics"] = {
            "provider_tokens": {"input_tokens": 1000, "output_tokens": 200},
            "provider_tokens_partial": True,
        }
        view = cost.view(
            {
                "stages": [
                    known,
                    record("terra", abandoned=True),
                    record("sol"),
                    record("check_replay", runner_owned=True, engine="runner", exit_code=0),
                ]
            }
        )
        by_stage = {row["stage"]: row for row in view["breakdown"]["by_stage"]}
        builder = by_stage["terra"]
        self.assertEqual(
            (0.008, None, 1, 1, False),
            (
                builder["estimated_usd"],
                builder["reported_usd"],
                builder["unpriced_stages"],
                builder["partial_stages"],
                builder["complete"],
            ),
        )
        self.assertIsNone(by_stage["sol"]["spent_usd"])
        self.assertEqual(0, by_stage["check_replay"]["spent_usd"])
        self.assertTrue(by_stage["check_replay"]["complete"])
        self.assertIn("Builder 2 attempts: at least $0.008 (1 interrupted, 1 unpriced)", cost.line(view))
        self.assertEqual(0.008, view["spent_usd"])

    def test_each_unpriced_stage_says_why_without_a_guessed_amount(self):
        tokens = {"provider_tokens": {"input_tokens": 1000, "output_tokens": 200}}
        priced = record("terra", model="anthropic/claude-opus-5-5", exit_code=0)
        unmapped = record("sol", model="vendor/no-such-model", exit_code=0)
        unnamed = record("astra_review", exit_code=0)
        for row in (priced, unmapped, unnamed):
            row["metrics"] = copy.deepcopy(tokens)
        view = cost.view({"stages": [priced, unmapped, unnamed, record("astra_plan", model="vendor/no-such-model")]})
        self.assertEqual(3, view["unpriced_stages"])
        self.assertEqual(
            {
                "usage_missing": 1,
                "model_unknown": 1,
                "rate_unavailable": 1,
                "models_without_rate": ["vendor/no-such-model"],
            },
            view["breakdown"]["unpriced"],
        )
        self.assertEqual(0.008, view["spent_usd"], "an unpriced stage still adds nothing to the known spend")
        self.assertFalse(view["complete"])

    def test_active_workers_are_excluded_and_routing_names_and_repair_purpose_survive(self):
        state = {
            "settings": {"workflow": {"mode": "glm_first_v1"}},
            "stages": [record("astra_checkpoint", 0.1, exit_code=0), record("terra_report_repair", 0.2, exit_code=0)],
            "active_stage": record("terra", 50),
            "orchestration_batch": {"workers": [{"status": "RUNNING"}]},
        }
        view = cost.view(state)
        self.assertFalse(view["complete"])
        self.assertEqual(0.3, view["spent_usd"])
        self.assertEqual(["Tester", "Builder (report repair)"], [row["job"] for row in view["breakdown"]["by_stage"]])

    def test_malformed_legacy_records_have_unknown_outcomes(self):
        view = cost.view(
            {
                "settings": "broken",
                "stages": [None, {"stage": []}, record("sol", 0.1, exit_code=True, abandoned="false")],
            }
        )
        [outcome] = view["breakdown"]["by_process_outcome"]
        self.assertEqual(("unknown", 2, 1), (outcome["process_outcome"], outcome["stages"], outcome["unpriced_stages"]))
        cost.line(view)


class LegacyPlanningLaunchLabels(unittest.TestCase):
    """Issue #2592: the Planner's draft, revision and their repairs are Planner launches.

    The prompts name astra_discovery and glm_revise as the Planner (prompts/discovery.md,
    prompts/requirements-revise.md: "You are the Planner"), so a saved ledger must display
    their launches under Planner, while requirements_gather stays Requirements.
    """

    def test_saved_legacy_planning_ledger_displays_one_requirements_and_three_planner_launches(self):
        state = {
            "stages": [
                record("requirements_gather", 0.10, exit_code=0),
                record("astra_discovery", 0.20, exit_code=0),
                record("glm_revise", 0.30, exit_code=0),
                record("glm_revise_report_repair", 0.40, exit_code=0),
            ]
        }
        view = cost.view(state)
        by_stage = {row["stage"]: row for row in view["breakdown"]["by_stage"]}
        self.assertEqual("Requirements", by_stage["requirements_gather"]["job"])
        self.assertEqual("Planner", by_stage["astra_discovery"]["job"])
        self.assertEqual("Planner", by_stage["glm_revise"]["job"])
        self.assertEqual("Planner (report repair)", by_stage["glm_revise_report_repair"]["job"])
        launches = {}
        for row in by_stage.values():
            job = row["job"].replace(" (report repair)", "")
            launches[job] = launches.get(job, 0) + row["stages"]
        self.assertEqual({"Requirements": 1, "Planner": 3}, launches)
        # Relabelling never changes the ledger's totals.
        self.assertEqual(4, sum(row["stages"] for row in by_stage.values()))
        self.assertEqual((1.0, 1.0, None), (view["spent_usd"], view["reported_usd"], view["estimated_usd"]))
        self.assertEqual(0, view["unpriced_stages"])

    def test_plain_and_report_repair_stages_cover_the_full_legacy_planning_ledger(self):
        expected = {
            "requirements_gather": "Requirements",
            "requirements_gather_report_repair": "Requirements (report repair)",
            "astra_discovery": "Planner",
            "astra_discovery_report_repair": "Planner (report repair)",
            "glm_revise": "Planner",
            "glm_revise_report_repair": "Planner (report repair)",
        }
        view = cost.view({"stages": [record(stage, 0.25, exit_code=0) for stage in expected]})
        self.assertEqual(expected, {row["stage"]: row["job"] for row in view["breakdown"]["by_stage"]})


class BillingBreakdownTests(unittest.TestCase):
    """How each stage bills, so an API-equivalent figure is not read as a subscription's bill (#1924)."""

    def stage(self, model):
        tokens = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
        return {"stage": "terra", "model": model, "metrics": {"provider_tokens": tokens}}

    def test_stages_are_grouped_by_how_their_model_bills(self):
        state = {
            "settings": {
                # An automatically selected run saved the billing discovery found for each candidate.
                "route_selection": {"candidates": {"sol": [{"model": "openai/gpt-6-sol", "billing": "subscription"}]}}
            },
            "stages": [
                # The plan reports 0, which events record as no reported cost: priced API-equivalent.
                self.stage("zai-coding-plan/glm-5.3"),
                self.stage("openai/gpt-6-sol"),
                self.stage("kilo/openai/gpt-6-sol"),
                self.stage("openai/gpt-5.6-terra"),  # an OpenAI login a saved run does not record
                self.stage("gpt-5.6-sol"),  # a bare Codex id: the engine's own login decides
            ],
        }
        detail = cost.view(state)["breakdown"]
        groups = {row["billing"]: row for row in detail["by_billing"]}
        self.assertEqual(
            {"subscription": 2, "pay per token": 1, "unknown": 2}, {k: v["stages"] for k, v in groups.items()}
        )
        self.assertIsNone(groups["subscription"]["reported_usd"])
        self.assertGreater(groups["subscription"]["estimated_usd"], 0)
        self.assertEqual(
            sum(row["stages"] for row in detail["by_stage"]), sum(row["stages"] for row in detail["by_billing"])
        )
