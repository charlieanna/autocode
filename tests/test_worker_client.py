"""Both clients consume worker actions from public JSON, without worker ownership."""

import json
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_chat_client as chat
import autocode_chat_worker as chat_worker
import autocode_client_actions as actions
import autocode_taskrun as taskrun
import autocode_worker_view as worker_view

from tests.test_chat_tui import screen_ui

WORKSPACE = Path("/project")
RUN = WORKSPACE / ".autocode" / "runs" / "one"
OBSERVATION = {"workspace": str(WORKSPACE), "run_dir": str(RUN), "checkpoint_token": "a" * 64}
EXECUTION = worker_view.execution(
    {"version": 1, "mode": "durable_local", "launch_id": "b" * 32, "controller_epoch": "c" * 32},
    observation=OBSERVATION,
)


class NamedWorkerTransport(unittest.TestCase):
    def test_exact_current_envelope_preserves_the_task_action_and_refuses_another_run(self):
        words = ["--follow-up", "Keep $HOME %n literal 你好"]
        wrapped = actions.worker_transport(EXECUTION, words, WORKSPACE, RUN)
        self.assertEqual(words, wrapped[wrapped.index("--") + 1 :])
        with self.assertRaises(actions.ClientGap):
            actions.worker_transport(EXECUTION, words, WORKSPACE, RUN.with_name("another"))
        for invalid in (
            {},
            {**EXECUTION, "next_command": ["sh", "-c", "anything"]},
            {**EXECUTION, "detach_supported": False},
        ):
            with self.assertRaises(actions.ClientGap):
                actions.worker_transport(invalid, words, WORKSPACE, RUN)

    def test_absent_legacy_fields_do_not_advertise_durability(self):
        self.assertEqual({"version": 1, "mode": "owned", "detach_supported": False}, worker_view.execution(None))
        self.assertFalse(worker_view.execution({"version": 1, "mode": "durable_local"})["detach_supported"])

    def test_durable_ctrl_d_and_quit_leave_while_legacy_working_stays(self):
        for keys in (("\x04",), tuple("/quit\n")):
            ui = screen_ui(*keys)
            ui.observe({"execution": EXECUTION})
            with self.assertRaises(EOFError):
                ui.wait(1)
        ui = screen_ui("\x04", *"/pause\n")
        ui.observe({})
        self.assertEqual("pause", ui.wait(1))

    def test_a_core_launch_receipt_permits_detach_before_a_run_view_exists(self):
        ui = screen_ui("\x04")
        ui.worker_launch({"execution": {"mode": "durable_local", "detach_supported": True}})
        with self.assertRaises(EOFError):
            ui.wait(1)
        ui.observe({})
        self.assertFalse(ui._can_detach())

    def test_repeated_ctrl_c_in_durable_watch_never_sends_a_worker_signal(self):
        client = chat.ChatClient(
            WORKSPACE, RUN, say=Mock(), wait=Mock(side_effect=[KeyboardInterrupt, KeyboardInterrupt, EOFError])
        )
        self.addCleanup(client.close)
        view = {
            "status": "RUNNING",
            "needs": {"kind": "continue"},
            "controller": {"kind": "running", "checked": True, "alive": True},
            "execution": EXECUTION,
        }
        with (
            patch.object(client, "status", return_value=view),
            patch.object(client, "request_pause", return_value=True) as pause,
        ):
            with self.assertRaises(EOFError):
                client.watch()
        self.assertEqual(2, pause.call_count)

    def test_headless_driver_returns_a_receipt_and_does_not_wait_for_or_claim_completion(self):
        receipt = {
            "launch_id": "b" * 32,
            "inspect_command": ["autocode", "worker", "inspect", "b" * 32],
            "ready": False,
        }
        process = Mock(return_value=subprocess.CompletedProcess([], 0, json.dumps(receipt), ""))
        run = taskrun.TaskRun(WORKSPACE, RUN)
        command = actions.NamedCommand(["--approve-goal", "-"], {"AUTOCODE_APPROVE_GOAL_TOKEN": "private-token"})
        with patch.object(taskrun, "run_process", process):
            observed = run.dispatch_durable(command, view={**OBSERVATION, "execution": EXECUTION})
        self.assertEqual(receipt, observed)
        process.assert_called_once()
        argv = process.call_args.args[0]
        self.assertNotIn("private-token", argv)
        self.assertEqual("private-token", process.call_args.kwargs["env"]["AUTOCODE_APPROVE_GOAL_TOKEN"])
        self.assertFalse(process.call_args.kwargs["advancing"])

    def test_ctrl_c_while_launch_receipt_is_pending_never_signals_or_replays_the_launcher(self):
        child = Mock(returncode=0)
        child.poll.side_effect = [None, None, 0]
        client = SimpleNamespace(
            command=("/runtime/python", "/runtime/autocode.py"),
            workspace=WORKSPACE,
            env={},
            _pause_pending=False,
            say=Mock(),
            _wait=Mock(side_effect=[KeyboardInterrupt, None]),
        )

        def started(*args, **kwargs):
            kwargs["stdout"].write('{"launch_id":"retained"}')
            return child

        with patch.object(chat_worker.subprocess, "Popen", side_effect=started) as popen:
            receipt = chat_worker.launch_command(client, ["worker", "launch"])
        self.assertEqual(0, receipt.returncode)
        self.assertTrue(client._pause_pending)
        self.assertEqual(1, popen.call_count)
        self.assertTrue(popen.call_args.kwargs["start_new_session"])
        self.assertEqual(subprocess.DEVNULL, popen.call_args.kwargs["stdin"])
        child.send_signal.assert_not_called()
        child.kill.assert_not_called()
        child.terminate.assert_not_called()

    def test_leaving_during_launch_receipt_wait_never_kills_or_replays_it(self):
        child = Mock()
        child.poll.return_value = None
        client = SimpleNamespace(
            command=("/runtime/core",),
            workspace=WORKSPACE,
            env={},
            _wait=Mock(side_effect=EOFError),
            _pause_pending=False,
            say=Mock(),
        )
        with patch.object(chat_worker.subprocess, "Popen", return_value=child) as popen:
            with self.assertRaises(EOFError):
                chat_worker.launch_command(client, ["worker", "launch"])
        popen.assert_called_once()
        child.kill.assert_not_called()
        child.terminate.assert_not_called()
