"""Bounded caller/durable-parent controls, never native image qualification."""

import hashlib
import json
import os
import stat
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
import autocode_checkout_lock as checkout_lock
import autocode_contract_identity as contracts
import autocode_goal_lifecycle as lifecycle
import autocode_native_calls as callers
import autocode_native_invocation as invocation
import autocode_native_launch as native_launch
import autocode_native_parent as parent
import autocode_native_role_isolation as isolation
import autocode_planning_artifacts as artifacts
import autocode_provider_launch as launch_policy
import autocode_supervision as supervision
import autocode_task_feasibility as feasibility
import autocode_util as util
import autocode_visual_checkpoint as checkpoint
import autocode_visual_factory as factories
import autocode_visual_profile as profiles
import autocode_visual_protection as protection
from goal_fixtures import body as goal_body


class PreparedSourceAdmissionTests(unittest.TestCase):
    """Real preparation/source guard; controlled transport is NOT native proof."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="native-prepared-source-unit-")
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        (self.root / ".gitignore").write_text(".autocode/\n")
        (self.root / "ui.py").write_text("original\n")
        for args in (
            ["init", "-q"],
            ["add", "."],
            ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "controlled"],
        ):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)
        self.run = self.root / ".autocode/runs/controlled"
        self.run.mkdir(parents=True)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        run_handle = self.stack.enter_context(util.run_lock(self.run))
        checkout_handle = self.stack.enter_context(checkout_lock.exclusive(self.root, self.run))
        self.state = {
            "workspace": str(self.root),
            "goal_contract": None,
            "stages": [],
            "settings": {
                "roles": {
                    "glm": {
                        "engine": "opencode",
                        "provider": "opencode",
                        "model": "controlled/model",
                        "reasoning_effort": "high",
                    }
                }
            },
        }
        self.prepare_input_context()
        self.raw = util.snapshot(self.root)
        self.context = native_launch.NativeRunContext(
            self.root,
            self.run,
            run_handle=run_handle,
            checkout_handle=checkout_handle,
            start_state=self.state,
            snapshot=self.raw,
        )
        self.owner = self.context.factory.protection
        binary = str(Path(sys.executable).resolve())
        self.command = [binary, "-I", "-S", "-B", "-c", "pass"]
        self.env = {"PATH": "/usr/bin:/bin"}
        before = self.run / "plan.before.json"
        util.atomic_json(before, self.raw)
        record = {
            "stage": "plan",
            "role": "glm",
            "engine": "opencode",
            "command": self.command,
            "launch_route": dict(self.state["settings"]["roles"]["glm"]),
            "events": str(self.run / "plan.jsonl"),
            "output": str(self.run / "plan.json"),
            "before_ref": str(before),
            **getattr(self, "record_overrides", {}),
        }
        manifest = self.run / "controlled-isolation.json"
        manifest.write_text("{}\n")
        descriptor = {
            "version": 1,
            "files": {},
            "manifest": str(manifest),
            "manifest_sha256": util.file_hash(manifest),
            "child_identity": {"executable": binary, "executable_sha256": util.file_hash(binary)},
        }
        # Only native transport conformance is controlled. Freshness, real
        # preparation, ownership, journal and original pins remain unmocked.
        with patch.object(
            self.owner, "_native_config", return_value=({str(manifest): util.file_hash(manifest)}, descriptor)
        ):
            self.prepared = self.context.factory.prepare_launch(
                self.state,
                self.raw,
                record,
                boundary={"allow_write": False, "write_paths": []},
                command=self.command,
                environment=self.env,
                native_role_isolation=None,
            )

    def prepare_input_context(self):
        pass

    def launch(self, handle=None):
        return self.context.held_launch(
            handle or self.prepared,
            command=self.command,
            environment=self.env,
            options={
                "cwd": self.root,
                "env": self.env,
                "start_new_session": True,
                "stdin": subprocess.DEVNULL,
                "stdout": subprocess.DEVNULL,
                "stderr": subprocess.DEVNULL,
            },
            receipt_path=self.run / "supervision.json",
            timeout=5,
        )

    def test_genuine_prepared_source_reaches_launch_admission(self):
        # Tripwire starts no Popen, browser, provider or model.
        with patch.object(supervision, "launch", side_effect=RuntimeError("launch admission tripwire")) as launch:
            with self.assertRaisesRegex(RuntimeError, "launch admission tripwire"):
                with self.launch():
                    self.fail("tripwire must stop before process creation")
        launch.assert_called_once()

    def test_unreleased_timeout_cannot_be_reconciled_as_a_stopped_original(self):
        record = json.loads(self.prepared.body)["record_identity"]
        with self.assertRaisesRegex(util.Paused, "current native handle|released readonly planning root"):
            self.owner.planning_abandonment(self.state, record, snapshot=self.raw, commit=True)
        self.assertFalse(Path(record["after_ref"]).exists())

    def assert_refuses_before_child(self, handle=None):
        with patch.object(supervision, "launch") as launch:
            with self.assertRaises(util.Paused):
                with self.launch(handle):
                    self.fail("Invalid original preparation must refuse")
        launch.assert_not_called()

    def test_foreign_reconstructed_pending_handle_refuses(self):
        foreign = protection.PreparedLaunch(self.owner, self.prepared.nonce, self.prepared.row_id, self.prepared.body)
        self.assert_refuses_before_child(foreign)

    def test_removed_pending_handle_cannot_replay_journal_row(self):
        del self.owner._pending[self.prepared.nonce]
        self.assert_refuses_before_child()

    def test_changed_source_refuses_before_child(self):
        (self.root / "ui.py").write_text("changed after original prepare\n")
        self.assert_refuses_before_child()

    def test_changed_original_isolation_evidence_pin_refuses_before_child(self):
        (self.run / "controlled-isolation.json").write_text('{"changed":true}\n')
        self.assert_refuses_before_child()

    def test_second_unfinished_historical_row_is_not_adopted(self):
        self.owner._append("prepared", protection._json(self.prepared.body))
        self.assert_refuses_before_child()

    def test_generic_idle_and_new_preparation_remain_strict(self):
        with self.assertRaisesRegex(util.Paused, "actual launch/check crash gap remains; no automatic backfill"):
            self.owner._source_continuity(self.raw)


class PlanningInputPinAdmissionTests(unittest.TestCase):
    """The real constructor derives input pins outside controlled transport."""

    setUp = PreparedSourceAdmissionTests.setUp
    launch = PreparedSourceAdmissionTests.launch
    assert_refuses_before_child = PreparedSourceAdmissionTests.assert_refuses_before_child

    def prepare_input_context(self):
        import autocode_design_manifest as manifest

        from tests.test_design_manifest import PlanningPartitionContextTests

        path, body = PlanningPartitionContextTests.scoped_bundle(self.root / "exports")
        approval = self.root / "approved-scope.json"
        approval.write_text(
            json.dumps(
                {
                    "version": 1,
                    "manifest_hash": util.digest(body),
                    "approved_case_ids": [row["id"] for row in body["cases"]],
                    "approval_evidence": [{"kind": "approved_scope_decision", "sha256": "a" * 64}],
                }
            )
        )
        args = {"approved_scope": approval, "approved_scope_sha256": util.file_hash(approval)}
        self.inputs = manifest.retain(manifest.load(path, **args), self.root, **args)
        self.state["settings"]["design_manifest"] = self.inputs

    def test_owner_computed_inputs_are_sealed_without_transport_supplied_pins(self):
        import autocode_design_manifest as manifest

        pins = manifest.planning_input_pins(self.state["settings"], role="glm")
        self.assertEqual(2, len(pins))
        prepared = json.loads(self.prepared.body)
        self.assertEqual(pins, {path: prepared["pins"][path] for path in pins})
        self.assertEqual(self.inputs, prepared["binding"]["settings"]["design_manifest"])
        with patch.object(supervision, "launch", side_effect=RuntimeError("input admission tripwire")) as launch:
            with self.assertRaisesRegex(RuntimeError, "input admission tripwire"):
                with self.launch():
                    self.fail("tripwire must stop before child creation")
        launch.assert_called_once()

    def test_prepared_catalog_replacement_refuses_before_child(self):
        path = Path(self.inputs["root"]) / "planning-catalog.json"
        original = path.read_bytes()
        path.unlink()
        path.write_bytes(b"0" * len(original))
        path.chmod(0o400)
        self.assert_refuses_before_child()

    def test_same_bytes_writable_catalog_refuses_before_child_and_retained_replay(self):
        path = Path(self.inputs["root"]) / "planning-catalog.json"
        original = path.read_bytes()
        path.chmod(0o600)
        self.assertEqual(original, path.read_bytes())
        self.assert_refuses_before_child()
        with self.assertRaises((ValueError, util.Paused)):
            self.owner._chain_cold()


class ParentSinkFailureTests(unittest.TestCase):
    """Controlled factory callbacks with the real JSON sink; no native credit."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="native-parent-sink-unit-")
        self.addCleanup(temp.cleanup)
        self.run = Path(temp.name).resolve()
        self.factory = object.__new__(factories.NativeVisualFactory)
        self.order = []
        self.factory.invalidate_parent_ack = Mock(side_effect=lambda: self.order.append("invalidate"))
        self.factory.prepare_parent_commit = Mock(return_value="controlled intent")
        self.factory.capture_parent_commit = Mock(return_value="controlled ACK")
        self.factory.commit_planning_publication = Mock()
        self.factory.evidence_publications = Mock(return_value=())

        def ordinary(path, value, **hooks):
            self.order.append("normalize")
            value["normalized"] = "actual sink"
            util.atomic_json(path, value, **hooks)

        self.sink = parent.ParentSink(
            self.run,
            factory=self.factory,
            ordinary_write=ordinary,
            snapshot=lambda value: {"controlled": value["version"]},
        )
        self.sink.ack = "prior ACK"
        self.path = self.run / "state.json"
        self.value = {"version": 1}

    def test_invalidate_precedes_normalization_and_actual_replace(self):
        with self.sink.publication(planning_record={"original": "record"}):
            self.sink.write(self.path, self.value)
        self.assertEqual(["invalidate", "normalize"], self.order)
        self.assertEqual(self.value, json.loads(self.path.read_text()))
        self.assertEqual("actual sink", self.sink.candidate["normalized"])
        self.factory.commit_planning_publication.assert_not_called()
        self.sink.publish(self.value, planning_record={"original": "record"})
        self.factory.commit_planning_publication.assert_called_once()

    def test_temp_fsync_failure_retires_ack_and_publishes_nothing(self):
        with patch.object(util.os, "fsync", side_effect=OSError("temporary fsync refused")):
            with self.assertRaisesRegex(OSError, "temporary fsync"):
                self.sink.write(self.path, self.value)
        self.assertIsNone(self.sink.ack)
        self.assertFalse(self.path.exists())
        self.factory.capture_parent_commit.assert_not_called()

    def test_replace_failure_is_uncertain_and_holds_future_authority(self):
        with patch.object(util.os, "replace", side_effect=OSError("replace refused")):
            with self.assertRaises(util.JSONCommitUncertain) as caught:
                self.sink.write(self.path, self.value)
        self.assertFalse(caught.exception.replaced)
        self.assertFalse(caught.exception.durable)
        self.assertTrue(self.sink.blocked)
        self.assertIsNone(self.sink.ack)
        with self.assertRaises(util.Paused):
            self.sink.require_ready()

    def test_directory_fsync_failure_retains_exact_replaced_candidate(self):
        real = util.os.fsync
        count = 0

        def fsync(fd):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("directory fsync refused")
            return real(fd)

        with patch.object(util.os, "fsync", side_effect=fsync):
            with self.assertRaises(util.JSONCommitUncertain) as caught:
                self.sink.write(self.path, self.value)
        self.assertTrue(caught.exception.replaced)
        self.assertFalse(caught.exception.durable)
        self.assertEqual(self.value, caught.exception.candidate)
        self.assertEqual(self.value, json.loads(self.path.read_text()))
        self.assertIsNone(self.sink.ack)

    def test_after_commit_callback_failure_never_leaves_prior_ack(self):
        self.factory.capture_parent_commit.side_effect = OSError("controlled ACK failure")
        with self.sink.publication(planning_record={"original": "record"}):
            self.sink.write(self.path, self.value)
        self.assertEqual(self.value, json.loads(self.path.read_text()))
        self.assertIsNone(self.sink.ack)
        with self.assertRaises(util.JSONCommitUncertain) as caught:
            self.sink.publish(self.value, planning_record={"original": "record"})
        self.assertEqual(self.value, caught.exception.candidate)
        self.assertTrue(caught.exception.durable)
        self.factory.commit_planning_publication.assert_not_called()
        self.assertTrue(self.sink.blocked)

    def test_preparation_failure_keeps_ordinary_commit_but_holds_authority(self):
        self.factory.prepare_parent_commit.side_effect = util.Paused(
            "PAUSED_VISUAL_EVIDENCE", "controlled preparation failure"
        )
        with self.sink.publication(planning_record={"original": "record"}):
            self.sink.write(self.path, self.value)
        self.assertEqual(self.value, json.loads(self.path.read_text()))
        self.factory.capture_parent_commit.assert_not_called()
        with self.assertRaises(util.JSONCommitUncertain) as caught:
            self.sink.publish(self.value, planning_record={"original": "record"})
        self.assertEqual(self.value, caught.exception.candidate)
        self.assertTrue(caught.exception.durable)
        self.assertTrue(self.sink.blocked)
        phases = {json.loads(path.read_text())["phase"] for path in (self.run / "native-parent-outcomes").iterdir()}
        self.assertIn("native_preparation", phases)

    def test_publication_failure_consumes_local_ack_and_holds_restart(self):
        with self.sink.publication(planning_record={"original": "record"}):
            self.sink.write(self.path, self.value)
        self.factory.commit_planning_publication.side_effect = OSError("controlled publication failure")
        with self.assertRaisesRegex(OSError, "publication failure"):
            self.sink.publish(self.value, planning_record={"original": "record"})
        self.assertIsNone(self.sink.ack)
        self.assertTrue(self.sink.blocked)
        self.assertEqual(self.value, json.loads(self.path.read_text()))
        reopened = parent.ParentSink(
            self.run, factory=self.factory, ordinary_write=self.sink.ordinary_write, snapshot=self.sink.snapshot
        )
        with self.assertRaises(util.Paused):
            reopened.require_ready()

    def test_post_persist_failure_marks_durable_and_retains_candidate(self):
        base = self.sink.ordinary_write

        def fail_after(*args, **kwargs):
            base(*args, **kwargs)
            raise OSError("status publication failed after durable sink")

        self.sink.ordinary_write = fail_after
        with self.assertRaises(util.JSONCommitUncertain) as caught:
            self.sink.write(self.path, self.value)
        self.assertTrue(caught.exception.replaced)
        self.assertTrue(caught.exception.durable)
        self.assertEqual(self.value, json.loads(self.path.read_text()))
        self.assertTrue(self.sink.blocked)

    def test_changed_candidate_or_later_write_cannot_publish_ack(self):
        with self.sink.publication(planning_record={"original": "record"}):
            self.sink.write(self.path, self.value)
        changed = {**self.value, "later": True}
        with self.assertRaises(util.JSONCommitUncertain) as caught:
            self.sink.publish(changed, planning_record={"original": "record"})
        self.assertEqual(self.value, caught.exception.candidate)
        self.assertNotIn("later", caught.exception.candidate)
        self.sink.write(self.path, changed)
        with self.assertRaises(util.JSONCommitUncertain) as caught:
            self.sink.publish(changed, planning_record={"original": "record"})
        self.assertEqual(changed, caught.exception.candidate)
        self.factory.commit_planning_publication.assert_not_called()

    def test_pre_save_error_cannot_borrow_previous_durable_candidate(self):
        self.sink.write(self.path, self.value)
        previous = self.path.read_bytes()
        with self.assertRaisesRegex(ValueError, "before the new save") as caught:
            with self.sink.publication(planning_record={"original": "record"}):
                raise ValueError("controlled failure before the new save")
        self.assertNotIsInstance(caught.exception, util.JSONCommitUncertain)
        self.assertEqual(previous, self.path.read_bytes())
        self.assertIsNone(self.sink.durable_candidate)
        self.factory.commit_planning_publication.assert_not_called()

    def test_retained_parent_outcome_blocks_restart_authority_before_setup(self):
        directory = self.run / "native-parent-outcomes"
        directory.mkdir()
        (directory / "controlled-error.json").write_text('{"native_authority":"HELD"}\n')
        reopened = parent.ParentSink(
            self.run, factory=self.factory, ordinary_write=self.sink.ordinary_write, snapshot=self.sink.snapshot
        )
        with self.assertRaises(util.Paused):
            reopened.require_ready()
        self.assertEqual([], self.order)

    def test_actual_user_boundary_retains_replaced_candidate_and_handoffs(self):
        raw = b"original final plan bytes\n"
        identity = {"path": "planning/final.json", "sha256": hashlib.sha256(raw).hexdigest()}
        original = {"version": 1, "original": "old memory"}
        candidate = {"version": 2, "pending_planning_outputs": [{"identity": identity, "bytes": raw}]}
        native = SimpleNamespace(sink=self.sink, approval=object())
        real = util.os.fsync
        root = self.run.stat()

        def fsync(fd):
            actual = os.fstat(fd)
            if stat.S_ISDIR(actual.st_mode) and (actual.st_dev, actual.st_ino) == (root.st_dev, root.st_ino):
                raise OSError("actual parent directory fsync refused")
            return real(fd)

        with patch.object(util.os, "fsync", side_effect=fsync):
            with self.assertRaises(util.JSONCommitUncertain):
                runner.commit_user_action(original, candidate, self.run, persist=self.sink.write, native=native)
        self.assertEqual(original, json.loads(self.path.read_text()))
        self.assertEqual(2, original["version"])
        self.assertNotIn("original", original)
        self.assertEqual(raw, (self.run / identity["path"]).read_bytes())
        self.assertTrue(self.sink.blocked)


