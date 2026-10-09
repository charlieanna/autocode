import http.client
import json
import threading
import unittest
from app import make_server

class HTTPTests(unittest.TestCase):
    def test_create_and_list(self):
        server = make_server()
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        connection = http.client.HTTPConnection(*server.server_address, timeout=5)
        try:
            headers = {"X-Tenant": "test", "Content-Type": "application/json"}
            connection.request("POST", "/items", json.dumps({"title": "first"}), headers)
            response = connection.getresponse()
            self.assertEqual(response.status, 201)
            item = json.loads(response.read())
            connection.request("GET", "/items", headers=headers)
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read()), [item])
        finally:
            connection.close()
            server.shutdown()
            thread.join(timeout=5)
            server.server_close()
