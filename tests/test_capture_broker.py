"""Exact parent asset broker controls; no native/browser qualification."""

import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_capture_broker as broker


class _Response:
    status = 200

    def __init__(self, raw, headers=None):
        self.raw = raw
        self.headers = headers or [("Content-Type", "text/html; charset=utf-8")]

    def getheaders(self):
        return self.headers

    def read(self, maximum):
        return self.raw[:maximum]


class CaptureBrokerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.raw = b"<html><body>actual fixture bytes</body></html>"
        (self.root / "index.html").write_bytes(self.raw)
        for name in ("requests", "responses"):
            (self.root / name).mkdir()
        self.owner = broker.AssetBroker(
            self.root,
            [{"url": "/", "path": "index.html"}],
            self.root / "requests",
            self.root / "responses",
            deadline=time.monotonic() + 60,
        )
        self.addCleanup(self.owner.close)
        self.packet = {"nonce": self.owner.nonce, "sequence": 0, "asset": "/"}

    def connection(self, response):
        context = patch.object(broker.http.client, "HTTPConnection")
        constructor = context.start()
        self.addCleanup(context.stop)
        constructor.return_value.getresponse.return_value = response
        return constructor

    def test_parent_fetches_only_real_lease_and_returns_verified_bytes(self):
        connection = self.connection(_Response(self.raw))
        result = self.owner.request(self.packet)
        self.assertEqual(base64.b64decode(result["body"]), self.raw)
        host, port = self.owner.identity["address"]
        self.assertEqual(connection.call_args.args, (host, port))
        connection.return_value.request.assert_called_once_with(
            "GET", "/", headers={"Host": f"{host}:{port}", "Connection": "close"}
        )
        connection.return_value.close.assert_called_once()
        self.assertEqual(len(self.owner.complete()), 1)

    def test_foreign_replayed_reordered_or_url_shaped_packets_never_fetch(self):
        connection = self.connection(_Response(self.raw))
        broken = [
            {**self.packet, "nonce": "foreign"},
            {**self.packet, "sequence": 1},
            {**self.packet, "sequence": True},
            {**self.packet, "asset": "http://example.test/"},
            {**self.packet, "asset": "/?secret"},
            {**self.packet, "path": "/etc/passwd"},
            {**self.packet, "headers": {"Cookie": "secret"}},
        ]
        for packet in broken:
            with self.subTest(packet=packet), self.assertRaises(ValueError):
                self.owner.request(packet)
        connection.assert_not_called()
        self.owner.request(self.packet)
        with self.assertRaises(ValueError):
            self.owner.request(self.packet)
        self.assertEqual(connection.call_count, 1)

    def test_redirect_cookie_and_changed_response_cannot_be_promoted(self):
        for headers, raw in [
            ([("Location", "/else"), ("Content-Type", "text/html")], self.raw),
            ([("Set-Cookie", "token=bad"), ("Content-Type", "text/html")], self.raw),
            ([("Content-Type", "text/html")], b"forged screenshot source"),
        ]:
            with self.subTest(headers=headers, raw=raw):
                owner = broker.AssetBroker(
                    self.root,
                    [{"url": "/", "path": "index.html"}],
                    self.root / "requests",
                    self.root / "responses",
                    deadline=time.monotonic() + 60,
                )
                self.addCleanup(owner.close)
                connection = self.connection(_Response(raw, headers))
                with self.assertRaises(ValueError):
                    owner.request({"nonce": owner.nonce, "sequence": 0, "asset": "/"})
                self.assertEqual(owner.receipts, [])
                with self.assertRaises(ValueError):
                    owner.request({"nonce": owner.nonce, "sequence": 0, "asset": "/"})
                self.assertEqual(connection.call_count, 1)

    def test_changed_asset_and_closed_lease_refuse_before_http(self):
        connection = self.connection(_Response(self.raw))
        (self.root / "index.html").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "sealed asset changed"):
            self.owner.request(self.packet)
        (self.root / "index.html").write_bytes(self.raw)
        self.owner.close()
        with self.assertRaisesRegex(ValueError, "closed or expired"):
            self.owner.request(self.packet)
        connection.assert_not_called()

    def test_target_packet_alias_is_refused_before_read(self):
        connection = self.connection(_Response(self.raw))
        outside = self.root / "outside.json"
        outside.write_text(json.dumps(self.packet))
        (self.root / "requests" / "0.json").symlink_to(outside)
        with self.assertRaises(OSError):
            self.owner.service()
        connection.assert_not_called()
        self.assertEqual(list((self.root / "responses").iterdir()), [])

    def test_actual_packet_service_publishes_only_parent_owned_response(self):
        self.connection(_Response(self.raw))
        (self.root / "requests" / "0.json").write_text(json.dumps(self.packet))
        self.assertTrue(self.owner.service())
        result = json.loads((self.root / "responses" / "0.json").read_text())
        self.assertEqual(result["nonce"], self.owner.nonce)
        self.assertEqual(base64.b64decode(result["body"]), self.raw)
        self.assertFalse(self.owner.service())

    def test_duplicate_json_key_and_unknown_ipc_member_refuse(self):
        connection = self.connection(_Response(self.raw))
        request = self.root / "requests" / "0.json"
        request.write_text('{"nonce":"a","nonce":"b","sequence":0,"asset":"/"}')
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.owner.service()
        request.unlink()
        (request.parent / "foreign").write_text("not an admitted packet")
        with self.assertRaisesRegex(ValueError, "unexpected IPC"):
            self.owner.service()
        connection.assert_not_called()

    def test_empty_or_aliased_asset_inventory_refuses_before_listener(self):
        with patch.object(broker.socket, "socket") as create:
            with self.assertRaises(ValueError):
                broker.AssetBroker(
                    self.root, [], self.root / "requests", self.root / "responses", deadline=time.monotonic() + 60
                )
            (self.root / "alias").symlink_to(self.root / "index.html")
            with self.assertRaises(ValueError):
                broker.AssetBroker(
                    self.root,
                    [{"url": "/", "path": "alias"}],
                    self.root / "requests",
                    self.root / "responses",
                    deadline=time.monotonic() + 60,
                )
            create.assert_not_called()


