"""Recorded dispatches use the owned emitter without executing recorded tool commands."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_check_replay as check_replay
import autocode_command_receipt as command_receipt
import autocode_completion as completion
import autocode_dependency as dependency
import autocode_event_log as event_log
import autocode_evidence_document as evidence_document
import autocode_evidence_export as evidence_export
import autocode_evidence_provenance as provenance
import autocode_model_inventory as model_inventory
import autocode_provider_launch as provider_launch
import autocode_regression as regression
import autocode_replay_checks as checks
import autocode_replay_recording as recording
import autocode_replay_transport as transport
import autocode_replay_view as replay_view
import autocode_run_view as run_view
import autocode_util as util
import autocode_verify as verify
from units import autoplanner as planning

from .test_replay_recording import make_export_projection


class OwnedTransportTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.projection = make_export_projection(self.root)

    def tape(self, *, events=None, report=None, updates=None, bindings=None):
        record = self.projection["records"][0]
        if events is not None:
            Path(record["events"]).write_text("".join(json.dumps(row) + "\n" for row in events))
        if report is not None:
            Path(record["output"]).write_text(json.dumps(report))
        if updates:
            record.update(updates)
        if bindings is not None:
            record["bindings"] = bindings
        tape = recording.load(recording.export(self.projection, self.root / "recording"))
        workspace = recording.prepare_workspace(tape, self.root / "replay-workspace")
        run_dir = workspace / ".autocode" / "runs" / "replay"
        run_dir.mkdir(parents=True)
        self.tape_value, self.workspace, self.run_dir = tape, workspace, run_dir
        self.report = run_dir / "builder.json"
        self.events = self.report.with_suffix(".jsonl")
        self.schema = run_dir / "builder.schema.json"
        self.schema.write_text("{}\n")
        self.prompt = run_dir / "builder.prompt.md"
        self.prompt.write_text("recorded prompt\n")
        return tape

    def prepare(self, **changes):
        tape = self.tape_value
        row = tape.document["dispatches"][0]
        authority = {
            "path": str(tape.manifest_path),
            "sha256": tape.sha256,
            "checks": "fresh",
            "dispatch_ids": [row["id"]],
        }
        context = {key: row[key] for key in (*transport.CONTEXT_KEYS, "task_id", "contract_hash", "criteria_revision")}
        context["consumed"] = 0
        arguments = {
            "settings": {"replay_recording": authority},
            "engine": row["engine"],
            "adapter": SimpleNamespace(),
            "role": row["role"],
            "route_role": row["route_role"],
            "workspace": self.workspace,
            "run_dir": self.run_dir,
            "session": None,
            "model": row["model"],
            "effort": row["reasoning_effort"],
            "provider": row["provider"],
            "report": self.report,
            "schema": self.schema,
            "prompt_file": self.prompt,
            "allow_write": True,
            "context": context,
        }
        arguments.update(changes)
        return transport.prepare(**arguments)

    def emit(self, prepared, *, environment=None):
        command, child_environment, _, _ = prepared
        if environment:
            child_environment = {**child_environment, **environment}
        with event_log.open_events(self.events) as output:
            result = subprocess.run(
                command,
                input="recorded prompt\n",
                cwd=self.workspace,
                env=child_environment,
                stdout=output,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
            )
        rows = [json.loads(line) for line in self.events.read_text().splitlines() if line.strip()]
        return result, rows

    def recorded_facade(self, tape):
        return transport.Facade(
            {
                "replay_recording": {
                    "path": str(tape.manifest_path),
                    "sha256": tape.sha256,
                    "checks": "fresh",
                    "dispatch_ids": [row["id"] for row in tape.document["dispatches"]],
                }
            }
        )

    def test_invocation_inventory_validates_recorded_routes_without_a_live_roster(self):
        tape = self.tape()
        facade = self.recorded_facade(tape)
        inventory = model_inventory.InvocationInventory(facade, self.workspace)
        routes = copy.deepcopy(tape.document["launch"]["settings"]["roles"])
        with patch.object(transport.native, "available_models", side_effect=AssertionError("live model listing")):
            self.assertIsNone(inventory.available_models(self.workspace))
            self.assertIsNone(inventory.check_models(routes))
            self.assertIsNone(facade.check_models(routes, self.workspace, inventory=set()))

    def test_replay_keeps_archived_reviewer_and_investigator_efforts_while_charging_reviews(self):
        self.projection["settings"].update(
            planning_review_call_limit=2,
            effort_origins={"plan_reviewer": "auto_selected"},
            roles={
                "terra": {"model": "fixture-model"},
                "plan_reviewer": {
                    "engine": "opencode",
                    "model": "openai/gpt-6-astra",
                    "reasoning_effort": "medium",
                    "model_pinned": True,
                    "light_effort": "low",
                },
                "investigator": {
                    "engine": "opencode",
                    "model": "openai/gpt-6-astra",
                    "reasoning_effort": "high",
                },
            },
        )
        tape = self.tape()
        state = {
            "task": self.projection["task"],
            "status": "RUNNING",
            "next_stage": "astra_challenge",
            "workflow": {"kind": "build", "clarity": "clear", "brief_size": "small"},
            "planning": {"astra_calls": 0, "reports": {}},
            "planning_history": [{"astra_calls": 1}],
        }
        args = SimpleNamespace(replay_recording=tape.manifest_path, replay_checks="fresh")
        state["settings"] = transport.configure(args, state)
        self.assertEqual("investigator", planning.route_for(state, "investigate_bug"))
        self.assertEqual("plan_reviewer", planning.route_for(state, "astra_challenge"))
        self.assertEqual("medium", state["settings"]["roles"]["plan_reviewer"]["reasoning_effort"])
        self.assertEqual({"plan_reviewer": "auto_selected"}, state["settings"]["effort_origins"])
        self.assertEqual("high", state["settings"]["roles"]["investigator"]["reasoning_effort"])
        self.assertEqual(tape.document["launch"]["settings"]["roles"], state["settings"]["roles"])
        self.assertTrue(state["planning"]["review_depth_reduced"])
        self.assertEqual(1, state["planning"]["review_calls_before_policy"])
        self.assertEqual(1, state["planning"]["astra_calls"])
        self.recorded_facade(tape).check_models(state["settings"]["roles"], self.workspace)
        planning.charge(state, "astra_challenge")
        self.assertEqual(2, state["planning"]["astra_calls"])
        with self.assertRaises(util.Paused) as raised:
            planning.charge(state, "astra_challenge")
        self.assertEqual("PAUSED_PLANNING_BUDGET", raised.exception.status)

    def test_historical_reviewer_effort_change_still_refuses_before_emitter_effects(self):
        self.projection["settings"]["roles"]["plan_reviewer"] = {
            "model": "fixture-model",
            "reasoning_effort": "medium",
        }
        self.tape(updates={"route_role": "plan_reviewer", "reasoning_effort": "high"})
        before = (self.workspace / "greet.py").read_bytes()
        with self.assertRaisesRegex(recording.RecordingError, "reasoning_effort differs"):
            self.prepare(effort="medium")
        self.assertEqual(before, (self.workspace / "greet.py").read_bytes())
        self.assertFalse(self.report.exists())

    def test_recorded_model_route_substitution_refuses_without_provider_or_workspace_effects(self):
        self.projection["settings"]["roles"]["terra"].update(
            engine="opencode", provider=None, reasoning_effort="medium"
        )
        tape = self.tape()
        facade = self.recorded_facade(tape)
        routes = copy.deepcopy(tape.document["launch"]["settings"]["roles"])
        alternatives = [{"unknown": routes["terra"]}, {"terra": {}}]
        for key, value in (
            ("model", "foreign-model"),
            ("engine", "codex"),
            ("provider", "foreign-provider"),
            ("reasoning_effort", "high"),
        ):
            changed = copy.deepcopy(routes)
            changed["terra"][key] = value
            alternatives.append(changed)
        before = (self.workspace / "greet.py").read_bytes()
        with patch.object(transport.native, "available_models", side_effect=AssertionError("live model listing")):
            for changed in alternatives:
                with (
                    self.subTest(routes=changed),
                    self.assertRaisesRegex(recording.RecordingError, "model routes differ"),
                ):
                    facade.check_models(changed, self.workspace)
        self.assertEqual(before, (self.workspace / "greet.py").read_bytes())
        self.assertFalse(self.report.exists())

    def test_model_inventory_readmission_refuses_changed_cached_or_manifest_evidence(self):
        tape = self.tape()
        routes = copy.deepcopy(tape.document["launch"]["settings"]["roles"])
        facade = self.recorded_facade(tape)
        facade.recording.document["launch"]["settings"]["roles"]["terra"]["model"] = "foreign-model"
        with self.assertRaisesRegex(recording.RecordingError, "document changed"):
            facade.check_models(routes, self.workspace)
        facade = self.recorded_facade(tape)
        tape.manifest_path.write_bytes(tape.manifest_path.read_bytes() + b" ")
        with self.assertRaisesRegex(recording.RecordingError, "manifest changed"):
            facade.check_models(routes, self.workspace)

    def test_replay_launch_identity_is_current_and_archive_identity_stays_distinct(self):
        archived = "a" * 64
        current = "b" * 64
        tape = self.tape(updates={"prompts_hash": archived})
        prepared = self.prepare()
        worker = prepared[3]
        with patch.object(provider_launch.prompts, "HASH", current):
            record = provider_launch.stage_record(worker)
        self.assertEqual(current, record["prompts_hash"])
        self.assertEqual(archived, record["replay"]["archived_prompts_hash"])
        self.assertEqual(archived, tape.document["dispatches"][0]["prompts_hash"])
        result, rows = self.emit(prepared)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(any(row.get("type") == "turn.completed" for row in rows))
        self.assertEqual("COMPLETE", json.loads(self.report.read_text())["status"])

    def test_legacy_replay_does_not_invent_an_archived_prompt_identity(self):
        self.tape()
        record = provider_launch.stage_record(self.prepare()[3])
        self.assertEqual(provider_launch.prompts.HASH, record["prompts_hash"])
        self.assertNotIn("archived_prompts_hash", record["replay"])

    def test_explicit_unknown_archived_identity_remains_unknown_during_replay(self):
        self.tape(updates={"prompts_hash": None})
        record = provider_launch.stage_record(self.prepare()[3])
        self.assertEqual(provider_launch.prompts.HASH, record["prompts_hash"])
        self.assertIsNone(record["replay"]["archived_prompts_hash"])

    def test_changed_archived_prompt_identity_refuses_before_emitter_effects(self):
        tape = self.tape(updates={"prompts_hash": "a" * 64})
        prepared = self.prepare()
        before = (self.workspace / "greet.py").read_bytes()
        document = json.loads(tape.manifest_path.read_text())
        document["dispatches"][0]["prompts_hash"] = "c" * 64
        tape.manifest_path.write_text(json.dumps(document) + "\n")
        result, rows = self.emit(prepared)
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertEqual("Replay transport stopped: recording divergence or unavailable evidence\n", result.stderr)
        self.assertEqual([], rows)
        self.assertFalse(self.report.exists())
        self.assertEqual(before, (self.workspace / "greet.py").read_bytes())

    def test_owned_emitter_retains_route_and_executes_no_recorded_tool_command(self):
        marker = self.root / "recorded-command-must-not-execute"
        events = [
            {"type": "thread.started", "thread_id": "recorded-session"},
            {
                "type": "item.completed",
                "item": {
                    "id": "tool-1",
                    "type": "command_execution",
                    "command": "touch " + str(marker),
                    "exit_code": 0,
                    "aggregated_output": "recorded only",
                },
            },
            {"type": "turn.completed", "usage": {}},
        ]
        self.tape(events=events)
        prepared = self.prepare()
        command, environment, overrides, worker = prepared
        self.assertEqual("-I", command[1])
        self.assertIn("autocode_replay_transport", command[3])
        self.assertEqual(("codex", "fixture-model", None), (worker["engine"], worker["model"], worker["provider"]))
        self.assertEqual({"PATH", "PYTHONNOUSERSITE"}, set(environment))
        self.assertIsNone(overrides)
        result, emitted = self.emit(prepared)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(events, emitted)
        self.assertFalse(marker.exists())
        self.assertEqual("print('after')\n", (self.workspace / "greet.py").read_text())
        self.assertEqual(0o400, self.events.stat().st_mode & 0o777)

    def test_recorded_failure_and_collected_exit_are_not_changed_into_success(self):
        events = [
            {"type": "thread.started", "thread_id": "recorded-session"},
            {"type": "turn.failed", "error": {"message": "recorded provider failure"}},
        ]
        self.tape(events=events, updates={"exit_code": 1})
        result, emitted = self.emit(self.prepare())
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertEqual(events, emitted)
        self.assertFalse(any(row.get("type") == "turn.completed" for row in emitted))

    def test_extra_dispatch_and_changed_occurrence_context_are_divergence(self):
        self.tape()
        row = self.tape_value.document["dispatches"][0]
        context = {key: row[key] for key in (*transport.CONTEXT_KEYS, "task_id", "contract_hash", "criteria_revision")}
        context["consumed"] = 0
        for changed in ({"consumed": 1}, {"stage": "sol"}, {"iteration": 2}, {"turn": 2}, {"report_only": True}):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "diverged"):
                self.prepare(context={**context, **changed})
        self.assertFalse((self.run_dir / "replay-transport").exists())

    def test_changed_role_model_effort_or_provider_cannot_reuse_the_recorded_response(self):
        self.tape()
        for changed in (
            {"role": "sol"},
            {"model": "another-model"},
            {"effort": "high"},
            {"provider": "foreign-provider"},
        ):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "diverged"):
                self.prepare(**changed)
        self.assertFalse((self.run_dir / "replay-transport").exists())

    def test_read_only_dispatch_cannot_apply_recorded_source_writes(self):
        self.tape()
        with self.assertRaisesRegex(ValueError, "read-only dispatch"):
            self.prepare(allow_write=False)
        self.assertEqual("print('before')\n", (self.workspace / "greet.py").read_text())

    def test_an_already_prepared_occurrence_cannot_be_dispatched_again(self):
        self.tape()
        self.prepare()
        with self.assertRaisesRegex(ValueError, "already prepared"):
            self.prepare()

    def test_dispatch_identity_cannot_be_an_absolute_escaping_or_aliased_job_path(self):
        self.tape()
        manifest = self.tape_value.manifest_path
        original = copy.deepcopy(self.tape_value.document)
        for identity in (
            "../escape",
            "/foreign",
            "folder/dispatch",
            "folder\\dispatch",
            ".",
            "..",
            "end",
            "_initial",
            "stageé",
            "a" * 65,
        ):
            document = copy.deepcopy(original)
            document["dispatches"][0]["id"] = identity
            manifest.write_text(json.dumps(document))
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                recording.load(manifest)
        self.assertFalse((self.run_dir / "replay-transport").exists())
        self.assertFalse((self.run_dir / "escape.json").exists())

    def test_dangling_job_symlink_cannot_redirect_preparation_to_a_foreign_file(self):
        self.tape()
        directory = self.run_dir / "replay-transport"
        directory.mkdir()
        job = directory / (self.tape_value.document["dispatches"][0]["id"] + ".json")
        foreign = self.root / "foreign-job.json"
        job.symlink_to(foreign)
        with self.assertRaises((ValueError, OSError)):
            self.prepare()
        self.assertFalse(foreign.exists())
        self.assertTrue(job.is_symlink())
        self.assertEqual("print('before')\n", (self.workspace / "greet.py").read_text())

    def test_job_changed_after_preparation_cannot_emit_or_apply_source_effects(self):
        self.tape()
        prepared = self.prepare()
        job = self.run_dir / "replay-transport" / (self.tape_value.document["dispatches"][0]["id"] + ".json")
        document = json.loads(job.read_text())
        foreign_report = self.run_dir / "not-the-admitted-report.json"
        document["report"] = str(foreign_report)
        job.write_text(json.dumps(document))
        result, emitted = self.emit(prepared)
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertEqual([], emitted)
        self.assertFalse(foreign_report.exists())
        self.assertFalse(self.report.exists())
        self.assertEqual("print('before')\n", (self.workspace / "greet.py").read_text())

    def test_source_drift_after_admission_stops_before_emitting_or_applying_effects(self):
        self.tape()
        prepared = self.prepare()
        (self.workspace / "greet.py").write_text("operator source change\n")
        result, emitted = self.emit(prepared)
        self.assertEqual(2, result.returncode)
        self.assertEqual([], emitted)
        self.assertFalse(self.report.exists())
        self.assertEqual("operator source change\n", (self.workspace / "greet.py").read_text())

    def test_manifest_tampering_after_admission_is_not_rebound_to_a_new_pin(self):
        self.tape()
        prepared = self.prepare()
        path = self.tape_value.manifest_path
        path.write_text(path.read_text() + " ")
        result, emitted = self.emit(prepared)
        self.assertEqual(2, result.returncode)
        self.assertEqual([], emitted)
        self.assertFalse(self.report.exists())
        self.assertEqual("print('before')\n", (self.workspace / "greet.py").read_text())

    def test_unknown_or_interrupted_original_exit_is_refused_without_a_completed_turn(self):
        for changed in ({"exit_code": None}, {"interrupted": True}, {"timed_out": True}):
            with self.subTest(changed=changed):
                case = self.root / ("case-" + str(len(list(self.root.glob("case-*")))))
                case.mkdir()
                original_root = self.root
                self.root = case
                self.projection = make_export_projection(case)
                self.tape(updates=changed)
                result, emitted = self.emit(self.prepare())
                self.assertEqual(2, result.returncode)
                self.assertEqual([], emitted)
                self.assertFalse(self.report.exists())
                self.root = original_root

    def test_fresh_dispatch_cannot_be_substituted_for_a_session_continuation(self):
        self.tape()
        with self.assertRaisesRegex(ValueError, "session continuation differs"):
            self.prepare(session="unexpected-resume")

    def test_invalid_original_capture_nonce_remains_invalid_in_emitted_report(self):
        report = {"capture_context": {"nonce": "wrong-original-nonce"}, "summary": "recorded diagnostic"}
        bindings = [{"pointer": "/report/capture_context/nonce", "symbol": "capture_nonce", "original_valid": False}]
        self.tape(report=report, bindings=bindings)
        result, _ = self.emit(
            self.prepare(), environment={"AUTOCODE_CAPTURE_CONTEXT": json.dumps({"nonce": "fresh-nonce"})}
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("wrong-original-nonce", json.loads(self.report.read_text())["capture_context"]["nonce"])

    def test_valid_explicit_capture_binding_uses_fresh_nonce_without_changing_diagnostics(self):
        report = {"capture_context": {"nonce": "old-nonce"}, "summary": "old-nonce remains in diagnostic"}
        bindings = [{"pointer": "/report/capture_context/nonce", "symbol": "capture_nonce", "original_valid": True}]
        self.tape(report=report, bindings=bindings)
        result, _ = self.emit(
            self.prepare(), environment={"AUTOCODE_CAPTURE_CONTEXT": json.dumps({"nonce": "fresh-nonce"})}
        )
        self.assertEqual(0, result.returncode, result.stderr)
        emitted = json.loads(self.report.read_text())
        self.assertEqual("fresh-nonce", emitted["capture_context"]["nonce"])
        self.assertEqual("old-nonce remains in diagnostic", emitted["summary"])

    def test_existing_provider_overwrite_bug_replays_the_preserved_transport_stream(self):
        # The existing bug note describes an accidental `w` open of the stdout
        # evidence path. Capture that actual attempt, then replay its saved rows.
        captured = Path(self.projection["run_dir"]) / "protected.jsonl"
        program = "\n".join(
            (
                "import json, sys",
                "print(json.dumps({'type':'thread.started','thread_id':'recorded-session'}), flush=True)",
                "try:",
                "    open(sys.argv[1], 'w').write('destroyed earlier events')",
                "except PermissionError:",
                "    print('overwrite denied', file=sys.stderr)",
                "else:",
                "    raise SystemExit(9)",
                "print(json.dumps({'type':'turn.completed','usage':{}}), flush=True)",
            )
        )
        with event_log.open_events(captured) as output:
            original = subprocess.run(
                [sys.executable, "-c", program, str(captured)],
                stdout=output,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
            )
        self.assertEqual(0, original.returncode, original.stderr)
        self.assertIn("overwrite denied", original.stderr)
        original_bytes = captured.read_bytes()
        self.assertNotIn(b"\x00", original_bytes)
        rows = [json.loads(line) for line in original_bytes.splitlines()]
        self.assertEqual(["thread.started", "turn.completed"], [row["type"] for row in rows])
        self.tape(events=rows)
        replayed, emitted = self.emit(self.prepare())
        self.assertEqual(0, replayed.returncode, replayed.stderr)
        self.assertEqual(rows, emitted)
        self.assertEqual(original_bytes, captured.read_bytes())

    def test_report_leaf_replaced_with_dangling_symlink_cannot_write_foreign_output(self):
        self.tape()
        prepared = self.prepare()
        foreign = self.root / "foreign-must-stay-absent.json"
        self.report.symlink_to(foreign)
        result, emitted = self.emit(prepared)
        self.assertEqual(2, result.returncode)
        self.assertEqual([], emitted)
        self.assertTrue(self.report.is_symlink())
        self.assertFalse(foreign.exists())


class ArchivedCheckTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.projection = make_export_projection(self.root)
        self.destination = self.root / "archival-checks"

    def add_check(self, *, identity="first", exit_code=0, authentication="intact", updates=None):
        run_dir = Path(self.projection["run_dir"])
        folder = run_dir / identity
        folder.mkdir()
        output = folder / "check.log"
        output.write_text("FAIL: recorded check\n" if exit_code else "OK: recorded check\n")
        record = self.projection["records"][0]
        snapshot = json.loads(Path(record["before_ref"]).read_text())
        value = {
            "command": "python3 check.py",
            "exit_code": exit_code,
            "timed_out": False,
            "error": "",
            "output": str(output),
            "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            "source_revision": snapshot["revision"],
        }
        if updates:
            value.update(updates)
        receipt = folder / "receipt.json"
        receipt.write_text(json.dumps(value))
        row = {
            "id": identity,
            "dispatch": "dispatch-0",
            "purpose": "independent_clean_replay",
            "command": "python3 check.py",
            "repetition": 1,
            "snapshot": "source-0",
            "receipt": str(receipt),
            "output": str(output),
            "authentication": authentication,
            "bindings": [],
            "context": {"contract_body": None, "task": {}, "case_ids": []},
        }
        self.projection["checks"].append(row)
        return receipt, output, value

    def tape(self):
        return recording.load(recording.export(self.projection, self.root / "recording"))

    def test_archival_copies_have_explicit_simulation_provenance_and_no_current_proof(self):
        receipt, output, original = self.add_check()
        tape = self.tape()
        archived = checks.replay_receipt(tape, "first", self.destination)
        self.assertIs(archived["simulated"], True)
        self.assertIs(archived["current_proof"], False)
        self.assertEqual(original, archived["recorded"])
        self.assertEqual(hashlib.sha256(receipt.read_bytes()).hexdigest(), archived["original_receipt_sha256"])
        self.assertEqual(hashlib.sha256(output.read_bytes()).hexdigest(), archived["original_output_sha256"])
        self.assertNotEqual(str(receipt), archived["receipt"])
        result = checks.archival_result(tape)
        self.assertIs(result["simulated"], True)
        self.assertIs(result["current_proof"], False)
        self.assertEqual(tape.sha256, result["manifest_sha256"])
        self.assertEqual("archival_recording", result["provenance"])

    def test_341_later_pass_retains_original_fail_receipt_log_source_and_all_prior_checks(self):
        run_dir = Path(self.projection["run_dir"])
        records = self.projection["records"]
        first = copy.deepcopy(records[0])
        first.update(stage="sol", role="sol", route_role="sol", original_stage="sol", after_ref=first["before_ref"])
        second = copy.deepcopy(first)
        second.update(iteration=2, before_ref=records[0]["after_ref"], after_ref=records[0]["after_ref"])
        self.projection["settings"]["roles"]["sol"] = {"model": "fixture-model"}
        self.projection["records"] = [first, second]
        original_files = {}
        for index, (verdict, command_rows, log) in enumerate(
            (
                (
                    "FAIL",
                    [
                        {"command": "cart", "exit_code": 0},
                        {"command": "checkout", "exit_code": 1},
                        {"command": "tax", "exit_code": 0},
                    ],
                    "FAIL: checkout 120.00 !=119.99\n",
                ),
                (
                    "PASS",
                    [{"command": "cart", "exit_code": 0}, {"command": "tax", "exit_code": 0}],
                    "OK: fewer recorded checks\n",
                ),
            )
        ):
            folder = run_dir / "iterations" / str(index + 1)
            folder.mkdir(parents=True)
            receipt = folder / "validator-01.json"
            output = folder / "check.log"
            output.write_text(log)
            source = json.loads(Path(self.projection["records"][index]["before_ref"]).read_text())["revision"]
            value = {
                "verdict": verdict,
                "source_revision": source,
                "checks": command_rows,
                "output": str(output),
                "output_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
            }
            receipt.write_text(json.dumps(value))
            original_files.update({receipt: receipt.read_bytes(), output: output.read_bytes()})
            self.projection["checks"].append(
                {
                    "id": "check-" + str(index),
                    "dispatch": "dispatch-" + str(index),
                    "purpose": "independent_clean_replay",
                    "command": "python3 check.py",
                    "repetition": 1,
                    "snapshot": "source-" + str(2 * index),
                    "receipt": str(receipt),
                    "output": str(output),
                    "authentication": "intact",
                    "bindings": [],
                    "context": {"contract_body": None, "task": {}, "case_ids": []},
                }
            )
        tape = self.tape()
        rejected = checks.replay_receipt(tape, "check-0", self.destination)
        kept_receipt = Path(rejected["receipt"]).read_bytes()
        kept_log = Path(rejected["output"]).read_bytes()
        accepted = checks.replay_receipt(tape, "check-1", self.destination)
        self.assertNotEqual(Path(rejected["receipt"]).parent, Path(accepted["receipt"]).parent)
        self.assertEqual(kept_receipt, Path(rejected["receipt"]).read_bytes())
        self.assertEqual(kept_log, Path(rejected["output"]).read_bytes())
        self.assertEqual("FAIL", json.loads(kept_receipt)["verdict"])
        self.assertEqual("PASS", accepted["recorded"]["verdict"])
        self.assertNotEqual(rejected["recorded"]["source_revision"], accepted["recorded"]["source_revision"])
        self.assertEqual(["cart", "checkout", "tax"], [row["command"] for row in rejected["recorded"]["checks"]])
        self.assertEqual(["cart", "tax"], [row["command"] for row in accepted["recorded"]["checks"]])
        for path, original in original_files.items():
            self.assertEqual(original, path.read_bytes())

    def test_missing_and_invalid_archived_authentication_are_not_repaired_by_copying(self):
        self.add_check(authentication="invalid")
        tape = self.tape()
        with self.assertRaisesRegex(ValueError, "authentication"):
            checks.replay_receipt(tape, "first", self.destination)
        with self.assertRaisesRegex(ValueError, "missing"):
            checks.replay_receipt(tape, "missing", self.destination)
        self.assertFalse(self.destination.exists())

    def test_corrupt_original_output_pin_is_not_replaced_with_the_exported_pin(self):
        self.add_check(updates={"output_sha256": "f" * 64})
        with self.assertRaisesRegex(ValueError, "output pin is damaged"):
            self.tape()
        self.assertFalse((self.root / "recording").exists())
        self.assertFalse(self.destination.exists())

    def test_replay_authentication_refuses_a_bundle_with_semantically_damaged_output_pin(self):
        self.add_check()
        tape = self.tape()
        document = copy.deepcopy(tape.document)
        declaration = document["artifacts"][document["checks"][0]["receipt"]]
        value = json.loads(tape.artifact(document["checks"][0]["receipt"]))
        value["output_sha256"] = "f" * 64
        raw = json.dumps(value).encode()
        (tape.root / declaration["path"]).write_bytes(raw)
        declaration.update(
            sha256=hashlib.sha256(raw).hexdigest(), original_sha256=hashlib.sha256(raw).hexdigest(), bytes=len(raw)
        )
        tape.manifest_path.write_text(json.dumps(document))
        altered = recording.load(tape.manifest_path)
        with self.assertRaisesRegex(ValueError, "output"):
            checks.replay_receipt(altered, "first", self.destination)
        self.assertFalse(self.destination.exists())

    def test_boolean_unknown_and_partial_owned_exits_cannot_become_complete_archival_checks(self):
        for index, changed in enumerate(
            (
                {"exit_code": True},
                {"exit_code": None},
                {"supervision_errors": []},
            )
        ):
            with self.subTest(changed=changed):
                self.add_check(identity="invalid-" + str(index), updates=changed)
        tape = self.tape()
        for index in range(3):
            with self.subTest(index=index), self.assertRaises(ValueError):
                checks.replay_receipt(tape, "invalid-" + str(index), self.destination)
        self.assertFalse(self.destination.exists())

    def test_partial_nested_command_output_metadata_is_refused_at_export(self):
        for index, partial in enumerate(
            (
                {"output": "placeholder"},
                {"output_sha256": "a" * 64},
                {"full_output": "placeholder"},
                {"full_output_sha256": "a" * 64},
            )
        ):
            with self.subTest(partial=partial):
                self.projection["checks"] = []
                receipt, output, value = self.add_check(identity="partial-" + str(index))
                metadata = {
                    key: str(output) if key in ("output", "full_output") else value for key, value in partial.items()
                }
                value["checks"] = [{"command": "check", "exit_code": 0, **metadata}]
                receipt.write_text(json.dumps(value))
                destination = self.root / ("partial-recording-" + str(index))
                with self.assertRaisesRegex(ValueError, "output"):
                    recording.export(self.projection, destination)
                self.assertFalse(destination.exists())

    def test_partial_nested_output_metadata_in_an_existing_bundle_is_not_authenticated(self):
        receipt, output, value = self.add_check()
        value["checks"] = [{"command": "check", "exit_code": 0}]
        receipt.write_text(json.dumps(value))
        tape = self.tape()
        original = copy.deepcopy(tape.document)
        for partial in (
            {"output": str(output)},
            {"output_sha256": util.file_hash(output)},
            {"full_output": str(output)},
            {"full_output_sha256": util.file_hash(output)},
        ):
            with self.subTest(partial=partial):
                document = copy.deepcopy(original)
                declaration = document["artifacts"][document["checks"][0]["receipt"]]
                altered_value = copy.deepcopy(value)
                altered_value["checks"][0].update(partial)
                raw = json.dumps(altered_value).encode()
                (tape.root / declaration["path"]).write_bytes(raw)
                declaration.update(
                    sha256=hashlib.sha256(raw).hexdigest(),
                    original_sha256=hashlib.sha256(raw).hexdigest(),
                    bytes=len(raw),
                )
                tape.manifest_path.write_text(json.dumps(document))
                altered = recording.load(tape.manifest_path)
                with self.assertRaisesRegex(ValueError, "output"):
                    checks.replay_receipt(altered, "first", self.destination)
                self.assertFalse(self.destination.exists())

    def test_nested_output_pin_cannot_reopen_original_log_when_bundle_map_is_missing(self):
        receipt, output, value = self.add_check()
        value["checks"] = [
            {"command": "check", "exit_code": 0, "output": str(output), "output_sha256": util.file_hash(output)}
        ]
        receipt.write_text(json.dumps(value))
        tape = self.tape()
        document = copy.deepcopy(tape.document)
        self.assertTrue(document["checks"][0]["outputs"])
        document["checks"][0]["outputs"] = {}
        tape.manifest_path.write_text(json.dumps(document))
        altered = recording.load(tape.manifest_path)
        self.assertTrue(output.exists())
        with self.assertRaisesRegex(ValueError, "output is missing"):
            checks.replay_receipt(altered, "first", self.destination)
        self.assertFalse(self.destination.exists())

    def test_legacy_timeout_interruption_and_error_are_not_simulated_as_collected_success(self):
        for index, changed in enumerate(({"timed_out": True}, {"interrupted": True}, {"error": "launch failure"})):
            self.add_check(identity="stopped-" + str(index), updates=changed)
        tape = self.tape()
        for index in range(3):
            with self.subTest(index=index), self.assertRaises(ValueError):
                checks.replay_receipt(tape, "stopped-" + str(index), self.destination)
        self.assertFalse(self.destination.exists())

    def add_owned_check(self, *, identity="owned", changes=None):
        receipt, output, value = self.add_check(identity=identity)
        ownership = receipt.with_name("ownership.json")
        metadata = {
            "schema": 1,
            "nonce": "a" * 32,
            "receipt": str(ownership),
            "owner": {"pid": 101, "birth_identity": 1},
            "keeper": {"pid": 102, "birth_identity": 2},
            "provider": {"pid": 103, "birth_identity": 3},
        }
        captured = {
            **metadata,
            "phase": "stopped",
            "cause": "provider_stopped",
            "processes": [metadata["provider"]],
            "cleanup_error": None,
            "observed_at": "2026-10-10T00:00:00+00:00",
        }
        if changes:
            captured.update(changes)
        # Deliberately noncanonical raw encoding: authentic bytes must be retained.
        ownership.write_text(json.dumps(captured) + "\n")
        value.update(supervision=metadata, supervision_sha256=util.file_hash(ownership), supervision_errors=[])
        receipt.write_text(json.dumps(value))
        self.projection["checks"][-1]["supervision"] = {str(ownership): value["supervision_sha256"]}
        return receipt, output, value, ownership

    def test_actual_collected_check_is_portable_without_reopening_original_absolute_paths(self):
        workspace = Path(self.projection["workspace"])
        run_dir = Path(self.projection["run_dir"])
        command = shlex.join([sys.executable, "-c", "print('actual collected check')"])
        actual = verify.run_command(command, workspace, run_dir / "actual.log", timeout=10)
        self.assertEqual(0, actual["exit_code"])
        self.assertTrue(command_receipt.completed(actual, root=run_dir))
        receipt, _, value = self.add_check(updates=actual)
        self.projection["checks"][-1].update(
            command=command,
            output=actual["output"],
            supervision={actual["supervision"]["receipt"]: actual["supervision_sha256"]},
        )
        original_receipt, original_output = receipt.read_bytes(), Path(actual["output"]).read_bytes()
        original_ownership = Path(actual["supervision"]["receipt"]).read_bytes()
        tape = self.tape()
        ownership_id = tape.document["checks"][0]["supervision"][actual["supervision"]["receipt"]]
        self.assertEqual(original_ownership, tape.artifact(ownership_id))
        run_dir.rename(run_dir.with_name("original-moved"))
        self.assertFalse(Path(actual["supervision"]["receipt"]).exists())
        archived = checks.replay_receipt(tape, "first", self.destination)
        self.assertEqual(value, archived["recorded"])
        self.assertEqual(original_receipt, Path(archived["receipt"]).read_bytes())
        self.assertEqual(original_output, Path(archived["output"]).read_bytes())
        self.assertIs(archived["current_proof"], False)
        self.assertFalse(command_receipt.completed(archived["recorded"]))

    def test_uncertain_cleanup_and_foreign_birth_identity_never_become_archival_authentication(self):
        for index, changed in enumerate(
            (
                {"phase": "armed", "cause": None},
                {"phase": "uncertain", "cause": "keeper_failure"},
                {"cleanup_error": "owned cleanup uncertain"},
                {"cause": "owner_lost"},
                {"nonce": "b" * 32},
                {"provider": {"pid": 103, "birth_identity": 99}},
            )
        ):
            self.add_owned_check(identity="invalid-" + str(index), changes=changed)
        tape = self.tape()
        for index in range(6):
            with self.subTest(index=index), self.assertRaises(ValueError):
                checks.replay_receipt(tape, "invalid-" + str(index), self.destination)
        self.assertFalse(self.destination.exists())

    def test_missing_declared_supervision_cannot_fall_back_to_an_original_valid_file(self):
        _, _, value, ownership = self.add_owned_check()
        self.assertTrue(command_receipt.completed(value))
        tape = self.tape()
        document = copy.deepcopy(tape.document)
        self.assertTrue(document["checks"][0]["supervision"])
        document["checks"][0]["supervision"] = {}
        tape.manifest_path.write_text(json.dumps(document))
        altered = recording.load(tape.manifest_path)
        self.assertTrue(ownership.exists())
        with self.assertRaises(ValueError):
            checks.replay_receipt(altered, "owned", self.destination)
        self.assertFalse(self.destination.exists())

    def test_original_provenance_pin_cannot_authenticate_replaced_captured_cleanup_bytes(self):
        self.add_owned_check()
        tape = self.tape()
        document = copy.deepcopy(tape.document)
        artifact_id = next(iter(document["checks"][0]["supervision"].values()))
        declaration = document["artifacts"][artifact_id]
        payload = json.loads(tape.artifact(artifact_id))
        payload["observed_at"] = "different captured bytes"
        replacement = json.dumps(payload).encode()
        (tape.root / declaration["path"]).write_bytes(replacement)
        declaration.update(sha256=hashlib.sha256(replacement).hexdigest(), bytes=len(replacement))
        # The manifest still claims the old original SHA, but actual payload differs.
        tape.manifest_path.write_text(json.dumps(document))
        altered = recording.load(tape.manifest_path)
        with self.assertRaises(ValueError):
            checks.replay_receipt(altered, "owned", self.destination)
        self.assertFalse(self.destination.exists())

    def test_redacted_ownership_cannot_be_promoted_even_when_its_json_shape_is_valid(self):
        self.add_owned_check()
        tape = self.tape()
        document = copy.deepcopy(tape.document)
        artifact_id = next(iter(document["checks"][0]["supervision"].values()))
        document["artifacts"][artifact_id]["redacted"] = True
        tape.manifest_path.write_text(json.dumps(document))
        altered = recording.load(tape.manifest_path)
        with self.assertRaises(ValueError):
            checks.replay_receipt(altered, "owned", self.destination)
        self.assertFalse(self.destination.exists())

    def test_archival_supervision_has_the_same_bounded_reader_as_current_receipts(self):
        self.add_owned_check()
        tape = self.tape()
        with patch.object(command_receipt, "MAX_RECEIPT_BYTES", 1), self.assertRaises(ValueError):
            checks.replay_receipt(tape, "owned", self.destination)
        self.assertFalse(self.destination.exists())


class RecordedObligationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        projection = make_export_projection(self.root)
        self.projection = projection
        snapshot = json.loads(Path(projection["records"][0]["before_ref"]).read_text())
        self.commands = ["python3 check.py", "python3 check.py"]
        for repetition, identity in enumerate(("first", "second"), 1):
            directory = Path(projection["run_dir"]) / identity
            directory.mkdir()
            output = directory / "check.log"
            output.write_text("OK: archived check\n")
            receipt = directory / "receipt.json"
            receipt.write_text(
                json.dumps(
                    {
                        "verdict": "PASS",
                        "commands": self.commands,
                        "checks": [{"command": command, "exit_code": 0} for command in self.commands],
                        "source_revision": snapshot["revision"],
                        "output": str(output),
                        "output_sha256": util.file_hash(output),
                    }
                )
            )
            projection["checks"].append(
                {
                    "id": identity,
                    "dispatch": "dispatch-0",
                    "purpose": "independent_clean_replay",
                    "command": "python3 check.py",
                    "repetition": repetition,
                    "snapshot": "source-0",
                    "receipt": str(receipt),
                    "output": str(output),
                    "authentication": "intact",
                    "bindings": [],
                    "context": {"contract_body": None, "task": {}, "case_ids": []},
                }
            )
        self.tape = recording.load(recording.export(projection, self.root / "recording"))
        self.workspace = recording.prepare_workspace(self.tape, self.root / "replay")
        self.state = {
            "workspace": str(self.workspace),
            "status": "BUILDING",
            "next_stage": "terra",
            "settings": {
                "replay_recording": {
                    "path": str(self.tape.manifest_path),
                    "sha256": self.tape.sha256,
                    "checks": "recorded",
                    "dispatch_ids": ["dispatch-0"],
                }
            },
        }
        self.record = {"replay": {"dispatch_id": "dispatch-0", "ordinal": 0}}

    def replay(self, *, commands=None, record=None):
        return checks.replay(
            [{"command": command} for command in (self.commands if commands is None else commands)],
            self.state,
            self.workspace,
            self.record if record is None else record,
        )

    def amend_tape(self, change):
        document = copy.deepcopy(self.tape.document)
        change(document)
        self.tape.manifest_path.write_text(json.dumps(document))
        self.tape = recording.load(self.tape.manifest_path)
        self.state["settings"]["replay_recording"]["sha256"] = self.tape.sha256

    def consume_all(self):
        self.replay()
        self.replay()
        recording.materialize(self.tape, self.tape.document["dispatches"][0]["after"], self.workspace)

    def test_repeated_same_dispatch_obligations_are_consumed_once_in_order(self):
        first = self.replay()
        kept = Path(first["archival"]["receipt"]).read_bytes()
        second = self.replay()
        self.assertEqual(["first", "second"], self.state["replay_result"]["consumed_checks"])
        self.assertEqual(self.commands, first["commands"])
        self.assertNotEqual(first["archival"]["receipt"], second["archival"]["receipt"])
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "already consumed"):
            self.replay()
        self.assertEqual(before, self.state)
        self.assertEqual(kept, Path(first["archival"]["receipt"]).read_bytes())

    def test_fresh_assignment_time_consumes_the_same_semantic_public_task_obligation(self):
        task = {
            "id": "task-1",
            "contract_hash": "a" * 64,
            "assigned_at": "2026-10-10T15:00:00+00:00",
            "description": "Keep the greeting contract",
            "validation_plan": ["python3 check.py"],
        }
        record = self.projection["records"][0]
        prompt = Path(self.projection["run_dir"]) / "validator.prompt.md"
        prompt.write_text("Recorded handoff\nCURRENT HANDOFF DATA\n" + json.dumps({"current_task": task}))
        record["prompt"] = str(prompt)
        for row in self.projection["checks"]:
            row.pop("context")
        raw_prompt = prompt.read_bytes()
        self.tape = recording.load(recording.export(self.projection, self.root / "assigned-recording"))
        self.state["settings"]["replay_recording"].update(path=str(self.tape.manifest_path), sha256=self.tape.sha256)
        self.state["current_task"] = {**task, "assigned_at": "2026-10-10T16:00:00+00:00"}
        self.state["current_task"]["validation_plan"] = ["python3 foreign.py"]
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "contract, task or case scope"):
            self.replay()
        self.assertEqual(before, self.state)
        self.state["current_task"]["validation_plan"] = task["validation_plan"]
        first, second = self.replay(), self.replay()
        self.assertEqual(["first", "second"], self.state["replay_result"]["consumed_checks"])
        self.assertEqual(raw_prompt, prompt.read_bytes(), "original assigned-at evidence must not be rewritten")
        self.assertIs(first["simulated"], True)
        self.assertIs(second["current_proof"], False)

    def test_changed_command_count_cannot_drop_a_repeated_executable_obligation(self):
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "executable obligations"):
            self.replay(commands=self.commands[:1])
        self.assertEqual(before, self.state)
        self.replay()
        self.assertEqual(["first"], self.state["replay_result"]["consumed_checks"])

    def test_semantic_contract_task_or_named_case_change_does_not_consume_archive(self):
        for changed in (
            {"goal_contract": {"body": {"summary": "foreign approved contract"}}},
            {"current_task": {"description": "foreign task"}},
            {"investigation": {"outcome": "reproduced", "test_cases": [{"id": "foreign"}]}},
        ):
            with self.subTest(changed=changed):
                original = copy.deepcopy(self.state)
                self.state.update(changed)
                before = copy.deepcopy(self.state)
                with self.assertRaisesRegex(ValueError, "contract, task or case scope"):
                    self.replay()
                self.assertEqual(before, self.state)
                self.state = original

    def test_wrong_dispatch_cannot_consume_the_next_check(self):
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "occurrence"):
            self.replay(record={"replay": {"dispatch_id": "foreign-dispatch"}})
        self.assertEqual(before, self.state)

    def test_wrong_repetition_cannot_skip_an_obligation(self):
        self.amend_tape(lambda document: document["checks"][0].update(repetition=2))
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "repetition"):
            self.replay()
        self.assertEqual(before, self.state)

    def test_foreign_or_reordered_ledger_is_refused_before_consumption(self):
        for used, pin in ((["second"], self.tape.sha256), (["first", "first"], self.tape.sha256), ([], "f" * 64)):
            with self.subTest(used=used, pin=pin):
                self.state["replay_result"] = {"consumed_checks": used, "manifest_sha256": pin}
                before = copy.deepcopy(self.state)
                with self.assertRaisesRegex(ValueError, "ledger"):
                    self.replay()
                self.assertEqual(before, self.state)

    def test_changed_source_cannot_consume_even_a_matching_command(self):
        path = self.workspace / "greet.py"
        path.write_text("operator drift\n")
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "source"):
            self.replay()
        self.assertEqual(before, self.state)
        self.assertEqual("operator drift\n", path.read_text())

    def test_complete_model_report_requires_logical_gate_and_every_archival_obligation(self):
        self.consume_all()
        qualified = copy.deepcopy(self.state)
        for logical in (False, None, 1):
            with self.subTest(logical=logical), self.assertRaisesRegex(ValueError, "logical completion"):
                checks.finish(
                    self.state, self.record, {"status": "TASK_COMPLETE"}, self.workspace, logical_ready=logical
                )
            self.assertEqual(qualified, self.state)
        for changed in (
            {"consumed_checks": ["first"]},
            {"consumed_checks": ["second", "first"]},
            {"manifest_sha256": "f" * 64},
        ):
            with self.subTest(changed=changed):
                self.state = copy.deepcopy(qualified)
                self.state["replay_result"].update(changed)
                before = copy.deepcopy(self.state)
                with self.assertRaisesRegex(ValueError, "archival obligations"):
                    checks.finish(
                        self.state, self.record, {"status": "TASK_COMPLETE"}, self.workspace, logical_ready=True
                    )
                self.assertEqual(before, self.state)

    def test_remaining_dispatch_or_noncomplete_decision_cannot_finish_replay(self):
        self.consume_all()
        before = copy.deepcopy(self.state)
        for record, decision in (
            ({"replay": {"ordinal": 1}}, {"status": "TASK_COMPLETE"}),
            (self.record, {"status": "CONTINUE"}),
        ):
            with self.subTest(record=record, decision=decision), self.assertRaises(ValueError):
                checks.finish(self.state, record, decision, self.workspace, logical_ready=True)
            self.assertEqual(before, self.state)

    def test_archival_finish_is_simulated_and_public_terminal_ledger_must_still_match(self):
        self.consume_all()
        checks.finish(self.state, self.record, {"status": "TASK_COMPLETE"}, self.workspace, logical_ready=True)
        self.assertEqual("REPLAY_COMPLETE", self.state["status"])
        self.assertIsNone(self.state["next_stage"])
        self.assertIs(self.state["replay_result"]["simulated"], True)
        self.assertIs(self.state["replay_result"]["current_proof"], False)
        view = {
            "status": "REPLAY_COMPLETE",
            "completion_current": False,
            "replay_progress": {"manifest_sha256": self.tape.sha256, "consumed": 1, "checks": "recorded"},
            "replay_result": copy.deepcopy(self.state["replay_result"]),
        }
        result = recording.verify_finished(self.tape, view, consumed_actions=0)
        self.assertEqual("TASK_COMPLETE", result["verdict"])
        self.assertIs(result["simulated"], True)
        self.assertIs(result["completion_current"], False)
        for changed in (
            {"consumed_checks": []},
            {"consumed_checks": ["second", "first"]},
            {"manifest_sha256": "f" * 64},
        ):
            with self.subTest(changed=changed):
                bad = copy.deepcopy(view)
                bad["replay_result"].update(changed)
                with self.assertRaisesRegex(ValueError, "ledger"):
                    recording.verify_finished(self.tape, bad, consumed_actions=0)


def make_retained_run(root, *, regression_cases=False, task=None):
    """Build a no-source-change attempt and declared, ordered archival checks."""
    projection = make_export_projection(root)
    attempt = projection["records"][0]
    attempt["before_ref"] = attempt["after_ref"]
    source = json.loads(Path(attempt["after_ref"]).read_text())
    if task is not None:
        prompt = Path(projection["run_dir"]) / "validator.prompt.md"
        prompt.write_text("Recorded handoff\nCURRENT HANDOFF DATA\n" + json.dumps({"current_task": task}))
        attempt["prompt"] = str(prompt)
    case_ids = ["B1"] if regression_cases else []
    suite, regression_command = "python3 suite.py", "python3 regression.py"
    independent = {
        "verdict": "PASS",
        "commands": [suite],
        "checks": [{"command": suite, "exit_code": 0}],
        "source_revision": source["revision"],
    }
    rows = [("independent", "independent_clean_replay", independent)]
    if regression_cases:
        rows = [
            (
                "baseline",
                "regression_baseline",
                {
                    "base": source["head"],
                    "command": suite,
                    "health": "red",
                    "receipt": {"command": suite, "exit_code": 1},
                },
            ),
            (
                "proof",
                "regression_proof",
                {
                    "verdict": "PASS",
                    "source_revision": source["revision"],
                    "commands": {"suite": suite, "regression": regression_command},
                    "case_scope": case_ids,
                    "case_tests": {"B1": ["test_greet"]},
                    "checks": {
                        "suite": {"command": suite, "exit_code": 0},
                        "regression": {"command": regression_command, "exit_code": 0},
                    },
                    "baseline": {"health": "red", "exit_code": 1},
                },
            ),
            *rows,
        ]
    for identity, purpose, value in rows:
        directory = Path(projection["run_dir"]) / identity
        directory.mkdir()
        output = directory / "check.log"
        output.write_text(identity + " archival output\n")
        value.update(output=str(output), output_sha256=util.file_hash(output))
        receipt = directory / "receipt.json"
        receipt.write_text(json.dumps(value))
        projection["checks"].append(
            {
                "id": identity,
                "dispatch": "dispatch-0",
                "purpose": purpose,
                "command": suite,
                "repetition": 1,
                "snapshot": "source-0",
                "receipt": str(receipt),
                "output": str(output),
                "authentication": "intact",
                "bindings": [],
                "context": {"contract_body": None, "task": {}, "case_ids": case_ids},
            }
        )
    if task is not None:
        for check in projection["checks"]:
            check.pop("context")
    tape = recording.load(recording.export(projection, root / "recording"))
    workspace = recording.prepare_workspace(tape, root / "replay")
    output = workspace / ".autocode" / "runs" / "replay" / "iterations" / "001" / "builder-01.json"
    output.parent.mkdir(parents=True)
    output.write_text('{"status":"COMPLETE"}\n')
    record = {
        "stage": "terra",
        "output": str(output),
        "replay": {"manifest_sha256": tape.sha256, "dispatch_id": "dispatch-0", "ordinal": 0},
    }
    state = {
        "workspace": str(workspace),
        "base_commit": source["head"],
        "status": "BUILDING",
        "stages": [],
        "settings": {
            "replay_recording": {
                "path": str(tape.manifest_path),
                "sha256": tape.sha256,
                "checks": "recorded",
                "dispatch_ids": ["dispatch-0"],
            }
        },
    }
    if task is not None:
        state["current_task"] = copy.deepcopy(task)
    if regression_cases:
        state["investigation"] = {"outcome": "reproduced", "test_cases": [{"id": "B1"}]}
    return tape, workspace, state, record, suite, regression_command


class RetainedArchivalReadinessTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.tape, self.workspace, self.state, self.record, self.suite, _ = make_retained_run(self.root)
        result = checks.replay([{"command": self.suite}], self.state, self.workspace, self.record)
        self.state["stages"] = [self.record]
        self.state["validation"] = {"output": self.record["output"], "check_replay": result}
        self.current = util.snapshot(self.workspace)
        self.assertTrue(self.ready())

    def ready(self, **changes):
        return checks.archival_ready(self.state, self.current, logical_ready=True, **changes)

    def test_intact_retained_archive_is_eligible_only_for_simulated_completion(self):
        self.assertTrue(self.ready())
        result = self.state["validation"]["check_replay"]
        self.assertIs(result["simulated"], True)
        self.assertIs(result["current_proof"], False)
        self.assertIs(self.state["replay_result"]["pending"], True)

    def test_retained_readiness_ignores_only_fresh_assignment_time_and_keeps_task_plan_binding(self):
        root = self.root / "assigned"
        root.mkdir()
        task = {
            "id": "task-1",
            "contract_hash": "a" * 64,
            "assigned_at": "2026-10-10T15:00:00+00:00",
            "description": "Keep the greeting contract",
            "validation_plan": ["python3 suite.py"],
        }
        tape, workspace, state, record, suite, _ = make_retained_run(root, task=task)
        result = checks.replay([{"command": suite}], state, workspace, record)
        state["stages"] = [record]
        state["validation"] = {"output": record["output"], "check_replay": result}
        current = util.snapshot(workspace)
        raw_manifest = tape.manifest_path.read_bytes()
        raw_receipt = Path(result["archival"]["receipt"]).read_bytes()
        state["current_task"]["assigned_at"] = "2026-10-10T16:00:00+00:00"
        self.assertTrue(checks.archival_ready(state, current, logical_ready=True))
        state["current_task"]["validation_plan"] = ["python3 foreign.py"]
        self.assertFalse(checks.archival_ready(state, current, logical_ready=True))
        state["current_task"]["validation_plan"] = task["validation_plan"]
        self.assertTrue(checks.archival_ready(state, current, logical_ready=True))
        self.assertEqual(raw_manifest, tape.manifest_path.read_bytes())
        self.assertEqual(raw_receipt, Path(result["archival"]["receipt"]).read_bytes())
        self.assertIs(result["simulated"], True)
        self.assertIs(result["current_proof"], False)

    def test_changed_retained_receipt_or_output_is_not_ready(self):
        archive = self.state["validation"]["check_replay"]["archival"]
        for key in ("receipt", "output"):
            with self.subTest(key=key):
                path = Path(archive[key])
                original = path.read_bytes()
                path.write_bytes(original + b" ")
                self.assertFalse(self.ready())
                path.write_bytes(original)
                self.assertTrue(self.ready())

    def test_same_bytes_at_foreign_or_aliased_paths_cannot_replace_owned_archive(self):
        archive = self.state["validation"]["check_replay"]["archival"]
        original = archive["receipt"]
        foreign = self.root / "foreign-receipt.json"
        foreign.write_bytes(Path(original).read_bytes())
        archive["receipt"] = str(foreign)
        self.assertFalse(self.ready())
        archive["receipt"] = original
        path = Path(original)
        payload = path.read_bytes()
        path.unlink()
        path.symlink_to(foreign)
        self.assertFalse(self.ready())
        self.assertEqual(payload, foreign.read_bytes())

    def test_changed_summary_or_archival_occurrence_cannot_use_unchanged_raw_receipt(self):
        original = copy.deepcopy(self.state)
        for changed in ({"commands": []}, {"source_revision": "f" * 64}, {"verdict": "FAIL"}):
            with self.subTest(changed=changed):
                self.state = copy.deepcopy(original)
                self.state["validation"]["check_replay"].update(changed)
                self.assertFalse(self.ready())
        for changed in ({"dispatch": "foreign"}, {"purpose": "regression_proof"}, {"manifest_sha256": "f" * 64}):
            with self.subTest(changed=changed):
                self.state = copy.deepcopy(original)
                self.state["validation"]["check_replay"]["archival"].update(changed)
                self.assertFalse(self.ready())

    def test_foreign_manifest_ledger_and_missing_validation_stage_are_not_ready(self):
        original = copy.deepcopy(self.state)
        self.state["replay_result"]["manifest_sha256"] = "f" * 64
        self.assertFalse(self.ready())
        self.state = copy.deepcopy(original)
        self.state["validation"]["output"] = "different stage output"
        self.assertFalse(self.ready())
        self.state = copy.deepcopy(original)
        self.state["stages"][0]["replay"]["dispatch_id"] = "foreign-dispatch"
        self.assertFalse(self.ready())

    def test_changed_approved_contract_task_or_due_cases_rejects_retained_scope(self):
        original = copy.deepcopy(self.state)
        for changed in (
            {"goal_contract": {"body": {"summary": "new approval"}}},
            {"current_task": {"description": "different assignment"}},
            {"investigation": {"outcome": "reproduced", "test_cases": [{"id": "B1"}]}},
        ):
            with self.subTest(changed=changed):
                self.state = copy.deepcopy(original)
                self.state.update(changed)
                self.assertFalse(self.ready())

    def test_model_complete_cannot_supply_missing_logical_or_named_case_gate(self):
        for logical in (False, None, 1):
            with self.subTest(logical=logical):
                self.assertFalse(checks.archival_ready(self.state, self.current, logical_ready=logical))
        self.assertFalse(self.ready(required_case_ids=["B1"]))


class RetainedRegressionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.tape, self.workspace, self.state, self.record, self.suite, self.regression_command = make_retained_run(
            self.root, regression_cases=True
        )
        self.proof = self.regression()

    def regression(self, **changes):
        arguments = {"case_ids": ["B1"], "suite": self.suite, "regression_command": self.regression_command}
        arguments.update(changes)
        return checks.regression(self.state, self.workspace, **arguments)

    def test_repeated_guard_reuses_only_pinned_archive_without_consuming_another_occurrence(self):
        before = copy.deepcopy(self.state)
        self.assertEqual(self.proof, self.regression())
        self.assertEqual(before, self.state)
        self.assertIs(self.proof["current_proof"], False)
        self.assertEqual(["baseline", "proof"], self.state["replay_result"]["consumed_checks"])

    def test_foreign_cached_ledger_cannot_bypass_selected_manifest_authority(self):
        self.state["replay_result"]["manifest_sha256"] = "f" * 64
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "ledger"):
            self.regression()
        self.assertEqual(before, self.state)

    def test_retained_baseline_and_proof_both_require_unchanged_receipt_and_output(self):
        for field in ("regression_baseline", "regression_proof"):
            for leaf in ("receipt", "output"):
                with self.subTest(field=field, leaf=leaf):
                    path = Path(self.state[field]["archival"][leaf])
                    original = path.read_bytes()
                    path.write_bytes(original + b" ")
                    with self.assertRaises(ValueError):
                        self.regression()
                    path.write_bytes(original)
                    self.assertEqual(self.proof, self.regression())

    def test_changed_command_or_named_case_scope_cannot_reuse_cached_proof(self):
        for changed in (
            {"suite": "python3 foreign_suite.py"},
            {"regression_command": "python3 other.py"},
            {"case_ids": ["B1", "B2"]},
        ):
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                self.regression(**changed)

    def test_wrong_archival_purpose_or_missing_named_case_cannot_be_reused(self):
        original = copy.deepcopy(self.state)
        self.state["regression_proof"]["archival"]["check_id"] = "baseline"
        with self.assertRaises(ValueError):
            self.regression()
        self.state = copy.deepcopy(original)
        self.state["regression_proof"]["case_tests"] = {}
        with self.assertRaises(ValueError):
            self.regression()

    def test_rebound_current_source_hash_cannot_repair_old_proof_source(self):
        (self.workspace / "greet.py").write_text("different operator source\n")
        self.state["regression_proof"]["source_revision"] = util.snapshot(self.workspace)["revision"]
        with self.assertRaises(ValueError):
            self.regression()

    def test_complete_archival_readiness_requires_all_due_regression_cases(self):
        replay = checks.replay([{"command": self.suite}], self.state, self.workspace, self.record)
        self.state["stages"] = [self.record]
        self.state["validation"] = {"output": self.record["output"], "check_replay": replay}
        current = util.snapshot(self.workspace)
        self.assertTrue(checks.archival_ready(self.state, current, logical_ready=True, required_case_ids=["B1"]))
        self.assertFalse(checks.archival_ready(self.state, current, logical_ready=True, required_case_ids=["B1", "B2"]))


class RecordedScopeSupportTests(unittest.TestCase):
    def serial(self):
        return {
            "settings": {"adaptive_planning": {"enabled": True}},
            "goal_contract": {
                "body": {
                    "acceptance_criteria": [{"id": "C1"}, {"id": "C2"}],
                    "milestones": [{"id": "M1", "acceptance_criteria": ["C1", "C2"]}],
                }
            },
            "current_task": {"milestone_id": "M1"},
        }

    def test_one_serial_milestone_must_cover_the_exact_approved_criteria(self):
        state = self.serial()
        checks.require_supported(state)
        for covered in (["C1"], ["C1", "C2", "foreign"], ["C1", "C1", "C2"]):
            bad = copy.deepcopy(state)
            bad["goal_contract"]["body"]["milestones"][0]["acceptance_criteria"] = covered
            with self.subTest(covered=covered), self.assertRaises(ValueError):
                checks.require_supported(bad)

    def test_multiple_milestones_or_foreign_current_task_is_unsupported(self):
        state = self.serial()
        state["goal_contract"]["body"]["milestones"].append({"id": "M2"})
        with self.assertRaises(ValueError):
            checks.require_supported(state)
        for task in ({"milestone_id": "foreign"}, {"milestone_id": "M1", "milestone_ids": ["M1"]}):
            state = self.serial()
            state["current_task"] = task
            with self.subTest(task=task), self.assertRaises(ValueError):
                checks.require_supported(state)

    def test_unimplemented_physical_or_operator_acceptance_obligations_refuse_simulation(self):
        for settings in (
            {"protected_tests": {"command": "real guard"}},
            {"protected_tests": {"files": ["test.py"]}},
            {"design_manifest": "design.json"},
            {"visual_profile": "browser"},
            {"visual_runtime": "browser"},
            {"orchestration": {"enabled": True, "max_parallel": 2}},
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                checks.require_supported({"settings": settings})
        for acceptance in ("brief_acceptance", "risk_acceptance"):
            with self.subTest(acceptance=acceptance), self.assertRaises(ValueError):
                checks.require_supported({"goal_contract": {"body": {acceptance: {"accepted": True}}}})
        with self.assertRaises(ValueError):
            checks.require_supported({"progressive": {"version": 1, "candidate": {}, "committed": True}})


class PrivateFixtureEmitterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.fixture = self.root / "fixture"
        self.fixture.mkdir()
        self.cli = Path(__file__).resolve().parents[1] / "tools" / "autocode.py"

    def output(self, prefix="project", *, iteration="001", stem="requirements-gather-01.json"):
        path = self.fixture / prefix / ".autocode" / "runs" / "run" / "iterations" / iteration / stem
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def invoke(self, rows, *, root=None, wire=None):
        environment = {key: value for key, value in os.environ.items() if not key.startswith("AUTOCODE_")}
        environment["AUTOCODE_REPLAY_FIXTURE_ROOT"] = str(self.fixture if root is None else root)
        return subprocess.run(
            [sys.executable, str(self.cli), "replay", "--emit-fixture-events"],
            input=wire if wire is not None else "".join(json.dumps(row) + "\n" for row in rows),
            env=environment,
            cwd=self.fixture,
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_actual_private_cli_accepts_serial_and_nested_worker_reports(self):
        for prefix, stem in (
            ("project", "requirements-gather-01.json"),
            ("project/.autocode/runs/main/builders/m1/project", "builder-01.json"),
        ):
            with self.subTest(prefix=prefix):
                output = self.output(prefix, stem=stem)
                event = {"type": "unknown.fixture.event", "opaque": "retained"}
                report = {"status": "COMPLETE", "summary": "fixture report"}
                result = self.invoke(
                    [{"kind": "report", "output": str(output), "report": report}, {"kind": "event", "event": event}]
                )
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(report, json.loads(output.read_text()))
                self.assertEqual([event], [json.loads(line) for line in result.stdout.splitlines()])

    def test_eof_preserves_unknown_event_and_never_invents_a_completed_turn(self):
        event = {"type": "item.started", "item": {"type": "unrecognized"}}
        result = self.invoke([{"kind": "event", "event": event}])
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual([event], [json.loads(line) for line in result.stdout.splitlines()])

    def test_outside_fixture_and_relative_reports_are_refused_without_writes(self):
        outside = self.root / "foreign" / ".autocode" / "runs" / "run" / "iterations" / "001" / "builder-01.json"
        outside.parent.mkdir(parents=True)
        for output in (str(outside), "project/.autocode/runs/run/iterations/001/builder-01.json"):
            with self.subTest(output=output):
                result = self.invoke([{"kind": "report", "output": output, "report": {}}])
                self.assertEqual(2, result.returncode)
                self.assertFalse(outside.exists())

    def test_aliased_fixture_root_and_parent_cannot_write_through_alias(self):
        output = self.output()
        alias = self.root / "fixture-alias"
        alias.symlink_to(self.fixture, target_is_directory=True)
        result = self.invoke([{"kind": "report", "output": str(output), "report": {}}], root=alias)
        self.assertEqual(2, result.returncode)
        self.assertFalse(output.exists())
        project_alias = self.fixture / "project-alias"
        project_alias.symlink_to(self.fixture / "project", target_is_directory=True)
        through_alias = project_alias / output.relative_to(self.fixture / "project")
        result = self.invoke([{"kind": "report", "output": str(through_alias), "report": {}}])
        self.assertEqual(2, result.returncode)
        self.assertFalse(output.exists())

    def test_dangling_report_leaf_is_refused_and_foreign_target_stays_absent(self):
        output = self.output()
        foreign = self.root / "must-stay-absent.json"
        output.symlink_to(foreign)
        result = self.invoke([{"kind": "report", "output": str(output), "report": {"status": "COMPLETE"}}])
        self.assertEqual(2, result.returncode)
        self.assertTrue(output.is_symlink())
        self.assertFalse(foreign.exists())

    def test_existing_report_is_never_overwritten(self):
        output = self.output()
        output.write_bytes(b"original report bytes\n")
        result = self.invoke([{"kind": "report", "output": str(output), "report": {"status": "COMPLETE"}}])
        self.assertEqual(2, result.returncode)
        self.assertEqual(b"original report bytes\n", output.read_bytes())

    def test_malformed_iteration_stem_and_wrong_runner_layout_refuse_output(self):
        for iteration, stem in (
            ("1", "builder-01.json"),
            ("01", "builder-01.json"),
            ("001", "builder-1.json"),
            ("001", "builder.json"),
            ("001", "Builder-01.json"),
            ("001", "builder-01.jsonl"),
        ):
            with self.subTest(iteration=iteration, stem=stem):
                output = self.output(iteration=iteration, stem=stem)
                result = self.invoke([{"kind": "report", "output": str(output), "report": {}}])
                self.assertEqual(2, result.returncode)
                self.assertFalse(output.exists())
        output = self.fixture / "iterations" / "001" / "builder-01.json"
        output.parent.mkdir(parents=True)
        result = self.invoke([{"kind": "report", "output": str(output), "report": {}}])
        self.assertEqual(2, result.returncode)
        self.assertFalse(output.exists())

    def test_malformed_protocol_cannot_select_an_executable_or_fabricate_report(self):
        output = self.output()
        rows = (
            {"kind": "event", "event": {}, "command": "must-not-execute"},
            {"kind": "report", "output": str(output), "report": []},
            {"kind": "command", "command": "must-not-execute"},
        )
        for row in rows:
            with self.subTest(row=row):
                result = self.invoke([row])
                self.assertEqual(2, result.returncode)
                self.assertFalse(output.exists())
        for wire in (
            '{"kind":"event","kind":"report","event":{}}\n',
            '{"kind":"event","event":{"elapsed":1e309}}\n',
            "not JSON\n",
        ):
            with self.subTest(wire=wire):
                result = self.invoke([], wire=wire)
                self.assertEqual(2, result.returncode)
                self.assertEqual("", result.stdout)


class CheckSummaryProjectionTests(unittest.TestCase):
    """Public summary references export exact owned evidence without path guesses."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        projection = make_export_projection(self.root)
        self.workspace = Path(projection["workspace"])
        self.run_dir = Path(projection["run_dir"])
        (self.workspace / "test_greet.py").write_text(
            "import unittest\nfrom pathlib import Path\n"
            "class Greeting(unittest.TestCase):\n"
            "    def test_changed_greeting(self):\n"
            "        self.assertEqual(\"print('after')\\n\", Path('greet.py').read_text())\n"
        )
        record = projection["records"][0]
        snapshot = util.snapshot(self.workspace)
        Path(record["after_ref"]).write_text(json.dumps(snapshot))
        record.update(
            stage="sol",
            role="sol",
            route_role="sol",
            original_stage="sol",
            before_ref=record["after_ref"],
            source_revision=snapshot["revision"],
        )
        command = shlex.join([sys.executable, "-B", "-m", "unittest", "test_greet", "-v"])

        def scratch_run(workspace, directory, *, command, timeout):
            directory.mkdir(parents=True)
            return verify.run_command(command, workspace, directory / "output.log", timeout=timeout)

        self.result = check_replay.replay(
            [{"command": command, "exit_code": 0, "evidence_ref": "event:check"}],
            self.workspace,
            self.run_dir,
            record,
            scratch_run,
            timeout=10,
        )
        self.summary = Path(self.result["receipt_path"])
        self.state = {
            "version": 2,
            "workspace": str(self.workspace),
            "run_dir": str(self.run_dir),
            "task": projection["task"],
            "status": "TASK_COMPLETE",
            "base_commit": projection["revisions"][0],
            "runner_revision": projection["origin"]["runner_revision"],
            "settings": {"engine": "codex", "roles": {"sol": {"model": "fixture-model"}}},
            "stages": [record],
            "validation": {"output": record["output"], "check_replay": self.result},
        }
        self.destination = self.root / "export"

    def public(self):
        original = copy.deepcopy(self.state)
        value = run_view.view(self.state)["replay_source"]
        self.assertEqual(original, self.state, "public status must remain a pure projection")
        self.assertEqual(1, len(value["checks"]))
        return value

    def legacy(self):
        self.result.pop("receipt_path")
        self.summary.write_text(json.dumps(self.result, indent=2) + "\n")
        return self.public()

    def retained(self):
        return {str(path): path.read_bytes() for path in self.run_dir.rglob("*") if path.is_file()}

    def refuse(self, projection):
        retained = self.retained()
        with self.assertRaises((ValueError, OSError)):
            recording.export(projection, self.destination)
        self.assertFalse(self.destination.exists(), "refusal must precede the first export write")
        self.assertEqual(retained, self.retained(), "export must not rewrite original evidence")

    def test_current_writer_ref_exports_the_summary_above_its_nested_command_log(self):
        self.assertEqual("PASS", self.result["verdict"])
        self.assertEqual(self.result, json.loads(self.summary.read_text()))
        raw_check = self.result["checks"][0]
        self.assertEqual(util.file_hash(Path(raw_check["output"])), raw_check["output_sha256"])
        self.assertNotEqual(self.summary.parent, Path(raw_check["output"]).parent)
        self.assertFalse((Path(raw_check["output"]).parent / "replay.json").exists())
        projection = self.public()
        self.assertEqual(str(self.summary), projection["checks"][0]["receipt"])
        retained = self.retained()
        tape = recording.load(recording.export(projection, self.destination))
        archived = checks.replay_receipt(tape, "check-0", self.root / "archive")
        self.assertEqual(self.summary.read_bytes(), Path(archived["receipt"]).read_bytes())
        self.assertEqual(Path(raw_check["output"]).read_bytes(), Path(archived["output"]).read_bytes())
        self.assertIs(archived["simulated"], True)
        self.assertIs(archived["current_proof"], False)
        self.assertEqual(retained, self.retained())

    def test_legacy_export_uses_the_unique_owned_stem_and_exact_public_summary_pin(self):
        projection = self.legacy()
        row = projection["checks"][0]
        self.assertIsNone(row["receipt"])
        self.assertEqual(Path(self.state["validation"]["output"]).stem, row["summary_stem"])
        self.assertEqual(util.digest(self.result), row["summary_sha256"])
        retained = self.retained()
        tape = recording.load(recording.export(projection, self.destination))
        self.assertNotIn("summary_stem", tape.document["checks"][0])
        archived = checks.replay_receipt(tape, "check-0", self.root / "archive")
        self.assertEqual(self.summary.read_bytes(), Path(archived["receipt"]).read_bytes())
        self.assertIs(archived["current_proof"], False)
        self.assertEqual(retained, self.retained())

    def test_missing_legacy_summary_keeps_status_available_but_refuses_export(self):
        self.legacy()
        self.summary.unlink()
        self.refuse(self.public())

    def test_duplicate_matching_legacy_summaries_are_ambiguous_without_path_preference(self):
        self.legacy()
        stem = Path(self.state["validation"]["output"]).stem
        duplicate = self.summary.parent.parent / (stem + "-" + "f" * 32)
        duplicate.mkdir()
        (duplicate / "replay.json").write_bytes(self.summary.read_bytes())
        self.refuse(self.public())

    def test_an_unrelated_stem_cannot_supply_even_an_identical_legacy_summary(self):
        self.legacy()
        self.summary.parent.rename(self.summary.parent.parent / ("unrelated-" + "e" * 32))
        self.refuse(self.public())

    def test_changed_legacy_summary_is_not_rebound_to_its_new_digest(self):
        self.legacy()
        value = copy.deepcopy(self.result)
        value["timeout_seconds"] += 1
        self.summary.write_text(json.dumps(value))
        self.refuse(self.public())

    def test_symlinked_legacy_summary_leaf_cannot_open_foreign_evidence(self):
        self.legacy()
        foreign = self.root / "foreign-summary.json"
        raw = self.summary.read_bytes()
        foreign.write_bytes(raw)
        self.summary.unlink()
        self.summary.symlink_to(foreign)
        self.refuse(self.public())
        self.assertEqual(raw, foreign.read_bytes())

    def test_symlinked_legacy_candidate_directory_cannot_supply_an_original_summary(self):
        self.legacy()
        directory = self.summary.parent
        foreign = self.root / "foreign-summary-directory"
        directory.rename(foreign)
        directory.symlink_to(foreign, target_is_directory=True)
        raw = (foreign / "replay.json").read_bytes()
        self.refuse(self.public())
        self.assertEqual(raw, (foreign / "replay.json").read_bytes())

    def test_explicit_current_reference_cannot_select_an_identical_foreign_summary(self):
        foreign = self.root / "foreign-summary.json"
        self.result["receipt_path"] = str(foreign)
        foreign.write_text(json.dumps(self.result))
        self.summary.write_bytes(foreign.read_bytes())
        raw = foreign.read_bytes()
        self.assertEqual(str(foreign), self.public()["checks"][0]["receipt"])
        self.refuse(self.public())
        self.assertEqual(raw, foreign.read_bytes())

    def test_malformed_present_reference_never_falls_back_to_valid_legacy_evidence(self):
        for reference in (None, "", [], {}, False, 0):
            with self.subTest(reference=reference):
                self.result["receipt_path"] = reference
                self.summary.write_text(json.dumps(self.result))
                projection = self.public()
                self.assertIsNone(projection["checks"][0]["receipt"])
                self.assertIsNone(projection["checks"][0]["summary_stem"])
                self.refuse(projection)


