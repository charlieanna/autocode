"""Cross-model verification defaults and the explicit single-model exception."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(HERE))

import autocode_dispatch as dispatch  # noqa: E402
import autocode_support as support  # noqa: E402


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

    def test_different_gpt_tiers_remain_independent_across_alias_spellings(self):
        for builder, checker in (
            ("gpt-6-sol", "openai/gpt-6-astra"),
            ("openai/gpt-6-sol", "gpt-5.6-sol"),
            ("gpt-6-luna", "openai/gpt-6-sol"),
        ):
            with self.subTest(builder=builder, checker=checker):
                dispatch.enforce_cross_model_verification(roles(terra=builder, sol=checker))

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


if __name__ == "__main__":
    unittest.main()