class PlanningArtifactFailureTests(unittest.TestCase):
    def test_uncertain_parent_keeps_written_original_handoffs(self):
        with tempfile.TemporaryDirectory(prefix="native-artifact-uncertain-") as tmp:
            run = Path(tmp).resolve()
            raw = b"original handoff\n"
            identity = {"path": "planning/final.json", "sha256": hashlib.sha256(raw).hexdigest()}
            candidate = {"pending_planning_outputs": [{"identity": identity, "bytes": raw}]}

            def fail(value):
                raise util.JSONCommitUncertain(
                    OSError("directory fsync"), candidate=value, replaced=True, durable=False
                )

            with self.assertRaises(util.JSONCommitUncertain):
                artifacts.commit_pending(candidate, run, fail)
            self.assertEqual(raw, (run / identity["path"]).read_bytes())

    def test_pre_replace_ordinary_failure_rolls_back_only_new_handoff(self):
        with tempfile.TemporaryDirectory(prefix="native-artifact-pre-replace-") as tmp:
            run = Path(tmp).resolve()
            raw = b"new unpublished handoff\n"
            identity = {"path": "planning/final.json", "sha256": hashlib.sha256(raw).hexdigest()}
            candidate = {"pending_planning_outputs": [{"identity": identity, "bytes": raw}]}
            with self.assertRaisesRegex(OSError, "pre-replace"):
                artifacts.commit_pending(candidate, run, Mock(side_effect=OSError("pre-replace")))
            self.assertFalse((run / identity["path"]).exists())


