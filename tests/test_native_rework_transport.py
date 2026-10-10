"""Original same-owner repair transport, with synthetic native responses.

Held roots, original protection journals, first/rework writer admissions,
source deltas, actual ordinary Resolver routing/assignment and durable sink are
real. Provider responses and kernel conformance are explicit controlled seams.
A rejected response is never converted into an image PASS. The final fresh
all-case image/model qualification remains a separate composed live gate.
"""

import json
import subprocess
import sys
import unittest
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import autocode_contract_identity as contracts
import autocode_design_manifest as design
import autocode_goal_lifecycle as lifecycle
import autocode_process as processes
import autocode_rework_policy as rework
import autocode_source_scope as source_scope
import autocode_util as util
import autocode_visual_history as history
import autocode_visual_runtime as visual_runtime
import autocode_visual_scope as scope
import autopilot

import tests.test_native_writer as initial
import tests.test_native_writer_execution as first
from tests.visual_capture_fixtures import png


class OriginalBoundedReworkTransport(unittest.TestCase):
    setUp = first.OriginalWriterTransportControls.setUp
    prepare = first.OriginalWriterTransportControls.prepare
    acknowledge = first.OriginalWriterTransportControls.acknowledge
    stopped = first.OriginalWriterTransportControls.stopped
    preparation = first.OriginalWriterTransportControls.preparation

    def commit(self):
        # Complete real hash-bound references BEFORE the original approval.
        # Synthetic pixels exercise manifest integrity, never image acceptance.
        bundle = self.run / "controlled-reference"
        bundle.mkdir()
        png(bundle / "only.png")
        util.atomic_json(bundle / "only.json", {"controlled_reference": True})
        body = {
            "version": 1,
            "files": [{"key": "Fixture", "nodes": ["1:2"]}],
            "cases": [
                {
                    "id": "only",
                    "file_key": "Fixture",
                    "node_id": "1:2",
                    "state": "controlled",
                    "route": "/only",
                    "implementation_paths": ["ignored.txt", "future.txt"],
                    "viewport": {"width": 2, "height": 1, "device_scale_factor": 1},
                    "export_scale": 1,
                    "artifacts": {
                        kind: {"path": name, "sha256": util.file_hash(bundle / name)}
                        for kind, name in (("screenshot", "only.png"), ("design_context", "only.json"))
                    },
                }
            ],
        }
        index = bundle / "inventory-manifest.json"
        util.atomic_json(index, body)
        manifest = {"body": body, "root": str(bundle), "manifest_hash": util.digest(body), "manifest_path": str(index)}
        self.assertEqual(manifest, design.verify(manifest))
        self.state["settings"]["design_manifest"] = manifest
        self.assertEqual(self.factory.runtime, Path(history.__file__).resolve().parent)
        self.assertEqual(self.factory.runtime, self.factory.history.runtime_root)
        self.assertEqual(
            history.runtime_files(self.factory.runtime), history.runtime_files(Path(history.__file__).parent)
        )
        # Install the complete controlled role distinction BEFORE approval.
        for role, model in (
            ("sol", "controlled/independent-sol-high"),
            ("astra_review", "controlled/completion-high"),
            ("astra_resolve", "controlled/independent-mimo-high"),
        ):
            self.state["settings"]["roles"][role] = {
                "engine": "opencode",
                "provider": "opencode",
                "model": model,
                "reasoning_effort": "high",
            }
        goal = self.state["goal_contract"]
        if getattr(self, "initial_validate", False):
            inputs = ["ignored.txt", "future.txt", "tracked.txt"]
            goal["body"]["constraints"].append('VISUAL_WRITER_PATHS=["ignored.txt","future.txt"]')
            goal["body"]["milestones"][0]["affected_paths"] = inputs
            goal["body"]["initial_task"].update(kind="validate", affected_paths=deepcopy(inputs))
        goal["body"]["acceptance_criteria"][0]["criterion"] = (
            "The selected source remains constrained and is independently checked"
        )
        goal["hash"] = util.digest({key: goal[key] for key in ("task_id", "revision", "body")})
        self.token = contracts.token(goal)
        self.state["displayed_goal"] = self.token
        self.state["planning"]["final_token"] = self.token
        self.owner._append(
            "planning-committed",
            {
                "contract": deepcopy(goal),
                "settings": deepcopy(self.state["settings"]),
                "source": deepcopy(self.raw),
                "token": self.token,
                "pins": {str(self.anchor): util.file_hash(self.anchor)},
            },
        )
        return initial.FirstWriterAuthorityTests.commit(self)

    def bound_report(self, report):
        return {
            **deepcopy(report),
            "task_id": self.state["current_task"]["id"],
            "contract_hash": self.state["goal_contract"]["hash"],
            "contract_revision": self.state["goal_contract"]["revision"],
        }

    def readonly_step(self, stage, report, snapshot, *, context=None):
        base = self.run / ("controlled-" + stage)
        output, events = base.with_suffix(".json"), base.with_suffix(".jsonl")
        before_ref, after_ref = base.with_suffix(".before.json"), base.with_suffix(".after.json")
        session = "controlled-native-" + stage
        payload = """import json,pathlib,sys
report=json.loads(sys.argv[3]);session=sys.argv[4]
rows=[{'type':'step_start','sessionID':session,'part':{'id':'start','messageID':'message','sessionID':session}},
 {'type':'text','sessionID':session,'part':{'id':'answer','messageID':'message','sessionID':session,'text':json.dumps(report)}},
 {'type':'step_finish','sessionID':session,'part':{'id':'finish','messageID':'message','sessionID':session,'reason':'stop'}}]
pathlib.Path(sys.argv[1]).write_text(json.dumps(report)+'\\n')
pathlib.Path(sys.argv[2]).write_text(''.join(json.dumps(row)+'\\n' for row in rows))
"""
        command = [
            str(Path(sys.executable).resolve()),
            "-I",
            "-S",
            "-B",
            "-c",
            payload,
            str(output),
            str(events),
            json.dumps(report),
            session,
        ]
        boundary = {**deepcopy(self.boundary), "allow_write": False, "write_paths": [], "writer_path_kinds": {}}
        environment = {**self.environment, "AUTOCODE_TOOL_CONTAINMENT": json.dumps(boundary)}
        route = self.state["settings"]["roles"][stage]
        record = {
            "stage": stage,
            "role": stage,
            "engine": "opencode",
            "command": command,
            "task_id": self.state["current_task"]["id"],
            "contract_hash": self.state["goal_contract"]["hash"],
            "contract_revision": self.state["goal_contract"]["revision"],
            "source_revision": snapshot["revision"],
            "launch_route": deepcopy(route),
            "before_ref": str(before_ref),
            "after_ref": str(after_ref),
            "events": str(events),
            "output": str(output),
            "expected_session": session,
        }
        if context is not None:
            record["visual_runtime"] = deepcopy(context)
        util.atomic_json(before_ref, snapshot)
        handle = self.factory.prepare_launch(
            self.state,
            snapshot,
            record,
            boundary=boundary,
            command=command,
            environment=environment,
            native_role_isolation=SimpleNamespace(_writer_execution=None),
        )
        self.state["active_stage"] = record
        self.sink.write(self.run / "state.json", self.state)
        options = {
            "cwd": self.root,
            "env": environment,
            "start_new_session": True,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        with self.context.held_launch(
            handle,
            command=command,
            environment=environment,
            options=options,
            receipt_path=base.with_suffix(".supervision.json"),
            timeout=15,
        ) as (child, on_root):
            code, expired = processes.wait_for_stage(child, 15, lambda _: None, on_root=on_root)
        self.assertEqual((0, False), (code, expired))
        self.assertFalse(processes.live_processes([child.supervision["provider"]]))
        after = source_scope.snapshot(self.root, self.state)
        self.assertEqual(snapshot, after)
        util.atomic_json(after_ref, after)
        record.update(exit_code=code, finished_at=util.now())
        rework.capture(record, report)
        self.factory.finish_launch(handle, record, after)
        self.state.pop("active_stage")
        self.state.setdefault("stages", []).append(deepcopy(record))
        return record

    def retain_failed_sol(self, builder, snapshot):
        derived = scope.derive(
            self.state["goal_contract"]["body"],
            self.state["settings"]["design_manifest"]["body"],
            self.state["current_task"],
            accepted_milestones=(),
            accepted_case_input_paths={},
            changed_paths=["ignored.txt", "future.txt"] if builder is not None else [],
        )
        context = {
            "status": "READY",
            "snapshot": deepcopy(snapshot),
            "pins": {},
            "producer": (
                {"kind": "native_builder", "events": builder["events"]}
                if builder is not None
                else visual_runtime._producer(self.state, self.run, snapshot)
            ),
            "current": {
                "runtime_hash": util.digest(history.runtime_files(self.factory.runtime)),
                "visual_scope": derived,
            },
        }
        manifest = self.run / "controlled-sol-launch.json"
        util.atomic_json(manifest, context)
        context.update(launch_manifest=str(manifest), launch_sha256=util.file_hash(manifest))
        report = self.bound_report(
            {
                "verdict": "BLOCKED",
                "checks": [],
                "criterion_results": [{"id": "C1", "status": "BLOCKED", "evidence_refs": []}],
                "diagnostic": "Synthetic original independent response; no passing image or functional claim",
            }
        )
        record = self.readonly_step("sol", report, snapshot, context=context)
        accepted = {
            **report,
            "output": record["output"],
            "source_revision": snapshot["revision"],
            "evidence_hashes": deepcopy(record["rework_evidence"]["hashes"]),
        }
        self.state["validation"] = deepcopy(accepted)
        selector = self.factory.history.retain_rejection(
            self.state, record, context=context, accepted_validation=accepted, snapshot=snapshot
        )
        handles = self.factory.evidence_publications()
        self.assertEqual(len(handles), 1)
        with self.sink.publication(publications=handles):
            self.sink.write(self.run / "state.json", self.state)
        self.sink.publish(self.state, publications=handles)
        replayed = self.factory.history.verify_rejection(selector)
        self.assertEqual(replayed["metadata"]["functional"]["verdict"], "BLOCKED")
        self.assertNotIn("receipt", replayed["metadata"])
        self.assertEqual(self.factory.current_decision_review(self.state, snapshot=snapshot), selector)
        with self.assertRaises(util.Paused):
            self.factory.history.verify_review(selector)
        return selector

    def repair_write(self, snapshot):
        self.before = snapshot
        self.isolation = SimpleNamespace(_writer_execution=None)
        before_ref = self.run / "repair.before.json"
        util.atomic_json(before_ref, snapshot)
        self.record = {
            "stage": "terra",
            "role": "terra",
            "engine": "opencode",
            "command": self.command,
            "task_id": self.state["current_task"]["id"],
            "contract_hash": self.state["goal_contract"]["hash"],
            "contract_revision": self.state["goal_contract"]["revision"],
            "source_revision": snapshot["revision"],
            "launch_route": deepcopy(self.state["settings"]["roles"]["terra"]),
            "before_ref": str(before_ref),
            "events": str(self.run / "repair.jsonl"),
            "output": str(self.run / "repair.json"),
        }
        self.command = [
            str(Path(sys.executable).resolve()),
            "-I",
            "-S",
            "-B",
            "-c",
            'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text("bounded actual correction\\n")',
            str(self.root / "ignored.txt"),
        ]
        self.record["command"] = self.command
        handle = self.preparation()
        with self.assertRaises(util.Paused):
            self.owner._idle()
        with self.assertRaises(util.Paused):
            self.factory.history.verify_rejection(self.rejected_selector)
        with self.assertRaises(util.Paused):
            with self.owner._replay_before(replace(handle, nonce="foreign")):
                pass
        options = {
            "cwd": self.root,
            "env": self.environment,
            "start_new_session": True,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        with self.context.held_launch(
            handle,
            command=self.command,
            environment=self.environment,
            options=options,
            receipt_path=self.run / "repair.supervision.json",
            timeout=15,
        ) as (child, on_root):
            code, expired = processes.wait_for_stage(child, 15, lambda _: None, on_root=on_root)
        self.assertEqual((0, False), (code, expired))
        self.assertFalse(processes.live_processes([child.supervision["provider"]]))
        after = source_scope.snapshot(self.root, self.state)
        Path(self.record["events"]).write_text('{"controlled":"not-native-provider-evidence"}\n')
        Path(self.record["output"]).write_text("{}\n")
        after_ref = self.run / "repair.after.json"
        util.atomic_json(after_ref, after)
        self.record.update(
            exit_code=code, after_ref=str(after_ref), source_revision=after["revision"], finished_at=util.now()
        )
        self.factory.finish_launch(handle, self.record, after)
        self.state.pop("active_stage")
        execution = self.owner._writer_executions[handle.nonce]
        if getattr(self, "initial_validate", False):
            self.assertIsNone(execution.admission["body"]["lineage"])
        else:
            self.assertEqual(execution.admission["body"]["lineage"]["kind"], "rework")
        self.assertEqual(execution.delta["body"]["after"], after)
        self.state.setdefault("stages", []).append(deepcopy(self.record))
        return after

    def test_actual_rejection_resolver_assignment_and_same_owner_repair_stay_nonterminal(self):
        original_contract, original_settings = deepcopy(self.state["goal_contract"]), deepcopy(self.state["settings"])
        self.state.update(
            version=3, iteration=1, task="controlled bounded repair", status="RUNNING", next_stage="terra"
        )
        self.state["acceptance_criteria"] = [
            {
                **deepcopy(self.state["goal_contract"]["body"]["acceptance_criteria"][0]),
                "status": "blocked",
                "evidence": "",
            }
        ]
        original_task = deepcopy(self.state["current_task"])
        if getattr(self, "initial_validate", False):
            snapshot = self.before
            builder = None
            self.state["stages"] = []
        else:
            handle, snapshot = self.stopped()
            builder = deepcopy(self.record)
            self.state["stages"] = [builder]
        selector = self.retain_failed_sol(builder, snapshot)
        self.rejected_selector = selector
        proof = self.factory.decision_review_proof(self.state, snapshot=snapshot, selector=selector)
        self.assertFalse(proof.current_scope_accepted)
        self.assertIsNone(proof.receipt_sha256)
        with self.assertRaises(util.Paused):
            self.factory.require_final_integration(self.state, snapshot=snapshot, review_selector=selector)
        spec = {
            key: deepcopy(value)
            for key, value in original_task.items()
            if key in ("kind", "milestone_id", "requirements", "acceptance_criteria", "validation_plan", "findings")
        }
        spec["kind"] = "implement"
        decision = self.bound_report(
            {
                "status": "REWORK",
                "next_objective": "Correct the independently retained source defect",
                "next_task": spec,
                "affected_paths": ["ignored.txt", "future.txt"],
                "acceptance_criteria": deepcopy(self.state["acceptance_criteria"]),
                "evidence": ["retained blocked original Sol"],
                "user_request": {"kind": "none"},
            }
        )
        completion = self.readonly_step("astra_review", decision, snapshot)
        autopilot.queue_resolution(self.state, decision, completion)
        self.assertEqual(self.state["next_stage"], "astra_resolve")
        self.factory.require_resolver_request(self.state, snapshot=snapshot)
        resolved = {**deepcopy(decision), "diagnosis": "Synthetic independent bounded defect diagnosis"}
        resolver = self.readonly_step("astra_resolve", resolved, snapshot)
        self.assertNotEqual(resolver["launch_route"]["model"], self.state["settings"]["roles"]["sol"]["model"])
        kind = self.factory.assign_task(lifecycle.assign_task, self.state, resolved, snapshot, resolver)
        self.assertEqual(kind, "implement")
        autopilot.finish_resolution(self.state, resolved, resolver)
        self.assertNotIn("resolution_request", self.state)
        self.assertEqual(self.state["repair_plan"]["tasks"][0]["id"], self.state["current_task"]["id"])
        self.assertNotEqual(original_task["id"], self.state["current_task"]["id"])
        self.assertEqual(self.state["task_archive"], [original_task])
        self.assertEqual(self.state["current_task"]["decision"], "REWORK")
        handles = self.factory.evidence_publications()
        with self.sink.publication(publications=handles):
            self.sink.write(self.run / "state.json", self.state)
        self.sink.publish(self.state, publications=handles)
        self.owner.require_task_authority(self.state, snapshot=snapshot)
        after = self.repair_write(snapshot)
        self.assertNotEqual(after, snapshot)
        self.assertEqual((self.root / "ignored.txt").read_text(), "bounded actual correction\n")
        self.assertEqual(original_contract, self.state["goal_contract"])
        self.assertEqual(original_settings, self.state["settings"])
        self.assertEqual(["ignored.txt", "future.txt"], self.state["current_task"]["affected_paths"])
        self.assertEqual((self.root / "tracked.txt").read_text(), "original\n")
        self.owner.readonly_task_authority(self.state, snapshot=after)
        # Old actual rejection remains historical evidence; it cannot authorize
        # a new task's review or become a current all-case passing integration.
        self.factory.history.verify_rejection(selector)
        with self.assertRaises(util.Paused):
            self.factory.current_decision_review(self.state, snapshot=after)
        with self.assertRaises(util.Paused):
            self.factory.require_final_integration(self.state, snapshot=after, review_selector=selector)
        self.assertFalse(self.state.get("visual_acceptance_receipts"))
        self.assertNotEqual(self.state.get("status"), "TASK_COMPLETE")


class OriginalInitialValidateRepairTransport(OriginalBoundedReworkTransport):
    def commit(self):
        self.initial_validate = True
        return super().commit()


if __name__ == "__main__":
    unittest.main()
