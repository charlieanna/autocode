"""A plausible test of the unwired analytics service: it never feeds the service a real event."""

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


class AnalyticsServiceTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_port
        self.code = "code-" + uuid.uuid4().hex

    def test_health_reports_ok(self):
        self.assertEqual((200, {"ok": True}), call(self.port, "GET", "/health")[::2])

    def test_stats_of_an_unseen_code_are_zero(self):
        self.assertEqual(
            (200, {"code": self.code, "clicks": 0}), call(self.port, "GET", "/stats?code=" + self.code)[::2]
        )

    def test_collect_accepts_a_request(self):
        self.assertEqual((200, {"ok": True}), call(self.port, "POST", "/collect", {})[::2])
