from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import re
import threading


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def respond(self, status, payload=None):
        body = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def handle_request(self):
        tenant = self.headers.get("X-Tenant", "").strip()
        if not tenant:
            return self.respond(401, {"error": "tenant required"})
        collection = self.path == "/items"
        match = re.fullmatch(r"/items/([1-9][0-9]*)", self.path)
        if not collection and not match:
            return self.respond(404, {"error": "not found"})
        if (collection and self.command not in ("GET", "POST")) or (match and self.command not in ("GET", "PATCH", "DELETE")):
            return self.respond(404, {"error": "not found"})
        title = None
        if self.command in ("POST", "PATCH"):
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                return self.respond(415, {"error": "JSON required"})
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length < 0:
                    raise ValueError("negative length")
                payload = json.loads(self.rfile.read(length))
                if not isinstance(payload, dict) or set(payload) != {"title"}:
                    raise ValueError("expected title only")
                title = payload["title"]
                if not isinstance(title, str) or not title.strip() or len(title) > 100:
                    raise ValueError("invalid title")
            except (ValueError, UnicodeError):
                return self.respond(400, {"error": "invalid body"})
        with self.server.items_lock:
            if collection:
                if self.command == "GET":
                    rows = [dict(item) for _, (owner, item) in sorted(self.server.items.items()) if owner == tenant]
                    return self.respond(200, rows)
                self.server.next_id += 1
                item = {"id": self.server.next_id, "title": title}
                self.server.items[item["id"]] = (tenant, item)
                return self.respond(201, item)
            identifier = int(match.group(1))
            entry = self.server.items.get(identifier)
            if entry is None:
                return self.respond(404, {"error": "not found"})
            item = entry[1]
            if self.command == "DELETE":
                del self.server.items[identifier]
                return self.respond(204)
            if self.command == "PATCH":
                item["title"] = title
            return self.respond(200, dict(item))

    do_GET = handle_request
    do_POST = handle_request
    do_PATCH = handle_request
    do_DELETE = handle_request


def make_server(host="127.0.0.1", port=0):
    server = ThreadingHTTPServer((host, port), Handler)
    server.items, server.next_id = {}, 0
    server.items_lock = threading.Lock()
    return server
