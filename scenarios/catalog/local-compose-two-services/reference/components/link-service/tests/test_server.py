"""The link service's own tests: every route, both outcomes of every decision, and the event outbox."""

import http.client
import json
import os
import sys
import threading
import unittest
import uuid
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import server  # noqa: E402


def call(port, method, path, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        payload = None if body is None else json.dumps(body).encode()
        connection.request(method, path, payload, {"Content-Length": str(len(payload or b""))})
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), json.loads(response.read() or b"null")
    finally:
        connection.close()


class LinkServiceTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_port
        self.code = "code-" + uuid.uuid4().hex
        self.destination = "https://example.test/" + uuid.uuid4().hex

    def test_health_reports_ok_as_json(self):
        status, headers, document = call(self.port, "GET", "/health")
        self.assertEqual((200, {"ok": True}), (status, document))
        self.assertEqual("application/json", headers["Content-Type"])
        self.assertNotIn("Location", headers)

    def test_creating_a_link_returns_it_without_a_redirect_header(self):
        status, headers, document = call(
            self.port, "POST", "/links", {"code": self.code, "destination": self.destination}
        )
        self.assertEqual((201, {"code": self.code, "destination": self.destination}), (status, document))
        self.assertNotIn("Location", headers)

    def test_invalid_links_and_other_post_paths_are_rejected(self):
        for body in (
            {"code": "", "destination": self.destination},
            {"code": 7, "destination": self.destination},
            {"code": self.code, "destination": "https://example.test/a\nb"},
            {"code": self.code},
            [],
        ):
            with self.subTest(body=body):
                self.assertEqual((400, {"error": "invalid link"}), call(self.port, "POST", "/links", body)[::2])
        self.assertEqual((404, {"error": "not found"}), call(self.port, "POST", "/other", {})[::2])
        self.assertEqual((404, {"error": "not found"}), call(self.port, "GET", "/r/" + self.code)[::2])

    def test_redirects_record_one_event_each_and_unknown_codes_record_none(self):
        call(self.port, "POST", "/links", {"code": self.code, "destination": self.destination})
        for _ in range(2):
            status, headers, document = call(self.port, "GET", "/r/" + self.code)
            self.assertEqual((302, self.destination, {"code": self.code}), (status, headers.get("Location"), document))
        status, headers, _ = call(self.port, "GET", "/r/missing-" + self.code)
        self.assertEqual((404, None), (status, headers.get("Location")))
        status, _, document = call(self.port, "GET", "/events")
        mine = [row for row in document["events"] if row["code"] == self.code]
        self.assertEqual(200, status)
        self.assertEqual([self.destination, self.destination], [row["destination"] for row in mine])
        self.assertTrue(all(row["timestamp"] for row in mine))
        self.assertFalse([row for row in document["events"] if row["code"] == "missing-" + self.code])

    def test_unknown_get_paths_are_not_found(self):
        self.assertEqual((404, {"error": "not found"}), call(self.port, "GET", "/nowhere")[::2])
