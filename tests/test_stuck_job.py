"""A stage that stops converging goes to an Investigator before the run pauses for the user."""

import copy
import json
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_builder_failure as builder_failure
import autocode_investigation_policy as investigation_policy
import autocode_jobs as jobs
import autocode_planning_advice as recovery_binding
import autocode_recovery_accounting as recovery_accounting
import autocode_recovery_limits as runner_limits
import autocode_stuck_job as stuck
import autocode_util as util
import autopilot
from units import autoresolver, common


class Paused(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def state_for(workspace=None, next_stage="builder", status="RUNNING", **extra):
    if workspace is None:  # a real directory holding the fixture report's cited evidence file
        workspace = Path(tempfile.mkdtemp(prefix="stuck-ws-"))
        (workspace / "docs" / "bugs").mkdir(parents=True)
        (workspace / "docs" / "bugs" / "cent-drift.json").write_text("{}\n")
        workspace = str(workspace)
    state = {
        "version": 3,
        "task": "Fix the rounding drift",
        "workspace": workspace,
        "status": status,
        "phase": "EXECUTING",
        "next_stage": next_stage,
        "stages": [],
        "iteration": 1,
        # An OpenCode run records its transport at creation (autocode.py configure).
        "settings": {
            "limits": {"no_progress_batches": 3},
            "transport_identities": {"opencode": {"fixture": True}},
            "roles": {
                "task_planner": {"model": "openai/gpt-6-astra", "engine": "opencode"},
                "plan_reviewer": {"model": "openai/gpt-6-astra", "engine": "opencode", "reasoning_effort": "high"},
                "planner": {"model": "zai-coding-plan/glm-5.3"},
                "builder": {"model": "openai/gpt-6-sol"},
                "tester": {"model": "zai-coding-plan/glm-5.3"},
            },
        },
    }
    state.update(extra)
    return state


def report(recommendation="retry", **overrides):
    value = {
        "diagnosis": "The Planner cites the diagnosis with prose after its path.",
        "cause": "stage_output",
        "guidance": "Cite docs/bugs/cent-drift.json exactly; put the explanation in the summary.",
        "recommendation": recommendation,
        "user_question": "",
        "evidence_refs": ["docs/bugs/cent-drift.json"],
        "example": "Given code_refs 'docs/bugs/cent-drift.json (see the note)'; when the runner checked the "
        "path; then it rejected it as missing",
        "probe": "",
        "untestable": "The fixture reads no saved attempt; the cause is stated, not shown",
    }
    if recommendation == "pause":
        value.update(
            guidance="",
            cause="needs_user",
            user_question="May the fix change the public API?",
            example="",
            untestable="",
        )
    value.update(overrides)
    return value


class PromptHandoffTests(unittest.TestCase):
    """C1 (#2113): the Investigator handoff renders as one compact line and round-trips its packet exactly."""

    def test_prompt_names_every_required_report_field(self):
        # A live MiMo Investigator put the cause inside diagnosis because the prompt described the
        # six causes but never named the field; its report was rejected for "$: missing cause".
        # untestable is defined inside the probe entry ("say why in untestable").
        named = {*(f for f in stuck.SCHEMA["required"] if f"- {f}:" in stuck.PROMPT), "untestable"}
        unnamed = [field for field in stuck.SCHEMA["required"] if field not in named]
        self.assertIn("say why in untestable", stuck.PROMPT)
        self.assertEqual(unnamed, [])

    def state(self, mode="ordinary"):
        state = state_for()
        request = {
            "stage": "builder",
            "status": "PAUSED_REPEATED_FAILURE",
            "reason": "rejected three times",
            "identity": "fixed-identity",
            "mode": mode,
        }
        if mode == "builder_failure":
            request["failure_evidence"] = {"failure_id": "F1", "evidence_refs": ["run/failure.json"]}
        state["stuck_investigation"] = request
        return state

    def check_compact_round_trip(self, mode):
        state = self.state(mode)
        text, metrics = stuck.prompt(state, "/run/state.json", engine="opencode")
        _, handoff = text.split("\nCURRENT HANDOFF DATA\n", 1)
        self.assertNotIn("\n", handoff, "the Investigator handoff must be one compact line (C1)")
        self.assertEqual(
            stuck.packet(state, "/run/state.json", engine="opencode"),
            json.loads(handoff),
        )
        self.assertEqual((len(text.encode()) + 3) // 4, metrics["estimated_prompt_tokens"])

    def test_the_ordinary_handoff_is_compact_and_round_trips(self):
        self.check_compact_round_trip("ordinary")

    def test_the_builder_failure_handoff_is_compact_and_round_trips(self):
        self.check_compact_round_trip("builder_failure")


class InterceptTests(unittest.TestCase):
    def test_a_non_convergence_pause_goes_to_the_investigator(self):
        state = state_for(
            status="PAUSED_REPEATED_FAILURE",
            stop_reason="rejected",
            paused_at="t",
            pending_report_repair={"attempts": 2},
        )
        self.assertTrue(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "rejected three times"))
        self.assertEqual(
            ("RUNNING", "INVESTIGATING", stuck.STAGE), (state["status"], state["phase"], state["next_stage"])
        )
        self.assertNotIn("stop_reason", state)
        self.assertNotIn("pending_report_repair", state)  # set aside, or the before-hook replays it first
        request = state["stuck_investigation"]
        self.assertEqual(
            ("builder", "PAUSED_REPEATED_FAILURE", {"attempts": 2}),
            (request["stage"], request["status"], request["pending_report_repair"]),
        )
        self.assertEqual("investigating", state["stuck_investigations"][0]["outcome"])

    def test_user_owned_and_unsafe_pauses_are_left_alone(self):
        for status, extra in (
            ("PAUSED_BUDGET", {}),
            ("PAUSED_CRITERIA_CHANGE", {}),
            ("PAUSED_ITERATION_LIMIT", {}),
            ("PAUSED_UNCERTAIN_STAGE", {}),
            ("WAITING_FOR_USER", {}),
            ("PAUSED_REPEATED_FAILURE", {"active_stage": {"stage": "builder"}}),
            ("PAUSED_REPEATED_FAILURE", {"version": 2}),
            ("PAUSED_REPEATED_FAILURE", {"next_stage": stuck.STAGE}),
            ("PAUSED_REPEATED_FAILURE", {"next_stage": "diagnose"}),
            ("PAUSED_REPEATED_FAILURE", {"next_stage": "resolve"}),
            ("PAUSED_REPEATED_FAILURE", {"settings": {"stuck_investigation": {"max_calls_per_run": 0}}}),
        ):
            with self.subTest(status=status, extra=extra):
                state = state_for(**extra)
                before = dict(state)
                self.assertFalse(stuck.intercept(state, status, "why"))
                self.assertEqual(before, state)

    def test_an_operator_authorized_retry_goes_back_to_the_operator(self):
        state = state_for(
            stages=[{"stage": "builder", "failure_key": "k1"}],
            failure_retry_authorizations=[{"failure_key": "k1", "consumed": True}],
        )
        before = copy.deepcopy(state)
        self.assertFalse(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "failed again after --retry-failed-stage"))
        self.assertEqual(before, state, "declining never touches the state")

    def test_one_investigation_per_problem_and_a_run_budget(self):
        state = state_for()
        self.assertTrue(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "x"))
        state.update(next_stage="builder", status="RUNNING")
        self.assertFalse(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "x again"))
        for stage in ("tester", "review_completion"):
            state["next_stage"] = stage
            self.assertTrue(stuck.intercept(state, "PAUSED_INVALID_OUTPUT", "x"))
        state["next_stage"] = "review_plan"
        self.assertFalse(stuck.intercept(state, "PAUSED_PLANNING_BUDGET", "x"), "three per run by default")

    def test_the_same_problem_on_changed_source_is_a_new_problem(self):
        state = state_for()
        state["stages"] = [{"stage": "tester", "source_revision": "rev-a", "exit_code": -15}]
        state["next_stage"] = "tester"
        self.assertTrue(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "stalled twice"))
        self.assertEqual("rev-a", state["stuck_investigations"][-1]["source_revision"])
        state.update(next_stage="tester", status="RUNNING")
        self.assertFalse(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "stalled again"), "same source: once")
        state["stages"].append({"stage": "tester", "source_revision": "rev-b", "exit_code": -15})
        self.assertTrue(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "stalled on the Builder's new source"))
        # An entry saved before revisions were recorded still matches its problem on any source.
        legacy = state_for()
        legacy["stuck_investigations"] = [{"identity": "tester:PAUSED_INVALID_OUTPUT", "outcome": "retried"}]
        legacy["stages"] = [{"stage": "tester", "source_revision": "rev-c"}]
        legacy["next_stage"] = "tester"
        self.assertFalse(stuck.intercept(legacy, "PAUSED_INVALID_OUTPUT", "rejected again"))

    def test_a_plan_with_more_milestones_than_the_budget_gets_one_investigation_each(self):
        state = state_for()
        self.assertEqual(stuck.MAX_CALLS, investigation_policy.max_calls(state))
        state["goal_contract"] = {"body": {"milestones": [{"id": f"M{n}"} for n in range(1, 8)]}}
        self.assertEqual(7, investigation_policy.max_calls(state))
        for n, stage in enumerate(("review_plan", "review_completion", "tester", "builder")):
            state.update(next_stage=stage, status="RUNNING")
            self.assertTrue(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", f"stuck {n}"), "beyond three")
        state["settings"]["stuck_investigation"] = {"max_calls_per_run": 4}
        state.update(next_stage="finalize_plan", status="RUNNING")
        self.assertFalse(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "x"), "an explicit budget wins")


