"""Public recovery controls with a controlled native journal, never model authority."""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode as runner
import autocode_action_recovery as actions
import autocode_native_calls as native
import autocode_native_invocation as invocation
import autocode_taskrun as taskrun
import autocode_util as util
import autocode_visual_history as history
import autocode_visual_protection as protection


class NativeAbandonFixture:
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native-abandon-public-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.run = self.workspace / ".autocode/runs/current"
        self.run.mkdir(parents=True)
        self.events = self.run / "plan-review-01.jsonl"
        self.events.write_text('{"type":"partial","text":"retained"}\n')
        self.snapshot = {"revision": "a" * 64, "files": {}}
        self.state = {
            "version": 3,
            "workspace": str(self.workspace),
            "task": "unchanged initial input",
            "status": "PAUSED_PROVIDER_TIMEOUT",
            "next_stage": "plan_review",
            "phase": "PLANNING",
            "iteration": 1,
            "stages": [],
            "history": [],
            "sessions": {"mimo": "old-session"},
            "settings": {
                "planning_flow": "v2",
                "roles": {"mimo": {"model": "original/mimo", "reasoning_effort": "high"}},
                "limits": {"stage_timeout_seconds": 900, "idle_timeout_seconds": 300},
            },
            "goal_contract": {
                "approval_status": "draft",
                "task_id": "controlled",
                "revision": 1,
                "body": {},
                "hash": "b" * 64,
            },
            "current_task": None,
            "active_stage": {
                "iteration": 1,
                "stage": "plan_review",
                "role": "astra",
                "route_role": "mimo",
                "events": str(self.events),
                "output": str(self.run / "plan-review-01.json"),
                "finished_at": "2026-10-14T00:00:00Z",
                "exit_code": -15,
                "pid": 301,
                "timed_out": True,
                "accounted": True,
                "source_revision": self.snapshot["revision"],
                "supervision": {"controlled": True},
            },
            "total_seconds": 341.5,
        }
        self.calls = object.__new__(native.PlanningCalls)
        self.calls.initial_task = self.state["task"]
        self.calls.stages = frozenset({"plan_review", "requirements", "plan", "plan_revise", "plan_finalize"})
        self.calls.run_dir, self.calls.workspace = self.run, self.workspace
        self.calls.snapshot = lambda _state: deepcopy(self.snapshot)
        self.calls.sink = SimpleNamespace(require_ready=lambda: None, write=self.save)
        self.protection = Mock()
        self.protection.planning_abandonment.side_effect = self.authenticate
        self.calls.context = SimpleNamespace(factory=SimpleNamespace(protection=self.protection))
        self.task = taskrun.TaskRun(self.workspace, self.run, command=("controlled-public-cli",))

    def authenticate(self, _state, record, *, snapshot, commit):
        # Controlled native journal seam; actual owner verification is covered
        # separately, and this fixture never grants live/native authority.
        retained = deepcopy(record)
        retained["after_ref"] = str(self.run / "plan-review-01.after.json")
        return retained

    def save(self, _path, state):
        # This is the original test-owned sink; no actual run checkpoint is read.
        self.saved = deepcopy(state)

    def public_cli(self, command, **_options):
        argv = command[1:]
        args = runner.cli_args.build_parser(None, runner.opencode.DEFAULT_MODELS).parse_args(argv)
        args._native_action_argv = tuple(argv)
        output = io.StringIO()
        try:
            if args.status:
                return subprocess.CompletedProcess(
                    command,
                    0,
                    json.dumps({"view": runner.run_view.view(self.state, run_dir=self.run, workspace=self.workspace)}),
                    "",
                )
            with contextlib.redirect_stdout(output):
                self.calls.require_supported_action(args, self.state)
                runtime = invocation.runner_view(runner, self.calls)
                result = actions.resume(
                    runtime,
                    args,
                    None,
                    self.state,
                    self.run / "state.json",
                    self.run,
                    self.workspace,
                    SimpleNamespace(gate=None, retained_report=None, information_admitted=False),
                )
            return subprocess.CompletedProcess(command, result, output.getvalue(), "")
        except (util.Paused, ValueError) as error:
            return subprocess.CompletedProcess(command, 2, "Input rejected: " + str(error), "")


