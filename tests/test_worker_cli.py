"""Exact durable action admission and retained outcomes; no paid or supervised provider."""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_util as util
import autocode_worker_binding as binding
import autocode_worker_cli as worker
import autocode_worker_records as records
import autocode_worker_service as service


class WorkerActions(unittest.TestCase):
    def test_only_accepted_durable_human_actions_continue_in_the_same_core_loop(self):
        args = SimpleNamespace(_worker_context={"verified": True}, approve_goal="exact-plan-token", unit=None)
        self.assertTrue(binding.continue_after_action(args, {"status": "RUNNING"}, 0))
        self.assertFalse(binding.continue_after_action(args, {"status": "WAITING_FOR_USER"}, 0))
        self.assertFalse(binding.continue_after_action(args, {"status": "RUNNING"}, 2))
        self.assertFalse(binding.continue_after_action(SimpleNamespace(approve_goal="token"), {"status": "RUNNING"}, 0))
        self.assertFalse(
            binding.continue_after_action(SimpleNamespace(_worker_context={}, show_goal=True), {"status": "RUNNING"}, 0)
        )
        args.unit = "autoplanner"
        self.assertFalse(binding.continue_after_action(args, {"status": "RUNNING"}, 0))

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.workspace = self.base / "project"
        self.workspace.mkdir()
        self.run_dir = self.workspace / ".autocode" / "runs" / "one"
        self.root = self.base / "launches"
        for name, value in (
            ("storage", self.root),
            ("boot_id", "boot-one"),
            ("runtime_identity", {"sha256": "runtime"}),
        ):
            held = patch.object(binding, name, return_value=value)
            held.start()
            self.addCleanup(held.stop)
        self.adapter = Mock()
        self.adapter.capability.return_value = {"available": True}
        self.adapter.inspect.return_value = {"checked": True, "present": False}

    def launch(self, argv=None, **kwargs):
        return worker.launch(
            self.workspace,
            self.run_dir,
            argv or ["--brief", "Fix $HOME literally", "--in-place"],
            services=self.adapter,
            **kwargs,
        )

    def test_lost_start_reply_is_reconciled_even_after_the_run_exists_and_unit_was_collected(self):
        self.adapter.start.side_effect = service.ServiceOutcomeUnknown("reply lost")
        first = self.launch(request_id="same-request")
        self.assertEqual(1, first["dispatch_count"])
        self.assertFalse(first["ready"])
        self.run_dir.mkdir(parents=True)
        util.atomic_json(self.run_dir / "state.json", {"workspace": str(self.workspace), "status": "RUNNING"})
        repeated = self.launch(request_id="same-request")
        self.assertEqual(first["launch_id"], repeated["launch_id"])
        self.adapter.start.assert_called_once()
        self.assertFalse(repeated["ready"])
        self.assertNotIn("complete", repeated)

    def test_a_reused_request_id_cannot_change_task_or_private_authority(self):
        self.launch(request_id="one")
        with self.assertRaisesRegex(ValueError, "different content"):
            self.launch(["--brief", "another task"], request_id="one")
        with patch.dict(os.environ, {"AUTOCODE_APPROVE_GOAL_TOKEN": "new-authority"}):
            with self.assertRaisesRegex(ValueError, "different content"):
                self.launch(request_id="one")
        self.adapter.start.assert_called_once()

    def test_capability_failure_never_dispatches_and_inspection_does_not_create_storage(self):
        with self.assertRaises(OSError):
            worker.inspect("a" * 32, services=self.adapter)
        self.assertFalse(self.root.exists())
        self.adapter.capability.return_value = {"available": False}
        with self.assertRaises(service.ServiceUnavailable):
            self.launch()
        self.adapter.start.assert_not_called()

    def test_saved_action_requires_current_checkpoint_runtime_and_durable_opt_in(self):
        self.run_dir.mkdir(parents=True)
        state = {
            "workspace": str(self.workspace),
            "execution": {"mode": "durable_local", "runtime": {"sha256": "runtime"}},
        }
        util.atomic_json(self.run_dir / "state.json", state)
        for checkpoint, current in (("a" * 64, state), (util.digest(state), {**state, "execution": {}})):
            util.atomic_json(self.run_dir / "state.json", current)
            with self.assertRaises(ValueError):
                self.launch(["--no-chat"], checkpoint=checkpoint)
        self.adapter.start.assert_not_called()

    def test_controller_checks_the_checkpoint_before_configuration_or_a_new_owner(self):
        state = {
            "workspace": str(self.workspace),
            "execution": {"mode": "durable_local", "runtime": {"sha256": "runtime"}},
        }
        wanted = records.request(
            workspace=str(self.workspace),
            run_dir=str(self.run_dir),
            argv=["--no-chat"],
            source={"sha256": "runtime"},
            boot_id="boot-one",
            request_id="one",
            expected_checkpoint=util.digest(state),
        )
        args = SimpleNamespace(run_dir=self.run_dir, _worker_context={"request": wanted})
        binding.guard(args, state, self.run_dir, self.workspace)
        with self.assertRaisesRegex(ValueError, "stale"):
            binding.guard(args, {**state, "status": "RUNNING"}, self.run_dir, self.workspace)
        with self.assertRaisesRegex(ValueError, "named worker action"):
            binding.guard(SimpleNamespace(), state, self.run_dir, self.workspace)

    def test_manager_pid_invocation_boot_and_literal_arguments_are_all_required_at_entry(self):
        first = self.launch()
        record = records.read(self.root, first["launch_id"])
        path = self.root / (record["id"] + ".json")
        observed = {"checked": True, "present": True, "pid": os.getpid(), "invocation_id": "b" * 32}
        with patch.dict(os.environ, {"INVOCATION_ID": "b" * 32}):
            self.adapter.inspect.return_value = observed
            self.assertEqual(record, binding.load_entry(path, record["request"]["argv"], services=self.adapter))
            for changed in ({"checked": False}, {"pid": os.getpid() + 1}, {"invocation_id": "c" * 32}):
                self.adapter.inspect.return_value = {**observed, **changed}
                with self.assertRaises(ValueError):
                    binding.load_entry(path, record["request"]["argv"], services=self.adapter)
            self.adapter.inspect.return_value = observed
            with self.assertRaises(ValueError):
                binding.load_entry(path, ["changed action"], services=self.adapter)
            with patch.object(binding, "boot_id", return_value="rebooted"):
                with self.assertRaises(ValueError):
                    binding.load_entry(path, record["request"]["argv"], services=self.adapter)

    def test_receipt_binding_alone_is_not_readiness_without_the_matching_committed_run(self):
        first = self.launch()
        record = records.read(self.root, first["launch_id"])
        owner = SimpleNamespace(receipt={"id": "owner-one"})
        state = {"workspace": str(self.workspace)}
        binding.bind(
            SimpleNamespace(_worker_context=record), state, self.run_dir, self.workspace, owner, util.atomic_json
        )
        ready = worker.inspect(first["launch_id"], services=self.adapter)
        self.assertTrue(ready["ready"])
        self.assertEqual("owner-one", ready["binding"]["controller_id"])
        (self.run_dir / "state.json").unlink()
        self.assertFalse(worker.inspect(first["launch_id"], services=self.adapter)["ready"])

    def test_uncertain_seal_persistence_holds_instead_of_replaying_dispatch(self):
        write = util.atomic_json

        def lose_reply(path, value):
            write(path, value)
            if value.get("phase") == "dispatching":
                raise util.JSONCommitUncertain(
                    OSError("directory fsync outcome lost"), candidate=value, replaced=True, durable=False
                )

        with patch.object(records.util, "atomic_json", side_effect=lose_reply):
            with self.assertRaises(util.JSONCommitUncertain):
                self.launch(request_id="uncertain-seal")
        self.adapter.start.assert_not_called()
        held = self.launch(request_id="uncertain-seal")
        self.assertEqual("dispatching", held["phase"])
        self.assertFalse(held["ready"])
        self.adapter.start.assert_not_called()

    def test_reboot_changed_runtime_missing_workspace_or_run_never_replay(self):
        first = self.launch(request_id="retained")
        for field, value, reason in (
            ("boot_id", "after-reboot", "rebooted"),
            ("runtime_identity", {"sha256": "changed"}, "runtime changed"),
        ):
            with patch.object(binding, field, return_value=value):
                inspected = worker.inspect(first["launch_id"], services=self.adapter)
                self.assertFalse(inspected["ready"])
                self.assertIn(reason, inspected["reason"])
                with self.assertRaisesRegex(ValueError, "different content"):
                    self.launch(request_id="retained")
        self.workspace.rmdir()
        with self.assertRaises(OSError):
            self.launch(request_id="retained")
        self.adapter.start.assert_called_once()

    def test_reboot_observation_is_explicit_and_an_unavailable_boot_read_stays_unknown(self):
        execution = {"launch_id": "a" * 32, "boot_id": "previous-boot"}
        with patch("autocode_worker_service.UserServices", return_value=self.adapter):
            self.assertFalse(binding.observation(execution)["boot_matches"])
            with patch.object(binding, "boot_id", side_effect=OSError("unavailable")):
                self.assertIsNone(binding.observation(execution)["boot_matches"])
        self.adapter.start.assert_not_called()
