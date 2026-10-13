"""Focused offline regressions for cancelled kernel session handoffs."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_process as processes
import autocode_supervision as supervision
import autocode_supervision_handoff as handoff


class SessionHandoffTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.creator = {"pid": 502, "birth_identity": 2.0, "started": "birth-2", "group": 501, "session": 501}
        self.record = {"kind": "setsid_pending", "creator": self.creator, "requested_session": 502}
        self.metadata = {
            "schema": 1,
            "nonce": "a" * 32,
            "owner": {"pid": 500, "birth_identity": 1.0},
            "keeper": {"pid": 600, "birth_identity": 3.0},
            "provider": {"pid": 501, "birth_identity": 1.5},
            "receipt": str(Path(self.directory.name) / "receipt.json"),
        }
        self.notice = {"session_admission": self.record, "request": 123, "guard": self.metadata["nonce"]}
        self.value = {
            **self.metadata,
            "phase": "armed",
            "cause": None,
            "cleanup_error": None,
            "session_admissions": [self.record],
        }
        self.save()

    def save(self):
        Path(self.metadata["receipt"]).write_text(json.dumps(self.value))

    def reply(self):
        return supervision.session_handoff_reply(self.metadata, json.dumps(self.notice).encode(), 15)

    def test_live_durably_owned_request_propagates_the_enclosing_ack(self):
        with (
            patch.object(processes, "process_table", return_value={502: self.creator}),
            patch.object(handoff, "admit_session", return_value=True) as admit,
        ):
            self.assertEqual({"session_admitted": 123, "guard": "a" * 32}, self.reply())
        admit.assert_called_once_with(self.metadata, self.record, 15)

    def test_a_cancelled_enclosing_ack_never_becomes_a_grant(self):
        with (
            patch.object(processes, "process_table", return_value={502: self.creator}),
            patch.object(handoff, "admit_session", return_value=False),
        ):
            self.assertEqual({"session_cancelled": 123, "guard": "a" * 32}, self.reply())

    def test_dead_and_reused_creators_cancel_without_outer_admission(self):
        for fresh in ({}, {502: {**self.creator, "birth_identity": 999}}):
            with (
                self.subTest(fresh=fresh),
                patch.object(processes, "process_table", return_value=fresh),
                patch.object(handoff, "admit_session") as admit,
            ):
                self.assertEqual({"session_cancelled": 123, "guard": "a" * 32}, self.reply())
                admit.assert_not_called()

    def test_retired_receipt_record_cancels_the_queued_notice(self):
        self.value["session_admissions"] = []
        self.save()
        with patch.object(processes, "process_table") as native, patch.object(handoff, "admit_session") as admit:
            self.assertEqual({"session_cancelled": 123, "guard": "a" * 32}, self.reply())
        native.assert_not_called()
        admit.assert_not_called()

    def test_terminal_clean_publication_makes_a_queued_notice_obsolete(self):
        self.value.update(phase="stopped", cause="provider_stopped", session_admissions=[])
        self.save()
        with patch.object(processes, "process_table") as native, patch.object(handoff, "admit_session") as admit:
            self.assertIsNone(self.reply())
        native.assert_not_called()
        admit.assert_not_called()

    def test_a_foreign_or_missing_receipt_cannot_cancel_or_grant(self):
        for foreign in (True, False):
            with self.subTest(foreign=foreign):
                if foreign:
                    self.value["nonce"] = "b" * 32
                    self.save()
                else:
                    Path(self.metadata["receipt"]).unlink()
                with patch.object(handoff, "admit_session") as admit:
                    with self.assertRaisesRegex(supervision.SupervisionError, "receipt is unavailable"):
                        self.reply()
                    admit.assert_not_called()

    def test_denied_native_identity_stays_unknown_instead_of_cancelled(self):
        with (
            patch.object(processes, "process_table", side_effect=processes.ProcessError("denied")),
            patch.object(handoff, "admit_session") as admit,
        ):
            with self.assertRaisesRegex(processes.ProcessError, "denied"):
                self.reply()
            admit.assert_not_called()

    def test_an_uncertain_receipt_does_not_authorize_a_handoff(self):
        self.value.update(phase="uncertain", cleanup_error="native inspection denied")
        self.save()
        with patch.object(handoff, "admit_session") as admit:
            with self.assertRaisesRegex(supervision.SupervisionError, "no longer armed"):
                self.reply()
            admit.assert_not_called()

    def test_unbound_or_malformed_notice_cannot_grant_even_after_cleanup(self):
        self.value.update(phase="stopped", cause="provider_stopped")
        self.save()
        original = self.notice
        for change in ({"guard": "b" * 32}, {"request": True}, {"request": -1}, {"session_admission": {}}):
            with self.subTest(change=change), patch.object(handoff, "admit_session") as admit:
                self.notice = {**original, **change}
                with self.assertRaises(supervision.SupervisionError):
                    self.reply()
                admit.assert_not_called()

    def test_no_outer_guard_and_legacy_callbacks_preserve_initial_admission(self):
        self.assertTrue(handoff.admit(self.metadata, 15))
        self.assertTrue(handoff.admit_session(self.metadata, self.record, 15))
        with handoff.enclosing_guard(lambda _notice, _deadline: None):
            self.assertTrue(handoff.admit(self.metadata, 15))
        with handoff.enclosing_guard(lambda _notice, _deadline: False):
            self.assertFalse(handoff.admit_session(self.metadata, self.record, 15))

    def test_enclosing_cancel_ack_requires_the_bound_creator_and_current_receipt(self):
        """Exercise the public guard with an offline keeper and no threads or waits."""
        for cancelled, valid_receipt, expected in ((502, True, False), (999, True, None), (502, False, None)):
            with self.subTest(cancelled=cancelled, valid_receipt=valid_receipt):
                state = {"phase": "armed", "cleanup_error": None}
                keeper = SimpleNamespace(pid=600, returncode=None, poll=lambda: None)

                def wait(timeout=None):
                    keeper.returncode = 0
                    state["phase"] = "discharged"

                keeper.wait = wait
                lines = [{"armed": "c" * 32}, {"session_cancelled": "a" * 32, "creator": cancelled}]

                def current(_metadata):
                    return state if valid_receipt or state["phase"] == "discharged" else None

                with (
                    patch.object(supervision.subprocess, "Popen", return_value=keeper),
                    patch.object(supervision, "_identity", side_effect=lambda pid: {"pid": pid, "birth_identity": 1}),
                    patch.object(supervision.processes, "live_processes", return_value=[{}]),
                    patch.object(supervision, "_send"),
                    patch.object(supervision, "_read_line", side_effect=lines),
                    patch.object(supervision, "receipt", side_effect=current),
                    patch.object(supervision.select, "select", return_value=([], [], [])),
                    patch.object(supervision.threading, "Thread", return_value=Mock()),
                ):
                    # The invalid-current control reaches transition validation,
                    # after initial guard admission has already been authenticated.
                    if not valid_receipt:
                        calls = [0]

                        def invalid_transition(_metadata):
                            calls[0] += 1
                            return state if calls[0] == 1 or state["phase"] == "discharged" else None

                        current_patch = patch.object(supervision, "receipt", side_effect=invalid_transition)
                    else:
                        current_patch = patch.object(supervision, "receipt", side_effect=current)
                    with current_patch:
                        with supervision.protect_owner(
                            -1,
                            receipt_path=Path(self.directory.name) / f"outer-{cancelled}-{valid_receipt}.json",
                            timeout=None,
                            owner_identity={"pid": os.getpid(), "birth_identity": 1},
                            nonce="c" * 32,
                        ):
                            if expected is False:
                                self.assertFalse(handoff.admit_session(self.metadata, self.record, 15))
                            else:
                                with self.assertRaises(supervision.SupervisionError):
                                    handoff.admit_session(self.metadata, self.record, 15)

    def test_callback_eof_stops_polling_the_closed_socket_without_waiting(self):
        self.watch_transport(failure=None, clean=True)

    def test_callback_transport_failure_remains_unknown_until_cleanup(self):
        self.watch_transport(failure=OSError("callback unavailable"), clean=False)

    def test_callback_transport_close_after_clean_publication_preserves_success(self):
        self.watch_transport(failure=BrokenPipeError("keeper finished"), clean=True)

    def watch_transport(self, *, failure, clean):
        """Drive the public launch with an offline transport and a fake event."""

        class Event:
            def __init__(self):
                self.flag = False
                self.waits = []

            def set(self):
                self.flag = True

            def is_set(self):
                return self.flag

            def wait(self, duration):
                self.waits.append(duration)
                return self.flag

        event = Event()
        watchers = []
        state = {"phase": "armed", "cause": None, "cleanup_error": None, "session_boundary": "linux-setsid-notify-v1"}
        polls = []
        keeper = SimpleNamespace(pid=600, returncode=None, poll=lambda: polls.pop(0) if polls else keeper.returncode)
        child = SimpleNamespace(pid=501, returncode=None, poll=lambda: child.returncode)

        def finished(timeout=None):
            keeper.returncode = 0
            state.update(phase="stopped", cause="provider_stopped")

        keeper.wait = finished
        keeper.kill = Mock()
        child.wait = Mock()
        child.kill = Mock()

        def thread(*, target, **_options):
            watchers.append(target)
            return Mock()

        socketpair = supervision.socket.socketpair
        with (
            patch.object(supervision.boundary, "require_supported"),
            patch.object(supervision.subprocess, "Popen", side_effect=(keeper, child)),
            patch.object(supervision, "_identity", side_effect=lambda pid: {"pid": pid, "birth_identity": 1}),
            patch.object(supervision.processes, "live_processes", return_value=[{}]),
            patch.object(supervision.processes, "ProcessTree") as fallback,
            patch.object(supervision, "_send"),
            patch.object(supervision.os, "write", return_value=1),
            patch.object(supervision, "_read_line", return_value={"armed": "a" * 32}),
            patch.object(supervision, "receipt", side_effect=lambda _metadata: state),
            patch.object(supervision.uuid, "uuid4", return_value=SimpleNamespace(hex="a" * 32)),
            patch.object(supervision.threading, "Event", return_value=event),
            patch.object(supervision.threading, "Thread", side_effect=thread),
            # This transport control does not install a Linux boundary. A
            # portable stream pair supplies the same closed-peer EOF here.
            patch.object(supervision.socket, "socketpair", side_effect=lambda *_args: socketpair()),
        ):
            context = supervision.launch(
                ["offline-provider"], receipt_path=Path(self.directory.name) / "launch.json", start_new_session=True
            )

            def exercise():
                with context:
                    if clean:
                        state.update(phase="stopped", cause="provider_stopped")
                    if failure is None:
                        polls.extend((None, 0))
                        with patch.object(supervision.select, "select", return_value=([object()], [], [])) as select:
                            watchers[0]()  # The real callback peer has closed: recv returns EOF.
                        self.assertEqual(1, select.call_count)
                        self.assertEqual([0.05], event.waits)
                    else:
                        with patch.object(supervision.select, "select", side_effect=failure):
                            watchers[0]()
                    child.returncode = 0

            if failure is not None and not clean:
                with self.assertRaisesRegex(supervision.SupervisionError, "Session ownership handoff failed: OSError"):
                    exercise()
                fallback.return_value.stop.assert_called_once()
            else:
                exercise()
                fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
