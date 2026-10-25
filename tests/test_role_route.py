"""One route record per role: the tool, the account, the model and why it is there (#1426)."""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode as runner
import autocode_configure
import autocode_milestones as milestones
import autocode_opencode as oc
import autocode_planning as planning
import autocode_role_route as role_route
import autopilot

from . import test_planning

GLM = "zai-coding-plan/glm-5.3"
MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"
GATEWAY = "kilo/anthropic/claude-opus-5.5"
LUNA = "openai/gpt-6-luna"


class RouteRecordTests(unittest.TestCase):
    def test_the_account_is_the_billing_segment_of_the_model_id(self):
        self.assertEqual("zai-coding-plan", role_route.account(GLM))
        self.assertEqual("kilo", role_route.account(GATEWAY))
        # A bare model bills whichever login the engine itself holds, so no route can name it.
        self.assertIsNone(role_route.account("gpt-5.6-sol"))
        self.assertIsNone(role_route.account(None))
        self.assertIsNone(role_route.account("/glm-5.3"))

    def test_the_tool_is_the_roles_provider_else_the_runs(self):
        run = {"engine": "opencode", "provider": "kilocode", "roles": {"sol": {"model": GLM}}}
        self.assertEqual("kilocode", role_route.provider(run, "sol"))
        self.assertEqual(
            "claude",
            role_route.provider({**run, "roles": {"sol": {"model": GLM, "provider": "claude"}}}, "sol"),
        )
        # Naming the default facade is not naming another tool, so the run's tool still answers.
        self.assertEqual(
            "kilocode",
            role_route.provider({**run, "roles": {"sol": {"model": GLM, "provider": "opencode"}}}, "sol"),
        )
        self.assertEqual("opencode", role_route.provider({"engine": "opencode", "roles": {"sol": {}}}, "sol"))

    def test_the_codex_engine_is_its_own_transport_so_it_names_itself(self):
        self.assertEqual(
            "codex",
            role_route.provider({"engine": "codex", "roles": {"sol": {"model": "gpt-5.6-sol"}}}, "sol"),
        )
        self.assertEqual("codex", role_route.provider({}, "sol"))
        # A role's own engine decides, not the run's.
        per_role = {"engine": "codex", "roles": {"sol": {"engine": "opencode", "model": GLM}}}
        self.assertEqual("opencode", role_route.engine(per_role, "sol"))
        self.assertEqual("codex", role_route.engine(per_role, "terra"))
        self.assertEqual("opencode", role_route.provider(per_role, "sol"))
        self.assertEqual("codex", role_route.provider(per_role, "terra"))

    def test_the_origin_is_read_from_the_record_that_moved_the_route_last(self):
        route = {"model": GLM, "reasoning_effort": "high"}
        saved = {"route_origins": {"sol": "user_explicit"}}
        self.assertEqual(
            "quota_assigned", role_route.origin(saved, "sol", route, assignments=[{"role": "sol", "to": GLM}])
        )
        # An assignment for another role, or to a model this route no longer runs, is not its cause.
        self.assertEqual(
            "user_explicit", role_route.origin(saved, "sol", route, assignments=[{"role": "terra", "to": GLM}])
        )
        self.assertEqual(
            "user_explicit", role_route.origin(saved, "sol", route, assignments=[{"role": "sol", "to": MIMO}])
        )

        strong = {"builder_retry": {"strong_model": MIMO}, "route_origins": {"terra": "runner_default"}}
        attempted = {"model": MIMO}
        self.assertEqual(
            "stronger_attempt", role_route.origin(strong, "terra", attempted, retries=[{"action": "escalate"}])
        )
        self.assertEqual("runner_default", role_route.origin(strong, "terra", attempted, retries=[{"action": "keep"}]))
        self.assertEqual("runner_default", role_route.origin(strong, "terra", attempted))
        # Only the Builder has a stronger attempt; another role on that model was not moved by it.
        self.assertEqual("unrecorded", role_route.origin(strong, "sol", attempted, retries=[{"action": "escalate"}]))

        self.assertEqual(
            "escalated", role_route.origin({}, "sol", route, escalations=[{"role": "sol", "selected": route}])
        )
        self.assertEqual(
            "unrecorded",
            role_route.origin(
                {},
                "sol",
                route,
                escalations=[{"role": "sol", "selected": {"model": GLM, "reasoning_effort": "medium"}}],
            ),
            "a climb to another reasoning level is not this route's cause",
        )

        for origin in role_route.CONFIGURED_ORIGINS:
            self.assertEqual(origin, role_route.origin({"route_origins": {"sol": origin}}, "sol", route))
        self.assertEqual("derived", role_route.origin({}, "resolver", {"model": GLM}))
        self.assertEqual("unrecorded", role_route.origin({}, "sol", route))
        self.assertEqual("unrecorded", role_route.origin({}, "sol", {}), "no route, no reason")
        # A word that is not one of the closed set is not an origin; nothing may invent one.
        self.assertEqual("unrecorded", role_route.origin({"route_origins": {"sol": "guessed"}}, "sol", route))
        # A cause that is not a record of anything is not evidence.
        self.assertEqual("unrecorded", role_route.origin({}, "sol", route, assignments=["sol"], escalations=[None]))

    def test_a_record_carries_every_part_of_one_route(self):
        settings = {
            "engine": "opencode",
            "provider": "kilocode",
            "roles": {"sol": {"model": GATEWAY, "reasoning_effort": "high"}},
        }
        self.assertEqual(
            {
                "model": GATEWAY,
                "engine": "opencode",
                "job": "Tester",
                "reasoning_effort": "high",
                "provider": "kilocode",
                "account": "kilo",
                "origin": "unrecorded",
            },
            role_route.record({"settings": settings}, "sol"),
        )
        self.assertEqual(
            {
                "model": None,
                "engine": "codex",
                "job": "Tester",
                "reasoning_effort": None,
                "provider": "codex",
                "account": None,
                "origin": "unrecorded",
            },
            role_route.record({}, "sol"),
            "a role with no saved route still answers, so a caller never branches on it",
        )

    def test_records_answer_for_every_configured_role_of_a_run(self):
        state = {
            "settings": {
                "engine": "opencode",
                "provider": "kilocode",
                "route_origins": {"sol": "user_explicit"},
                "roles": {
                    "sol": {"model": GLM, "reasoning_effort": "high"},
                    "terra": {"model": MIMO},
                    "orchestrator": "no model runs here",
                },
            },
            "reasoning_escalations": [{"role": "terra", "selected": {"model": MIMO, "reasoning_effort": None}}],
        }
        records = role_route.records(state, [{"role": "sol", "to": GLM}])
        self.assertEqual(("sol", "terra"), tuple(records))
        self.assertEqual("quota_assigned", records["sol"]["origin"], "the newest cause wins over the saved one")
        self.assertEqual("escalated", records["terra"]["origin"])
        self.assertEqual("xiaomi-token-plan-sgp", records["terra"]["account"])
        self.assertNotIn("orchestrator", records, "a stage with no model has no route")
        self.assertEqual({}, role_route.records({}))
        self.assertEqual({}, role_route.records({"settings": {"roles": None}}))

    def test_a_new_runs_origins_say_whose_choice_each_route_was(self):
        roles = {"sol": {}, "terra": {}}
        self.assertEqual(
            {"sol": "user_explicit", "terra": "runner_default"},
            role_route.origins(SimpleNamespace(sol_model=GLM), roles, {}, None),
        )
        # A model an earlier command of this conversation named is the person's choice too.
        self.assertEqual(
            {"sol": "user_explicit"}, role_route.origins(SimpleNamespace(), {"sol": {}}, {"sol": GLM}, None)
        )
        self.assertEqual(
            {"sol": "single_model", "terra": "single_model"},
            role_route.origins(SimpleNamespace(sol_model=GLM), roles, {}, LUNA),
            "--single-model names every route, so none of them is the runner's choice",
        )
        # --glm-model names the Requirements route too (autocode_configure.configure_joint).
        named = SimpleNamespace(glm_model=MIMO)
        self.assertEqual({"requirements": "user_explicit"}, role_route.origins(named, {"requirements": {}}, {}, None))
        self.assertEqual(
            {"requirements": "runner_default"}, role_route.origins(SimpleNamespace(), {"requirements": {}}, {}, None)
        )
        self.assertEqual({}, role_route.origins(SimpleNamespace(), {}, {}, None))

    def test_a_saved_run_re_records_only_the_routes_a_flag_moved(self):
        settings = {
            "route_origins": {"glm": "runner_default", "terra": "runner_default"},
            "roles": {"glm": {}, "terra": {}},
        }
        role_route.moved(SimpleNamespace(glm_model=MIMO), settings)
        self.assertEqual({"glm": "user_explicit", "terra": "runner_default"}, settings["route_origins"])
        # A launch that names nothing leaves the record alone, so a saved run's hash is stable.
        before = dict(settings)
        role_route.moved(SimpleNamespace(), settings)
        self.assertEqual(before, settings)
        # A run saved before the record existed gains one only for the routes it moves.
        legacy = {"roles": {"glm": {}, "terra": {}}}
        role_route.moved(SimpleNamespace(), legacy)
        self.assertNotIn("route_origins", legacy)
        role_route.moved(SimpleNamespace(terra_model=GLM), legacy)
        self.assertEqual({"terra": "user_explicit"}, legacy["route_origins"])