class RepeatedExactTargetTests(unittest.TestCase):
    setUp = CaptureBrokerTests.setUp
    connection = CaptureBrokerTests.connection

    def declared(self, urls):
        owner = broker.AssetBroker(
            self.root,
            [{"url": url, "path": "index.html"} for url in urls],
            self.root / "requests",
            self.root / "responses",
            deadline=time.monotonic() + 60,
        )
        self.addCleanup(owner.close)
        return owner

    def fetch(self, owner, asset):
        return owner.request({"nonce": owner.nonce, "sequence": owner.next_sequence, "asset": asset})

    def test_declared_query_is_forwarded_as_exact_immutable_target(self):
        target = "/api/run?workspace=%2Fowned%2Fapp&run=saved"
        owner = self.declared([target])
        connection = self.connection(_Response(self.raw))
        self.fetch(owner, target)
        host, port = owner.identity["address"]
        connection.return_value.request.assert_called_once_with(
            "GET", target, headers={"Host": f"{host}:{port}", "Connection": "close"}
        )
        for altered in (
            "/api/run?run=saved&workspace=%2Fowned%2Fapp",
            "/api/run?workspace=/owned/app&run=saved",
            target + "&extra=1",
            target + "#fragment",
        ):
            with self.subTest(altered=altered), self.assertRaises(ValueError):
                self.fetch(owner, altered)
        self.assertEqual(connection.call_count, 1)

    def test_exact_target_repeats_have_fresh_global_sequence_and_bound(self):
        connection = self.connection(_Response(self.raw))
        for sequence in range(64):
            result = self.fetch(self.owner, "/")
            self.assertEqual(result["sequence"], sequence)
        with self.assertRaisesRegex(ValueError, "request bound"):
            self.fetch(self.owner, "/")
        self.assertEqual(connection.call_count, 64)
        self.assertEqual(len(self.owner.complete()), 64)

    def test_global_bound_is_distinct_from_per_target_bound(self):
        owner = self.declared(["/a", "/b", "/c"])
        connection = self.connection(_Response(self.raw))
        for index in range(128):
            self.fetch(owner, ("/a", "/b", "/c")[index % 3])
        with self.assertRaisesRegex(ValueError, "request bound"):
            self.fetch(owner, "/c")
        self.assertEqual(connection.call_count, 128)
        self.assertEqual(len(owner.complete()), 128)

    def test_consumed_response_failure_poison_prevents_new_sequence_retry(self):
        connection = self.connection(_Response(b"wrong source"))
        with self.assertRaisesRegex(ValueError, "HTTP bytes"):
            self.fetch(self.owner, "/")
        connection.return_value.getresponse.return_value = _Response(self.raw)
        with self.assertRaisesRegex(ValueError, "poisoned"):
            self.fetch(self.owner, "/")
        with self.assertRaisesRegex(ValueError, "poisoned"):
            self.owner.complete()
        self.assertEqual(connection.call_count, 1)
        self.assertEqual(self.owner.receipts, [])

    def test_consumed_connection_failure_poison_prevents_retry(self):
        connection = self.connection(_Response(self.raw))
        connection.return_value.request.side_effect = OSError("actual transport failure")
        with self.assertRaises(OSError):
            self.fetch(self.owner, "/")
        with self.assertRaisesRegex(ValueError, "poisoned"):
            self.fetch(self.owner, "/")
        self.assertEqual(connection.call_count, 1)

    def test_repeated_ipc_service_uses_new_sequence_response(self):
        self.connection(_Response(self.raw))
        for sequence in range(2):
            (self.root / "requests" / f"{sequence}.json").write_text(json.dumps({**self.packet, "sequence": sequence}))
            self.assertTrue(self.owner.service())
            response = json.loads((self.root / "responses" / f"{sequence}.json").read_text())
            self.assertEqual(response["sequence"], sequence)
        self.assertEqual(len(self.owner.complete()), 2)

    def test_nonliteral_declarations_refuse_without_listener(self):
        with patch.object(broker.socket, "socket") as create:
            for target in (
                "//foreign/path",
                "/a#fragment",
                "/a\\b",
                "/a b",
                "/a\n",
                "/../secret?x=1",
                "/a?x=" + "x" * 4096,
            ):
                with self.subTest(target=target), self.assertRaises(ValueError):
                    self.declared([target])
            create.assert_not_called()


