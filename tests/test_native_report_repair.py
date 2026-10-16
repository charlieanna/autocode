"""Native planning's bounded report-only repair and its refusals (#1257).

A completed native planning report that fails validation gets master's bounded
report-only repair of the same stage on the same route, through the same native
writer and launch owner. A stopped repair launch is closed like a stopped original
planning stage, and native runs send no stage pause to an Investigator they cannot
run. The native factory is controlled here, except where a test borrows the real
journal owner: these tests prove admission, bounds and wiring, not native
isolation (that needs the macOS boundary).
"""

import json
import tempfile
import unittest
from contextlib import ExitStack, contextmanager, nullcontext
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode as runner
import autocode_action_recovery as recovery_actions
import autocode_builder_failure as builder_failure
import autocode_native_calls as callers
import autocode_native_invocation as invocation
import autocode_taskrun as taskrun
import autocode_util as util
import autocode_visual_factory as factories
import autocode_visual_profile as profiles
import autocode_visual_protection as protection

from tests import test_native_abandon_constructor as abandon_constructor_tests
from tests import test_native_cleanup_cancellation as cleanup_cancellation_tests
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
FORMAT_ERROR = RuntimeError(runner.format_correction.FORMAT_ERROR)


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
            self.calls.reject(self.state, self.record(stage), MALFORMED, closed=True)  # run_role closed the launch
        return self.state["pending_report_repair"]

    def disk(self):
        return json.loads(self.path.read_text())


