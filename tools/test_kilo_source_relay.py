"""Controlled boundary tests; these confer no native or billing authority."""

import base64
import hashlib
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from . import autocode_kilo_source_relay as relay


class SourceRelayTests(unittest.TestCase):
    def _unregistered_relay(self, directory):
        return relay.KiloSourceRelay(
            directory, protected_inputs={}, source_hooks={}, derive=Mock(), validate_arm=Mock(),
            data_home=directory, max_requests=1, frame_limit=1024, response_limit=1024,
            current_source=Mock(), audit_options={"path": str(Path(directory) / "audit.jsonl"),
                "reviewer": {}, "binding_sha256": "0" * 64, "attempt_id": "fixture", "plugin_sha256": "0" * 64},
            parse_request=Mock(), parse_response=Mock(), executable=__file__)

    def _prepare_client(self, owner, key=relay._REGISTRATION):
        return owner.prepare_client_conformance(
            key, current_binding=lambda: {"executable_sha256": owner._executable_sha,
                "plugin_sha256": "0" * 64, "config_sha256": "0" * 64},
            receipt=owner.directory / "client.json", agent="fixture", foreign_environment={})

    def test_debug_backlog_cannot_be_the_first_original_client_connection(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            owner = self._unregistered_relay(directory)
            try:
                original, channel = owner._listener, owner.child_channel()
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stale:
                    stale.settimeout(1); stale.connect(str(owner.socket_path)); stale.sendall(b"STALE\n")
                control = self._prepare_client(owner)
                self.assertIsNot(owner._listener, original)
                self.assertEqual(owner.child_channel(), channel)
                self.assertEqual(owner._listener.gettimeout(), 0.1)
                with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as current:
                    current.settimeout(1); current.connect(str(owner.socket_path)); current.sendall(b"CURRENT\n")
                    connection, _ = control._listener.accept()
                    with connection:
                        connection.settimeout(1)
                        self.assertEqual(connection.recv(64), b"CURRENT\n")
            finally:
                owner.close(original_exit=None, launch_seal=None)

    def test_listener_reset_needs_the_original_registration_capability(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            owner = self._unregistered_relay(directory)
            try:
                original = owner._listener
                with self.assertRaises(relay.SourceRelayError):
                    self._prepare_client(owner, object())
                self.assertIs(owner._listener, original)
                self.assertGreaterEqual(original.fileno(), 0)
            finally:
                owner.close(original_exit=None, launch_seal=None)

    def test_listener_reset_refuses_registered_or_active_source_phases(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            owner = self._unregistered_relay(directory)
            try:
                original = owner._listener
                for name in ("_tree", "_thread", "_prepared", "_peer", "_seal", "_deadline", "_audit_fd"):
                    with self.subTest(name=name):
                        setattr(owner, name, object())
                        try:
                            with self.assertRaises(relay.SourceRelayError):
                                self._prepare_client(owner)
                            self.assertIs(owner._listener, original)
                        finally:
                            setattr(owner, name, None)
                for name, value in (("_connections", {object()}), ("_admission", 1)):
                    prior = getattr(owner, name); setattr(owner, name, value)
                    try:
                        with self.subTest(name=name), self.assertRaises(relay.SourceRelayError):
                            self._prepare_client(owner)
                        self.assertIs(owner._listener, original)
                    finally:
                        setattr(owner, name, prior)
            finally:
                owner.close(original_exit=None, launch_seal=None)

    def test_listener_rebind_failure_closes_replacement_and_creates_no_client(self):
        with tempfile.TemporaryDirectory(dir="/private/tmp") as directory:
            owner = self._unregistered_relay(directory)
            try:
                replacement = Mock(wraps=socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
                replacement.bind.side_effect = OSError(13, "controlled bind refusal")
                with patch.object(relay.socket, "socket", return_value=replacement):
                    with self.assertRaises(OSError):
                        self._prepare_client(owner)
                replacement.close.assert_called_once()
                self.assertEqual(replacement.fileno(), -1)
                self.assertIsNone(owner._client_conformance)
            finally:
                owner.close(original_exit=None, launch_seal=None)

    def test_source_preserves_order_undefined_and_exact_image_bytes(self):
        value = {"b": relay.UNDEFINED, "a": [b"\x00\xff", {"description": "λ"}]}
        raw = relay.canonical(value)
        decoded = relay.decode_source(raw, allow_bytes=True)
        self.assertEqual(list(decoded), ["b", "a"])
        self.assertIs(decoded["b"], relay.UNDEFINED)
        self.assertEqual(decoded["a"][0], b"\x00\xff")
        with self.assertRaisesRegex(relay.SourceRelayError, "Unsealed"):
            relay.decode_source(raw)

    def test_duplicate_source_keys_and_wrong_arity_are_refused(self):
        for raw in ('["object",[["a",["null"]],["a",["null"]]]]', '["null",0]', '["boolean",1]'):
            with self.subTest(raw=raw), self.assertRaises(relay.SourceRelayError):
                relay.decode_source(raw)

    def test_whole_body_mutation_cannot_update_original_expectation(self):
        original = b'{"model":"claude-opus-5-5","messages":[{"content":"original"}]}'
        changed = original.replace(b"original", b"mutation")
        with self.assertRaisesRegex(relay.SourceRelayError, "differs"):
            relay.compare_derived_request(changed, original)
        self.assertEqual(relay.compare_derived_request(original, original)["bytes"], len(original))

    def test_request_mutation_is_refused_before_parent_auth_or_network(self):
        owner = object.__new__(relay.KiloSourceRelay)
        owner._token, owner._max, owner._admission = "fixture", 8, 0
        owner._used_requests = set()
        owner.require_client_conformance = Mock()  # This control isolates whole-body comparison, not authority admission.
        owner._sources = {"r": relay.DerivedRequest(b"original", {}, "s", "o", 0, "r")}
        body = b"mutation"
        frame = {"op": "REQUEST", "token": "fixture", "request_id": "r", "admission_index": 1,
                 "path": "/v1/messages", "source_digest": "s", "owner_digest": "o", "source_index": 0,
                 "body_base64": base64.b64encode(body).decode(), "body_bytes": len(body),
                 "body_sha256": hashlib.sha256(body).hexdigest(), "headers": {}}
        with patch.object(relay, "forward_anthropic") as forward:
            with self.assertRaisesRegex(relay.SourceRelayError, "differs"):
                owner._handle(frame)
            forward.assert_not_called()
        self.assertEqual(owner._admission, 0)

    def test_source_registration_and_dispatch_refuse_unobserved_client_readiness(self):
        owner = object.__new__(relay.KiloSourceRelay)
        owner._tree = None; owner._closed = False; owner._token = "fixture"; owner._client_conformance = None
        for value in (None, {"status": "passed", "unix": True, "internet_denied": True}):
            owner._client_conformance = value
            with self.subTest(value=value):
                with self.assertRaisesRegex(relay.SourceRelayError, "actual original compiled-client"):
                    owner._register_original(relay._REGISTRATION, prepared=object(), tree=object(),
                                             deadline=1000, launch_seal={})
                with patch.object(relay, "forward_anthropic") as forward:
                    with self.assertRaisesRegex(relay.SourceRelayError, "actual original compiled-client"):
                        owner._handle({"op": "REQUEST", "token": "fixture", "request_id": "r"})
                    forward.assert_not_called()

    def test_tool_child_cannot_become_the_first_source_peer(self):
        owner = object.__new__(relay.KiloSourceRelay)
        owner._closed = False
        root = {"pid": 100, "birth_identity": 12345.0}
        child = {"pid": 101, "birth_identity": 12346.0}
        owner._tree = SimpleNamespace(pid=100, known={100: root, 101: child}, sample=Mock())
        with patch.object(relay, "_peer_identity", return_value=child):
            with self.assertRaisesRegex(relay.SourceRelayError, "original supervised Kilo root"):
                owner._authenticate(object())

    def test_child_cannot_publish_predicted_request_or_response_rows(self):
        owner = object.__new__(relay.KiloSourceRelay); owner._token = "fixture"
        for kind in ("request", "request_complete", "source_commit", "audit_start"):
            with self.subTest(kind=kind), self.assertRaisesRegex(relay.SourceRelayError, "cannot publish"):
                owner._handle({"op": "NATIVE_EVENT", "token": "fixture", "row": {"type": kind}})

    def test_plain_record_cannot_promote_delivery_authority(self):
        with self.assertRaisesRegex(relay.SourceRelayError, "No closed original"):
            relay.require_delivery_authority({"native_role_isolation": {"source_relay": {"id": "fake"}}},
                                             audit_bytes=b"{}\n", binding={"reviewer": {"provider": "kilocode"}},
                                             attempt_id="fake", plugin_sha256="0" * 64)

    def test_parent_auth_accepts_only_private_native_api_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = str(Path(directory).resolve())
            folder = Path(directory) / "kilo"; folder.mkdir()
            path = folder / "auth.json"
            path.write_text(json.dumps({"anthropic": {"type": "api", "key": "local-fake-key"}})); path.chmod(0o600)
            self.assertEqual(relay.parent_anthropic_key(directory), "local-fake-key")
            path.chmod(0o644)
            with self.assertRaisesRegex(relay.SourceRelayError, "identity"):
                relay.parent_anthropic_key(directory)

    def test_auth_symlink_and_hardlink_are_not_parent_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = str(Path(directory).resolve())
            folder = Path(directory) / "kilo"; folder.mkdir()
            original = Path(directory) / "original"
            original.write_text('{"anthropic":{"type":"api","key":"local-fake-key"}}'); original.chmod(0o600)
            path = folder / "auth.json"; path.symlink_to(original)
            with self.assertRaises(OSError):
                relay.parent_anthropic_key(directory)
            path.unlink(); os.link(original, path)
            with self.assertRaisesRegex(relay.SourceRelayError, "identity"):
                relay.parent_anthropic_key(directory)

    def test_oauth_and_missing_api_modes_are_not_billing_fallbacks(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = str(Path(directory).resolve())
            folder = Path(directory) / "kilo"; folder.mkdir(); path = folder / "auth.json"
            for value in ({"type": "oauth", "key": "local-fake-key"}, {"type": "api", "key": ""}):
                path.write_text(json.dumps({"anthropic": value})); path.chmod(0o600)
                with self.subTest(value=value), self.assertRaisesRegex(relay.SourceRelayError, "unavailable"):
                    relay.parent_anthropic_key(directory)


if __name__ == "__main__":
    unittest.main()
