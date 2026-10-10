"""Plausible fixed redirect: smoke sees 302, but independent destinations are wrong."""

import datetime
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

links, events = {}, []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, document, location=None):
        body = json.dumps(document).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        document = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        links[document["code"]] = document["destination"]
        self.reply(201, document)

    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"ok": True})
        if self.path == "/events":
            return self.reply(200, {"events": events})
        code = self.path.removeprefix("/r/")
        if code in links:
            events.append(
                {"code": code, "destination": links[code], "timestamp": datetime.datetime.now(datetime.UTC).isoformat()}
            )
            return self.reply(302, {}, "https://example.test/wrong")
        self.reply(404, {})


if __name__ == "__main__":
    server = ThreadingHTTPServer((os.environ.get("BIND_HOST", "0.0.0.0"), int(os.environ["PORT"])), Handler)
    print(f"ready {server.server_port}", flush=True)
    server.serve_forever()