class OuterParentDispositionTests(unittest.TestCase):
    """Actual CLI catch owners; controlled admission supplies no authority."""

    setUp = ParentSinkFailureTests.setUp

    def dependencies(self):
        calls = object.__new__(callers.PlanningCalls)
        calls.sink, calls.run_dir, calls.approval = self.sink, self.run, object()
        calls.guard = Mock()
        calls.require_supported_action = Mock()
        proxy = invocation.runner_view(runner, calls)
        proxy.consume_interventions = Mock(return_value=False)
        proxy.normalize_human_boundary = Mock()
        proxy.finish_human_action = Mock()
        proxy.intervention_metadata = Mock(return_value={"pending_count": 0, "inbox_error": None})
        proxy.run_role = Mock(side_effect=AssertionError("No provider is launched by these controls"))
        self.state = {
            "version": 1,
            "workspace": str(self.run),
            "task": "Controlled original task",
            "status": "AWAITING_GOAL_APPROVAL",
            "phase": "AWAITING_APPROVAL",
            "next_stage": "plan_finalize",
            "stages": [],
            "current_task": None,
            "settings": {"roles": {"glm": {"model": "controlled/model"}}, "limits": {}},
        }
        self.raw = b"original final handoff bytes\n"
        self.identity = {"path": "planning/final.json", "sha256": hashlib.sha256(self.raw).hexdigest()}
        self.saved = None
        ordinary = self.sink.ordinary_write

        def save(path, value, **hooks):
            ordinary(path, value, **hooks)
            if Path(path) == self.path:
                self.saved = deepcopy(value)

        self.sink.ordinary_write = save

        def approve(candidate, selected):
            self.assertEqual("controlled-exact-token", selected)
            candidate.update(status="READY", phase="READY", first_approval="controlled actual action")
            candidate["pending_planning_outputs"] = [{"identity": self.identity, "bytes": self.raw}]

        proxy.approve_goal = approve
        util.atomic_json(self.path, self.state)  # load_locked supplies a durable initial checkpoint.
        return proxy, calls

    def invoke_approval(self, proxy, calls):
        args, parser = runner.cli_args.parse(
            None,
            ["--run-dir", str(self.run), "--approve-goal", "controlled-exact-token"],
            runner.opencode.DEFAULT_MODELS,
        )
        with (
            patch.object(runner.run_setup, "load_locked", return_value=self.state),
            patch.object(
                runner.run_actions.resolver_human,
                "current",
                return_value={"scope": "goal_approval", "request_id": "controlled-issued-request"},
            ),
            patch.object(runner.run_actions.lifecycle, "present", return_value="Controlled approved plan"),
            patch.object(runner.run_actions.action_decisions.autopilot, "publish_handoffs"),
        ):
            return runner.run_setup.run_locked(
                proxy, args, parser, self.state, self.path, self.run, self.run, native=calls, checkout_owned=True
            )

    def assert_retained_approval(self, code):
        self.assertEqual(2, code)
        actual = json.loads(self.path.read_text())
        self.assertEqual("READY", actual["status"])
        self.assertEqual("controlled actual action", actual["first_approval"])
        self.assertNotIn("stop_reason", actual)
        self.assertEqual(actual, self.state)
        self.assertEqual(self.raw, (self.run / self.identity["path"]).read_bytes())
        self.assertTrue(self.sink.blocked)

    def test_outer_approval_publication_failure_keeps_actual_durable_candidate(self):
        proxy, calls = self.dependencies()
        self.factory.commit_initial_approval = Mock(side_effect=OSError("controlled publication failure"))
        self.assert_retained_approval(self.invoke_approval(proxy, calls))

    def test_outer_approval_preparation_failure_keeps_actual_durable_candidate(self):
        proxy, calls = self.dependencies()
        self.factory.prepare_parent_commit.side_effect = ValueError("controlled native preparation failure")
        self.assert_retained_approval(self.invoke_approval(proxy, calls))

    def test_outer_approval_ack_failure_keeps_actual_durable_candidate(self):
        proxy, calls = self.dependencies()
        self.factory.capture_parent_commit.side_effect = OSError("controlled native ACK failure")
        self.assert_retained_approval(self.invoke_approval(proxy, calls))

    def test_outer_stage_publication_failure_does_not_reject_or_overwrite_report(self):
        proxy, calls = self.dependencies()
        calls.approval = None
        self.state["status"] = "RUNNING"
        record = {"stage": "plan_finalize", "output": str(self.run / "original-final.json")}
        Path(record["output"]).write_text('{"controlled":"original report"}\n')
        original_report = Path(record["output"]).read_bytes()
        self.factory.commit_planning_publication.side_effect = util.Paused(
            "PAUSED_VISUAL_EVIDENCE", "controlled publication failure"
        )

        def commit_stage(current, stage, _value, original, workspace, run_dir):
            candidate = deepcopy(current)
            candidate.update(
                status="AWAITING_GOAL_APPROVAL",
                phase="AWAITING_APPROVAL",
                original_final="controlled applied report",
                stages=[deepcopy(original)],
            )
            candidate["pending_planning_outputs"] = [{"identity": self.identity, "bytes": self.raw}]
            runner.commit_boundary_candidate(
                current, candidate, run_dir, workspace, persist=self.sink.write, native=calls, planning_record=original
            )

        proxy.commit_stage_result = commit_stage
        proxy.run_role = Mock(return_value=({}, record))
        proxy.account_stage = Mock()
        proxy.rotate_if_needed = Mock()
        proxy.reject_completed_stage = Mock(wraps=proxy.reject_completed_stage)
        request = SimpleNamespace(
            role="glm",
            route_role="glm",
            prompt="Controlled final",
            metrics={"estimated_prompt_tokens": 0, "soft_budget_tokens": 1},
            schema={},
            allow_write=False,
        )
        args = SimpleNamespace(unit="autocode", chat=False, pause_after_stage=False)

        def drive(current, dispatch, **callbacks):
            dispatch(current, "plan_finalize")

        with ExitStack() as stack:
            stack.enter_context(patch.object(runner.run_setup, "load_locked", return_value=self.state))
            stack.enter_context(patch.object(runner.run_actions, "handle", return_value=None))
            stack.enter_context(patch.object(runner.orchestrator, "drive", side_effect=drive))
            for module, name in (
                (runner.build_loop.progressive, "guard_dispatch"),
                (runner.build_loop.milestones, "dispatch_guard"),
                (runner.build_loop.workflow, "dispatch_guard"),
                (runner.build_loop.autopilot, "admit_validation"),
                (runner.build_loop.regression, "before_review"),
                (runner.build_loop.launch_inputs, "guard"),
            ):
                stack.enter_context(patch.object(module, name))
            stack.enter_context(patch.object(runner.build_loop.planning, "is_planning", return_value=False))
            stack.enter_context(patch.object(runner.build_loop.autopilot, "prepare_request", return_value=request))
            code = runner.run_setup.run_locked(
                proxy, args, None, self.state, self.path, self.run, self.run, native=calls, checkout_owned=True
            )
        self.assertEqual(2, code)
        proxy.reject_completed_stage.assert_not_called()
        actual = json.loads(self.path.read_text())
        self.assertEqual("AWAITING_GOAL_APPROVAL", actual["status"])
        self.assertEqual(actual, self.state)
        self.assertNotIn("stop_reason", actual)
        self.assertNotIn("rejected", actual["stages"][0])
        self.assertEqual(self.raw, (self.run / self.identity["path"]).read_bytes())
        self.assertEqual(original_report, Path(record["output"]).read_bytes())
        self.assertTrue(self.sink.blocked)

    def test_post_save_intervention_error_preserves_candidate_and_new_handoff(self):
        proxy, calls = self.dependencies()
        calls.approval = None
        candidate = {
            **self.state,
            "after_boundary": "actual replacement",
            "pending_planning_outputs": [{"identity": self.identity, "bytes": self.raw}],
        }

        def consumed(current, run_dir, workspace, **options):
            options["persist"](run_dir / "state.json", current)
            raise runner.interventions.InterventionError(
                "PAUSED_INTERVENTION_ACK", "controlled inbox acknowledgement failed after save"
            )

        with patch.object(runner, "consume_interventions", side_effect=consumed):
            with self.assertRaises(util.JSONCommitUncertain) as caught:
                runner.commit_boundary_candidate(
                    self.state,
                    candidate,
                    self.run,
                    self.run,
                    persist=self.sink.write,
                    native=calls,
                    planning_record={"original": "record"},
                )
        self.assertTrue(caught.exception.replaced)
        self.assertTrue(caught.exception.durable)
        self.assertEqual(json.loads(self.path.read_text()), caught.exception.candidate)
        self.assertEqual(caught.exception.candidate, self.state)
        self.assertEqual(self.raw, (self.run / self.identity["path"]).read_bytes())