class BuilderFailureClassificationTests(unittest.TestCase):
    def test_changed_line_investigation_does_not_offer_a_person_question(self):
        state = {
            "stuck_investigation": {
                "mode": "builder_failure",
                "failure_evidence": {"diagnosis": {"changed_code_evidence": {"status": "UNVERIFIED"}}},
            }
        }
        fields = stuck.schema(state)["properties"]
        self.assertNotIn("needs_user", fields["cause"]["enum"])
        self.assertEqual([""], fields["user_question"]["enum"])
        self.assertIn("needs_user", stuck.SCHEMA["properties"]["cause"]["enum"])

    def fixture(self, root):
        workspace = Path(root) / "workspace"
        workspace.mkdir()
        output = Path(root) / "builder.json"
        output.write_text("{}\n")
        state = state_for(
            str(workspace),
            current_task={"id": "T1"},
            goal_contract={"hash": "C1", "revision": 1},
            builder_retries={"existing": {"failures": ["earlier"]}},
            no_progress_batches=2,
        )
        snapshot = patch("autocode_builder_failure.source_scope.snapshot", return_value={"revision": "source-1"})
        snapshot.start()
        self.addCleanup(snapshot.stop)
        record = {"output": str(output), "source_revision": "source-1"}
        return state, builder_failure.evidence(state, record)

    def test_requested_pause_retains_the_queued_classification_without_spending_it(self):
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            builder_failure.queue(state, evidence, "Empty diff", enabled=True)
            request = copy.deepcopy(state["stuck_investigation"])
            history = copy.deepcopy(state["stuck_investigations"])
            calls = []

            def pause(current):
                raise Paused("PAUSED_REQUESTED", "Requested saved boundary")

            with self.assertRaises(Paused) as caught:
                stuck.drive(
                    state,
                    lambda current, stage: calls.append(stage),
                    before=pause,
                    active=lambda current: current["status"] == "RUNNING",
                    skip=object(),
                    paused=Paused,
                    investigate=True,
                )
            self.assertEqual("PAUSED_REQUESTED", caught.exception.status)
            self.assertEqual("Requested saved boundary", str(caught.exception))
            self.assertEqual([], calls)
            self.assertEqual(request, state["stuck_investigation"])
            self.assertEqual(history, state["stuck_investigations"])
            self.assertNotIn("builder_failure_hold", state)
            self.assertEqual(["earlier"], state["builder_retries"]["existing"]["failures"])

    def test_pre_escalation_report_is_mode_specific_and_bound_to_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            self.assertTrue(builder_failure.queue(state, evidence, "Empty diff", enabled=True))
            request = autoresolver.prepare_stuck(state, Path(root) / "state.json")
            self.assertFalse(request.allow_write)
            self.assertIn("failure_class", request.schema["required"])
            self.assertNotIn("failure_class", stuck.SCHEMA["properties"])
            value = report(
                failure_class="execution", failure_id=evidence["failure_id"], evidence_refs=evidence["evidence_refs"]
            )
            result = stuck.apply(state, value, {"output": str(Path(root) / "diagnosis.json")}, state["workspace"])
            self.assertEqual(evidence["failure_id"], result["diagnosis"]["failure_id"])
            self.assertEqual(2, state["no_progress_batches"])
            self.assertEqual(["earlier"], state["builder_retries"]["existing"]["failures"])
            self.assertNotIn("in_force", state.get("stuck_investigation", {}))
            self.assertFalse(builder_failure.queue(state, evidence, "Same failure", enabled=True))

    def test_stale_identity_and_uncited_evidence_cannot_grant_continuation(self):
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            builder_failure.queue(state, evidence, "Empty diff", enabled=True)
            for failure_id, refs in [("other", evidence["evidence_refs"]), (evidence["failure_id"], ["other"])]:
                with self.assertRaises(ValueError):
                    stuck.apply(
                        state,
                        report(failure_class="execution", failure_id=failure_id, evidence_refs=refs),
                        {},
                        state["workspace"],
                    )
            Path(evidence["record"]["output"]).write_text('{"changed": true}\n')
            with self.assertRaises(util.Paused):
                autoresolver.prepare_stuck(state, Path(root) / "state.json")

    def test_the_milestones_classification_limit_refuses_a_new_failure_without_resetting_any_lane(self):
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            milestone = builder_failure.policy.key(state)
            state["stuck_investigations"] = [
                {
                    "identity": f"builder-failure:{n}",
                    "trigger": "builder_failure",
                    "milestone_key": milestone,
                    "outcome": "classified",
                }
                for n in range(3)
            ]
            self.assertFalse(builder_failure.queue(state, evidence, "Unknown", enabled=True))
            self.assertEqual("PAUSED_BUILDER_CLASSIFICATION", state["status"])
            self.assertEqual(["earlier"], state["builder_retries"]["existing"]["failures"])

    def test_typed_operational_cause_retains_its_pause_without_retry_or_reassignment(self):
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            evidence["error_class"] = "PAUSED_PROVIDER_TIMEOUT"
            evidence["diagnosis"] = {
                "failure_id": evidence["failure_id"],
                "failure_class": "execution",
                "evidence_refs": evidence["evidence_refs"],
            }
            before = copy.deepcopy(state["settings"]["roles"])
            reassess = []
            action = builder_failure.route(
                state, evidence, "Provider timed out", enabled=True, reassess=lambda *args: reassess.append(args)
            )
            self.assertEqual("recover", action)
            self.assertEqual("PAUSED_PROVIDER_TIMEOUT", state["status"])
            self.assertEqual(before, state["settings"]["roles"])
            self.assertEqual([], reassess)
            self.assertNotIn("stuck_investigation", state)
            self.assertEqual(["earlier"], state["builder_retries"]["existing"]["failures"])

    def test_valid_execution_diagnosis_needing_user_is_an_operator_hold_not_a_retry(self):
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            builder_failure.queue(state, evidence, "Empty diff", enabled=True)
            value = report(
                "pause",
                failure_class="execution",
                failure_id=evidence["failure_id"],
                evidence_refs=evidence["evidence_refs"],
                example="The assigned approach needs a scope decision",
                untestable="Only the operator can approve the requested scope",
                user_question="May this change scope?",
            )
            result = stuck.apply(state, value, {"output": str(Path(root) / "diagnosis.json")}, state["workspace"])
            self.assertIsNone(result)
            self.assertEqual("PAUSED_BUILDER_CLASSIFICATION", state["status"])
            self.assertIn("May this change scope?", state["stop_reason"])
            self.assertEqual(["earlier"], state["builder_retries"]["existing"]["failures"])

    def test_reconstructing_evidence_through_a_file_alias_cannot_create_a_new_incident(self):
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            alias = Path(root) / "alias.json"
            alias.symlink_to(evidence["record"]["output"])
            rebuilt = builder_failure.evidence(state, {**evidence["record"], "output": str(alias)})
            self.assertEqual(evidence["failure_id"], rebuilt["failure_id"])
            self.assertEqual(evidence["evidence_refs"], rebuilt["evidence_refs"])
            builder_failure.queue(state, evidence, "Empty diff", enabled=True)
            self.assertFalse(builder_failure.queue(state, rebuilt, "Alias replay", enabled=True))

    def test_accepting_current_completed_attempt_defers_admission_until_normal_save(self):
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            completed = {**evidence["record"], "stage": "builder", "exit_code": 0}
            state["active_stage"] = completed
            self.assertFalse(
                builder_failure.queue(state, evidence, "Empty diff", enabled=True, completed_record=completed)
            )
            self.assertNotIn("stuck_investigations", state)
            self.assertIs(completed, state["active_stage"])
            with self.assertRaises(util.Paused):
                builder_failure.finalize(state, completed, enabled=True)

    def test_foreign_active_attempt_and_uncertain_ownership_cannot_defer_or_spend_a_call(self):
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            for extra in (
                {"active_stage": {"output": "foreign.json", "stage": "builder", "exit_code": 0}},
                {"active_stage": evidence["record"], "uncertain_artifacts": ["uncollected"]},
            ):
                held = {**copy.deepcopy(state), **extra}
                self.assertFalse(
                    builder_failure.queue(
                        held,
                        evidence,
                        "Empty diff",
                        enabled=True,
                        completed_record={**evidence["record"], "stage": "builder", "exit_code": 0},
                    )
                )
                self.assertNotIn("pending_builder_failure", held)
                self.assertNotIn("stuck_investigations", held)

    @unittest.skipUnless(sys.platform == "darwin", "native read-only classification probe sandbox")
    def test_classification_probe_cannot_overwrite_read_and_restore_cited_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            cited = Path(root) / "builder.json"
            cited.write_text("original")
            scripts = (
                "from pathlib import Path; assert Path('builder.json').read_text() == 'original'",
                "from pathlib import Path; p=Path('builder.json'); old=p.read_text(); "
                "p.write_text('invented'); assert p.read_text() == 'invented'; p.write_text(old)",
            )
            for index, script in enumerate(scripts):
                command = builder_failure.readonly_probe(shlex.join([sys.executable, "-c", script]))
                receipt = subprocess.run(command, shell=True, cwd=root, capture_output=True, text=True, timeout=10)
                self.assertEqual(index == 0, receipt.returncode == 0, receipt.stderr)
                self.assertEqual("original", cited.read_text())

    def test_probe_without_enforceable_read_only_boundary_fails_closed(self):
        with patch.object(builder_failure.sys, "platform", "unsupported"):
            with self.assertRaisesRegex(ValueError, "containment is unavailable"):
                builder_failure.readonly_probe("true")

    def test_probe_becomes_untestable_when_containment_is_unavailable(self):
        # Fail closed without rejecting the classification: an Investigator may
        # still state an untestable advisory cause (see readonly_probe). CI on
        # Linux has no sandbox-exec; a live model that supplies probe there must
        # not park every builder-failure classification in report repair.
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            builder_failure.queue(state, evidence, "Empty diff", enabled=True)
            value = report(
                failure_class="execution",
                failure_id=evidence["failure_id"],
                evidence_refs=evidence["evidence_refs"],
                probe="python -c 'raise SystemExit(0)'",
                untestable="",
            )
            with patch.object(builder_failure.sys, "platform", "unsupported"):
                stuck.apply(state, value, {"output": str(Path(root) / "diagnosis.json")}, state["workspace"])
            entry = state["stuck_investigations"][-1]
            self.assertEqual("", entry["probe"])
            self.assertIn("containment is unavailable", entry["untestable"])
            self.assertIsNone(entry["probe_result"])
            self.assertEqual("classified", entry["outcome"])

    def test_unchanged_held_binding_refuses_writer_and_diagnostician_without_renewing_authority(self):
        with tempfile.TemporaryDirectory() as root:
            state, evidence = self.fixture(root)
            builder_failure.hold(state, evidence, "PAUSED_BUILDER_CLASSIFICATION", "May this change scope?")
            before = copy.deepcopy(state["builder_retries"])
            for stage in ("builder", "orchestrator", "resolve", "investigate_stuck"):
                with self.assertRaisesRegex(util.Paused, "May this change scope"):
                    builder_failure.dispatch_guard(state, stage, state["workspace"])
            self.assertEqual(before, state["builder_retries"])
            self.assertIn("builder_failure_hold", state)
            state["current_task"] = {**state["current_task"], "id": "genuinely-new-assignment"}
            builder_failure.dispatch_guard(state, "builder", state["workspace"])
            self.assertNotIn("builder_failure_hold", state)
            self.assertEqual(before, state["builder_retries"])

    def test_check_classification_uses_executed_facts_not_model_authored_operational_labels(self):
        checks = [{"command": "check", "exit_code": 1, "evidence_ref": "event:C1", "timed_out": True}]
        event = {"type": "item.completed", "item": {"id": "C1", "type": "command_execution", "exit_code": 1}}
        facts = builder_failure.check_facts(checks, {"events": "executed.jsonl"}, ".", read_events=lambda _: [event])
        self.assertNotIn("timed_out", facts[0])
        event["item"]["timed_out"] = True
        facts = builder_failure.check_facts(
            [{key: value for key, value in checks[0].items() if key != "timed_out"}],
            {"events": "executed.jsonl"},
            ".",
            read_events=lambda _: [event],
        )
        self.assertTrue(facts[0]["timed_out"])


