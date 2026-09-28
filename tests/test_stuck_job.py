"""A stage that stops converging goes to an Investigator before the run pauses for the user."""
import copy
import tempfile
import unittest
from pathlib import Path

import autocode_jobs as jobs
import autocode_stuck_job as stuck
import autopilot
from units import autoresolver, common


class Paused(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def state_for(workspace="/nowhere", next_stage="terra", status="RUNNING", **extra):
    state = {"version": 3, "task": "Fix the rounding drift", "workspace": workspace, "status": status,
             "phase": "EXECUTING", "next_stage": next_stage, "stages": [], "iteration": 1,
             "settings": {"limits": {"no_progress_batches": 3}, "roles": {
                 "astra": {"model": "openai/gpt-6-astra", "engine": "opencode"},
                 "plan_reviewer": {"model": "openai/gpt-6-astra", "engine": "opencode", "reasoning_effort": "high"},
                 "glm": {"model": "zai-coding-plan/glm-5.3"}, "terra": {"model": "openai/gpt-6-sol"},
                 "sol": {"model": "zai-coding-plan/glm-5.3"}}}}
    state.update(extra)
    return state


def report(recommendation="retry", **overrides):
    value = {"diagnosis": "The Planner cites the diagnosis with prose after its path.", "cause": "stage_output",
             "guidance": "Cite docs/bugs/cent-drift.json exactly; put the explanation in the summary.",
             "recommendation": recommendation, "user_question": "", "evidence_refs": ["docs/bugs/cent-drift.json"]}
    if recommendation == "pause":
        value.update(guidance="", cause="needs_user", user_question="May the fix change the public API?")
    value.update(overrides)
    return value


class InterceptTests(unittest.TestCase):
    def test_a_non_convergence_pause_goes_to_the_investigator(self):
        state = state_for(status="PAUSED_REPEATED_FAILURE", stop_reason="rejected", paused_at="t",
                          pending_report_repair={"attempts": 2})
        self.assertTrue(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "rejected three times"))
        self.assertEqual(("RUNNING", "INVESTIGATING", stuck.STAGE), (state["status"], state["phase"], state["next_stage"]))
        self.assertNotIn("stop_reason", state)
        self.assertNotIn("pending_report_repair", state)  # set aside, or the before-hook replays it first
        request = state["stuck_investigation"]
        self.assertEqual(("terra", "PAUSED_REPEATED_FAILURE", {"attempts": 2}),
                         (request["stage"], request["status"], request["pending_report_repair"]))
        self.assertEqual("investigating", state["stuck_investigations"][0]["outcome"])

    def test_user_owned_and_unsafe_pauses_are_left_alone(self):
        for status, extra in (("PAUSED_BUDGET", {}), ("PAUSED_CRITERIA_CHANGE", {}), ("PAUSED_ITERATION_LIMIT", {}),
                              ("PAUSED_UNCERTAIN_STAGE", {}), ("WAITING_FOR_USER", {}),
                              ("PAUSED_REPEATED_FAILURE", {"active_stage": {"stage": "terra"}}),
                              ("PAUSED_REPEATED_FAILURE", {"version": 2}),
                              ("PAUSED_REPEATED_FAILURE", {"next_stage": stuck.STAGE}),
                              ("PAUSED_REPEATED_FAILURE", {"next_stage": "astra_diagnose"}),
                              ("PAUSED_REPEATED_FAILURE", {"next_stage": "astra_resolve"}),
                              ("PAUSED_REPEATED_FAILURE", {"settings": {"stuck_investigation": {"max_calls_per_run": 0}}})):
            with self.subTest(status=status, extra=extra):
                state = state_for(**extra)
                before = dict(state)
                self.assertFalse(stuck.intercept(state, status, "why"))
                self.assertEqual(before, state)

    def test_an_operator_authorized_retry_goes_back_to_the_operator(self):
        state = state_for(stages=[{"stage": "terra", "failure_key": "k1"}],
                          failure_retry_authorizations=[{"failure_key": "k1", "consumed": True}])
        before = copy.deepcopy(state)
        self.assertFalse(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "failed again after --retry-failed-stage"))
        self.assertEqual(before, state, "declining never touches the state")

    def test_one_investigation_per_problem_and_a_run_budget(self):
        state = state_for()
        self.assertTrue(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "x"))
        state.update(next_stage="terra", status="RUNNING")
        self.assertFalse(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "x again"))
        for stage in ("sol", "astra_review"):
            state["next_stage"] = stage
            self.assertTrue(stuck.intercept(state, "PAUSED_INVALID_OUTPUT", "x"))
        state["next_stage"] = "astra_challenge"
        self.assertFalse(stuck.intercept(state, "PAUSED_PLANNING_BUDGET", "x"), "three per run by default")


