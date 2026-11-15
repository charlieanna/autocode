"""Private ancestry protocol tests: descriptors and packets, no target launches."""

import array
import errno
import json
import os
import socket
import stat
import struct
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import autocode_capture_family as family
import autocode_capture_reaper as reaper
import autocode_captured_process as captured

ATTEMPT, ANCESTOR, REQUEST = "a" * 32, "b" * 32, "c" * 32
BOOT = "00000000-0000-4000-8000-000000000001"


@unittest.skipUnless(os.name == "posix", "private Unix capture protocol")
class CaptureFamilyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = {key: value for key, value in os.environ.items() if key != family.ENVIRONMENT_KEY}
        context = patch.dict(os.environ, environment, clear=True)
        context.start()
        self.addCleanup(context.stop)

    def packet(self, **changes):
        return {
            "schema": 1,
            "family_clean": True,
            "attempt_nonce": ATTEMPT,
            "ancestor_nonce": ANCESTOR,
            "boot_id": BOOT,
            **changes,
        }

    def backup(self):
        path = self.root / f"family-result-{ATTEMPT}-{ANCESTOR}.json"
        path.touch(mode=0o600)
        return path

    def test_absent_context_is_inert_without_creating_backup_files(self):
        self.assertEqual([], family.channels())
        registration = family.register_backup(self.root, ATTEMPT, None, boot_id=BOOT)
        registration.release()
        self.assertEqual([], list(self.root.iterdir()))
        self.assertFalse(family.backup_clean(self.root, ATTEMPT, BOOT))

    def test_malformed_or_non_socket_advertised_context_is_refused(self):
        path = self.backup()
        for value in (
            [],
            {},
            [{"endpoint": False, "nonce": ANCESTOR}],
            [{"endpoint": "relative", "nonce": ANCESTOR}],
            [{"endpoint": str(path), "nonce": ANCESTOR}],
            [{"endpoint": str(self.root / "missing"), "nonce": ANCESTOR}],
            [{"endpoint": str(path), "nonce": "invalid"}],
            [{"endpoint": str(path), "nonce": ANCESTOR, "extra": True}],
        ):
            with self.subTest(value=value), patch.dict(os.environ, {family.ENVIRONMENT_KEY: json.dumps(value)}):
                with self.assertRaises(family.FamilyError):
                    family.channels()

    def test_launch_validates_ancestors_without_opening_fds_or_poll_records(self):
        path = self.root / "control"
        inherited = [{"endpoint": str(path), "nonce": ANCESTOR}]
        before = set(self.root.iterdir())

        def attributes(current):
            return SimpleNamespace(
                st_uid=os.geteuid(), st_mode=(stat.S_IFSOCK | 0o600) if current == path else (stat.S_IFDIR | 0o700)
            )

        with (
            patch.dict(os.environ, {family.ENVIRONMENT_KEY: json.dumps(inherited)}),
            patch.object(Path, "lstat", autospec=True, side_effect=attributes),
        ):
            launch = family.Launch()
            self.assertEqual(inherited, launch.packet()["ancestors"])
            self.assertNotIn('"fd":', json.dumps(launch.packet()))
        self.assertEqual(before, set(self.root.iterdir()))

    def test_partial_unarmed_enrolment_rolls_back_only_acknowledged_ancestors(self):
        channels = [{"endpoint": "/unused-one", "nonce": ANCESTOR}, {"endpoint": "/unused-two", "nonce": "d" * 32}]
        released, visible = [], []

        def acknowledge(channel, message, descriptors, deadline):
            visible.append(os.fstat(descriptors[0]).st_size)
            self.assertFalse((self.root / "pending.json").exists())
            if len(visible) == 2:
                raise family.FamilyError("second ancestor refused")

        with (
            patch.object(family, "channels", return_value=channels),
            patch.object(family, "_rpc", side_effect=acknowledge),
            patch.object(
                family.Registration,
                "release",
                autospec=True,
                side_effect=lambda registration: released.extend(registration.enrolled),
            ),
        ):
            with self.assertRaises(family.FamilyError):
                family.register_backup(self.root, ATTEMPT, 42, boot_id=BOOT)
        self.assertEqual([0, 0], visible)
        self.assertEqual(channels[:1], released)
        self.assertFalse((self.root / "pending.json").exists())

    def test_unbounded_target_still_has_bounded_ancestor_registration(self):
        channel = {"endpoint": "/private/control", "nonce": ANCESTOR}
        with (
            patch.object(family, "channels", return_value=[channel]),
            patch.object(family.time, "monotonic", return_value=10),
            patch.object(family, "_rpc") as enroll,
        ):
            family.register_backup(self.root, ATTEMPT, None, boot_id=BOOT)
        self.assertEqual(10 + family.CLEANUP_ALLOWANCE, enroll.call_args.args[-1])

    def test_client_rejects_foreign_peer_before_sending_backup_descriptor(self):
        connection = Mock()
        connection.getsockopt.return_value = struct.pack("3i", 999, os.geteuid(), os.getgid())
        with (
            patch.object(family, "_socket", return_value=connection),
            patch.object(family, "_within", return_value=False),
            patch.object(socket, "SO_PEERCRED", getattr(socket, "SO_PEERCRED", 17), create=True),
        ):
            with self.assertRaises(family.FamilyError):
                family._connect({"endpoint": "/private/control"}, time.monotonic() + 1)
        connection.close.assert_called_once()
        connection.sendmsg.assert_not_called()

    def test_rpc_requires_exact_correlated_boolean_acknowledgement(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection

        def answer(_):
            request = json.loads(connection.sendmsg.call_args.args[0][0])["request"]
            return json.dumps({"request": request, "accepted": 1}).encode()

        connection.recv.side_effect = answer
        with patch.object(family, "_connect", return_value=connection), patch.object(family, "_wait"):
            with self.assertRaises(family.FamilyError):
                family._rpc({"nonce": ANCESTOR}, {"operation": "register"}, (55,), 42)

    def test_idle_connections_yield_control_and_do_not_starve_later_requests(self):
        server = family.Server.__new__(family.Server)
        server.socket = Mock()
        server.socket.accept.side_effect = BlockingIOError()
        idle = [Mock() for _ in range(family.SERVICE_BATCH + 1)]
        for connection in idle:
            connection.recvmsg.side_effect = BlockingIOError()
        server.connections = list(idle)
        with patch.object(socket, "MSG_CMSG_CLOEXEC", getattr(socket, "MSG_CMSG_CLOEXEC", 0), create=True):
            server.service()
            self.assertEqual(family.SERVICE_BATCH, sum(connection.recvmsg.call_count for connection in idle))
            self.assertEqual(0, idle[-1].recvmsg.call_count)
            server.service()
            self.assertEqual(1, idle[-1].recvmsg.call_count)

    def test_peer_reset_and_accept_exhaustion_cannot_preempt_owned_cleanup(self):
        server = family.Server.__new__(family.Server)
        server.socket = Mock()
        server.socket.accept.side_effect = OSError(errno.EMFILE, "fixture resource exhaustion")
        connection = Mock()
        connection.recvmsg.side_effect = ConnectionResetError()
        server.connections, server.registered = [connection], {}
        with (
            patch.object(socket, "MSG_CMSG_CLOEXEC", getattr(socket, "MSG_CMSG_CLOEXEC", 0), create=True),
            patch.object(reaper, "drain", return_value=(0, True)) as collected,
            patch.object(reaper, "signal_children") as signal_children,
        ):
            self.assertEqual(0, reaper.cleanup(123, 0, server))
        connection.close.assert_called_once()
        self.assertEqual([], server.connections)
        collected.assert_called_once_with(123, 0)
        signal_children.assert_not_called()

    def test_known_empty_setup_failure_keeps_command_failure_separate(self):
        with (
            patch.object(reaper, "establish") as adoption,
            patch.object(reaper.family, "Server", side_effect=OSError(errno.EPERM, "fixture bind denial")),
            patch.object(reaper, "drain", return_value=(None, True)) as absent,
            patch.object(reaper.subprocess, "Popen") as target,
        ):
            result = reaper.execute({}, 55)
        self.assertEqual({"started": False, "returncode": None, "expired": False, "setup_failed": True}, result)
        adoption.assert_called_once()
        absent.assert_called_once_with(None, None)
        target.assert_not_called()
        with (
            patch.object(reaper, "establish", side_effect=OSError(errno.EPERM, "adoption denied")),
            patch.object(reaper, "drain") as absent,
        ):
            with self.assertRaises(OSError):
                reaper.execute({}, 55)
        absent.assert_not_called()
        with (
            patch.object(reaper, "establish"),
            patch.object(reaper.family, "Server", side_effect=OSError()),
            patch.object(reaper, "drain", return_value=(None, False)),
        ):
            with self.assertRaises(RuntimeError):
                reaper.execute({}, 55)

    def test_invalid_setup_packet_cannot_hide_behind_parent_cancellation(self):
        helper = Mock(returncode=0)
        helper.wait.side_effect = [KeyboardInterrupt("original"), 0]

        def launch(_words, **options):
            request = json.loads(os.pread(options["pass_fds"][0], 65536, 0))
            os.write(request["ready"], json.dumps({"schema": 1, "nonce": request["nonce"], "ready": True}).encode())
            packet = {
                "schema": 1,
                "nonce": request["nonce"],
                "clean": True,
                "started": True,
                "returncode": 0,
                "expired": False,
                "setup_failed": True,
            }
            os.write(options["pass_fds"][1], json.dumps(packet).encode())
            return helper

        with (
            tempfile.TemporaryFile("w+t") as output,
            tempfile.TemporaryFile("w+t") as errors,
            patch.object(captured.subprocess, "Popen", side_effect=launch),
        ):
            with self.assertRaises(captured.ProcessError):
                captured._linux(["not-launched"], 1, None, None, output, errors, None)

    def test_unbounded_target_has_bounded_ready_and_failed_preparation_collection(self):
        with (
            patch.object(captured.time, "monotonic", side_effect=[10, 19]),
            patch.object(captured.select, "select") as wait,
        ):
            self.assertIsNone(captured._await_ready(Mock(), ATTEMPT, None))
        wait.assert_not_called()
        helper = Mock(returncode=0)

        def launch(_words, **options):
            request = json.loads(os.pread(options["pass_fds"][0], 65536, 0))
            packet = {
                "schema": 1,
                "nonce": request["nonce"],
                "clean": True,
                "started": False,
                "returncode": None,
                "expired": False,
            }
            os.write(options["pass_fds"][1], json.dumps(packet).encode())
            return helper

        with (
            tempfile.TemporaryFile("w+t") as output,
            tempfile.TemporaryFile("w+t") as errors,
            patch.object(captured, "_await_ready", return_value=None),
            patch.object(captured.subprocess, "Popen", side_effect=launch),
        ):
            with self.assertRaisesRegex(captured.ProcessError, "preparation"):
                captured._linux(["not-launched"], None, None, None, output, errors, None)
        self.assertLessEqual(helper.wait.call_args.kwargs["timeout"], captured.HELPER_RESERVE)

    def test_backup_can_be_reconciled_without_a_current_ancestor_context(self):
        path = self.backup()
        path.write_text(json.dumps(self.packet()))
        self.assertTrue(family.backup_clean(self.root, ATTEMPT, BOOT))
        self.assertNotIn(family.ENVIRONMENT_KEY, os.environ)

    def test_invalid_backup_packets_never_release_custody(self):
        path = self.backup()
        for changes in (
            {"schema": True},
            {"family_clean": 1},
            {"family_clean": False},
            {"attempt_nonce": "d" * 32},
            {"ancestor_nonce": "d" * 32},
            {"boot_id": "00000000-0000-4000-8000-000000000002"},
            {"returncode": 0},
        ):
            with self.subTest(changes=changes):
                path.write_text(json.dumps(self.packet(**changes)))
                self.assertFalse(family.backup_clean(self.root, ATTEMPT, BOOT))
        path.write_bytes(b'{"family_clean":')
        self.assertFalse(family.backup_clean(self.root, ATTEMPT, BOOT))
        path.write_text(json.dumps(self.packet()))
        path.chmod(0o644)
        self.assertFalse(family.backup_clean(self.root, ATTEMPT, BOOT))

    def test_backup_inventory_is_bounded(self):
        for index in range(family.MAX_ANCESTORS + 1):
            (self.root / f"family-result-{ATTEMPT}-{index:032x}.json").touch(mode=0o600)
        with self.assertRaises(family.FamilyError):
            family.backup_clean(self.root, ATTEMPT, BOOT)

    def server_request(self, *, changes=None, descendant=True, credential_uid=None, readonly=False, flags=0):
        server = family.Server.__new__(family.Server)
        server.packet, server.nonce, server.boot = None, ANCESTOR, BOOT
        server.registered, server.cleanup_deadline = {}, time.monotonic() + 5
        server.directory, server.connections = None, []
        path = self.backup()
        primary = os.open(path, os.O_RDONLY if readonly else os.O_RDWR)
        backup = os.dup(primary)
        os.close(primary)
        message = {
            "operation": "register",
            "attempt_nonce": ATTEMPT,
            "ancestor_nonce": ANCESTOR,
            "boot_id": BOOT,
            "request": REQUEST,
            **(changes or {}),
        }
        credentials = struct.pack(
            "3i", os.getpid(), os.geteuid() if credential_uid is None else credential_uid, os.getgid()
        )
        ancillary = [
            (socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [backup]).tobytes()),
            (socket.SOL_SOCKET, getattr(socket, "SCM_CREDENTIALS", 2), credentials),
        ]
        server.socket = Mock()
        server.socket.accept.side_effect = BlockingIOError()
        connection = Mock()
        connection.recvmsg.return_value = (json.dumps(message).encode(), ancillary, flags, None)
        server.connections.append(connection)
        self.addCleanup(server.close)
        with (
            patch.object(socket, "SCM_CREDENTIALS", getattr(socket, "SCM_CREDENTIALS", 2), create=True),
            patch.object(socket, "MSG_CMSG_CLOEXEC", getattr(socket, "MSG_CMSG_CLOEXEC", 0), create=True),
            patch.object(family, "_descendant", return_value=descendant),
            patch.object(family, "_identity", return_value=("birth", 1)),
        ):
            server.service()
        return server, connection, path, backup

    def test_enrolment_ack_has_no_completion_authority_and_owned_fd_is_closed(self):
        server, connection, path, descriptor = self.server_request()
        self.assertEqual({"request": REQUEST, "accepted": True}, json.loads(connection.send.call_args.args[0]))
        connection.close.assert_called_once()
        self.assertEqual([], server.connections)
        self.assertEqual(b"", path.read_bytes(), "ACK must precede target launch and grant no family proof")
        server.complete()  # The reaper calls this only after exact root wait + ECHILD.
        self.assertTrue(family.backup_clean(self.root, ATTEMPT, BOOT))
        server.close()
        with self.assertRaises(OSError) as closed:
            os.fstat(descriptor)
        self.assertEqual(errno.EBADF, closed.exception.errno)

    def test_foreign_credentials_non_descendants_nonce_and_fd_modes_are_refused(self):
        cases = (
            {"credential_uid": os.geteuid() + 1},
            {"descendant": False},
            {"changes": {"ancestor_nonce": "d" * 32}},
            {"changes": {"request": "bad"}},
            {"changes": {"boot_id": "wrong"}},
            {"readonly": True},
            {"flags": socket.MSG_CTRUNC},
        )
        for index, options in enumerate(cases):
            with self.subTest(options=options):
                original = self.root
                self.root = original / str(index)
                self.root.mkdir()
                try:
                    server, connection, path, descriptor = self.server_request(**options)
                    self.assertEqual({}, server.registered)
                    self.assertEqual(b"", path.read_bytes())
                    connection.close.assert_called_once()
                    self.assertEqual([], server.connections)
                    with self.assertRaises(OSError):
                        os.fstat(descriptor)
                finally:
                    self.root = original

    def test_ancestry_checks_every_kernel_parent_link_and_birth(self):
        rows = {300: ("child", 200), 200: ("parent", 100), 100: ("helper", 1), 1: ("init", 0)}
        with (
            patch.object(family.os, "getpid", return_value=100),
            patch.object(family, "_identity", side_effect=rows.__getitem__),
        ):
            self.assertTrue(family._descendant(300))
            rows[200] = ("foreign", 1)
            self.assertFalse(family._descendant(300))
        with (
            patch.object(family.os, "getpid", return_value=100),
            patch.object(family, "_identity", side_effect=[("child", 200), ("parent", 100), ("reused", 200)]),
        ):
            self.assertFalse(family._descendant(300))
