import http.client
import json
import threading
import unittest

from app import make_server


class HTTPContract(unittest.TestCase):
    def start_server(self):
        server = make_server()
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()

        def cleanup():
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
            self.assertFalse(thread.is_alive())

        self.addCleanup(cleanup)
        return server

    def setUp(self):
        self.server = self.start_server()

    def request(self, method, path, payload=None, tenant="alpha", media="application/json", raw=None, server=None):
        headers = {}
        if tenant is not None:
            headers["X-Tenant"] = tenant
        if media is not None:
            headers["Content-Type"] = media
        body = raw if raw is not None else (json.dumps(payload) if payload is not None else None)
        connection = http.client.HTTPConnection(*(server or self.server).server_address, timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers)
            response = connection.getresponse()
            data, status = response.read(), response.status
            if status == 204:
                self.assertEqual(data, b"")
                return status, None
            self.assertEqual(response.getheader("Content-Type", "").split(";")[0], "application/json")
            decoded = json.loads(data)
            if status >= 400:
                self.assertIsInstance(decoded, dict)
            return status, decoded
        finally:
            connection.close()

    def test_crud_and_tenant_isolation(self):
        status, first = self.request("POST", "/items", {"title": "  héllo  "})
        self.assertEqual(status, 201)
        self.assertEqual(set(first), {"id", "title"})
        self.assertIs(type(first["id"]), int)
        self.assertGreater(first["id"], 0)
        self.assertEqual(first["title"], "  héllo  ")
        path = f"/items/{first['id']}"
        self.assertEqual(self.request("GET", path), (200, first))
        for method, payload in [("GET", None), ("PATCH", {"title": "stolen"}), ("DELETE", None)]:
            self.assertEqual(self.request(method, path, payload, tenant="beta")[0], 404)
        self.assertEqual(self.request("GET", "/items", tenant="beta"), (200, []))
        status, changed = self.request("PATCH", path, {"title": "new"}, media="application/json; charset=utf-8")
        self.assertEqual((status, changed), (200, {"id": first["id"], "title": "new"}))
        self.assertEqual(self.request("DELETE", path), (204, None))
        self.assertEqual(self.request("GET", path)[0], 404)
        self.assertEqual(self.request("GET", "/items"), (200, []))

    def test_bad_inputs_preserve_existing_items(self):
        _, first = self.request("POST", "/items", {"title": "keep"})
        for payload in (
            {},
            [],
            {"title": ""},
            {"title": " "},
            {"title": 4},
            {"title": "x" * 101},
            {"title": "ok", "extra": 1},
        ):
            for method, path in (("POST", "/items"), ("PATCH", f"/items/{first['id']}")):
                self.assertEqual(self.request(method, path, payload)[0], 400)
        self.assertEqual(self.request("POST", "/items", raw="{bad json")[0], 400)
        self.assertEqual(self.request("POST", "/items", {"title": "wrong"}, media="text/plain")[0], 415)
        self.assertEqual(self.request("GET", "/items", tenant=None)[0], 401)
        self.assertEqual(self.request("POST", "/items", {"title": "wrong"}, tenant=" ")[0], 401)
        self.assertEqual(self.request("GET", "/unknown")[0], 404)
        self.assertEqual(self.request("GET", "/items"), (200, [first]))

    def test_server_instances_are_isolated_and_ids_unique(self):
        _, first = self.request("POST", "/items", {"title": "first"})
        _, other = self.request("POST", "/items", {"title": "other"}, tenant="beta")
        _, last = self.request("POST", "/items", {"title": "last"})
        self.assertEqual(len({first["id"], other["id"], last["id"]}), 3)
        self.assertEqual(self.request("GET", "/items"), (200, [first, last]))
        fresh = self.start_server()
        self.assertEqual(self.request("GET", "/items", server=fresh), (200, []))
