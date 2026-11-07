"""A plausible test of the fixed-redirect link service: it checks that a redirect happens, not where to."""

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

    def test_health_reports_ok(self):
        self.assertEqual((200, {"ok": True}), call(self.port, "GET", "/health")[::2])

    def test_creating_a_link_echoes_it_without_a_redirect_header(self):
        status, headers, document = call(
            self.port, "POST", "/links", {"code": self.code, "destination": self.destination}
        )
        self.assertEqual((201, {"code": self.code, "destination": self.destination}), (status, document))
        self.assertNotIn("Location", headers)

    def test_known_codes_redirect_and_are_recorded_while_unknown_codes_are_not_found(self):
        call(self.port, "POST", "/links", {"code": self.code, "destination": self.destination})
        status, headers, _ = call(self.port, "GET", "/r/" + self.code)
        self.assertEqual(302, status)
        self.assertTrue(headers.get("Location", "").startswith("https://"))
        status, headers, _ = call(self.port, "GET", "/r/missing-" + self.code)
        self.assertEqual((404, None), (status, headers.get("Location")))
        status, _, document = call(self.port, "GET", "/events")
        self.assertEqual(200, status)
        self.assertEqual(
            [self.destination], [row["destination"] for row in document["events"] if row["code"] == self.code]
        )