class ApplyTests(unittest.TestCase):
    def investigated(self, status, stage="terra", **extra):
        state = state_for(next_stage=stage, **extra)
        self.assertTrue(stuck.intercept(state, status, "the original reason"))
        return state

    def test_retry_after_repeated_failure_keeps_the_failure_history(self):
        history = {"k1": {"identity": {"stage": "terra"}, "count": 3}, "k2": {"identity": {"stage": "sol"}, "count": 1}}
        state = self.investigated("PAUSED_REPEATED_FAILURE", pending_report_repair={"attempts": 2},
                                  failure_history=copy.deepcopy(history),
                                  stages=[{"stage": "terra", "failure_key": "k1"}, {"stage": "sol", "failure_key": "k2"}])
        stuck.apply(state, report(), {"output": "o"}, "/ws")
        # One more attempt, not a fresh failure budget: another failure counts on top of these.
        self.assertEqual(history, state["failure_history"])
        self.assertEqual(["k1", "k2"], [row.get("failure_key") for row in state["stages"]])
        self.assertEqual({"attempts": 2}, state["report_repair_archive"][-1]["repair"])
        self.assertEqual(("RUNNING", "EXECUTING", "terra"), (state["status"], state["phase"], state["next_stage"]))
        self.assertTrue(state["stuck_investigation"]["in_force"])
        self.assertEqual("retried", state["stuck_investigations"][0]["outcome"])

    def test_the_route_exists_only_while_the_investigator_runs(self):
        state = self.investigated("PAUSED_REPEATED_FAILURE")
        state["settings"]["roles"][stuck.ROUTE] = {"model": "openai/gpt-6-astra", "engine": "opencode",
                                                   "reasoning_effort": "xhigh"}
        stuck.apply(state, report(), {"output": "o"}, "/ws")
        self.assertNotIn(stuck.ROUTE, state["settings"]["roles"])
        self.assertEqual(("openai/gpt-6-astra", "opencode"),
                         (state["stuck_investigations"][0]["model"], state["stuck_investigations"][0]["engine"]))

    def test_pinned_route_from_the_cli(self):
        from types import SimpleNamespace
        settings = {"engine": "codex"}
        self.assertIs(settings, stuck.configure(settings, SimpleNamespace()))
        self.assertNotIn("stuck_investigation", settings)
        stuck.configure(settings, SimpleNamespace(investigator_model="openai/gpt-6-sol", investigator_reasoning_effort=None))
        self.assertEqual({"model": "openai/gpt-6-sol", "reasoning_effort": "high", "provider": None, "engine": "opencode"},
                         settings["stuck_investigation"]["route"])
        stuck.configure(settings, SimpleNamespace(investigator_model=None, investigator_reasoning_effort="max"))
        self.assertEqual(("openai/gpt-6-sol", "max"), (stuck.pinned_route(settings)["model"],
                                                     stuck.pinned_route(settings)["reasoning_effort"]))
        stuck.configure(settings, SimpleNamespace(investigator_model="gpt-6-astra", investigator_reasoning_effort=None))
        self.assertEqual("codex", settings["stuck_investigation"]["route"]["engine"], "a bare name keeps the run's engine")
        with self.assertRaises(ValueError):
            stuck.configure({"engine": "codex"}, SimpleNamespace(investigator_model=None, investigator_reasoning_effort="high"))

    def test_retry_grants_exactly_one_more_review_round_or_batch(self):
        for stage, used, expected in (("astra_challenge", 2, 4), ("astra_finalize", 2, 3)):
            state = self.investigated("PAUSED_PLANNING_BUDGET", stage=stage,
                                      planning={"review_call_limit": 2, "astra_calls": used})
            stuck.apply(state, report(), {}, "/ws")
            self.assertEqual((expected, "PLANNING"), (state["planning"]["review_call_limit"], state["phase"]), stage)
        state = self.investigated("PAUSED_NO_PROGRESS", no_progress_batches=3)
        stuck.apply(state, report(), {}, "/ws")
        self.assertEqual(2, state["no_progress_batches"])

    def test_pause_restores_the_original_pause_with_the_diagnosis(self):
        state = self.investigated("PAUSED_REPEATED_FAILURE", pending_report_repair={"attempts": 2})
        stuck.apply(state, report("pause"), {}, "/ws")
        self.assertEqual(("PAUSED_REPEATED_FAILURE", "PAUSED_OR_BLOCKED", "terra"),
                         (state["status"], state["phase"], state["next_stage"]))
        self.assertIn("the original reason", state["stop_reason"])
        self.assertIn("Investigator (paused): The Planner cites", state["stop_reason"])
        self.assertIn("Needs you: May the fix change the public API?", state["stop_reason"])
        self.assertEqual({"attempts": 2}, state["pending_report_repair"])
        self.assertNotIn("stuck_investigation", state)

    def test_a_diagnose_only_pause_is_never_retried(self):
        state = self.investigated("PAUSED_BUILDER_RETRY_LIMIT")
        stuck.apply(state, report(), {}, "/ws")
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", state["status"])
        self.assertIn("Investigator (paused)", state["stop_reason"])

    def test_reports_the_runner_cannot_act_on_are_rejected(self):
        for name, value, changed in (("wrote to the workspace", report(), ["billing/tax.py"]),
                                     ("found nothing", report(diagnosis=" "), []),
                                     ("retry without guidance", report(guidance=""), []),
                                     ("asks the user nothing", report("pause", user_question=""), [])):
            with self.subTest(name):
                state = self.investigated("PAUSED_REPEATED_FAILURE")
                with self.assertRaises(ValueError):
                    stuck.apply(state, value, {"changed_files": changed}, "/ws")

    def test_a_failed_investigation_restores_the_original_pause(self):
        state = self.investigated("PAUSED_INVALID_OUTPUT", pending_report_repair={"attempts": 2})
        status, reason = stuck.abandon(state, "provider timed out")
        self.assertEqual(("PAUSED_INVALID_OUTPUT", "terra"), (status, state["next_stage"]))
        self.assertIn("could not finish: provider timed out", reason)
        self.assertEqual({"attempts": 2}, state["pending_report_repair"])
        self.assertEqual("investigation_failed", state["stuck_investigations"][0]["outcome"])


