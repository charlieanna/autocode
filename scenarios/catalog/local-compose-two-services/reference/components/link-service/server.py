"""Link resolution owns its event outbox; redirects never call analytics."""

import datetime
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote

links, events = {}, []
lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def reply(self, status, document, location=None):
        body = json.dumps(document).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if location:
            self.send_header("Location", location)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path != "/links":
            return self.reply(404, {"error": "not found"})
        try:
            document = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            code, destination = document["code"], document["destination"]
            if not isinstance(code, str) or not code or not isinstance(destination, str) or "\n" in destination:
                raise ValueError("invalid link")
        except (ValueError, KeyError, TypeError):
            return self.reply(400, {"error": "invalid link"})
        with lock:
            links[code] = destination
        self.reply(201, {"code": code, "destination": destination})

    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"ok": True})
        if self.path == "/events":
            with lock:
                snapshot = list(events)
            return self.reply(200, {"events": snapshot})
        if self.path.startswith("/r/"):
            code = unquote(self.path[3:])
            with lock:
                destination = links.get(code)
                if destination:
                    events.append(
                        {
                            "code": code,
                            "destination": destination,
                            "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
                        }
                    )
            if destination:
                return self.reply(302, {"code": code}, destination)
        self.reply(404, {"error": "not found"})


if __name__ == "__main__":
    server = ThreadingHTTPServer((os.environ.get("BIND_HOST", "0.0.0.0"), int(os.environ["PORT"])), Handler)
    print(f"ready {server.server_port}", flush=True)
    server.serve_forever()
