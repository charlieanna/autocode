"""When a started run's stage models may change, and what the status view and list offer (pure)."""

import copy
import importlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_action_stage_model as action_stage_model
import autocode_quota_route as quota_route
import autocode_role_route as role_route
import autocode_role_transport as role_transport
import autocode_run_stage_models as stage_models
import autocode_run_view as run_view
import autocode_stuck_job as stuck_job
from units import autoresolver, autoreview


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


class _StageModelRunner:
    def __init__(self, state):
        self.saved_state = None
        self.rejected = None
        self.opencode = SimpleNamespace(CONFIGURED=False)
        self.interventions = SimpleNamespace(pending=lambda _run_dir: False)
        self.status_command = SimpleNamespace(
            reject_input=lambda _r, _s, _w, _d, err: setattr(self, "rejected", str(err)) or 2
        )
        self._state = state

    @staticmethod
    def now():
        return "2026-11-13T00:00:00+00:00"

    def commit_user_action(self, _old, candidate, _run_dir):
        self.saved_state = copy.deepcopy(candidate)


class InheritedStageRouteSyncTests(unittest.TestCase):
    @staticmethod
    def _ext_identity(provider):
        return {
            "provider": provider,
            "identity": {
                "engine": provider,
                "identity_version": 1,
                "executable": f"/usr/bin/{provider}",
                "version": "1.0",
                "config_sha256": "a" * 64,
            },
        }

    def test_changing_tester_syncs_materialized_acceptance_tester_and_repeat_changes_still_sync(self):
        tester_route = {
            "engine": "opencode",
            "provider": "kilocode",
            "model": "anthropic/claude-opus-5",
            "reasoning_effort": "high",
        }
        ext_id = self._ext_identity("kilocode")
        run_state = {
            "status": "PAUSED_BUDGET",
            "settings": {
                "engine": "opencode",
                "provider": "opencode",
                "roles": {
                    "builder": {"engine": "opencode", "model": "xiaomi-token-plan-sgp/mimo-v2.6-pro"},
                    "tester": copy.deepcopy(tester_route),
                    "acceptance_tester": copy.deepcopy(tester_route),
                },
                "route_origins": {"tester": "user_explicit"},
                "effort_origins": {"tester": "user_explicit"},
                "transport_identities": {
                    "role:tester": copy.deepcopy(ext_id),
                    "role:acceptance_tester": copy.deepcopy(ext_id),
                },
            },
            "sessions": {"tester": "sess-t", "acceptance_tester": "sess-at"},
            "user_events": [],
        }
        before_settings = copy.deepcopy(run_state["settings"])
        record = quota_route.assign(
            run_state,
            "tester",
            "openai/gpt-6-sol",
            at="t1",
            via=stage_models.VIA,
            effort="medium",
        )
        self.assertEqual("openai/gpt-6-sol", run_state["settings"]["roles"]["acceptance_tester"]["model"])
        self.assertEqual("medium", run_state["settings"]["roles"]["acceptance_tester"]["reasoning_effort"])
        self.assertEqual(ext_id, run_state["settings"]["transport_identities"]["role:acceptance_tester"])
        self.assertNotIn("acceptance_tester", run_state["sessions"])
        self.assertNotIn("acceptance_tester", run_state["settings"].get("route_origins", {}))
        self.assertEqual(
            "derived",
            role_route.origin(
                run_state["settings"], "acceptance_tester", run_state["settings"]["roles"]["acceptance_tester"]
            ),
        )
        self.assertIn("acceptance_tester", record["inherited_routes"])
        self.assertEqual("tester", record["inherited_routes"]["acceptance_tester"]["parent"])

        # A second change continues to sync acceptance_tester, and moving off an external provider pops stale identity.
        t2_state = copy.deepcopy(run_state)
        t2_state["user_events"] = []
        t2_state["settings"]["roles"]["tester"].pop("provider")
        t2_state["settings"]["transport_identities"].pop("role:tester")
        before_t2_no_ext = copy.deepcopy(t2_state["settings"])
        quota_route.assign(t2_state, "tester", "anthropic/claude-sonnet-5", at="t2", via=stage_models.VIA, effort="low")
        self.assertEqual("anthropic/claude-sonnet-5", t2_state["settings"]["roles"]["acceptance_tester"]["model"])
        self.assertNotIn("provider", t2_state["settings"]["roles"]["acceptance_tester"])
        self.assertNotIn("role:acceptance_tester", t2_state["settings"]["transport_identities"])
        self.assertEqual(before_t2_no_ext, quota_route.unassigned(t2_state, t2_state["settings"]))

        # Unassigning a derived external identity whose prior identity was absent pops role:acceptance_tester.
        onto_ext = copy.deepcopy(run_state)
        onto_ext["user_events"] = []
        onto_ext["settings"]["roles"]["acceptance_tester"] = {"engine": "opencode", "model": "old"}
        onto_ext["settings"]["transport_identities"].pop("role:acceptance_tester")
        before_onto_ext = copy.deepcopy(onto_ext["settings"])
        onto_record = quota_route.assign(
            onto_ext, "tester", "anthropic/claude-opus-5", at="t2", via=stage_models.VIA, effort="high"
        )
        self.assertIsNone(onto_record["inherited_routes"]["acceptance_tester"]["transport_identity"]["from"])
        self.assertEqual(ext_id, onto_ext["settings"]["transport_identities"]["role:acceptance_tester"])
        self.assertEqual(before_onto_ext, quota_route.unassigned(onto_ext, onto_ext["settings"]))

        # Unassigning restores both parent and derived routes cleanly after multiple assignments.
        quota_route.assign(run_state, "tester", "openai/gpt-6-nova", at="t2", via=stage_models.VIA, effort="low")
        self.assertEqual("openai/gpt-6-nova", run_state["settings"]["roles"]["acceptance_tester"]["model"])
        restored = quota_route.unassigned(run_state, run_state["settings"])
        self.assertEqual(before_settings, restored)

    def test_stage_model_action_default_effort_and_stale_child_refusal(self):
        run_state = {
            "status": "PAUSED_BUDGET",
            "settings": {
                "engine": "codex",
                "roles": {
                    "builder": {"engine": "codex", "model": "gpt-5.6-terra", "reasoning_effort": "medium"},
                    "tester": {"engine": "codex", "model": "gpt-6-luna", "reasoning_effort": "high"},
                    "acceptance_tester": {"engine": "codex", "model": "gpt-5.6-sol", "reasoning_effort": "high"},
                },
            },
            "sessions": {"tester": "s1", "acceptance_tester": "s2"},
            "user_events": [],
        }
        # Stale child lets stage_models re-apply the same parent model/effort so child catches up.
        self.assertIsNone(stage_models.refusal(run_state, "tester", "gpt-6-luna", "high"))

        # First change with tester starting at None -> 'high' preserves 'reasoning_effort': None key presence on undo.
        none_to_high = copy.deepcopy(run_state)
        none_to_high["settings"]["roles"]["tester"] = {
            "engine": "codex",
            "model": "gpt-5.6-sol",
            "reasoning_effort": None,
        }
        before_none = copy.deepcopy(none_to_high["settings"])
        runner0 = _StageModelRunner(none_to_high)
        self.assertEqual(
            0,
            action_stage_model.apply(
                runner0,
                SimpleNamespace(stage_model=("tester", "gpt-6-luna", "high")),
                None,
                none_to_high,
                None,
                "/tmp/run",
                "/tmp/ws",
                None,
            ),
        )
        self.assertTrue(runner0.saved_state["user_events"][-1]["settings_change"]["reasoning_effort"]["present"])
        self.assertEqual(before_none, quota_route.unassigned(runner0.saved_state, runner0.saved_state["settings"]))

        # Applying with effort='default' syncs child to None both on model change and effort-only change.
        run_state["settings"]["declared_variants"] = {"gpt-6-luna": ["low", "medium"], "gpt-5.6-sol": ["low", "high"]}
        run_state["settings"]["roles"]["tester"] = {
            "engine": "codex",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "high",
        }
        run_state["settings"]["roles"]["acceptance_tester"] = {
            "engine": "codex",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "high",
        }
        before_settings = copy.deepcopy(run_state["settings"])
        args = SimpleNamespace(stage_model=("tester", "gpt-6-luna", "default"))
        runner = _StageModelRunner(run_state)
        self.assertEqual(0, action_stage_model.apply(runner, args, None, run_state, None, "/tmp/run", "/tmp/ws", None))
        saved = runner.saved_state
        self.assertEqual("gpt-6-luna", saved["settings"]["roles"]["acceptance_tester"]["model"])
        self.assertIsNone(saved["settings"]["roles"]["acceptance_tester"]["reasoning_effort"])
        self.assertEqual(
            "gpt-5.6-sol", saved["user_events"][-1]["inherited_routes"]["acceptance_tester"]["from"]["model"]
        )
        self.assertEqual({"from": "high", "to": None, "named": True}, saved["user_events"][-1]["effort"])
        self.assertEqual({"from": "high", "to": None}, saved["user_events"][-1]["settings_change"]["reasoning_effort"])
        self.assertNotIn("effort_fits", saved["settings"])
        self.assertEqual(before_settings, quota_route.unassigned(saved, saved["settings"]))

        # When another role already has an effort_fits entry, setting tester to 'default' keeps that entry.
        keep_other = copy.deepcopy(run_state)
        keep_other["settings"]["effort_fits"] = {"builder": {"from": "high", "to": "medium", "reason": "route_fit"}}
        runner_keep = _StageModelRunner(keep_other)
        self.assertEqual(
            0, action_stage_model.apply(runner_keep, args, None, keep_other, None, "/tmp/run", "/tmp/ws", None)
        )
        self.assertEqual(["builder"], list(runner_keep.saved_state["settings"]["effort_fits"]))

        # Same-model change to default syncs stale/higher child effort even without assign() adding inherited_routes.
        same_model = {
            "status": "PAUSED_BUDGET",
            "settings": {
                "engine": "codex",
                "roles": {
                    "builder": {"engine": "codex", "model": "gpt-5.6-terra", "reasoning_effort": "medium"},
                    "tester": {"engine": "codex", "model": "gpt-6-luna", "reasoning_effort": "high"},
                    "acceptance_tester": {"engine": "codex", "model": "gpt-6-luna", "reasoning_effort": "high"},
                },
            },
            "user_events": [],
        }
        runner2 = _StageModelRunner(same_model)
        self.assertEqual(
            0,
            action_stage_model.apply(
                runner2,
                SimpleNamespace(stage_model=("tester", "gpt-6-luna", "default")),
                None,
                same_model,
                None,
                "/tmp/run",
                "/tmp/ws",
                None,
            ),
        )
        self.assertIsNone(runner2.saved_state["settings"]["roles"]["acceptance_tester"]["reasoning_effort"])
        self.assertEqual(
            "gpt-6-luna", runner2.saved_state["user_events"][-1]["inherited_routes"]["acceptance_tester"]["to"]["model"]
        )
        self.assertIn(
            "already runs gpt-6-luna at default",
            stage_models.refusal(runner2.saved_state, "tester", "gpt-6-luna", "default"),
        )

    def test_architect_cap_and_resolver_override_and_job_locks(self):
        run_state = {
            "status": "PAUSED_BUDGET",
            "settings": {
                "engine": "codex",
                "roles": {
                    "plan_reviewer": {"model": "gpt-5.6-sol", "reasoning_effort": "high"},
                    "task_planner": {"model": "gpt-6-astra", "reasoning_effort": "high"},
                    "architect": {"model": "gpt-5.6-sol", "reasoning_effort": "medium"},
                    "resolver": {"model": "gpt-6-astra", "reasoning_effort": "high"},
                },
            },
            "user_events": [],
        }
        # Updating task_planner syncs resolver, but does NOT hijack architect when plan_reviewer exists.
        quota_route.assign(run_state, "task_planner", "gpt-6-luna", at="t1", via=stage_models.VIA, effort="high")
        self.assertEqual("gpt-6-luna", run_state["settings"]["roles"]["resolver"]["model"])
        self.assertEqual("gpt-5.6-sol", run_state["settings"]["roles"]["architect"]["model"])

        # Updating plan_reviewer caps architect at ARCHITECT_MAX_EFFORT ("medium") and fits when medium is undeclared.
        run_state["settings"]["declared_variants"] = {"gpt-6-nova": ["low", "high"]}
        quota_route.assign(run_state, "plan_reviewer", "gpt-6-nova", at="t2", via=stage_models.VIA, effort="high")
        self.assertEqual("high", run_state["settings"]["roles"]["architect"]["reasoning_effort"])
        run_state["settings"]["declared_variants"] = {"gpt-6-astra": ["low", "medium", "high"]}
        quota_route.assign(run_state, "plan_reviewer", "gpt-6-astra", at="t3", via=stage_models.VIA, effort="high")
        self.assertEqual("medium", run_state["settings"]["roles"]["architect"]["reasoning_effort"])
        # Absent reasoning_effort on parent defaults architect to ARCHITECT_MAX_EFFORT; low stays low.
        del run_state["settings"]["roles"]["plan_reviewer"]["reasoning_effort"]
        run_state["settings"]["roles"]["architect"]["reasoning_effort"] = "low"
        self.assertEqual("medium", quota_route.inherited_route(run_state["settings"], "architect")["reasoning_effort"])
        run_state["settings"]["roles"]["plan_reviewer"]["reasoning_effort"] = "low"
        self.assertEqual("low", quota_route.inherited_route(run_state["settings"], "architect")["reasoning_effort"])
        # Overlong parent effort above medium caps to medium.
        run_state["settings"]["roles"]["plan_reviewer"]["reasoning_effort"] = "xhigh"
        self.assertEqual("medium", quota_route.inherited_route(run_state["settings"], "architect")["reasoning_effort"])

        # Explicit user override on resolver prevents parent sync.
        explicit = copy.deepcopy(run_state)
        explicit["settings"]["route_origins"] = {"resolver": "user_explicit"}
        quota_route.assign(explicit, "task_planner", "gpt-5.6-terra", at="t4", via=stage_models.VIA)
        self.assertEqual("gpt-6-luna", explicit["settings"]["roles"]["resolver"]["model"])

        # Prior direct route_assignment on child also locks child from automatic parent sync.
        assigned = copy.deepcopy(run_state)
        assigned["user_events"].append({"kind": quota_route.KIND, "role": "resolver", "to": "gpt-6-luna"})
        quota_route.assign(assigned, "task_planner", "gpt-5.6-terra", at="t4", via=stage_models.VIA)
        self.assertEqual("gpt-6-luna", assigned["settings"]["roles"]["resolver"]["model"])

        # Active stage or bound failed job retry locks child from syncing.
        live = {**copy.deepcopy(run_state), "active_stage": {"stage": "resolve"}}
        self.assertTrue(quota_route.derived_locked_by_job(live, "resolver"))
        quota_route.assign(live, "task_planner", "gpt-5.6-terra", at="t4", via=stage_models.VIA)
        self.assertEqual("gpt-6-luna", live["settings"]["roles"]["resolver"]["model"])

        paused_job = {
            **copy.deepcopy(run_state),
            "status": "PAUSED_JOB_FAILURE",
            "job_failure": {"stage": "resolve", "job_retry_token": "tok"},
        }
        self.assertTrue(quota_route.derived_locked_by_job(paused_job, "resolver"))
        authorized_job = {
            **copy.deepcopy(run_state),
            "status": "RUNNING",
            "job_failure": {"stage": "resolve", "job_retry_token": "tok"},
            "job_retry_authorization": {"consumed": False, "stage": "resolve", "token": "tok"},
        }
        self.assertTrue(quota_route.derived_locked_by_job(authorized_job, "resolver"))
        self.assertFalse(quota_route.derived_locked_by_job(authorized_job, "architect"))

        # Missing parent yields None from derived_parent / inherited_route / sync_derived_role.
        self.assertIsNone(quota_route.derived_parent({"roles": {}}, "resolver"))
        self.assertIsNone(quota_route.inherited_route({"roles": {}}, "architect"))
        missing_parent = {"settings": {"roles": {"architect": {"model": "m"}}}}
        self.assertIsNone(quota_route.sync_derived_role(missing_parent, "architect", parent_role="plan_reviewer"))
        self.assertEqual({"model": "m"}, missing_parent["settings"]["roles"]["architect"])

    def test_a_climbed_derived_role_keeps_its_rung_until_its_parent_moves(self):
        # The Resolver climbs its own route after a rejected report; a prepare sync that re-derived
        # it from an unmoved parent relaunched the retry on the rung that was just rejected.
        climbed = {
            "status": "PAUSED_INVALID_OUTPUT",
            "settings": {
                "engine": "codex",
                "roles": {
                    "task_planner": {"model": "gpt-6-astra", "reasoning_effort": "high"},
                    "resolver": {"model": "gpt-6-astra", "reasoning_effort": "xhigh"},
                },
            },
            "reasoning_escalations": [
                {
                    "role": "resolver",
                    "previous": {"model": "gpt-6-astra", "reasoning_effort": "high"},
                    "selected": {"model": "gpt-6-astra", "reasoning_effort": "xhigh"},
                }
            ],
            "user_events": [],
        }
        self.assertIsNone(quota_route.sync_derived_role(climbed, "resolver"))
        self.assertEqual("xhigh", climbed["settings"]["roles"]["resolver"]["reasoning_effort"])
        quota_route.assign(climbed, "task_planner", "gpt-6-luna", at="t5", via=stage_models.VIA, effort="high")
        self.assertEqual({"model": "gpt-6-luna", "reasoning_effort": "high"}, climbed["settings"]["roles"]["resolver"])

    def test_release_route_and_transport_inherit_cleanup(self):
        kilo = self._ext_identity("kilocode")
        kilo_v2 = {**copy.deepcopy(kilo), "identity": {**kilo["identity"], "version": "2.0"}}
        claude = self._ext_identity("claude")
        settings = {
            "provider": "opencode",
            "roles": {
                "builder": {"engine": "opencode", "provider": "kilocode", "model": "anthropic/claude-opus-5"},
                "tester": {"engine": "opencode", "provider": "kilocode", "model": "anthropic/claude-opus-5"},
                "acceptance_tester": {"engine": "opencode", "provider": "kilocode", "model": "anthropic/claude-opus-5"},
                "stuck_investigator": {
                    "engine": "opencode",
                    "provider": "kilocode",
                    "model": "anthropic/claude-opus-5",
                },
            },
            "transport_identities": {
                "opencode": {"provider": "opencode"},
                "role:builder": copy.deepcopy(kilo),
                "role:tester": copy.deepcopy(kilo_v2),
                "role:acceptance_tester": copy.deepcopy(claude),
                "role:stuck_investigator": copy.deepcopy(kilo),
            },
        }
        # Re-inheriting prefers the explicit parent source over another role sharing the same provider.
        self.assertEqual(kilo_v2, role_transport.inherit(settings, "acceptance_tester", "tester"))
        self.assertEqual(kilo_v2, settings["transport_identities"]["role:acceptance_tester"])
        # When the direct source lacks a matching identity, inherit falls back across other role:* identities.
        saved_tester_id = settings["transport_identities"].pop("role:tester")
        settings["transport_identities"].pop("role:acceptance_tester")
        self.assertEqual(kilo, role_transport.inherit(settings, "acceptance_tester", "tester"))
        self.assertEqual(kilo, settings["transport_identities"]["role:acceptance_tester"])
        settings["transport_identities"]["role:tester"] = saved_tester_id
        # Moving to the whole-run provider pops any stale external identity instead of copying an identity of that provider.
        settings["roles"]["acceptance_tester"]["provider"] = "opencode"
        settings["transport_identities"]["role:tester"] = {"provider": "opencode"}
        self.assertIsNone(role_transport.inherit(settings, "acceptance_tester", "tester"))
        self.assertNotIn("role:acceptance_tester", settings["transport_identities"])
        settings["transport_identities"]["role:tester"] = saved_tester_id
        # If target switches to an external provider with no matching source or sibling identity, stale identity is popped.
        settings["transport_identities"]["role:acceptance_tester"] = copy.deepcopy(claude)
        settings["roles"]["acceptance_tester"]["provider"] = "other_tool"
        self.assertIsNone(role_transport.inherit(settings, "acceptance_tester", "tester"))
        self.assertNotIn("role:acceptance_tester", settings["transport_identities"])
        with self.assertRaisesRegex(ValueError, "missing for acceptance_tester"):
            role_transport.validate(settings)
        settings["roles"]["acceptance_tester"].pop("provider")
        # Release pops both stuck_investigator role and its transport identity so validate() passes cleanly.
        released = stuck_job.release_route({"settings": settings})
        self.assertEqual("kilocode", released["provider"])
        self.assertNotIn("role:stuck_investigator", settings["transport_identities"])
        role_transport.validate(settings)

    def test_autoreview_and_autoresolver_prepare_sync_already_materialized_routes(self):
        with tempfile.TemporaryDirectory() as tmp:
            ws = tmp
            run_state = {
                "workspace": ws,
                "task": "Task",
                "goal_contract": {"hash": "h", "body": {"acceptance_criteria": []}},
                "current_task": {"id": "T1"},
                "resolution_request": {
                    "contract_hash": "h",
                    "task_id": "T1",
                    "source_output": __file__,
                    "evidence_hashes": {__file__: "d"},
                    "source_revision": "rev",
                },
                "diagnosis_request": {"contract_hash": "h", "source_revision": "rev", "evidence_hashes": {}},
                "settings": {
                    "engine": "codex",
                    "roles": {
                        "tester": {"engine": "codex", "model": "gpt-6-luna", "reasoning_effort": "high"},
                        "acceptance_tester": {"engine": "codex", "model": "gpt-5.6-sol", "reasoning_effort": "high"},
                        "plan_reviewer": {"engine": "codex", "model": "gpt-6-luna", "reasoning_effort": "high"},
                        "architect": {"engine": "codex", "model": "gpt-6-astra", "reasoning_effort": "medium"},
                        "task_planner": {"engine": "codex", "model": "gpt-5.6-sol", "reasoning_effort": "high"},
                        "resolver": {"engine": "codex", "model": "gpt-6-astra", "reasoning_effort": "high"},
                    },
                },
            }
            fresh_state = copy.deepcopy(run_state)
            for derived in ("acceptance_tester", "architect", "resolver"):
                fresh_state["settings"]["roles"].pop(derived)
            with (
                patch.object(autoreview.acceptance_tests_job, "prompt", return_value=("p", {})),
                patch.object(autoreview.design_check_job, "prompt", return_value=("p", {})),
                patch.object(autoreview.autoplanner, "workspace_inventory", return_value={}),
                patch.object(autoreview.autoplanner, "engine_for", return_value="codex"),
                patch.object(autoresolver, "guard"),
                patch.object(autoresolver, "diagnosis_guard"),
                patch.object(autoresolver.failures, "repeated", return_value=False),
                patch.object(
                    autoresolver,
                    "execution_request",
                    return_value=SimpleNamespace(
                        prompt="CURRENT HANDOFF DATA\n{}",
                        metrics={},
                        schema={"properties": {"status": {}, "user_request": {"properties": {}}}, "required": []},
                    ),
                ),
            ):
                autoreview.prepare(run_state, "write_acceptance_tests", None, None)
                autoreview.prepare(run_state, "check_design", None, None)
                autoresolver.prepare(run_state, "resolve", None, None)
                self.assertEqual("gpt-5.6-sol", run_state["settings"]["roles"]["resolver"]["model"])
                run_state["settings"]["roles"]["resolver"]["model"] = "stale"
                autoresolver.prepare(run_state, "diagnose", None, None)
                # Fresh (unmaterialized) roles still materialize via the fallback creation branch.
                autoreview.prepare(fresh_state, "write_acceptance_tests", None, None)
                autoreview.prepare(fresh_state, "check_design", None, None)
                autoresolver.prepare(fresh_state, "resolve", None, None)
                fresh_state["settings"]["roles"].pop("resolver")
                autoresolver.prepare(fresh_state, "diagnose", None, None)
            self.assertEqual("gpt-6-luna", run_state["settings"]["roles"]["acceptance_tester"]["model"])
            self.assertEqual(
                ("gpt-6-luna", "medium"),
                (
                    run_state["settings"]["roles"]["architect"]["model"],
                    run_state["settings"]["roles"]["architect"]["reasoning_effort"],
                ),
            )
            self.assertEqual("gpt-5.6-sol", run_state["settings"]["roles"]["resolver"]["model"])
            self.assertEqual("gpt-6-luna", fresh_state["settings"]["roles"]["acceptance_tester"]["model"])
            self.assertEqual(
                ("gpt-6-luna", "medium"),
                (
                    fresh_state["settings"]["roles"]["architect"]["model"],
                    fresh_state["settings"]["roles"]["architect"]["reasoning_effort"],
                ),
            )
            self.assertEqual("gpt-5.6-sol", fresh_state["settings"]["roles"]["resolver"]["model"])

            # Verify package-relative import path (`from .. import autocode_quota_route`) binds cleanly too.
            pkg_root = str(Path(__file__).resolve().parent.parent)
            prior_tools = {k: v for k, v in sys.modules.items() if k == "tools" or k.startswith("tools.")}
            added_sys_path = pkg_root not in sys.path
            if added_sys_path:
                sys.path.insert(0, pkg_root)
            try:
                pkg_autoreview = importlib.import_module("tools.units.autoreview")
                pkg_autoresolver = importlib.import_module("tools.units.autoresolver")
                self.assertIs(
                    pkg_autoreview.quota_route.sync_derived_role, pkg_autoresolver.quota_route.sync_derived_role
                )
                self.assertTrue(callable(pkg_autoreview.quota_route.escalation.unclimbed))
            finally:
                if added_sys_path and pkg_root in sys.path:
                    sys.path.remove(pkg_root)
                for key in list(sys.modules):
                    if key == "tools" or key.startswith("tools."):
                        if key in prior_tools:
                            sys.modules[key] = prior_tools[key]
                        else:
                            sys.modules.pop(key, None)


if __name__ == "__main__":
    unittest.main()