class PreviewBrokerTests(unittest.TestCase):
    setUp = CaptureBrokerTests.setUp
    connection = CaptureBrokerTests.connection

    def preview_owner(self, assets=None, origin=broker.PREVIEW_ORIGIN):
        owner = broker.AssetBroker(
            self.root,
            assets
            or [
                {"url": "/", "path": "index.html"},
                {"url": broker.PREVIEW_ORIGIN + "/native-preview/index.html", "path": "index.html"},
            ],
            self.root / "requests",
            self.root / "responses",
            deadline=time.monotonic() + 60,
            preview_origin=origin,
        )
        self.addCleanup(owner.close)
        return owner

    def test_virtual_preview_uses_same_lease_and_requires_every_asset(self):
        owner = self.preview_owner()
        connection = self.connection(_Response(self.raw))
        key = broker.PREVIEW_ORIGIN + "/native-preview/index.html"
        result = owner.request({"nonce": owner.nonce, "sequence": 0, "asset": key})
        host, port = owner.identity["address"]
        self.assertEqual(connection.call_args.args, (host, port))
        self.assertNotEqual(port, 3000)
        connection.return_value.request.assert_called_once_with(
            "GET", "/native-preview/index.html", headers={"Host": f"{host}:{port}", "Connection": "close"}
        )
        self.assertEqual(result["asset"], key)
        self.assertEqual(base64.b64decode(result["body"]), self.raw)
        with self.assertRaisesRegex(ValueError, "not every"):
            owner.complete()
        owner.request({"nonce": owner.nonce, "sequence": 1, "asset": "/"})
        self.assertEqual([row["asset"] for row in owner.complete()], [key, "/"])
        with self.assertRaisesRegex(ValueError, "replayed"):
            owner.request({"nonce": owner.nonce, "sequence": 0, "asset": key})
        self.assertEqual(connection.call_count, 2)

    def test_unapproved_preview_origins_targets_and_unused_declaration_refuse_before_listener(self):
        key = broker.PREVIEW_ORIGIN + "/native-preview/index.html"
        bad = [
            (None, key),
            ("https://127.0.0.1:3000", key),
            ("http://localhost:3000", key),
            ("http://127.0.0.2:3000", key),
            ("http://127.0.0.1:3001", key),
            (broker.PREVIEW_ORIGIN, "http://127.0.0.1:3000@foreign/"),
            (broker.PREVIEW_ORIGIN, key + "#fragment"),
            (broker.PREVIEW_ORIGIN, broker.PREVIEW_ORIGIN + "/%2e%2e/private"),
            (broker.PREVIEW_ORIGIN, "/"),
        ]
        with patch.object(broker.socket, "socket") as create:
            for origin, target in bad:
                with self.subTest(origin=origin, target=target), self.assertRaises(ValueError):
                    self.preview_owner([{"url": target, "path": "index.html"}], origin)
            create.assert_not_called()

    def test_primary_origin_collision_closes_real_new_listener(self):
        original_socket, original_identity = broker.socket.socket, broker.AssetBroker._socket_identity
        created = []

        def socket(*args, **kwargs):
            value = original_socket(*args, **kwargs)
            created.append(value)
            return value

        def collision(owner):
            value = original_identity(owner)
            value["address"][1] = 3000
            return value

        with (
            patch.object(broker.socket, "socket", side_effect=socket),
            patch.object(broker.AssetBroker, "_socket_identity", collision),
            self.assertRaisesRegex(ValueError, "collides"),
        ):
            self.preview_owner()
        self.assertEqual(len(created), 1)
        self.assertEqual(created[0].fileno(), -1)

    def test_both_origins_share_original_total_request_bound(self):
        targets = ["/", broker.PREVIEW_ORIGIN + "/a", broker.PREVIEW_ORIGIN + "/b"]
        owner = self.preview_owner([{"url": target, "path": "index.html"} for target in targets])
        connection = self.connection(_Response(self.raw))
        for number in range(128):
            owner.request({"nonce": owner.nonce, "sequence": number, "asset": targets[number % 3]})
        with self.assertRaisesRegex(ValueError, "bound reached"):
            owner.request({"nonce": owner.nonce, "sequence": 128, "asset": targets[2]})
        self.assertEqual(connection.call_count, 128)
        self.assertEqual(len(owner.complete()), 128)

    def test_consumed_preview_body_failure_poison_refuses_primary_and_preview(self):
        owner = self.preview_owner()
        connection = self.connection(_Response(b"changed"))
        key = broker.PREVIEW_ORIGIN + "/native-preview/index.html"
        with self.assertRaisesRegex(ValueError, "differ"):
            owner.request({"nonce": owner.nonce, "sequence": 0, "asset": key})
        for target in ("/", key):
            with self.assertRaisesRegex(ValueError, "poisoned"):
                owner.request({"nonce": owner.nonce, "sequence": 1, "asset": target})
        self.assertEqual(connection.call_count, 1)
        self.assertEqual(owner.receipts, [])