class ApplyTests(unittest.TestCase):
    def investigated(self, status, stage="builder", **extra):
        state = state_for(next_stage=stage, **extra)
        self.assertTrue(stuck.intercept(state, status, "the original reason"))
        return state

    def test_retry_after_repeated_failure_keeps_the_failure_history(self):
        history = {
            "k1": {"identity": {"stage": "builder"}, "count": 3},
            "k2": {"identity": {"stage": "tester"}, "count": 1},
        }
        state = self.investigated(
            "PAUSED_REPEATED_FAILURE",
            pending_report_repair={"attempts": 2},
            failure_history=copy.deepcopy(history),
            stages=[{"stage": "builder", "failure_key": "k1"}, {"stage": "tester", "failure_key": "k2"}],
        )
        stuck.apply(state, report(), {"output": "o"}, state["workspace"])
        # One more attempt, not a fresh failure budget: another failure counts on top of these.
        self.assertEqual(history, state["failure_history"])
        self.assertEqual(["k1", "k2"], [row.get("failure_key") for row in state["stages"]])
        self.assertEqual({"attempts": 2}, state["report_repair_archive"][-1]["repair"])
        self.assertEqual(("RUNNING", "EXECUTING", "builder"), (state["status"], state["phase"], state["next_stage"]))
        self.assertTrue(state["stuck_investigation"]["in_force"])
        self.assertEqual("retried", state["stuck_investigations"][0]["outcome"])

    def test_the_route_exists_only_while_the_investigator_runs(self):
        state = self.investigated("PAUSED_REPEATED_FAILURE")
        state["settings"]["roles"][stuck.ROUTE] = {
            "model": "openai/gpt-6-astra",
            "engine": "opencode",
            "reasoning_effort": "xhigh",
        }
        stuck.apply(state, report(), {"output": "o"}, state["workspace"])
        self.assertNotIn(stuck.ROUTE, state["settings"]["roles"])
        self.assertEqual(
            ("openai/gpt-6-astra", "opencode"),
            (state["stuck_investigations"][0]["model"], state["stuck_investigations"][0]["engine"]),
        )

    def test_pinned_route_from_the_cli(self):
        from types import SimpleNamespace

        settings = {"engine": "codex"}
        self.assertIs(settings, stuck.configure(settings, SimpleNamespace()))
        self.assertNotIn("stuck_investigation", settings)
        stuck.configure(
            settings, SimpleNamespace(investigator_model="openai/gpt-6-sol", investigator_reasoning_effort=None)
        )
        self.assertEqual(
            {"model": "openai/gpt-6-sol", "reasoning_effort": "high", "provider": None, "engine": "opencode"},
            settings["stuck_investigation"]["route"],
        )
        stuck.configure(settings, SimpleNamespace(investigator_model=None, investigator_reasoning_effort="max"))
        self.assertEqual(
            ("openai/gpt-6-sol", "max"),
            (stuck.pinned_route(settings)["model"], stuck.pinned_route(settings)["reasoning_effort"]),
        )
        stuck.configure(settings, SimpleNamespace(investigator_model="gpt-6-astra", investigator_reasoning_effort=None))
        self.assertEqual(
            "codex", settings["stuck_investigation"]["route"]["engine"], "a bare name keeps the run's engine"
        )
        with self.assertRaises(ValueError):
            stuck.configure(
                {"engine": "codex"}, SimpleNamespace(investigator_model=None, investigator_reasoning_effort="high")
            )

    def test_retry_grants_exactly_one_more_review_round_or_batch(self):
        for stage, used, expected in (("review_plan", 2, 4), ("finalize_plan", 2, 3)):
            state = self.investigated(
                "PAUSED_PLANNING_BUDGET", stage=stage, planning={"review_call_limit": 2, "astra_calls": used}
            )
            stuck.apply(state, report(), {}, state["workspace"])
            self.assertEqual((expected, "PLANNING"), (state["planning"]["review_call_limit"], state["phase"]), stage)
        state = self.investigated("PAUSED_NO_PROGRESS", no_progress_batches=3)
        stuck.apply(state, report(), {}, state["workspace"])
        self.assertEqual(2, state["no_progress_batches"])

    def test_investigator_review_round_survives_same_turn_replanning_without_refilling(self):
        from units import autoplanner as planning

        for origin in ("absent", None):
            with self.subTest(origin=origin):
                state = state_for(
                    next_stage="review_plan",
                    planning={
                        "astra_calls": 2,
                        "review_call_limit": 2,
                        "review_depth_reduced": True,
                        "reports": {},
                        "final_token": None,
                    },
                )
                self.addCleanup(shutil.rmtree, state["workspace"])
                state["settings"].update(joint_planning=True, adaptive_planning=True)
                state["workflow"] = {"clarity": "clear", "brief_size": "small"}
                if origin != "absent":
                    state["planning"]["review_call_limit_origin"] = origin
                settings = copy.deepcopy(state["settings"])
                self.assertTrue(stuck.intercept(state, "PAUSED_PLANNING_BUDGET", "2/2 reviews used"))
                stuck.apply(state, report(), {}, state["workspace"])
                planning.charge(state, "review_plan", {"stage": "review_plan", "output": "review-3"})
                planning.new_cycle(state)
                self.assertEqual(4, planning.review_call_limit(state), "Keep the round the core already granted")
                planning.charge(state, "finalize_plan", {"stage": "finalize_plan", "output": "review-4"})
                planning.new_cycle(state)
                with self.assertRaises(planning.s.Paused) as paused:
                    planning.charge(state, "review_plan", {"stage": "review_plan", "output": "review-5"})
                self.assertEqual("PAUSED_PLANNING_BUDGET", paused.exception.status)
                self.assertEqual(4, state["planning"]["astra_calls"])
                self.assertEqual(settings, state["settings"])

    def test_granted_review_round_preserves_an_existing_explicit_origin(self):
        from units import autoplanner as planning

        state = {
            "settings": {},
            "planning": {
                "astra_calls": 2,
                "review_call_limit": 2,
                "review_call_limit_origin": "user_explicit",
                "review_depth_reduced": True,
                "reports": {},
            },
        }
        stuck.grant_one_attempt(state, {"status": "PAUSED_PLANNING_BUDGET", "stage": "review_plan"})
        planning.new_cycle(state, new_turn=True)
        self.assertEqual(4, planning.review_call_limit(state))
        self.assertEqual("user_explicit", state["planning"]["review_call_limit_origin"])
        self.assertEqual(2, state["planning"]["astra_calls"])

    def test_core_review_grant_does_not_replace_the_configured_next_turn_allowance(self):
        from units import autoplanner as planning

        state = {
            "settings": {"planning_review_call_limit": 2, "planning_review_call_limit_origin": "config.toml"},
            "planning": {"astra_calls": 2, "review_call_limit": 2, "review_depth_reduced": True, "reports": {}},
        }
        settings = copy.deepcopy(state["settings"])
        stuck.grant_one_attempt(state, {"status": "PAUSED_PLANNING_BUDGET", "stage": "review_plan"})
        planning.new_cycle(state)
        self.assertEqual(4, planning.review_call_limit(state))
        planning.new_cycle(state, new_turn=True)
        self.assertEqual(2, planning.review_call_limit(state))
        self.assertEqual(0, state["planning"]["astra_calls"])
        self.assertEqual(settings, state["settings"])

    def test_exhausted_timeout_recovery_goes_to_the_investigator_which_grants_one_recovery(self):
        # A live run (2026-11-06) asked a person to --grant-recovery after three stage timeouts.
        self.assertIn("PAUSED_TIMEOUT_RECOVERY", stuck.RETRYABLE)
        state = self.investigated(
            "PAUSED_TIMEOUT_RECOVERY",
            stage="review_completion",
            automatic_recoveries_since_resume=3,
            consecutive_timeout_recoveries=3,
            automatic_timeout_recoveries=[{"stage": "review_completion"}] * 3,
        )
        stuck.apply(state, report(), {}, state["workspace"])
        self.assertEqual(
            (2, 2, "RUNNING", "review_completion"),
            (
                state["automatic_recoveries_since_resume"],
                state["consecutive_timeout_recoveries"],
                state["status"],
                state["next_stage"],
            ),
        )
        self.assertEqual(3, len(state["automatic_timeout_recoveries"]))
        self.assertIsNone(
            runner_limits.stop_reason(
                state, recovery_accounting.spent(state), recovery_accounting.MAX_AUTOMATIC_RECOVERIES
            )
        )
        state.update(automatic_recoveries_since_resume=3, consecutive_timeout_recoveries=3)
        self.assertEqual(
            "PAUSED_TIMEOUT_RECOVERY",
            runner_limits.stop_reason(state, 3, recovery_accounting.MAX_AUTOMATIC_RECOVERIES)[0],
        )

    def test_pause_restores_the_original_pause_with_the_diagnosis(self):
        state = self.investigated("PAUSED_REPEATED_FAILURE", pending_report_repair={"attempts": 2})
        stuck.apply(state, report("pause"), {}, state["workspace"])
        self.assertEqual(
            ("PAUSED_REPEATED_FAILURE", "PAUSED_OR_BLOCKED", "builder"),
            (state["status"], state["phase"], state["next_stage"]),
        )
        self.assertIn("the original reason", state["stop_reason"])
        self.assertIn("Investigator (paused): The Planner cites", state["stop_reason"])
        self.assertIn("Needs you: May the fix change the public API?", state["stop_reason"])
        self.assertEqual({"attempts": 2}, state["pending_report_repair"])
        self.assertNotIn("stuck_investigation", state)

    def test_a_diagnose_only_pause_is_never_retried(self):
        state = self.investigated("PAUSED_BUILDER_RETRY_LIMIT")
        stuck.apply(state, report(), {}, state["workspace"])
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", state["status"])
        self.assertIn("Investigator (paused)", state["stop_reason"])

    def test_reports_the_runner_cannot_act_on_are_rejected(self):
        for name, value, changed in (
            ("wrote to the workspace", report(), ["billing/tax.py"]),
            ("found nothing", report(diagnosis=" "), []),
            ("retry without guidance", report(guidance=""), []),
            ("asks the user nothing", report("pause", user_question=""), []),
        ):
            with self.subTest(name):
                state = self.investigated("PAUSED_REPEATED_FAILURE")
                with self.assertRaises(ValueError):
                    stuck.apply(state, value, {"changed_files": changed}, state["workspace"])

    def test_a_failed_investigation_restores_the_original_pause(self):
        state = self.investigated("PAUSED_INVALID_OUTPUT", pending_report_repair={"attempts": 2})
        status, reason = stuck.abandon(state, "provider timed out")
        self.assertEqual(("PAUSED_INVALID_OUTPUT", "builder"), (status, state["next_stage"]))
        self.assertIn("could not finish: provider timed out", reason)
        self.assertEqual({"attempts": 2}, state["pending_report_repair"])
        self.assertEqual("investigation_failed", state["stuck_investigations"][0]["outcome"])


