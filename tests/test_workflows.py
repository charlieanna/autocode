"""Workflow recognition: the first stage of a new run and the `workflow` view field."""
import unittest
from unittest import mock

import autocode_run_view as run_view
import autocode_workflows as workflows
from units import autoplanner


def fresh(joint=True, task="Review pr-184.patch before I merge it."):
    roles = {"astra": {"model": "a"}, "terra": {"model": "t"}, "sol": {"model": "s"}}
    if joint:
        roles.update(requirements={"model": "r"}, glm={"model": "g"}, plan_reviewer={"model": "p"})
    return {"version": 3, "task": task, "workspace": "/nowhere", "status": "RUNNING", "stages": [],
            "settings": {"joint_planning": joint, "roles": roles}}


class ModuleTests(unittest.TestCase):
    def test_begin_makes_recognition_the_first_stage_and_remembers_what_follows(self):
        state = fresh()
        workflows.begin(state, "requirements_gather")
        self.assertEqual(workflows.STAGE, state["next_stage"])
        self.assertIsNone(workflows.kind(state))
        self.assertIsNone(run_view.view(state)["workflow"])

    def test_apply_saves_the_kind_and_hands_over(self):
        state = fresh()
        workflows.begin(state, "requirements_gather")
        workflows.apply(state, {"workflow": "build", "reason": "asks for a feature", "signals": ["add"]},
                        {"output": "/run/recognize_workflow-01.json"})
        self.assertEqual("build", workflows.kind(state))
        self.assertEqual("build", run_view.view(state)["workflow"])
        # A build starts with the build pipeline's own entry stage.
        self.assertEqual("requirements_gather", state["next_stage"])
        self.assertEqual("model", state["workflow"]["source"])

    def test_review_bugfix_and_design_get_their_own_first_stage_and_keep_the_build_entry(self):
        for kind, stage in (("review", workflows.REVIEW_STAGE), ("bugfix", workflows.INVESTIGATE_STAGE),
                            ("design", workflows.DESIGN_STAGE), ("discuss", workflows.DISCUSS_STAGE)):
            state = fresh()
            workflows.begin(state, "requirements_gather")
            workflows.apply(state, {"workflow": kind, "reason": "", "signals": []}, {})
            self.assertEqual(stage, state["next_stage"], kind)
            self.assertEqual("requirements_gather", state["workflow"]["then"], kind)

    def test_pin_names_the_kind_before_recognition_and_refuses_after(self):
        state = fresh()
        workflows.begin(state, "requirements_gather")
        workflows.pin(state, "review")
        self.assertEqual(("review", "user", workflows.REVIEW_STAGE),
                         (workflows.kind(state), state["workflow"]["source"], state["next_stage"]))
        self.assertEqual("requirements_gather", state["workflow"]["then"])
        view = run_view.view(state)
        self.assertEqual(("review", "user"), (view["workflow"], view["workflow_source"]))
        with self.assertRaisesRegex(ValueError, "already runs as 'review'.*--workflow bugfix"):
            workflows.pin(state, "bugfix")
        with self.assertRaisesRegex(ValueError, "Unknown workflow"):
            workflows.pin(fresh(), "refactor")
        # A run from before recognition existed has no recognizer stage to skip.
        with self.assertRaisesRegex(ValueError, "predates"):
            workflows.pin({**fresh(), "next_stage": "astra_discovery"}, "build")

    def test_the_plan_for_approval_states_the_job_kind_and_how_to_correct_it(self):
        self.assertEqual("", workflows.approval_note({}))
        recognized = {"workflow": {"kind": "discuss", "reason": "A tradeoff question. ", "source": "model"}}
        note = workflows.approval_note(recognized)
        self.assertIn("Job kind: discuss (recognized: A tradeoff question)", note)
        self.assertIn("Do not approve; start a new run with --workflow build|bugfix|review|design|discuss", note)
        named = {"workflow": {"kind": "review", "reason": "Named by the user with --workflow", "source": "user"}}
        self.assertIn("Job kind: review (named by you with --workflow)", workflows.approval_note(named))

    def test_describe_reports_the_recognition_and_how_to_override_it(self):
        state = fresh()
        workflows.begin(state, "requirements_gather")
        self.assertEqual("", workflows.describe(state, "astra_discovery"))
        workflows.apply(state, {"workflow": "design", "reason": "asks for a design, nothing built.",
                                "signals": ["design how", "don't implement"]}, {})
        line = workflows.describe(state, workflows.STAGE)
        self.assertIn("Workflow: design. asks for a design, nothing built.", line)
        self.assertIn("Signals: design how, don't implement.", line)
        self.assertIn("--workflow build|bugfix|review|design|discuss", line)
        self.assertEqual(("model", "asks for a design, nothing built."),
                         (run_view.view(state)["workflow_source"], run_view.view(state)["workflow_reason"]))

    def test_apply_rejects_an_unknown_kind(self):
        state = fresh()
        workflows.begin(state, "astra_discovery")
        with self.assertRaisesRegex(ValueError, "Unknown workflow"):
            workflows.apply(state, {"workflow": "refactor", "reason": "", "signals": []}, {})

    def test_schema_names_exactly_the_five_workflows(self):
        self.assertEqual(list(workflows.WORKFLOWS), workflows.SCHEMA["properties"]["workflow"]["enum"])
        self.assertEqual(set(workflows.WORKFLOWS), set(workflows.DESCRIPTIONS))

    def test_prompt_carries_the_request_and_the_handoff_marker(self):
        text, metrics = workflows.prompt(fresh(), {"files": ["regclient/client.py"]})
        self.assertIn("CURRENT HANDOFF DATA\n", text)
        self.assertIn("Review pr-184.patch", text)
        self.assertIn("regclient/client.py", text)
        self.assertGreater(metrics["estimated_prompt_tokens"], 0)