class NativeAbandonPublicTests(NativeAbandonFixture, unittest.TestCase):
    def test_public_abandon_stopped_native_planning_retains_evidence_without_dispatch(self):
        original = self.events.read_bytes()
        settings = deepcopy(self.state["settings"])
        with (
            patch.object(taskrun, "run_process", side_effect=self.public_cli),
            patch.object(runner, "run_role", side_effect=AssertionError("abandonment dispatched a provider")),
        ):
            view = self.task.abandon_stage("001/plan-review-01")
        self.assertEqual("PAUSED_STAGE_ABANDONED", view["status"])
        self.assertEqual("plan_review", view["next_stage"])
        self.assertEqual(original, self.events.read_bytes())
        self.assertEqual(settings, self.state["settings"])
        self.assertEqual(341.5, self.state["total_seconds"])

    def test_public_abandon_unparsed_launch_retains_evidence_without_dispatch(self):
        self.state.update(status="PAUSED_INVALID_OUTPUT")
        self.state["active_stage"].update(engine="opencode", timed_out=False, exit_code=0, rejected=True)
        original = self.events.read_bytes()
        settings = deepcopy(self.state["settings"])
        with (
            patch.object(taskrun, "run_process", side_effect=self.public_cli),
            patch.object(runner, "run_role", side_effect=AssertionError("abandonment dispatched a provider")),
        ):
            view = self.task.abandon_stage("001/plan-review-01")
        self.assertEqual("PAUSED_STAGE_ABANDONED", view["status"])
        self.assertEqual("plan_review", view["next_stage"])
        self.assertEqual(original, self.events.read_bytes())
        self.assertEqual(settings, self.state["settings"])
        self.assertEqual(341.5, self.state["total_seconds"])
        self.assertIn("unparsed", self.state["stages"][-1]["rejection_reason"])

    def test_public_abandon_refuses_wrong_attempt_postapproval_and_uncertain_exit(self):
        for changed in ("selected", "approved", "exit", "pid", "interrupted", "unaccounted", "stage"):
            with self.subTest(changed=changed):
                before = deepcopy(self.state)
                selected = "001/plan-review-01"
                if changed == "selected":
                    selected = "001/another-stage"
                if changed == "approved":
                    self.state["goal_contract"]["approval_status"] = "approved"
                if changed == "exit":
                    self.state["active_stage"]["exit_code"] = None
                if changed == "pid":
                    self.state["active_stage"]["pid"] = None
                if changed == "interrupted":
                    self.state["active_stage"]["interrupted"] = True
                if changed == "unaccounted":
                    self.state["active_stage"]["accounted"] = False
                if changed == "stage":
                    self.state["active_stage"]["stage"] = "terra"
                rejected = deepcopy(self.state)
                with patch.object(taskrun, "run_process", side_effect=self.public_cli):
                    with self.assertRaises(taskrun.TaskRunError):
                        self.task.abandon_stage(selected)
                self.assertEqual(rejected, self.state)
                self.protection.planning_abandonment.assert_not_called()
                self.state = before

    def test_public_abandon_refuses_mixed_resume_feedback_model_and_budget_flags(self):
        for extra in (
            ("--resume-paused",),
            ("--feedback", "technical correction"),
            ("--sol-model", "different/model"),
            ("--max-seconds", "9999"),
            ("--approve-goal", "a-token"),
            ("--accept-source-edit",),
        ):
            with self.subTest(extra=extra):
                before = deepcopy(self.state)
                with patch.object(taskrun, "run_process", side_effect=self.public_cli):
                    with self.assertRaises(taskrun.TaskRunError):
                        self.task._act("abandon", "--abandon-stage", "001/plan-review-01", *extra)
                self.assertEqual(before, self.state)
                self.protection.planning_abandonment.assert_not_called()

    def test_public_abandon_keeps_failed_owner_authentication_closed(self):
        for reason in ("source changed", "original root still live", "uncertain supervisor", "mismatched root"):
            with self.subTest(reason=reason):
                before = deepcopy(self.state)
                self.protection.planning_abandonment.side_effect = util.Paused("HELD", reason)
                with patch.object(taskrun, "run_process", side_effect=self.public_cli):
                    with self.assertRaises(taskrun.TaskRunError):
                        self.task.abandon_stage("001/plan-review-01")
                self.assertEqual(before, self.state)
                self.assertFalse(hasattr(self, "saved"))