class NonfinalBoundaryObservationTests(unittest.TestCase):
    """No publication intent: actual new save owns failure disposition."""

    setUp = ParentSinkFailureTests.setUp
    dependencies = OuterParentDispositionTests.dependencies

    def establish_previous_save(self):
        proxy, calls = self.dependencies()
        calls.approval = None
        self.previous_artifact = self.run / "planning/committed.json"
        self.previous_artifact.parent.mkdir()
        self.previous_bytes = b"previously committed handoff bytes\n"
        self.previous_artifact.write_bytes(self.previous_bytes)
        previous_identity = {
            "path": "planning/committed.json",
            "sha256": hashlib.sha256(self.previous_bytes).hexdigest(),
        }
        self.state["committed_artifact"] = previous_identity
        self.state["previous_observation"] = "earlier dispatch/status save"
        self.sink.write(self.path, self.state)
        self.previous = deepcopy(self.state)
        self.candidate = {
            **self.state,
            "new_nonfinal_result": "only this boundary may save me",
            "pending_planning_outputs": [
                {"identity": previous_identity, "bytes": self.previous_bytes},
                {"identity": self.identity, "bytes": self.raw},
            ],
        }
        return proxy, calls

    def fail_before_save(self, state, run_dir, workspace, **options):
        raise runner.interventions.InterventionError("inbox_unreadable", "controlled second inbox read before new save")

    def fail_after_save(self, state, run_dir, workspace, **options):
        options["persist"](run_dir / "state.json", state)
        raise runner.interventions.InterventionError("ack_failed", "controlled inbox acknowledgement after new save")

    def commit(self, calls):
        return runner.commit_boundary_candidate(
            self.state, self.candidate, self.run, self.run, persist=self.sink.write, native=calls, planning_record=None
        )

    def test_nonfinal_pre_save_error_cannot_borrow_previous_durable_candidate(self):
        proxy, calls = self.establish_previous_save()
        with patch.object(runner, "consume_interventions", side_effect=self.fail_before_save):
            with self.assertRaises(runner.interventions.InterventionError) as caught:
                self.commit(calls)
        self.assertNotIsInstance(caught.exception, util.JSONCommitUncertain)
        self.assertEqual(self.previous, self.state)
        self.assertEqual(self.previous, json.loads(self.path.read_text()))
        self.assertFalse((self.run / self.identity["path"]).exists())
        self.assertEqual(self.previous_bytes, self.previous_artifact.read_bytes())
        self.assertIsNone(self.sink.durable_candidate)
        self.factory.prepare_parent_commit.assert_not_called()
        self.factory.capture_parent_commit.assert_not_called()

    def invoke_outer(self, proxy, calls, consume):
        def handle(*args, **kwargs):
            self.commit(calls)
            return 0

        with (
            patch.object(runner.run_setup, "load_locked", return_value=self.state),
            patch.object(runner.run_actions, "handle", side_effect=handle),
            patch.object(runner, "consume_interventions", side_effect=consume),
        ):
            return runner.run_setup.run_locked(
                proxy,
                SimpleNamespace(),
                None,
                self.state,
                self.path,
                self.run,
                self.run,
                native=calls,
                checkout_owned=True,
            )

    def test_outer_nonfinal_pre_save_failure_rolls_back_uncommitted_handoff(self):
        proxy, calls = self.establish_previous_save()
        code = self.invoke_outer(proxy, calls, self.fail_before_save)
        self.assertEqual(2, code)
        actual = json.loads(self.path.read_text())
        self.assertEqual("PAUSED_INVALID_OUTPUT", actual["status"])
        self.assertNotIn("new_nonfinal_result", actual)
        self.assertEqual("earlier dispatch/status save", actual["previous_observation"])
        self.assertFalse((self.run / self.identity["path"]).exists())
        self.assertEqual(self.previous_bytes, self.previous_artifact.read_bytes())
        self.assertEqual(actual, self.state)
        self.factory.prepare_parent_commit.assert_not_called()
        self.factory.capture_parent_commit.assert_not_called()

    def test_outer_nonfinal_post_save_failure_preserves_only_actual_new_candidate(self):
        proxy, calls = self.establish_previous_save()
        code = self.invoke_outer(proxy, calls, self.fail_after_save)
        self.assertEqual(2, code)
        actual = json.loads(self.path.read_text())
        self.assertEqual("only this boundary may save me", actual["new_nonfinal_result"])
        self.assertNotIn("stop_reason", actual)
        self.assertEqual(self.raw, (self.run / self.identity["path"]).read_bytes())
        self.assertEqual(self.previous_bytes, self.previous_artifact.read_bytes())
        self.assertEqual(actual, self.state)
        self.assertTrue(self.sink.blocked)
        self.factory.prepare_parent_commit.assert_not_called()
        self.factory.capture_parent_commit.assert_not_called()
        self.factory.commit_planning_publication.assert_not_called()