class FollowUpContextTests(unittest.TestCase):
    """What the recognizer is told about the turn a follow-up continues (autocode_follow_up)."""

    def followed(self, previous):
        state = fresh(task="Build it.")
        state["turns"] = [{"at": "t1", "say": "Build it.", "stage_index": 0, "event_id": "feedback-1",
                           "previous": {"task": "Shared it is; design it.", "workflow": "design", **previous}}]
        workflows.begin(state, "requirements_gather")
        return state

    def test_a_follow_up_to_a_design_turn_carries_what_that_turn_produced(self):
        design = {"mode": "propose", "documents": ["docs/design/cache.md"]}
        context = workflows.packet(self.followed({"design": design}))["follow_up"]
        self.assertEqual(("Build it.", "design", design),
                         (context["message"], context["previous_workflow"], context["previous_design"]))
        self.assertNotIn("previous_design", workflows.packet(self.followed({}))["follow_up"])
        # The recognizer is told that building it is a build of that one document.
        self.assertIn("build, with design_document set to the one document in follow_up.previous_design.documents",
                      " ".join(workflows.PROMPT.split()))


class PlannerUnitTests(unittest.TestCase):
    def test_recognition_is_a_read_only_planning_stage_on_every_run(self):
        self.assertTrue(autoplanner.is_planning(fresh(joint=True), autoplanner.RECOGNIZE))
        self.assertTrue(autoplanner.is_planning(fresh(joint=False), autoplanner.RECOGNIZE))
        self.assertFalse(autoplanner.is_planning(fresh(joint=False), "requirements_gather"))

    def test_route_is_requirements_when_joint_else_plan_reviewer(self):
        self.assertEqual("requirements", autoplanner.role_for(fresh(joint=True), autoplanner.RECOGNIZE))
        self.assertEqual("requirements", autoplanner.route_for(fresh(joint=True), autoplanner.RECOGNIZE))
        self.assertEqual("astra", autoplanner.role_for(fresh(joint=False), autoplanner.RECOGNIZE))
        self.assertEqual("astra", autoplanner.route_for(fresh(joint=False), autoplanner.RECOGNIZE))

    def test_prepare_builds_a_read_only_request_with_the_recognition_schema(self):
        state = fresh()
        request = autoplanner.prepare(state, autoplanner.RECOGNIZE, "/run/state.json", None)
        self.assertFalse(request.allow_write)
        self.assertEqual(workflows.SCHEMA, request.schema)
        self.assertEqual("requirements", request.route_role)
        self.assertIn("Review pr-184.patch", request.prompt)
        self.assertEqual("DISCOVERING", state["phase"])

    def test_recognize_applies_and_keeps_the_run_running(self):
        state = fresh()
        workflows.begin(state, "requirements_gather")
        autoplanner.recognize(state, {"workflow": "build", "reason": "r", "signals": []}, {"output": "o"})
        self.assertEqual("build", run_view.view(state)["workflow"])
        self.assertEqual(("RUNNING", "requirements_gather"), (state["status"], state["next_stage"]))


if __name__ == "__main__":
    unittest.main()


class JobRouteTests(unittest.TestCase):
    """run_role launches on route_for(); every job stage must resolve to the route its unit prepared,
    or it silently takes the Plan Reviewer route's engine, effort and saved session."""

    def test_each_job_stage_resolves_to_its_own_route_when_prepared(self):
        roles = {"astra": {"model": "a"}, "plan_reviewer": {"model": "p"}}
        for stage, route in autoplanner.JOB_ROUTES.items():
            with self.subTest(stage=stage):
                state = {"settings": {"roles": {**roles, route: {"model": "m", "reasoning_effort": "xhigh"}}}}
                self.assertEqual(route, autoplanner.route_for(state, stage, "astra"))
                # Before its unit has prepared the route, the stage keeps the old behaviour.
                self.assertEqual("astra", autoplanner.route_for({"settings": {"roles": roles}}, stage, "astra"))

    def test_the_stuck_investigator_launches_on_its_prepared_route(self):
        import tempfile
        from pathlib import Path
        import autocode_stuck_job as stuck
        from units import autoresolver
        with tempfile.TemporaryDirectory() as workspace:
            state = {"version": 3, "task": "t", "workspace": workspace, "status": "RUNNING", "next_stage": "terra",
                     "stages": [], "phase": "EXECUTING",
                     "settings": {"stuck_investigation": {"route": {"model": "openai/gpt-6-astra", "engine": "opencode",
                                                                    "reasoning_effort": "xhigh", "provider": None}},
                                  "roles": {"astra": {"model": "gpt-6-astra", "engine": "codex"},
                                            "terra": {"model": "gpt-6-sol"}}}}
            stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "x")
            # Hermetic: no OpenCode install needed; its local settings are what gets recorded.
            tool = type("Tool", (), {"local_settings": staticmethod(lambda workspace: {"fixture": "opencode"})})
            with mock.patch("autocode_providers.resolve", return_value=tool):
                request = autoresolver.prepare(state, stuck.STAGE, Path(workspace) / "state.json", None)
        self.assertEqual(request.route_role, autoplanner.route_for(state, stuck.STAGE, request.role))
        self.assertEqual("opencode", autoplanner.engine_for(state["settings"], request.route_role))
        # A Codex run's first OpenCode stage records that transport, so the drift and billing checks cover it.
        self.assertEqual({"fixture": "opencode"}, state["settings"]["transport_identities"]["opencode"])
