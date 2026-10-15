"""Future-origin held cancellation through original constructors and public task actions.

Controlled supervisor I/O only; no live provider, current-run access or migration.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import autocode_checkout_lock as locks
import autocode_native_launch as launch
import autocode_native_stage_recovery as recovery
import autocode_supervision as supervision
import autocode_taskrun as taskrun
import autocode_util as util
import autocode_visual_profile as profiles
import autocode_visual_protection as protection

from tests import test_native_abandon_constructor as fixtures


class NativeCleanupEligibilityTests(unittest.TestCase):
    def test_cleanup_deadline_receives_exact_nonadvancing_constructor_intent(self):
        fixture = fixtures.NativeAbandonConstructorTests(
            "test_public_collected_length_abandon_preserves_original_caps_and_rejected_tail"
        )
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.state["status"] = "PAUSED_PROCESS_CLEANUP"
        fixture.state["active_stage"].update(timeout_kind="idle", timeout_reason="Original controlled deadline")
        before = deepcopy(fixture.state)
        intent = fixture.intent()
        self.assertIs(type(intent), recovery.PlanningAbandonIntent)
        self.assertEqual(util.digest(before["active_stage"]), intent.record_sha256)
        self.assertEqual(before, fixture.state)


class NativeHeldCancellationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="native-abandon-constructor-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / ".gitignore").write_text(".autocode/\n")
        (self.root / "source.txt").write_text("original controlled source\n")
        for args in (
            ("init", "-q"),
            ("add", "."),
            ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "fixture"),
        ):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)
        self.run = self.root / ".autocode/runs/controlled"
        self.run.mkdir(parents=True)
        self.raw = util.snapshot(self.root)
        profile = {
            "version": 1,
            "mode": "isolated-builtin-openai",
            "opencode_version": "1.18.33",
            "model": "openai/controlled",
            "reasoning_effort": "high",
            "sdk_path": ".autocode/sdk",
            "sdk_manifest_sha256": "a" * 64,
            "transport_identity_sha256": "b" * 64,
            "executable_sha256": "c" * 64,
            "max_requests": 2,
            "image_limits": {"width": 320, "height": 240, "pixels": 76800, "bytes": 1048576},
        }
        self.task_text = "Controlled initial scope\n" + profiles.MARKER + json.dumps(profile)
        self.activation = {"task": self.task_text, "profile": profile}
        self.state = {
            "version": 3,
            "workspace": str(self.root),
            "task": self.task_text,
            "goal_contract": None,
            "current_task": None,
            "stages": [],
            "history": [],
            "user_events": [],
            "iteration": 1,
            "status": "RUNNING",
            "next_stage": "plan_revise",
            "phase": "PLANNING",
            "settings": {
                "planning_flow": "v2",
                "roles": {
                    "glm": {
                        "engine": "opencode",
                        "provider": "opencode",
                        "model": "controlled/model",
                        "reasoning_effort": "high",
                    }
                },
                "limits": {
                    "max_seconds": 7200,
                    "stage_timeout_seconds": 900,
                    "tool_timeout_seconds": 600,
                    "idle_timeout_seconds": 300,
                },
            },
            "sessions": {"glm": "controlled-session"},
            "total_seconds": 900.0,
            "planning": {
                "astra_calls": 1,
                "review_call_limit": 2,
                "reports": {},
                "final_token": None,
                "v2_cycle": {
                    "boundary": {"clarification_input": None, "program_guidance": None},
                    "requirements_artifact_token": "requirements:controlled",
                },
            },
        }
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
            before = self.run / "plan-revise-01.before.json"
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
                "events": str(self.run / "plan-revise-01.jsonl"),
                "output": str(self.run / "plan-revise-01.json"),
                "before_ref": str(before),
            }
            Path(record["events"]).write_text('{"type":"partial","text":"controlled retained evidence"}\n')
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
            self.prepared_body = protection._json(prepared.body)
            self.original_root = {"pid": 401, "birth_identity": 1000000001.0, "group": 401}
            metadata = {
                "schema": 1,
                "nonce": "d" * 32,
                "provider": self.original_root,
                "owner": {"pid": os.getpid(), "birth_identity": 1000000002.0},
                "keeper": {"pid": 403, "birth_identity": 1000000003.0},
                "receipt": str(self.run / "plan-revise-01.supervision.json"),
            }
            self.metadata = metadata
            self.receipt = {
                **metadata,
                "phase": "stopped",
                "cause": "stage_deadline",
                "cleanup_error": None,
                "processes": [self.original_root],
            }
            armed = {**metadata, "phase": "armed", "cleanup_error": None, "processes": [self.original_root]}
            util.atomic_json(Path(metadata["receipt"]), armed)
            self.held_id = owner._append(
                "planning-held",
                {
                    "prepared_id": prepared.row_id,
                    "supervision": deepcopy(metadata),
                    "root": deepcopy(metadata["provider"]),
                    "command_sha256": self.prepared_body["command_sha256"],
                    "environment_sha256": self.prepared_body["environment_sha256"],
                },
            )["id"]
            self.original_context = context
            self.live_prepared = prepared
            self.receipt["cause"] = "provider_stopped"
            util.atomic_json(Path(metadata["receipt"]), self.receipt)
            self.original_owner = owner
            self.record = deepcopy(record)
            self.record.update(
                source_revision=self.raw["revision"],
                after_ref=self.prepared_body["record_identity"]["after_ref"],
                pid=401,
                supervision=deepcopy(metadata),
                processes=[self.original_root],
                timed_out=True,
                accounted=True,
                exit_code=-15,
                finished_at="2026-10-14T00:00:00Z",
            )
        self.record.update(timeout_kind="idle", timeout_reason="Original controlled activity deadline fired")
        self.state.update(status="PAUSED_PROCESS_CLEANUP", active_stage=self.record)
        self.state_path = self.run / "state.json"
        util.atomic_json(self.state_path, self.state)
        self.driver = taskrun.TaskRun(self.root, self.run, command=("controlled-public-cli",))

    public_cli = fixtures.NativeAbandonConstructorTests.public_cli
    observe = fixtures.NativeAbandonConstructorTests.observe
    journal_bytes = fixtures.NativeAbandonConstructorTests.journal_bytes
    construct = fixtures.NativeAbandonConstructorTests.construct
    intent = fixtures.NativeAbandonConstructorTests.intent

    def append_original(self, kind, body):
        with ExitStack() as stack:
            rh = stack.enter_context(util.run_lock(self.run))
            wh = stack.enter_context(locks.exclusive(self.root, self.run))
            self.original_owner.handles = ((rh, self.run / "writer.lock"), (wh, self.root / ".autocode/writer.lock"))
            return self.original_owner._append(kind, body)

    def abandon(self):
        with patch.object(taskrun, "run_process", side_effect=self.public_cli):
            return self.driver.abandon_stage("001/plan-revise-01")

    def test_public_abandon_closes_only_rejected_original_held_tail(self):
        before = deepcopy(self.state)
        events = Path(self.record["events"]).read_bytes()
        view = self.abandon()
        self.assertEqual("PAUSED_STAGE_ABANDONED", view["status"])
        self.assertEqual("plan_revise", view["next_stage"])
        for key in ("settings", "planning", "total_seconds"):
            self.assertEqual(before[key], self.state[key])
        self.assertEqual(events, Path(self.record["events"]).read_bytes())
        archived = self.state["stages"][-1]
        self.assertTrue(archived["rejected"] and archived["abandoned"])
        self.assertEqual(-15, archived["exit_code"])
        self.assertTrue(archived["timed_out"])
        self.assertEqual(before["active_stage"]["timeout_kind"], archived["timeout_kind"])
        self.assertFalse(Path(archived["output"]).exists())
        with ExitStack() as stack, patch.object(protection.processes, "live_processes", side_effect=self.observe):
            rh = stack.enter_context(util.run_lock(self.run))
            wh = stack.enter_context(locks.exclusive(self.root, self.run))
            context = launch.NativeRunContext(
                self.root,
                self.run,
                run_handle=rh,
                checkout_handle=wh,
                start_state=self.state,
                snapshot=self.raw,
                activation_intent=self.activation,
            )
            rows, prepared, sealed, released, closed = context.factory.protection._chain()
            self.assertEqual({}, sealed)
            self.assertEqual({}, released)
            self.assertEqual(["planning-cancelled"], [r["kind"] for r in closed.values()])
            context.factory.protection._source_continuity(self.raw)
            with self.assertRaises(util.Paused):
                context.factory.protection._finished_for(protection.history.record_identity(archived))
        with self.assertRaises(taskrun.TaskRunError):
            self.abandon()

    def test_separate_explicit_resume_retains_frontier_route_and_caps(self):
        before = deepcopy(self.state)
        self.abandon()
        with patch.object(taskrun, "run_process", side_effect=self.public_cli):
            self.driver.resume_paused()
        self.assertEqual(before["settings"], self.state["settings"])
        self.assertEqual(before["planning"], self.state["planning"])
        self.assertEqual(before["total_seconds"], self.state["total_seconds"])
        self.assertEqual("plan_revise", self.resume_admission["record"]["stage"])
        self.assertEqual("64000", self.resume_admission["environment"]["OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"])

    def test_missing_original_held_admission_is_not_backfilled(self):
        directory = self.run / "visual-native-protection"
        tail = sorted(directory.iterdir())[-1]
        self.assertEqual("planning-held", json.loads(tail.read_bytes())["kind"])
        tail.unlink()  # This owned fixture now has the historical origin+prepared gap.
        before = self.journal_bytes()
        with self.assertRaises(taskrun.TaskRunError):
            self.abandon()
        self.assertEqual(before, self.journal_bytes())
        self.assertFalse(Path(self.record["after_ref"]).exists())

    def test_original_supervisor_mismatches_uncertainty_and_scope_refuse_before_write(self):
        original, receipt = deepcopy(self.state), deepcopy(self.receipt)
        for case in (
            "nonce",
            "owner",
            "keeper",
            "provider",
            "live",
            "receipt-phase",
            "cleanup",
            "source",
            "settings",
            "writer",
            "approved",
            "unaccounted",
            "untyped-timeout",
        ):
            self.state, altered = deepcopy(original), deepcopy(receipt)
            record = self.state["active_stage"]
            if case == "nonce":
                record["supervision"]["nonce"] = altered["nonce"] = "e" * 32
            elif case in ("owner", "keeper", "provider"):
                altered[case]["birth_identity"] += 1
                record["supervision"][case] = deepcopy(altered[case])
            elif case == "live":
                self.matching_live = [self.metadata["owner"]]
            elif case == "receipt-phase":
                altered["phase"] = "uncertain"
            elif case == "cleanup":
                altered["cleanup_error"] = "controlled failure"
            elif case == "source":
                (self.root / "source.txt").write_text("changed controlled source\n")
            elif case == "settings":
                self.state["settings"]["roles"]["glm"]["model"] = "changed/model"
            elif case == "writer":
                self.state["current_task"] = {"kind": "implement"}
            elif case == "approved":
                self.state["goal_contract"] = {"approval_status": "approved"}
            elif case == "unaccounted":
                record["accounted"] = False
            else:
                record["timed_out"] = False
            util.atomic_json(Path(self.metadata["receipt"]), altered)
            util.atomic_json(self.state_path, self.state)
            before = self.journal_bytes()
            with self.subTest(case=case), self.assertRaises(taskrun.TaskRunError):
                self.abandon()
            self.assertEqual(before, self.journal_bytes())
            self.assertFalse(Path(self.record["after_ref"]).exists())
            self.matching_live = []
            (self.root / "source.txt").write_text("original controlled source\n")
        self.state = original
        util.atomic_json(self.state_path, self.state)
        util.atomic_json(Path(self.metadata["receipt"]), receipt)

    def test_released_root_is_not_cancelled_and_mixed_actions_are_not_admitted(self):
        for extra in (["--resume-paused"], ["--glm-model", "other/model"], ["--max-seconds", "9999"]):
            before = self.journal_bytes()
            with self.subTest(extra=extra), patch.object(taskrun, "run_process", side_effect=self.public_cli):
                with self.assertRaises(taskrun.TaskRunError):
                    self.driver._act("abandon", "--abandon-stage", "001/plan-revise-01", *extra)
            self.assertEqual(before, self.journal_bytes())
        self.append_original(
            "sealed",
            {
                "prepared_id": self.live_prepared.row_id,
                "root": self.original_root,
                "barrier": {"supervision_envelope": {"supervision": deepcopy(self.metadata)}},
            },
        )
        self.append_original("released", {"prepared_id": self.live_prepared.row_id})
        before = self.journal_bytes()
        with self.assertRaises(taskrun.TaskRunError):
            self.abandon()
        self.assertEqual(before, self.journal_bytes())

    def test_terminal_stop_cannot_replace_original_record_facts(self):
        import autocode_native_cancellation as cancellation

        self.abandon()
        with ExitStack() as stack, patch.object(protection.processes, "live_processes", side_effect=self.observe):
            rh = stack.enter_context(util.run_lock(self.run))
            wh = stack.enter_context(locks.exclusive(self.root, self.run))
            context = launch.NativeRunContext(
                self.root,
                self.run,
                run_handle=rh,
                checkout_handle=wh,
                start_state=self.state,
                snapshot=self.raw,
                activation_intent=self.activation,
            )
            rows, prepared, _, _, closed = context.factory.protection._chain()
            terminal = next(iter(closed.values()))["body"]
            held = next(row for row in rows if row["kind"] == "planning-held")
            original = next(iter(prepared.values()))["body"]
            for key, value in [("exit_code", -9), ("timed_out", False), ("finished_at", "changed")]:
                altered = deepcopy(terminal)
                altered["stop"][key] = value
                with self.subTest(key=key), self.assertRaisesRegex(util.Paused, "contradicts"):
                    cancellation.check_terminal(
                        altered, original, held, released=False, check_record=protection.check_finished_record
                    )
            for key, record_value, stop_value in [("timed_out", 1, True), ("exit_code", True, 1)]:
                altered = deepcopy(terminal)
                altered["record"][key], altered["stop"][key] = record_value, stop_value
                with self.subTest(type_alias=key), self.assertRaisesRegex(util.Paused, "contradicts"):
                    cancellation.check_terminal(
                        altered, original, held, released=False, check_record=protection.check_finished_record
                    )
            forged = deepcopy(terminal)
            forged["stop"]["exit_code"] = forged["record"]["exit_code"] = 0
            with self.assertRaises(util.Paused):
                cancellation.check_terminal(
                    forged, original, held, released=False, check_record=protection.check_finished_record
                )

    def test_actual_supervisor_issues_held_identity_before_on_root_without_target_release(self):
        directory = self.run / "visual-native-protection"
        sorted(directory.iterdir())[-1].unlink()  # Remove only this fixture's controlled held row.
        Path(self.metadata["receipt"]).unlink()
        with ExitStack() as stack:
            rh = stack.enter_context(util.run_lock(self.run))
            wh = stack.enter_context(locks.exclusive(self.root, self.run))
            context = self.original_context
            context._run_handle, context._checkout_handle = rh, wh
            context.factory.protection.handles = (
                (rh, self.run / "writer.lock"),
                (wh, self.root / ".autocode/writer.lock"),
            )
            environment = {"PATH": "/usr/bin:/bin"}
            child = None
            try:
                with context.held_launch(
                    self.live_prepared,
                    command=self.record["command"],
                    environment=environment,
                    options={
                        "cwd": self.root,
                        "env": environment,
                        "start_new_session": True,
                        "stdin": subprocess.DEVNULL,
                        "stdout": subprocess.DEVNULL,
                        "stderr": subprocess.DEVNULL,
                    },
                    receipt_path=Path(self.metadata["receipt"]),
                    timeout=15,
                ) as (child, _on_root):
                    # No on_root or release token: the original WriterBarrier still blocks the target.
                    rows = context.factory.protection._rows()
                    held = rows[-1]
                    self.assertEqual("planning-held", held["kind"])
                    self.assertEqual(child.supervision, held["body"]["supervision"])
                    self.assertEqual(child.pid, held["body"]["root"]["pid"])
                    self.assertNotIn("released", [r["kind"] for r in rows])
                    child.terminate()
                    child.wait(timeout=5)
                stopped = supervision.receipt(child.supervision)
                self.assertEqual("stopped", stopped["phase"])
                self.assertIsNone(stopped.get("cleanup_error"))
            except protection.processes.ProcessError as error:
                receipt = supervision.receipt(child.supervision) if child is not None else None
                self.fail(
                    "controlled issuer cleanup: "
                    + json.dumps(
                        {
                            "phase": (receipt or {}).get("phase"),
                            "cause": (receipt or {}).get("cause"),
                            "cleanup_error": (receipt or {}).get("cleanup_error"),
                            "supervision_errors": getattr(child, "supervision_errors", []),
                            "original_error_type": type(error).__name__,
                        }
                    )
                )

    def test_original_stage_deadline_needs_matching_retained_stage_decision(self):
        self.receipt["cause"] = "stage_deadline"
        util.atomic_json(Path(self.metadata["receipt"]), self.receipt)
        before = self.journal_bytes()
        with self.assertRaises(taskrun.TaskRunError):
            self.abandon()  # Original idle decision cannot borrow a stage-deadline cause.
        self.assertEqual(before, self.journal_bytes())
        self.state["active_stage"]["timeout_kind"] = "stage"
        util.atomic_json(self.state_path, self.state)
        view = self.abandon()
        self.assertEqual("PAUSED_STAGE_ABANDONED", view["status"])
        self.assertEqual("stage", self.state["stages"][-1]["timeout_kind"])
        self.assertEqual(900, self.state["settings"]["limits"]["stage_timeout_seconds"])

    def test_sealed_but_unreleased_root_closes_as_cancellation_without_release(self):
        self.append_original(
            "sealed",
            {
                "prepared_id": self.live_prepared.row_id,
                "root": self.original_root,
                "barrier": {"supervision_envelope": {"supervision": deepcopy(self.metadata)}},
            },
        )
        self.abandon()
        kinds = [json.loads(b)["kind"] for b in self.journal_bytes().values()]
        self.assertIn("planning-cancelled", kinds)
        self.assertNotIn("released", kinds)
        self.assertNotIn("finished", kinds)


if __name__ == "__main__":
    unittest.main()
