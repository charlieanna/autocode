"""One rule for fitting a role's effort to its model's declared variants (#2006, #2057)."""

import unittest

import autocode_route_fit as route_fit
import autocode_util as util

GLM = ["low", "high", "max"]  # GLM 5.3's declared variants: no medium, no xhigh


def settings_for(role="terra", model="zai-coding-plan/glm-5.3", effort="high", *, origin=None, declared=GLM):
    settings = {"roles": {role: {"model": model, "reasoning_effort": effort}}}
    if origin:
        settings["effort_origins"] = {role: origin}
    if declared is not None:
        settings[route_fit.DECLARED] = {model: list(declared)}
    return settings


class NearestTests(unittest.TestCase):
    def test_a_declared_effort_stays_and_an_undeclared_one_moves_to_the_next_stronger_else_weaker(self):
        self.assertEqual("high", route_fit.nearest("high", GLM))
        self.assertEqual("high", route_fit.nearest("medium", GLM))
        self.assertEqual("max", route_fit.nearest("xhigh", GLM))
        self.assertEqual(
            "high", route_fit.nearest("xhigh", ["low", "medium", "high"]), "nothing stronger: the strongest weaker"
        )
        self.assertIsNone(route_fit.nearest("medium", []), "nothing declared: unknown, the launch check decides")
        self.assertEqual("low", route_fit.nearest("odd", GLM))

    def test_the_flag_is_the_roles_own_or_the_global_one(self):
        self.assertEqual("--completion-reasoning-effort", route_fit.flag("completion"))
        self.assertEqual("--plan-reviewer-reasoning-effort", route_fit.flag("plan_reviewer"))
        self.assertEqual("--reasoning-effort", route_fit.flag("analyst"), "the Analyst has no flag of its own")


class FitTests(unittest.TestCase):
    def test_a_runner_chosen_level_moves_to_the_nearest_declared_variant(self):
        fitted = route_fit.fit(settings_for(effort="medium"), "terra")
        self.assertEqual(route_fit.Fit("zai-coding-plan/glm-5.3", "high", "medium", "fitted", False), fitted)

    def test_a_declared_level_needs_no_move(self):
        fitted = route_fit.fit(settings_for(effort="max"), "terra")
        self.assertEqual(("declared", "max", False), (fitted.reason, fitted.effort, fitted.explicit))

    def test_a_derived_tier_is_never_a_persons_choice_so_it_fits_even_when_the_saved_level_is_theirs(self):
        settings = settings_for(effort="max", origin=route_fit.USER_EXPLICIT)
        fitted = route_fit.fit(settings, "terra", effort="medium")
        self.assertEqual(("fitted", "high", False), (fitted.reason, fitted.effort, fitted.explicit))
        self.assertEqual("max", settings["roles"]["terra"]["reasoning_effort"], "the saved level is untouched")

    def test_a_person_named_undeclared_level_refuses_with_its_flag_and_is_both_a_pause_and_a_value_error(self):
        settings = settings_for("completion", effort="medium", origin=route_fit.USER_EXPLICIT)
        with self.assertRaises(route_fit.EffortUnsupported) as raised:
            route_fit.fit(settings, "completion")
        self.assertIsInstance(raised.exception, util.Paused)
        self.assertIsInstance(raised.exception, ValueError)
        self.assertEqual("PAUSED_EFFORT_UNSUPPORTED", raised.exception.status)
        self.assertRegex(str(raised.exception), "--completion-reasoning-effort.*declares low, high, max")
        self.assertEqual("medium", settings["roles"]["completion"]["reasoning_effort"], "nothing moved")

    def test_a_person_named_level_the_model_declares_is_kept_and_marked_explicit(self):
        settings = settings_for(effort="max", origin=route_fit.USER_EXPLICIT)
        fitted = route_fit.fit(settings, "terra")
        self.assertEqual(("max", True), (fitted.effort, fitted.explicit))

    def test_a_reviewer_fallback_fits_against_the_model_it_actually_launches(self):
        # The fallback borrows another role's route for this launch; the saved route's model is not what runs.
        settings = settings_for(effort="medium", declared=["low", "high"])
        settings[route_fit.DECLARED]["openai/gpt-6-sol"] = ["low", "medium", "high"]
        fitted = route_fit.fit(settings, "terra", model="openai/gpt-6-sol")
        self.assertEqual(("declared", "medium"), (fitted.reason, fitted.effort))

    def test_no_listing_records_the_effort_as_unverified_instead_of_guessing(self):
        fitted = route_fit.fit(settings_for(effort="medium", declared=None), "terra")
        self.assertEqual(("unverified", "medium"), (fitted.reason, fitted.effort))

    def test_the_runs_saved_candidate_levels_serve_a_fit_after_a_new_listing(self):
        settings = settings_for(effort="medium", declared=None)
        settings["route_selection"] = {
            "candidates": {"completion": [{"model": "zai-coding-plan/glm-5.3", "levels": GLM}]}
        }
        fitted = route_fit.fit(settings, "terra")
        self.assertEqual(("fitted", "high"), (fitted.reason, fitted.effort))