class GuidanceTests(unittest.TestCase):
    def test_execution_guidance_handles_a_saved_snapshot_path_without_reading_it(self):
        state = state_for(next_stage="tester", source_snapshot="/saved/source-1.json")
        stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "x")
        stuck.apply(state, report(), {}, state["workspace"])
        self.assertIn("INVESTIGATOR GUIDANCE", stuck.with_guidance(state, "tester", self.request()).prompt)
        state["source_snapshot"] = "/saved/source-2.json"
        self.assertEqual(self.request(), stuck.with_guidance(state, "tester", self.request()))

    def test_execution_guidance_is_bound_to_the_task_source_contract_and_user_input(self):
        for change in (
            {"current_task": {"id": "another-task"}},
            {"goal_contract": {"revision": 5, "hash": "new-contract"}},
            {"source_snapshot": {"revision": "changed-source"}},
            {"answers": {"Q1": {"text": "Changed requirement"}}},
            {"brief_feedback": [{"id": "new", "text": "Use the other API"}]},
            {"task": "Another task"},
        ):
            with self.subTest(change=change):
                state = self.in_force("tester")
                state.update(change)
                self.assertEqual(self.request(), stuck.with_guidance(state, "tester", self.request()))
                stuck.retire(state, "PAUSED_INTERVENTION")
                self.assertNotIn("stuck_investigation", state)

    def test_boundary_pause_keeps_advice_without_changing_grants_history_or_limits(self):
        for stage in ("builder", "tester", "review_completion", "draft_plan"):
            with self.subTest(stage=stage):
                state = self.in_force(stage)
                before = copy.deepcopy(state)
                stuck.retire(state, "PAUSED_INTERVENTION")
                self.assertEqual(before, state)
                self.assertIn("INVESTIGATOR GUIDANCE", stuck.with_guidance(state, stage, self.request()).prompt)
                stuck.retire(state, "PAUSED_REPEATED_FAILURE")
                self.assertNotIn("stuck_investigation", state)

    def test_checkpoint_and_budget_holds_keep_the_pending_retry_guidance(self):
        # Live run: --pause-after-stage saved an Investigator report, then the milestone
        # budget stopped the retry. Both holds discarded the very advice the retry needed.
        for pause in (
            "PAUSED_REQUESTED",
            "PAUSED_MILESTONE_BUDGET",
            "PAUSED_PLANNING_BUDGET",
            "PAUSED_TIME_LIMIT",
            "PAUSED_COST_LIMIT",
            "PAUSED_ITERATION_LIMIT",
        ):
            with self.subTest(pause=pause):
                state = self.in_force("builder")
                before = copy.deepcopy(state)

                def hold(current):
                    raise Paused(pause, "No stage launched")

                with self.assertRaises(Paused) as caught:
                    stuck.drive(
                        state,
                        lambda *_: self.fail("A held retry must not launch"),
                        before=hold,
                        active=lambda s: s["status"] == "RUNNING",
                        skip=object(),
                        paused=Paused,
                        investigate=True,
                    )
                self.assertEqual(pause, caught.exception.status)
                self.assertEqual(before, state, "A hold must not renew retry authority or change limits")
                prompt = stuck.with_guidance(state, "builder", self.request()).prompt
                self.assertIn("Cite docs/bugs/cent-drift.json exactly", prompt)
                self.assertLess(prompt.index("INVESTIGATOR GUIDANCE"), prompt.index("CURRENT HANDOFF DATA"))

                state["source_snapshot"] = {"revision": "changed-after-pause"}
                stuck.retire(state, pause)
                self.assertEqual(self.request(), stuck.with_guidance(state, "builder", self.request()))
                self.assertNotIn("stuck_investigation", state)

    def test_replanning_does_not_replay_obsolete_report_instructions_or_diagnoses(self):
        for change in (
            {"goal_contract": {"revision": 5, "hash": "new-contract"}},
            {"answers": {"rewording-1": {"text": "Keep the approved wording"}}},
            {"brief_feedback": [{"id": "new", "text": "Require real joint proof"}]},
            {"planning": {"reports": {"review_plan": {"report": {"concerns": [{"id": "PR-01"}]}}}}},
            {"task": "A different bug"},
        ):
            with self.subTest(change=change):
                state = self.in_force("finalize_plan")
                state.update(change)
                before = copy.deepcopy(state)
                self.assertEqual(self.request(), stuck.with_guidance(state, "finalize_plan", self.request()))
                self.assertEqual(
                    "current rejection", stuck.annotate(state, "PAUSED_REPEATED_FAILURE", "current rejection")
                )
                stuck.retire(state)
                self.assertEqual(self.request(), stuck.with_guidance(state, "finalize_plan", self.request()))
                self.assertEqual(before["stuck_investigations"], state["stuck_investigations"])
                self.assertFalse(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "new rejection"))

    def history(self, **overrides):
        return {
            **recovery_binding.binding({}, "draft_plan"),
            "identity": "draft_plan:PAUSED_INVALID_OUTPUT",
            "stage": "draft_plan",
            "requested_at": "2026-09-30T08:00:00+00:00",
            "outcome": "retried",
            "status": "PAUSED_INVALID_OUTPUT",
            "trigger": "rejected_output",
            "cause": "stage_output",
            "diagnosis": "The Planner cited runner state instead of source.",
            "guidance": "Cite README.md and app/__init__.py, never .autocode/state.json.",
            **overrides,
        }

    def test_report_corrections_reach_a_fresh_request_with_the_same_planning_inputs(self):
        state = {"stuck_investigations": [self.history()], "answers": {"Q1": {"text": "Use the existing API"}}}
        state["stuck_investigations"][0].update(recovery_binding.binding(state, "draft_plan"))
        before = copy.deepcopy(state)
        for stage in ("draft_plan", "revise_plan", "finalize_plan"):
            with self.subTest(stage=stage):
                text = stuck.with_guidance(state, stage, self.request()).prompt
                self.assertIn(self.history()["guidance"], text)
                self.assertLess(text.index(self.history()["guidance"]), text.index("CURRENT HANDOFF DATA"))
        self.assertEqual(before, state, "Remembering a correction must not replenish a retry or budget")
        self.assertEqual(self.request(), stuck.with_guidance(state, "builder", self.request()))

    def test_pauses_and_decision_diagnoses_are_not_reused_as_report_corrections(self):
        for row in (
            self.history(outcome="paused"),
            self.history(cause="needs_user"),
            self.history(cause="environment"),
            self.history(stage="builder"),
            self.history(guidance=""),
            self.history(planning_context=None),
        ):
            with self.subTest(row=row):
                self.assertEqual(
                    self.request(),
                    stuck.with_guidance({"stuck_investigations": [row]}, "draft_plan", self.request()),
                )

    def test_report_lessons_survive_boundaries_that_do_not_reset_investigation_identity(self):
        state = {
            "turns": [{"at": "2026-09-30T09:00:00+00:00"}],
            "clarification_episode": {"started_by": "answer:Q1"},
            "stuck_investigations": [self.history()],
        }
        self.assertIn(self.history()["guidance"], stuck.with_guidance(state, "draft_plan", self.request()).prompt)

    def test_active_guidance_keeps_earlier_lessons_and_wins_on_conflict(self):
        first = self.history()
        active = self.history(
            identity="revise_plan:PAUSED_INVALID_OUTPUT", stage="revise_plan", guidance="Use a valid field"
        )
        state = {"stuck_investigations": [first, active], "stuck_investigation": dict(active, in_force=True)}
        text = stuck.with_guidance(state, "draft_plan", self.request()).prompt
        self.assertEqual(1, text.count(active["guidance"]))
        self.assertLess(text.index(first["guidance"]), text.index(active["guidance"]))
        self.assertIn("takes precedence", text)
        self.assertNotIn("PREVIOUSLY VERIFIED", text)

    def test_configured_lesson_limit_and_rejection_trigger(self):
        rows = [
            self.history(identity=str(i), guidance=f"Correction {i}", status="PAUSED_REPEATED_FAILURE")
            for i in range(5)
        ]
        rows.append(self.history(trigger="non_convergence", status="PAUSED_PLANNING_BUDGET", guidance="Accept M3"))
        state = {"settings": {"stuck_investigation": {"max_calls_per_run": 5}}, "stuck_investigations": rows}
        text = stuck.with_guidance(state, "draft_plan", self.request()).prompt
        for i in range(5):
            self.assertIn(f"Correction {i}", text)
        self.assertNotIn("Accept M3", text)
        state["settings"]["stuck_investigation"]["max_calls_per_run"] = 0
        self.assertEqual(self.request(), stuck.with_guidance(state, "draft_plan", self.request()))

    def request(self):
        return common.ModelRequest("planner", "planner", "Do the stage.\nCURRENT HANDOFF DATA\n{}", {}, {}, False)

    def in_force(self, stage):
        state = state_for(next_stage=stage)
        stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "x")
        stuck.apply(state, report(), {}, state["workspace"])
        return state

    def test_guidance_reaches_the_stuck_stage_before_its_handoff_data(self):
        state = self.in_force("builder")
        prompt = stuck.with_guidance(state, "builder", self.request()).prompt
        self.assertIn("INVESTIGATOR GUIDANCE", prompt)
        self.assertLess(prompt.index("Cite docs/bugs/cent-drift.json exactly"), prompt.index("CURRENT HANDOFF DATA"))
        self.assertNotIn("INVESTIGATOR GUIDANCE", stuck.with_guidance(state, "tester", self.request()).prompt)

    def test_execution_guidance_is_spent_when_its_stage_completes(self):
        state = self.in_force("builder")
        stuck.settle(state, "tester")
        self.assertIn("stuck_investigation", state)
        stuck.settle(state, "builder")
        self.assertNotIn("stuck_investigation", state)

    def test_planning_guidance_covers_the_whole_planning_cycle_until_the_run_stops(self):
        state = self.in_force("review_plan")
        for stage in ("revise_plan", "finalize_plan", "draft_plan"):
            self.assertIn("INVESTIGATOR GUIDANCE", stuck.with_guidance(state, stage, self.request()).prompt, stage)
            stuck.settle(state, stage)
        self.assertNotIn("INVESTIGATOR GUIDANCE", stuck.with_guidance(state, "builder", self.request()).prompt)
        stuck.retire(state)
        self.assertNotIn("stuck_investigation", state)

    def test_autopilot_prepares_every_stage_through_the_guidance(self):
        state = self.in_force("draft_plan")
        original = autopilot.unit_module
        autopilot.unit_module = lambda stage: type("Unit", (), {"prepare": staticmethod(lambda *a: self.request())})
        try:
            prompt = autopilot.prepare_request(state, "revise_plan", Path("/run/state.json"), None).prompt
        finally:
            autopilot.unit_module = original
        self.assertIn("INVESTIGATOR GUIDANCE", prompt)


