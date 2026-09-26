"""Cross-model verification must hold before build/validate dispatch."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import autocode_dispatch as dispatch  # noqa: E402
import autocode_support as support  # noqa: E402


def roles(**models):
    return {"settings": {"roles": {
        role: {"model": model} for role, model in models.items()
    }}}


class CrossModelTest(unittest.TestCase):
    def test_identical_builder_validator_is_rejected(self):
        state = roles(terra="zai-coding-plan/glm-5.3", sol="zai-coding-plan/glm-5.3",
                      completion="xiaomi-token-plan-sgp/mimo-v2.6-pro",
                      glm="zai-coding-plan/glm-5.3",
                      plan_reviewer="xiaomi-token-plan-sgp/mimo-v2.6-pro")
        with self.assertRaises(support.Paused) as ctx:
            dispatch.enforce_cross_model_verification(state)
        self.assertEqual("PAUSED_CROSS_MODEL", ctx.exception.status)
        self.assertIn("identical model", str(ctx.exception))

    def test_same_family_builder_validator_is_rejected(self):
        state = roles(terra="xiaomi-token-plan-sgp/mimo-v2.6-pro",
                      sol="xiaomi-token-plan-sgp/mimo-v2.5-pro",
                      completion="zai-coding-plan/glm-5.3",
                      glm="zai-coding-plan/glm-5.3",
                      plan_reviewer="xiaomi-token-plan-sgp/mimo-v2.6-pro")
        with self.assertRaises(support.Paused) as ctx:
            dispatch.enforce_cross_model_verification(state)
        self.assertEqual("PAUSED_CROSS_MODEL", ctx.exception.status)
        self.assertIn("Builder/Validator", str(ctx.exception))

    def test_same_family_builder_completion_is_rejected(self):
        state = roles(terra="zai-coding-plan/glm-5.3",
                      sol="xiaomi-token-plan-sgp/mimo-v2.6-pro",
                      completion="zai-coding-plan/glm-5.2",
                      glm="zai-coding-plan/glm-5.3",
                      plan_reviewer="xiaomi-token-plan-sgp/mimo-v2.6-pro")
        with self.assertRaises(support.Paused) as ctx:
            dispatch.enforce_cross_model_verification(state)
        self.assertIn("Builder/Completion Owner", str(ctx.exception))

    def test_same_family_planner_reviewer_is_rejected(self):
        state = roles(terra="xiaomi-token-plan-sgp/mimo-v2.6-pro",
                      sol="zai-coding-plan/glm-5.3",
                      completion="zai-coding-plan/glm-5.3",
                      glm="zai-coding-plan/glm-5.3",
                      plan_reviewer="zai-coding-plan/glm-5.2")
        with self.assertRaises(support.Paused) as ctx:
            dispatch.enforce_cross_model_verification(state)
        self.assertIn("Planner/Plan Reviewer", str(ctx.exception))

    def test_cross_family_pair_passes(self):
        state = roles(terra="xiaomi-token-plan-sgp/mimo-v2.6-pro",
                      sol="zai-coding-plan/glm-5.3",
                      completion="zai-coding-plan/glm-5.3",
                      glm="zai-coding-plan/glm-5.3",
                      plan_reviewer="xiaomi-token-plan-sgp/mimo-v2.6-pro")
        dispatch.enforce_cross_model_verification(state)  # must not raise

    def test_missing_roles_are_skipped(self):
        dispatch.enforce_cross_model_verification({"settings": {"roles": {}}})

    def test_run_role_is_an_unskippable_chokepoint(self):
        """Even if before_code_stage/dispatch are bypassed, run_role must pause."""
        import autocode as runner
        state = roles(terra="xiaomi-token-plan-sgp/mimo-v2.6-pro",
                      sol="xiaomi-token-plan-sgp/mimo-v2.6-pro",
                      completion="zai-coding-plan/glm-5.3",
                      glm="zai-coding-plan/glm-5.3",
                      plan_reviewer="xiaomi-token-plan-sgp/mimo-v2.6-pro")
        state.update(iteration=1, next_stage="sol", settings={
            **state["settings"],
            "engine": "opencode",
            "limits": {},
            "sessions": {},
        })
        with self.assertRaises(support.Paused) as ctx:
            runner.run_role(
                role="sol", prompt="p", sandbox="read-only",
                workspace=self.state if False else Path("."),
                run_dir=Path("."),
                state=state, schema=Path("schema.json"),
                model="xiaomi-token-plan-sgp/mimo-v2.6-pro",
                allow_write=False, dry_run=True,
            )
        self.assertEqual("PAUSED_CROSS_MODEL", ctx.exception.status)


if __name__ == "__main__":
    unittest.main()