class ConfiguredRouteOriginTests(unittest.TestCase):
    """The record configure saves, through the same call the runner makes."""

    def setUp(self):
        provider_patch = patch.object(runner, "opencode", oc)
        provider_patch.start()
        self.addCleanup(provider_patch.stop)
        # Hermetic default-provider resolution; see test_planning.PlanningTests.setUp.
        config_home = tempfile.TemporaryDirectory()
        self.addCleanup(config_home.cleanup)
        env_patch = patch.dict(os.environ, {"XDG_CONFIG_HOME": config_home.name})
        env_patch.start()
        self.addCleanup(env_patch.stop)
        previous_provider = os.environ.pop("AUTOCODE_PROVIDER", None)
        if previous_provider is not None:
            self.addCleanup(os.environ.__setitem__, "AUTOCODE_PROVIDER", previous_provider)

    configure_args = test_planning.PlanningTests.configure_args

    def configure(self, state, **overrides):
        with patch.object(oc, "local_settings", return_value={"engine": "opencode"}):
            return autocode_configure.configure(
                self.configure_args(**overrides),
                state,
                planning=planning,
                milestones=milestones,
                autopilot=autopilot,
                opencode=runner.opencode,
            )

    def test_reviewer_effort_origin_is_independent_of_explicit_model(self):
        for effort, origin in ((None, "runner_default"), ("medium", "user_explicit")):
            with self.subTest(effort=effort):
                settings = self.configure(
                    {"workspace": "/fixture", "iteration": 0},
                    plan_reviewer_model="openai/gpt-6-sol",
                    plan_reviewer_reasoning_effort=effort,
                )
                self.assertEqual("user_explicit", settings["route_origins"]["plan_reviewer"])
                self.assertEqual(origin, settings["effort_origins"]["plan_reviewer"])
                state = {"settings": settings, "workflow": {"clarity": "clear", "brief_size": "small"}}
                planning.route_for(state, "astra_challenge")
                self.assertEqual("medium", settings["roles"]["plan_reviewer"]["reasoning_effort"])

    def test_saved_effort_keeps_its_origin_unless_an_effort_override_is_named(self):
        settings = self.configure({"workspace": "/fixture", "iteration": 0})
        settings.pop("effort_origins")  # legacy saved settings, not a guessed origin
        settings["roles"]["plan_reviewer"]["reasoning_effort"] = "medium"
        state = {"workspace": "/fixture", "iteration": 0, "settings": settings}
        kept = self.configure(state, plan_reviewer_model="openai/gpt-6-sol")
        self.assertNotIn("effort_origins", kept)
        planning.apply_reviewer_effort_tier({"settings": kept})
        self.assertEqual("medium", kept["roles"]["plan_reviewer"]["reasoning_effort"])
        moved = self.configure(state, plan_reviewer_reasoning_effort="high")
        self.assertEqual("user_explicit", moved["effort_origins"]["plan_reviewer"])
        planning.apply_reviewer_effort_tier({"settings": moved})
        self.assertEqual("high", moved["roles"]["plan_reviewer"]["reasoning_effort"])
        self.assertNotIn("effort_origins", settings, "configure must not mutate the input checkpoint")

    def test_project_reviewer_effort_is_explicit_after_the_public_parser_loads_it(self):
        import autocode_args

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            (project / ".autocode").mkdir()
            (project / ".autocode/config.toml").write_text('plan-reviewer-reasoning-effort = "medium"\n')
            args, _ = autocode_args.parse(None, ["Build a greeting", "--workspace", directory], oc.DEFAULT_MODELS)
            with patch.object(oc, "local_settings", return_value={"engine": "opencode"}):
                settings = autocode_configure.configure(
                    args,
                    {"workspace": directory, "iteration": 0},
                    planning=planning,
                    milestones=milestones,
                    autopilot=autopilot,
                    opencode=oc,
                )
            self.assertEqual("user_explicit", settings["effort_origins"]["plan_reviewer"])
            planning.apply_reviewer_effort_tier({"settings": settings})
            self.assertEqual("medium", settings["roles"]["plan_reviewer"]["reasoning_effort"])

    def test_ignored_global_effort_does_not_pin_a_different_reviewer_default(self):
        settings = self.configure({"workspace": "/fixture", "iteration": 0}, reasoning_effort="medium")
        self.assertEqual("high", settings["roles"]["plan_reviewer"]["reasoning_effort"])
        self.assertEqual("runner_default", settings["effort_origins"]["plan_reviewer"])

    def test_auto_selected_effort_and_applicable_global_override_have_distinct_origins(self):
        for selected, global_effort, expected in (
            (True, None, "auto_selected"),
            (True, "medium", "user_explicit"),
            (False, None, "runner_default"),
        ):
            with self.subTest(selected=selected, global_effort=global_effort):
                settings = {"roles": {"plan_reviewer": {"reasoning_effort": "medium"}}}
                if selected:
                    settings["route_selection"] = {"routes": {"plan_reviewer": {"origin": "auto_selected"}}}
                role_route.record_efforts(SimpleNamespace(reasoning_effort=global_effort), settings, fresh=True)
                self.assertEqual(expected, settings["effort_origins"]["plan_reviewer"])
                planning.apply_reviewer_effort_tier({"settings": settings})
                self.assertEqual(
                    "medium" if global_effort else "low", settings["roles"]["plan_reviewer"]["reasoning_effort"]
                )

    def test_configure_records_whether_the_person_chose_the_builders_effort(self):
        # The light first Builder attempt (#1710) lowers only the runner's own default.
        for flags, origin in (({}, "runner_default"), ({"terra_reasoning_effort": "high"}, "user_explicit")):
            with self.subTest(flags=flags):
                settings = self.configure({"workspace": "/fixture", "iteration": 0}, **flags)
                self.assertEqual(origin, settings["effort_origins"]["terra"])

    def test_the_builders_effort_provenance_follows_the_same_rule(self):
        for named, global_effort, fresh, expected in (
            ("high", None, True, "user_explicit"),
            (None, "medium", True, "user_explicit"),
            (None, None, True, "runner_default"),
            (None, None, False, None),
        ):
            with self.subTest(named=named, global_effort=global_effort, fresh=fresh):
                settings = {"roles": {"terra": {"reasoning_effort": "medium"}}}
                args = SimpleNamespace(terra_reasoning_effort=named, reasoning_effort=global_effort)
                role_route.record_efforts(args, settings, fresh=fresh)
                self.assertEqual(expected, (settings.get("effort_origins") or {}).get("terra"))
                self.assertEqual(
                    expected in ("runner_default", "auto_selected"), role_route.effort_can_reduce(settings, "terra")
                )

    def test_a_new_run_records_why_each_role_has_the_route_it_has(self):
        settings = self.configure({"workspace": "/fixture", "iteration": 0}, sol_model=LUNA)
        self.assertEqual("user_explicit", settings["route_origins"]["sol"])
        self.assertEqual(LUNA, settings["roles"]["sol"]["model"])
        for role in ("terra", "glm", "requirements", "plan_reviewer", "completion", "astra"):
            self.assertEqual("runner_default", settings["route_origins"][role], role)
        self.assertEqual(set(settings["roles"]), set(settings["route_origins"]), "every route has a reason")

    def test_a_flag_that_names_two_routes_records_both_as_the_persons_choice(self):
        settings = self.configure({"workspace": "/fixture", "iteration": 0}, glm_model=MIMO)
        self.assertEqual(MIMO, settings["roles"]["glm"]["model"])
        self.assertEqual(MIMO, settings["roles"]["requirements"]["model"], "--glm-model names Requirements too")
        self.assertEqual("user_explicit", settings["route_origins"]["glm"])
        self.assertEqual("user_explicit", settings["route_origins"]["requirements"])
        self.assertEqual("runner_default", settings["route_origins"]["plan_reviewer"])

    def test_single_model_records_one_choice_for_every_route(self):
        settings = self.configure({"workspace": "/fixture", "iteration": 0}, single_model=LUNA)
        self.assertTrue(settings["route_origins"])
        self.assertEqual({"single_model"}, set(settings["route_origins"].values()))

    def test_a_saved_run_keeps_its_origins_until_a_flag_moves_a_route(self):
        original = self.configure({"workspace": "/fixture", "iteration": 0}, sol_model=LUNA)
        state = {"workspace": "/fixture", "settings": original, "sessions": {"astra": "saved"}}
        resumed = self.configure(state)
        self.assertEqual(original["route_origins"], resumed["route_origins"], "a plain resume re-chooses nothing")

        moved = self.configure(state, glm_model=MIMO)
        self.assertEqual(MIMO, moved["roles"]["glm"]["model"])
        self.assertEqual("user_explicit", moved["route_origins"]["glm"], "the resume that moved it records it")
        self.assertEqual("user_explicit", moved["route_origins"]["sol"], "the start's record survives the resume")
        self.assertEqual("runner_default", moved["route_origins"]["terra"])
        self.assertEqual(
            "runner_default",
            moved["route_origins"]["requirements"],
            "a saved run's --glm-model moves the Planner only, so it records the Planner only",
        )
        self.assertEqual(original["route_origins"], resumed["route_origins"], "the untouched resume is unchanged")

    def test_a_run_saved_before_the_record_existed_stays_unrecorded(self):
        legacy = self.configure({"workspace": "/fixture", "iteration": 0})
        del legacy["route_origins"]
        state = {"workspace": "/fixture", "settings": legacy, "sessions": {"astra": "saved"}}
        resumed = self.configure(state)
        self.assertNotIn("route_origins", resumed, "a plain resume of an old run invents no reason")
        self.assertEqual("unrecorded", role_route.records({"settings": resumed})["sol"]["origin"])
        moved = self.configure(state, sol_model=LUNA)
        self.assertEqual({"sol": "user_explicit"}, moved["route_origins"], "it records only the route it moved")


if __name__ == "__main__":
    unittest.main()