class RouteTests(unittest.TestCase):
    def test_automatic_investigator_reuses_permitted_reviewer_not_gateway_default(self):
        state = state_for()
        state["settings"]["provider"] = "kilocode"
        state["settings"]["roles"]["plan_reviewer"].update(model="openai/gpt-6-sol", reasoning_effort=None)
        state["settings"]["route_selection"] = {
            "candidates": {"plan_reviewer": [{"model": "openai/gpt-6-sol", "levels": []}]}
        }
        state["stuck_investigation"] = {"stage": "tester"}
        with (
            patch.object(stuck, "prompt", return_value=("diagnose", {})),
            patch.object(autoresolver.autoplanner, "workspace_inventory", return_value={}),
        ):
            autoresolver.prepare_stuck(state, Path(state["workspace"]) / "state.json")
        self.assertEqual("openai/gpt-6-sol", state["settings"]["roles"][stuck.ROUTE]["model"])
        self.assertIsNone(state["settings"]["roles"][stuck.ROUTE]["reasoning_effort"])

    def test_automatic_investigator_refuses_when_only_the_stuck_family_is_permitted(self):
        state = state_for()
        state["settings"]["route_selection"] = {
            "candidates": {"tester": [{"model": "zai-coding-plan/glm-5.3", "levels": None}]}
        }
        state["stuck_investigation"] = {"stage": "tester"}
        with (
            self.assertRaisesRegex(util.Paused, "No independent permitted Investigator"),
            patch.object(stuck, "prompt") as prompt,
        ):
            autoresolver.prepare_stuck(state, Path(state["workspace"]) / "state.json")
        prompt.assert_not_called()
        self.assertNotIn(stuck.ROUTE, state["settings"]["roles"])

    def test_default_models_never_astra_and_never_the_stuck_stages_model(self):
        roles = state_for()["settings"]["roles"]
        pick = lambda roles, stuck_model, provider=None: tuple(
            stuck.route(roles, stuck_model, provider)[k] for k in ("model", "reasoning_effort")
        )
        # kilocode runs use the run's own direct routes, never the Resolver's Astra and never a kilo/ gateway
        # route (user 2026-10-21): the first configured role outside the stuck stage's family.
        self.assertEqual(("openai/gpt-6-sol", "high"), pick(roles, "zai-coding-plan/glm-5.3", "kilocode"))
        self.assertEqual(("zai-coding-plan/glm-5.3", "high"), pick(roles, "openai/gpt-6-sol", "kilocode"))
        self.assertEqual(("zai-coding-plan/glm-5.3", "high"), pick(roles, "kilo/anthropic/claude-opus-5.5", "kilocode"))
        direct = {
            "plan_reviewer": {"model": "xiaomi-token-plan-sgp/mimo-v2.6-pro", "engine": "opencode"},
            "planner": {"model": "zai-coding-plan/glm-5.3"},
            "task_planner": {"model": "kilo/anthropic/claude-opus-5.5"},
        }
        self.assertEqual(
            ("xiaomi-token-plan-sgp/mimo-v2.6-pro", "high"), pick(direct, "zai-coding-plan/glm-5.3", "kilocode")
        )
        self.assertEqual(
            ("zai-coding-plan/glm-5.3", "high"), pick(direct, "xiaomi-token-plan-sgp/mimo-v2.6-pro", "kilocode")
        )
        gateway = {
            "plan_reviewer": {"model": "kilo/anthropic/claude-opus-5.5"},
            "planner": {"model": "kilo/openai/gpt-6-sol"},
        }
        # Only gateway routes configured: the reviewer's own, which prepare_stuck's independence check then judges.
        self.assertEqual(
            ("kilo/anthropic/claude-opus-5.5", "high"), pick(gateway, "zai-coding-plan/glm-5.3", "kilocode")
        )
        self.assertEqual("glm", stuck.family("zhipuai-coding-plan/glm-5.3"))
        self.assertEqual(stuck.family("openai/gpt-6-luna"), stuck.family("gpt-6-sol"))
        # OpenCode runs: Sol, or GLM when the stuck stage runs on Sol.
        self.assertEqual(("openai/gpt-6-sol", "high"), pick(roles, "zai-coding-plan/glm-5.3"))
        self.assertEqual(("zai-coding-plan/glm-5.3", "high"), pick(roles, "openai/gpt-6-sol"))
        self.assertEqual(("zai-coding-plan/glm-5.3", "high"), pick(roles, "openai/gpt-6-luna"))  # one GPT family
        self.assertEqual("opencode", stuck.route(roles, "x")["engine"])
        # Native Codex runs have GPT models only: Sol, or Luna when the stuck stage runs on Sol.
        codex = {"plan_reviewer": {"model": "gpt-6-astra", "engine": "codex"}}
        self.assertEqual(("gpt-6-sol", "high"), pick(codex, "gpt-6-astra"))
        self.assertEqual(("gpt-6-luna", "high"), pick(codex, "gpt-6-sol"))
        # A custom provider keeps its reviewer model.
        self.assertEqual(("custom/model", "high"), pick({"task_planner": {"model": "custom/model"}}, "x", "mytool"))
        for provider in (None, "opencode", "kilocode"):
            self.assertNotIn("task_planner", pick(roles, "openai/gpt-6-sol", provider)[0])
        self.assertEqual(
            "high", roles["plan_reviewer"]["reasoning_effort"], "the Plan Reviewer's own route is untouched"
        )

    def test_the_autoresolver_prepares_a_fresh_investigator(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace, next_stage="review_plan", sessions={stuck.ROUTE: "old"})
            stuck.intercept(state, "PAUSED_PLANNING_BUDGET", "2/2 plan-review calls used")
            request = autoresolver.prepare(state, stuck.STAGE, Path(workspace) / "state.json", None)
        self.assertEqual(
            ("task_planner", stuck.ROUTE, False, stuck.SCHEMA),
            (request.role, request.route_role, request.allow_write, request.schema),
        )
        # The stuck Plan Reviewer runs GPT-6 Astra: another GPT model shares its family, so GLM investigates.
        self.assertEqual("zai-coding-plan/glm-5.3", state["settings"]["roles"][stuck.ROUTE]["model"])
        self.assertNotIn(stuck.ROUTE, state["sessions"])
        self.assertIn('"status":"PAUSED_PLANNING_BUDGET"', request.prompt)
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
            if stage == stuck.STAGE and outcome != "paused":
                return stuck.apply(current, outcome, {}, state["workspace"])
            if stage != stuck.STAGE:
                current.update(next_stage=None, status="TASK_COMPLETE")

        def fail(status):
            def raise_(current):
                current["stages"].append({"stage": current["next_stage"], "rejected": True})
                raise Paused(status, f"{status} at {current['next_stage']}")

            return raise_

        for stage, actions in script.items():
            script[stage] = [fail(a[1:]) if isinstance(a, str) and a.startswith("!") else a for a in actions]
        finished = stuck.drive(
            state,
            dispatch,
            apply=apply,
            persist=lambda _: None,
            active=lambda s: s["status"] == "RUNNING",
            skip=object(),
            paused=Paused,
            investigate=investigate,
        )
        return finished, calls

    def test_a_stuck_stage_is_investigated_retried_and_finishes(self):
        state = state_for()
        done, calls = self.run_loop(state, {"builder": ["!PAUSED_REPEATED_FAILURE", "built"], stuck.STAGE: [report()]})
        self.assertEqual(["builder", stuck.STAGE, "builder"], calls)
        self.assertEqual("TASK_COMPLETE", done["status"])

    def test_the_same_problem_twice_pauses_with_the_diagnosis(self):
        state = state_for()
        with self.assertRaises(Paused) as caught:
            self.run_loop(
                state, {"builder": ["!PAUSED_REPEATED_FAILURE", "!PAUSED_REPEATED_FAILURE"], stuck.STAGE: [report()]}
            )
        self.assertEqual("PAUSED_REPEATED_FAILURE", caught.exception.status)
        self.assertIn("Investigator (retried): The Planner cites", str(caught.exception))

    def test_an_investigation_that_fails_restores_the_original_pause(self):
        state = state_for()
        with self.assertRaises(Paused) as caught:
            self.run_loop(state, {"builder": ["!PAUSED_REPEATED_FAILURE"], stuck.STAGE: ["!PAUSED_INVALID_OUTPUT"]})
        self.assertEqual("PAUSED_REPEATED_FAILURE", caught.exception.status)
        self.assertIn("could not finish", str(caught.exception))
        self.assertEqual(("PAUSED_REPEATED_FAILURE", "builder"), (state["status"], state["next_stage"]))

    def test_an_investigator_out_of_time_restores_the_stuck_pause_not_an_exact_retry_question(self):
        # A live run (2026-11-06): the Investigator of a planner that kept timing out timed out
        # three times itself, and the run stopped on "Continue?" for an exact Investigator retry.
        def stopped(kind):
            def stop(current):
                current["stages"].append({"stage": stuck.STAGE, "rejected": True})
                cause = "Investigator: Stage exceeded its 480-second hard runtime limit"
                current["job_failure"] = {
                    "stage": stuck.STAGE,
                    "kind": kind,
                    "reason": cause + ". AutoCode already retried the Investigator ... waits for its exact retry",
                }
                current["recovery_context"] = {"kind": "job_failure", "stage": stuck.STAGE, "reason": cause}
                current.update(status="PAUSED_JOB_FAILURE", stop_reason=current["job_failure"]["reason"])
                return "paused"

            return stop

        state = state_for()
        with self.assertRaises(Paused) as caught:
            self.run_loop(state, {"builder": ["!PAUSED_TIMEOUT_RECOVERY"], stuck.STAGE: [stopped("timeout")]})
        self.assertEqual("PAUSED_TIMEOUT_RECOVERY", caught.exception.status)
        self.assertIn("PAUSED_TIMEOUT_RECOVERY at builder", str(caught.exception))
        self.assertIn("could not finish: Investigator: Stage exceeded its 480-second", str(caught.exception))
        self.assertNotIn("exact retry", str(caught.exception))
        self.assertEqual(("PAUSED_TIMEOUT_RECOVERY", "builder"), (state["status"], state["next_stage"]))
        for key in ("job_failure", "recovery_context", "stuck_investigation"):
            self.assertNotIn(key, state)
        self.assertEqual("investigation_failed", state["stuck_investigations"][-1]["outcome"])

        # A model stop keeps the job's model question; nothing is abandoned.
        state = state_for()
        done, _ = self.run_loop(state, {"builder": ["!PAUSED_TIMEOUT_RECOVERY"], stuck.STAGE: [stopped("quota")]})
        self.assertEqual(("PAUSED_JOB_FAILURE", stuck.STAGE), (done["status"], done["next_stage"]))
        self.assertIn("stuck_investigation", done)
        # A Builder failure classification keeps its exact retry: it still needs an Investigator.
        done.update(job_failure={**done["job_failure"], "kind": "timeout"})
        self.assertTrue(stuck.timed_out(done))
        done["stuck_investigation"]["mode"] = "builder_failure"
        self.assertFalse(stuck.timed_out(done))

    def test_a_pause_set_on_the_state_is_investigated_too(self):
        state = state_for(next_stage="review_completion")
        calls = []

        def dispatch(current, stage):
            calls.append(stage)
            if stage == stuck.STAGE:
                return report()
            current["stages"].append({"stage": stage})
            if calls.count("review_completion") == 1:
                current.update(status="PAUSED_COMPLETION_REVIEW", stop_reason="All required criteria already pass")
                return "paused"
            current.update(status="TASK_COMPLETE", next_stage=None)
            return "done"

        def apply(current, stage, outcome):
            if stage == stuck.STAGE:
                stuck.apply(current, outcome, {}, state["workspace"])

        stuck.drive(
            state,
            dispatch,
            apply=apply,
            active=lambda s: s["status"] == "RUNNING",
            skip=object(),
            paused=Paused,
            investigate=True,
        )
        self.assertEqual(["review_completion", stuck.STAGE, "review_completion"], calls)
        self.assertEqual("TASK_COMPLETE", state["status"])

    def test_a_pause_reasserted_on_resume_launches_nothing(self):
        # A guard re-raising a known pause before any stage runs is not a new failure to converge.
        state = state_for(status="RUNNING")
        calls = []

        def dispatch(current, stage):
            calls.append(stage)
            raise Paused("PAUSED_BUILDER_RETRY_LIMIT", "Builder lane paused")

        with self.assertRaises(Paused) as caught:
            stuck.drive(
                state,
                dispatch,
                active=lambda s: s["status"] == "RUNNING",
                skip=object(),
                paused=Paused,
                investigate=True,
            )
        self.assertEqual((["builder"], "Builder lane paused"), (calls, str(caught.exception)))
        self.assertNotIn("stuck_investigation", state)

    def test_a_runner_owned_receipt_is_not_a_fresh_attempt(self):
        state = state_for(status="RUNNING")

        def dispatch(current, stage):
            current["stages"].append({"stage": "resolver", "runner_owned": True})
            raise Paused("PAUSED_REPEATED_FAILURE", "known failure re-asserted")

        with self.assertRaises(Paused):
            stuck.drive(
                state,
                dispatch,
                active=lambda s: s["status"] == "RUNNING",
                skip=object(),
                paused=Paused,
                investigate=True,
            )
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
            stuck.drive(
                state,
                dispatch,
                active=lambda s: s["status"] == "RUNNING",
                skip=object(),
                paused=Paused,
                investigate=True,
            )
        self.assertEqual("PAUSED_UNCERTAIN_STAGE", caught.exception.status)
        self.assertEqual(stuck.STAGE, state["next_stage"], "the investigation reruns after reconciliation")
        self.assertIn("stuck_investigation", state)

    def test_without_investigate_the_loop_is_unchanged(self):
        state = state_for()
        with self.assertRaises(Paused) as caught:
            self.run_loop(state, {"builder": ["!PAUSED_REPEATED_FAILURE"]}, investigate=False)
        self.assertEqual("PAUSED_REPEATED_FAILURE at builder", str(caught.exception))
        self.assertNotIn("stuck_investigations", state)