class PublicReplayProjectionTests(unittest.TestCase):
    def test_only_an_explicit_initial_workflow_is_exported_as_a_creation_pin(self):
        execution_policy = {"mode": "glm_first_v1", "approval_event": {"actor": "user"}}
        for workflow, expected in (
            ({"kind": "build", "source": "user", "output": None}, "build"),
            ({"kind": "build", "source": "model", "output": "recognizer.json"}, None),
            ({"kind": "build", "source": "model", "output": None}, None),
            ({"kind": "build", "source": "user"}, None),
            ({"kind": "build"}, None),
            ({"source": "user", "output": None}, None),
            ({"kind": None, "source": "user", "output": None}, None),
            ({}, None),
            (None, None),
            (True, None),
            ("corrupt", None),
        ):
            with self.subTest(workflow=workflow):
                state = {"workflow": workflow, "settings": {"workflow": execution_policy}}
                original = copy.deepcopy(state)
                projection = replay_view.project(state)
                self.assertEqual(execution_policy, projection["settings"]["workflow"])
                if expected is None:
                    self.assertNotIn("workflow_pin", projection)
                else:
                    self.assertEqual(expected, projection["workflow_pin"])
                self.assertEqual(original, state)

    def test_public_capture_preserves_archived_identity_without_install_inference(self):
        archived = "a" * 64
        records = [
            {"role": "terra", "events": "known.jsonl", "prompts_hash": archived},
            {"role": "sol", "events": "unknown.jsonl", "prompts_hash": None},
            {"role": "sol", "events": "legacy.jsonl"},
        ]
        state = {"status": "RUNNING", "prompts_hash": "b" * 64, "stages": records}
        original = copy.deepcopy(state)
        public = run_view.view(state)
        self.assertEqual("b" * 64, public["prompts_hash"])
        self.assertEqual(records, public["replay_source"]["records"])
        self.assertNotIn("prompts_hash", public["replay_source"]["records"][2])
        self.assertEqual(original, state)

    def test_actual_checkpoint_receipt_list_preserves_source_commit_recovery_candidates(self):
        state = {"base_commit": "a" * 40, "code_checkpoints": [{"commit": "b" * 40}, {"available": False}]}
        original = copy.deepcopy(state)
        self.assertEqual(["a" * 40, "b" * 40], replay_view.project(state)["revisions"])
        self.assertEqual(original, state)

    def test_legacy_empty_checkpoint_records_do_not_break_the_additive_public_projection(self):
        for checkpoints in (None, [], {}):
            with self.subTest(checkpoints=checkpoints):
                self.assertEqual(
                    ["a" * 40],
                    replay_view.project({"base_commit": "a" * 40, "code_checkpoints": checkpoints})["revisions"],
                )

    def test_invalid_checkpoint_container_does_not_invent_recovery_revisions(self):
        for checkpoints in (True, "not a receipt list", {"history": [{"commit": "b" * 40}]}):
            with self.subTest(checkpoints=checkpoints):
                self.assertEqual(
                    ["a" * 40],
                    replay_view.project({"base_commit": "a" * 40, "code_checkpoints": checkpoints})["revisions"],
                )

    def test_corrupt_stage_inventory_keeps_status_readable_and_export_unavailable(self):
        valid = {"stage": "terra", "role": "terra", "events": "builder.jsonl", "output": "builder.json"}
        for stages in (None, "none", True, {}, ["x", None, valid], [valid, "corrupt"]):
            with self.subTest(stages=stages), tempfile.TemporaryDirectory() as temporary:
                state = {"status": "RUNNING", "stages": stages}
                original = copy.deepcopy(state)
                public = run_view.view(state)
                self.assertIn("usage", public)
                self.assertIsNone(public["replay_source"])
                self.assertIsNone(public["replay_progress"])
                destination = Path(temporary) / "export"
                with self.assertRaises(recording.RecordingError):
                    recording.export(public["replay_source"], destination)
                self.assertFalse(destination.exists())
                self.assertEqual(original, state)

    def test_valid_neighbor_rows_preserve_every_replayable_dispatch_without_mutation(self):
        records = [
            {"role": "runner", "events": "runner.jsonl"},
            {"stage": "terra", "role": "terra", "events": "builder.jsonl", "output": "builder.json"},
            {"stage": "sol", "role": "sol", "events": "tester.jsonl", "output": "tester.json"},
            {"stage": "sol", "metrics": {}},
        ]
        state = {"status": "RUNNING", "stages": records}
        original = copy.deepcopy(state)
        self.assertEqual(records[1:3], run_view.view(state)["replay_source"]["records"])
        self.assertEqual(original, state)

    def test_corrupt_optional_inventory_cannot_silently_drop_an_action_or_receipt(self):
        valid = {"stage": "terra", "role": "terra", "events": "builder.jsonl", "output": "builder.json"}
        for key in ("contract_history", "user_events", "validation_archive", "regression_proofs"):
            for value in ("corrupt", {}, [None, {}], [{}, "corrupt"]):
                with self.subTest(key=key, value=value):
                    state = {"stages": [valid], key: value}
                    original = copy.deepcopy(state)
                    self.assertIsNone(replay_view.project(state))
                    self.assertEqual(original, state)

    def test_corrupt_nested_action_or_receipt_containers_refuse_the_whole_projection(self):
        updates = (
            {"settings": "corrupt"},
            {"goal_contract": "corrupt"},
            {"contract_history": [{"approval_event": "corrupt"}]},
            {"goal_contract": {"body": "corrupt"}},
            {"user_events": [{"kind": "answer", "question": "corrupt"}]},
            {"validation_archive": [{"validation": "corrupt"}]},
            {"validation": {"check_replay": "corrupt"}},
            {"validation": {"check_replay": {"checks": [{}, "corrupt"]}}},
            {"regression_baseline": "corrupt"},
        )
        for update in updates:
            with self.subTest(update=update):
                state = {"stages": [{"role": "terra", "events": "builder.jsonl"}], **update}
                original = copy.deepcopy(state)
                self.assertIsNone(replay_view.project(state))
                self.assertEqual(original, state)

    def test_missing_and_normal_empty_optional_collections_preserve_projection(self):
        state = {
            "stages": [],
            "contract_history": None,
            "user_events": None,
            "validation_archive": None,
            "regression_proofs": None,
            "settings": None,
            "goal_contract": None,
            "validation": None,
            "regression_baseline": None,
        }
        original = copy.deepcopy(state)
        self.assertEqual([], replay_view.project({})["records"])
        self.assertEqual([], replay_view.project(state)["records"])
        self.assertEqual(original, state)

    def test_progress_requires_an_intact_authority_and_its_ordered_dispatch_prefix(self):
        digest = "a" * 64
        authority = {"sha256": digest, "checks": "fresh", "dispatch_ids": ["dispatch-0", "dispatch-1"]}
        record = {"replay": {"manifest_sha256": digest, "dispatch_id": "dispatch-0", "ordinal": 0}}
        state = {"settings": {"replay_recording": authority}, "stages": [record]}
        original = copy.deepcopy(state)
        self.assertEqual(
            {"manifest_sha256": digest, "checks": "fresh", "consumed": 1, "next_dispatch": "dispatch-1"},
            replay_view.progress(state),
        )
        for malformed in (
            True,
            "corrupt",
            {},
            {**authority, "dispatch_ids": "dispatch-0"},
            {**authority, "dispatch_ids": ["dispatch-0", "dispatch-0"]},
            {**authority, "sha256": []},
            {**authority, "checks": "unknown"},
        ):
            with self.subTest(authority=malformed):
                self.assertIsNone(replay_view.progress({"settings": {"replay_recording": malformed}, "stages": []}))
        for replay in (
            {},
            "corrupt",
            {**record["replay"], "dispatch_id": "foreign"},
            {**record["replay"], "manifest_sha256": "b" * 64},
            {**record["replay"], "ordinal": True},
        ):
            with self.subTest(replay=replay):
                self.assertIsNone(replay_view.progress({"settings": state["settings"], "stages": [{"replay": replay}]}))
        self.assertEqual(original, state)

    def test_malformed_present_replay_authority_keeps_public_status_readable_but_not_current(self):
        for authority in (None, True, "corrupt", [], {}, {"checks": "fresh"}):
            with self.subTest(authority=authority):
                state = {"status": "TASK_COMPLETE", "settings": {"replay_recording": authority}, "stages": []}
                original = copy.deepcopy(state)
                public = run_view.view(state, completion_current=True)
                self.assertEqual("TASK_COMPLETE", public["status"])
                self.assertFalse(public["done"])
                self.assertIs(public["completion_current"], False)
                self.assertIsNone(public["replay_source"])
                self.assertIsNone(public["replay_progress"])
                self.assertEqual(original, state)

    def test_absent_replay_authority_preserves_ordinary_public_completion(self):
        for settings in (None, {}):
            with self.subTest(settings=settings):
                state = {"status": "TASK_COMPLETE", "stages": []}
                if settings is not None:
                    state["settings"] = settings
                original = copy.deepcopy(state)
                public = run_view.view(state, completion_current=True)
                self.assertTrue(public["done"])
                self.assertIsNone(public["replay_progress"])
                self.assertIsInstance(public["replay_source"], dict)
                self.assertNotIn("completion_current", public)
                self.assertEqual(original, state)


class CurrentProofRefusalTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.projection = make_export_projection(self.root)
        self.workspace = Path(self.projection["workspace"])
        self.run_dir = Path(self.projection["run_dir"])
        evidence = self.run_dir / "check.log"
        evidence.write_text("actual fixture evidence\n")
        criteria = [{"id": "C1", "criterion": "Contract holds", "status": "verified", "evidence": str(evidence)}]
        self.current = util.snapshot(self.workspace)
        self.state = {
            "version": 2,
            "workspace": str(self.workspace),
            "run_dir": str(self.run_dir),
            "task": "Keep the contract",
            "status": "TASK_COMPLETE",
            "acceptance_criteria": criteria,
            "criteria_revision": util.digest(util.criteria_definition(criteria)),
            "settings": {"roles": {}, "headroom": {"enabled": False}},
        }
        self.state["validation"] = {
            "verdict": "PASS",
            "criteria_revision": self.state["criteria_revision"],
            "source_revision": self.current["revision"],
            "checks": [{"exit_code": 0}],
            "findings": [],
            "unverified_criteria": [],
            "criterion_results": [{"id": "C1", "status": "PASS", "evidence_refs": [str(evidence)]}],
            "evidence_hashes": {str(evidence): util.file_hash(evidence)},
        }
        self.decision = {"status": "TASK_COMPLETE", "acceptance_criteria": copy.deepcopy(criteria)}

    def simulated(self):
        self.state["settings"]["replay_recording"] = {"checks": "recorded", "sha256": "a" * 64, "dispatch_ids": []}

    def test_current_completion_declines_otherwise_passing_evidence_even_when_independence_probe_disabled(self):
        self.assertTrue(completion.completion_ready(self.state, self.decision, self.current))
        self.simulated()
        for independent in (True, False):
            with self.subTest(independent=independent):
                self.assertFalse(
                    completion.completion_ready(
                        self.state, self.decision, self.current, require_independent=independent
                    )
                )

    def test_saved_complete_status_cannot_make_recorded_mode_done_or_current(self):
        self.assertTrue(run_view.view(self.state, completion_current=True)["done"])
        self.simulated()
        view = run_view.view(self.state, completion_current=True)
        self.assertIs(view["done"], False)
        self.assertIs(view["completion_current"], False)
        self.assertEqual("recorded", view["replay_progress"]["checks"])

    def test_direct_delivery_export_declines_recorded_mode_even_with_current_true_from_caller(self):
        self.state["stages"] = []
        for stage in ("sol", "astra_review"):
            report = self.run_dir / (stage + ".json")
            report.write_text("{}\n")
            self.state["stages"].append(
                {"stage": stage, "source_revision": self.current["revision"], "output": str(report)}
            )
        self.assertTrue(dependency.export(self.state, self.current, True)["verified_complete"])
        self.simulated()
        self.assertIsNone(dependency.export(self.state, self.current, True))

    def test_direct_regression_current_reader_declines_intact_proof_in_recorded_mode(self):
        output = self.run_dir / "proof.log"
        output.write_text("named test passed\n")
        proof_path = self.run_dir / "proof.json"
        proof = {
            "verdict": "PASS",
            "source_revision": self.current["revision"],
            "checks": {"candidate": {"exit_code": 0, "output": str(output), "output_sha256": util.file_hash(output)}},
            "case_tests": {"C1": ["test_c1_contract"]},
            "case_scope": ["C1"],
        }
        util.atomic_json(proof_path, proof)
        self.state["goal_contract"] = {"body": {"task_kind": "bugfix", "acceptance_criteria": []}}
        self.state["investigation"] = {"outcome": "reproduced", "test_cases": [{"id": "C1", "text": "Contract holds"}]}
        self.state["regression_proof"] = {
            **proof,
            "path": str(proof_path),
            "receipt_sha256": util.file_hash(proof_path),
        }
        # This handcrafted legacy receipt covers ordinary proof authentication,
        # not the new independently measured changed-line completion gate.
        self.assertTrue(
            regression.complete(self.state, self.current["revision"], all_due=True, require_changed_code=False)
        )
        self.assertFalse(regression.complete(self.state, self.current["revision"], all_due=True))
        self.simulated()
        self.assertFalse(
            regression.complete(self.state, self.current["revision"], all_due=True, require_changed_code=False)
        )
        self.assertFalse(regression.complete(self.state, self.current["revision"], all_due=True))

    def test_canonical_document_declines_recorded_progress_or_simulated_archival_result(self):
        arguments = {
            "run_identity": "fixture",
            "completed_at": None,
            "provenance": provenance.configured({}, "fake"),
            "binding": "binding",
        }
        self.assertEqual(
            "TASK_COMPLETE", evidence_document.build({"status": "TASK_COMPLETE"}, **arguments)["run"]["status"]
        )
        for marker in ({"replay_progress": {"checks": "recorded"}}, {"replay_result": {"simulated": True}}):
            with self.subTest(marker=marker), self.assertRaisesRegex(ValueError, "Archival"):
                evidence_document.build({"status": "TASK_COMPLETE", **marker}, **arguments)

    def test_canonical_publisher_does_not_change_current_files_or_anchor_in_recorded_mode(self):
        view = {"status": "TASK_COMPLETE"}
        self.assertTrue(evidence_export.publish(self.run_dir, self.state, view, provenance.configured({}, "fake")))
        saved = {path: path.read_bytes() for path in (self.run_dir / "evidence.json", self.run_dir / "evidence.md")}
        anchor = copy.deepcopy(self.state["evidence_export"])
        self.simulated()
        self.assertFalse(evidence_export.publish(self.run_dir, self.state, view, provenance.configured({}, "fake")))
        self.assertEqual(anchor, self.state["evidence_export"])
        for path, payload in saved.items():
            self.assertEqual(payload, path.read_bytes())

    def test_accept_completion_refuses_before_any_current_gate_or_state_mutation(self):
        import autocode as runner

        self.simulated()
        original = copy.deepcopy(self.state)
        with (
            patch.object(runner.goals, "approved") as approved,
            patch.object(runner.launch_inputs, "guard") as guard,
            patch.object(runner.completion_gate, "completion_ready") as ready,
        ):
            with self.assertRaisesRegex(ValueError, "simulation"):
                runner.accept_completion(self.state, self.workspace, run_dir=self.run_dir)
        approved.assert_not_called()
        guard.assert_not_called()
        ready.assert_not_called()
        self.assertEqual(original, self.state)


if __name__ == "__main__":
    unittest.main()
