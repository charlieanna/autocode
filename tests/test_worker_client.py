"""Both clients consume worker actions from public JSON, without worker ownership."""

import json
import signal
import subprocess
import sys
import tempfile
import threading
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
WORKER_LAUNCH_OFFER = {  # the modes list's durable offer a fresh run launches through (fresh=True)
    "id": "durable_local",
    "available": True,
    "next_command": [
        "autocode",
        "worker",
        "launch",
        "--workspace",
        "WORKSPACE",
        "--run-dir",
        "RUN_DIR",
        "--checkpoint",
        "CHECKPOINT",
        "--",
    ],
}


class NamedWorkerTransport(unittest.TestCase):
    def test_ctrl_c_at_a_durable_question_does_not_exit_or_signal_the_worker(self):
        client = chat.ChatClient(WORKSPACE, RUN, say=Mock(), ask=Mock(side_effect=[KeyboardInterrupt, "kept reply"]))
        self.addCleanup(client.close)
        client._status_view = {"execution": EXECUTION}
        with patch.object(client, "request_pause", return_value=True) as pause:
            self.assertEqual("kept reply", client.ask("Your answer?"))
            pause.assert_called_once()
        legacy = chat.ChatClient(WORKSPACE, RUN, say=Mock(), ask=Mock(side_effect=KeyboardInterrupt))
        self.addCleanup(legacy.close)
        with self.assertRaises(chat.Left):
            legacy.ask("Your answer?")

    def test_idle_durable_reattachment_does_not_launch_until_the_user_continues(self):
        client = chat.ChatClient(WORKSPACE, RUN, say=Mock(), ask=Mock(side_effect=EOFError))
        self.addCleanup(client.close)
        view = {
            "status": "RUNNING",
            "needs": {"kind": "continue"},
            "execution": EXECUTION,
            "controller": {"kind": "ready", "checked": True, "alive": False},
            "next_command": "autocode --no-chat",
        }
        with patch.object(client, "status", return_value=view), patch.object(client, "take") as take:
            self.assertEqual(2, client.converse())
            take.assert_not_called()

    def test_manual_continue_does_not_apply_to_a_boundary_changed_while_it_was_displayed(self):
        client = chat.ChatClient(WORKSPACE, RUN, say=Mock(), ask=Mock(return_value=""))
        self.addCleanup(client.close)
        view = {
            "status": "RUNNING",
            "needs": {"kind": "continue"},
            "execution": EXECUTION,
            "controller": {"kind": "ready", "checked": True, "alive": False},
            "next_command": "autocode --no-chat",
        }
        changed = {**view, "execution": {**EXECUTION, "next_command": [*EXECUTION["next_command"][:8], "d" * 64, "--"]}}
        with patch.object(client, "status", side_effect=[view, changed]), patch.object(client, "take") as take:
            self.assertIsNone(client.step())
            take.assert_not_called()

    def test_sigint_after_first_live_view_before_watch_requests_boundary_pause(self):
        client = chat.ChatClient(WORKSPACE, RUN, say=Mock())
        self.addCleanup(client.close)
        view = {
            "status": "RUNNING",
            "needs": {"kind": "continue"},
            "controller": {"kind": "running", "checked": True, "alive": True},
            "execution": EXECUTION,
        }

        def status():
            client._status_view = view
            signal.raise_signal(signal.SIGINT)  # a live view is already visible, before watch() handles keys

        def paused():
            self.assertTrue(client._attached)
            return True

        previous = signal.signal(signal.SIGINT, signal.default_int_handler)
        try:
            calls = iter((status, lambda: 2))
            with (
                patch.object(client, "step", side_effect=lambda: next(calls)()),
                patch.object(client, "request_pause", side_effect=paused) as pause,
                patch.object(client, "advance") as launch,
            ):
                self.assertEqual(2, client.converse())
                pause.assert_called_once()
                launch.assert_not_called()
        finally:
            signal.signal(signal.SIGINT, previous)

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
        child.poll.side_effect = [None, None, 0, 0]
        client = SimpleNamespace(
            command=("/runtime/python", "/runtime/autocode.py"),
            workspace=WORKSPACE,
            env={},
            _pause_pending=False,
            say=Mock(),
            _wait=Mock(side_effect=[KeyboardInterrupt, None]),
        )

        def started(*args, **kwargs):
            kwargs["stdout"].write(b'{"launch_id":"retained"}')
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

    def test_an_unreadable_inspection_body_is_a_gap_not_a_crash(self):
        receipt = {
            "launch_id": "b" * 32,
            "inspect_command": ["autocode", "worker", "inspect", "b" * 32],
            "ready": False,
        }
        child = Mock(returncode=0)
        child.poll.side_effect = [None, 0, 0]

        def started(*args, **kwargs):
            kwargs["stdout"].write(json.dumps(receipt).encode())
            return child

        client = SimpleNamespace(
            command=("/runtime/core",),
            workspace=WORKSPACE,
            run_dir=RUN,
            env={},
            _execution_offer=WORKER_LAUNCH_OFFER,
            _status_view=None,
            _pause_pending=False,
            _attached=False,
            _launched_worker=None,
            say=Mock(),
            _wait=Mock(return_value=None),
            cli=Mock(return_value=subprocess.CompletedProcess([], 0, "", "")),  # rc 0, empty body
            watch=Mock(),
        )
        with patch.object(chat_worker.subprocess, "Popen", side_effect=started):
            with self.assertRaisesRegex(actions.ClientGap, "inspection is unreadable"):
                chat_worker.advance(client, ["--new-run-dir", str(RUN)])
        client.watch.assert_not_called()

    def test_a_ready_receipt_without_a_binding_is_a_gap_not_a_crash(self):
        receipt = {
            "launch_id": "b" * 32,
            "inspect_command": ["autocode", "worker", "inspect", "b" * 32],
            "ready": True,
        }
        child = Mock(returncode=0)
        child.poll.side_effect = [None, 0, 0]

        def started(*args, **kwargs):
            kwargs["stdout"].write(json.dumps(receipt).encode())
            return child

        client = SimpleNamespace(
            command=("/runtime/core",),
            workspace=WORKSPACE,
            run_dir=RUN,
            env={},
            _execution_offer=WORKER_LAUNCH_OFFER,
            _status_view=None,
            _pause_pending=False,
            _attached=False,
            _launched_worker=None,
            say=Mock(),
            _wait=Mock(return_value=None),
            watch=Mock(),
        )
        with patch.object(chat_worker.subprocess, "Popen", side_effect=started):
            with self.assertRaisesRegex(actions.ClientGap, "no core binding"):
                chat_worker.advance(client, ["--new-run-dir", str(RUN)])
        client.watch.assert_not_called()

    def test_a_launcher_log_with_invalid_bytes_is_replaced_not_fatal(self):
        with tempfile.TemporaryDirectory() as folder:
            client = SimpleNamespace(
                command=(sys.executable,),
                workspace=Path(folder),
                env={},
                _pause_pending=False,
                say=Mock(),
                _wait=Mock(return_value=None),
            )
            reply = chat_worker.launch_command(client, ["-c", "import sys; sys.stdout.buffer.write(b'\\xff\\xfe bad')"])
        self.assertEqual(0, reply.returncode)
        self.assertIn("\ufffd", reply.stdout)

    def test_a_pending_launcher_is_reaped_by_a_daemon_thread(self):
        with tempfile.TemporaryDirectory() as folder:
            client = SimpleNamespace(
                command=(sys.executable,),
                workspace=Path(folder),
                env={},
                _pause_pending=False,
                say=Mock(),
                _wait=Mock(return_value=None),
            )
            with patch.object(chat_worker.polling, "READ_TIMEOUT", 0.05):
                with self.assertRaisesRegex(actions.ClientGap, "receipt is pending"):
                    chat_worker.launch_command(client, ["-c", "import time; time.sleep(1)"])
            reaper = next(thread for thread in threading.enumerate() if thread.name == "autocode-chat-launch-reap")
            reaper.join(10)  # the launcher exits on its own; the reaper must not outlive it
        self.assertFalse(reaper.is_alive())
