"""Cross-model verification defaults and the explicit single-model exception."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(HERE))

import autocode_dispatch as dispatch  # noqa: E402
import autocode_support as support  # noqa: E402
import autocode_verifier_independence as verifier_independence  # noqa: E402


def roles(**models):
    return {"settings": {"roles": {role: {"model": model} for role, model in models.items()}}}


class CrossModelTest(unittest.TestCase):
    def test_identical_builder_validator_is_rejected(self):
        state = roles(
            terra="zai-coding-plan/glm-5.3",
            sol="zai-coding-plan/glm-5.3",
            completion="xiaomi-token-plan-sgp/mimo-v2.6-pro",
            glm="zai-coding-plan/glm-5.3",
            plan_reviewer="xiaomi-token-plan-sgp/mimo-v2.6-pro",
        )
        with self.assertRaises(support.Paused) as ctx:
            dispatch.enforce_cross_model_verification(state)
        self.assertEqual("PAUSED_CROSS_MODEL", ctx.exception.status)
        self.assertIn("identical model", str(ctx.exception))

    def test_same_family_builder_validator_is_rejected(self):
        state = roles(
            terra="xiaomi-token-plan-sgp/mimo-v2.6-pro",
            sol="xiaomi-token-plan-sgp/mimo-v2.5-pro",
            completion="zai-coding-plan/glm-5.3",
            glm="zai-coding-plan/glm-5.3",
            plan_reviewer="xiaomi-token-plan-sgp/mimo-v2.6-pro",
        )
        with self.assertRaises(support.Paused) as ctx:
            dispatch.enforce_cross_model_verification(state)
        self.assertEqual("PAUSED_CROSS_MODEL", ctx.exception.status)
        self.assertIn("Builder/Tester", str(ctx.exception))

    def test_bare_and_openai_gpt_aliases_are_the_same_exact_model(self):
        for producer, verifier, label in (
            ("terra", "sol", "Builder/Tester"),
            ("terra", "completion", "Builder/Completion Reviewer"),
            ("glm", "plan_reviewer", "Planner/Plan Reviewer"),
        ):
            for builder, checker in (("gpt-6-sol", "openai/gpt-6-sol"), ("openai/gpt-6-sol", "gpt-6-sol")):
                with self.subTest(producer=producer, verifier=verifier, builder=builder):
                    state = roles(**{producer: builder, verifier: checker})
                    with self.assertRaises(support.Paused) as ctx:
                        dispatch.enforce_cross_model_verification(state)
                    self.assertEqual("PAUSED_CROSS_MODEL", ctx.exception.status)
                    self.assertIn(label + ": identical model", str(ctx.exception))

    def test_different_gpt_tiers_share_the_openai_family_across_alias_spellings(self):
        # Owner decision 2026-10-20: GPT tiers share training and judging biases like any family.
        for builder, checker in (
            ("gpt-6-sol", "openai/gpt-6-astra"),
            ("openai/gpt-6-sol", "gpt-5.6-sol"),
            ("gpt-6-luna", "openai/gpt-6-sol"),
        ):
            with self.subTest(builder=builder, checker=checker):
                with self.assertRaises(support.Paused) as ctx:
                    dispatch.enforce_cross_model_verification(roles(terra=builder, sol=checker))
                self.assertIn("same family openai", str(ctx.exception))

    def test_a_gpt_only_engine_lets_different_tiers_check_each_other(self):
        # Codex reaches only GPT models, as the Claude Code engine reaches only Claude: there only one model
        # checking itself is refused.
        state = roles(terra="gpt-6-sol", sol="gpt-6-astra")
        state["settings"]["engine"] = "codex"
        dispatch.enforce_cross_model_verification(state)

    def test_other_provider_prefixes_are_not_openai_aliases(self):
        dispatch.enforce_cross_model_verification(roles(terra="custom/gpt-6-sol", sol="gpt-6-sol"))
        dispatch.enforce_cross_model_verification(roles(terra="openai/custom-model", sol="custom-model"))

    def test_same_family_builder_completion_is_rejected(self):
        state = roles(
            terra="zai-coding-plan/glm-5.3",
            sol="xiaomi-token-plan-sgp/mimo-v2.6-pro",
            completion="zai-coding-plan/glm-5.2",
            glm="zai-coding-plan/glm-5.3",
            plan_reviewer="xiaomi-token-plan-sgp/mimo-v2.6-pro",
        )
        with self.assertRaises(support.Paused) as ctx:
            dispatch.enforce_cross_model_verification(state)
        self.assertIn("Builder/Completion Reviewer", str(ctx.exception))

    def test_same_family_planner_reviewer_is_rejected(self):
        state = roles(
            terra="xiaomi-token-plan-sgp/mimo-v2.6-pro",
            sol="zai-coding-plan/glm-5.3",
            completion="zai-coding-plan/glm-5.3",
            glm="zai-coding-plan/glm-5.3",
            plan_reviewer="zai-coding-plan/glm-5.2",
        )
        with self.assertRaises(support.Paused) as ctx:
            dispatch.enforce_cross_model_verification(state)
        self.assertIn("Planner/Plan Reviewer", str(ctx.exception))

    def test_cross_family_pair_passes(self):
        state = roles(
            terra="xiaomi-token-plan-sgp/mimo-v2.6-pro",
            sol="zai-coding-plan/glm-5.3",
            completion="zai-coding-plan/glm-5.3",
            glm="zai-coding-plan/glm-5.3",
            plan_reviewer="xiaomi-token-plan-sgp/mimo-v2.6-pro",
        )
        dispatch.enforce_cross_model_verification(state)  # must not raise

    def test_bare_glm_and_mimo_names_are_their_family(self):
        """The dashboard's Codex console routes GLM as bare glm-5.3 / glm-5.3-flash."""
        state = roles(
            terra="glm-5.3-flash",
            sol="glm-5.3",
            completion="gpt-5.6-sol",
            glm="zai-coding-plan/glm-5.3",
            plan_reviewer="openai/gpt-6-sol",
        )
        with self.assertRaises(support.Paused) as ctx:
            dispatch.enforce_cross_model_verification(state)
        self.assertIn("Builder/Tester: same family glm", str(ctx.exception))
        state = roles(
            terra="gpt-5.6-terra",
            sol="glm-5.3",
            completion="glm-5.3",
            glm="mimo-v2.6-pro",
            plan_reviewer="xiaomi-token-plan-sgp/mimo-v2.5-pro",
        )
        with self.assertRaises(support.Paused) as ctx:
            dispatch.enforce_cross_model_verification(state)
        self.assertIn("Planner/Plan Reviewer: same family mimo", str(ctx.exception))

    def test_default_routes_are_independent(self):
        import autocode_opencode as opencode

        dispatch.enforce_cross_model_verification(
            {"settings": {"roles": {role: {"model": model} for role, model in opencode.DEFAULT_MODELS.items()}}}
        )

    def test_missing_roles_are_skipped(self):
        dispatch.enforce_cross_model_verification({"settings": {"roles": {}}})

    def test_single_model_mode_allows_same_model_with_role_specific_effort(self):
        state = roles(
            terra="zai-coding-plan/glm-5.3",
            sol="zai-coding-plan/glm-5.3",
            completion="zai-coding-plan/glm-5.3",
            glm="zai-coding-plan/glm-5.3",
            plan_reviewer="zai-coding-plan/glm-5.3",
        )
        state["settings"]["single_model_mode"] = True
        dispatch.enforce_cross_model_verification(state)  # explicit single-subscription opt-in

    def test_a_review_run_has_no_builder_whose_work_its_tester_could_grade(self):
        # `autocode verify --engine codex --model gpt-5.6-terra` (#2502): the Codex Builder default
        # is the same model, but a review run never launches the Builder.
        state = roles(terra="gpt-5.6-terra", sol="gpt-5.6-terra", completion="gpt-5.6-terra")
        state["settings"]["engine"] = "codex"
        state["workflow"] = {"kind": "review", "source": "user"}
        dispatch.enforce_cross_model_verification(state)

        for kind in ("build", "bugfix", None):
            with self.subTest(kind=kind):
                state["workflow"] = {"kind": kind}
                with self.assertRaises(support.Paused) as ctx:
                    dispatch.enforce_cross_model_verification(state)
                self.assertEqual("PAUSED_CROSS_MODEL", ctx.exception.status)
                self.assertIn("Builder/Tester: identical model gpt-5.6-terra", str(ctx.exception))

    def test_run_role_is_an_unskippable_chokepoint(self):
        """Even if before_code_stage/dispatch are bypassed, run_role must pause."""
        import autocode as runner

        state = roles(
            terra="xiaomi-token-plan-sgp/mimo-v2.6-pro",
            sol="xiaomi-token-plan-sgp/mimo-v2.6-pro",
            completion="zai-coding-plan/glm-5.3",
            glm="zai-coding-plan/glm-5.3",
            plan_reviewer="xiaomi-token-plan-sgp/mimo-v2.6-pro",
        )
        state.update(
            iteration=1,
            next_stage="sol",
            settings={
                **state["settings"],
                "engine": "opencode",
                "limits": {},
                "sessions": {},
            },
        )
        with self.assertRaises(support.Paused) as ctx:
            runner.run_role(
                role="sol",
                prompt="p",
                sandbox="read-only",
                workspace=self.state if False else Path("."),
                run_dir=Path("."),
                state=state,
                schema=Path("schema.json"),
                model="xiaomi-token-plan-sgp/mimo-v2.6-pro",
                allow_write=False,
                dry_run=True,
            )
        self.assertEqual("PAUSED_CROSS_MODEL", ctx.exception.status)

    def test_clashes_name_the_verifier_role_to_reroute(self):
        rows = verifier_independence.clashes(
            roles(
                terra="zai-coding-plan/glm-5.3",
                sol="zai-coding-plan/glm-5.3",
                completion="xiaomi-token-plan-sgp/mimo-v2.6-pro",
                glm="gpt-5.6-sol",
                plan_reviewer="gpt-5.6-sol",
            )["settings"]
        )
        self.assertEqual(["sol", "plan_reviewer"], [row["verifier"] for row in rows])
        self.assertEqual("terra", rows[0]["producer"])
        self.assertIn("identical model", rows[0]["problem"])

    def test_cross_model_question_offers_models_that_pass_and_the_view_asks_for_one(self):
        import autocode_quota_route as quota_route
        import autocode_run_view as run_view

        state = {
            "status": "PAUSED_CROSS_MODEL",
            "stop_reason": "Cross-model verification violated; Planner/Plan Reviewer: identical model gpt-5.6-sol",
            "settings": {
                "engine": "codex",
                "roles": {
                    "terra": {"model": "gpt-5.6-terra"},
                    "sol": {"model": "gpt-6-sol"},
                    "completion": {"model": "gpt-6-luna"},
                    "glm": {"model": "gpt-5.6-sol"},
                    "plan_reviewer": {"model": "gpt-5.6-sol"},
                },
            },
        }
        asked = quota_route.cross_model_question(state)
        self.assertEqual("route-plan_reviewer", asked["id"])
        self.assertEqual("plan_reviewer", asked["route_role"])
        self.assertEqual("cross_model", asked["cause"])
        self.assertEqual("gpt-5.6-sol", asked["stopped_model"])
        self.assertIn("gpt-5.6-terra", asked["candidates"])
        self.assertNotIn("gpt-5.6-sol", asked["candidates"])
        view = run_view.view(state, workspace=Path("/ws"), run_dir=Path("/run"))
        self.assertEqual("answer", view["needs"]["kind"])
        self.assertEqual("route-plan_reviewer", view["needs"]["route"]["question_id"])
        self.assertEqual("cross_model", view["needs"]["route"]["cause"])
        self.assertIn("--answer route-plan_reviewer=MODEL", view["next_command"])
        self.assertNotIn("--resume-paused", view["next_command"])

    def test_saved_gocode_models_are_offered_by_the_public_view_and_accepted(self):
        import autocode_quota_route as quota_route
        import autocode_run_view as run_view

        identity = {
            "engine": "gocode", "identity_version": 1, "executable": "/tools/gocode",
            "config_sha256": "a" * 64,
        }
        state = {
            "status": "PAUSED_CROSS_MODEL",
            "settings": {
                "engine": "opencode", "provider": "gocode", "transport_identity": identity,
                "transport_identities": {"opencode": identity},
                "roles": {
                    "terra": {"model": "gpt-6.1-sol"}, "sol": {"model": "gpt-6.1-sol"},
                    "plan_reviewer": {"model": "claude-opus-5-5"},
                    "investigator": {"model": "glm-5"},
                    "architect": {"model": "qwen3-235b-a22b-2507"},
                },
            },
        }
        expected = {"claude-opus-5-5", "glm-5", "qwen3-235b-a22b-2507"}
        asked = quota_route.cross_model_question(state)
        self.assertEqual(expected, set(asked["candidates"]))
        view = run_view.view(state, workspace=Path("/ws"), run_dir=Path("/run"))
        for model in expected:
            self.assertIn(model, view["needs"]["questions"][0]["why"])
        for model in expected:
            quota_route.validate(state, "sol", model, configured_tool=False, cross_check=quota_route.cross_model_check("sol"))
        with self.assertRaisesRegex(ValueError, "same family openai"):
            quota_route.validate(state, "sol", "gpt-6-astra", configured_tool=False, cross_check=quota_route.cross_model_check("sol"))
        state["settings"]["provider"] = "opencode"
        with self.assertRaisesRegex(ValueError, "OpenCode provider/model"):
            quota_route.validate(state, "sol", "claude-opus-5-5", configured_tool=False, cross_check=quota_route.cross_model_check("sol"))

    def test_the_named_answer_switches_the_verifier_and_is_accepted(self):
        import autocode_quota_route as quota_route

        state = {
            "status": "PAUSED_CROSS_MODEL",
            "stop_reason": "Cross-model verification violated",
            "settings": {
                "engine": "codex",
                "roles": {
                    "glm": {"model": "gpt-5.6-sol"},
                    "plan_reviewer": {"model": "gpt-5.6-sol"},
                },
            },
            "user_events": [],
        }
        asked = quota_route.cross_model_question(state)
        asked, model = quota_route.parse_answer(
            [f"{asked['id']}=gpt-5.6-terra"], [asked], {"pause_status": quota_route.CROSS_MODEL_STATUS}
        )
        self.assertEqual("plan_reviewer", asked["route_role"])
        self.assertEqual("gpt-5.6-terra", model)
        quota_route.validate(
            state,
            "plan_reviewer",
            model,
            configured_tool=False,
            cross_check=dispatch.enforce_cross_model_verification,
        )
        record = quota_route.assign(
            state,
            "plan_reviewer",
            model,
            at="t",
            via="answer",
            attempt={"pause_status": quota_route.CROSS_MODEL_STATUS, "role": "plan_reviewer"},
        )
        self.assertEqual("gpt-5.6-terra", record["to"])
        self.assertEqual("gpt-5.6-sol", record["from"])
        self.assertEqual("gpt-5.6-terra", state["settings"]["roles"]["plan_reviewer"]["model"])
        dispatch.enforce_cross_model_verification(state)  # the named model clears the guard

    def test_each_clash_is_asked_in_turn_when_two_pairs_break_independence(self):
        import autocode_quota_route as quota_route

        state = {
            "status": "PAUSED_CROSS_MODEL",
            "settings": {
                "engine": "codex",
                "roles": {
                    "terra": {"model": "gpt-5.6-sol"},
                    "sol": {"model": "gpt-5.6-sol"},
                    "completion": {"model": "gpt-6-luna"},
                    "glm": {"model": "gpt-5.6-terra"},
                    "plan_reviewer": {"model": "gpt-5.6-terra"},
                },
            },
            "user_events": [],
        }
        asked = quota_route.cross_model_question(state)
        self.assertEqual("sol", asked["route_role"])
        self.assertIn("gpt-6-luna", asked["candidates"])  # the Planner/Plan Reviewer clash does not refuse it
        check = quota_route.cross_model_check("sol")
        with self.assertRaisesRegex(RuntimeError, r"^Builder/Tester: identical model gpt-5\.6-sol$"):
            check(state)  # the Tester's own pair still counts; the other pair does not
        quota_route.validate(state, "sol", "gpt-6-luna", configured_tool=False, cross_check=check)
        quota_route.assign(
            state,
            "sol",
            "gpt-6-luna",
            at="t",
            via="answer",
            attempt={"pause_status": quota_route.CROSS_MODEL_STATUS, "role": "sol"},
        )
        with self.assertRaisesRegex(support.Paused, "Planner/Plan Reviewer"):
            dispatch.enforce_cross_model_verification(state)  # the launch guard still holds the other pair
        self.assertEqual("plan_reviewer", quota_route.cross_model_question(state)["route_role"])


if __name__ == "__main__":
    unittest.main()
