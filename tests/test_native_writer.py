"""Offline first-writer authority controls; no target or native qualification.

Planning publication is a controlled original journal fixture (as in the
source-transition controls). Approval preparation, journal chronology, selected
Git snapshots and the post-normalized atomic parent sink are real. No provider,
held target, browser or retained run is opened.
"""

import json
import subprocess
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import autocode_contract_identity as contracts
import autocode_native_parent as parent
import autocode_source_scope as source_scope
import autocode_util as util
import autocode_visual_checkpoint as checkpoint
import autocode_visual_factory as factories
import autocode_visual_protection as protection


class FirstWriterAuthorityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="native-first-writer-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / ".gitignore").write_text(".autocode/\nignored.txt\nfuture.txt\nunrelated.txt\n")
        (self.root / "tracked.txt").write_text("original\n")
        for args in (
            ["init", "-q"],
            ["add", "."],
            ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "fixture"],
        ):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)
        (self.root / "ignored.txt").write_text("selected ignored source\n")
        self.run = self.root / ".autocode" / "runs" / "owned"
        self.run.mkdir(parents=True)
        self.run_handle = (self.run / "writer.lock").open("a+")
        self.checkout_handle = (self.root / ".autocode" / "writer.lock").open("a+")
        self.addCleanup(self.run_handle.close)
        self.addCleanup(self.checkout_handle.close)
        self.raw = util.snapshot(self.root)
        self.factory = factories.NativeVisualFactory(
            self.root,
            self.run,
            run_handle=self.run_handle,
            checkout_handle=self.checkout_handle,
            start_state={"goal_contract": None, "settings": {}, "stages": []},
            snapshot=self.raw,
        )
        self.owner = self.factory.protection
        self.origin = next(self.owner.root.iterdir())
        self.origin_bytes = self.origin.read_bytes()
        paths = ["ignored.txt", "future.txt"]
        initial = {
            "kind": "implement",
            "milestone_id": "M1",
            "objective": "Implement approved source",
            "requirements": ["Preserve scope"],
            "acceptance_criteria": ["C1"],
            "affected_paths": paths,
            "validation_plan": ["controlled check"],
            "findings": [],
        }
        body = {
            "open_blocking_questions": [],
            "acceptance_criteria": [{"id": "C1"}],
            "constraints": ["VISUAL_SLICE_GATES=complete-cases-v1", 'VISUAL_CASE_CRITERIA={"only":["C1"]}'],
            "milestones": [{"id": "M1", "acceptance_criteria": ["C1"], "depends_on": [], "affected_paths": paths}],
            "initial_task": initial,
        }
        goal = {"task_id": "fixture", "revision": 1, "body": body}
        goal["hash"] = util.digest(goal)
        goal.update(origin="user_cli_edit", approval_status="draft", approval_event=None)
        self.token = contracts.token(goal)
        self.state = {
            "workspace": str(self.root),
            "goal_contract": goal,
            "settings": {
                "joint_planning": True,
                "milestone_checkpoints": {"enabled": True},
                "design_manifest": {"body": {"version": 1, "cases": [{"id": "only", "implementation_paths": paths}]}},
            },
            "current_task": None,
            "status": "AWAITING_GOAL_APPROVAL",
            "displayed_goal": self.token,
            "planning": {"final_token": self.token},
            "user_events": [],
            "findings_ledger": [],
        }
        self.anchor = self.run / "controlled-planning.txt"
        self.anchor.write_text("Controlled planning fixture; no native model receipt.\n")
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

    def prepare(self):
        handle = self.factory.prepare_initial_approval(self.state, self.token, snapshot=self.raw)
        candidate = deepcopy(self.state)
        event = {"kind": "goal_approval", "actor": "user_cli", "token": self.token, "at": util.now()}
        candidate["user_events"].append(event)
        candidate["goal_contract"].update(approval_status="approved", approval_event=event)
        current = source_scope.snapshot(self.root, candidate)
        self.factory.initial_assignment_proof(handle, candidate, snapshot=current)
        decision = protection._json(handle.body)["decision"]
        candidate["current_task"] = {
            **deepcopy(decision["next_task"]),
            "id": "task-0123456789ab",
            "assigned_at": util.now(),
            "objective": decision["next_objective"],
            "affected_paths": decision["affected_paths"],
            "source_revision": current["revision"],
            "contract_hash": candidate["goal_contract"]["hash"],
            "contract_revision": 1,
            "decision": "CONTINUE",
        }
        return handle, candidate, current

    def acknowledge(self, handle, candidate, current):
        def normalized_write(path, value, **hooks):
            normalized = {**deepcopy(value), "controlled_normalization": "actual final sink bytes"}
            return util.atomic_json(path, normalized, **hooks)

        sink = parent.ParentSink(
            self.run,
            factory=self.factory,
            ordinary_write=normalized_write,
            snapshot=lambda value: source_scope.snapshot(self.root, value),
        )
        with sink.publication(approval=handle):
            sink.write(self.run / "state.json", candidate)
            candidate.clear()
            candidate.update(deepcopy(sink.durable_candidate))
        return sink

    def commit(self):
        handle, candidate, current = self.prepare()
        sink = self.acknowledge(handle, candidate, current)
        sink.publish(candidate, approval=handle)
        return candidate, current

    def proof(self, state, current):
        return self.factory.first_writer_proof(state, snapshot=current)

    def test_original_approval_and_actual_normalized_sink_yield_unaccepted_launch_scope(self):
        state, current = self.commit()
        proof = self.proof(state, current)
        checkpoint.require_launch(state, current, proof)
        self.assertIs(type(proof), checkpoint.VerifiedCheckpoint)
        self.assertEqual(proof.authorized_launch, checkpoint.launch_identity(state["current_task"]))
        self.assertFalse(proof.current_scope_accepted)
        self.assertEqual(proof.historical_milestone_ids, ())
        self.assertEqual(proof.eligible_milestone_ids, ())
        self.assertIsNone(proof.receipt_sha256)
        self.assertIsNone(proof.authorized_acceptance)
        self.assertIsNone(proof.authorized_assignment)
        self.assertEqual(self.origin_bytes, self.origin.read_bytes())
        self.assertIn("ignored.txt", current["files"])
        self.assertNotIn("ignored.txt", self.raw["files"])
        ack = next(row for row in self.owner._rows() if row["kind"] == "parent-commit-acknowledged")
        expected = (json.dumps(state, indent=2, sort_keys=True) + "\n").encode()
        import hashlib

        self.assertEqual(ack["body"]["candidate_sha256"], hashlib.sha256(expected).hexdigest())

    def test_concrete_owner_authenticates_original_binding_and_never_dispatch(self):
        state, current = self.commit()
        writer = self.factory.first_writer_owner(state, snapshot=current)
        binding = writer.binding()
        query = {"binding": binding, "workspace": str(self.root), "run_dir": str(self.run)}
        selector = {key: binding[key] for key in ("contract", "runtime_sha256", "settings")}
        proof = writer.authenticate("approval-runtime", query, selector)
        self.assertEqual(proof["query_sha256"], util.digest(query))
        self.assertTrue(proof["protected_refs"])
        self.assertEqual(proof["identity"]["task_id"], state["current_task"]["id"])
        for path, digest in proof["evidence_hashes"].items():
            self.assertEqual(util.file_hash(path), digest)
        for kind in ("dispatch", "builder-delta", "containment", "prerequisite", "retry", "unknown"):
            with self.subTest(kind=kind), self.assertRaises(util.Paused):
                writer.authenticate(kind, query, selector)
        self.assertFalse(writer.admissions.root.exists())

    def test_prepared_approval_and_ack_alone_are_not_first_writer_authority(self):
        handle, candidate, current = self.prepare()
        with self.assertRaises(util.Paused):
            self.proof(candidate, current)
        self.acknowledge(handle, candidate, current)
        with self.assertRaises(util.Paused):
            self.proof(candidate, current)

    def test_failed_actual_sink_and_intervening_write_cannot_grant_authority(self):
        handle, candidate, current = self.prepare()
        with patch.object(util.os, "replace", side_effect=OSError("controlled before replace")):
            with self.assertRaises(OSError):
                self.acknowledge(handle, candidate, current)
        with self.assertRaises(util.Paused):
            self.proof(candidate, current)
        sink = self.acknowledge(handle, candidate, current)
        sink.write(self.run / "state.json", {**candidate, "intervention": "later"})
        with self.assertRaises(util.JSONCommitUncertain):
            sink.publish(candidate, approval=handle)
        with self.assertRaises(util.Paused):
            self.proof(candidate, current)

    def test_ignored_selected_create_edit_delete_hold_unrelated_ignored_does_not(self):
        state, current = self.commit()
        (self.root / "unrelated.txt").write_text("excluded\n")
        checkpoint.require_launch(state, current, self.proof(state, current))
        for name, content in [("future.txt", "created"), ("ignored.txt", "edited"), ("ignored.txt", None)]:
            path = self.root / name
            original = path.read_bytes() if path.exists() else None
            try:
                if content is None:
                    path.unlink()
                else:
                    path.write_text(content)
                with self.subTest(name=name, content=content), self.assertRaises(util.Paused):
                    self.proof(state, source_scope.snapshot(self.root, state))
            finally:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    path.write_bytes(original)

    def test_forged_binding_foreign_query_and_conflicting_criteria_are_denied(self):
        state, current = self.commit()
        writer = self.factory.first_writer_owner(state, snapshot=current)
        binding = writer.binding()
        query = {"binding": binding, "workspace": str(self.root), "run_dir": str(self.run)}
        selector = {key: binding[key] for key in ("contract", "runtime_sha256", "settings")}
        for mutation in ("run", "source", "selector", "extra"):
            changed, chosen = deepcopy(query), deepcopy(selector)
            if mutation == "run":
                changed["run_dir"] += "-foreign"
            elif mutation == "source":
                changed["binding"]["source"]["source_paths"] = ["ignored.txt"]
            elif mutation == "selector":
                chosen["runtime_sha256"] = "f" * 64
            else:
                changed["cached_accepted"] = True
            with self.subTest(mutation=mutation), self.assertRaises(util.Paused):
                writer.authenticate("approval-runtime", changed, chosen)
        state["current_task"]["criterion_ids"] = ["FOREIGN"]
        with self.assertRaises(util.Paused):
            writer.binding()

    def test_changed_original_planning_bytes_and_runtime_are_not_rebound(self):
        state, current = self.commit()
        writer = self.factory.first_writer_owner(state, snapshot=current)
        original = self.anchor.read_bytes()
        self.anchor.write_text("changed retained planning")
        with self.assertRaises(util.Paused):
            writer.binding()
        self.anchor.write_bytes(original)
        with patch.object(protection.history, "runtime_files", return_value={"foreign.py": "a" * 64}):
            with self.assertRaises(util.Paused):
                writer.binding()

    def test_changed_event_settings_task_or_assignment_snapshot_refuse(self):
        state, current = self.commit()
        for change in ("event", "settings", "task", "source", "owner", "kind"):
            broken = deepcopy(state)
            if change == "event":
                broken["user_events"] = []
            elif change == "settings":
                broken["settings"]["limit"] = 123
            elif change == "task":
                broken["current_task"]["objective"] = "other"
            elif change == "source":
                broken["current_task"]["source_revision"] = self.raw["revision"]
            elif change == "owner":
                broken["current_task"]["milestone_id"] = "M2"
            else:
                broken["current_task"]["kind"] = "validate"
            with self.subTest(change=change), self.assertRaises(util.Paused):
                self.proof(broken, current)

    def test_original_validate_assignment_remains_held(self):
        # Create the original reviewed fixture as validate, before approval.
        goal = self.state["goal_contract"]
        goal["body"]["initial_task"]["kind"] = "validate"
        goal["hash"] = util.digest({key: goal[key] for key in ("task_id", "revision", "body")})
        self.token = contracts.token(goal)
        self.state["displayed_goal"] = self.state["planning"]["final_token"] = self.token
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
        state, current = self.commit()
        with self.assertRaises(util.Paused):
            self.proof(state, current)

    def test_original_owner_reopen_replays_proof_without_mutating_history(self):
        state, current = self.commit()
        pins = {p.name: p.read_bytes() for p in self.owner.root.iterdir()}
        reopened = factories.NativeVisualFactory(
            self.root,
            self.run,
            run_handle=self.run_handle,
            checkout_handle=self.checkout_handle,
            start_state=state,
            snapshot=current,
        )
        checkpoint.require_launch(state, current, reopened.first_writer_proof(state, snapshot=current))
        self.assertEqual(pins, {p.name: p.read_bytes() for p in self.owner.root.iterdir()})

    def test_journal_tampering_and_parent_failure_outcome_hold(self):
        state, current = self.commit()
        writer = self.factory.first_writer_owner(state, snapshot=current)
        outcomes = self.run / "native-parent-outcomes"
        outcomes.mkdir()
        (outcomes / "retained-failure.json").write_text('{"native_authority":"HELD"}')
        with self.assertRaises(util.Paused):
            writer.binding()
        (outcomes / "retained-failure.json").unlink()
        original = self.origin.read_bytes()
        self.origin.chmod(0o600)
        self.origin.write_bytes(original + b" ")
        with self.assertRaises(util.Paused):
            writer.binding()

    def test_wrong_displayed_token_contract_and_head_refuse(self):
        for change in ("displayed", "contract"):
            broken = deepcopy(self.state)
            if change == "displayed":
                broken["displayed_goal"] = "r999:foreign"
            else:
                broken["goal_contract"]["body"]["initial_task"]["objective"] = "changed"
            with self.subTest(change=change), self.assertRaises(util.Paused):
                self.factory.prepare_initial_approval(broken, self.token, snapshot=self.raw)
        state, current = self.commit()
        subprocess.run(
            [
                "git",
                "-C",
                str(self.root),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.test",
                "commit",
                "--allow-empty",
                "-qm",
                "changed HEAD",
            ],
            check=True,
            capture_output=True,
        )
        with self.assertRaises(util.Paused):
            self.proof(state, source_scope.snapshot(self.root, state))

    def test_only_original_first_writer_callers_reach_the_unaccepted_launch_scope(self):
        from types import SimpleNamespace

        import autocode_native_calls as calls
        import autocode_native_writer as writer_module

        state, current = self.commit()
        state["task"] = "original operator input"
        native = calls.PlanningCalls(
            SimpleNamespace(factory=self.factory),
            {
                "version": 1,
                "mode": "isolated-builtin-openai",
                "opencode_version": "1.18.33",
                "model": "openai/gpt-5.6",
                "reasoning_effort": "high",
                "max_requests": 1,
                "image_limits": {"width": 480, "height": 640, "pixels": 480 * 640, "bytes": 1024 * 1024},
                "sdk_path": ".autocode/sdk",
                "sdk_manifest_sha256": "a" * 64,
                "transport_identity_sha256": "b" * 64,
                "executable_sha256": "c" * 64,
            },
            initial_task=state["task"],
            run_dir=self.run,
            workspace=self.root,
            write=util.atomic_json,
            snapshot=lambda value: source_scope.snapshot(self.root, value),
            planning_stages=("requirements_gather",),
            recognizer="recognize_workflow",
        )
        native.guard(state, "terra", allow_write=True)
        state["next_stage"] = "terra"
        native.require_supported_action(SimpleNamespace(run_dir=self.run), state)
        state["next_stage"] = "astra_resolve"
        with self.assertRaises(util.Paused):
            native.require_supported_action(SimpleNamespace(run_dir=self.run), state)
        with self.assertRaises(util.Paused):
            native.guard(state, "terra", allow_write=False)
        with self.assertRaises(util.Paused):
            writer_module.NativeWriterAuthority(
                SimpleNamespace(first_writer_authority=lambda **_: {}), state, snapshot=current
            )

    def test_binding_result_cannot_mutate_original_authority(self):
        state, current = self.commit()
        writer = self.factory.first_writer_owner(state, snapshot=current)
        result = writer.binding()
        result["task"]["objective"] = "forged detached result"
        result["source"]["files"].clear()
        self.assertEqual(writer.binding()["task"], state["current_task"])
        self.assertEqual(writer.binding()["source"], current)

    def test_missing_original_ack_row_refuses_without_adopting_current_state(self):
        state, current = self.commit()
        row = next(row for row in self.owner._rows() if row["kind"] == "parent-commit-acknowledged")
        path = self.owner.root / (f"{row['sequence']:08d}-" + row["id"] + ".json")
        retained = self.run / "temporarily-unavailable-ack.json"
        path.rename(retained)
        try:
            with self.assertRaises(util.Paused):
                self.proof(state, current)
        finally:
            retained.rename(path)
        checkpoint.require_launch(state, current, self.proof(state, current))

    def test_active_worker_and_lost_original_lock_hold(self):
        state, current = self.commit()
        state["active_stage"] = {"stage": "terra"}
        with self.assertRaises(util.Paused):
            self.proof(state, current)
        state.pop("active_stage")
        self.checkout_handle.close()
        with self.assertRaises(util.Paused):
            self.proof(state, current)


if __name__ == "__main__":
    unittest.main()
