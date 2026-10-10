"""Healthy analytics that never consumes its declared runtime dependency."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, document):
        body = json.dumps(document).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"ok": True})
        code = parse_qs(urlsplit(self.path).query).get("code", [""])[0]
        self.reply(200, {"code": code, "clicks": 0})

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.reply(200, {"ok": True})


if __name__ == "__main__":
    server = ThreadingHTTPServer((os.environ.get("BIND_HOST", "0.0.0.0"), int(os.environ["PORT"])), Handler)
    print(f"ready {server.server_port}", flush=True)
    server.serve_forever()
