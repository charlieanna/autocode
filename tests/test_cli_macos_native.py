"""CLI-level macOS-compatible native planning recovery tests (#1257).

Covers CLI entrypoint invocations for:
1. Malformed final plan review (plan_finalize) report-only repair, verifying
   repaired report acceptance and native planning publication before approval.
2. Operator abandonment (--abandon-stage) of unparsed OpenCode planning launches,
   retaining launch events and allowing clean resume back to the planning stage.
"""

import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode as runner
import autocode_action_recovery as recovery_actions
import autocode_checkout_lock as locks
import autocode_native_invocation as invocation
import autocode_native_launch as launch
import autocode_native_stage_recovery as recovery
import autocode_supervision as supervision
import autocode_taskrun as taskrun
import autocode_util as util
import autocode_visual_profile as profiles
import autocode_visual_protection as protection
from units import autoplanner as planning

PROFILE = {
    "version": 1,
    "mode": "isolated-builtin-openai",
    "opencode_version": "1.18.33",
    "model": "openai/controlled",
    "reasoning_effort": "high",
    "sdk_path": ".autocode/sdk",
    "sdk_manifest_sha256": "a" * 64,
    "transport_identity_sha256": "b" * 64,
    "executable_sha256": "c" * 64,
    "max_requests": 8,
    "image_limits": dict(profiles.IMAGE_BOUNDS),
}
MALFORMED = ValueError("Agent did not produce valid JSON: Extra data: line 1 column 1830")


class CliMacOsNativeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cli-macos-native-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / ".gitignore").write_text(".autocode/\n")
        (self.root / "source.txt").write_text("controlled original source\n")
        for args in (
            ("init", "-q"),
            ("add", "."),
            ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "fixture"),
        ):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)
        self.run = self.root / ".autocode/runs/controlled"
        self.run.mkdir(parents=True)
        self.state_path = self.run / "state.json"
        self.raw = util.snapshot(self.root)
        self.task_text = "Controlled native planning recovery task\n" + profiles.MARKER + json.dumps(PROFILE)
        self.activation = {"task": self.task_text, "profile": deepcopy(PROFILE)}
        self.state = {
            "version": 3,
            "workspace": str(self.root),
            "task": self.task_text,
            "phase": "PLANNING",
            "iteration": 1,
            "next_stage": "plan_finalize",
            "stages": [],
            "history": [],
            "sessions": {"glm": "controlled-session"},
            "planning": {"astra_calls": 1, "review_call_limit": 2},
            "settings": {
                "joint_planning": True,
                "planning_flow": "v2",
                "report_repair": {"max_attempts": 2},
                "engine": "opencode",
                "provider": "opencode",
                "roles": {
                    "plan_reviewer": {
                        "engine": "opencode",
                        "provider": "opencode",
                        "model": "controlled/reviewer",
                        "reasoning_effort": "high",
                    },
                    "glm": {
                        "engine": "opencode",
                        "provider": "opencode",
                        "model": "controlled/planner",
                        "reasoning_effort": "high",
                    },
                    "astra": {
                        "engine": "opencode",
                        "provider": "opencode",
                        "model": "controlled/astra",
                        "reasoning_effort": "high",
                    },
                },
                "limits": {"stage_timeout_seconds": 900, "idle_timeout_seconds": 300},
            },
            "goal_contract": {
                "approval_status": "draft",
                "task_id": "controlled",
                "revision": 1,
                "body": {"constraints": [profiles.MARKER + json.dumps(PROFILE)]},
                "hash": util.digest(
                    {
                        "task_id": "controlled",
                        "revision": 1,
                        "body": {"constraints": [profiles.MARKER + json.dumps(PROFILE)]},
                    }
                ),
            },
            "current_task": None,
            "next_stage": "plan_finalize",
            "total_seconds": 15.0,
        }
        util.atomic_json(self.state_path, self.state)
        self.driver = taskrun.TaskRun(self.root, self.run, command=("controlled-public-cli",))

    def public_cli(self, command, **_options):
        if "--status" in command:
            return subprocess.CompletedProcess(
                command,
                0,
                json.dumps({"view": runner.run_view.view(self.state, run_dir=self.run, workspace=self.root)}),
                "",
            )
        argv = command[1:]
        args = runner.cli_args.build_parser(None, runner.opencode.DEFAULT_MODELS).parse_args(argv)
        args._native_action_argv = tuple(argv)

        def action(_base, _args, _parser, state, _state_path, run, workspace, *, native, **_kwargs):
            if args.abandon_stage is not None:
                native.abandon_stage(state, run, workspace, args.abandon_stage)
            else:
                recovery_actions.resume(
                    _base,
                    args,
                    None,
                    state,
                    _state_path,
                    run,
                    workspace,
                    SimpleNamespace(gate=None, retained_report=None, information_admitted=False),
                )
                if state.get("pending_report_repair"):
                    _base.execute_report_repair(state, run, workspace)
            self.state = state
            return 0

        try:
            with (
                patch.object(runner.support, "assert_no_legacy_process"),
                patch.object(invocation, "select_provider"),
                patch.object(runner.run_setup, "run_locked", side_effect=action),
                patch.object(supervision, "launch", side_effect=AssertionError("uncontrolled provider dispatched")),
            ):
                code = invocation.run(runner, args, None, (self.root, self.run, self.state_path, deepcopy(self.state)))
            return subprocess.CompletedProcess(command, code, "", "")
        except (util.Paused, ValueError) as error:
            return subprocess.CompletedProcess(command, 2, "Input rejected: " + str(error), "")

    def test_cli_plan_finalize_report_repair_and_publication(self):
        """CLI-level plan_finalize report repair queues repair and commits native publication."""
        with ExitStack() as stack:
            run_handle = stack.enter_context(util.run_lock(self.run))
            checkout_handle = stack.enter_context(locks.exclusive(self.root, self.run))
            context = launch.NativeRunContext(
                self.root,
                self.run,
                run_handle=run_handle,
                checkout_handle=checkout_handle,
                start_state=self.state,
                snapshot=self.raw,
                activation_intent=self.activation,
            )
            owner = context.factory.protection
            binary = str(Path(sys.executable).resolve())
            before = self.run / "iterations/001/plan-finalize-01.before.json"
            after_ref = self.run / "iterations/001/plan-finalize-01.after.json"
            schema = self.run / "iterations/001/plan-finalize-01.schema.json"
            before.parent.mkdir(parents=True, exist_ok=True)
            util.atomic_json(before, self.raw)
            util.atomic_json(after_ref, self.raw)
            schema.write_text('{"type": "object"}\n')
            record = {
                "iteration": 1,
                "stage": "plan_finalize",
                "role": "plan_reviewer",
                "route_role": "plan_reviewer",
                "command": [binary, "-I", "-S", "-B", "-c", "pass"],
                "engine": "opencode",
                "expected_session": "controlled-session",
                "launch_route": deepcopy(self.state["settings"]["roles"]["plan_reviewer"]),
                "events": str(self.run / "iterations/001/plan-finalize-01.jsonl"),
                "output": str(self.run / "iterations/001/plan-finalize-01.json"),
                "before_ref": str(before),
                "after_ref": str(after_ref),
                "schema": str(schema),
                "exit_code": 0,
                "timed_out": False,
                "interrupted": False,
                "planning": True,
                "source_revision": self.raw["revision"],
                "contract_hash": self.state["goal_contract"]["hash"],
            }
            Path(record["events"]).write_text('{"type":"turn.completed"}\n')
            Path(record["output"]).write_text('{"decision": "malformed"')
            manifest = self.run / "controlled-isolation.json"
            manifest.write_text("{}\n")
            descriptor = {
                "version": 1,
                "files": {},
                "manifest": str(manifest),
                "manifest_sha256": util.file_hash(manifest),
                "child_identity": {"executable": binary, "executable_sha256": util.file_hash(binary)},
            }
            with patch.object(
                owner, "_native_config", return_value=({str(manifest): util.file_hash(manifest)}, descriptor)
            ):
                prepared = context.factory.prepare_launch(
                    self.state,
                    self.raw,
                    record,
                    boundary={"allow_write": False, "write_paths": []},
                    command=[binary, "-I", "-S", "-B", "-c", "pass"],
                    environment={"PATH": "/usr/bin:/bin"},
                    native_role_isolation=None,
                )
            root = {"pid": 501, "birth_identity": 1000000001.0, "group": 501}
            metadata = {
                "schema": 1,
                "nonce": "f" * 32,
                "provider": root,
                "owner": {"pid": 502, "birth_identity": 1000000002.0},
                "keeper": {"pid": 503, "birth_identity": 1000000003.0},
                "receipt": str(self.run / "iterations/001/plan-finalize-01.supervision.json"),
            }
            receipt = {
                **metadata,
                "phase": "stopped",
                "cause": "controller_finished",
                "cleanup_error": None,
                "processes": [root],
            }
            util.atomic_json(Path(metadata["receipt"]), receipt)
            owner._append(
                "sealed",
                {
                    "prepared_id": prepared.row_id,
                    "root": root,
                    "barrier": {"supervision_envelope": {"supervision": deepcopy(metadata)}},
                },
            )
            owner._append("released", {"prepared_id": prepared.row_id})
            owner._active[prepared.nonce] = (
                prepared,
                SimpleNamespace(returncode=0),
                SimpleNamespace(known={}),
            )
            Path(record["output"]).write_text("{ broken json")

            # Reject the launch and queue the bounded report repair
            after = self.raw
            native = invocation.native_calls.PlanningCalls(
                context,
                PROFILE,
                run_dir=self.run,
                workspace=self.root,
                initial_task=self.task_text,
                write=util.atomic_json,
                snapshot=lambda _s: self.raw,
                planning_stages=frozenset({"plan_finalize"}),
                recognizer="recognize_workflow",
            )
            with self.assertRaises(runner.ReportRepairQueued):
                native.reject_launch(self.state, record, MALFORMED, prepared, after)

            self.assertEqual("RUNNING", self.state["status"])
            self.assertEqual("REPORT_REPAIR", self.state["phase"])
            pending = self.state.get("pending_report_repair")
            self.assertIsNotNone(pending)
            self.assertEqual("plan_finalize", pending["original"]["stage"])

            # Execute resume through CLI entrypoint
            planning_dir = self.run / "planning"
            planning_dir.mkdir(parents=True, exist_ok=True)
            for name in ("artifact.json", "delta.json", "graph.json"):
                (planning_dir / name).write_text("{}\n")

            repair_output = self.run / "iterations/001/plan-finalize-report-repair-01.json"
            repair_events = self.run / "iterations/001/plan-finalize-report-repair-01.jsonl"
            repair_before = self.run / "iterations/001/plan-finalize-report-repair-01.before.json"
            repair_after = self.run / "iterations/001/plan-finalize-report-repair-01.after.json"
            contract_body = self.state["goal_contract"]["body"]
            report_data = {"contract": contract_body}
            repair_output.write_text(json.dumps(report_data) + "\n")
            event_lines = [
                {"type": "step_start", "sessionID": "controlled-session", "part": {"id": "p1", "messageID": "m1"}},
                {
                    "type": "text",
                    "sessionID": "controlled-session",
                    "part": {"id": "p2", "messageID": "m1", "text": json.dumps(report_data)},
                },
                {
                    "type": "step_finish",
                    "sessionID": "controlled-session",
                    "part": {"id": "p3", "messageID": "m1", "reason": "stop"},
                },
            ]
            repair_events.write_text("".join(json.dumps(e) + "\n" for e in event_lines))
            util.atomic_json(repair_before, self.raw)
            util.atomic_json(repair_after, self.raw)
            repair_record = {
                "iteration": 1,
                "stage": "plan_finalize_report_repair",
                "original_stage": "plan_finalize",
                "role": "plan_reviewer",
                "route_role": "plan_reviewer",
                "expected_session": "controlled-session",
                "launch_route": deepcopy(self.state["settings"]["roles"]["plan_reviewer"]),
                "command": [binary, "-I", "-S", "-B", "-c", "pass"],
                "engine": "opencode",
                "output": str(repair_output),
                "events": str(repair_events),
                "schema": str(schema),
                "before_ref": str(repair_before),
                "after_ref": str(repair_after),
                "source_revision": self.raw["revision"],
                "contract_hash": self.state["goal_contract"]["hash"],
                "report_only": True,
                "exit_code": 0,
            }

            with patch.object(
                owner, "_native_config", return_value=({str(manifest): util.file_hash(manifest)}, descriptor)
            ):
                repair_prepared = context.factory.prepare_launch(
                    self.state,
                    self.raw,
                    repair_record,
                    boundary={"allow_write": False, "write_paths": []},
                    command=[binary, "-I", "-S", "-B", "-c", "pass"],
                    environment={"PATH": "/usr/bin:/bin"},
                    native_role_isolation=None,
                )
            repair_root = {"pid": 601, "birth_identity": 1000000004.0, "group": 601}
            repair_meta = {
                "schema": 1,
                "nonce": "e" * 32,
                "provider": repair_root,
                "owner": {"pid": 602, "birth_identity": 1000000005.0},
                "keeper": {"pid": 603, "birth_identity": 1000000006.0},
                "receipt": str(self.run / "iterations/001/plan-finalize-report-repair-01.supervision.json"),
            }
            util.atomic_json(
                Path(repair_meta["receipt"]),
                {
                    **repair_meta,
                    "phase": "stopped",
                    "cause": "controller_finished",
                    "cleanup_error": None,
                    "processes": [repair_root],
                },
            )
            owner._append(
                "sealed",
                {
                    "prepared_id": repair_prepared.row_id,
                    "root": repair_root,
                    "barrier": {"supervision_envelope": {"supervision": deepcopy(repair_meta)}},
                },
            )
            owner._append("released", {"prepared_id": repair_prepared.row_id})
            owner._active[repair_prepared.nonce] = (
                repair_prepared,
                SimpleNamespace(returncode=0),
                SimpleNamespace(known={}),
            )
            context.factory.finish_launch(repair_prepared, repair_record, self.raw)

        def apply(state, stage, value, record, workspace, run_dir, *, native):
            runner.save_record(state, record)
            state["status"] = "AWAITING_GOAL_APPROVAL"
            token = runner.lifecycle.token(state["goal_contract"])
            state["planning_final"] = {
                "artifact": {
                    "path": "planning/artifact.json",
                    "sha256": util.file_hash(planning_dir / "artifact.json"),
                },
                "delta": {
                    "path": "planning/delta.json",
                    "sha256": util.file_hash(planning_dir / "delta.json"),
                },
                "graph": {
                    "path": "planning/graph.json",
                    "sha256": util.file_hash(planning_dir / "graph.json"),
                },
                "final_token": token,
            }
            state.setdefault("planning", {})["final_token"] = token

        with (
            patch.object(runner, "run_role", return_value=(report_data, repair_record)),
            patch.object(runner, "apply_result", side_effect=apply),
            patch.object(taskrun, "run_process", side_effect=self.public_cli),
        ):
            self.driver.resume_paused()

        disk = json.loads(self.state_path.read_text())
        self.assertNotIn("pending_report_repair", disk)
        self.assertEqual("accepted", disk["report_repair_history"][-1]["result"])
        self.assertEqual("plan_finalize", disk["report_repair_history"][-1]["repair"]["original_stage"])
        journal = [
            json.loads(path.read_text())
            for path in sorted((self.run / "visual-native-protection").glob("*.json"))
        ]
        kinds = [row["kind"] for row in journal]
        self.assertIn("planning-committed", kinds)

    def test_cli_abandon_unparsed_opencode_launch_and_resume(self):
        """CLI-level --abandon-stage closes unparsed OpenCode launch and enables clean resume."""
        with ExitStack() as stack:
            run_handle = stack.enter_context(util.run_lock(self.run))
            checkout_handle = stack.enter_context(locks.exclusive(self.root, self.run))
            context = launch.NativeRunContext(
                self.root,
                self.run,
                run_handle=run_handle,
                checkout_handle=checkout_handle,
                start_state=self.state,
                snapshot=self.raw,
                activation_intent=self.activation,
            )
            owner = context.factory.protection
            binary = str(Path(sys.executable).resolve())
            before = self.run / "iterations/001/plan-revise-01.before.json"
            before.parent.mkdir(parents=True, exist_ok=True)
            util.atomic_json(before, self.raw)
            record = {
                "iteration": 1,
                "stage": "plan_revise",
                "role": "glm",
                "route_role": "glm",
                "command": [binary, "-I", "-S", "-B", "-c", "pass"],
                "engine": "opencode",
                "expected_session": "controlled-session",
                "launch_route": deepcopy(self.state["settings"]["roles"]["glm"]),
                "events": str(self.run / "iterations/001/plan-revise-01.jsonl"),
                "output": str(self.run / "iterations/001/plan-revise-01.json"),
                "before_ref": str(before),
            }
            Path(record["events"]).write_text('{"type":"partial","text":"unparsed stream"}\n')
            # output file is unparsed and does not exist
            manifest = self.run / "controlled-isolation.json"
            manifest.write_text("{}\n")
            descriptor = {
                "version": 1,
                "files": {},
                "manifest": str(manifest),
                "manifest_sha256": util.file_hash(manifest),
                "child_identity": {"executable": binary, "executable_sha256": util.file_hash(binary)},
            }
            with patch.object(
                owner, "_native_config", return_value=({str(manifest): util.file_hash(manifest)}, descriptor)
            ):
                prepared = context.factory.prepare_launch(
                    self.state,
                    self.raw,
                    record,
                    boundary={"allow_write": False, "write_paths": []},
                    command=[binary, "-I", "-S", "-B", "-c", "pass"],
                    environment={"PATH": "/usr/bin:/bin"},
                    native_role_isolation=None,
                )
            root = {"pid": 601, "birth_identity": 1000000001.0, "group": 601}
            metadata = {
                "schema": 1,
                "nonce": "e" * 32,
                "provider": root,
                "owner": {"pid": 602, "birth_identity": 1000000002.0},
                "keeper": {"pid": 603, "birth_identity": 1000000003.0},
                "receipt": str(self.run / "iterations/001/plan-revise-01.supervision.json"),
            }
            receipt = {
                **metadata,
                "phase": "stopped",
                "cause": "controller_finished",
                "cleanup_error": None,
                "processes": [root],
            }
            util.atomic_json(Path(metadata["receipt"]), receipt)
            owner._append(
                "sealed",
                {
                    "prepared_id": prepared.row_id,
                    "root": root,
                    "barrier": {"supervision_envelope": {"supervision": deepcopy(metadata)}},
                },
            )
            owner._append("released", {"prepared_id": prepared.row_id})

            prepared_body = protection._json(prepared.body)
            record.update(
                source_revision=self.raw["revision"],
                after_ref=prepared_body["record_identity"]["after_ref"],
                pid=601,
                supervision=deepcopy(metadata),
                processes=[root],
                timed_out=False,
                accounted=True,
                exit_code=0,
                finished_at="2026-10-14T00:00:00Z",
                rejected=True,
                rejection_reason="Output stream ended without valid JSON",
            )
            self.state.update(
                next_stage="plan_revise",
                status="PAUSED_INVALID_OUTPUT",
                active_stage=deepcopy(record),
            )
            util.atomic_json(self.state_path, self.state)

        with patch.object(taskrun, "run_process", side_effect=self.public_cli):
            view = self.driver.abandon_stage("001/plan-revise-01")

        self.assertEqual("PAUSED_STAGE_ABANDONED", view["status"])
        self.assertEqual("plan_revise", view["next_stage"])
        self.assertEqual("PAUSED_STAGE_ABANDONED", self.state["status"])
        self.assertNotIn("active_stage", self.state)
        last = self.state["stages"][-1]
        self.assertTrue(last["rejected"])
        self.assertTrue(last["abandoned"])
        self.assertIn("unparsed", last["rejection_reason"])

        # Resume explicitly without dispatch
        with patch.object(taskrun, "run_process", side_effect=self.public_cli):
            self.driver.resume_paused()