class OriginalTimeoutOwnerTests(NativeAbandonFixture, unittest.TestCase):
    """Real receipt/pin comparisons over controlled journal data, no provider."""

    def setUp(self):
        super().setUp()
        self.before = self.run / "plan-review-01.before.json"
        self.after = self.run / "plan-review-01.after.json"
        util.atomic_json(self.before, self.snapshot)
        root = {"pid": 301, "birth_identity": 1000000001.0, "group": 301}
        metadata = {
            "schema": 1,
            "nonce": "a" * 32,
            "provider": root,
            "owner": {"pid": 302, "birth_identity": 1000000002.0},
            "keeper": {"pid": 303, "birth_identity": 1000000003.0},
            "receipt": str(self.run / "supervision.json"),
        }
        self.receipt = {
            **metadata,
            "phase": "stopped",
            "cause": "provider_stopped",
            "cleanup_error": None,
            "processes": [root],
        }
        util.atomic_json(Path(metadata["receipt"]), self.receipt)
        self.record = deepcopy(self.state["active_stage"])
        self.record.update(before_ref=str(self.before), supervision=metadata)
        original = history.record_identity(self.record)
        original["after_ref"] = str(self.after)
        self.binding = {
            "source": deepcopy(self.snapshot),
            "task": None,
            "settings": deepcopy(self.state["settings"]),
            "runtime_sha256": "a" * 64,
            "contract": deepcopy(self.state["goal_contract"]),
        }
        self.body = {
            "binding": self.binding,
            "record_identity": original,
            "grants": [],
            "boundary": {"allow_write": False},
        }
        self.seal = {"root": root, "barrier": {"supervision_envelope": {"supervision": deepcopy(metadata)}}}
        self.rows = [{"kind": "origin", "body": {"source": deepcopy(self.snapshot)}}]
        self.owner = object.__new__(protection.NativeProtection)
        self.owner._active = self.owner._pending = self.owner._active_checks = self.owner._pending_checks = {}
        self.owner._ownership = Mock()
        self.owner._chain = lambda: (
            self.rows,
            {"original": {"body": self.body}},
            {"original": {"body": deepcopy(self.seal)}},
            {"original": {}},
            {},
        )
        self.owner._launch_binding = lambda _state, _snapshot: deepcopy(self.binding)
        self.owner._fresh_snapshot = lambda _rows=None: deepcopy(self.snapshot)
        self.owner._safe = lambda name, **_kw: Path(name)
        self.owner._append = Mock()
        self.owner._pins = lambda pins: [
            self.assertEqual(digest, util.file_hash(name)) for name, digest in pins.items()
        ]

    def test_original_receipt_closes_only_selected_rejected_root(self):
        events = self.events.read_bytes()
        with patch.object(protection.processes, "live_processes", return_value=[]):
            result = self.owner.planning_abandonment(self.state, self.record, snapshot=self.snapshot, commit=True)
        self.assertTrue(result["rejected"])
        self.assertTrue(result["timed_out"])
        self.assertEqual(events, self.events.read_bytes())
        row = self.owner._append.call_args.args[1]
        self.assertEqual("original", row["prepared_id"])
        self.assertEqual([], row["possible_writes"])
        self.assertIn(self.record["supervision"]["receipt"], row["pins"])

    def test_live_wrong_root_uncertain_source_and_settings_fail_before_write(self):
        for case in (
            "live",
            "owner-live",
            "wrong-root",
            "saved-root",
            "wrong-owner",
            "wrong-keeper",
            "wrong-nonce",
            "sealed-metadata",
            "uncertain",
            "cleanup",
            "source",
            "settings",
            "writer",
            "stable-record",
        ):
            with self.subTest(case=case):
                receipt = deepcopy(self.receipt)
                record = deepcopy(self.record)
                body = deepcopy(self.body)
                sealed = deepcopy(self.seal)
                if case == "wrong-root":
                    receipt["provider"] = {**receipt["provider"], "birth_identity": 1000000020.0}
                if case == "saved-root":
                    record["pid"] = 399
                if case in ("wrong-owner", "wrong-keeper"):
                    key = case.removeprefix("wrong-")
                    receipt[key] = {"pid": 399, "birth_identity": 1000000030.0}
                    record["supervision"][key] = deepcopy(receipt[key])
                if case == "wrong-nonce":
                    receipt["nonce"] = record["supervision"]["nonce"] = "c" * 32
                if case == "sealed-metadata":
                    sealed["barrier"]["supervision_envelope"]["supervision"]["nonce"] = "c" * 32
                if case == "uncertain":
                    receipt["phase"] = "uncertain"
                if case == "cleanup":
                    receipt["cleanup_error"] = "failed"
                if case == "source":
                    body["binding"]["source"]["revision"] = "b" * 64
                if case == "settings":
                    body["binding"]["settings"]["limits"]["idle_timeout_seconds"] = 600
                if case == "writer":
                    body["grants"] = ["ui.py"]
                if case == "stable-record":
                    record["role"] = "another-role"
                self.owner._chain = lambda: (
                    self.rows,
                    {"original": {"body": body}},
                    {"original": {"body": sealed}},
                    {"original": {}},
                    {},
                )
                util.atomic_json(Path(record["supervision"]["receipt"]), receipt)

                def live(rows):
                    if case == "live":
                        return [self.receipt["provider"]]
                    owner = self.receipt["owner"]
                    return [owner] if case == "owner-live" and owner in rows else []

                with patch.object(protection.processes, "live_processes", side_effect=live):
                    with self.assertRaises(util.Paused):
                        self.owner.planning_abandonment(self.state, record, snapshot=self.snapshot, commit=True)
                self.owner._append.assert_not_called()
                self.assertFalse(self.after.exists())
