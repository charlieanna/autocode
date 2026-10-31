"""Focused offline regressions for cancelled kernel session handoffs."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from contextlib import ExitStack, contextmanager
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

    def test_initial_missing_open_retries_then_checks_the_matching_receipt(self):
        with (
            Path(self.metadata["receipt"]).open("rb") as stream,
            patch.object(Path, "open", side_effect=[FileNotFoundError("synthetic"), stream]) as opened,
            patch.object(supervision.time, "sleep") as sleep,
        ):
            self.assertEqual(self.value, supervision.receipt(self.metadata))
        self.assertEqual(2, opened.call_count)
        sleep.assert_called_once_with(0.025)

    def test_single_read_keeps_the_parse_diagnostic_without_retrying(self):
        Path(self.metadata["receipt"]).write_text("{")
        with patch.object(supervision.time, "sleep") as sleep:
            self.assertEqual((None, ("parse", "json_invalid")), supervision._read_receipt_once(self.metadata))
        sleep.assert_not_called()

    def test_parse_visibility_retry_reopens_and_authenticates_fresh_bytes(self):
        target = Path(self.metadata["receipt"])
        target.write_text("{")
        original_open = Path.open
        with (
            patch.object(Path, "open", autospec=True, side_effect=original_open) as opened,
            patch.object(supervision.time, "sleep", side_effect=lambda _: self.save()) as sleep,
            patch.object(processes, "process_table", return_value={502: self.creator}),
            patch.object(handoff, "admit_session", return_value=True) as admit,
        ):
            self.assertEqual({"session_admitted": 123, "guard": "a" * 32}, self.reply())
        self.assertEqual(2, sum(call.args[1:] == ("rb",) for call in opened.call_args_list))
        sleep.assert_called_once_with(0.025)
        admit.assert_called_once_with(self.metadata, self.record, 15)

    def test_parse_retry_never_uses_a_foreign_replacement_or_old_valid_value(self):
        for key in ("nonce", "owner", "keeper", "provider", "schema"):
            with self.subTest(key=key):
                Path(self.metadata["receipt"]).write_text("{")
                foreign = {**self.value, key: "foreign"}

                def publish_foreign(_):
                    Path(self.metadata["receipt"]).write_text(json.dumps(foreign))

                with (
                    patch.object(supervision.time, "sleep", side_effect=publish_foreign) as sleep,
                    patch.object(handoff, "admit_session") as admit,
                ):
                    with self.assertRaisesRegex(supervision.SupervisionError, "receipt is unavailable"):
                        self.reply()
                sleep.assert_called_once_with(0.025)
                admit.assert_not_called()

    def test_persistent_parse_failure_is_bounded_and_cannot_grant(self):
        Path(self.metadata["receipt"]).write_text("{")
        original_open = Path.open
        with (
            patch.object(Path, "open", autospec=True, side_effect=original_open) as opened,
            patch.object(supervision.time, "sleep") as sleep,
            patch.object(handoff, "admit_session") as admit,
        ):
            with self.assertRaises(supervision.SupervisionError) as caught:
                self.reply()
        self.assertEqual(5, opened.call_count)
        self.assertEqual([((0.025,), {})] * 4, sleep.call_args_list)
        admit.assert_not_called()
        self.assertIn(
            "receipt_reason=json_invalid", supervision._session_handoff_failure(caught.exception, "check_ownership")
        )

    def test_initial_open_retry_preserves_the_enclosing_admission_decision(self):
        for admitted in (False, True):
            with (
                self.subTest(admitted=admitted),
                Path(self.metadata["receipt"]).open("rb") as stream,
                patch.object(Path, "open", side_effect=[FileNotFoundError("synthetic"), stream]) as opened,
                patch.object(supervision.time, "sleep") as sleep,
                patch.object(processes, "process_table", return_value={502: self.creator}),
                patch.object(handoff, "admit_session", return_value=admitted) as admit,
            ):
                key = "session_admitted" if admitted else "session_cancelled"
                self.assertEqual({key: 123, "guard": "a" * 32}, self.reply())
            self.assertEqual(2, opened.call_count)
            sleep.assert_called_once_with(0.025)
            admit.assert_called_once_with(self.metadata, self.record, 15)

    def test_persistent_missing_open_stays_bounded_and_cannot_grant_admission(self):
        with (
            patch.object(Path, "open", side_effect=FileNotFoundError("synthetic")) as opened,
            patch.object(supervision.time, "sleep") as sleep,
            patch.object(handoff, "admit_session") as admit,
        ):
            with self.assertRaisesRegex(supervision.SupervisionError, "receipt is unavailable") as caught:
                self.reply()
        self.assertEqual(5, opened.call_count)
        self.assertEqual([((0.025,), {})] * 4, sleep.call_args_list)
        admit.assert_not_called()
        message = supervision._session_handoff_failure(caught.exception, "check_ownership")
        self.assertIn("receipt_phase=open", message)
        self.assertIn("receipt_reason=not_found", message)

    def test_initial_open_retry_never_bypasses_content_or_access_validation(self):
        cases = [
            ("wrong_nonce", {**self.value, "nonce": "foreign"}, "nonce_mismatch"),
            ("wrong_owner", {**self.value, "owner": {}}, "owner_mismatch"),
            ("wrong_keeper", {**self.value, "keeper": {}}, "keeper_mismatch"),
            ("wrong_provider", {**self.value, "provider": {}}, "provider_mismatch"),
            ("wrong_schema", {**self.value, "schema": 2}, "schema_invalid"),
        ]
        for name, value, reason in cases:
            with self.subTest(case=name):
                Path(self.metadata["receipt"]).write_text(value if isinstance(value, str) else json.dumps(value))
                with (
                    Path(self.metadata["receipt"]).open("rb") as stream,
                    patch.object(Path, "open", side_effect=[FileNotFoundError("synthetic"), stream]) as opened,
                    patch.object(supervision.time, "sleep") as sleep,
                    patch.object(handoff, "admit_session") as admit,
                ):
                    with self.assertRaises(supervision.SupervisionError) as caught:
                        self.reply()
                self.assertEqual(2, opened.call_count)
                sleep.assert_called_once_with(0.025)
                admit.assert_not_called()
                self.assertIn(
                    "receipt_reason=" + reason,
                    supervision._session_handoff_failure(caught.exception, "check_ownership"),
                )
        with (
            patch.object(Path, "open", side_effect=PermissionError("synthetic")) as opened,
            patch.object(supervision.time, "sleep") as sleep,
        ):
            self.assertEqual((None, ("open", "access_denied")), supervision._read_receipt(self.metadata))
        opened.assert_called_once_with("rb")
        sleep.assert_not_called()

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
                with patch.object(handoff, "admit_session") as admit, patch.object(supervision.time, "sleep"):
                    with self.assertRaisesRegex(supervision.SupervisionError, "receipt is unavailable"):
                        self.reply()
                    admit.assert_not_called()

    def test_receipt_failures_preserve_none_and_name_only_the_failed_read_branch(self):
        secret = "synthetic-receipt-private-value"
        original_metadata, original_value = copy.deepcopy(self.metadata), copy.deepcopy(self.value)
        path = Path(self.metadata["receipt"])
        original_open = Path.open

        def faulted_read(error):
            def open_stream(path, *args, **kwargs):
                stream = original_open(path, *args, **kwargs)
                stack.enter_context(patch.object(stream, "read", side_effect=error))
                return stream

            stack.enter_context(patch.object(Path, "open", open_stream))

        cases = [
            ("missing_path_key", "metadata", "metadata_invalid", lambda: self.metadata.pop("receipt")),
            ("invalid_path_type", "metadata", "metadata_invalid", lambda: self.metadata.update(receipt=123)),
            ("invalid_path_value", "open", "value_invalid", lambda: self.metadata.update(receipt=secret + "\0")),
            ("missing_file", "open", "not_found", path.unlink),
            (
                "stat_denied",
                "stat",
                "access_denied",
                lambda: stack.enter_context(patch.object(supervision.os, "fstat", side_effect=PermissionError(secret))),
            ),
            (
                "stat_io",
                "stat",
                "io_error",
                lambda: stack.enter_context(patch.object(supervision.os, "fstat", side_effect=OSError(secret))),
            ),
            (
                "size_limit",
                "stat",
                "size_limit",
                lambda: stack.enter_context(
                    patch.object(
                        supervision.os,
                        "fstat",
                        return_value=SimpleNamespace(st_size=supervision.receipts.MAX_RECEIPT_BYTES + 1),
                    )
                ),
            ),
            (
                "read_missing",
                "read",
                "not_found",
                lambda: faulted_read(FileNotFoundError(secret)),
            ),
            (
                "read_denied",
                "read",
                "access_denied",
                lambda: faulted_read(PermissionError(secret)),
            ),
            (
                "read_io",
                "read",
                "io_error",
                lambda: faulted_read(OSError(secret)),
            ),
            (
                "read_type",
                "read",
                "value_invalid",
                lambda: faulted_read(TypeError(secret)),
            ),
            ("text_decode", "read", "text_decode_invalid", lambda: path.write_bytes(b"\xff")),
            ("json", "parse", "json_invalid", lambda: path.write_text("{" + secret)),
            ("object", "validate", "object_invalid", lambda: path.write_text("[]")),
            ("schema", "validate", "schema_invalid", lambda: path.write_text(json.dumps({**self.value, "schema": 2}))),
        ]
        cases += [
            (
                key,
                "validate",
                key + "_mismatch",
                lambda key=key: path.write_text(json.dumps({**self.value, key: secret})),
            )
            for key in ("nonce", "owner", "keeper", "provider")
        ]
        for name, phase, reason, prepare in cases:
            with self.subTest(branch=name), ExitStack() as stack, patch.object(supervision.time, "sleep"):
                self.metadata, self.value = copy.deepcopy(original_metadata), copy.deepcopy(original_value)
                self.save()
                prepare()
                self.assertIsNone(supervision.receipt(self.metadata))
                with patch.object(handoff, "admit_session") as admit:
                    with self.assertRaisesRegex(supervision.SupervisionError, "receipt is unavailable") as caught:
                        self.reply()
                    admit.assert_not_called()
                message = supervision._session_handoff_failure(caught.exception, "check_ownership")
                self.assertIn("reason=receipt_unavailable", message)
                self.assertIn("receipt_phase=" + phase, message)
                self.assertIn("receipt_reason=" + reason, message)
                self.assertNotIn(secret, message)
                self.assertNotIn(str(path), message)
                self.assertLess(len(message), 384)

    def test_receipt_diagnostic_describes_the_same_failed_read_without_retry(self):
        path = Path(self.metadata["receipt"])
        with (
            path.open("rb") as stream,
            patch.object(Path, "open", return_value=stream) as opened,
            patch.object(
                stream, "read", side_effect=[OSError("synthetic-private-error"), json.dumps(self.value).encode()]
            ) as read,
        ):
            with self.assertRaises(supervision.SupervisionError) as caught:
                self.reply()
            message = supervision._session_handoff_failure(caught.exception, "check_ownership")
        self.assertEqual(1, opened.call_count)
        self.assertEqual(1, read.call_count)
        self.assertIn("receipt_phase=read", message)
        self.assertIn("receipt_reason=io_error", message)
        self.assertNotIn("synthetic-private-error", message)

    def test_receipt_diagnostics_do_not_tighten_legacy_schema_or_exception_behavior(self):
        for schema in (1, 1.0, True):
            with self.subTest(schema=schema):
                self.value["schema"] = schema
                self.save()
                self.assertEqual(self.value, supervision.receipt(self.metadata))
        # The original reader does not catch arbitrary exceptions from a decoder.
        with patch.object(supervision.json, "loads", side_effect=RecursionError("synthetic-private-error")):
            with self.assertRaises(RecursionError):
                supervision.receipt(self.metadata)

    def test_receipt_diagnostic_formatter_rejects_untrusted_details(self):
        class UnhashableText(str):
            __hash__ = None

        class Uninspectable:
            def __str__(self):
                raise AssertionError("Untrusted diagnostic detail was inspected")

        secret = "synthetic-receipt-private-value"
        details = [
            ("read", secret),
            (secret, "io_error"),
            (UnhashableText("read"), "io_error"),
            ["read", "io_error"],
            Uninspectable(),
            ("read", "io_error", secret),
        ]
        for index, detail in enumerate(details):
            with self.subTest(index=index):
                error = supervision.SupervisionError("Session ownership receipt is unavailable")
                error.__dict__["_receipt_diagnostic"] = detail
                message = supervision._session_handoff_failure(error, "check_ownership")
                self.assertNotIn("receipt_phase=", message)
                self.assertNotIn("receipt_reason=", message)
                self.assertNotIn(secret, message)

    def test_public_launch_names_missing_receipt_and_keeps_fail_closed_cleanup(self):
        message = self.watch_transport(failure=None, clean=False, ownership_failure=True, receipt_missing=True)
        self.assertIn("phase=check_ownership", message)
        self.assertIn("reason=receipt_unavailable", message)
        self.assertIn("receipt_phase=open", message)
        self.assertIn("receipt_reason=not_found", message)

    @contextmanager
    def mutate_after_size_check(self, target, mutate, *, enabled=lambda: True):
        """One real publication mutation at the old path stat or new fd stat."""
        path_stat, fd_stat = Path.stat, os.fstat
        fired = []

        def mutate_once(info):
            if enabled() and not fired:
                fired.append(True)
                mutate(target)
            return info

        def inspect_path(path, *args, **kwargs):
            info = path_stat(path, *args, **kwargs)
            return mutate_once(info) if path == target else info

        def inspect_fd(fd):
            info = fd_stat(fd)
            try:
                current = path_stat(target)
            except FileNotFoundError:
                return info
            return mutate_once(info) if (info.st_dev, info.st_ino) == (current.st_dev, current.st_ino) else info

        with patch.object(Path, "stat", inspect_path), patch.object(os, "fstat", inspect_fd):
            yield fired
        self.assertEqual([True], fired, "The same race must actually run in either reader")

    @unittest.skipUnless(os.name == "posix", "replacing an opened receipt requires POSIX publication semantics")
    def test_opened_receipt_survives_path_removal_without_retry(self):
        target = Path(self.metadata["receipt"])
        with self.mutate_after_size_check(target, lambda path: path.unlink()):
            self.assertEqual(self.value, supervision.receipt(self.metadata))
        self.assertFalse(target.exists())

    @unittest.skipUnless(os.name == "posix", "replacing an opened receipt requires POSIX publication semantics")
    def test_path_replacement_never_mixes_another_inode_with_the_checked_receipt(self):
        target = Path(self.metadata["receipt"])
        replacement = target.with_name("replacement.json")
        replacement.write_text(json.dumps({**self.value, "nonce": "foreign"}))
        with self.mutate_after_size_check(target, lambda path: replacement.replace(path)):
            self.assertEqual(self.value, supervision.receipt(self.metadata))
        # Foreign bytes opened on the next independent check still fail closed.
        self.assertIsNone(supervision.receipt(self.metadata))

    def test_growth_after_size_check_is_bounded_and_cannot_contribute_receipt_bytes(self):
        target = Path(self.metadata["receipt"])
        data = target.read_bytes() + b" " * (supervision.receipts.MAX_RECEIPT_BYTES + 1)
        with self.mutate_after_size_check(target, lambda path: path.write_bytes(data)):
            value, diagnostic = supervision._read_receipt(self.metadata)
            self.assertIsNone(value)
            self.assertEqual(("read", "size_limit"), diagnostic)

    @unittest.skipUnless(sys.platform == "linux", "real supervised session handoffs require Linux")
    def test_public_launch_recovers_one_malformed_read_during_session_handoff(self):
        target = Path(self.directory.name) / "real-parse-handoff.json"
        malformed = target.with_name("malformed.json")
        malformed.write_text("{")
        local = threading.local()
        original_reply, original_open = supervision.session_handoff_reply, Path.open
        injected = []

        def reply(*args):
            local.active = True
            try:
                return original_reply(*args)
            finally:
                local.active = False

        def open_receipt(path, *args, **kwargs):
            if path == target and getattr(local, "active", False) and not injected:
                injected.append(True)
                return original_open(malformed, *args, **kwargs)
            return original_open(path, *args, **kwargs)

        with (
            patch.object(supervision, "session_handoff_reply", side_effect=reply),
            patch.object(Path, "open", open_receipt),
        ):
            with supervision.launch(
                [
                    sys.executable,
                    "-c",
                    "import subprocess,sys; subprocess.run([sys.executable,'-c','pass'],start_new_session=True,check=True); print('handoff-done')",
                ],
                receipt_path=target,
                timeout=20,
                start_new_session=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            ) as child:
                output, stderr = child.communicate(timeout=15)
                self.assertEqual(0, child.returncode, stderr)
        self.assertEqual([True], injected)
        self.assertEqual("handoff-done\n", output)
        final = supervision.receipt(child.supervision)
        self.assertEqual("stopped", final["phase"])
        self.assertIsNone(final["cleanup_error"])
        self.assertFalse(processes.live_processes([child.supervision[key] for key in ("keeper", "provider")]))

    @unittest.skipUnless(sys.platform == "linux", "real supervised session handoffs require Linux")
    def test_public_launch_keeps_handoff_when_the_opened_receipt_path_is_removed(self):
        target = Path(self.directory.name) / "real-handoff.json"
        local = threading.local()
        original = supervision.session_handoff_reply

        def reply(*args):
            local.active = True
            try:
                return original(*args)
            finally:
                local.active = False

        error = None
        output = ""
        with (
            self.mutate_after_size_check(
                target, lambda path: path.unlink(), enabled=lambda: getattr(local, "active", False)
            ),
            patch.object(supervision, "session_handoff_reply", side_effect=reply),
        ):
            try:
                with supervision.launch(
                    [
                        sys.executable,
                        "-c",
                        "import subprocess,sys; subprocess.run([sys.executable,'-c','pass'],start_new_session=True,check=True); print('handoff-done')",
                    ],
                    receipt_path=target,
                    timeout=20,
                    start_new_session=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                ) as child:
                    output, stderr = child.communicate(timeout=15)
                    self.assertEqual(0, child.returncode, stderr)
            except supervision.SupervisionError as stopped:
                error = str(stopped)
        self.assertIsNone(error)
        self.assertEqual("handoff-done\n", output)
        final = supervision.receipt(child.supervision)
        self.assertEqual("stopped", final["phase"])
        self.assertIsNone(final["cleanup_error"])
        self.assertFalse(processes.live_processes([child.supervision[key] for key in ("keeper", "provider")]))

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

    def test_callback_reset_after_verified_deadline_preserves_timeout(self):
        self.watch_transport(
            failure=ConnectionResetError("keeper finished"),
            clean=True,
            terminal_cause="stage_deadline",
            callback_read_failure=True,
        )

    def test_callback_reset_while_armed_is_not_excused_by_later_deadline(self):
        self.watch_transport(
            failure=ConnectionResetError("ownership unknown"),
            clean=False,
            terminal_cause="stage_deadline",
            callback_read_failure=True,
        )

    def test_deadline_callback_reset_with_cleanup_error_remains_blocked(self):
        self.watch_transport(
            failure=ConnectionResetError("cleanup unknown"),
            clean=True,
            terminal_cause="stage_deadline",
            terminal_cleanup_error="ProcessError: cleanup unverified",
            callback_read_failure=True,
        )

    def test_callback_failure_retains_a_safe_specific_ownership_reason(self):
        cases = (
            ("Session ownership receipt is unavailable", "receipt_unavailable"),
            ("Session ownership is no longer armed", "receipt_not_armed"),
            ("Enclosing keeper did not retain session ownership", "enclosing_ownership_unverified"),
        )
        for detail, reason in cases:
            with self.subTest(reason=reason):
                message = self.watch_transport(
                    failure=supervision.SupervisionError(detail), clean=False, ownership_failure=True
                )
                self.assertIn(f"reason={reason}", message)
                self.assertIn("phase=check_ownership", message)

    def test_callback_failure_omits_unclassified_exception_payload(self):
        secret = "synthetic-control-token-value"
        error = supervision.SupervisionError("AUTOCODE_APPROVE_GOAL_TOKEN=" + secret + "x" * 4096)
        message = self.watch_transport(failure=error, clean=False, ownership_failure=True)
        self.assertIn("reason=unclassified", message)
        self.assertNotIn(secret, message)
        self.assertNotIn("AUTOCODE_APPROVE_GOAL_TOKEN", message)
        self.assertLess(len(message), 384)

    def test_callback_failure_does_not_expose_dynamic_names_or_inspect_unknown_payloads(self):
        secret = "synthetic_private_class_name"

        def inaccessible(_error):
            raise AssertionError("Untrusted exception payload must not be inspected")

        error_type = type(secret, (Exception,), {"args": property(inaccessible)})
        message = self.watch_transport(
            failure=error_type(), clean=False, ownership_failure=True, expected_type="Exception"
        )
        self.assertIn("reason=unclassified", message)
        self.assertNotIn(secret, message)

    def test_callback_failure_does_not_hash_a_string_subclass(self):
        class UnhashableText(str):
            __hash__ = None

        message = self.watch_transport(
            failure=supervision.SupervisionError(UnhashableText("untrusted payload")),
            clean=False,
            ownership_failure=True,
        )
        self.assertIn("reason=unclassified", message)
        self.assertNotIn("untrusted payload", message)

    def watch_transport(
        self,
        *,
        failure,
        clean,
        ownership_failure=False,
        expected_type=None,
        receipt_missing=False,
        terminal_cause="provider_stopped",
        callback_read_failure=False,
        terminal_cleanup_error=None,
    ):
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
        state = {
            "phase": "armed",
            "cause": None,
            "cleanup_error": terminal_cleanup_error,
            "session_boundary": "linux-setsid-notify-v1",
        }
        polls = []
        keeper = SimpleNamespace(pid=600, returncode=None, poll=lambda: polls.pop(0) if polls else keeper.returncode)
        child = SimpleNamespace(pid=501, returncode=None, poll=lambda: child.returncode)

        def finished(timeout=None):
            keeper.returncode = 0
            state.update(phase="stopped", cause=terminal_cause)

        keeper.wait = finished
        keeper.kill = Mock()
        child.wait = Mock()
        child.kill = Mock()

        def thread(*, target, **_options):
            watchers.append(target)
            return Mock()

        socketpair = supervision.socket.socketpair
        pair_count = 0
        receipt_probe = [False]
        read_receipt = supervision.receipt

        def current(metadata):
            return read_receipt(metadata) if receipt_missing and receipt_probe[0] else state

        def callback_pair(*_args):
            nonlocal pair_count
            parent, child_socket = socketpair()
            pair_count += 1
            if ownership_failure and pair_count == 2:
                child_socket.send(json.dumps(self.notice).encode() if receipt_missing else b"offline-notice")
            if callback_read_failure and pair_count == 2:
                parent = Mock(wraps=parent)
                parent.recv.side_effect = failure
            return parent, child_socket

        with (
            patch.object(supervision.boundary, "require_supported"),
            patch.object(supervision.subprocess, "Popen", side_effect=(keeper, child)),
            patch.object(supervision, "_identity", side_effect=lambda pid: {"pid": pid, "birth_identity": 1}),
            patch.object(supervision.processes, "live_processes", return_value=[{}]),
            patch.object(supervision.processes, "ProcessTree") as fallback,
            patch.object(supervision, "_send") as sent,
            patch.object(supervision.os, "write", return_value=1),
            patch.object(supervision, "_read_line", return_value={"armed": "a" * 32}),
            patch.object(supervision, "receipt", side_effect=current),
            patch.object(supervision.uuid, "uuid4", return_value=SimpleNamespace(hex="a" * 32)),
            patch.object(supervision.threading, "Event", return_value=event),
            patch.object(supervision.threading, "Thread", side_effect=thread),
            patch.object(supervision.time, "sleep"),
            # This transport control does not install a Linux boundary. A
            # portable stream pair supplies the same closed-peer EOF here.
            patch.object(supervision.socket, "socketpair", side_effect=callback_pair),
        ):
            context = supervision.launch(
                ["offline-provider"], receipt_path=Path(self.directory.name) / "launch.json", start_new_session=True
            )

            def exercise():
                with context:
                    control_fd = sent.call_args.args[0]
                    if clean:
                        state.update(phase="stopped", cause=terminal_cause)
                    if failure is None and not receipt_missing:
                        polls.extend((None, 0))
                        with patch.object(supervision.select, "select", return_value=([object()], [], [])) as select:
                            watchers[0]()  # The real callback peer has closed: recv returns EOF.
                        self.assertEqual(1, select.call_count)
                        self.assertEqual([0.05], event.waits)
                    elif callback_read_failure:
                        with patch.object(supervision.select, "select", return_value=([object()], [], [])):
                            watchers[0]()
                    elif ownership_failure:
                        with (
                            patch.object(supervision.select, "select", return_value=([object()], [], [])),
                            patch.object(
                                supervision,
                                "session_handoff_reply",
                                **(
                                    {"wraps": supervision.session_handoff_reply}
                                    if receipt_missing
                                    else {"side_effect": failure}
                                ),
                            ) as checked,
                        ):
                            receipt_probe[0] = receipt_missing
                            try:
                                watchers[0]()
                            finally:
                                receipt_probe[0] = False
                        checked.assert_called_once()
                    else:
                        with patch.object(supervision.select, "select", side_effect=failure):
                            watchers[0]()
                    if (failure is not None or receipt_missing) and (not clean or terminal_cleanup_error):
                        with self.assertRaises(OSError):
                            os.fstat(control_fd)
                    child.returncode = 0

            if (failure is not None or receipt_missing) and (not clean or terminal_cleanup_error):
                name = expected_type or ("SupervisionError" if receipt_missing else type(failure).__name__)
                expected = f"Session ownership handoff failed: {name}"
                with self.assertRaisesRegex(supervision.SupervisionError, expected) as caught:
                    exercise()
                fallback.return_value.stop.assert_called_once()
                return str(caught.exception)
            else:
                if terminal_cause == "stage_deadline":
                    with self.assertRaises(supervision.VerifiedStop) as caught:
                        exercise()
                    self.assertEqual("stage_deadline", caught.exception.cause)
                else:
                    exercise()
                fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