class GuidanceTests(unittest.TestCase):
    def request(self):
        return common.ModelRequest("glm", "glm", "Do the stage.\nCURRENT HANDOFF DATA\n{}", {}, {}, False)

    def in_force(self, stage):
        state = state_for(next_stage=stage)
        stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "x")
        stuck.apply(state, report(), {}, "/ws")
        return state

    def test_guidance_reaches_the_stuck_stage_before_its_handoff_data(self):
        state = self.in_force("terra")
        prompt = stuck.with_guidance(state, "terra", self.request()).prompt
        self.assertIn("INVESTIGATOR GUIDANCE", prompt)
        self.assertLess(prompt.index("Cite docs/bugs/cent-drift.json exactly"), prompt.index("CURRENT HANDOFF DATA"))
        self.assertNotIn("INVESTIGATOR GUIDANCE", stuck.with_guidance(state, "sol", self.request()).prompt)

    def test_execution_guidance_is_spent_when_its_stage_completes(self):
        state = self.in_force("terra")
        stuck.settle(state, "sol")
        self.assertIn("stuck_investigation", state)
        stuck.settle(state, "terra")
        self.assertNotIn("stuck_investigation", state)

    def test_planning_guidance_covers_the_whole_planning_cycle_until_the_run_stops(self):
        state = self.in_force("astra_challenge")
        for stage in ("glm_revise", "astra_finalize", "astra_discovery"):
            self.assertIn("INVESTIGATOR GUIDANCE", stuck.with_guidance(state, stage, self.request()).prompt, stage)
            stuck.settle(state, stage)
        self.assertNotIn("INVESTIGATOR GUIDANCE", stuck.with_guidance(state, "terra", self.request()).prompt)
        stuck.retire(state)
        self.assertNotIn("stuck_investigation", state)

    def test_autopilot_prepares_every_stage_through_the_guidance(self):
        state = self.in_force("astra_discovery")
        original = autopilot.unit_module
        autopilot.unit_module = lambda stage: type("Unit", (), {"prepare": staticmethod(lambda *a: self.request())})
        try:
            prompt = autopilot.prepare_request(state, "glm_revise", Path("/run/state.json"), None).prompt
        finally:
            autopilot.unit_module = original
        self.assertIn("INVESTIGATOR GUIDANCE", prompt)


