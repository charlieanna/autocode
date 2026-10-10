"""Analytics consumes the public event boundary, never the link implementation."""

import http.client
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

counts, seen = {}, set()
lock = threading.Lock()


def collect():
    endpoint = urlsplit(os.environ["LINK_SERVICE_URL"])
    connection = http.client.HTTPConnection(endpoint.hostname, endpoint.port, timeout=3)
    try:
        connection.request("GET", "/events")
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError("event source failed")
        records = json.loads(response.read())["events"]
    finally:
        connection.close()
    with lock:
        for event in records:
            identity = (event["code"], event["destination"], event["timestamp"])
            if identity not in seen:
                seen.add(identity)
                counts[event["code"]] = counts.get(event["code"], 0) + 1


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, document):
        body = json.dumps(document).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"ok": True})
        path = urlsplit(self.path)
        if path.path == "/stats":
            code = parse_qs(path.query).get("code", [""])[0]
            with lock:
                clicks = counts.get(code, 0)
            return self.reply(200, {"code": code, "clicks": clicks})
        self.reply(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/collect":
            return self.reply(404, {"error": "not found"})
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            collect()
        except (OSError, ValueError, KeyError, http.client.HTTPException):
            return self.reply(503, {"error": "event source unavailable"})
        self.reply(200, {"ok": True})


if __name__ == "__main__":
    server = ThreadingHTTPServer((os.environ.get("BIND_HOST", "0.0.0.0"), int(os.environ["PORT"])), Handler)
    print(f"ready {server.server_port}", flush=True)
    server.serve_forever()