if __name__ == "__main__":
    unittest.main()


class ProbeTests(unittest.TestCase):
    """A retry's diagnosed cause is shown by a probe the runner runs over the cited files only; no model."""

    REJECTED = {"code_refs": ["docs/bugs/cent-drift.json (see the note)"], "summary": "plan"}
    # The rejected output with the path fixed: the cause the probe shows is gone (#1769).
    FIXED_REF = {
        "file": "run/draft_plan-03.json",
        "find": "cent-drift.json (see the note)",
        "replace": "cent-drift.json",
    }
    SPACE_PROBE = (
        "python3 -c \"import json; r = json.load(open('run/draft_plan-03.json')); assert ' ' in r['code_refs'][0]\""
    )

    def setup(self):
        import json
        import subprocess

        workspace = Path(tempfile.mkdtemp(prefix="stuck-probe-ws-"))
        (workspace / "docs" / "bugs").mkdir(parents=True)
        (workspace / "docs" / "bugs" / "cent-drift.json").write_text("{}\n")
        for command in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "seed"]):
            subprocess.run(
                ["git", "-c", "user.name=t", "-c", "user.email=t@example.test", *command], cwd=workspace, check=True
            )
        run_dir = Path(tempfile.mkdtemp(prefix="stuck-probe-run-"))
        (run_dir / "draft_plan-03.json").write_text(json.dumps(self.REJECTED))
        (run_dir / "secret-uncited.json").write_text("{}")
        state = state_for(workspace=str(workspace), next_stage="draft_plan")
        self.assertTrue(stuck.intercept(state, "PAUSED_REPEATED_FAILURE", "rejected three times"))
        return state, workspace, run_dir

    def apply(self, state, run_dir, workspace, **overrides):
        value = report(
            **{
                "evidence_refs": [str(run_dir / "draft_plan-03.json")],
                "untestable": "",
                "counterexample": self.FIXED_REF,
                **overrides,
            }
        )
        autoresolver.apply_job(
            stuck.STAGE,
            state,
            value,
            {"changed_files": [], "output": str(run_dir / "investigate_stuck-01.json")},
            str(workspace),
        )
        return state

    def test_a_probe_over_the_cited_run_file_is_accepted_and_recorded(self):
        state, workspace, run_dir = self.setup()
        probe = (
            "python3 -c \"import json; r = json.load(open('run/draft_plan-03.json')); assert ' ' in r['code_refs'][0]\""
        )
        self.apply(
            state,
            run_dir,
            workspace,
            probe=probe,
            example="Given code_refs 'docs/bugs/cent-drift.json (see "
            "the note)'; when the runner checked the path; then it was rejected as missing",
        )
        entry = state["stuck_investigations"][0]
        self.assertEqual(("retried", 0, probe), (entry["outcome"], entry["probe_result"]["exit_code"], entry["probe"]))
        self.assertNotEqual(0, entry["probe_result"]["counterexample"]["exit_code"])
        self.assertEqual("draft_plan", state["next_stage"])
        self.assertTrue((run_dir / "secret-uncited.json").is_file())  # the real run directory is untouched
        self.assertEqual(
            self.REJECTED["code_refs"], json.loads((run_dir / "draft_plan-03.json").read_text())["code_refs"]
        )

    def test_a_probe_that_passes_whatever_the_cited_files_say_sends_nothing_back(self):
        # #1769: a retry is checked, not trusted, so its probe must fail once the cited file no longer shows
        # the cause. Without that the stage is not retried, and the original pause is restored.
        for probe, counterexample in (
            ("python3 -c 'assert 2 + 2 == 4'", self.FIXED_REF),
            ("test -f run/draft_plan-03.json", self.FIXED_REF),
            (self.SPACE_PROBE, {"file": "", "find": "", "replace": ""}),
        ):
            with self.subTest(probe=probe, counterexample=counterexample):
                state, workspace, run_dir = self.setup()
                with self.assertRaisesRegex(ValueError, "counterexample"):
                    self.apply(state, run_dir, workspace, example="x", probe=probe, counterexample=counterexample)

    def test_an_archived_output_is_probed_at_its_file_name_as_the_prompt_says(self):
        # Live run 2026-09-29: the rejected output had been moved to archived-sol-01-*/ and a probe
        # that opened run/sol-01.json, as the prompt told it to, found nothing.
        state, workspace, run_dir = self.setup()
        archived = run_dir / "archived-draft_plan-03-1a2b3c"
        archived.mkdir()
        (run_dir / "draft_plan-03.json").rename(archived / "draft_plan-03.json")
        probe = (
            "python3 -c \"import json; r = json.load(open('run/draft_plan-03.json')); assert ' ' in r['code_refs'][0]\""
        )
        self.apply(
            state,
            run_dir,
            workspace,
            probe=probe,
            example="x",
            evidence_refs=[str(archived / "draft_plan-03.json")],
        )
        self.assertEqual("retried", state["stuck_investigations"][0]["outcome"])

    def test_two_cited_run_files_with_one_name_are_refused(self):
        state, workspace, run_dir = self.setup()
        (run_dir / "archived").mkdir()
        (run_dir / "archived" / "draft_plan-03.json").write_text("{}")
        with self.assertRaisesRegex(ValueError, "two run files named draft_plan-03.json"):
            stuck.cited_files(
                report(
                    evidence_refs=[
                        str(run_dir / "draft_plan-03.json"),
                        str(run_dir / "archived" / "draft_plan-03.json"),
                    ]
                ),
                workspace,
                run_dir,
            )

    def test_a_probe_that_does_not_show_the_cause_rejects_the_report(self):
        state, workspace, run_dir = self.setup()
        with self.assertRaisesRegex(ValueError, "diagnosed causes' probes did not exit 0"):
            self.apply(
                state,
                run_dir,
                workspace,
                example="x",
                probe="python3 -c \"import json; r = json.load(open('run/draft_plan-03.json')); assert ' ' not in r['code_refs'][0]\"",
            )

    def test_a_probe_sees_only_the_cited_files(self):
        state, workspace, run_dir = self.setup()
        with self.assertRaisesRegex(ValueError, "did not exit 0"):
            self.apply(state, run_dir, workspace, example="x", probe="test -f run/secret-uncited.json")

    def test_cited_files_must_exist(self):
        state, workspace, run_dir = self.setup()
        with self.assertRaisesRegex(ValueError, "not found there"):
            self.apply(
                state,
                run_dir,
                workspace,
                example="x",
                untestable="judgement",
                evidence_refs=["docs/bugs/no-such-note.json"],
            )
        self.assertEqual(
            {"run/draft_plan-03.json": (run_dir / "draft_plan-03.json").resolve()},
            stuck.cited_files(
                report(evidence_refs=["docs/bugs/cent-drift.json:3", str(run_dir / "draft_plan-03.json")]),
                workspace,
                run_dir,
            ),
        )

    def test_a_retry_needs_an_example_and_exactly_one_of_probe_and_untestable(self):
        state, workspace, run_dir = self.setup()
        with self.assertRaisesRegex(ValueError, "example in plain English"):
            self.apply(state, run_dir, workspace, example=" ", untestable="judgement")
        with self.assertRaisesRegex(ValueError, "exactly one of probe"):
            self.apply(state, run_dir, workspace, example="x")
        with self.assertRaisesRegex(ValueError, "must cite the files"):
            self.apply(state, run_dir, workspace, example="x", untestable="judgement", evidence_refs=[])