class RouteTests(unittest.TestCase):
    def test_default_models_never_astra_and_never_the_stuck_stages_model(self):
        roles = state_for()["settings"]["roles"]
        pick = lambda roles, stuck_model, provider=None: tuple(
            stuck.route(roles, stuck_model, provider)[k] for k in ("model", "reasoning_effort"))
        # kilocode runs reach Claude through the Kilo Gateway.
        self.assertEqual((stuck.CLAUDE, "high"), pick(roles, "zai-coding-plan/glm-5.3", "kilocode"))
        self.assertEqual(("openai/gpt-6-sol", "high"), pick(roles, stuck.CLAUDE, "kilocode"))
        # OpenCode runs: Sol, or GLM when the stuck stage runs on Sol.
        self.assertEqual(("openai/gpt-6-sol", "high"), pick(roles, "zai-coding-plan/glm-5.3"))
        self.assertEqual(("zai-coding-plan/glm-5.3", "high"), pick(roles, "openai/gpt-6-sol"))
        self.assertEqual("opencode", stuck.route(roles, "x")["engine"])
        # Native Codex runs have GPT models only: Sol, or Luna when the stuck stage runs on Sol.
        codex = {"plan_reviewer": {"model": "gpt-6-astra", "engine": "codex"}}
        self.assertEqual(("gpt-6-sol", "high"), pick(codex, "gpt-6-astra"))
        self.assertEqual(("gpt-6-luna", "high"), pick(codex, "gpt-6-sol"))
        # A custom provider keeps its reviewer model.
        self.assertEqual(("custom/model", "high"), pick({"astra": {"model": "custom/model"}}, "x", "mytool"))
        for provider in (None, "opencode", "kilocode"):
            self.assertNotIn("astra", pick(roles, "openai/gpt-6-sol", provider)[0])
        self.assertEqual("high", roles["plan_reviewer"]["reasoning_effort"], "the Plan Reviewer's own route is untouched")

    def test_the_autoresolver_prepares_a_fresh_investigator(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace, next_stage="astra_challenge", sessions={stuck.ROUTE: "old"})
            stuck.intercept(state, "PAUSED_PLANNING_BUDGET", "2/2 plan-review calls used")
            request = autoresolver.prepare(state, stuck.STAGE, Path(workspace) / "state.json", None)
        self.assertEqual(("astra", stuck.ROUTE, False, stuck.SCHEMA),
                         (request.role, request.route_role, request.allow_write, request.schema))
        self.assertEqual("openai/gpt-6-sol", state["settings"]["roles"][stuck.ROUTE]["model"])
        self.assertNotIn(stuck.ROUTE, state["sessions"])
        self.assertIn('"status": "PAUSED_PLANNING_BUDGET"', request.prompt)
        self.assertIn(str(Path(workspace) / "state.json"), request.prompt)
        self.assertEqual("autoresolver", autopilot.unit_for(stuck.STAGE))
        self.assertIn(stuck.STAGE, jobs.STAGES)


