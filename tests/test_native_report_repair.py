"""Native planning's bounded report-only repair and its refusals (#1257).

A completed native planning report that fails validation gets master's bounded
report-only repair of the same stage on the same route, through the same native
writer and launch owner, and native runs never queue an Investigator they cannot
run. The native factory is controlled here: these tests prove admission, bounds
and wiring, not native isolation (that needs the macOS boundary).
"""

import json
import tempfile
import unittest
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode as runner
import autocode_action_recovery as recovery_actions
import autocode_native_calls as callers
import autocode_native_invocation as invocation
import autocode_util as util
import autocode_visual_factory as factories
import autocode_visual_profile as profiles

from tests import test_native_planning_calls as planning_call_tests

PROFILE = {
    "version": 1,
    "mode": "isolated-builtin-openai",
    "opencode_version": "1.18.33",
    "model": "openai/gpt-6-sol",
    "reasoning_effort": "high",
    "sdk_path": ".autocode/sdk",
    "sdk_manifest_sha256": "a" * 64,
    "transport_identity_sha256": "b" * 64,
    "executable_sha256": "c" * 64,
    "max_requests": 8,
    "image_limits": dict(profiles.IMAGE_BOUNDS),
}
TASK = "Controlled original planning task"
ROUTE = {"engine": "opencode", "provider": "opencode", "model": "controlled/reviewer", "reasoning_effort": "high"}
MALFORMED = ValueError("Agent did not produce valid JSON: Extra data: line 1 column 1830")


