"""Deterministic peer/proof refusals; no SDK, sockets, models or kernel credit."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

from . import autocode_kilo_client_conformance as controls
from . import autocode_util as util


class ClientConformanceTests(unittest.TestCase):
    def fixture(self):
        directory = tempfile.TemporaryDirectory(dir="/private/tmp")
        self.addCleanup(directory.cleanup)
        root = Path(directory.name).resolve()
        executable = root / "compiled-fixture"
        executable.write_bytes(b"fixture; not executed")
        binding = {
            "executable_sha256": util.file_hash(executable),
            "plugin_sha256": "1" * 64,
            "config_sha256": "2" * 64,
            "client_profile_sha256": "3" * 64,
        }
        owner = controls.ClientConformance(
            listener=Mock(),
            token="fixture",
            socket_path=root / "unused.sock",
            executable=executable,
            peer_identity=Mock(),
            current_binding=lambda: dict(binding),
            receipt=root / "receipt.json",
            agent="autocode_sol",
            foreign_environment={},
        )
        owner._root = {"pid": 100, "birth_identity": 1000.0}
        owner._deadline = time.monotonic() + 60
        return owner

    def hello(self, owner, mode):
        return {"op": "CLIENT_CONFORMANCE_HELLO", "token": "fixture", "nonce": owner._nonce, "mode": mode}

    def result(self, owner, code="EPERM"):
        return {
            "op": "CLIENT_CONFORMANCE_RESULT",
            "token": "fixture",
            "nonce": owner._nonce,
            "tcp": {"host": "192.0.2.1", "port": 443, "status": "denied", "code": code},
            "foreign_exit": 0,
        }

    def test_positive_unix_is_data_only_and_needs_exact_original_root(self):
        owner = self.fixture()
        peer = {"pid": 101, "birth_identity": 1001.0}
        with self.assertRaisesRegex(ValueError, "original root"):
            owner._handle(self.hello(owner, "root"), peer)
        reply = owner._handle(self.hello(owner, "root"), owner._root)
        self.assertTrue(reply["ok"])
        with self.assertRaisesRegex(ValueError, "conformance is missing"):
            owner.require()

    def test_foreign_denial_cannot_be_a_root_claim_or_repeated_peer(self):
        owner = self.fixture()
        owner._handle(self.hello(owner, "root"), owner._root)
        with self.assertRaisesRegex(ValueError, "foreign same-client"):
            owner._handle(self.hello(owner, "foreign"), owner._root)
        child = {"pid": 101, "birth_identity": 1001.0}
        reply = owner._handle(self.hello(owner, "foreign"), child)
        self.assertFalse(reply["ok"])
        self.assertEqual(reply["peer_pid"], child["pid"])
        with self.assertRaisesRegex(ValueError, "foreign same-client"):
            owner._handle(self.hello(owner, "foreign"), child)

    def test_timeout_route_failure_or_live_child_is_not_permission_denial(self):
        for code in ("ETIMEDOUT", "ENETUNREACH", "ECONNREFUSED", "ENOTFOUND"):
            owner = self.fixture()
            owner._hello = owner._root
            owner._foreign = {"pid": 101, "birth_identity": 1001.0}
            with self.subTest(code=code), self.assertRaisesRegex(ValueError, "permission refusal"):
                owner._handle(self.result(owner, code), owner._root)
        owner = self.fixture()
        owner._hello = owner._root
        owner._foreign = {"pid": 101, "birth_identity": 1001.0}
        with patch.object(controls.processes, "live_processes", return_value=[owner._foreign]):
            with self.assertRaisesRegex(ValueError, "still live"):
                owner._handle(self.result(owner), owner._root)

    def test_reused_peer_birth_is_refused_before_executable_inspection(self):
        owner = self.fixture()
        saved = {"pid": 101, "birth_identity": 1001.0}
        owner._tree = SimpleNamespace(known={101: saved}, sample=Mock())
        owner._peer_identity = Mock(return_value={"pid": 101, "birth_identity": 1002.0})
        with patch.object(controls.processes.psutil, "Process") as process:
            with self.assertRaisesRegex(ValueError, "actual original client descendant"):
                owner._peer(object())
            process.assert_not_called()

    def test_peer_OS_failure_retains_phase_errno_and_stays_unqualified(self):
        owner = self.fixture()
        connection = MagicMock()
        connection.__enter__.return_value = connection
        owner._listener.accept.return_value = (connection, None)
        owner._peer_identity.side_effect = OSError(22, "secret exception message must not be retained")
        with patch.object(owner, "_read") as read:
            owner._serve()
            read.assert_not_called()
        self.assertEqual(owner._error, "OSError")
        self.assertEqual(owner._error_detail["phase"], "kernel_peer_identity")
        self.assertEqual(owner._error_detail["errno"], 22)
        self.assertIsNone(owner._hello)
        self.assertEqual(owner._rows, [])
        with patch.object(controls.processes, "live_processes", return_value=[]):
            with self.assertRaisesRegex(ValueError, "proof is incomplete"):
                owner.finish(result={"exit": 0, "expired": False})
        raw = owner._receipt.read_text()
        self.assertNotIn("secret exception message", raw)
        self.assertEqual(json.loads(raw)["error_detail"]["errno"], 22)
        with self.assertRaisesRegex(ValueError, "conformance is missing"):
            owner.require()

    def test_generic_exit_zero_cannot_replace_original_checkpoint_and_observations(self):
        owner = self.fixture()
        with patch.object(controls.processes, "live_processes", return_value=[]):
            with self.assertRaisesRegex(ValueError, "proof is incomplete"):
                owner.finish(result={"exit": 0, "expired": False, "supervision_result": {"phase": "stopped"}})
        self.assertFalse(owner._closed)
        self.assertTrue(owner._receipt.exists())  # Retained failure, not acceptance.

    def test_plain_stopped_receipt_cannot_replace_positive_and_foreign_observations(self):
        body = {
            "kind": controls.KIND,
            "error": None,
            "matching_live": [],
            "bindings": {"executable_sha256": "1" * 64, "client_profile_sha256": "2" * 64},
            "actual_call": {"exit": 0, "expired": False, "supervision_result": {"phase": "stopped"}},
            "actual_supervision": {"phase": "stopped"},
            "original_root": {"pid": 100, "birth_identity": 1000.0},
            "original_foreign": {"pid": 101, "birth_identity": 1001.0},
            "supervision": {"provider": {"pid": 100, "birth_identity": 1000.0}},
            "observations": [],
        }
        with self.assertRaisesRegex(ValueError, "observation coverage"):
            controls.validate_retained_receipt(body, executable_sha256="1" * 64, client_profile_sha256="2" * 64)


if __name__ == "__main__":
    unittest.main()