class LaunchTests(unittest.TestCase):
    def test_a_fitted_move_is_recorded_at_the_one_launch_boundary(self):
        settings = settings_for(effort="medium")
        self.assertEqual("high", route_fit.launch(settings, "terra", "medium", stage="terra"))
        self.assertEqual({"from": "medium", "to": "high", "reason": "route_fit"}, settings["effort_fits"]["terra"])

    def test_an_unverified_launch_says_so_and_keeps_an_existing_fit(self):
        settings = settings_for(effort="medium", declared=None)
        route_fit.launch(settings, "terra", "medium", stage="terra")
        self.assertEqual(
            {"from": "medium", "to": "medium", "reason": "unverified", "stage": "terra"},
            settings["effort_fits"]["terra"],
        )
        settings["effort_fits"]["terra"] = {"from": "medium", "to": "high", "reason": "route_answer"}
        route_fit.launch(settings, "terra", "medium")
        self.assertEqual(
            {"from": "medium", "to": "high", "reason": "route_answer"},
            settings["effort_fits"]["terra"],
            "the earlier decision stays as recorded",
        )

    def test_a_declared_level_records_nothing(self):
        settings = settings_for(effort="high")
        self.assertEqual("high", route_fit.launch(settings, "terra", "high"))
        self.assertNotIn("effort_fits", settings)


class ReconcileTests(unittest.TestCase):
    def roles(self):
        return {
            "terra": {"model": "m", "reasoning_effort": "high"},
            "completion": {"model": "m", "reasoning_effort": "medium"},
            "plan_reviewer": {"model": "m", "reasoning_effort": "xhigh"},
        }

    def test_runner_chosen_efforts_move_and_are_recorded_and_said(self):
        roles = self.roles()
        moved = route_fit.reconcile(roles, "zai-coding-plan/glm-5.3", GLM)
        self.assertEqual(
            {"completion": {"from": "medium", "to": "high"}, "plan_reviewer": {"from": "xhigh", "to": "max"}}, moved
        )
        self.assertEqual(
            ("high", "high", "max"),
            tuple(roles[r]["reasoning_effort"] for r in ("terra", "completion", "plan_reviewer")),
        )
        note = route_fit.notice(moved, "zai-coding-plan/glm-5.3", GLM)
        self.assertIn("declares the reasoning variants low, high, max", note)
        self.assertIn("completion medium -> high, plan_reviewer xhigh -> max", note)
        self.assertIsNone(route_fit.notice({}, "m", ["low"]))

    def test_an_effort_a_person_named_is_refused_with_its_flag_never_moved(self):
        roles = self.roles()
        with self.assertRaisesRegex(ValueError, "--completion-reasoning-effort.*declares low, high, max"):
            route_fit.reconcile(roles, "zai-coding-plan/glm-5.3", GLM, explicit={"completion"})
        self.assertEqual("medium", roles["completion"]["reasoning_effort"], "nothing moved before the refusal")

    def test_nothing_declared_moves_nothing(self):
        roles = self.roles()
        self.assertEqual({}, route_fit.reconcile(roles, "m", None))
        self.assertEqual({}, route_fit.reconcile(roles, "m", []))
        self.assertEqual("medium", roles["completion"]["reasoning_effort"])


class DeclaredForTests(unittest.TestCase):
    def test_a_single_model_runs_saved_listing_serves_declared_for_first(self):
        settings = {
            route_fit.DECLARED: {"zai-coding-plan/glm-5.3": GLM},
            "route_selection": {"candidates": {"sol": [{"model": "zai-coding-plan/glm-5.3", "levels": ["low"]}]}},
        }
        self.assertEqual(GLM, route_fit.declared_for(settings, "zai-coding-plan/glm-5.3"))
        self.assertIsNone(route_fit.declared_for(settings, "other/model"))
        self.assertIsNone(route_fit.declared_for({route_fit.DECLARED: {"m": "not a list"}}, "m"))

    def test_the_runs_recorded_candidate_levels_serve_a_route_answer(self):
        settings = {
            "route_selection": {
                "candidates": {
                    "sol": [{"model": "openai/gpt-6-sol", "levels": ["low", "medium", "high"]}],
                    "completion": [{"model": "zai-coding-plan/glm-5.3", "levels": GLM}],
                }
            }
        }
        self.assertEqual(GLM, route_fit.declared_for(settings, "zai-coding-plan/glm-5.3"))
        self.assertIsNone(route_fit.declared_for(settings, "xiaomi-token-plan-sgp/mimo-v2.6-pro"))
        self.assertIsNone(route_fit.declared_for({}, "m"))


class SavedSettingsIdentityTests(unittest.TestCase):
    """A launch-time fit records how an effort was derived; it never changes which route was approved."""

    def test_a_fit_recorded_after_an_approval_leaves_its_settings_identity_unchanged(self):
        approved = settings_for(effort="medium")
        launched = settings_for(effort="medium")
        route_fit.record(launched, "terra", route_fit.fit(launched, "terra"))
        self.assertNotEqual(approved, launched, "the launch records its fit")
        self.assertTrue(route_fit.same(approved, launched))
        self.assertEqual(route_fit.identity(approved), route_fit.identity(launched))
        self.assertIn(util.digest(route_fit.identity(approved)), route_fit.digests(launched))

    def test_a_changed_route_is_still_a_different_identity(self):
        approved = settings_for(effort="medium")
        changed = settings_for(effort="max")
        self.assertFalse(route_fit.same(approved, changed))
        self.assertNotIn(util.digest(approved), route_fit.digests(changed))


if __name__ == "__main__":
    unittest.main()
