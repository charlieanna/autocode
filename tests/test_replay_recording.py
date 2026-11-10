"""Saved recordings preserve source identities and rebind only valid named fields."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_replay_recording as recording
import autocode_util as util


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def make_recording(directory, *, payload=b"print('hello')\n", head="a" * 40):
    """Write a small real recording, including the source payload named by its pin."""
    directory = Path(directory)
    directory.mkdir()
    digest = sha(payload)
    (directory / "blobs").mkdir()
    (directory / "blobs" / digest).write_bytes(payload)
    document = {
        "version": 1,
        "kind": "autocode-replay",
        "origin": {
            "run_name": "recorded-fixture",
            "runner_revision": head,
            "provenance": {"kind": "fake"},
            "verdict": "TASK_COMPLETE",
        },
        "launch": {
            "task": "Print a greeting",
            "settings": {"engine": "codex", "roles": {"terra": {"model": "fixture-model"}}},
            "base": "initial",
        },
        "blobs": {digest: {"path": "blobs/" + digest, "bytes": len(payload), "mode": 0o644}},
        "snapshots": {"initial": {"head": head, "files": {"greet.py": digest}, "source_paths": []}},
        "dispatches": [],
        "artifacts": {},
        "actions": [],
        "checks": [],
        "expected": {"verdict": "TASK_COMPLETE", "stage_sequence": [], "checks_mode": "fresh"},
    }
    manifest = directory / "manifest.json"
    manifest.write_text(json.dumps(document, sort_keys=True) + "\n")
    return manifest, document


class RecordingValidationTests(unittest.TestCase):
    def test_planning_and_review_policy_survives_recording_without_transport_authority(self):
        settings = {
            **self.document["launch"]["settings"],
            "compact_bugfix_planning": True,
            "sensitive_paths": ["auth/**"],
            "review_routing": "combined",
            "implementation_flow": "standard",
        }
        self.document["launch"]["settings"] = recording.launch_settings(settings)
        self.save(self.document)
        saved = recording.load(self.manifest).document["launch"]["settings"]
        for key in ("compact_bugfix_planning", "sensitive_paths", "review_routing", "implementation_flow"):
            self.assertEqual(settings[key], saved[key])

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.manifest, self.document = make_recording(self.root / "recording")

    def save(self, document):
        self.manifest.write_text(json.dumps(document, sort_keys=True) + "\n")

    def test_manifest_and_every_source_payload_are_bound_to_their_exact_bytes(self):
        manifest_bytes = self.manifest.read_bytes()
        loaded = recording.load(self.manifest, expected_hash=sha(manifest_bytes))
        self.assertEqual(sha(manifest_bytes), loaded.sha256)
        self.assertEqual(self.document, loaded.document)
        identity = self.document["snapshots"]["initial"]["files"]["greet.py"]
        self.assertEqual(b"print('hello')\n", loaded.blob(identity))
        with self.assertRaisesRegex(ValueError, "manifest changed"):
            recording.load(self.manifest, expected_hash="f" * 64)

    def test_invalid_initial_workflow_pins_are_refused(self):
        for value in (None, True, [], {}, "", "unknown-workflow"):
            document = copy.deepcopy(self.document)
            document["launch"]["workflow_pin"] = value
            self.save(document)
            with self.subTest(pin=value), self.assertRaisesRegex(recording.RecordingError, "workflow pin"):
                recording.load(self.manifest)

    def test_optional_provider_light_effort_is_preserved_and_typed(self):
        self.assertNotIn("light_effort", recording.load(self.manifest).document["launch"]["settings"]["roles"]["terra"])
        document = copy.deepcopy(self.document)
        route = document["launch"]["settings"]["roles"]["terra"]
        route["light_effort"] = "low"
        self.save(document)
        self.assertEqual(
            "low", recording.load(self.manifest).document["launch"]["settings"]["roles"]["terra"]["light_effort"]
        )
        for value in (None, True, 1, [], {}):
            route["light_effort"] = value
            self.save(document)
            with self.subTest(value=value), self.assertRaisesRegex(recording.RecordingError, "light effort"):
                recording.load(self.manifest)

    def test_optional_reviewer_effort_origin_is_portable_and_typed(self):
        self.assertNotIn("effort_origins", recording.load(self.manifest).document["launch"]["settings"])
        document = copy.deepcopy(self.document)
        settings = document["launch"]["settings"]
        settings["roles"]["plan_reviewer"] = {"model": "fixture-model"}
        for origin in ("user_explicit", "runner_default", "auto_selected", "stage_choice"):
            settings["effort_origins"] = {"plan_reviewer": origin, "terra": origin}
            self.save(document)
            saved = recording.load(self.manifest).document["launch"]["settings"]
            self.assertEqual(settings["effort_origins"], recording.launch_settings(saved)["effort_origins"])
        for invalid in (
            None,
            True,
            [],
            {"plan_reviewer": "derived"},
            {"plan_reviewer": []},
            {"terra": "derived"},
            {"completion": "user_explicit"},
        ):
            settings["effort_origins"] = invalid
            self.save(document)
            with self.subTest(value=invalid), self.assertRaisesRegex(recording.RecordingError, "effort origin"):
                recording.load(self.manifest)

    def test_the_levels_a_model_offers_are_portable_and_typed(self):
        # A review under a stage-choice ceiling runs at a level its listing offers (2026-11-05), so a
        # replay keeps the listing to derive the same level.
        document = copy.deepcopy(self.document)
        settings = document["launch"]["settings"]
        settings["declared_variants"] = {"fixture-model": ["low", "medium", "high"]}
        self.save(document)
        saved = recording.load(self.manifest).document["launch"]["settings"]
        self.assertEqual(settings["declared_variants"], recording.launch_settings(saved)["declared_variants"])
        for invalid in (None, [], {"fixture-model": "high"}, {"fixture-model": [1]}):
            settings["declared_variants"] = invalid
            self.save(document)
            with self.subTest(value=invalid), self.assertRaisesRegex(recording.RecordingError, "reasoning variants"):
                recording.load(self.manifest)

    def test_unknown_fields_bool_version_and_missing_source_payload_are_refused(self):
        alternatives = []
        unknown = copy.deepcopy(self.document)
        unknown["provider_command"] = "must never execute"
        alternatives.append(unknown)
        boolean_version = copy.deepcopy(self.document)
        boolean_version["version"] = True
        alternatives.append(boolean_version)
        missing = copy.deepcopy(self.document)
        missing["blobs"] = {}
        alternatives.append(missing)
        for index, document in enumerate(alternatives):
            with self.subTest(case=index):
                self.save(document)
                with self.assertRaises(ValueError):
                    recording.load(self.manifest)

    def test_corrupt_source_blob_is_rejected_without_replacing_its_pin(self):
        [identity] = self.document["blobs"]
        blob = self.manifest.parent / "blobs" / identity
        original_manifest = self.manifest.read_bytes()
        blob.write_bytes(b"tampered source\n")
        with self.assertRaisesRegex(ValueError, "blob changed"):
            recording.load(self.manifest)
        self.assertEqual(original_manifest, self.manifest.read_bytes())
        self.assertEqual(identity, self.document["snapshots"]["initial"]["files"]["greet.py"])

    def test_source_traversal_and_runner_storage_are_refused(self):
        [identity] = self.document["blobs"]
        for name in ("../greet.py", "/greet.py", ".git/config", ".autocode/state.json", "folder/../greet.py"):
            document = copy.deepcopy(self.document)
            document["snapshots"]["initial"]["files"] = {name: identity}
            self.save(document)
            with self.subTest(path=name), self.assertRaises(ValueError):
                recording.load(self.manifest)

    def test_symlinked_blob_and_aliased_manifest_root_are_refused(self):
        [identity] = self.document["blobs"]
        blob = self.manifest.parent / "blobs" / identity
        foreign = self.root / "foreign-payload"
        foreign.write_bytes(blob.read_bytes())
        blob.unlink()
        blob.symlink_to(foreign)
        with self.assertRaises((ValueError, OSError)):
            recording.load(self.manifest)
        alias = self.root / "alias"
        alias.symlink_to(self.manifest.parent, target_is_directory=True)
        with self.assertRaises(ValueError):
            recording.load(alias / self.manifest.name)
        self.assertEqual(b"print('hello')\n", foreign.read_bytes())

    def test_duplicate_json_fields_are_not_a_portable_manifest(self):
        wire = self.manifest.read_text()
        self.assertIn('"version": 1', wire)
        self.manifest.write_text(wire.replace('"version": 1', '"version": 1, "version": 1'))
        with self.assertRaises(ValueError):
            recording.load(self.manifest)

    def test_nonfinite_json_numbers_cannot_become_launch_budget_authority(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            document = copy.deepcopy(self.document)
            document["launch"]["settings"]["limits"] = {"max_seconds": value}
            self.save(document)
            with self.subTest(value=value), self.assertRaises(ValueError):
                recording.load(self.manifest)
        for literal in ("1e309", "-1e309"):
            document = copy.deepcopy(self.document)
            document["launch"]["settings"]["limits"] = {"max_seconds": "OVERFLOW"}
            self.manifest.write_text(json.dumps(document).replace('"OVERFLOW"', literal))
            with self.subTest(literal=literal), self.assertRaises(ValueError):
                recording.load(self.manifest)

    def test_declared_payload_bounds_and_boolean_sizes_are_refused(self):
        [identity] = self.document["blobs"]
        for size in (True, -1, recording.MAX_ARTIFACT_BYTES + 1):
            document = copy.deepcopy(self.document)
            document["blobs"][identity]["bytes"] = size
            self.save(document)
            with self.subTest(size=size), self.assertRaises(ValueError):
                recording.load(self.manifest)


def make_export_projection(root):
    """Create one captured attempt with a real Git base and changed Builder source."""
    root = Path(root)
    workspace = root / "workspace"
    workspace.mkdir()
    git = lambda *args: subprocess.check_output(
        ["git", "-C", str(workspace), *args], text=True, stderr=subprocess.PIPE
    ).strip()
    git("init", "-q")
    (workspace / ".gitignore").write_text(".autocode/\n")
    (workspace / "greet.py").write_text("print('before')\n")
    git("add", ".")
    git("-c", "user.name=Replay tests", "-c", "user.email=replay@example.test", "commit", "-qm", "base")
    run_dir = workspace / ".autocode" / "runs" / "fixture"
    run_dir.mkdir(parents=True)
    before = util.snapshot(workspace)
    before_ref = run_dir / "builder.before.json"
    before_ref.write_text(json.dumps(before))
    (workspace / "greet.py").write_text("print('after')\n")
    after_ref = run_dir / "builder.after.json"
    after_ref.write_text(json.dumps(util.snapshot(workspace)))
    events = run_dir / "builder.jsonl"
    events.write_text(
        json.dumps({"type": "thread.started", "thread_id": "recorded-session"})
        + "\n"
        + json.dumps({"type": "turn.completed", "usage": {}})
        + "\n"
    )
    report = run_dir / "builder.json"
    report.write_text(json.dumps({"status": "COMPLETE", "summary": "source changed"}))
    record = {
        "stage": "terra",
        "iteration": 1,
        "role": "terra",
        "route_role": "terra",
        "engine": "codex",
        "provider": None,
        "model": "fixture-model",
        "reasoning_effort": None,
        "report_only": False,
        "original_stage": "terra",
        "turn": 1,
        "task_id": "task-1",
        "milestone_id": None,
        "contract_hash": "a" * 64,
        "criteria_revision": "b" * 64,
        "session": {"kind": "fresh", "id": "recorded-session"},
        "output_mode": "codex_events",
        "supports_sessions": True,
        "before_ref": str(before_ref),
        "after_ref": str(after_ref),
        "events": str(events),
        "output": str(report),
        "exit_code": 0,
        "bindings": [],
    }
    return {
        "version": 1,
        "workspace": str(workspace),
        "run_dir": str(run_dir),
        "origin": {
            "run_name": "fixture",
            "runner_revision": before["head"],
            "provenance": {"kind": "fake"},
            "verdict": "TASK_COMPLETE",
        },
        "task": "Print a greeting",
        "settings": {"engine": "codex", "roles": {"terra": {"model": "fixture-model"}}},
        "records": [record],
        "revisions": [before["head"]],
        "captures": [],
        "actions": [],
        "checks": [],
    }


class ExportAndMaterializationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.projection = make_export_projection(self.root)
        self.destination = self.root / "export"

    def test_native_capture_dependencies_refuse_export_before_writing_a_bundle(self):
        receipt = {
            "command": ["python3", "-m", "unittest"],
            "exit_code": 0,
            "full_output": "/old/scratch/check.log",
            "full_output_sha256": "0" * 64,
            "verification_copy": {"manifest_sha256": "0" * 64},
        }
        output = "check output\n" + json.dumps(receipt) + "\n"
        for backend in ("codex", "opencode"):
            for code in (0, None):
                with self.subTest(backend=backend, exit_code=code):
                    destination = self.root / f"export-{backend}-{code}"
                    record = self.projection["records"][0]
                    Path(record["output"]).write_text(
                        json.dumps({"checks": [{"evidence_ref": "/old/scratch/check.json"}]})
                    )
                    if backend == "codex":
                        event = {
                            "type": "item.completed",
                            "item": {
                                "type": "command_execution" if code is not None else "tool_output",
                                "command": "capture -- approved-check",
                                "aggregated_output": output,
                                "exit_code": code,
                            },
                        }
                    else:
                        event = {
                            "type": "tool_use",
                            "sessionID": "recorded-session",
                            "part": {
                                "id": "tool-1",
                                "tool": "bash",
                                "state": {
                                    "status": "completed",
                                    "input": {"command": "capture -- approved-check"},
                                    "output": output,
                                    "metadata": {"exit": code},
                                },
                            },
                        }
                    Path(record["events"]).write_text(json.dumps(event) + "\n")
                    original = Path(record["events"]).read_bytes()
                    with self.assertRaisesRegex(recording.RecordingError, "native capture.*unsupported in v1"):
                        recording.export(self.projection, destination)
                    self.assertFalse(destination.exists())
                    self.assertEqual(original, Path(record["events"]).read_bytes())

    def native_capture_example(self):
        record = self.projection["records"][0]
        receipt = Path(self.projection["workspace"]) / ".autocode/check.receipt"
        value = {"verification_copy": {"manifest_sha256": "0" * 64}, "full_output": str(receipt.with_suffix(".log"))}
        Path(record["events"]).write_text(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": "cat docs/capture-example.json",
                        "exit_code": 0,
                        "aggregated_output": json.dumps(value),
                    },
                }
            )
            + "\n"
        )
        return record, receipt

    def test_unreferenced_native_capture_documentation_output_remains_exportable(self):
        record, _ = self.native_capture_example()
        original = Path(record["events"]).read_bytes()
        manifest = recording.export(self.projection, self.destination)
        document = recording.load(manifest).document
        events = document["artifacts"][document["dispatches"][0]["events"]]
        self.assertEqual(json.loads(original), json.loads((self.destination / events["path"]).read_bytes()))
        self.assertEqual(sha(original), events["original_sha256"])
        self.assertEqual(original, Path(record["events"]).read_bytes())

    def test_native_capture_dependency_uses_the_same_original_answer_as_export(self):
        from unittest.mock import patch

        record, receipt = self.native_capture_example()
        original = Path(record["output"]).read_bytes()
        read = recording._read
        report_reads = []

        def changing_answer(root, name, **options):
            data, mode = read(root, name, **options)
            if name == Path(record["output"]).name:
                report_reads.append(name)
                if len(report_reads) > 1:
                    data = json.dumps({"checks": [{"evidence_ref": str(receipt)}]}).encode()
            return data, mode

        with patch.object(recording, "_read", side_effect=changing_answer):
            manifest = recording.export(self.projection, self.destination)
        document = recording.load(manifest).document
        answer = document["artifacts"][document["dispatches"][0]["report"]]
        self.assertEqual(json.loads(original), json.loads((self.destination / answer["path"]).read_bytes()))
        self.assertEqual(sha(original), answer["original_sha256"])
        self.assertEqual(1, len(report_reads), "dependency inspection re-read the retained answer")

    def test_redacted_native_capture_reference_still_refuses_using_original_answer(self):
        record, receipt = self.native_capture_example()
        Path(record["output"]).write_text(json.dumps({"checks": [{"evidence_ref": str(receipt)}]}))
        with self.assertRaisesRegex(recording.RecordingError, "native capture.*unsupported in v1"):
            recording.export(self.projection, self.destination, secret_values=(str(receipt),))
        self.assertFalse(self.destination.exists())

    def test_later_retained_answer_referencing_native_capture_refuses_before_export(self):
        first, receipt = self.native_capture_example()
        for field in ("output", "response"):
            with self.subTest(answer=field):
                second = copy.deepcopy(first)
                second.update(stage="sol", role="sol", route_role="sol", before_ref=first["after_ref"])
                second["events"] = str(Path(self.projection["run_dir"]) / "validator.jsonl")
                Path(second["events"]).write_text(json.dumps({"type": "turn.completed", "usage": {}}) + "\n")
                second.pop("output")
                second[field] = str(Path(self.projection["run_dir"]) / "validator.json")
                Path(second[field]).write_text(
                    json.dumps(
                        {"checks": [{"evidence_ref": str(receipt.relative_to(Path(self.projection["workspace"])))}]}
                    )
                )
                self.projection["records"] = [first, second]
                with self.assertRaisesRegex(recording.RecordingError, "native capture.*unsupported in v1"):
                    recording.export(self.projection, self.destination)
                self.assertFalse(self.destination.exists())

    def test_native_capture_mentioned_in_model_text_is_not_an_execution_dependency(self):
        record = self.projection["records"][0]
        receipt = {"verification_copy": {}, "full_output": "/do/not/read/this/path"}
        Path(record["events"]).write_text(
            json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(receipt)}})
            + "\n"
        )
        manifest = recording.export(self.projection, self.destination)
        self.assertEqual(1, len(recording.load(manifest).document["dispatches"]))

    def compacted_mutation_projection(self):
        import autocode_changed_code_evidence as evidence
        import autocode_context as context
        import autocode_proof_detail as detail

        run = Path(self.projection["run_dir"])
        record = self.projection["records"][0]
        source = util.snapshot(self.projection["workspace"])
        binding = {"source_revision": source["revision"], "proof_context": "recorded-proof"}
        output = str(run / "regression/proof-old/mutation-check/mutant-old.log")
        mutant = {
            "line": 1,
            "end_line": 1,
            "operator": "constant_return",
            "outcome": "killed",
            "evidence": [output],
            "failed_tests": ["tests.test_value"],
            "before": "return value # " + "large mutation detail " * 4000,
        }
        digest = source["files"]["greet.py"]
        gap = evidence.gap_id(*binding.values(), "greet.py", digest, 1)
        proof = {
            "changed_code_evidence": {
                **binding,
                "obligations": [
                    {
                        "gap_id": gap,
                        "handle": "L1",
                        "path": "greet.py",
                        "sha256": digest,
                        "line": 1,
                        "status": "reason_required",
                        "reason": "large reason detail " * 4000,
                    }
                ],
            },
            "changed_code_inputs": {
                "mutation": {**binding, "files": [{"path": "greet.py", "sha256": digest, "mutants": [mutant]}]},
            },
            "mutation_check": {"files": [{"path": "greet.py", "sha256": digest, "mutants": [mutant]}]},
        }
        packet, moved = context.compact({"stage": "sol", "regression_proof": proof}, run / "state.json")
        self.assertIn(detail.INPUTS, moved)
        self.assertIn(detail.OBLIGATIONS, moved)
        self.assertEqual(
            detail.INPUTS, packet["regression_proof"]["changed_code_inputs"]["mutation"]["details_artifact"]["field"]
        )
        record.update(stage="sol", original_stage="sol", role="sol", route_role="sol")
        Path(record["before_ref"]).write_bytes(Path(record["after_ref"]).read_bytes())
        prompt = run / "validator.prompt.md"
        record["prompt"] = str(prompt)
        prompt.write_text("Review the proof.\nCURRENT HANDOFF DATA\n" + json.dumps(packet))
        report = {"changed_code_exceptions": [{"gap_id": "L1", "evidence_refs": [output]}]}
        Path(record["output"]).write_text(json.dumps(report))
        return packet, proof, report

    def test_export_rebinds_mutation_and_gap_from_authentic_compacted_proof(self):
        import autocode_changed_code_evidence as evidence

        packet, proof, report = self.compacted_mutation_projection()
        original = copy.deepcopy(self.projection)
        prompt = Path(original["records"][0]["prompt"])
        prompt_bytes = prompt.read_bytes()
        archive_bytes = Path(packet["context_artifact"]["path"]).read_bytes()
        loaded = recording.load(recording.export(self.projection, self.destination))
        bindings = loaded.document["dispatches"][0]["bindings"]
        self.assertEqual({"mutation_capture", "changed_code_gap"}, {row["symbol"] for row in bindings})
        current = copy.deepcopy(proof)
        binding = {"source_revision": "replayed-source", "proof_context": "replayed-proof"}
        current["changed_code_evidence"].update(binding)
        current["changed_code_inputs"]["mutation"].update(binding)
        new_log = str(self.root / "new-run/proof-new/mutant-new.log")
        current["changed_code_inputs"]["mutation"]["files"][0]["mutants"][0]["evidence"] = [new_log]
        rebound = recording.rebind(
            {"report": report},
            bindings,
            {"changed_code_gap": binding, "mutation_capture": {"citations": evidence.mutation_citations(current)}},
        )
        decision = rebound["report"]["changed_code_exceptions"][0]
        self.assertEqual([new_log], decision["evidence_refs"])
        row = proof["changed_code_evidence"]["obligations"][0]
        self.assertEqual(
            evidence.gap_id(*binding.values(), row["path"], row["sha256"], row["line"]), decision["gap_id"]
        )
        archive = str(self.root / "new-run/archived-proof.json")
        simulated = recording.rebind(
            {"report": report},
            bindings,
            {
                "changed_code_gap": binding,
                "mutation_capture": {"citations": evidence.mutation_citations(current), "archive": archive},
            },
        )
        self.assertEqual([archive], simulated["report"]["changed_code_exceptions"][0]["evidence_refs"])
        self.assertEqual(original, self.projection)
        self.assertEqual(prompt_bytes, prompt.read_bytes())
        self.assertEqual(archive_bytes, Path(packet["context_artifact"]["path"]).read_bytes())

    def test_compacted_identity_recovery_requires_owned_pinned_complete_context(self):
        packet, _, _ = self.compacted_mutation_projection()
        record = self.projection["records"][0]
        prompt = Path(record["prompt"])
        archive = Path(packet["context_artifact"]["path"])
        original = archive.read_bytes()
        outside = self.root / "foreign-context.json"
        outside.write_bytes(original)
        alias = archive.with_name("alias.json")
        alias.symlink_to(archive)
        for fault in (
            "changed",
            "missing",
            "outside",
            "traversal",
            "symlink",
            "unpinned",
            "incomplete",
            "malformed",
            "bad_obligations",
        ):
            with self.subTest(fault=fault):
                archive.write_bytes(original)
                current = copy.deepcopy(packet)
                if fault == "changed":
                    archive.write_bytes(original + b"\n")
                elif fault == "missing":
                    archive.unlink()
                elif fault == "outside":
                    current["context_artifact"]["path"] = str(outside)
                elif fault == "traversal":
                    current["context_artifact"]["path"] = str(
                        Path(self.projection["run_dir"]) / "../foreign-context.json"
                    )
                elif fault == "symlink":
                    current["context_artifact"]["path"] = str(alias)
                elif fault == "unpinned":
                    current["context_artifact"].pop("sha256")
                else:
                    value = json.loads(original)
                    if fault == "incomplete":
                        value.pop("regression_proof_changed_code_inputs")
                    elif fault == "malformed":
                        value["regression_proof_changed_code_inputs"] = []
                    else:
                        value["regression_proof_obligations"] = {}
                    archive.write_text(json.dumps(value))
                    current["context_artifact"]["sha256"] = sha(archive.read_bytes())
                prompt.write_text("Review.\nCURRENT HANDOFF DATA\n" + json.dumps(current))
                with self.assertRaisesRegex(recording.RecordingError, "pinned context identity"):
                    recording.export(self.projection, self.destination)
                self.assertFalse(self.destination.exists())

    def test_unrelated_context_is_not_read_for_replay_identity(self):
        record = self.projection["records"][0]
        prompt = Path(self.projection["run_dir"]) / "builder.prompt.md"
        record["prompt"] = str(prompt)
        packet = {"context_artifact": {"path": "/unowned/missing.json", "fields": {"source_snapshot": {"total": 100}}}}
        prompt.write_text("Build.\nCURRENT HANDOFF DATA\n" + json.dumps(packet))
        loaded = recording.load(recording.export(self.projection, self.destination))
        self.assertEqual("terra", loaded.document["dispatches"][0]["stage"])

    def test_export_preserves_explicit_workflow_separately_from_execution_policy(self):
        self.projection["workflow_pin"] = "build"
        self.projection["settings"]["workflow"] = {"mode": "glm_first_v1"}
        original = copy.deepcopy(self.projection)
        manifest = recording.export(self.projection, self.destination)
        loaded = recording.load(manifest)
        self.assertEqual("build", loaded.document["launch"]["workflow_pin"])
        self.assertEqual({"mode": "glm_first_v1"}, loaded.document["launch"]["settings"]["workflow"])
        self.assertEqual(original, self.projection)

    def test_archived_prompt_identity_retains_known_unknown_and_legacy_absence(self):
        for index, identity in enumerate(("a" * 64, None, "absent")):
            with self.subTest(identity=identity):
                projection = copy.deepcopy(self.projection)
                record = projection["records"][0]
                if identity != "absent":
                    record["prompts_hash"] = identity
                original = copy.deepcopy(projection)
                destination = self.root / ("identity-" + str(index))
                tape = recording.load(recording.export(projection, destination))
                row = tape.document["dispatches"][0]
                if identity == "absent":
                    self.assertNotIn("prompts_hash", row)
                else:
                    self.assertEqual(identity, row["prompts_hash"])
                self.assertEqual(original, projection)

    def test_malformed_archived_prompt_identity_refuses_before_bundle_creation(self):
        values = (True, False, 0, "", "A" * 64, "a" * 63, "g" * 64, ["a" * 64], {})
        for index, identity in enumerate(values):
            with self.subTest(identity=identity):
                projection = copy.deepcopy(self.projection)
                projection["records"][0]["prompts_hash"] = identity
                destination = self.root / ("invalid-identity-" + str(index))
                with self.assertRaises(recording.RecordingError):
                    recording.export(projection, destination)
                self.assertFalse(destination.exists())

    def test_essential_archived_prompt_identity_cannot_be_masked_or_repinned(self):
        identity = "a" * 64
        self.projection["records"][0]["prompts_hash"] = identity
        original = copy.deepcopy(self.projection)
        with self.assertRaisesRegex(recording.RecordingError, "archived prompt identity"):
            recording.export(self.projection, self.destination, secret_values=[identity])
        self.assertFalse(self.destination.exists())
        self.assertEqual(original, self.projection)

    def test_export_masks_credentials_on_every_surface_and_keeps_raw_captures_unchanged(self):
        canary = "ghp_CANARY0123456789abcdef"
        opaque = "opaque-credential-not-a-standard-token"
        record = self.projection["records"][0]
        events = Path(record["events"])
        events.write_text(
            json.dumps({"type": "thread.started", "thread_id": "recorded-session", "api_key": opaque, "text": canary})
            + "\n"
        )
        Path(record["output"]).write_text(json.dumps({"summary": canary, "credentials": {"password": opaque}}))
        self.projection["task"] += " " + canary
        self.projection["origin"]["provenance"]["reason"] = canary
        raw_before = {path: path.read_bytes() for path in Path(self.projection["run_dir"]).iterdir() if path.is_file()}
        manifest = recording.export(self.projection, self.destination, secret_values=[opaque])
        loaded = recording.load(manifest)
        for path in self.destination.rglob("*"):
            if path.is_file():
                self.assertNotIn(canary.encode(), path.read_bytes())
                self.assertNotIn(opaque.encode(), path.read_bytes())
        for path, original in raw_before.items():
            self.assertEqual(original, path.read_bytes(), "export may not rewrite raw captures")
        event_entry = loaded.document["artifacts"]["events-0"]
        self.assertEqual(sha(raw_before[events]), event_entry["original_sha256"])
        self.assertNotEqual(event_entry["original_sha256"], event_entry["sha256"])
        self.assertTrue(event_entry["redacted"])

    def test_essential_source_redaction_refuses_before_creating_an_export(self):
        workspace = Path(self.projection["workspace"])
        (workspace / "greet.py").write_text("print('ghp_CANARY0123456789abcdef')\n")
        Path(self.projection["records"][0]["after_ref"]).write_text(json.dumps(util.snapshot(workspace)))
        with self.assertRaisesRegex(ValueError, "essential source needs redaction"):
            recording.export(self.projection, self.destination)
        self.assertFalse(self.destination.exists())

    def test_missing_intermediate_payload_refuses_before_writing_an_export(self):
        reference = Path(self.projection["records"][0]["before_ref"])
        snapshot = json.loads(reference.read_text())
        snapshot["files"]["greet.py"] = sha(b"unrecoverable intermediate\n")
        snapshot["revision"] = util.digest({"head": snapshot["head"], "files": snapshot["files"]})
        reference.write_text(json.dumps(snapshot))
        with self.assertRaisesRegex(ValueError, "exact intermediate source"):
            recording.export(self.projection, self.destination)
        self.assertFalse(self.destination.exists())

    def test_foreign_event_capture_and_damaged_source_snapshot_are_refused(self):
        foreign = self.root / "foreign.jsonl"
        foreign.write_text('{"type":"turn.completed"}\n')
        original_record = copy.deepcopy(self.projection["records"][0])
        self.projection["records"][0]["events"] = str(foreign)
        with self.assertRaisesRegex(ValueError, "outside its owned run"):
            recording.export(self.projection, self.destination)
        self.assertFalse(self.destination.exists())
        self.projection["records"][0] = original_record
        reference = Path(original_record["before_ref"])
        snapshot = json.loads(reference.read_text())
        snapshot["revision"] = "f" * 64
        reference.write_text(json.dumps(snapshot))
        with self.assertRaisesRegex(ValueError, "snapshot identity is damaged"):
            recording.export(self.projection, self.destination)
        self.assertFalse(self.destination.exists())

    def test_attempts_before_a_move_are_read_only_from_the_run_that_moved_here(self):
        # A chat answers Requirements in the person's folder, then moves to its own worktree (#3085).
        origin = Path(self.projection["run_dir"])
        moved = origin.parent / "moved"
        moved.mkdir()
        self.projection.update(
            run_dir=str(moved), moved_from={"workspace": self.projection["workspace"], "run_dir": str(origin)}
        )
        saved = origin / "state.json"
        saved.write_text(json.dumps({"build_worktree": {"state": "moved", "moved_to": {"run_dir": str(origin)}}}))
        with self.assertRaisesRegex(ValueError, "did not move to this run"):
            recording.export(self.projection, self.destination)
        self.assertFalse(self.destination.exists())

        saved.write_text(json.dumps({"build_worktree": {"state": "moved", "moved_to": {"run_dir": str(moved)}}}))
        manifest = recording.export(self.projection, self.destination)
        self.assertEqual(["dispatch-0"], [row["id"] for row in recording.load(manifest).document["dispatches"]])

        del self.projection["moved_from"]
        with self.assertRaisesRegex(ValueError, "outside its owned run"):
            recording.export(self.projection, self.root / "unmoved")
        self.assertFalse((self.root / "unmoved").exists())

    def test_external_provider_configuration_is_not_portable_launch_authority(self):
        self.projection["settings"].update(
            {
                "provider_environment": {"SECRET": "never copy"},
                "provider_config": {"startup": "never execute"},
                "transport_identity": {"sha256": "f" * 64},
            }
        )
        loaded = recording.load(recording.export(self.projection, self.destination))
        settings = loaded.document["launch"]["settings"]
        for key in ("provider_environment", "provider_config", "transport_identity"):
            self.assertNotIn(key, settings)
        self.assertEqual("fake", settings["evidence_provenance"]["kind"])
        self.assertEqual("recorded_observations", settings["evidence_provenance"]["basis"])
        self.assertEqual("fixture-model", settings["roles"]["terra"]["model"])

    def test_prepared_workspace_has_original_source_and_materializes_only_declared_effects(self):
        manifest = recording.export(self.projection, self.destination)
        loaded = recording.load(manifest)
        replay_workspace = self.root / "replay-workspace"
        recording.prepare_workspace(loaded, replay_workspace)
        self.assertEqual("print('before')\n", (replay_workspace / "greet.py").read_text())
        recording.verify_source(loaded, "source-0", replay_workspace)
        recording.materialize(loaded, "source-1", replay_workspace)
        self.assertEqual("print('after')\n", (replay_workspace / "greet.py").read_text())
        recording.verify_source(loaded, "source-1", replay_workspace)
        with self.assertRaises(ValueError):
            recording.prepare_workspace(loaded, replay_workspace)

    def test_workspace_changes_after_preparation_cannot_satisfy_saved_source_identity(self):
        loaded = recording.load(recording.export(self.projection, self.destination))
        replay_workspace = recording.prepare_workspace(loaded, self.root / "replay-workspace")
        (replay_workspace / "greet.py").write_text("operator changed source\n")
        with self.assertRaisesRegex(ValueError, "source bytes, modes or Git HEAD differ"):
            recording.verify_source(loaded, "source-0", replay_workspace)

    def test_copy_preserves_exact_manifest_spelling_and_bound_artifacts(self):
        loaded = recording.load(recording.export(self.projection, self.destination))
        copied = recording.copy_into(loaded, self.root / "copied-recording")
        self.assertEqual(loaded.sha256, copied.sha256)
        self.assertEqual(loaded.manifest_path.read_bytes(), copied.manifest_path.read_bytes())
        for identity in loaded.document["artifacts"]:
            self.assertEqual(loaded.artifact(identity), copied.artifact(identity))
        with self.assertRaises(ValueError):
            recording.copy_into(loaded, self.root / "copied-recording")

    def test_credential_bearing_source_filename_refuses_without_renaming_or_repinning_it(self):
        workspace = Path(self.projection["workspace"])
        canary = "opaque-filename-credential"
        path = workspace / (canary + ".py")
        path.write_text("print('safe content')\n")
        reference = Path(self.projection["records"][0]["after_ref"])
        reference.write_text(json.dumps(util.snapshot(workspace)))
        original = path.read_bytes(), reference.read_bytes()
        with self.assertRaisesRegex(ValueError, "redaction"):
            recording.export(self.projection, self.destination, secret_values=[canary])
        self.assertFalse(self.destination.exists())
        self.assertEqual(original, (path.read_bytes(), reference.read_bytes()))

    def test_credential_bearing_historical_tree_filename_is_not_leaked_in_git_pack(self):
        workspace = Path(self.projection["workspace"])
        canary = "opaque-historical-filename-credential"
        path = workspace / (canary + ".py")
        path.write_text("safe historical contents\n")

        def git(*args):
            return subprocess.check_output(["git", "-C", str(workspace), *args], text=True).strip()

        git("add", "--", path.name)
        git("-c", "user.name=Replay tests", "-c", "user.email=replay@example.test", "commit", "-qm", "intermediate")
        intermediate = git("rev-parse", "HEAD")
        path.unlink()
        git("add", "--", path.name)
        git("-c", "user.name=Replay tests", "-c", "user.email=replay@example.test", "commit", "-qm", "removed")
        self.projection["revisions"].extend([intermediate, git("rev-parse", "HEAD")])
        Path(self.projection["records"][0]["after_ref"]).write_text(json.dumps(util.snapshot(workspace)))
        self.assertNotIn(canary, json.dumps(util.snapshot(workspace)))
        with self.assertRaisesRegex(ValueError, "redaction"):
            recording.export(self.projection, self.destination, secret_values=[canary])
        self.assertFalse(self.destination.exists())


class RevalidatedAdmissionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        projection = make_export_projection(self.root)
        self.manifest = recording.export(projection, self.root / "bundle")
        self.loaded = recording.load(self.manifest)
        self.workspace = self.root / "replay-workspace"

    def terminal_view(self, *, status="TASK_COMPLETE"):
        return {
            "status": status,
            "completion_current": True,
            "replay_progress": {
                "manifest_sha256": self.loaded.sha256,
                "consumed": len(self.loaded.document["dispatches"]),
                "checks": "fresh",
            },
        }

    def test_admitted_manifest_basename_is_preserved_at_every_revalidation_boundary(self):
        renamed = self.manifest.with_name("portable-tape.json")
        self.manifest.rename(renamed)
        self.loaded = recording.load(renamed)
        self.assertEqual(renamed, self.loaded.manifest_path)
        recording.prepare_workspace(self.loaded, self.workspace)
        recording.verify_source(self.loaded, "source-0", self.workspace)
        recording.materialize(self.loaded, "source-1", self.workspace)
        recording.verify_source(self.loaded, "source-1", self.workspace)
        copied = recording.copy_into(self.loaded, self.root / "copy")
        self.assertEqual(renamed.read_bytes(), copied.manifest_path.read_bytes())
        self.assertEqual("TASK_COMPLETE", recording.verify_finished(self.loaded, self.terminal_view())["verdict"])

    def test_mutated_launch_projection_cannot_initialize_a_different_saved_source(self):
        self.loaded.document["launch"]["base"] = "source-1"
        with self.assertRaisesRegex(ValueError, "document changed"):
            recording.prepare_workspace(self.loaded, self.workspace)
        self.assertFalse(self.workspace.exists())
        self.assertEqual("source-0", recording.load(self.manifest).document["launch"]["base"])

    def test_mutated_snapshot_cannot_authorize_changed_workspace_bytes(self):
        recording.prepare_workspace(self.loaded, self.workspace)
        path = self.workspace / "greet.py"
        path.write_text("print('after')\n")
        self.loaded.document["snapshots"]["source-0"]["files"]["greet.py"] = self.loaded.document["snapshots"][
            "source-1"
        ]["files"]["greet.py"]
        with self.assertRaisesRegex(ValueError, "document changed"):
            recording.verify_source(self.loaded, "source-0", self.workspace)
        self.assertEqual("print('after')\n", path.read_text())

    def test_changed_manifest_refuses_materialization_before_any_source_effect(self):
        recording.prepare_workspace(self.loaded, self.workspace)
        before = util.snapshot(self.workspace)
        replacement = copy.deepcopy(self.loaded.document)
        replacement["origin"]["run_name"] = "other-recorded-run"
        self.manifest.write_text(json.dumps(replacement))
        with self.assertRaisesRegex(ValueError, "manifest changed"):
            recording.materialize(self.loaded, "source-1", self.workspace)
        self.assertEqual(before, util.snapshot(self.workspace))

    def test_cached_verdict_mutation_cannot_make_a_different_terminal_result_match(self):
        self.loaded.document["expected"]["verdict"] = "BLOCKED_HUMAN"
        with self.assertRaisesRegex(ValueError, "document changed"):
            recording.verify_finished(self.loaded, self.terminal_view(status="BLOCKED_HUMAN"))
        self.assertEqual("TASK_COMPLETE", recording.load(self.manifest).document["expected"]["verdict"])

    def test_mutated_decoded_bundle_cannot_be_copied_as_new_admitted_authority(self):
        self.loaded.document["origin"]["run_name"] = "other-recorded-run"
        destination = self.root / "copy"
        with self.assertRaisesRegex(ValueError, "document changed"):
            recording.copy_into(self.loaded, destination)
        self.assertFalse(destination.exists())

    def test_payload_changed_after_load_refuses_materialization_without_partial_source_changes(self):
        recording.prepare_workspace(self.loaded, self.workspace)
        before = util.snapshot(self.workspace)
        identity = self.loaded.document["snapshots"]["source-1"]["files"]["greet.py"]
        row = self.loaded.document["blobs"][identity]
        (self.loaded.root / row["path"]).write_bytes(b"foreign changed payload\n")
        with self.assertRaisesRegex(ValueError, "blob changed"):
            recording.materialize(self.loaded, "source-1", self.workspace)
        self.assertEqual(before, util.snapshot(self.workspace))


class SemanticActionTests(unittest.TestCase):
    def test_matching_semantic_question_uses_fresh_public_request_without_copying_old_token(self):
        questions = [{"id": "Q-1", "question": "Use punctuation?"}]
        action = {
            "kind": "answer",
            "match": {"kind": "answer", "questions": questions},
            "payload": {"answers": {"Q-1": "yes"}},
        }
        view = {"needs": {"kind": "answer", "questions": questions, "resolver_token": "fresh-token"}}
        self.assertEqual(action["payload"], recording.match_action(None, action, view))
        view["needs"]["questions"] = [{"id": "Q-1", "question": "Delete source?"}]
        with self.assertRaisesRegex(ValueError, "operator request differs"):
            recording.match_action(None, action, view)

    def test_unused_dispatch_or_wrong_terminal_verdict_cannot_be_reported_as_reproduced(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        manifest, _ = make_recording(Path(temporary.name).resolve() / "recording")
        loaded = recording.load(manifest)
        view = {
            "status": "TASK_COMPLETE",
            "replay_progress": {"manifest_sha256": loaded.sha256, "consumed": 0},
            "completion_current": True,
        }
        self.assertEqual("TASK_COMPLETE", recording.verify_finished(loaded, view)["verdict"])
        for changed in ({"consumed": 1}, {"manifest_sha256": "f" * 64}):
            with self.subTest(changed=changed):
                bad = copy.deepcopy(view)
                bad["replay_progress"].update(changed)
                with self.assertRaises(ValueError):
                    recording.verify_finished(loaded, bad)
        view["status"] = "PAUSED_VALIDATION"
        with self.assertRaises(ValueError):
            recording.verify_finished(loaded, view)

    def test_terminal_verdict_requires_every_operator_action_to_have_been_consumed(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        manifest, document = make_recording(Path(temporary.name).resolve() / "recording")
        document["actions"] = [
            {"before_dispatch": "end", "kind": "answer", "match": {"kind": "answer"}, "payload": {"text": "yes"}}
        ]
        manifest.write_text(json.dumps(document))
        tape = recording.load(manifest)
        view = {"status": "TASK_COMPLETE", "replay_progress": {"manifest_sha256": tape.sha256, "consumed": 0}}
        with self.assertRaisesRegex(ValueError, "action consumption"):
            recording.verify_finished(tape, view)
        for consumed in (0, 2, True):
            with self.subTest(consumed=consumed), self.assertRaises(ValueError):
                recording.verify_finished(tape, view, consumed_actions=consumed)
        self.assertEqual("TASK_COMPLETE", recording.verify_finished(tape, view, consumed_actions=1)["verdict"])

    def test_pending_recorded_obligations_cannot_echo_expected_complete_as_final_verdict(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        manifest, _ = make_recording(Path(temporary.name).resolve() / "recording")
        tape = recording.load(manifest)
        view = {
            "status": "REPLAY_COMPLETE",
            "replay_progress": {"manifest_sha256": tape.sha256, "consumed": 0},
            "replay_result": {
                "simulated": True,
                "manifest_sha256": tape.sha256,
                "pending": True,
                "recorded_verdict": "TASK_COMPLETE",
                "consumed_checks": [],
            },
        }
        with self.assertRaisesRegex(ValueError, "still pending"):
            recording.verify_finished(tape, view, consumed_actions=0)


class StructuredRedactionTests(unittest.TestCase):
    def test_opaque_values_under_quoted_credential_keys_are_masked(self):
        secret = "opaque-value-with-no-standard-token-prefix"
        original = {
            "api_key": secret,
            "nested": [{"password": secret}, {"authorization": "custom " + secret}],
            "ordinary": "safe",
        }
        before = copy.deepcopy(original)
        sanitized = recording.sanitize(original)
        self.assertNotIn(secret, json.dumps(sanitized))
        self.assertEqual("safe", sanitized["ordinary"])
        self.assertEqual(before, original)

    def test_known_explicit_secret_values_are_masked_in_nested_diagnostics(self):
        secret = "opaque-config-value-that-must-not-leave"
        sanitized = recording.sanitize({"diagnostics": ["prefix " + secret, {"text": "again " + secret}]}, [secret])
        self.assertNotIn(secret, json.dumps(sanitized))
        self.assertEqual(2, json.dumps(sanitized).count("[REDACTED]"))

    def test_shared_token_shapes_are_masked_and_plain_fields_are_retained(self):
        canary = "ghp_CANARY0123456789abcdef"
        sanitized = recording.sanitize({"text": "received " + canary, "status": "PASS", "exit_code": 0})
        self.assertNotIn(canary, json.dumps(sanitized))
        self.assertEqual(("PASS", 0), (sanitized["status"], sanitized["exit_code"]))

    def test_redaction_key_collisions_refuse_the_export(self):
        first = "ghp_FIRST0123456789abcdefghijk"
        second = "ghp_SECOND0123456789abcdefghij"
        with self.assertRaises(ValueError):
            recording.sanitize({first: "one", second: "two"})


class SourceRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

    def git(self, *arguments):
        return subprocess.check_output(
            ["git", "-C", str(self.workspace), *arguments], text=True, stderr=subprocess.PIPE
        ).strip()

    def commit(self, name, payload, *, executable=False):
        if not (self.workspace / ".git").exists():
            self.git("init", "-q")
        path = self.workspace / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        path.chmod(0o755 if executable else 0o644)
        self.git("add", "--", name)
        self.git("-c", "user.name=Replay tests", "-c", "user.email=replay@example.test", "commit", "-qm", "source")
        return self.git("rev-parse", "HEAD")

    def test_current_leaf_is_available_only_with_exact_bytes_and_executable_mode(self):
        payload = b"print('recorded source')\n"
        path = self.workspace / "app.py"
        path.write_bytes(payload)
        path.chmod(0o644)
        self.assertEqual(payload, recording.recover_leaf(self.workspace, "app.py", sha(payload)))
        path.chmod(0o755)
        with self.assertRaises(ValueError):
            recording.recover_leaf(self.workspace, "app.py", sha(payload))
        self.assertEqual(payload, recording.recover_leaf(self.workspace, "app.py", "executable:" + sha(payload)))
        path.write_bytes(b"different source\n")
        with self.assertRaises(ValueError):
            recording.recover_leaf(self.workspace, "app.py", "executable:" + sha(payload))

    def test_saved_git_object_recovers_intermediate_source_after_current_leaf_changes(self):
        original = b"print('before builder')\n"
        revision = self.commit("app.py", original, executable=True)
        (self.workspace / "app.py").write_bytes(b"print('after builder')\n")
        (self.workspace / "app.py").chmod(0o644)
        self.assertEqual(
            original,
            recording.recover_leaf(self.workspace, "app.py", "executable:" + sha(original), revisions=[revision]),
        )
        with self.assertRaises(ValueError):
            recording.recover_leaf(self.workspace, "app.py", sha(original), revisions=[revision])

    def test_unavailable_intermediate_source_cannot_be_invented_from_final_workspace(self):
        (self.workspace / "app.py").write_bytes(b"final source\n")
        with self.assertRaises(ValueError):
            recording.recover_leaf(self.workspace, "app.py", sha(b"missing intermediate source\n"))
        self.assertEqual(b"final source\n", (self.workspace / "app.py").read_bytes())

    def test_private_capture_must_match_the_original_payload_and_mode(self):
        original = b"uncommitted before builder\n"
        blob = self.root / "captured-blob"
        blob.write_bytes(original)
        capture = {"path": str(blob), "sha256": sha(original), "mode": 0o755}
        self.assertEqual(
            original,
            recording.recover_leaf(self.workspace, "app.py", "executable:" + sha(original), captures=[capture]),
        )
        with self.assertRaises(ValueError):
            recording.recover_leaf(self.workspace, "app.py", sha(original), captures=[capture])
        blob.write_bytes(b"tampered capture\n")
        with self.assertRaises(ValueError):
            recording.recover_leaf(self.workspace, "app.py", "executable:" + sha(original), captures=[capture])

    def test_symlink_payload_is_the_literal_link_without_reading_its_target(self):
        target = self.workspace / "private-target"
        target.write_text("never include target contents")
        link = self.workspace / "link"
        link.symlink_to("private-target")
        self.assertEqual(b"private-target", recording.recover_leaf(self.workspace, "link", "symlink:private-target"))
        self.assertEqual("never include target contents", target.read_text())
        for identity in ("symlink:../private-target", "symlink:/private-target"):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                recording.recover_leaf(self.workspace, "link", identity)

    def test_unsafe_paths_and_symlinked_parents_are_refused(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "app.py").write_bytes(b"foreign source\n")
        (self.workspace / "alias").symlink_to(outside, target_is_directory=True)
        for name in (
            "../outside/app.py",
            str(outside / "app.py"),
            ".git/config",
            ".autocode/state.json",
            "alias/app.py",
        ):
            with self.subTest(path=name), self.assertRaises(ValueError):
                recording.recover_leaf(self.workspace, name, sha(b"foreign source\n"))

    def test_deleted_and_uninitialized_submodule_entries_have_no_regular_payload(self):
        for identity in ("deleted", "uninitialized-submodule", "submodule:" + "a" * 64):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                recording.recover_leaf(self.workspace, "app.py", identity)


class IdentityRebindingTests(unittest.TestCase):
    def test_reviewed_draft_inference_rebinds_only_the_offered_snapshot(self):
        import autocode_replay_bindings as bindings

        old = "draft-failure-v1:" + "a" * 64
        new = "draft-failure-v1:" + "b" * 64
        receipt = {"snapshot": old, "rationale": "Keep the archived snapshot text " + old}
        value = {"report": {"concerns": [{"draft_failure_correction": receipt}]}}
        saved = bindings.bindings(value, {}, {}, "/old/work", "/old/run", [], draft_snapshot=old)
        self.assertEqual(["draft_inference_snapshot"], [row["symbol"] for row in saved])
        rebound = recording.rebind(value, saved, {"draft_inference_snapshot": new})
        self.assertEqual(
            {"snapshot": new, "rationale": receipt["rationale"]},
            rebound["report"]["concerns"][0]["draft_failure_correction"],
        )
        self.assertEqual([], bindings.bindings(value, {}, {}, "/old/work", "/old/run", [], draft_snapshot=new))
        with self.assertRaises(ValueError):
            recording.rebind(value, saved, {"draft_inference_snapshot": None})

    def test_mutation_citations_follow_only_the_same_kill_in_the_current_proof(self):
        import autocode_changed_code_evidence as evidence
        import autocode_replay_bindings as bindings

        old = "/old/run/regression/proof-old/mutation-check/mutant-old.log"
        new = "/new/run/regression/proof-new/mutation-check/mutant-new.log"

        def proof(output, test="test_value"):
            context = {"source_revision": "source", "proof_context": "context"}
            mutant = {
                "line": 2,
                "end_line": 2,
                "operator": "constant_return",
                "outcome": "killed",
                "evidence": [output],
                "failed_tests": [test],
            }
            return {
                "changed_code_evidence": context,
                "changed_code_inputs": {
                    "mutation": {**context, "files": [{"path": "calc.py", "sha256": "a" * 64, "mutants": [mutant]}]}
                },
            }

        value = {"report": {"changed_code_exceptions": [{"evidence_refs": [old]}]}}
        saved = bindings.bindings(
            value, {}, {}, "/old/workspace", "/old/run", [], mutations=evidence.mutation_citations(proof(old))
        )
        self.assertEqual("mutation_capture", saved[0]["symbol"])
        aliases = evidence.mutation_citations(proof(old))
        relative = {"report": {"changed_code_exceptions": [{"evidence_refs": [old.removeprefix("/old/")]}]}}
        self.assertEqual(saved, bindings.bindings(relative, {}, {}, "/old", "/old/run", [], mutations=aliases))
        relative["report"]["changed_code_exceptions"][0]["evidence_refs"][0] = "./" + old.removeprefix("/old/")
        self.assertEqual([], bindings.bindings(relative, {}, {}, "/old", "/old/run", [], mutations=aliases))
        self.assertEqual(
            {"report": {"changed_code_exceptions": [{"evidence_refs": [new]}]}},
            recording.rebind(
                value, saved, {"mutation_capture": {"citations": evidence.mutation_citations(proof(new))}}
            ),
        )
        archive = "/new/run/replay-checks/recorded-receipt.json"
        self.assertEqual(
            {"report": {"changed_code_exceptions": [{"evidence_refs": [archive]}]}},
            recording.rebind(
                value,
                saved,
                {"mutation_capture": {"citations": evidence.mutation_citations(proof(old)), "archive": archive}},
            ),
        )
        for available in ({}, evidence.mutation_citations(proof(new, "another_test"))):
            with self.subTest(available=available), self.assertRaisesRegex(ValueError, "no unique current capture"):
                recording.rebind(value, saved, {"mutation_capture": {"citations": available, "archive": archive}})

    def test_only_explicit_json_pointer_fields_change_and_input_is_untouched(self):
        original = {
            "checks": [{"evidence_ref": "/old/workspace/.autocode/check.json"}],
            "summary": "Keep /old/workspace in the recorded diagnostic",
            "nonce": "invalid-original-nonce",
        }
        before = copy.deepcopy(original)
        rebound = recording.rebind(
            original,
            [
                {
                    "pointer": "/checks/0/evidence_ref",
                    "symbol": "workspace",
                    "suffix": ".autocode/check.json",
                    "original_valid": True,
                }
            ],
            {"workspace": "/fresh/workspace"},
        )
        self.assertEqual("/fresh/workspace/.autocode/check.json", rebound["checks"][0]["evidence_ref"])
        self.assertEqual(original["summary"], rebound["summary"])
        self.assertEqual("invalid-original-nonce", rebound["nonce"])
        self.assertEqual(before, original)

    def test_wrong_original_nonce_and_foreign_session_are_not_repaired(self):
        value = {"capture_context": {"nonce": "wrong-nonce"}, "session": "foreign-session"}
        bindings = [
            {"pointer": "/capture_context/nonce", "symbol": "capture_nonce", "original_valid": False},
            {"pointer": "/session", "symbol": "session", "original_valid": False},
        ]
        self.assertEqual(
            value, recording.rebind(value, bindings, {"capture_nonce": "fresh-nonce", "session": "fresh-session"})
        )

    def test_valid_identity_fields_bind_to_fresh_values_without_global_replacement(self):
        value = {
            "capture_context": {"nonce": "old-nonce"},
            "session": "old-session",
            "diagnostic": "old-nonce old-session",
        }
        rebound = recording.rebind(
            value,
            [
                {"pointer": "/capture_context/nonce", "symbol": "capture_nonce", "original_valid": True},
                {"pointer": "/session", "symbol": "session", "original_valid": True},
            ],
            {"capture_nonce": "fresh-nonce", "session": "fresh-session"},
        )
        self.assertEqual("fresh-nonce", rebound["capture_context"]["nonce"])
        self.assertEqual("fresh-session", rebound["session"])
        self.assertEqual("old-nonce old-session", rebound["diagnostic"])

    def test_array_pointer_aliases_and_invalid_escape_sequences_are_refused(self):
        value = {"checks": [{"value": "old"}], "fields": {"~2": "old"}}
        for pointer in ("/checks/-1/value", "/checks/00/value", "/fields/~2"):
            with self.subTest(pointer=pointer), self.assertRaises(ValueError):
                recording.rebind(
                    value, [{"pointer": pointer, "symbol": "session", "original_valid": True}], {"session": "fresh"}
                )
        self.assertEqual("old", value["checks"][0]["value"])

    def test_missing_pointer_unknown_symbol_and_escaping_suffix_are_refused(self):
        for binding in (
            {"pointer": "/missing", "symbol": "session", "original_valid": True},
            {"pointer": "/value", "symbol": "arbitrary", "original_valid": True},
            {"pointer": "/value", "symbol": "workspace", "suffix": "../outside", "original_valid": True},
        ):
            with self.subTest(binding=binding), self.assertRaises(ValueError):
                recording.rebind(
                    {"value": "old"}, [binding], {"session": "fresh", "arbitrary": "new", "workspace": "/fresh"}
                )


if __name__ == "__main__":
    unittest.main()
