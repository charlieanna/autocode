"""Original constructor entry for explicit recovery; controlled transport only."""

import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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


class NativeAbandonConstructorTests(unittest.TestCase):
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
                **getattr(self, "record_overrides", {}),
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
                # Actual supervisor admission includes SID; ProcessTree.capture_root omits it.
                "provider": {**self.original_root, "session": self.original_root["pid"]},
                "owner": {"pid": 402, "birth_identity": 1000000002.0},
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
            util.atomic_json(Path(metadata["receipt"]), self.receipt)
            owner._append(
                "sealed",
                {
                    "prepared_id": prepared.row_id,
                    "root": self.original_root,
                    "barrier": {"supervision_envelope": {"supervision": deepcopy(metadata)}},
                },
            )
            owner._append("released", {"prepared_id": prepared.row_id})
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
        self.state.update(status="PAUSED_PROVIDER_TIMEOUT", active_stage=self.record)
        self.state_path = self.run / "state.json"
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
                native.guard(state, state["next_stage"], allow_write=False)
                binary = str(Path(sys.executable).resolve())
                before = run / "plan-revise-02.before.json"
                util.atomic_json(before, self.raw)
                record = {
                    "iteration": 1,
                    "stage": state["next_stage"],
                    "role": "glm",
                    "route_role": "glm",
                    "engine": "opencode",
                    "expected_session": None,
                    "command": [binary, "-I", "-S", "-B", "-c", "pass"],
                    "launch_route": deepcopy(state["settings"]["roles"]["glm"]),
                    "events": str(run / "plan-revise-02.jsonl"),
                    "output": str(run / "plan-revise-02.json"),
                    "before_ref": str(before),
                }
                environment = {"PATH": "/usr/bin:/bin", "OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX": "64000"}
                manifest = run / "controlled-resume-isolation.json"
                manifest.write_text("{}\n")
                descriptor = {
                    "version": 1,
                    "files": {},
                    "manifest": str(manifest),
                    "manifest_sha256": util.file_hash(manifest),
                    "child_identity": {"executable": binary, "executable_sha256": util.file_hash(binary)},
                }
                with patch.object(
                    native.context.factory.protection,
                    "_native_config",
                    return_value=({str(manifest): util.file_hash(manifest)}, descriptor),
                ):
                    prepared = native.context.factory.prepare_launch(
                        state,
                        self.raw,
                        record,
                        boundary={"allow_write": False, "write_paths": []},
                        command=record["command"],
                        environment=environment,
                        native_role_isolation=None,
                    )
                self.resume_admission = {
                    "record": deepcopy(record),
                    "environment": deepcopy(environment),
                    "prepared": protection._json(prepared.body),
                }
                with patch.object(
                    supervision, "launch", side_effect=RuntimeError("resume launch admission tripwire")
                ) as admitted:
                    with self.assertRaisesRegex(RuntimeError, "resume launch admission tripwire"):
                        with native.context.held_launch(
                            prepared,
                            command=record["command"],
                            environment=environment,
                            options={
                                "cwd": workspace,
                                "env": environment,
                                "start_new_session": True,
                                "stdin": subprocess.DEVNULL,
                                "stdout": subprocess.DEVNULL,
                                "stderr": subprocess.DEVNULL,
                            },
                            receipt_path=run / "resume.supervision.json",
                            timeout=5,
                        ):
                            self.fail("controlled transport must stop before process creation")
                    admitted.assert_called_once()
            self.state = state
            return 0

        try:
            with (
                patch.object(runner.support, "assert_no_legacy_process"),
                patch.object(invocation, "select_provider"),
                patch.object(runner.run_setup, "run_locked", side_effect=action),
                patch.object(protection.processes, "live_processes", side_effect=self.observe) as observed,
                patch.object(supervision, "launch", side_effect=AssertionError("provider dispatched")),
            ):
                code = invocation.run(runner, args, None, (self.root, self.run, self.state_path, deepcopy(self.state)))
                self.observed_originals = [call.args[0] for call in observed.call_args_list]
            return subprocess.CompletedProcess(command, code, "", "")
        except (util.Paused, ValueError) as error:
            return subprocess.CompletedProcess(command, 2, "Input rejected: " + str(error), "")

    def observe(self, originals):
        self.assertTrue(any(row["pid"] == self.metadata["owner"]["pid"] for row in originals))
        return self.matching_live if hasattr(self, "matching_live") else []

    def journal_bytes(self):
        return {p.name: p.read_bytes() for p in (self.run / "visual-native-protection").iterdir()}

    def construct(self, *, intent=None, state=None, verify_idle=False):
        current = self.state if state is None else state
        with ExitStack() as stack:
            run_handle = stack.enter_context(util.run_lock(self.run))
            checkout_handle = stack.enter_context(locks.exclusive(self.root, self.run))
            context = launch.NativeRunContext(
                self.root,
                self.run,
                run_handle=run_handle,
                checkout_handle=checkout_handle,
                start_state=current,
                snapshot=util.snapshot(self.root),
                activation_intent=self.activation,
                **({"planning_abandonment_intent": intent} if intent is not None else {}),
            )
            if verify_idle:
                with self.assertRaisesRegex(util.Paused, "actual launch/check crash gap remains"):
                    context.factory.protection._source_continuity(self.raw)
            return context

    def intent(self):
        args = SimpleNamespace(
            abandon_stage="001/plan-revise-01",
            _native_action_argv=[
                "--abandon-stage",
                "001/plan-revise-01",
                "--workspace",
                str(self.root),
                "--run-dir",
                str(self.run),
            ],
        )
        return recovery.constructor_intent(args, self.state, {"plan_revise"})

    def collected_length(self):
        rows = [
            {"type": "step_start", "sessionID": "controlled-session", "part": {"id": "start"}},
            {
                "type": "step_finish",
                "sessionID": "controlled-session",
                "part": {
                    "id": "finish",
                    "reason": "length",
                    "tokens": {"input": 1, "output": 33, "reasoning": 63967, "cache": {"read": 0, "write": 0}},
                },
            },
        ]
        Path(self.record["events"]).write_text("".join(json.dumps(row) + "\n" for row in rows))
        self.record.update(timed_out=False, exit_code=0, output_token_cap={"tokens": 64000, "set_by": "autocode"})
        self.record.pop("source_revision", None)
        self.record.pop("after_ref", None)
        self.receipt["cause"] = "provider_stopped"
        util.atomic_json(Path(self.metadata["receipt"]), self.receipt)
        self.state.update(status="PAUSED_OUTPUT_CAP", active_stage=self.record)
        util.atomic_json(self.state_path, self.state)

    def authentication_rejection(self):
        error = {
            "type": "error",
            "timestamp": 1,
            "sessionID": "controlled-session",
            "error": {
                "name": "APIError",
                "data": {"message": "Invalid API Key", "statusCode": 401, "isRetryable": False},
            },
        }
        Path(self.record["events"]).write_text(json.dumps(error) + "\n")
        self.record.update(timed_out=False, exit_code=1)
        self.receipt["cause"] = "provider_stopped"
        util.atomic_json(Path(self.metadata["receipt"]), self.receipt)
        self.state.update(status="PAUSED_PROVIDER_UNCERTAIN", active_stage=self.record)
        util.atomic_json(self.state_path, self.state)

    def unparsed_launch(self):
        self.record.update(timed_out=False, exit_code=0, rejected=True)
        self.receipt["cause"] = "controller_finished"
        util.atomic_json(Path(self.metadata["receipt"]), self.receipt)
        self.state.update(status="PAUSED_INVALID_OUTPUT", active_stage=self.record)
        util.atomic_json(self.state_path, self.state)

    def test_public_unparsed_launch_retains_history_and_same_stage_resume(self):
        self.unparsed_launch()
        before = deepcopy(self.state)
        events = Path(self.record["events"]).read_bytes()
        with patch.object(taskrun, "run_process", side_effect=self.public_cli):
            view = self.driver.abandon_stage("001/plan-revise-01")
        self.assertEqual("PAUSED_STAGE_ABANDONED", view["status"])
        self.assertEqual("plan_revise", view["next_stage"])
        self.assertEqual(before["settings"], self.state["settings"])
        self.assertEqual(before["planning"], self.state["planning"])
        self.assertEqual(before["total_seconds"], self.state["total_seconds"])
        self.assertEqual(events, Path(self.record["events"]).read_bytes())
        rejected = self.state["stages"][-1]
        self.assertTrue(rejected["rejected"])
        self.assertTrue(rejected["abandoned"])
        self.assertEqual(0, rejected["exit_code"])
        self.assertFalse(rejected["timed_out"])
        self.assertIn("unparsed", rejected["rejection_reason"])
        self.assertFalse(Path(rejected["output"]).exists())
        with patch.object(taskrun, "run_process", side_effect=self.public_cli):
            self.driver.resume_paused()

    def test_public_authentication_rejection_retains_history_and_same_stage_resume(self):
        self.authentication_rejection()
        before = deepcopy(self.state)
        events = Path(self.record["events"]).read_bytes()
        with patch.object(taskrun, "run_process", side_effect=self.public_cli):
            view = self.driver.abandon_stage("001/plan-revise-01")
        self.assertEqual("PAUSED_STAGE_ABANDONED", view["status"])
        self.assertEqual("plan_revise", view["next_stage"])
        self.assertEqual(before["settings"], self.state["settings"])
        self.assertEqual(before["planning"], self.state["planning"])
        self.assertEqual(before["total_seconds"], self.state["total_seconds"])
        self.assertEqual(events, Path(self.record["events"]).read_bytes())
        rejected = self.state["stages"][-1]
        self.assertTrue(rejected["rejected"])
        self.assertEqual(1, rejected["exit_code"])
        self.assertFalse(rejected["timed_out"])
        self.assertIn("authentication", rejected["rejection_reason"])
        self.assertFalse(Path(rejected["output"]).exists())
        with patch.object(taskrun, "run_process", side_effect=self.public_cli):
            self.driver.resume_paused()

    def test_authentication_rejection_refuses_mixed_forged_and_incomplete_transport(self):
        self.authentication_rejection()
        events = Path(self.record["events"])
        original = json.loads(events.read_bytes())
        alternatives = {
            "model text before error": json.dumps({"type": "text", "part": {"text": "done"}})
            + "\n"
            + json.dumps(original),
            "model turn after error": json.dumps(original) + '\n{"type":"step_finish"}',
            "incomplete": json.dumps(original)[:-2],
            "duplicate status": json.dumps(original).replace(
                '"statusCode": 401', '"statusCode": 500, "statusCode": 401'
            ),
            "invalid utf8": b"\xff",
            "oversize": b" " * 65537,
            "excessive nesting": b"[" * 1200 + b"0" + b"]" * 1200,
        }
        for name, value in (
            ("server error", {"statusCode": 500}),
            ("quota", {"statusCode": 429}),
            ("string code", {"statusCode": "401"}),
            ("boolean code", {"statusCode": True}),
            ("retryable", {"isRetryable": True}),
            ("usage", {"usage": {"output_tokens": 1}}),
        ):
            altered = deepcopy(original)
            altered["error"]["data"].update(value)
            alternatives[name] = json.dumps(altered)
        for name, value in (("foreign session", "another-session"), ("empty session", "")):
            alternatives[name] = json.dumps({**original, "sessionID": value})
        for name, value in alternatives.items():
            events.write_bytes(value.encode() if isinstance(value, str) else value)
            before = self.journal_bytes()
            with self.subTest(case=name), patch.object(taskrun, "run_process", side_effect=self.public_cli):
                with self.assertRaises(taskrun.TaskRunError):
                    self.driver.abandon_stage("001/plan-revise-01")
            self.assertEqual(before, self.journal_bytes())
            self.assertFalse(Path(self.prepared_body["record_identity"]["after_ref"]).exists())

    def test_authentication_rejection_refuses_retained_reports_and_deadline_receipt(self):
        self.authentication_rejection()
        output = Path(self.record["output"])
        for path in (output, output.with_suffix(".reported.json"), output.with_suffix(".response.txt")):
            path.write_text("retained response is not error-only\n")
            before = self.journal_bytes()
            with self.subTest(path=path.name), patch.object(taskrun, "run_process", side_effect=self.public_cli):
                with self.assertRaises(taskrun.TaskRunError):
                    self.driver.abandon_stage("001/plan-revise-01")
            self.assertEqual(before, self.journal_bytes())
            path.unlink()
        self.receipt["cause"] = "stage_deadline"
        util.atomic_json(Path(self.metadata["receipt"]), self.receipt)
        before = self.journal_bytes()
        with patch.object(taskrun, "run_process", side_effect=self.public_cli):
            with self.assertRaises(taskrun.TaskRunError):
                self.driver.abandon_stage("001/plan-revise-01")
        self.assertEqual(before, self.journal_bytes())

    def test_authentication_rejection_pins_exact_error_bytes_before_publication(self):
        self.authentication_rejection()
        events = Path(self.record["events"])
        original = recovery.authentication_rejection

        def replaced(raw, **options):
            accepted = original(raw, **options)
            changed = json.loads(raw)
            changed["error"]["data"]["statusCode"] = 403
            events.write_text(json.dumps(changed) + "\n")
            return accepted

        before = self.journal_bytes()
        with (
            patch.object(taskrun, "run_process", side_effect=self.public_cli),
            patch.object(recovery, "authentication_rejection", side_effect=replaced),
        ):
            with self.assertRaises(taskrun.TaskRunError):
                self.driver.abandon_stage("001/plan-revise-01")
        self.assertEqual(before, self.journal_bytes())

    def test_authentication_recovery_keeps_runtime_and_live_owner_guards(self):
        self.authentication_rejection()
        before = self.journal_bytes()
        with (
            patch.object(taskrun, "run_process", side_effect=self.public_cli),
            patch.object(protection.history, "runtime_files", return_value={"changed": "runtime"}),
        ):
            with self.assertRaises(taskrun.TaskRunError):
                self.driver.abandon_stage("001/plan-revise-01")
        self.assertEqual(before, self.journal_bytes())
        self.matching_live = [self.metadata["owner"]]
        with patch.object(taskrun, "run_process", side_effect=self.public_cli):
            with self.assertRaises(taskrun.TaskRunError):
                self.driver.abandon_stage("001/plan-revise-01")
        self.assertEqual(before, self.journal_bytes())

    def test_public_collected_length_abandon_preserves_original_caps_and_rejected_tail(self):
        self.collected_length()
        before = deepcopy(self.state)
        events = Path(self.record["events"]).read_bytes()
        with patch.object(taskrun, "run_process", side_effect=self.public_cli):
            view = self.driver.abandon_stage("001/plan-revise-01")
        self.assertEqual("PAUSED_STAGE_ABANDONED", view["status"])
        self.assertEqual("plan_revise", view["next_stage"])
        self.assertEqual(before["settings"], self.state["settings"])
        self.assertEqual(before["planning"], self.state["planning"])
        self.assertEqual(before["total_seconds"], self.state["total_seconds"])
        self.assertEqual(events, Path(self.record["events"]).read_bytes())
        abandoned = self.state["stages"][-1]
        self.assertFalse(abandoned["timed_out"])
        self.assertEqual(0, abandoned["exit_code"])
        self.assertEqual(before["active_stage"]["output_token_cap"], abandoned["output_token_cap"])
        self.assertEqual(self.raw["revision"], abandoned["source_revision"])
        self.assertTrue(abandoned["rejected"])
        self.assertFalse(Path(abandoned["output"]).exists())
        self.assertIn("output", abandoned["rejection_reason"])
        with patch.object(protection.processes, "live_processes", side_effect=self.observe):
            self.construct()

    def test_collected_length_constructor_refuses_untrusted_transport_without_publication(self):
        self.collected_length()
        events = Path(self.record["events"])
        original = events.read_text()
        rows = [json.loads(line) for line in original.splitlines()]
        alternatives = {
            "wrong-session": [{**row, "sessionID": "foreign-session"} for row in rows],
            "mixed-session": [rows[0], {**rows[1], "sessionID": "foreign-session"}],
            "unknown-event": [*rows, {"type": "unrecognized", "sessionID": "controlled-session", "part": {}}],
            "error-event": [*rows, {"type": "error", "sessionID": "controlled-session", "error": "failed"}],
            "completed-turn": [*rows, {"type": "turn.completed"}],
            "trailing-start": [*rows, rows[0]],
            "missing-terminal": [rows[0]],
            "ordinary-stop": [rows[0], {**rows[1], "part": {**rows[1]["part"], "reason": "stop"}}],
            "normalized-forgery": [
                {"type": "thread.started", "thread_id": "controlled-session"},
                {"type": "turn.failed", "error": {"code": "output_token_limit"}},
            ],
        }
        for case, altered in alternatives.items():
            events.write_text("".join(json.dumps(row) + "\n" for row in altered))
            before = self.journal_bytes()
            with (
                self.subTest(case=case),
                patch.object(protection.processes, "live_processes", side_effect=self.observe),
            ):
                with self.assertRaises(util.Paused):
                    self.construct(intent=self.intent())
            self.assertEqual(before, self.journal_bytes())
            self.assertFalse(Path(self.prepared_body["record_identity"]["after_ref"]).exists())
        events.write_text(original + "not JSON\n")
        before = self.journal_bytes()
        with (
            patch.object(protection.processes, "live_processes", side_effect=self.observe),
            self.assertRaises(util.Paused),
        ):
            self.construct(intent=self.intent())
        self.assertEqual(before, self.journal_bytes())

    def test_collected_length_public_control_refuses_uncertain_or_changed_scope(self):
        self.collected_length()
        original = deepcopy(self.state)
        changes = (
            {"engine": "unknown"},
            {"exit_code": -1},
            {"exit_code": None},
            {"exit_code": False},
            {"accounted": False},
            {"timed_out": True},
            {"interrupted": True},
            {"report_only": True},
            {"dry_run": True},
            {"rejected": True},
            {"abandoned": True},
            {"cleanup_error": "failed"},
            {"supervision_errors": ["failed"]},
        )
        for change in changes:
            self.state = deepcopy(original)
            self.state["active_stage"].update(change)
            util.atomic_json(self.state_path, self.state)
            before = self.journal_bytes()
            with self.subTest(change=change), patch.object(taskrun, "run_process", side_effect=self.public_cli):
                with self.assertRaises(taskrun.TaskRunError):
                    self.driver.abandon_stage("001/plan-revise-01")
            self.assertEqual(before, self.journal_bytes())
            self.assertIsNotNone(self.state["active_stage"])
        for case in ("writer", "postapproval", "missing-session"):
            self.state = deepcopy(original)
            if case == "writer":
                self.state["current_task"] = {"kind": "implement"}
            elif case == "postapproval":
                self.state["goal_contract"] = {"approval_status": "approved"}
            else:
                self.state["active_stage"].pop("expected_session")
            util.atomic_json(self.state_path, self.state)
            before = self.journal_bytes()
            with self.subTest(case=case), patch.object(taskrun, "run_process", side_effect=self.public_cli):
                with self.assertRaises(taskrun.TaskRunError):
                    self.driver.abandon_stage("001/plan-revise-01")
            self.assertEqual(before, self.journal_bytes())

    def test_collected_length_constructor_checks_original_root_keeper_and_descendants(self):
        self.collected_length()
        descendant = {"pid": 404, "birth_identity": 1000000004.0}
        self.state["active_stage"]["processes"].append(descendant)
        for live in (self.original_root, self.metadata["owner"], self.metadata["keeper"], descendant):
            before = self.journal_bytes()
            self.matching_live = [live]
            with (
                self.subTest(pid=live["pid"]),
                patch.object(protection.processes, "live_processes", side_effect=self.observe),
            ):
                with self.assertRaises(util.Paused):
                    self.construct(intent=self.intent())
            self.assertEqual(before, self.journal_bytes())

    def test_collected_length_authentication_pins_the_exact_classified_bytes(self):
        self.collected_length()
        events = Path(self.record["events"])
        original = protection.opencode_events.normalized_events

        def substituted(rows):
            normalized = original(rows)
            changed = deepcopy(rows)
            changed[0]["part"]["id"] = "changed-after-classification"
            events.write_text("".join(json.dumps(row) + "\n" for row in changed))
            return normalized

        before = self.journal_bytes()
        with (
            patch.object(protection.processes, "live_processes", side_effect=self.observe),
            patch.object(protection.opencode_events, "normalized_events", side_effect=substituted),
            self.assertRaises(util.Paused),
        ):
            self.construct(intent=self.intent())
        self.assertEqual(before, self.journal_bytes())
        self.assertFalse(Path(self.prepared_body["record_identity"]["after_ref"]).exists())

    def test_collected_length_refuses_original_supervisor_deadline_despite_valid_length_bytes(self):
        self.collected_length()
        events = Path(self.record["events"]).read_bytes()
        record = deepcopy(self.state["active_stage"])
        self.receipt["cause"] = "stage_deadline"
        util.atomic_json(Path(self.metadata["receipt"]), self.receipt)
        before = self.journal_bytes()
        with (
            patch.object(protection.processes, "live_processes", side_effect=self.observe),
            self.assertRaisesRegex(util.Paused, "collected supervisor cause"),
        ):
            self.construct(intent=self.intent())
        self.assertEqual(before, self.journal_bytes())
        self.assertEqual(record, self.state["active_stage"])
        self.assertEqual(events, Path(self.record["events"]).read_bytes())

    def test_public_collected_length_abandon_then_resume_reaches_original_same_stage_admission(self):
        self.collected_length()
        before = deepcopy(self.state)
        events = Path(self.record["events"]).read_bytes()
        with patch.object(taskrun, "run_process", side_effect=self.public_cli):
            self.driver.abandon_stage("001/plan-revise-01")
            self.driver.resume_paused()
        self.assertEqual("plan_revise", self.resume_admission["record"]["stage"])
        self.assertEqual(before["settings"]["roles"]["glm"], self.resume_admission["record"]["launch_route"])
        self.assertEqual("64000", self.resume_admission["environment"]["OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX"])
        self.assertEqual(before["settings"], self.state["settings"])
        self.assertEqual(before["planning"], self.state["planning"])
        self.assertEqual(before["total_seconds"], self.state["total_seconds"])
        self.assertEqual(events, Path(self.record["events"]).read_bytes())
        self.assertEqual(self.raw, self.resume_admission["prepared"]["binding"]["source"])
        self.assertFalse(Path(self.state["stages"][-1]["output"]).exists())
        cycle = deepcopy(self.state["planning"]["v2_cycle"])
        planning.charge(self.state, "plan_finalize")
        self.assertEqual(2, self.state["planning"]["astra_calls"])
        self.assertEqual(2, planning.review_call_limit(self.state))
        self.assertEqual(cycle, self.state["planning"]["v2_cycle"])
        self.assertNotIn("planning_review_refunded", self.state["stages"][-1])

    def test_public_abandon_reaches_original_constructor_and_preserves_rejected_tail(self):
        self.assertNotEqual(self.original_root, self.metadata["provider"])
        self.assertEqual(self.original_root["pid"], self.metadata["provider"]["session"])
        events = Path(self.record["events"]).read_bytes()
        before = self.state_path.read_bytes()
        with patch.object(taskrun, "run_process", side_effect=self.public_cli):
            view = self.driver.abandon_stage("001/plan-revise-01")
        self.assertEqual(view["status"], "PAUSED_STAGE_ABANDONED")
        self.assertEqual(Path(self.record["events"]).read_bytes(), events)
        self.assertNotEqual(self.state_path.read_bytes(), before)
        self.assertEqual(self.state["total_seconds"], 900.0)
        self.assertEqual(self.metadata, self.state["stages"][-1]["supervision"])
        self.assertTrue(all(any(row["pid"] == 402 for row in inventory) for inventory in self.observed_originals))
        rows = [json.loads(p.read_bytes()) for p in sorted((self.run / "visual-native-protection").iterdir())]
        stopped = [row["body"] for row in rows if row["kind"] == "finished"]
        self.assertEqual(len(stopped), 1)
        self.assertTrue(stopped[0]["record"]["timed_out"])
        self.assertTrue(stopped[0]["record"]["rejected"])
        self.assertEqual(stopped[0]["possible_writes"], [])

    def test_default_constructor_retains_idle_crash_gap_and_writes_nothing(self):
        before = self.journal_bytes()
        with self.assertRaisesRegex(util.Paused, "actual launch/check crash gap remains"):
            self.construct()
        self.assertEqual(self.journal_bytes(), before)
        self.assertFalse(Path(self.record["after_ref"]).exists())

    def test_intent_constructor_authenticates_without_finishing_or_granting_ordinary_launch(self):
        before = self.journal_bytes()
        with patch.object(protection.processes, "live_processes", side_effect=self.observe):
            self.construct(intent=self.intent(), verify_idle=True)
        self.assertEqual(self.journal_bytes(), before)
        self.assertFalse(Path(self.record["after_ref"]).exists())

    def test_public_mixed_stale_unaccounted_postapproval_and_uncertain_controls_refuse_before_context(self):
        base = [
            "controlled-public-cli",
            "--workspace",
            str(self.root),
            "--run-dir",
            str(self.run),
            "--abandon-stage",
            "001/plan-revise-01",
        ]
        original = deepcopy(self.state)
        cases = ("mixed", "stale", "unaccounted", "approved", "uncertain", "wrongstage")
        for case in cases:
            self.state = deepcopy(original)
            command = list(base)
            if case == "mixed":
                command += ["--resume-paused"]
            elif case == "stale":
                command[-1] = "001/plan-revise-00"
            elif case == "unaccounted":
                self.state["active_stage"]["accounted"] = False
            elif case == "approved":
                self.state["goal_contract"] = {"approval_status": "approved"}
            elif case == "uncertain":
                self.state["active_stage"]["exit_code"] = None
            else:
                self.state["active_stage"]["stage"] = self.state["next_stage"] = "terra"
            util.atomic_json(self.state_path, self.state)
            before = self.journal_bytes()
            with self.subTest(case=case), patch.object(launch, "NativeRunContext") as constructor:
                result = self.public_cli(command)
                self.assertEqual(result.returncode, 2)
                constructor.assert_not_called()
                self.assertEqual(self.journal_bytes(), before)

    def test_original_constructor_refuses_changed_seal_supervisor_and_live_controller(self):
        original = self.receipt.copy()
        for case in (
            "nonce",
            "owner",
            "keeper",
            "provider",
            "provider_session",
            "provider_group",
            "phase",
            "cleanup",
            "cause",
            "live",
        ):
            receipt = deepcopy(original)
            if case == "nonce":
                receipt["nonce"] = "e" * 32
            elif case in ("owner", "keeper", "provider"):
                receipt[case]["birth_identity"] += 1
            elif case == "provider_session":
                receipt["provider"]["session"] += 1
            elif case == "provider_group":
                receipt["provider"]["group"] += 1
            elif case == "phase":
                receipt["phase"] = "running"
            elif case == "cleanup":
                receipt["cleanup_error"] = "controlled uncertain cleanup"
            elif case == "cause":
                receipt["cause"] = "owner_lost"
            else:
                self.matching_live = [self.metadata["owner"]]
            util.atomic_json(Path(self.metadata["receipt"]), receipt)
            before = self.journal_bytes()
            with (
                self.subTest(case=case),
                patch.object(protection.processes, "live_processes", side_effect=self.observe),
                self.assertRaises(util.Paused),
            ):
                self.construct(intent=self.intent())
            self.assertEqual(self.journal_bytes(), before)
            self.assertFalse(Path(self.record["after_ref"]).exists())
            self.matching_live = []
        util.atomic_json(Path(self.metadata["receipt"]), original)

    def test_saved_admission_keeps_exact_session_and_group_despite_projected_root(self):
        original = deepcopy(self.record["supervision"])
        for field in ("session", "group", "birth_identity", "pid"):
            changed = deepcopy(original)
            changed["provider"][field] += 1
            self.record["supervision"] = changed
            util.atomic_json(self.state_path, self.state)
            before = self.journal_bytes()
            with self.subTest(field=field), patch.object(taskrun, "run_process", side_effect=self.public_cli):
                with self.assertRaises(taskrun.TaskRunError):
                    self.driver.abandon_stage("001/plan-revise-01")
            self.assertEqual(before, self.journal_bytes())
        self.record["supervision"] = original

    def test_original_constructor_refuses_source_settings_and_stale_intent_without_writes(self):
        original = deepcopy(self.state)
        for case in ("source", "settings", "intent"):
            self.state = deepcopy(original)
            intent = self.intent()
            if case == "source":
                (self.root / "source.txt").write_text("changed controlled source\n")
            elif case == "settings":
                self.state["settings"]["roles"]["glm"]["model"] = "foreign/model"
            else:
                self.state["active_stage"]["finished_at"] = "changed timestamp"
            before = self.journal_bytes()
            with (
                self.subTest(case=case),
                patch.object(protection.processes, "live_processes", side_effect=self.observe),
                self.assertRaises(util.Paused),
            ):
                self.construct(intent=intent)
            self.assertEqual(self.journal_bytes(), before)
            self.assertFalse(Path(self.record["after_ref"]).exists())
            (self.root / "source.txt").write_text("original controlled source\n")

    def test_original_constructor_rejects_multiple_pending_tails(self):
        with ExitStack() as stack:
            run_handle = stack.enter_context(util.run_lock(self.run))
            checkout_handle = stack.enter_context(locks.exclusive(self.root, self.run))
            self.original_owner.handles = (
                (run_handle, self.run / "writer.lock"),
                (checkout_handle, self.root / ".autocode/writer.lock"),
            )
            self.original_owner._append("prepared", self.prepared_body)
        before = self.journal_bytes()
        with (
            patch.object(protection.processes, "live_processes", side_effect=self.observe),
            self.assertRaisesRegex(util.Paused, "overlapping/unfinished native operation"),
        ):
            self.construct(intent=self.intent())
        self.assertEqual(self.journal_bytes(), before)