class NeutralUserBoundaryObservationTests(unittest.TestCase):
    """Actual neutral user persistence; no approval or publication intent."""

    setUp = ParentSinkFailureTests.setUp
    dependencies = OuterParentDispositionTests.dependencies
    establish_previous_save = NonfinalBoundaryObservationTests.establish_previous_save

    def prepare_user_action(self):
        proxy, calls = self.establish_previous_save()
        self.candidate.pop("new_nonfinal_result")
        self.candidate["new_user_answer"] = "controlled scoped operator answer"
        return proxy, calls

    def commit(self, calls, *, guarded=False):
        return runner.commit_user_action(
            self.state, self.candidate, self.run, persist=self.sink.write, native=calls, require_current_inputs=guarded
        )

    def test_neutral_user_pre_save_error_cannot_borrow_prior_candidate(self):
        proxy, calls = self.prepare_user_action()
        with patch.object(
            runner.launch_inputs, "guard", side_effect=ValueError("controlled stale input before user save")
        ):
            with self.assertRaises(util.Paused) as caught:
                self.commit(calls, guarded=True)
        self.assertEqual("PAUSED_STALE_VALIDATION", caught.exception.status)
        self.assertNotIsInstance(caught.exception, util.JSONCommitUncertain)
        self.assertEqual(self.previous, json.loads(self.path.read_text()))
        self.assertEqual(self.previous, self.state)
        self.assertFalse((self.run / self.identity["path"]).exists())
        self.assertEqual(self.previous_bytes, self.previous_artifact.read_bytes())
        self.assertIsNone(self.sink.durable_candidate)
        self.factory.prepare_parent_commit.assert_not_called()
        self.factory.capture_parent_commit.assert_not_called()

    def invoke_outer(self, proxy, calls, *, guarded=False):
        def handle(*args, **kwargs):
            self.commit(calls, guarded=guarded)
            return 0

        with (
            patch.object(runner.run_setup, "load_locked", return_value=self.state),
            patch.object(runner.run_actions, "handle", side_effect=handle),
        ):
            return runner.run_setup.run_locked(
                proxy,
                SimpleNamespace(),
                None,
                self.state,
                self.path,
                self.run,
                self.run,
                native=calls,
                checkout_owned=True,
            )

    def test_outer_neutral_user_pre_save_failure_retains_old_bytes_only(self):
        proxy, calls = self.prepare_user_action()
        with patch.object(
            runner.launch_inputs, "guard", side_effect=ValueError("controlled stale input before user save")
        ):
            code = self.invoke_outer(proxy, calls, guarded=True)
        self.assertEqual(2, code)
        actual = json.loads(self.path.read_text())
        self.assertEqual("PAUSED_STALE_VALIDATION", actual["status"])
        self.assertNotIn("new_user_answer", actual)
        self.assertFalse((self.run / self.identity["path"]).exists())
        self.assertEqual(self.previous_bytes, self.previous_artifact.read_bytes())
        self.assertEqual(actual, self.state)
        self.factory.prepare_parent_commit.assert_not_called()
        self.factory.capture_parent_commit.assert_not_called()

    def test_outer_neutral_user_post_save_failure_retains_new_and_old_bytes(self):
        proxy, calls = self.prepare_user_action()
        ordinary = self.sink.ordinary_write

        def fail_after(path, value, **hooks):
            ordinary(path, value, **hooks)
            if Path(path) == self.path:
                raise OSError("controlled neutral user failure after durable save")

        self.sink.ordinary_write = fail_after
        code = self.invoke_outer(proxy, calls)
        self.assertEqual(2, code)
        actual = json.loads(self.path.read_text())
        self.assertEqual("controlled scoped operator answer", actual["new_user_answer"])
        self.assertNotIn("stop_reason", actual)
        self.assertEqual(self.raw, (self.run / self.identity["path"]).read_bytes())
        self.assertEqual(self.previous_bytes, self.previous_artifact.read_bytes())
        self.assertEqual(actual, self.state)
        self.assertTrue(self.sink.blocked)
        self.factory.prepare_parent_commit.assert_not_called()
        self.factory.capture_parent_commit.assert_not_called()
        self.factory.commit_planning_publication.assert_not_called()