class DriveTests(unittest.TestCase):
    """The loop end to end with a scripted stage and investigator."""

    def run_loop(self, state, script, investigate=True):
        calls = []

        def dispatch(current, stage):
            calls.append(stage)
            action = script[stage].pop(0)
            return action(current) if callable(action) else action

        def apply(current, stage, outcome):
            if stage == stuck.STAGE:
                return stuck.apply(current, outcome, {}, "/ws")
            current.update(next_stage=None, status="TASK_COMPLETE")

        def fail(status):
            def raise_(current):
                current["stages"].append({"stage": current["next_stage"], "rejected": True})
                raise Paused(status, f"{status} at {current['next_stage']}")
            return raise_

        for stage, actions in script.items():
            script[stage] = [fail(a[1:]) if isinstance(a, str) and a.startswith("!") else a for a in actions]
        finished = stuck.drive(state, dispatch, apply=apply, persist=lambda _: None,
                                      active=lambda s: s["status"] == "RUNNING", skip=object(), paused=Paused,
                                      investigate=investigate)
        return finished, calls

    def test_a_stuck_stage_is_investigated_retried_and_finishes(self):
        state = state_for()
        done, calls = self.run_loop(state, {"terra": ["!PAUSED_REPEATED_FAILURE", "built"],
                                            stuck.STAGE: [report()]})
        self.assertEqual(["terra", stuck.STAGE, "terra"], calls)
        self.assertEqual("TASK_COMPLETE", done["status"])

    def test_the_same_problem_twice_pauses_with_the_diagnosis(self):
        state = state_for()
        with self.assertRaises(Paused) as caught:
            self.run_loop(state, {"terra": ["!PAUSED_REPEATED_FAILURE", "!PAUSED_REPEATED_FAILURE"],
                                  stuck.STAGE: [report()]})
        self.assertEqual("PAUSED_REPEATED_FAILURE", caught.exception.status)
        self.assertIn("Investigator (retried): The Planner cites", str(caught.exception))

    def test_an_investigation_that_fails_restores_the_original_pause(self):
        state = state_for()
        with self.assertRaises(Paused) as caught:
            self.run_loop(state, {"terra": ["!PAUSED_REPEATED_FAILURE"], stuck.STAGE: ["!PAUSED_INVALID_OUTPUT"]})
        self.assertEqual("PAUSED_REPEATED_FAILURE", caught.exception.status)
        self.assertIn("could not finish", str(caught.exception))
        self.assertEqual(("PAUSED_REPEATED_FAILURE", "terra"), (state["status"], state["next_stage"]))

    def test_a_pause_set_on_the_state_is_investigated_too(self):
        state = state_for(next_stage="astra_review")
        calls = []

        def dispatch(current, stage):
            calls.append(stage)
            if stage == stuck.STAGE:
                return report()
            current["stages"].append({"stage": stage})
            if calls.count("astra_review") == 1:
                current.update(status="PAUSED_COMPLETION_REVIEW", stop_reason="All required criteria already pass")
                return "paused"
            current.update(status="TASK_COMPLETE", next_stage=None)
            return "done"

        def apply(current, stage, outcome):
            if stage == stuck.STAGE:
                stuck.apply(current, outcome, {}, "/ws")

        stuck.drive(state, dispatch, apply=apply, active=lambda s: s["status"] == "RUNNING", skip=object(),
                    paused=Paused, investigate=True)
        self.assertEqual(["astra_review", stuck.STAGE, "astra_review"], calls)
        self.assertEqual("TASK_COMPLETE", state["status"])

    def test_a_pause_reasserted_on_resume_launches_nothing(self):
        # A guard re-raising a known pause before any stage runs is not a new failure to converge.
        state = state_for(status="RUNNING")
        calls = []

        def dispatch(current, stage):
            calls.append(stage)
            raise Paused("PAUSED_BUILDER_RETRY_LIMIT", "Builder lane paused")

        with self.assertRaises(Paused) as caught:
            stuck.drive(state, dispatch, active=lambda s: s["status"] == "RUNNING", skip=object(), paused=Paused,
                        investigate=True)
        self.assertEqual((["terra"], "Builder lane paused"), (calls, str(caught.exception)))
        self.assertNotIn("stuck_investigation", state)

    def test_a_runner_owned_receipt_is_not_a_fresh_attempt(self):
        state = state_for(status="RUNNING")

        def dispatch(current, stage):
            current["stages"].append({"stage": "resolver", "runner_owned": True})
            raise Paused("PAUSED_REPEATED_FAILURE", "known failure re-asserted")

        with self.assertRaises(Paused):
            stuck.drive(state, dispatch, active=lambda s: s["status"] == "RUNNING", skip=object(), paused=Paused,
                        investigate=True)
        self.assertNotIn("stuck_investigation", state)

    def test_an_uncertain_investigation_pauses_like_any_stage(self):
        state = state_for()

        def dispatch(current, stage):
            if stage == stuck.STAGE:
                current["active_stage"] = {"stage": stuck.STAGE}
                raise Paused("PAUSED_UNCERTAIN_STAGE", "provider result unknown")
            current["stages"].append({"stage": stage})
            raise Paused("PAUSED_REPEATED_FAILURE", "rejected again")

        with self.assertRaises(Paused) as caught:
            stuck.drive(state, dispatch, active=lambda s: s["status"] == "RUNNING", skip=object(), paused=Paused,
                        investigate=True)
        self.assertEqual("PAUSED_UNCERTAIN_STAGE", caught.exception.status)
        self.assertEqual(stuck.STAGE, state["next_stage"], "the investigation reruns after reconciliation")
        self.assertIn("stuck_investigation", state)

    def test_without_investigate_the_loop_is_unchanged(self):
        state = state_for()
        with self.assertRaises(Paused) as caught:
            self.run_loop(state, {"terra": ["!PAUSED_REPEATED_FAILURE"]}, investigate=False)
        self.assertEqual("PAUSED_REPEATED_FAILURE at terra", str(caught.exception))
        self.assertNotIn("stuck_investigations", state)


if __name__ == "__main__":
    unittest.main()
