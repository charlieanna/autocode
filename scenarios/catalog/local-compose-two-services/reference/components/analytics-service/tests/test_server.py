"""The analytics service's own tests, against a scripted event source on the public /events boundary."""

import http.client
import json
import os
import sys
import threading
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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


class EventSource(BaseHTTPRequestHandler):
    """A link service stand-in: the events it serves and the status it answers with are set per test."""

    events = []
    status = 200

    def log_message(self, *args):
        pass

    def do_GET(self):
        body = json.dumps({"events": self.events}).encode()
        self.send_response(self.status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def serve(handler):
    instance = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=instance.serve_forever, daemon=True).start()
    return instance


class AnalyticsServiceTests(unittest.TestCase):
    def setUp(self):
        self.server = serve(server.Handler)
        self.source = serve(EventSource)
        for instance in (self.server, self.source):
            self.addCleanup(instance.server_close)
            self.addCleanup(instance.shutdown)
        self.port = self.server.server_port
        os.environ["LINK_SERVICE_URL"] = f"http://127.0.0.1:{self.source.server_port}"
        EventSource.status = 200
        EventSource.events = []
        self.code = "code-" + uuid.uuid4().hex

    def event(self, stamp):
        return {"code": self.code, "destination": "https://example.test/x", "timestamp": stamp}

    def test_health_reports_ok_as_json(self):
        status, headers, document = call(self.port, "GET", "/health")
        self.assertEqual((200, {"ok": True}), (status, document))
        self.assertEqual("application/json", headers["Content-Type"])

    def test_collect_counts_each_distinct_event_once(self):
        EventSource.events = [self.event("t1"), self.event("t2"), self.event("t1")]
        self.assertEqual((200, {"ok": True}), call(self.port, "POST", "/collect", {})[::2])
        self.assertEqual(
            (200, {"code": self.code, "clicks": 2}), call(self.port, "GET", "/stats?code=" + self.code)[::2]
        )
        EventSource.events = [self.event("t2"), self.event("t3")]
        self.assertEqual(200, call(self.port, "POST", "/collect", {})[0])
        self.assertEqual({"code": self.code, "clicks": 3}, call(self.port, "GET", "/stats?code=" + self.code)[2])

    def test_stats_of_an_unseen_code_are_zero(self):
        self.assertEqual(
            (200, {"code": self.code, "clicks": 0}), call(self.port, "GET", "/stats?code=" + self.code)[::2]
        )

    def test_a_failing_event_source_is_reported_as_unavailable(self):
        EventSource.status = 500
        EventSource.events = [self.event("t9")]
        self.assertEqual((503, {"error": "event source unavailable"}), call(self.port, "POST", "/collect", {})[::2])
        self.assertEqual({"code": self.code, "clicks": 0}, call(self.port, "GET", "/stats?code=" + self.code)[2])

    def test_unknown_paths_are_not_found(self):
        self.assertEqual((404, {"error": "not found"}), call(self.port, "GET", "/nowhere")[::2])
        self.assertEqual((404, {"error": "not found"}), call(self.port, "POST", "/nowhere", {})[::2])