class InitialIntentAndScopeTests(unittest.TestCase):
    def profile(self):
        return {
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

    def test_complete_initial_directive_only_not_prose_or_duplicate(self):
        raw = profiles.MARKER + json.dumps(self.profile())
        self.assertEqual(self.profile(), profiles.initial_intent("Actual request\n" + raw))
        self.assertIsNone(profiles.initial_intent("Discuss " + raw))
        with self.assertRaises(ValueError):
            profiles.initial_intent(raw + "\n" + raw)
        with self.assertRaises(ValueError):
            profiles.initial_intent(profiles.MARKER + "{}")

    def test_later_routes_hold_before_artifact_or_provider_setup(self):
        calls = object.__new__(callers.PlanningCalls)
        calls.sink = SimpleNamespace(require_ready=lambda: None)
        calls.initial_task = "Original input"
        calls.stages = frozenset(("requirements", "plan", "plan_review", "plan_revise", "plan_finalize"))
        calls.context = SimpleNamespace(factory=SimpleNamespace(require_planning_origin=Mock()))
        calls.snapshot = Mock()
        state = {"task": "Original input", "settings": {"planning_flow": "v2"}, "next_stage": "terra"}
        with (
            patch.object(runner.artifacts, "reserve") as reserve,
            patch.object(runner.provider_launch, "prepare") as prepare,
        ):
            with self.assertRaises(util.Paused):
                runner.run_role(
                    state=state,
                    role="terra",
                    workspace=Path("/unreachable"),
                    run_dir=Path("/unreachable"),
                    model="model",
                    prompt="prompt",
                    schema=Path("/unreachable"),
                    allow_write=True,
                    sandbox="workspace-write",
                    dry_run=False,
                    native=calls,
                )
        reserve.assert_not_called()
        prepare.assert_not_called()
        calls.snapshot.assert_not_called()
        calls.context.factory.require_planning_origin.assert_not_called()

    def test_fresh_declared_test_command_is_allowed_saved_correction_is_held(self):
        calls = object.__new__(callers.PlanningCalls)
        calls.sink = SimpleNamespace(require_ready=lambda: None)
        calls.initial_task = "Original input"
        args = SimpleNamespace(run_dir=None, test_command="controlled complete command")
        calls.require_supported_action(args, {"task": "Original input", "current_task": None})
        args.run_dir = Path("/saved")
        with self.assertRaises(util.Paused):
            calls.require_supported_action(args, {"task": "Original input", "current_task": None})

    def test_invocation_dependency_view_does_not_replace_global_writer(self):
        calls = SimpleNamespace(sink=SimpleNamespace(write=Mock()), approve=Mock(), reject=Mock())
        original = runner.write_json
        view = invocation.runner_view(runner, calls)
        self.assertIs(original, runner.write_json)
        self.assertIsNot(original, view.write_json)
        view.approve_goal({"controlled": True}, "exact token")
        calls.approve.assert_called_once_with(lifecycle.approve, {"controlled": True}, "exact token")

    def test_changed_complete_initial_input_refuses_before_origin_recheck(self):
        calls = object.__new__(callers.PlanningCalls)
        calls.sink = SimpleNamespace(require_ready=lambda: None)
        calls.initial_task = "Original operator task\n" + profiles.MARKER + json.dumps(self.profile())
        args = SimpleNamespace(run_dir=Path("/saved"))
        with self.assertRaisesRegex(util.Paused, "initial operator input changed"):
            calls.require_supported_action(
                args, {"task": calls.initial_task.replace("Original operator task", "Changed task")}
            )

    def test_legacy_saved_profile_cannot_activate_before_provider_selection(self):
        with tempfile.TemporaryDirectory(prefix="native-legacy-intent-unit-") as tmp:
            root = Path(tmp).resolve()
            state = {"task": "Original task\n" + profiles.MARKER + json.dumps(self.profile())}
            args = SimpleNamespace(run_dir=root, _native_intent=None)
            base = SimpleNamespace(support=util, json_output=runner.json_output)
            with patch.object(invocation, "select_provider") as select:
                with self.assertRaisesRegex(util.Paused, "retrospectively"):
                    invocation.run(base, args, None, (root, root, root / "state.json", state))
            select.assert_not_called()

    def test_final_output_and_capture_environment_precede_native_seal_preparation(self):
        authority = object.__new__(isolation.NativeRoleIsolation)
        authority.containment_request = lambda **kwargs: {}
        descriptor = {"manifest": "/controlled/native-manifest.json", "manifest_sha256": "a" * 64}
        authority._descriptor = descriptor  # Controlled loader result; no native authority.
        provided = {
            "PATH": "/usr/bin:/bin",
            "AUTOCODE_OUTPUT_MODE": "report_file",
            "AUTOCODE_OUTPUT_STORE": "/owned/report.json",
            "AUTOCODE_CAPTURE_CONTEXT": '{"nonce":"controlled exact context"}',
        }
        seen = []

        def launch(*args, **kwargs):
            seen.append(deepcopy(kwargs))
            environment = {
                **kwargs["env"],
                "AUTOCODE_NATIVE_ROLE_ISOLATION": json.dumps(descriptor),
                "AUTOCODE_TOOL_CONTAINMENT": json.dumps({"native_role_isolation": descriptor}),
            }
            return ["controlled", "literal command"], environment, {}

        adapter = SimpleNamespace(
            CONFIGURED=False, NAME="opencode", launch=launch, transport_version=lambda settings: "1.18.33"
        )
        command, environment, overrides, worker = launch_policy.prepare(
            engine="opencode",
            adapter=adapter,
            role="glm",
            route_role="glm",
            workspace=Path("/owned"),
            run_dir=Path("/owned/.autocode/runs/new"),
            session=None,
            model="controlled/model",
            effort="high",
            allow_write=False,
            planning=True,
            report=Path("/owned/report.json"),
            schema=Path("/owned/schema.json"),
            prompt_file=Path("/owned/prompt.md"),
            sandbox="read-only",
            transport_args=[],
            chatgpt=False,
            provider="opencode",
            settings={},
            native_role_isolation=authority,
            native_environment=provided,
        )
        self.assertEqual(provided, seen[0]["env"])
        self.assertEqual("1.18.33", seen[0]["opencode_version"])
        self.assertTrue(all(environment[key] == value for key, value in provided.items()))
        self.assertEqual(environment, worker["environment"])


class InitialValidateDeclarationTests(unittest.TestCase):
    def fixture(self):
        first = {
            "kind": "validate",
            "milestone_id": "M1",
            "objective": "Check owned source",
            "requirements": ["Preserve source"],
            "acceptance_criteria": ["C1"],
            "affected_paths": [],
            "validation_plan": ["controlled check"],
            "findings": [],
        }
        body = {
            "open_blocking_questions": [],
            "acceptance_criteria": [{"id": "C1"}],
            "constraints": ["VISUAL_SLICE_GATES=complete-cases-v1", 'VISUAL_CASE_CRITERIA={"only":["C1"]}'],
            "milestones": [{"id": "M1", "acceptance_criteria": ["C1"], "depends_on": [], "affected_paths": ["ui.py"]}],
            "initial_task": first,
        }
        saved = {"task_id": "controlled", "revision": 1, "body": body}
        saved["hash"] = util.digest(saved)
        saved.update(approval_status="approved", origin="planning")
        event = {"actor": "user_cli", "kind": "goal_approval", "token": contracts.token(saved)}
        saved["approval_event"] = event
        state = {
            "goal_contract": saved,
            "current_task": None,
            "user_events": [deepcopy(event)],
            "settings": {
                "milestone_checkpoints": {"enabled": True},
                "design_manifest": {
                    "body": {"version": 1, "cases": [{"id": "only", "implementation_paths": ["ui.py"]}]}
                },
            },
        }
        current = {"head": "controlled", "files": {"ui.py": "a" * 64}}
        current["revision"] = util.digest(current)
        decision = {
            "status": "CONTINUE",
            "next_objective": first["objective"],
            "affected_paths": ["ui.py"],
            "next_task": {k: v for k, v in first.items() if k not in ("objective", "affected_paths")},
        }
        proof = checkpoint.VerifiedInitialAssignment(
            checkpoint.binding(state, current, initial=True),
            current["revision"],
            "b" * 64,
            checkpoint.assignment_identity(decision),
        )
        return state, current, decision, proof

    def test_initial_validate_inherits_exact_owned_milestone_paths(self):
        state, current, decision, proof = self.fixture()
        checkpoint.require_initial_assignment(state, current, proof, decision)

    def test_changed_paths_or_missing_proof_still_refuse(self):
        state, current, decision, proof = self.fixture()
        with self.assertRaises(util.Paused):
            checkpoint.require_initial_assignment(state, current, None, decision)
        changed = deepcopy(decision)
        changed["affected_paths"] = ["unowned.py"]
        with self.assertRaises(util.Paused):
            checkpoint.require_initial_assignment(state, current, proof, changed)

    def test_real_assignment_threads_exact_initial_validate_proof(self):
        state, current, decision, proof = self.fixture()
        original = deepcopy(state["goal_contract"])
        lifecycle.assign_task(state, decision, current, visual_proof=proof)
        self.assertEqual(["ui.py"], state["current_task"]["affected_paths"])
        self.assertEqual("validate", state["current_task"]["kind"])
        self.assertEqual(original, state["goal_contract"])


class ActualApprovalCallbackTests(unittest.TestCase):
    """Actual nonvisual lifecycle ordering, never an image/native approval."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="native-first-approval-unit-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / ".gitignore").write_text(".autocode/\n")
        (self.root / "greet.py").write_text("original source\n")
        for args in (
            ["init", "-q"],
            ["add", "."],
            ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "controlled"],
        ):
            subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True)
        self.state = {
            "version": 2,
            "workspace": str(self.root),
            "task": "Original request",
            "iteration": 1,
            "status": "RUNNING",
            "stages": [],
            "history": [],
            "sessions": {},
            "settings": {"joint_planning": True, "milestone_checkpoints": {"enabled": True}},
        }
        lifecycle.migrate(self.state, fresh=True)
        self.body = goal_body()
        self.body["initial_task"] = {
            "objective": "Implement declared greeting",
            "kind": "implement",
            "milestone_id": "M1",
            "affected_paths": ["greet.py"],
            "requirements": ["Declare original flow"],
            "acceptance_criteria": ["C1"],
            "validation_plan": ["Review declared source"],
        }

    def draft(self):
        lifecycle.install_draft(self.state, self.body, origin="test", queue_human=False)
        # Controlled nonvisual review frontier. This supplies no native journal,
        # launch, image receipt or committed parent authority.
        output = self.root / ".autocode" / "controlled-final.json"
        output.parent.mkdir(exist_ok=True)
        output.write_text('{"unit_fixture":"nonvisual review"}\n')
        revision = runner.source_scope.snapshot(self.root, self.state, base_snapshot=util.snapshot)["revision"]
        self.state["stages"] = [{"stage": "astra_finalize", "output": str(output), "source_revision": revision}]
        self.state["planning"] = {
            "astra_calls": 0,
            "reports": {"astra_finalize": {"output": str(output)}},
            "final_token": contracts.token(self.state["goal_contract"]),
        }
        lifecycle.human.queue(
            self.state,
            "goal_approval",
            {"stage": "controlled nonvisual review"},
            status="AWAITING_GOAL_APPROVAL",
            next_stage="astra_plan",
        )
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)

    def test_draft_feasibility_calls_no_assignment_and_preserves_constraints(self):
        self.body["constraints"].append("VISUAL_SLICE_GATES=complete-cases-v1")
        self.body["constraints"].append('VISUAL_CASE_CRITERIA={"only":["C1"]}')
        self.state["settings"]["design_manifest"] = {
            "body": {"version": 1, "cases": [{"id": "only", "implementation_paths": ["greet.py"]}]}
        }
        original = deepcopy(self.body)
        # Only external manifest file pin verification is controlled here;
        # actual draft schema and complete-case policy are still applied.
        with (
            patch.object(lifecycle.design_plan.manifest, "verify"),
            patch.object(lifecycle, "assign_task", side_effect=AssertionError("draft invented assignment")),
        ):
            lifecycle.validate_body(self.state, self.body, ready=True)
        self.assertEqual(original, self.body)
        self.assertIsNone(self.state.get("goal_contract"))

    def test_callback_occurs_after_actual_event_before_exact_first_assignment(self):
        self.draft()
        selected = self.state["displayed_goal"]
        seen = []
        sentinel = object()

        def proof(candidate, *, snapshot):
            event = candidate["goal_contract"]["approval_event"]
            self.assertEqual(("user_cli", "goal_approval", selected), (event["actor"], event["kind"], event["token"]))
            self.assertIn(event, candidate["user_events"])
            self.assertEqual("approved", candidate["goal_contract"]["approval_status"])
            self.assertIsNone(candidate.get("current_task"))
            seen.append(snapshot)
            return sentinel

        with patch.object(lifecycle, "assign_task", wraps=lifecycle.assign_task) as assignment:
            lifecycle.approve(self.state, selected, initial_assignment_proof=proof)
        self.assertEqual(1, len(seen))
        self.assertIs(sentinel, assignment.call_args.kwargs["visual_proof"])
        self.assertEqual(["greet.py", "test_greeting.py"], self.state["current_task"]["affected_paths"])
        self.assertEqual(seen[0]["revision"], self.state["current_task"]["source_revision"])

    def test_wrong_token_or_missing_final_review_calls_no_producer(self):
        self.draft()
        original = deepcopy(self.state)
        callback = Mock(side_effect=AssertionError("unexpected producer"))
        with self.assertRaises(ValueError):
            lifecycle.approve(self.state, "wrong token", initial_assignment_proof=callback)
        self.assertEqual(original, self.state)
        self.state["planning"] = {}
        with self.assertRaises(ValueError):
            lifecycle.approve(self.state, self.state["displayed_goal"], initial_assignment_proof=callback)
        callback.assert_not_called()

    def test_implement_empty_scope_refuses_but_validate_inherits_owned_paths(self):
        self.body["initial_task"]["affected_paths"] = []
        with self.assertRaises(ValueError):
            feasibility.first_task(self.body, self.state["settings"], workspace=self.root)
        self.body["initial_task"]["kind"] = "validate"
        self.assertEqual(
            ["greet.py", "test_greeting.py"],
            feasibility.first_task(self.body, self.state["settings"], workspace=self.root),
        )
