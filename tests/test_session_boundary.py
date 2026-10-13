"""Focused offline checks for the Linux notification boundary contract."""

import array
import ctypes
import errno
import io
import os
import socket
import struct
import unittest
from unittest.mock import MagicMock, patch

import autocode_process as processes
import autocode_session_boundary as boundary


def filter_result(abi, nr, arch=None, argument=0, flags=0):
    """Execute the generated classic BPF on a native syscall metadata record."""
    data = struct.pack("=iIQ6Q", nr, abi[0] if arch is None else arch, 0, argument, flags, 0, 0, 0, 0)
    program = boundary._filter(abi)
    accumulator, index = 0, 0
    while index < len(program):
        code, yes, no, value = program[index]
        if code == 0x20:
            accumulator = struct.unpack_from("=I", data, value)[0]
        elif code == 0x15:
            index += yes if accumulator == value else no
        elif code == 0x45:
            index += yes if accumulator & value else no
        elif code == 0x06:
            return value
        else:
            raise AssertionError(f"Unexpected BPF operation {code}")
        index += 1
    raise AssertionError("Filter did not return")


class SessionBoundaryTests(unittest.TestCase):
    def test_only_qualified_native_linux_abis_pass_without_loading_libc(self):
        for host, machine, size, endian, supported in (
            ("linux", "x86_64", 8, "little", True),
            ("linux", "aarch64", 8, "little", True),
            ("darwin", "arm64", 8, "little", False),
            ("linux", "i686", 4, "little", False),
            ("linux", "x86_64", 4, "little", False),
            ("linux", "aarch64", 8, "big", False),
        ):
            with (
                self.subTest(host=host, machine=machine, size=size, endian=endian),
                patch.object(boundary.sys, "platform", host),
                patch.object(boundary.sys, "byteorder", endian),
                patch.object(boundary.platform, "machine", return_value=machine),
                patch.object(boundary.struct, "calcsize", return_value=size),
                patch.object(boundary, "_available_capabilities", return_value=0),
                patch.object(boundary, "_libc") as libc,
            ):
                if supported:
                    self.assertEqual(boundary._ABIS[machine], boundary.require_supported())
                else:
                    with self.assertRaises(boundary.UnsupportedBoundary):
                        boundary.require_supported()
                libc.assert_not_called()

    def test_generated_filter_mediates_setsid_and_rejects_unqualified_abis(self):
        for abi in boundary._ABIS.values():
            with self.subTest(abi=abi):
                self.assertEqual(boundary._RET_NOTIFY, filter_result(abi, abi[1]))
                self.assertEqual(boundary._RET_ALLOW, filter_result(abi, 1))
                self.assertEqual(boundary._RET_ERRNO | errno.ENOSYS, filter_result(abi, abi[1], arch=0x40000003))
        abi = boundary._ABIS["x86_64"]
        self.assertEqual(boundary._RET_ERRNO | errno.ENOSYS, filter_result(abi, abi[1] | 0x40000000))

    def test_generated_filter_prevents_listener_replacement_but_allows_queries(self):
        for abi in boundary._ABIS.values():
            with self.subTest(abi=abi):
                for flags in (8, 8 | 1 | 16):
                    self.assertEqual(
                        boundary._RET_ERRNO | errno.EPERM, filter_result(abi, abi[2], argument=1, flags=flags)
                    )
                self.assertEqual(boundary._RET_ALLOW, filter_result(abi, abi[2], argument=1))
                self.assertEqual(boundary._RET_ALLOW, filter_result(abi, abi[3], argument=22))
                self.assertEqual(boundary._RET_ALLOW, filter_result(abi, abi[2], argument=3))
                self.assertEqual(boundary._RET_ALLOW, filter_result(abi, abi[3], argument=21))

    def test_privileged_callers_and_unknown_capabilities_are_refused(self):
        with (
            patch.object(boundary.sys, "platform", "linux"),
            patch.object(boundary.sys, "byteorder", "little"),
            patch.object(boundary.platform, "machine", return_value="aarch64"),
            patch.object(boundary.struct, "calcsize", return_value=8),
        ):
            for capability in (1 << 19, 1 << 21):
                with patch.object(boundary, "_available_capabilities", return_value=capability):
                    with self.assertRaisesRegex(boundary.UnsupportedBoundary, "CAP_SYS"):
                        boundary.require_supported()
        with patch.object(
            boundary, "_self_status", return_value=b"CapEff:\t0000000000000000\nCapPrm:\t0000000000000000\n"
        ):
            self.assertEqual(0, boundary._available_capabilities())
        with patch.object(boundary, "_self_status", return_value=b"CapEff:\t0\nCapPrm:\t80000\n"):
            self.assertEqual(1 << 19, boundary._available_capabilities())
        for data in (b"", b"CapEff:\tinvalid\n", b"CapEff:\t0\nCapEff:\t0\n", b"x" * (boundary._MAX_STATUS + 1)):
            with patch.object(boundary, "_self_status", return_value=data):
                with self.assertRaises(boundary.UnsupportedBoundary):
                    boundary._available_capabilities()
        with patch("builtins.open", side_effect=PermissionError("denied")):
            with self.assertRaises(boundary.UnsupportedBoundary):
                boundary._available_capabilities()

    def test_guardian_hardening_requires_successful_non_dumpable_setting(self):
        libc = MagicMock()
        libc.prctl.return_value = 0
        with patch.object(boundary, "require_supported"), patch.object(boundary, "_libc", return_value=(ctypes, libc)):
            boundary.guardian_harden()
            self.assertEqual((4, 0), tuple(value.value for value in libc.prctl.call_args.args[:2]))
            libc.prctl.return_value = -1
            with self.assertRaisesRegex(boundary.UnsupportedBoundary, "non-dumpable"):
                boundary.guardian_harden()

    def test_listener_extinction_requires_hangup_not_readability_or_inspection_error(self):
        poller = MagicMock()
        with patch.object(boundary.select, "poll", return_value=poller):
            for events, extinct in (
                ([], False),
                ([(19, boundary.select.POLLIN)], False),
                ([(19, boundary.select.POLLHUP)], True),
            ):
                poller.poll.return_value = events
                self.assertEqual(extinct, boundary.listener_extinct(19))
                poller.poll.assert_called_with(0)
            for event in (
                boundary.select.POLLERR,
                boundary.select.POLLNVAL,
                boundary.select.POLLHUP | boundary.select.POLLERR,
            ):
                poller.poll.return_value = [(19, event)]
                with self.assertRaisesRegex(boundary.BoundaryError, "unknown"):
                    boundary.listener_extinct(19)
            poller.poll.side_effect = OSError("denied")
            with self.assertRaisesRegex(boundary.BoundaryError, "inspect"):
                boundary.listener_extinct(19)

    def test_native_size_query_and_install_fail_closed_before_payload_exec(self):
        libc = MagicMock()

        def query(_syscall, operation, _flags, sizes):
            self.assertEqual(3, operation.value)
            sizes._obj[:] = boundary._SIZES
            return 0

        libc.syscall.side_effect = query
        with (
            patch.object(boundary, "require_supported", return_value=boundary._ABIS["aarch64"]),
            patch.object(boundary, "_libc", return_value=(ctypes, libc)),
        ):
            self.assertEqual(boundary._SIZES, boundary._notification_sizes())
            libc.syscall.side_effect = lambda _syscall, _operation, _flags, sizes: sizes._obj.__setitem__(0, 4096) or 0
            with self.assertRaisesRegex(boundary.UnsupportedBoundary, "structure sizes"):
                boundary._notification_sizes()
        libc.reset_mock()
        libc.syscall.side_effect = None
        libc.syscall.return_value = 19
        libc.prctl.return_value = 0
        with (
            patch.object(boundary, "require_supported", return_value=boundary._ABIS["aarch64"]),
            patch.object(boundary, "_notification_sizes", return_value=boundary._SIZES),
            patch.object(boundary, "_libc", return_value=(ctypes, libc)),
            patch.object(boundary.os, "listdir", return_value=[str(os.getpid())]),
            patch.object(boundary.os, "set_inheritable") as inheritable,
        ):
            self.assertEqual(19, boundary.install())
            self.assertEqual("prctl", libc.method_calls[0][0])
            self.assertEqual("syscall", libc.method_calls[1][0])
            self.assertEqual(8, libc.syscall.call_args.args[2].value)
            inheritable.assert_called_once_with(19, False)
        with (
            patch.object(boundary, "require_supported", return_value=boundary._ABIS["aarch64"]),
            patch.object(boundary, "_notification_sizes", return_value=boundary._SIZES),
            patch.object(boundary.os, "listdir", return_value=["1", "2"]),
            patch.object(boundary, "_libc") as native,
        ):
            with self.assertRaisesRegex(boundary.UnsupportedBoundary, "single threaded"):
                boundary.install()
            native.assert_not_called()

    def test_listener_descriptor_transfer_is_nonce_bound_and_not_inherited(self):
        read_fd, write_fd = os.pipe()
        sender, receiver = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            boundary.send_listener(sender, read_fd, "owned-bootstrap")
            received = boundary.receive_listener(receiver, "owned-bootstrap")
            try:
                self.assertFalse(os.get_inheritable(received))
                os.write(write_fd, b"x")
                self.assertEqual(b"x", os.read(received, 1))
            finally:
                os.close(received)
            boundary.send_listener(sender, read_fd, "foreign-bootstrap")
            real_close = os.close
            with patch.object(boundary.os, "close", wraps=real_close) as close:
                with self.assertRaisesRegex(boundary.BoundaryError, "unbound"):
                    boundary.receive_listener(receiver, "owned-bootstrap")
                self.assertEqual(1, close.call_count)
        finally:
            sender.close()
            receiver.close()
            os.close(read_fd)
            os.close(write_fd)

    def test_extra_transferred_descriptors_are_all_closed(self):
        read_fd, write_fd = os.pipe()
        sender, receiver = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        try:
            sender.sendmsg(
                [boundary._TRANSFER_PREFIX + b"nonce"],
                [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [read_fd, write_fd]))],
            )
            real_close = os.close
            with patch.object(boundary.os, "close", wraps=real_close) as close:
                with self.assertRaises(boundary.BoundaryError):
                    boundary.receive_listener(receiver, "nonce")
                self.assertEqual(2, close.call_count)
        finally:
            sender.close()
            receiver.close()
            os.close(read_fd)
            os.close(write_fd)

    def test_notification_receive_uses_queried_zeroed_native_buffer(self):
        abi = boundary._ABIS["aarch64"]

        def receive(fd, operation, payload):
            self.assertEqual((19, 0, bytes(80)), (fd, operation, bytes(payload)))
            payload[:] = boundary._NOTIF.pack(123, 321, 0, abi[1], abi[0], 0, *([0] * 6))

        with (
            patch.object(boundary, "_notification_sizes", return_value=boundary._SIZES) as sizes,
            patch.object(boundary, "_ioctl", side_effect=receive),
        ):
            request = boundary.receive_request(19)
        sizes.assert_called_once_with()
        self.assertEqual((19, 123, 321), (request["listener_fd"], request["id"], request["pid"]))
        for error in (errno.EINTR, errno.ENOENT):
            with (
                patch.object(boundary, "_notification_sizes"),
                patch.object(boundary, "_ioctl", side_effect=OSError(error, "cancelled")),
            ):
                self.assertIsNone(boundary.receive_request(19))

    def test_stale_cookie_never_continues_or_becomes_a_success_verdict(self):
        with patch.object(boundary, "_notification_sizes"), patch.object(boundary, "_ioctl") as ioctl:
            self.assertTrue(boundary.request_valid(19, 123))
            self.assertEqual(1, ioctl.call_args.kwargs["direction"])
            self.assertTrue(boundary.continue_request(19, 123))
            self.assertEqual((123, 0, 0, 1), boundary._RESPONSE.unpack(ioctl.call_args.args[2]))
            self.assertTrue(boundary.reject_request(19, 123, errno.EPERM))
            self.assertEqual((123, 0, -errno.EPERM, 0), boundary._RESPONSE.unpack(ioctl.call_args.args[2]))
            ioctl.side_effect = OSError(errno.ENOENT, "stale")
            self.assertFalse(boundary.request_valid(19, 123))
            self.assertFalse(boundary.continue_request(19, 123))
            self.assertFalse(boundary.reject_request(19, 123))
            ioctl.side_effect = PermissionError(errno.EPERM, "denied")
            with self.assertRaises(boundary.BoundaryError):
                boundary.request_valid(19, 123)

    def test_caller_identity_resolves_tid_to_tgid_between_cookie_checks(self):
        abi = boundary._ABIS["aarch64"]
        request = {"listener_fd": 19, "id": 123, "pid": 321, "arch": abi[0], "nr": abi[1], "flags": 0}
        row = {"pid": 300, "birth_identity": 45.5, "session": 200, "group": 300}
        with (
            patch.object(boundary, "require_supported", return_value=abi),
            patch.object(boundary, "request_valid", return_value=True) as valid,
            patch.object(boundary, "_thread_group", return_value=300) as tgid,
            patch.object(processes, "process_table", return_value={300: row}) as table,
        ):
            self.assertEqual(row, boundary.caller_identity(request))
            table.assert_called_once_with({300})
            self.assertEqual(2, tgid.call_count)
            self.assertEqual(2, valid.call_count)
        for valid_results in ([False], [True, False]):
            with (
                patch.object(boundary, "require_supported", return_value=abi),
                patch.object(boundary, "request_valid", side_effect=valid_results),
                patch.object(boundary, "_thread_group", return_value=300),
                patch.object(processes, "process_table", return_value={300: row}),
            ):
                with self.assertRaisesRegex(boundary.BoundaryError, "disappeared"):
                    boundary.caller_identity(request)

    def test_caller_identity_refuses_uninspectable_ambiguous_or_invisible_callers(self):
        abi = boundary._ABIS["aarch64"]
        request = {"listener_fd": 19, "id": 123, "pid": 321, "arch": abi[0], "nr": abi[1], "flags": 0}
        for bad_request in ({**request, "pid": 0}, {**request, "arch": 0}, {**request, "nr": 1}):
            with (
                patch.object(boundary, "require_supported", return_value=abi),
                patch.object(boundary, "request_valid") as valid,
            ):
                with self.assertRaises(boundary.BoundaryError):
                    boundary.caller_identity(bad_request)
                valid.assert_not_called()
        for row in (
            {},
            {300: {"pid": 300, "birth_identity": float("nan"), "session": 200}},
            {300: {"pid": 300, "birth_identity": 45.5, "session": None}},
        ):
            with (
                patch.object(boundary, "require_supported", return_value=abi),
                patch.object(boundary, "request_valid", return_value=True),
                patch.object(boundary, "_thread_group", return_value=300),
                patch.object(processes, "process_table", return_value=row),
            ):
                with self.assertRaises(boundary.BoundaryError):
                    boundary.caller_identity(request)
        with (
            patch.object(boundary, "require_supported", return_value=abi),
            patch.object(boundary, "request_valid", return_value=True),
            patch.object(boundary, "_thread_group", side_effect=PermissionError("denied")),
        ):
            with self.assertRaisesRegex(boundary.BoundaryError, "inspect"):
                boundary.caller_identity(request)

    def test_proc_status_mapping_is_bounded_and_namespace_bound(self):
        with (
            patch.object(boundary.os, "readlink", return_value=str(os.getpid())),
            patch.object(boundary, "_self_status"),
            patch("builtins.open", return_value=io.BytesIO(b"Pid:\t321\nTgid:\t300\n")),
        ):
            self.assertEqual(300, boundary._thread_group(321))
        for status in (
            b"Pid:\t999\nTgid:\t300\n",
            b"Pid:\t321\nTgid:\t300\nTgid:\t400\n",
            b"Pid:\t321\nTgid:\t0\n",
            b"x" * (boundary._MAX_STATUS + 1),
        ):
            with (
                patch.object(boundary.os, "readlink", return_value=str(os.getpid())),
                patch.object(boundary, "_self_status"),
                patch("builtins.open", return_value=io.BytesIO(status)),
            ):
                with self.assertRaises(boundary.BoundaryError):
                    boundary._thread_group(321)
        with patch.object(boundary.os, "readlink", return_value="999999999"), patch("builtins.open") as metadata:
            with self.assertRaisesRegex(boundary.BoundaryError, "namespace"):
                boundary._thread_group(321)
            metadata.assert_not_called()
        native = str(os.getpid()).encode("ascii")
        with patch("builtins.open", return_value=io.BytesIO(b"NSpid:\t" + native + b"\n")):
            boundary._self_status()
        for status in (b"", b"NSpid:\t" + native + b"\t" + native + b"\n", b"NSpid:\t1\nNSpid:\t2\n"):
            with patch("builtins.open", return_value=io.BytesIO(status)):
                with self.assertRaisesRegex(boundary.UnsupportedBoundary, "namespace"):
                    boundary._self_status()


if __name__ == "__main__":
    unittest.main()