class RejectionQueuesTheBoundedRepair(NativeRepairFixture):
    def test_malformed_completed_planning_report_queues_report_only_repair(self):
        record = self.record()
        with self.assertRaises(runner.ReportRepairQueued):
            self.calls.reject(self.state, record, MALFORMED, closed=True)
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
        record = self.record()
        Path(record["output"]).unlink()  # the response never became a report file
        with self.assertRaisesRegex(util.Paused, "Original native output retained") as caught:
            self.calls.reject_launch(self.state, record, FORMAT_ERROR, "controlled prepared", self.snapshot)
        self.assertEqual("PAUSED_INVALID_OUTPUT", caught.exception.status)
        self.factory.finish_launch.assert_not_called()
        self.assertNotIn("pending_report_repair", self.state)
        self.assertEqual("PAUSED_INVALID_OUTPUT", self.disk()["status"])

    def test_launch_with_its_report_file_is_closed_before_its_repair_is_queued(self):
        record = self.record()
        with self.assertRaises(runner.ReportRepairQueued):
            self.calls.reject_launch(self.state, record, MALFORMED, "controlled prepared", self.snapshot)
        self.factory.finish_launch.assert_called_once_with("controlled prepared", record, self.snapshot)
        self.assertEqual(record["output"], self.state["pending_report_repair"]["original"]["output"])

    def test_rejection_must_say_whether_the_launch_closed(self):
        with self.assertRaises(TypeError):
            self.calls.reject(self.state, self.record(), MALFORMED)

    def test_final_review_malformed_report_queues_the_bounded_repair(self):
        self.state["next_stage"] = "plan_finalize"
        record = self.record("plan_finalize")
        with self.assertRaises(runner.ReportRepairQueued):
            self.calls.reject(self.state, record, MALFORMED, closed=True)
        pending = self.state["pending_report_repair"]
        self.assertEqual("plan_finalize", pending["original"]["stage"])
        self.assertEqual("REPORT_REPAIR", self.state["phase"])

    def test_non_report_errors_keep_the_original_pause(self):
        cases = (
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
            with self.subTest(label):
                with self.assertRaises(util.Paused) as caught:
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
        for label, key, value in (
            ("repair of a repair", "report_only", True),
            ("nonzero exit", "exit_code", 1),
            ("no source pin", "source_revision", None),
            ("timed out", "timed_out", True),
            ("interrupted", "interrupted", True),
            ("dry run", "dry_run", True),
            ("fallback grant", "reviewer_fallback_grant", "grant-1"),
        ):
            original = lambda s, key=key, value=value: s["pending_report_repair"]["original"].update({key: value})
            changed(label, original)
            changed(label + " (launch)", original, **repair)

        changed("no pending repair", lambda s: s.pop("pending_report_repair"), **repair)
        self.factory.require_planning_origin.assert_not_called()

    def test_guard_admits_final_review_queued_repair_and_its_resume(self):
        pending = self.queue("plan_finalize")
        self.calls.guard(self.state)
        pending["attempts"] = 1
        self.calls.guard(self.state, "plan_finalize", report_only=True, allow_write=False)
        self.assertEqual(2, self.factory.require_planning_origin.call_count)
        self.calls.require_supported_action(SimpleNamespace(run_dir=self.run), self.state)

    def test_repair_launch_must_use_the_original_route(self):
        self.queue()
        with patch.object(callers.isolation, "NativeRoleIsolation") as isolation:
            with self.assertRaisesRegex(util.Paused, "original saved route"):
                self.calls.role_isolation(self.state, "glm")
            self.calls.role_isolation(self.state, "plan_reviewer")
        isolation.assert_called_once()

    def test_plain_resume_is_admitted_and_retry_report_stays_refused(self):
        self.queue()
        self.calls.require_supported_action(SimpleNamespace(run_dir=self.run), self.state)
        with self.assertRaisesRegex(util.Paused, "native scope beyond first approval"):
            self.calls.require_supported_action(SimpleNamespace(run_dir=self.run, retry_report="001/x"), self.state)
        self.state["pending_report_repair"]["original"]["stage"] = "terra"
        self.state["next_stage"] = "terra"
        with self.assertRaisesRegex(util.Paused, "only its own bounded report-only repair"):
            self.calls.require_supported_action(SimpleNamespace(run_dir=self.run), self.state)


class RepairRunsUnderTheNativeOwner(NativeRepairFixture):
    def setUp(self):
        super().setUp()
        self.view = invocation.runner_view(runner, self.calls)

    def test_repair_launches_report_only_on_the_original_route_through_the_native_owner(self):
        self.queue()
        queued, counted = len(self.saved), []

        def stop(*_args, **_options):
            # The attempt counts the native parent sink had saved when the repair launched.
            counted.extend(row["pending_report_repair"]["attempts"] for row in self.saved[queued:])
            raise RuntimeError("controlled launch stop")

        with (
            patch.object(runner, "write_json", side_effect=AssertionError("ordinary writer used")),
            patch.object(runner, "run_role", side_effect=stop) as launch,
        ):
            with self.assertRaisesRegex(RuntimeError, "controlled launch stop"):
                self.view.execute_report_repair(self.state, self.run, self.root)
        self.assertEqual([1], counted)  # counted through the native parent sink before the launch
        options = launch.call_args.kwargs
        self.assertEqual(
            ("plan_reviewer", "read-only", False, True, "controlled/reviewer"),
            (options["role"], options["sandbox"], options["allow_write"], options["report_only"], options["model"]),
        )
        self.assertIs(self.calls, options["native"])
        self.assertEqual(self.calls.sink.write, options["persist"])
        self.assertIn(str(MALFORMED), options["prompt"])
        self.assertEqual(1, self.state["pending_report_repair"]["attempts"])
        self.assertEqual(1, self.disk()["pending_report_repair"]["attempts"])

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

    def test_accepted_plan_finalize_repair_commits_native_planning_publication(self):
        self.queue("plan_finalize")
        record = self.record("plan_finalize", repair=True, name="final-repair-accepted")
        Path(record["output"]).write_text('{"decision": "repaired"}')

        def apply(state, stage, value, record, workspace, run_dir, *, native):
            runner.save_record(state, record)
            state["status"] = "AWAITING_GOAL_APPROVAL"
            state["planning_final"] = {
                "artifact": {"path": "planning/artifact.json", "sha256": "0" * 64},
                "delta": {"path": "planning/delta.json", "sha256": "0" * 64},
                "graph": {"path": "planning/graph.json", "sha256": "0" * 64},
                "final_token": "token-1",
            }
            state["planning"]["final_token"] = "token-1"

        with (
            patch.object(runner, "run_role", return_value=({"decision": "repaired"}, record)) as launch,
            patch.object(runner, "apply_result", side_effect=apply) as applied,
        ):
            self.view.execute_report_repair(self.state, self.run, self.root)
        self.assertNotIn("pending_report_repair", self.state)
        self.assertEqual("accepted", self.state["report_repair_history"][-1]["result"])
        self.factory.prepare_parent_commit.assert_called_once()
        self.factory.commit_planning_publication.assert_called_once_with(
            "controlled ACK", self.state, snapshot={"controlled": "snapshot"}
        )
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

    def plain_resume(self):
        """A plain `autocode resume`: admitted before setup, then the ordinary resume step."""
        self.calls.require_supported_action(SimpleNamespace(run_dir=self.run), self.state)
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
        return recovery_actions.resume(self.view, args, None, self.state, self.path, self.run, self.root, decided)

    def test_resume_never_renews_the_native_repair_bound(self):
        pending = self.queue()
        pending["attempts"] = 1
        self.state.update(status="PAUSED_REQUESTED", phase="PAUSED_OR_BLOCKED", stop_reason="Pause requested")
        self.assertIsNone(self.plain_resume())
        self.assertEqual(1, self.state["pending_report_repair"]["attempts"])
        self.assertNotIn("report_repair_lifetime_attempts", self.state)

    def test_resume_after_the_spent_bound_keeps_the_count_and_runs_the_stage_again(self):
        pending = self.queue("plan")
        pending["attempts"] = 2
        last = self.record("plan", repair=True, name="plan-repair-2")
        with self.assertRaises(util.Paused) as caught:
            self.calls.reject(self.state, last, ValueError("second repair invalid"), closed=True)
        self.assertEqual("PAUSED_INVALID_OUTPUT", caught.exception.status)
        self.assertIsNone(self.plain_resume())
        # The spent repair is archived with its count, every rejected attempt stays, and no file moves.
        self.assertNotIn("pending_report_repair", self.state)
        archived = self.state["report_repair_archive"][-1]["repair"]
        self.assertEqual((2, last["output"]), (archived["attempts"], archived["latest_rejected"]["output"]))
        self.assertEqual(2, len(self.state["stages"]))
        self.assertTrue(all(Path(row["output"]).is_file() for row in self.state["stages"]))
        # The stage itself runs again as a fresh attempt, not as another repair of the spent one.
        with self.assertRaises(util.Paused):
            self.calls.guard(self.state, "plan", report_only=True, allow_write=False)
        self.calls.guard(self.state, "plan", allow_write=False)


class ResumeThatChangesTheRepairedRoute(NativeRepairFixture):
    """`autocode resume` with a new model or effort for the role whose report is being repaired.

    The repair runs only on its original route, so a changed route archives it and the stage runs
    again as a fresh attempt on the new route, as a resume after a rejected native report did before
    repairs existed. The real CLI parser, run_setup.run_locked, load_locked, configure and the resume
    step run; registration, containment setup and the model catalogue are stubbed, and the build loop
    is replaced by the native admission of the launch it would make next.
    """

    def setUp(self):
        super().setUp()
        self.state["settings"].update(
            engine="opencode", provider="opencode", joint_planning=True, transport_identity={"controlled": True}
        )
        for role in ("astra", "terra", "sol", "completion", "requirements"):
            self.state["settings"]["roles"].setdefault(role, dict(ROUTE, model="controlled/" + role))
        self.state["sessions"] = {}

    def resume(self, *flags):
        util.atomic_json(self.path, self.state)
        argv = ["--run-dir", str(self.run), "--resume-paused", *flags]
        args, parser = runner.cli_args.parse(None, argv, runner.opencode.DEFAULT_MODELS)
        args._native_action_argv = tuple(argv)
        self.calls.require_supported_action(args, self.state)  # native_invocation.run, before setup
        admitted = []

        def resume_step(view, args, parser, state, *rest):
            decided = SimpleNamespace(gate=None, retained_report=None, information_admitted=False)
            return recovery_actions.resume(view, args, parser, state, *rest, decided)

        def build(view, args, state, *_rest, **_options):
            self.calls.guard(state)  # before_code_stage: the native admission of the next launch
            admitted.append(deepcopy(state))
            return 0

        # Each state save the native parent sink makes, with the state on disk just before it.
        sink_write, saved_before, self.sink_saves = self.calls.sink.write, len(self.saved), []

        def through_sink(path, value, **options):
            if Path(path) == self.path:
                self.sink_saves.append((self.disk(), deepcopy(value)))
            return sink_write(path, value, **options)

        with (
            patch.object(self.calls.sink, "write", side_effect=through_sink),
            patch.object(runner, "write_json", side_effect=AssertionError("ordinary writer used")),
            patch.object(runner.run_actions, "handle", side_effect=resume_step),
            patch.object(runner.build_loop, "run", side_effect=build),
            patch.object(runner.run_setup.registry, "register_run"),
            patch.object(runner.run_setup.containment_policy, "configure"),
            patch.object(runner.run_setup.quota_route, "model_problem", return_value=None),
        ):
            code = runner.run_setup.run_locked(
                invocation.runner_view(runner, self.calls),
                args,
                parser,
                self.state,
                self.path,
                self.run,
                self.root,
                native=self.calls,
                checkout_owned=True,
            )
        # Every state save of the resume went through the native parent sink, never the ordinary writer.
        through_sink = [value for _disk, value in self.sink_saves]
        self.assertTrue(self.saved[saved_before:] == through_sink, "a state save bypassed the native parent sink")
        return code, admitted

    def assert_fresh_attempt(self, code, admitted, attempts, route, paused):
        saved = self.disk()
        message = (
            "Archived the pending Plan Reviewer report repair because the Plan Reviewer route changed from "
            f"{ROUTE['model']} ({ROUTE['reasoning_effort']}) to {route['model']} ({route['reasoning_effort']}), "
            "so the Plan Reviewer stage will run again as a fresh attempt on the new route."
        )
        # The native parent sink saved the archive: the one save that took the pending repair off disk.
        archived_by = [
            value
            for disk, value in self.sink_saves
            if "pending_report_repair" in disk and "pending_report_repair" not in value
        ]
        self.assertEqual(1, len(archived_by))
        self.assertEqual(message, archived_by[0]["reconciliation_notes"][-1]["reason"])
        # Admitted past native setup: the resume step keeps the pause until the later resume steps clear it.
        self.assertEqual((0, paused), (code, saved["status"]), saved.get("stop_reason"))
        self.assertEqual(1, len(admitted))
        self.assertNotIn("pending_report_repair", saved)
        self.assertEqual(("plan_review", route), (saved["next_stage"], saved["settings"]["roles"]["plan_reviewer"]))
        archived = saved["report_repair_archive"][-1]
        self.assertEqual("The repaired stage's route changed", archived["reason"])
        self.assertEqual(
            (attempts, dict(ROUTE)), (archived["repair"]["attempts"], archived["repair"]["original"]["launch_route"])
        )
        self.assertTrue(all(Path(row["output"]).is_file() for row in saved["stages"]))
        # The stage runs again as a fresh attempt; no repair of the archived one can launch.
        with self.assertRaises(util.Paused):
            self.calls.guard(saved, "plan_review", report_only=True, allow_write=False)

    def test_new_model_after_the_spent_repairs_runs_the_stage_afresh_on_it(self):
        self.queue()["attempts"] = 2
        with self.assertRaises(util.Paused):
            self.calls.reject(self.state, self.record(repair=True, name="r2"), ValueError("still invalid"), closed=True)
        self.assertEqual("PAUSED_INVALID_OUTPUT", self.disk()["status"])
        code, admitted = self.resume("--plan-reviewer-model", "openai/other-reviewer")
        self.assert_fresh_attempt(
            code, admitted, 2, dict(ROUTE, model="openai/other-reviewer"), "PAUSED_INVALID_OUTPUT"
        )

    def test_new_effort_at_a_pause_before_the_repair_runs_the_stage_afresh_on_it(self):
        self.queue()
        runner.run_status.pause(self.state, "PAUSED_REQUESTED", "Pause requested; previous stage saved")
        code, admitted = self.resume("--plan-reviewer-reasoning-effort", "low")
        self.assert_fresh_attempt(code, admitted, 0, dict(ROUTE, reasoning_effort="low"), "PAUSED_REQUESTED")

    def test_another_role_changed_keeps_the_pending_repair(self):
        self.queue()["attempts"] = 1
        runner.run_status.pause(self.state, "PAUSED_REQUESTED", "Pause requested; previous stage saved")
        code, admitted = self.resume("--requirements-model", "openai/other-requirements")
        saved = self.disk()
        self.assertEqual((0, 1), (code, saved["pending_report_repair"]["attempts"]))
        self.assertNotIn("report_repair_archive", saved)
        self.assertEqual("openai/other-requirements", saved["settings"]["roles"]["requirements"]["model"])
        self.assertEqual(1, len(admitted))


class AutomaticRecoveriesStayOrdinaryOnly(NativeRepairFixture):
    """The ordinary shortcuts around a repair never run under native authority."""

    def setUp(self):
        super().setUp()
        self.view = invocation.runner_view(runner, self.calls)

    def test_command_only_report_from_a_session_still_gets_the_full_bounded_repair(self):
        # The input an ordinary run corrects in the same session: a command-only final message
        # of an attempt that ran in a session thread.
        record = self.record()
        Path(record["events"]).write_text(
            json.dumps({"type": "thread.started", "thread_id": "th-1"})
            + "\n"
            + json.dumps({"type": "turn.completed"})
            + "\n"
        )
        with self.assertRaises(runner.ReportRepairQueued):
            self.calls.reject(self.state, record, ValueError(runner.cmd_only.ERROR), closed=True)
        pending = self.state["pending_report_repair"]
        self.assertEqual("th-1", runner.format_correction.session_for(self.state, pending, "plan_reviewer"))
        with patch.object(runner, "run_role", side_effect=RuntimeError("controlled launch stop")) as launch:
            with self.assertRaisesRegex(RuntimeError, "controlled launch stop"):
                self.view.execute_report_repair(self.state, self.run, self.root)
        launch.assert_called_once()
        self.assertIsNone(launch.call_args.kwargs.get("resume_session"))
        self.assertTrue(launch.call_args.kwargs["report_only"])
        self.assertNotIn("correction_attempted", pending)
        self.assertEqual(1, pending["attempts"])  # the counted, full report-only repair

    def timed_out_repair(self):
        """A repair launch that run_role stopped at its deadline, as it leaves the run."""
        self.queue()
        repair = self.record(repair=True, name="repair-timeout")
        repair.update(
            timed_out=True,
            exit_code=-15,
            accounted=True,
            pid=4242,
            supervision={"controlled": True},
            processes=[{"pid": 4242, "birth_identity": 1.0}],
            finished_at="2026-10-15T00:00:00Z",
            timeout_kind="stage",
            timeout_reason="Stage exceeded its 900-second hard runtime limit",
        )

        def timed_out(**_options):
            self.state["active_stage"] = repair
            raise util.Paused("PAUSED_PROVIDER_TIMEOUT", "plan_reviewer: " + repair["timeout_reason"])

        with patch.object(runner, "run_role", side_effect=timed_out), self.assertRaises(util.Paused) as caught:
            self.view.execute_report_repair(self.state, self.run, self.root)
        runner.run_status.pause(self.state, caught.exception.status, str(caught.exception))
        self.calls.sink.write(self.path, self.state)  # as run_setup.run_locked saves the pause
        return repair, caught.exception

    def test_timed_out_repair_keeps_its_pause_without_the_ordinary_automatic_retry(self):
        repair, paused = self.timed_out_repair()
        self.assertEqual("PAUSED_PROVIDER_TIMEOUT", paused.status)
        self.assertIs(repair, self.state["active_stage"])
        self.assertEqual(1, self.state["pending_report_repair"]["attempts"])
        self.assertNotIn("abandoned", repair)
        self.assertTrue(Path(repair["events"]).is_file())

    def abandon(self, selected):
        args = SimpleNamespace(
            run_dir=self.run,
            abandon_stage=selected,
            _native_action_argv=("--abandon-stage", selected, "--run-dir", str(self.run)),
        )
        self.calls.require_supported_action(args, self.state)  # before setup, as the CLI checks it
        self.calls.abandon_stage(self.state, self.run, self.root, selected)

    def test_stopped_repair_launch_is_abandoned_like_an_original_planning_stop(self):
        repair, _ = self.timed_out_repair()
        events = Path(repair["events"]).read_bytes()
        with self.assertRaisesRegex(util.Paused, "Original native worker reconciliation"):
            self.calls.require_supported_action(SimpleNamespace(run_dir=self.run), self.state)
        # The controlled journal owner returns the retained record; real authentication is tested below.
        self.factory.protection = Mock()
        self.factory.protection.planning_abandonment.side_effect = lambda state, record, **_: deepcopy(record)
        selected = "001/" + Path(repair["output"]).stem
        self.abandon(selected)
        self.assertEqual(2, self.factory.protection.planning_abandonment.call_count)  # checked, then committed
        self.assertEqual(("PAUSED_STAGE_ABANDONED", "plan_review"), (self.state["status"], self.state["next_stage"]))
        self.assertIn("report repair timeout retained", self.state["stop_reason"])
        self.assertNotIn("active_stage", self.state)
        self.assertEqual(1, self.state["pending_report_repair"]["attempts"])  # the stopped attempt stays spent
        abandoned = self.state["stages"][-1]
        self.assertEqual(
            (repair["output"], True, True), (abandoned["output"], abandoned["abandoned"], abandoned["rejected"])
        )
        self.assertEqual(selected, self.state["recovery_context"]["attempt_id"])
        self.assertEqual(events, Path(repair["events"]).read_bytes())
        self.assertEqual(self.state, self.disk())
        # A plain resume is admitted, and its locked load reconciles the abandoned repair the ordinary
        # way: the pending repair (with its counted attempt) is archived and the stage itself runs again.
        self.calls.require_supported_action(SimpleNamespace(run_dir=self.run), self.state)
        self.view.configure = Mock(side_effect=StopIteration("controlled stop after reconciliation"))
        args = SimpleNamespace(run_dir=self.run, resume_paused=True, expected_recovery_token=None)
        with self.assertRaisesRegex(StopIteration, "controlled stop"):
            runner.run_setup.load_locked(self.view, args, None, self.state, self.path, self.run, self.root)
        resumed = self.disk()
        self.assertNotIn("pending_report_repair", resumed)
        self.assertEqual(1, resumed["report_repair_archive"][-1]["repair"]["attempts"])
        self.calls.require_supported_action(SimpleNamespace(run_dir=self.run), resumed)
        self.calls.guard(resumed, "plan_review", allow_write=False)  # the original stage, not another repair

    def test_only_the_counted_launch_of_the_pending_repair_can_be_abandoned(self):
        repair, _ = self.timed_out_repair()
        self.factory.protection = Mock()
        selected = "001/" + Path(repair["output"]).stem
        changes = (
            ("uncounted", lambda s: s["pending_report_repair"].update(attempts=0)),
            ("no pending repair", lambda s: s.pop("pending_report_repair")),
            ("another stage", lambda s: s["active_stage"].update(original_stage="plan_revise")),
            ("another route", lambda s: s["active_stage"].update(route_role="glm")),
            ("not report-only", lambda s: s["active_stage"].pop("report_only")),
        )
        for label, change in changes:
            saved = deepcopy(self.state)
            change(self.state)
            with self.subTest(label), self.assertRaises(util.Paused) as caught:
                self.abandon(selected)
            self.assertEqual("PAUSED_VISUAL_EVIDENCE", caught.exception.status)
            self.state = saved
        self.factory.protection.planning_abandonment.assert_not_called()


class RejectedLaunchThroughRunRole(NativeRepairFixture):
    """run_role closes a rejected native launch only on its report file, and queues the repair only then."""

    def setUp(self):
        super().setUp()
        self.charge = Mock()  # the planning review allowance charge run_role makes before the provider starts

    def launch(self, *, report, error, report_only=False):
        self.state["sessions"] = {}
        self.state["settings"]["joint_planning"] = True  # as every native planning run saves it
        schema = self.run / "controlled.schema.json"
        schema.write_text("{}")
        self.factory.prepare_launch = Mock(return_value="controlled prepared")
        adapter = SimpleNamespace(
            CONFIGURED=False, OUTPUT="opencode_events", prompt_for_schema=lambda prompt, *_args: prompt
        )

        @contextmanager
        def held_launch(prepared, *, options, checkpoint, **_launch):
            # The provider's own stream: one completed turn in a session; a report file only if asked.
            self.assertEqual("controlled prepared", prepared)
            checkpoint({"provider": {"pid": 4242}, "owner": {"pid": 4243}})
            options["stdout"].write(
                json.dumps({"type": "thread.started", "thread_id": "th-1"})
                + "\n"
                + json.dumps({"type": "turn.completed"})
                + "\n"
            )
            options["stdout"].flush()
            if report:
                Path(self.state["active_stage"]["output"]).write_text('{"decision": "controlled"')
            yield SimpleNamespace(pid=4242, poll=lambda: 0), None

        self.calls.context.held_launch = held_launch
        seams = (
            # Setup and accounting around the launch; none of them decides native authority.
            (runner, "timeout_recovery_guard", Mock()),
            (runner.dispatch, "enforce_cross_model_verification", Mock()),
            (runner.planning_artifacts, "verify_predecessor", Mock()),
            (callers.isolation, "NativeRoleIsolation", Mock()),
            (runner.processes, "preflight", Mock()),
            (runner.processes, "interruption_handler", lambda: nullcontext()),
            (runner.role_transport, "session_adapter", Mock(return_value=(adapter, True))),
            (
                runner.provider_launch,
                "prepare",
                Mock(return_value=(["controlled"], {"PATH": "/usr/bin:/bin"}, {}, {"tool_containment": {}})),
            ),
            (runner.provider_launch, "containment_prompt", lambda prompt, *_args, **_kw: prompt),
            (runner.provider_launch, "stage_record", Mock(return_value={})),
            (runner.provider_launch, "verify_containment", Mock()),
            (runner.task_preflight, "guard", Mock()),
            (runner.readonly_events, "prepare_opencode_snapshots", Mock()),
            (runner.job_source, "prepare_capture_context", Mock()),
            (runner.job_source, "capture", Mock()),
            (runner.job_source, "stopped", Mock()),
            (runner.network_retry, "bind_attempt", Mock()),
            (runner.interventions, "admission", lambda _run: nullcontext()),
            (runner.resolver_recovery, "admit_dispatch", Mock()),
            (runner.planning, "charge", self.charge),
            (runner.resolver_runtime, "charge_diagnostic_dispatch", Mock()),
            (runner.progressive_state, "admit_attempt", Mock()),
            (runner.job_failure, "admit", Mock()),
            (runner.classification_recovery, "admit", Mock()),
            (runner.processes, "wait_for_stage", Mock(return_value=(0, False))),
            (runner.supervision, "receipt", Mock(return_value=None)),
            (runner.source_diff, "write", Mock()),
            # The report itself does not validate.
            (runner, "load_stage_report", Mock(side_effect=error)),
        )
        with ExitStack() as stack:
            for module, name, value in seams:
                stack.enter_context(patch.object(module, name, value))
            runner.run_role(
                role="plan_reviewer",
                prompt="Controlled plan review",
                sandbox="read-only",
                workspace=self.root,
                run_dir=self.run,
                state=self.state,
                schema=schema,
                model=ROUTE["model"],
                allow_write=False,
                dry_run=False,
                report_only=report_only,
                persist=self.calls.sink.write,
                native=self.calls,
            )

    def test_unparsed_final_message_without_a_report_file_keeps_the_original_pause(self):
        with self.assertRaisesRegex(util.Paused, "Original native output retained") as caught:
            self.launch(report=False, error=FORMAT_ERROR)
        self.assertEqual("PAUSED_INVALID_OUTPUT", caught.exception.status)
        self.factory.finish_launch.assert_not_called()  # the journal keeps the launch unclosed
        self.assertNotIn("pending_report_repair", self.state)
        self.assertTrue(self.state["stages"][-1]["rejected"])
        self.assertEqual("PAUSED_INVALID_OUTPUT", self.disk()["status"])

    def test_invalid_report_file_closes_the_launch_and_queues_its_repair(self):
        with self.assertRaises(runner.ReportRepairQueued):
            self.launch(report=True, error=MALFORMED)
        self.factory.finish_launch.assert_called_once()
        self.assertEqual("controlled prepared", self.factory.finish_launch.call_args.args[0])
        pending = self.state["pending_report_repair"]
        self.assertEqual((0, "plan_review"), (pending["attempts"], pending["original"]["stage"]))
        self.assertEqual(("RUNNING", "REPORT_REPAIR"), (self.disk()["status"], self.disk()["phase"]))

    def test_repair_launch_is_not_charged_to_the_review_allowance(self):
        with self.assertRaises(runner.ReportRepairQueued):
            self.launch(report=True, error=MALFORMED)
        self.assertEqual(["plan_review"], [call.args[1] for call in self.charge.call_args_list])  # the original
        self.state["pending_report_repair"]["attempts"] = 1  # execute_report_repair counts it before run_role
        with self.assertRaises(runner.ReportRepairQueued):
            self.launch(report=True, error=ValueError("repaired report still invalid"), report_only=True)
        self.assertEqual(1, self.charge.call_count)  # the report-only repair launch is not charged
        repair = self.state["stages"][-1]
        self.assertEqual(("plan_review_report_repair", True), (repair["stage"], repair["report_only"]))
        self.assertEqual(repair["output"], self.state["pending_report_repair"]["latest_rejected"]["output"])


class NoInvestigatorUnderNativeAuthority(NativeRepairFixture):
    """The native build loop sends no stage pause to the Investigator and restores one it refuses."""

    def run_native_loop(self, attempt):
        proxy = invocation.runner_view(runner, self.calls)
        proxy.consume_interventions = Mock(return_value=False)
        util.atomic_json(self.path, self.state)
        real_drive = runner.orchestrator.drive

        def drive(current, dispatch, **callbacks):
            # The build loop's own flags and persistence, with a controlled stage attempt.
            return real_drive(
                current,
                attempt,
                before=lambda value: self.calls.guard(value),
                persist=callbacks["persist"],
                investigate=callbacks["investigate"],
                queue=callbacks["queue"],
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
        return self.disk()

    def test_native_planning_pause_keeps_its_reason_without_spending_an_investigation(self):
        self.state["next_stage"] = "plan_finalize"

        def attempt(current, stage):
            # A fresh rejected final review, as native reject leaves it.
            current["stages"].append({"stage": stage, "rejected": True})
            raise util.Paused("PAUSED_INVALID_OUTPUT", "Original native output retained: controlled malformed report")

        saved = self.run_native_loop(attempt)
        self.assertEqual("PAUSED_INVALID_OUTPUT", saved["status"])
        self.assertEqual("plan_finalize", saved["next_stage"])
        self.assertNotIn("Investigator", saved["stop_reason"])
        self.assertNotIn("stuck_investigations", saved)
        self.assertNotIn("stuck_investigation", saved)

    def test_native_stage_that_ends_the_loop_paused_keeps_its_reason_without_an_investigation(self):
        # A stage can also record its own non-convergence pause and return: the loop then ends normally.
        self.state["planning"]["reports"] = {}  # the pause screen lists the planning reports
        reason = "Original native output retained: controlled repeated rejection"

        def attempt(current, stage):
            record = {"stage": stage, "rejected": True}
            current["stages"].append(record)
            runner.run_status.pause(current, "PAUSED_REPEATED_FAILURE", reason)
            return record

        saved = self.run_native_loop(attempt)
        self.assertEqual(("PAUSED_REPEATED_FAILURE", reason), (saved["status"], saved["stop_reason"]))
        self.assertEqual("plan_review", saved["next_stage"])
        self.assertNotIn("stuck_investigations", saved)
        self.assertNotIn("stuck_investigation", saved)

    def test_native_builder_failure_classification_is_restored_to_its_own_pause(self):
        # After approval a native run owns the Builder. A Builder that changed nothing is routed by
        # autopilot.route_builder_failure, which queues the Investigator to classify the failure.
        self.factory.first_writer_proof = Mock()
        self.state["settings"]["roles"]["terra"] = dict(ROUTE, model="controlled/builder")
        self.state.update(
            current_task={"id": "t1", "kind": "implement", "title": "Controlled task"},
            next_stage="terra",
            phase="IMPLEMENTATION",
        )
        reason = "Builder changed no files"

        def attempt(current, stage):
            self.assertEqual("terra", stage)
            output = self.run / "builder-01.json"
            output.write_text('{"status": "CONTINUE"}')
            record = {
                "stage": "terra",
                "role": "terra",
                "iteration": 1,
                "exit_code": 0,
                "output": str(output),
                "source_revision": self.snapshot["revision"],
                "started_at": "2026-10-15T00:00:00Z",
            }
            current["stages"].append(record)
            evidence = builder_failure.evidence(current, record)
            self.assertEqual("investigate", runner.autopilot.route_builder_failure(current, evidence, reason))
            self.assertEqual("investigate_stuck", current["next_stage"])
            return record

        saved = self.run_native_loop(attempt)
        # Native authority refused the Investigator: the run holds the Builder's own classification pause.
        self.assertEqual("PAUSED_BUILDER_CLASSIFICATION", saved["status"])
        self.assertEqual("terra", saved["next_stage"])
        self.assertTrue(saved["stop_reason"].startswith(reason))
        self.assertEqual("PAUSED_BUILDER_CLASSIFICATION", saved["builder_failure_hold"]["status"])
        self.assertNotIn("stuck_investigation", saved)
        self.assertEqual(["investigation_failed"], [row["outcome"] for row in saved["stuck_investigations"]])
        self.calls.require_supported_action(SimpleNamespace(run_dir=self.run), saved)  # a resume is admitted


REPAIR_LAUNCH = {"stage": "plan_revise_report_repair", "original_stage": "plan_revise", "report_only": True}


def pend_repair(case):
    """Make the fixture's stopped launch the counted launch of the run's pending repair."""
    original = {key: deepcopy(case.record[key]) for key in ("iteration", "role", "route_role", "engine")}
    original.update(stage="plan_revise", rejected=True, exit_code=0, source_revision=case.raw["revision"])
    case.state["pending_report_repair"] = {"original": original, "attempts": 1, "pins": {}, "contract_hash": None}
    util.atomic_json(case.state_path, case.state)


def owner_refuses(case, reason):
    """The original journal owner itself refuses the saved stopped launch and records nothing.

    The CLI's admission (stage_recovery.request) refuses such a launch first, so it is skipped
    here: the owner's own check is what decides.
    """
    record = deepcopy(case.state["active_stage"])
    intent = protection.stage_recovery.PlanningAbandonIntent(
        "001/plan-revise-01", frozenset({"plan_revise"}), util.digest(record)
    )
    before = case.journal_bytes()
    with (
        patch.object(protection.stage_recovery, "constructor_record", return_value=record),
        patch.object(protection.processes, "live_processes", side_effect=case.observe),
        case.assertRaisesRegex(util.Paused, reason),
    ):
        case.construct(intent=intent)
    case.assertEqual(before, case.journal_bytes())


class StoppedRepairLaunchClosesThroughTheOriginalOwner(unittest.TestCase):
    """The real native journal owner closes a stopped repair launch as it closes an original stop."""

    fixture = abandon_constructor_tests.NativeAbandonConstructorTests
    public_cli = fixture.public_cli
    observe = fixture.observe
    journal_bytes = fixture.journal_bytes
    construct = fixture.construct
    collected_length = fixture.collected_length
    record_overrides = REPAIR_LAUNCH

    def setUp(self):
        self.fixture.setUp(self)  # a released, stopped report-only launch in the real journal
        pend_repair(self)

    def abandon(self):
        before = self.journal_bytes()
        events = Path(self.record["events"]).read_bytes()
        with patch.object(taskrun, "run_process", side_effect=self.public_cli):
            view = self.driver.abandon_stage("001/plan-revise-01")
        self.assertEqual(("PAUSED_STAGE_ABANDONED", "plan_revise"), (view["status"], view["next_stage"]))
        self.assertNotEqual(before, self.journal_bytes())  # the owner recorded the close
        self.assertEqual(events, Path(self.record["events"]).read_bytes())
        self.assertNotIn("active_stage", self.state)
        self.assertEqual(1, self.state["pending_report_repair"]["attempts"])
        abandoned = self.state["stages"][-1]
        self.assertEqual(
            ("plan_revise_report_repair", True, True),
            (abandoned["stage"], abandoned["abandoned"], abandoned["rejected"]),
        )
        return abandoned

    def test_timed_out_repair_launch_is_closed_by_its_original_owner(self):
        self.assertIn("stopped native report repair timeout", self.abandon()["rejection_reason"])
        with patch.object(protection.processes, "live_processes", side_effect=self.observe):
            self.construct()  # the original journal still replays with the closed repair launch

    def test_collected_output_limit_of_a_repair_launch_is_closed_by_its_original_owner(self):
        self.collected_length()
        self.assertIn("collected native report repair output limit", self.abandon()["rejection_reason"])
        with patch.object(protection.processes, "live_processes", side_effect=self.observe):
            self.construct()

    def test_uncounted_repair_launch_is_refused_before_the_journal(self):
        self.state["pending_report_repair"]["attempts"] = 0
        util.atomic_json(self.state_path, self.state)
        before = self.journal_bytes()
        with patch.object(taskrun, "run_process", side_effect=self.public_cli):
            with self.assertRaises(taskrun.TaskRunError):
                self.driver.abandon_stage("001/plan-revise-01")
        self.assertEqual(before, self.journal_bytes())
        self.assertIn("active_stage", self.state)

    def test_owner_refuses_a_report_only_output_limit_that_is_not_the_pending_repair_launch(self):
        self.collected_length()
        original = deepcopy(self.state)
        cases = {
            "no pending repair": lambda state: state.pop("pending_report_repair"),
            "uncounted": lambda state: state["pending_report_repair"].update(attempts=0),
            "another stage's repair": lambda state: state["pending_report_repair"]["original"].update(
                stage="plan_review"
            ),
        }
        for label, change in cases.items():
            with self.subTest(label):
                self.state = deepcopy(original)
                change(self.state)
                owner_refuses(self, "output limit is not an original collected planning stop")


class HeldRepairLaunchCancelsThroughTheOriginalOwner(unittest.TestCase):
    """A repair launch held at a failed cleanup is cancelled as an original held launch is."""

    fixture = cleanup_cancellation_tests.NativeHeldCancellationTests
    public_cli = fixture.public_cli
    observe = fixture.observe
    journal_bytes = fixture.journal_bytes
    construct = fixture.construct
    intent = fixture.intent
    append_original = fixture.append_original
    cancel = fixture.abandon
    record_overrides = REPAIR_LAUNCH

    def setUp(self):
        self.fixture.setUp(self)  # PAUSED_PROCESS_CLEANUP with an unreleased, held report-only launch
        pend_repair(self)

    def test_held_repair_launch_is_cancelled_by_its_original_owner(self):
        before = self.journal_bytes()
        view = self.cancel()
        self.assertEqual(("PAUSED_STAGE_ABANDONED", "plan_revise"), (view["status"], view["next_stage"]))
        self.assertNotEqual(before, self.journal_bytes())
        self.assertNotIn("active_stage", self.state)
        self.assertEqual(1, self.state["pending_report_repair"]["attempts"])
        abandoned = self.state["stages"][-1]
        self.assertEqual("plan_revise_report_repair", abandoned["stage"])
        self.assertIn("cancelled native report repair launch", abandoned["rejection_reason"])
        with patch.object(protection.processes, "live_processes", side_effect=self.observe):
            self.construct()  # the original journal still replays with the cancelled repair launch


class HeldReportOnlyLaunchOutsideARepairStage(unittest.TestCase):
    """The original owner cancels a held report-only launch only on a report-repair stage."""

    fixture = cleanup_cancellation_tests.NativeHeldCancellationTests
    observe = fixture.observe
    journal_bytes = fixture.journal_bytes
    construct = fixture.construct
    record_overrides = {"original_stage": "plan_revise", "report_only": True}  # on plan_revise itself

    def setUp(self):
        self.fixture.setUp(self)  # PAUSED_PROCESS_CLEANUP with an unreleased, held report-only launch
        pend_repair(self)

    def test_held_report_only_launch_of_the_original_stage_is_refused(self):
        owner_refuses(self, "not a collected original deadline-cancelled planning stop")


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