class NativeRepairFixture(unittest.TestCase):
    """A real PlanningCalls and parent sink over a controlled factory; no provider runs."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="native-report-repair-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.run = self.root / ".autocode/runs/controlled"
        self.run.mkdir(parents=True)
        self.path = self.run / "state.json"
        self.factory = object.__new__(factories.NativeVisualFactory)
        for name in ("bind_parent_sink", "invalidate_parent_ack", "require_planning_origin", "finish_launch"):
            setattr(self.factory, name, Mock())
        self.factory.prepare_parent_commit = Mock(return_value="controlled intent")
        self.factory.capture_parent_commit = Mock(return_value="controlled ACK")
        self.factory.commit_planning_publication = Mock()
        self.factory.evidence_publications = Mock(return_value=())
        self.saved = []

        def write(path, value, **hooks):
            util.atomic_json(path, value, **hooks)
            if Path(path) == self.path:
                self.saved.append(deepcopy(value))

        self.calls = callers.PlanningCalls(
            SimpleNamespace(factory=self.factory),
            PROFILE,
            initial_task=TASK,
            run_dir=self.run,
            workspace=self.root,
            write=write,
            snapshot=lambda value: {"controlled": "snapshot"},
            planning_stages=runner.planning.V2_STAGES,
            recognizer=runner.workflows.STAGE,
        )
        self.snapshot = {"head": "controlled-head", "files": {}}
        self.snapshot["revision"] = util.digest(self.snapshot)
        contract = {"task_id": "t1", "revision": 1, "body": {"open_blocking_questions": []}, "approval_status": "draft"}
        contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
        self.state = {
            "version": 3,
            "workspace": str(self.root),
            "task": TASK,
            "goal_contract": contract,
            "current_task": None,
            "stages": [],
            "history": [],
            "iteration": 1,
            "status": "RUNNING",
            "phase": "PLANNING",
            "next_stage": "plan_review",
            "settings": {
                "planning_flow": "v2",
                "report_repair": {"max_attempts": 2},
                "roles": {"plan_reviewer": dict(ROUTE), "glm": dict(ROUTE, model="controlled/planner")},
                "limits": {},
            },
            "planning": {"astra_calls": 2, "review_call_limit": 2},
        }
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(
            patch.object(runner.source_scope, "snapshot", side_effect=lambda *a, **k: self.snapshot)
        )

    def record(self, stage="plan_review", *, name=None, repair=False, **overrides):
        base = self.run / "iterations" / "001" / (name or stage.replace("_", "-") + ("-repair" if repair else ""))
        base.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "role": "plan_reviewer",
            "stage": stage + ("_report_repair" if repair else ""),
            "iteration": 1,
            "exit_code": 0,
            "timed_out": False,
            "engine": "opencode",
            "launch_route": dict(ROUTE),
            "planning": True,
            "source_revision": self.snapshot["revision"],
            "contract_hash": self.state["goal_contract"]["hash"],
        }
        if repair:
            record.update(report_only=True, original_stage=stage)
        for key, suffix, text in (
            ("events", ".jsonl", json.dumps({"type": "turn.completed"}) + "\n"),
            ("output", ".json", '{"decision": "controlled"'),
            ("schema", ".schema.json", "{}"),
            ("before_ref", ".before.json", json.dumps(self.snapshot)),
            ("after_ref", ".after.json", json.dumps(self.snapshot)),
        ):
            path = Path(str(base) + suffix)
            path.write_text(text)
            record[key] = str(path)
        record.update(overrides)
        return record

    def queue(self, stage="plan_review"):
        self.state["next_stage"] = stage
        with self.assertRaises(runner.ReportRepairQueued):
            self.calls.reject(self.state, self.record(stage), MALFORMED)  # a closed launch, as run_role reports it
        return self.state["pending_report_repair"]

    def disk(self):
        return json.loads(self.path.read_text())


class RejectionQueuesTheBoundedRepair(NativeRepairFixture):
    def test_malformed_completed_planning_report_queues_report_only_repair(self):
        record = self.record()
        with self.assertRaises(runner.ReportRepairQueued):
            self.calls.reject(self.state, record, MALFORMED)
        pending = self.state["pending_report_repair"]
        self.assertEqual(0, pending["attempts"])
        self.assertEqual(record["output"], pending["original"]["output"])
        self.assertTrue(pending["original"]["rejected"])
        self.assertEqual(self.state["goal_contract"]["hash"], pending["contract_hash"])
        self.assertEqual(str(MALFORMED), pending["error"])
        for key in ("events", "output", "schema", "before_ref", "after_ref"):
            self.assertEqual(util.file_hash(record[key]), pending["pins"][record[key]], key)
        self.assertEqual(("RUNNING", "REPORT_REPAIR"), (self.state["status"], self.state["phase"]))
        self.assertEqual("plan_review", self.state["next_stage"])
        # The original bytes stay in place and in history; the review allowance is untouched.
        self.assertEqual('{"decision": "controlled"', Path(record["output"]).read_text())
        self.assertEqual([record["output"]], [row["output"] for row in self.state["stages"]])
        self.assertEqual({"astra_calls": 2, "review_call_limit": 2}, self.state["planning"])
        self.assertEqual(self.state, self.disk())  # saved through the native parent sink

    def test_launch_without_its_closing_report_file_keeps_the_original_pause(self):
        with self.assertRaisesRegex(util.Paused, "Original native output retained") as caught:
            self.calls.reject(self.state, self.record(), MALFORMED, closed=False)
        self.assertEqual("PAUSED_INVALID_OUTPUT", caught.exception.status)
        self.assertNotIn("pending_report_repair", self.state)
        self.assertEqual("PAUSED_INVALID_OUTPUT", self.disk()["status"])

    def test_final_review_and_non_report_errors_keep_the_original_pause(self):
        cases = (
            ("final review", "plan_finalize", MALFORMED, {}),
            ("guard pause", "plan_review", util.Paused("PAUSED_STALE_VALIDATION", "changed"), {}),
            ("nonzero exit", "plan_review", MALFORMED, {"exit_code": 1}),
            ("timed out", "plan_review", MALFORMED, {"timed_out": True}),
            ("no source pin", "plan_review", MALFORMED, {"source_revision": None}),
        )
        for label, stage, error, overrides in cases:
            with self.subTest(label):
                self.state.pop("pending_report_repair", None)
                self.state["next_stage"] = stage
                with self.assertRaises(util.Paused) as caught:
                    self.calls.reject(self.state, self.record(stage, name=label, **overrides), error, closed=True)
                self.assertEqual("PAUSED_INVALID_OUTPUT", caught.exception.status)
                self.assertNotIn("pending_report_repair", self.state)

    def test_rejected_repairs_are_bounded_and_keep_every_attempt(self):
        pending = self.queue()
        pending["attempts"] = 1
        first = self.record(repair=True, name="repair-1")
        with self.assertRaises(runner.ReportRepairQueued):
            self.calls.reject(self.state, first, ValueError("still malformed"), closed=True)
        self.assertEqual(first["output"], pending["latest_rejected"]["output"])
        self.assertEqual("still malformed", pending["error"])
        pending["attempts"] = 2
        last = self.record(repair=True, name="repair-2")
        with self.assertRaises(util.Paused) as caught:
            self.calls.reject(self.state, last, ValueError("malformed again"), closed=True)
        self.assertEqual("PAUSED_INVALID_OUTPUT", caught.exception.status)
        self.assertEqual(2, self.state["pending_report_repair"]["attempts"])
        self.assertEqual(last["output"], self.state["pending_report_repair"]["latest_rejected"]["output"])
        self.assertEqual(3, len(self.state["stages"]))


class AdmissionOfExactlyThatRepair(NativeRepairFixture):
    def test_guard_admits_the_queued_repair_and_its_report_only_launch(self):
        pending = self.queue()
        self.calls.guard(self.state)  # the stage boundary before execute_report_repair
        pending["attempts"] = 1  # execute_report_repair counts the launch before run_role
        self.calls.guard(self.state, "plan_review", report_only=True, allow_write=False)
        self.assertEqual(2, self.factory.require_planning_origin.call_count)

    def test_guard_refuses_anything_beyond_the_same_bounded_repair(self):
        def changed(label, mutate, **launch):
            state = deepcopy(self.state)
            mutate(state)
            with self.subTest(label), self.assertRaises(util.Paused) as caught:
                self.calls.guard(state, state["next_stage"], **launch)
            self.assertEqual("PAUSED_VISUAL_EVIDENCE", caught.exception.status)

        self.queue()
        self.state["pending_report_repair"]["attempts"] = 1
        repair = {"report_only": True, "allow_write": False}
        changed("writer", lambda s: None, report_only=True, allow_write=True)
        changed("original stage again", lambda s: None, allow_write=False)
        changed("uncounted launch", lambda s: s["pending_report_repair"].update(attempts=0), **repair)
        changed("over the bound", lambda s: s["pending_report_repair"].update(attempts=3), **repair)
        changed("another stage", lambda s: s.update(next_stage="plan_revise"), **repair)
        changed("contract changed", lambda s: s["goal_contract"].update(hash="changed"), **repair)
        changed("model changed", lambda s: s["settings"]["roles"]["plan_reviewer"].update(model="other"), **repair)
        changed("effort changed", lambda s: s["settings"]["roles"]["plan_reviewer"].update(reasoning_effort="low"))
        changed("not rejected", lambda s: s["pending_report_repair"]["original"].update(rejected=False))
        changed("repair of a repair", lambda s: s["pending_report_repair"]["original"].update(report_only=True))
        changed("final review", lambda s: s.update(next_stage="plan_finalize"))
        changed("no pending repair", lambda s: s.pop("pending_report_repair"), **repair)
        self.factory.require_planning_origin.assert_not_called()

    def test_repair_launch_must_use_the_original_route(self):
        self.queue()
        with patch.object(callers.isolation, "NativeRoleIsolation") as isolation:
            with self.assertRaisesRegex(util.Paused, "original saved route"):
                self.calls.role_isolation(self.state, "glm")
            self.calls.role_isolation(self.state, "plan_reviewer")
        isolation.assert_called_once()

    def test_plain_resume_is_admitted_and_retry_controls_stay_refused(self):
        self.queue()
        self.calls.require_supported_action(SimpleNamespace(run_dir=self.run), self.state)
        for flag in ("retry_report", "retry_format"):
            with self.subTest(flag), self.assertRaisesRegex(util.Paused, "native scope beyond first approval"):
                self.calls.require_supported_action(SimpleNamespace(run_dir=self.run, **{flag: "001/x"}), self.state)
        self.state["pending_report_repair"]["original"]["stage"] = "plan_finalize"
        with self.assertRaises(util.Paused):
            self.calls.require_supported_action(SimpleNamespace(run_dir=self.run), self.state)


class RepairRunsUnderTheNativeOwner(NativeRepairFixture):
    def setUp(self):
        super().setUp()
        self.view = invocation.runner_view(runner, self.calls)

    def test_repair_launches_report_only_on_the_original_route_through_the_native_owner(self):
        self.queue()
        with patch.object(runner, "run_role", side_effect=RuntimeError("controlled launch stop")) as launch:
            with self.assertRaisesRegex(RuntimeError, "controlled launch stop"):
                self.view.execute_report_repair(self.state, self.run, self.root)
        options = launch.call_args.kwargs
        self.assertEqual(
            ("plan_reviewer", "read-only", False, True, "controlled/reviewer"),
            (options["role"], options["sandbox"], options["allow_write"], options["report_only"], options["model"]),
        )
        self.assertIs(self.calls, options["native"])
        self.assertEqual(self.calls.sink.write, options["persist"])
        self.assertNotIn("resume_session", options)  # never the same-session format shortcut
        self.assertIn(str(MALFORMED), options["prompt"])
        self.assertEqual(1, self.state["pending_report_repair"]["attempts"])
        self.assertEqual(1, self.disk()["pending_report_repair"]["attempts"])  # counted before the launch

    def test_exhausted_or_changed_inputs_launch_nothing(self):
        self.queue()
        cases = (
            (
                "bound spent",
                "PAUSED_REPORT_REPAIR_LIMIT",
                lambda: self.state["pending_report_repair"].update(attempts=2),
            ),
            ("contract", "PAUSED_STALE_VALIDATION", lambda: self.state["goal_contract"].update(hash="changed")),
            ("evidence", "PAUSED_STALE_VALIDATION", lambda: Path(self.state["stages"][0]["events"]).write_text("{}\n")),
        )
        for label, status, change in cases:
            with self.subTest(label):
                saved = deepcopy(self.state)
                change()
                with patch.object(runner, "run_role") as launch, self.assertRaises(util.Paused) as caught:
                    self.view.execute_report_repair(self.state, self.run, self.root)
                self.assertEqual(status, caught.exception.status)
                launch.assert_not_called()
                self.state = saved
                Path(saved["stages"][0]["events"]).write_text(json.dumps({"type": "turn.completed"}) + "\n")

    def repaired(self, **options):
        record = self.record(repair=True, name="repair-accepted")
        Path(record["output"]).write_text('{"decision": "repaired"}')
        with (
            patch.object(runner, "run_role", return_value=({"decision": "repaired"}, record)) as launch,
            patch.object(runner, "apply_result", **options) as apply,
        ):
            self.view.execute_report_repair(self.state, self.run, self.root)
        launch.assert_called_once()
        return record, apply

    def test_accepted_repair_is_applied_and_saved_by_the_native_owner(self):
        self.queue()

        def apply(state, stage, value, record, workspace, run_dir, *, native):
            runner.save_record(state, record)
            state["next_stage"] = "plan_revise"

        record, applied = self.repaired(side_effect=apply)
        self.assertIs(self.calls, applied.call_args.kwargs["native"])
        self.assertEqual("plan_review", applied.call_args.args[1])
        self.assertNotIn("pending_report_repair", self.state)
        self.assertEqual("accepted", self.state["report_repair_history"][-1]["result"])
        self.assertEqual(record["output"], self.state["stages"][-1]["output"])
        self.assertEqual("plan_revise", self.state["next_stage"])
        self.assertEqual({"astra_calls": 2, "review_call_limit": 2}, self.state["planning"])
        self.assertEqual(self.state, self.disk())
        self.factory.prepare_parent_commit.assert_not_called()  # no planning publication claimed
        self.assertFalse(self.calls.sink.blocked)

    def test_repair_whose_report_still_fails_queues_the_next_attempt_then_stops(self):
        self.queue()
        with self.assertRaises(runner.ReportRepairQueued):
            self.repaired(side_effect=ValueError("repaired report still invalid"))
        pending = self.state["pending_report_repair"]
        self.assertEqual((1, "repaired report still invalid"), (pending["attempts"], pending["error"]))
        self.assertTrue(pending["latest_rejected"]["report_only"])
        with self.assertRaises(util.Paused) as caught:
            self.repaired(side_effect=ValueError("second repair invalid"))
        self.assertEqual("PAUSED_INVALID_OUTPUT", caught.exception.status)
        with patch.object(runner, "run_role") as launch, self.assertRaises(util.Paused) as exhausted:
            self.view.execute_report_repair(self.state, self.run, self.root)
        self.assertEqual("PAUSED_REPORT_REPAIR_LIMIT", exhausted.exception.status)
        launch.assert_not_called()
        self.assertEqual({"astra_calls": 2, "review_call_limit": 2}, self.state["planning"])

    def test_resume_never_renews_the_native_repair_bound(self):
        pending = self.queue()
        pending["attempts"] = 1
        self.state.update(status="PAUSED_REQUESTED", phase="PAUSED_OR_BLOCKED", stop_reason="Pause requested")
        args = SimpleNamespace(
            resume_paused=True,
            abandon_stage=None,
            accept_source_edit=False,
            grant_recovery=None,
            max_milestone_seconds=None,
            retry_report=None,
            retry_failed_stage=False,
            diagnose_failed_stage=False,
        )
        decided = SimpleNamespace(gate=None, retained_report=None, information_admitted=False)
        self.assertIsNone(
            recovery_actions.resume(self.view, args, None, self.state, self.path, self.run, self.root, decided)
        )
        self.assertEqual(1, self.state["pending_report_repair"]["attempts"])
        self.assertNotIn("report_repair_lifetime_attempts", self.state)


class NoInvestigatorUnderNativeAuthority(NativeRepairFixture):
    def test_native_build_loop_restores_the_original_pause_without_spending_an_investigation(self):
        proxy = invocation.runner_view(runner, self.calls)
        proxy.consume_interventions = Mock(return_value=False)
        self.state["next_stage"] = "plan_finalize"
        util.atomic_json(self.path, self.state)
        options = {}
        real_drive = runner.orchestrator.drive

        def attempt(current, stage):
            # A fresh rejected final review, as native reject leaves it.
            current["stages"].append({"stage": stage, "rejected": True})
            raise util.Paused("PAUSED_INVALID_OUTPUT", "Original native output retained: controlled malformed report")

        def drive(current, dispatch, **callbacks):
            options.update(callbacks)
            return real_drive(
                current,
                attempt,
                before=lambda value: self.calls.guard(value),
                persist=callbacks["persist"],
                investigate=callbacks["investigate"],
            )

        args = SimpleNamespace(unit=None, chat=False, pause_after_stage=False, run_dir=self.run)
        with (
            patch.object(runner.run_setup, "load_locked", return_value=self.state),
            patch.object(runner.run_actions, "handle", return_value=None),
            patch.object(runner.orchestrator, "drive", side_effect=drive),
        ):
            code = runner.run_setup.run_locked(
                proxy, args, None, self.state, self.path, self.run, self.root, native=self.calls, checkout_owned=True
            )
        self.assertEqual(2, code)
        saved = self.disk()
        self.assertEqual("PAUSED_INVALID_OUTPUT", saved["status"])
        self.assertEqual("plan_finalize", saved["next_stage"])
        self.assertNotIn("Investigator", saved["stop_reason"])
        self.assertNotIn("stuck_investigations", saved)
        self.assertNotIn("stuck_investigation", saved)
        self.assertFalse(options["investigate"])


class RepairLaunchPinsTheStageInputs(unittest.TestCase):
    """The real launch preparation pins a repair to the planning inputs of the stage it repairs."""

    setUp = planning_call_tests.PlanningInputPinAdmissionTests.setUp
    prepare_input_context = planning_call_tests.PlanningInputPinAdmissionTests.prepare_input_context
    launch = planning_call_tests.PlanningInputPinAdmissionTests.launch
    assert_refuses_before_child = planning_call_tests.PlanningInputPinAdmissionTests.assert_refuses_before_child
    record_overrides = {"stage": "plan_report_repair", "original_stage": "plan", "report_only": True}

    def test_repair_launch_pins_the_planning_inputs_of_its_stage(self):
        import autocode_design_manifest as manifest

        pins = manifest.planning_input_pins(self.state["settings"], role="glm")
        self.assertEqual(2, len(pins))
        prepared = json.loads(self.prepared.body)
        self.assertEqual("plan_report_repair", prepared["record_identity"]["stage"])
        self.assertEqual(pins, {path: prepared["pins"].get(path) for path in pins})

    def test_replaced_planning_input_refuses_the_repair_before_child(self):
        path = Path(self.inputs["root"]) / "planning-catalog.json"
        original = path.read_bytes()
        path.unlink()
        path.write_bytes(b"0" * len(original))
        path.chmod(0o400)
        self.assert_refuses_before_child()


if __name__ == "__main__":
    unittest.main()
