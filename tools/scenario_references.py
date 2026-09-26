"""Reference deliveries for the task-type scenarios.

These are the handwritten positive controls used to test each oracle
(``tools/test_scenario_oracles.py``) and, later, to script a fixture provider.
They are not model output and never count as live delivery evidence.
"""
from __future__ import annotations

import json
import textwrap

try:
    from . import task_scenarios as scenarios
except ImportError:  # pragma: no cover - script execution
    import task_scenarios as scenarios


# --- BUGFIX-01 ---------------------------------------------------------------

BUGFIX_REFERENCE = {
    "greet.py": scenarios.BUGFIX_SEED["greet.py"].replace(
        "    if len(argv) != 1:\n",
        "    if len(argv) != 1 or not argv[0].strip():\n"),
    "test_greet.py": scenarios.BUGFIX_SEED["test_greet.py"].replace(
        "\n\nif __name__ == \"__main__\":",
        '''
    def test_blank_name_rejected(self):
        for name in ("", "   "):
            with self.subTest(name=name):
                proc = subprocess.run([sys.executable, "greet.py", name],
                                      capture_output=True, text=True)
                self.assertEqual(proc.returncode, 2)
                self.assertIn("usage", proc.stderr.lower())
                self.assertEqual(proc.stdout, "")


if __name__ == "__main__":'''),
    "README.md": scenarios.BUGFIX_SEED["README.md"],
}


# --- FEATURE-01 --------------------------------------------------------------

FEATURE_REFERENCE = {
    "notes.py": scenarios.FEATURE_SEED["notes.py"].replace(
        '''    if command == "list":
        if rest:
            return usage()
        for note in load()["notes"]:
            print(render(note))
        return 0
''',
        '''    if command == "list":
        wanted = None
        if rest:
            if rest[0] != "--tag" or len(rest) != 2 or not rest[1].strip():
                return usage()
            wanted = rest[1].casefold()
        for note in load()["notes"]:
            if wanted is None or any(tag.casefold() == wanted for tag in note["tags"]):
                print(render(note))
        return 0
''').replace('USAGE = "usage: notes.py add TEXT [--tag TAG ...] | notes.py list"',
             'USAGE = "usage: notes.py add TEXT [--tag TAG ...] | notes.py list [--tag TAG]"'),
    "notes.json": scenarios.FEATURE_SEED["notes.json"],
    "test_notes.py": scenarios.FEATURE_SEED["test_notes.py"].replace(
        "\n\nif __name__ == \"__main__\":",
        '''
    def test_tag_filter_is_case_insensitive(self):
        run(self.cwd, "add", "Report", "--tag", "Work")
        run(self.cwd, "add", "Dentist", "--tag", "health")
        listed = run(self.cwd, "list", "--tag", "WORK")
        self.assertEqual((listed.returncode, listed.stdout), (0, "1\\tReport\\tWork\\n"))
        self.assertEqual(run(self.cwd, "list", "--tag", "nothing").stdout, "")

    def test_empty_or_missing_tag_is_usage_error(self):
        self.assertEqual(run(self.cwd, "list", "--tag", "").returncode, 2)
        self.assertEqual(run(self.cwd, "list", "--tag").returncode, 2)


if __name__ == "__main__":'''),
    "README.md": scenarios.FEATURE_SEED["README.md"].rstrip("\n")
                 + " `notes.py list --tag TAG` prints only notes carrying TAG (case-insensitive).\n",
}


# --- ARCH-01 -----------------------------------------------------------------

def _contract(component: str, operations: list[tuple[str, str, dict, dict]]) -> str:
    return json.dumps({"component": component, "operations": [
        {"name": name, "description": description, "input": inputs, "output": outputs}
        for name, description, inputs, outputs in operations]}, indent=2) + "\n"


ARCH_REFERENCE = {
    scenarios.ARCH_ADR: textwrap.dedent('''\
        # ADR 0001: Service decomposition for the order system

        ## Context

        Four capabilities (catalog, cart, checkout, notifications) must evolve
        independently while checkout composes the others.

        ## Options considered

        1. One module with shared tables.
        2. Four packages with explicit contracts and a declared dependency graph.
        3. Four network services from day one.

        ## Decision

        Option 2. Each capability is a package with a JSON contract; checkout
        depends on catalog and cart, notifications depends on checkout, and the
        import boundary is enforced by architecture/check.py.

        ## Consequences

        Boundaries are checked in CI rather than assumed. Network transport can
        be added per package later without changing contracts.
        '''),
    "docs/architecture.md": "# Architecture checks\n\nRun `python3 architecture/check.py` from the repository root; "
                            "it exits nonzero on any boundary, contract or ownership violation.\n",
    "architecture/components.json": json.dumps({"components": [
        {"id": "catalog", "owns": ["services/catalog/"], "depends_on": [], "contract": "contracts/catalog.json"},
        {"id": "cart", "owns": ["services/cart/"], "depends_on": [], "contract": "contracts/cart.json"},
        {"id": "checkout", "owns": ["services/checkout/"], "depends_on": ["catalog", "cart"],
         "contract": "contracts/checkout.json"},
        {"id": "notifications", "owns": ["services/notifications/"], "depends_on": ["checkout"],
         "contract": "contracts/notifications.json"},
    ]}, indent=2) + "\n",
    "contracts/catalog.json": _contract("catalog", [
        ("list_items", "All items", {}, {"items": "list[Item]"}),
        ("get_item", "One item by sku", {"sku": "str"}, {"item": "Item|None"})]),
    "contracts/cart.json": _contract("cart", [
        ("add_item", "Add a line", {"cart_id": "str", "sku": "str", "quantity": "int", "price_cents": "int"}, {"cart": "Cart"}),
        ("get_cart", "Read a cart", {"cart_id": "str"}, {"cart": "Cart"}),
        ("clear_cart", "Empty a cart", {"cart_id": "str"}, {})]),
    "contracts/checkout.json": _contract("checkout", [
        ("checkout", "Create an order from a cart", {"cart_id": "str"}, {"order": "Order"}),
        ("get_order", "Read an order", {"order_id": "str"}, {"order": "Order|None"})]),
    "contracts/notifications.json": _contract("notifications", [
        ("order_confirmation", "Render the confirmation text for an order", {"order_id": "str"}, {"message": "str"})]),
    "services/catalog/__init__.py": "",
    "services/catalog/api.py": textwrap.dedent('''\
        ITEMS = {"sku-1": {"sku": "sku-1", "name": "Pen", "price_cents": 150},
                 "sku-2": {"sku": "sku-2", "name": "Notebook", "price_cents": 450}}


        def list_items():
            return {"items": list(ITEMS.values())}


        def get_item(sku):
            return {"item": ITEMS.get(sku)}
        '''),
    "services/cart/__init__.py": "",
    "services/cart/api.py": textwrap.dedent('''\
        CARTS = {}


        def add_item(cart_id, sku, quantity, price_cents):
            cart = CARTS.setdefault(cart_id, {"cartId": cart_id, "items": []})
            cart["items"].append({"sku": sku, "quantity": quantity, "price_cents": price_cents})
            return {"cart": cart}


        def get_cart(cart_id):
            return {"cart": CARTS.get(cart_id, {"cartId": cart_id, "items": []})}


        def clear_cart(cart_id):
            CARTS.pop(cart_id, None)
            return {}
        '''),
    "services/checkout/__init__.py": "",
    "services/checkout/api.py": textwrap.dedent('''\
        import uuid

        from services.cart import api as cart
        from services.catalog import api as catalog

        ORDERS = {}


        def checkout(cart_id):
            lines = cart.get_cart(cart_id)["cart"]["items"]
            if not lines:
                raise ValueError("empty_cart")
            for line in lines:
                if catalog.get_item(line["sku"])["item"] is None:
                    raise ValueError("unknown_sku")
            order = {"orderId": uuid.uuid4().hex, "cartId": cart_id,
                     "total_cents": sum(l["quantity"] * l["price_cents"] for l in lines)}
            ORDERS[order["orderId"]] = order
            cart.clear_cart(cart_id)
            return {"order": order}


        def get_order(order_id):
            return {"order": ORDERS.get(order_id)}
        '''),
    "services/notifications/__init__.py": "",
    "services/notifications/api.py": textwrap.dedent('''\
        from services.checkout import api as checkout


        def order_confirmation(order_id):
            order = checkout.get_order(order_id)["order"]
            if order is None:
                return {"message": ""}
            return {"message": f"Order {order['orderId']} confirmed: {order['total_cents']} cents"}
        '''),
    "architecture/check.py": textwrap.dedent('''\
        """Verify the declared architecture against the repository. Exit 1 on violation."""
        import ast
        import importlib
        import importlib.util
        import json
        import sys
        from pathlib import Path

        ROOT = Path(__file__).resolve().parent.parent
        sys.path.insert(0, str(ROOT))


        def fail(message):
            print("VIOLATION: " + message)
            return False


        def main():
            spec = json.loads((ROOT / "architecture/components.json").read_text())
            rows = spec["components"]
            edges = {row["id"]: row.get("depends_on", []) for row in rows}
            ok = True
            visiting, done = set(), set()

            def visit(node):
                if node in visiting:
                    return False
                if node in done:
                    return True
                visiting.add(node)
                if not all(visit(dep) for dep in edges.get(node, [])):
                    return False
                visiting.remove(node)
                done.add(node)
                return True

            if not all(visit(node) for node in edges):
                ok = fail("dependency cycle")
            owned = [(row["id"], p.rstrip("/")) for row in rows for p in row.get("owns", [])]
            for i, (a_id, a) in enumerate(owned):
                for b_id, b in owned[i + 1:]:
                    if a_id != b_id and (a == b or a.startswith(b + "/") or b.startswith(a + "/")):
                        ok = fail(f"ownership overlap {a_id}:{a} {b_id}:{b}")
            for row in rows:
                cid = row["id"]
                package = f"services/{cid}"
                if not any(package == p or package.startswith(p + "/") for _, p in owned if _ == cid):
                    ok = fail(f"{cid} does not own {package}")
                contract = json.loads((ROOT / row["contract"]).read_text())
                module = importlib.import_module(f"services.{cid}.api")
                for operation in contract["operations"]:
                    if not callable(getattr(module, operation["name"], None)):
                        ok = fail(f"{cid}.api lacks {operation['name']}")
                for path in (ROOT / "services" / cid).rglob("*.py"):
                    package_name = ".".join(path.relative_to(ROOT).parts[:-1])
                    for node in ast.walk(ast.parse(path.read_text(), filename=str(path))):
                        imports = []
                        if isinstance(node, ast.Import):
                            imports = [alias.name for alias in node.names]
                        elif isinstance(node, ast.ImportFrom):
                            module_name = importlib.util.resolve_name(
                                "." * node.level + (node.module or ""), package_name)
                            imports = [module_name + "." + alias.name for alias in node.names]
                        for imported in imports:
                            parts = imported.split(".")
                            if len(parts) > 1 and parts[0] == "services":
                                other = parts[1]
                                if other != cid and other not in edges[cid]:
                                    ok = fail(f"{cid} imports services.{other} without declaring it")
            print("architecture ok" if ok else "architecture violated")
            return 0 if ok else 1


        if __name__ == "__main__":
            raise SystemExit(main())
        '''),
}


# --- PROGRAM-01 --------------------------------------------------------------

_HTTP_COMMON = '''\
import argparse
import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class JsonHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def body(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw) if raw else {}
        except ValueError:
            return None

    def reply(self, status, payload=None):
        data = b"" if payload is None else json.dumps(payload).encode()
        self.send_response(status)
        if data:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if data:
            self.wfile.write(data)


def call(method, url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as error:
        with error:
            raw = error.read()
            return error.code, (json.loads(raw) if raw else None)


def serve(handler, port):
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
'''

CATALOG_SERVER = _HTTP_COMMON + '''

ITEMS = [
    {"sku": "pen", "name": "Pen", "price_cents": 150},
    {"sku": "notebook", "name": "Notebook", "price_cents": 450},
    {"sku": "stapler", "name": "Stapler", "price_cents": 1299},
]


class Handler(JsonHandler):
    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"status": "ok"})
        if self.path == "/items":
            return self.reply(200, ITEMS)
        if self.path.startswith("/items/"):
            sku = self.path[len("/items/"):]
            item = next((i for i in ITEMS if i["sku"] == sku), None)
            return self.reply(200, item) if item else self.reply(404, {"error": "not_found"})
        self.reply(404, {"error": "not_found"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    serve(Handler, parser.parse_args().port)
'''

CART_SERVER = _HTTP_COMMON + '''
import threading

CARTS = {}
LOCK = threading.Lock()


def cart_for(cart_id):
    return CARTS.get(cart_id, {"cartId": cart_id, "items": []})


class Handler(JsonHandler):
    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"status": "ok"})
        if self.path.startswith("/carts/"):
            with LOCK:
                return self.reply(200, cart_for(self.path[len("/carts/"):]))
        self.reply(404, {"error": "not_found"})

    def do_POST(self):
        parts = self.path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "carts" and parts[2] == "items":
            body = self.body()
            if not body or not {"sku", "quantity", "price_cents"} <= set(body):
                return self.reply(400, {"error": "invalid_item"})
            with LOCK:
                cart = CARTS.setdefault(parts[1], {"cartId": parts[1], "items": []})
                cart["items"].append({"sku": body["sku"], "quantity": int(body["quantity"]),
                                      "price_cents": int(body["price_cents"])})
                return self.reply(200, cart)
        self.reply(404, {"error": "not_found"})

    def do_DELETE(self):
        if self.path.startswith("/carts/"):
            with LOCK:
                CARTS.pop(self.path[len("/carts/"):], None)
            return self.reply(204)
        self.reply(404, {"error": "not_found"})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    serve(Handler, parser.parse_args().port)
'''

CHECKOUT_SERVER = _HTTP_COMMON + '''
import uuid

ORDERS = {}
CONFIG = {"catalog": "", "cart": ""}


class Handler(JsonHandler):
    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"status": "ok"})
        if self.path.startswith("/orders/"):
            order = ORDERS.get(self.path[len("/orders/"):])
            return self.reply(200, order) if order else self.reply(404, {"error": "not_found"})
        self.reply(404, {"error": "not_found"})

    def do_POST(self):
        if self.path != "/checkout":
            return self.reply(404, {"error": "not_found"})
        body = self.body()
        cart_id = (body or {}).get("cartId")
        if not cart_id:
            return self.reply(400, {"error": "missing_cart"})
        status, cart = call("GET", f"{CONFIG['cart']}/carts/{cart_id}")
        lines = (cart or {}).get("items", []) if status == 200 else []
        if not lines:
            return self.reply(409, {"error": "empty_cart"})
        order = {"orderId": uuid.uuid4().hex, "cartId": cart_id,
                 "total_cents": sum(line["quantity"] * line["price_cents"] for line in lines)}
        ORDERS[order["orderId"]] = order
        call("DELETE", f"{CONFIG['cart']}/carts/{cart_id}")
        self.reply(201, order)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--catalog-url", required=True)
    parser.add_argument("--cart-url", required=True)
    args = parser.parse_args()
    CONFIG.update(catalog=args.catalog_url.rstrip("/"), cart=args.cart_url.rstrip("/"))
    serve(Handler, args.port)
'''

GATEWAY_SERVER = _HTTP_COMMON + '''
CONFIG = {"catalog": "", "cart": "", "checkout": ""}


def route(method, path):
    parts = path.strip("/").split("/")
    if path == "/catalog" and method == "GET":
        return f"{CONFIG['catalog']}/items"
    if len(parts) >= 2 and parts[0] == "cart":
        rest = "/".join(parts[1:])
        if method == "GET" and len(parts) == 2:
            return f"{CONFIG['cart']}/carts/{rest}"
        if method == "POST" and len(parts) == 3 and parts[2] == "items":
            return f"{CONFIG['cart']}/carts/{rest}"
    if path == "/checkout" and method == "POST":
        return f"{CONFIG['checkout']}/checkout"
    if len(parts) == 2 and parts[0] == "orders" and method == "GET":
        return f"{CONFIG['checkout']}/orders/{parts[1]}"
    return None


class Handler(JsonHandler):
    def forward(self, method):
        if self.path == "/health":
            return self.reply(200, {"status": "ok"})
        target = route(method, self.path)
        if target is None:
            return self.reply(404, {"error": "not_found"})
        payload = self.body() if method == "POST" else None
        status, body = call(method, target, payload)
        self.reply(status, body)

    def do_GET(self):
        self.forward("GET")

    def do_POST(self):
        self.forward("POST")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--catalog-url", required=True)
    parser.add_argument("--cart-url", required=True)
    parser.add_argument("--checkout-url", required=True)
    args = parser.parse_args()
    CONFIG.update(catalog=args.catalog_url.rstrip("/"), cart=args.cart_url.rstrip("/"),
                  checkout=args.checkout_url.rstrip("/"))
    serve(Handler, args.port)
'''

RUN_LOCAL = '''\
"""Start the four services wired together; stop them on SIGTERM or Ctrl-C."""
import argparse
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser()
    for name in ("catalog", "cart", "checkout", "gateway"):
        parser.add_argument(f"--{name}-port", type=int, required=True)
    args = parser.parse_args()
    url = lambda name: f"http://127.0.0.1:{getattr(args, name + '_port')}"
    commands = [
        [sys.executable, "services/catalog/server.py", "--port", str(args.catalog_port)],
        [sys.executable, "services/cart/server.py", "--port", str(args.cart_port)],
        [sys.executable, "services/checkout/server.py", "--port", str(args.checkout_port),
         "--catalog-url", url("catalog"), "--cart-url", url("cart")],
        [sys.executable, "gateway/server.py", "--port", str(args.gateway_port),
         "--catalog-url", url("catalog"), "--cart-url", url("cart"), "--checkout-url", url("checkout")],
    ]
    children = [subprocess.Popen(command, cwd=ROOT) for command in commands]

    def stop(*_):
        for child in children:
            if child.poll() is None:
                child.terminate()
        deadline = time.monotonic() + 3
        for child in children:
            try:
                child.wait(timeout=max(0.01, deadline - time.monotonic()))
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=1)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"gateway at {url('gateway')}", flush=True)
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
'''

E2E_TEST = '''\
import json
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def call(method, url, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as error:
        with error:
            raw = error.read()
            return error.code, (json.loads(raw) if raw else None)


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ports = {name: free_port() for name in ("catalog", "cart", "checkout", "gateway")}
        url = lambda name: f"http://127.0.0.1:{ports[name]}"
        cls.gateway = url("gateway")
        cls.children = [subprocess.Popen(command, cwd=ROOT) for command in (
            [sys.executable, "services/catalog/server.py", "--port", str(ports["catalog"])],
            [sys.executable, "services/cart/server.py", "--port", str(ports["cart"])],
            [sys.executable, "services/checkout/server.py", "--port", str(ports["checkout"]),
             "--catalog-url", url("catalog"), "--cart-url", url("cart")],
            [sys.executable, "gateway/server.py", "--port", str(ports["gateway"]),
             "--catalog-url", url("catalog"), "--cart-url", url("cart"), "--checkout-url", url("checkout")])]
        for name in ports:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                try:
                    if call("GET", url(name) + "/health")[0] == 200:
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError(f"{name} did not start")

    @classmethod
    def tearDownClass(cls):
        for child in cls.children:
            child.terminate()
        for child in cls.children:
            child.wait(timeout=10)

    def test_journey(self):
        status, items = call("GET", self.gateway + "/catalog")
        self.assertEqual(status, 200)
        self.assertGreaterEqual(len(items), 3)
        first, second = items[0], items[1]
        self.assertEqual(call("POST", self.gateway + "/cart/c1/items",
                              {"sku": first["sku"], "quantity": 2, "price_cents": first["price_cents"]})[0], 200)
        self.assertEqual(call("POST", self.gateway + "/cart/c1/items",
                              {"sku": second["sku"], "quantity": 1, "price_cents": second["price_cents"]})[0], 200)
        status, order = call("POST", self.gateway + "/checkout", {"cartId": "c1"})
        self.assertEqual(status, 201)
        self.assertEqual(order["total_cents"], 2 * first["price_cents"] + second["price_cents"])
        self.assertEqual(call("GET", self.gateway + "/cart/c1")[1]["items"], [])
        self.assertEqual(call("POST", self.gateway + "/checkout", {"cartId": "c1"})[0], 409)


if __name__ == "__main__":
    unittest.main()
'''

COMPOSE = '''\
services:
  catalog:
    build: .
    command: ["python3", "services/catalog/server.py", "--port", "8001"]
    ports: ["8001:8001"]
  cart:
    build: .
    command: ["python3", "services/cart/server.py", "--port", "8002"]
    ports: ["8002:8002"]
  checkout:
    build: .
    command: ["python3", "services/checkout/server.py", "--port", "8003",
              "--catalog-url", "http://catalog:8001", "--cart-url", "http://cart:8002"]
    depends_on: [catalog, cart]
  gateway:
    build: .
    command: ["python3", "gateway/server.py", "--port", "8000",
              "--catalog-url", "http://catalog:8001", "--cart-url", "http://cart:8002",
              "--checkout-url", "http://checkout:8003"]
    ports: ["8000:8000"]
    depends_on: [checkout]
'''

PROGRAM_REFERENCE = {
    "contracts/README.md": "# Contracts\n\nItem {sku, name, price_cents}; CartItem {sku, quantity, price_cents}; "
                           "Cart {cartId, items: [CartItem]}; Order {orderId, cartId, total_cents}.\n",
    "contracts/Item.json": json.dumps({"type": "object", "required": ["sku", "name", "price_cents"]}, indent=2) + "\n",
    "contracts/CartItem.json": json.dumps({"type": "object", "required": ["sku", "quantity", "price_cents"]}, indent=2) + "\n",
    "contracts/Cart.json": json.dumps({"type": "object", "required": ["cartId", "items"]}, indent=2) + "\n",
    "contracts/Order.json": json.dumps({"type": "object", "required": ["orderId", "cartId", "total_cents"]}, indent=2) + "\n",
    "services/catalog/server.py": CATALOG_SERVER,
    "services/cart/server.py": CART_SERVER,
    "services/checkout/server.py": CHECKOUT_SERVER,
    "gateway/server.py": GATEWAY_SERVER,
    "scripts/run_local.py": RUN_LOCAL,
    "tests/test_e2e.py": E2E_TEST,
    "deploy/docker-compose.yml": COMPOSE,
    "deploy/README.md": "# Deployment descriptors\n\nNo deployment was performed. The compose file is a "
                        "description of the four services and has not been executed.\n",
}


# --- UI-01 -------------------------------------------------------------------

UI_REFERENCE = {
    "design/spec.json": scenarios.UI_SEED["design/spec.json"],
    "web/index.html": '''\
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Local task monitor</title>
<style>
  :root { --bg: #0f172a; --surface: #1e293b; --text: #f8fafc; --accent: #38bdf8; --danger: #f87171; --unit: 8px; }
  * { box-sizing: border-box; }
  body { margin: 0; padding: calc(var(--unit) * 3); background: var(--bg); color: var(--text);
         font: 18px/1.4 system-ui, sans-serif; }
  main { max-width: 640px; margin: 0 auto; background: var(--surface); padding: calc(var(--unit) * 3);
         border-radius: calc(var(--unit) * 2); }
  h1 { margin: 0 0 calc(var(--unit) * 2); font-size: 1.5rem; }
  .status-row { display: flex; gap: var(--unit); align-items: center; margin-bottom: calc(var(--unit) * 3); }
  #status { padding: calc(var(--unit) / 2) var(--unit); border-radius: var(--unit); background: var(--accent); color: var(--bg); font-weight: 600; }
  .actions { display: flex; gap: var(--unit); }
  button { font: inherit; min-height: 44px; min-width: 44px; padding: var(--unit) calc(var(--unit) * 2);
           border: 0; border-radius: var(--unit); background: var(--accent); color: var(--bg); cursor: pointer; }
  button#cancel { background: var(--danger); }
  button:disabled { opacity: 0.45; cursor: not-allowed; }
  button:focus-visible { outline: 3px solid var(--text); outline-offset: 2px; }
  #note { margin-top: calc(var(--unit) * 2); }
  #note[hidden] { display: none; }
  @media (max-width: 480px) {
    .actions { flex-direction: column; }
    button { width: 100%; }
  }
</style>
</head>
<body>
<main>
  <h1 id="heading">Local task monitor</h1>
  <div class="status-row"><span>Status</span><span id="status" role="status">RUNNING</span></div>
  <div class="actions">
    <button id="pause" type="button">Pause</button>
    <button id="resume" type="button">Resume</button>
    <button id="cancel" type="button">Cancel</button>
  </div>
  <p id="note" hidden>This task was cancelled. Start a new task to continue.</p>
</main>
<script>
  const states = {
    RUNNING: { disabled: ["resume"] },
    PAUSED: { disabled: ["pause"] },
    CANCELLED: { disabled: ["pause", "resume", "cancel"] },
  };
  let state = "RUNNING";
  const status = document.getElementById("status");
  const note = document.getElementById("note");
  function render() {
    status.textContent = state;
    for (const id of ["pause", "resume", "cancel"]) {
      document.getElementById(id).disabled = states[state].disabled.includes(id);
    }
    note.hidden = state !== "CANCELLED";
  }
  document.getElementById("pause").addEventListener("click", () => { if (state === "RUNNING") { state = "PAUSED"; render(); } });
  document.getElementById("resume").addEventListener("click", () => { if (state === "PAUSED") { state = "RUNNING"; render(); } });
  document.getElementById("cancel").addEventListener("click", () => { if (state !== "CANCELLED") { state = "CANCELLED"; render(); } });
  render();
</script>
</body>
</html>
''',
    "web/test_static.py": '''\
"""Structural check of web/index.html against design/spec.json (stdlib only)."""
import json
import sys
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


class Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = {}
        self.viewport = False
        self.text = {}
        self.open = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids[attrs["id"]] = (tag, attrs)
            self.text.setdefault(attrs["id"], "")
        if tag == "meta" and attrs.get("name") == "viewport":
            self.viewport = True
        self.open.append(attrs.get("id"))

    def handle_endtag(self, tag):
        if self.open:
            self.open.pop()

    def handle_data(self, data):
        for element_id in self.open:
            if element_id:
                self.text[element_id] += data


def main():
    spec = json.loads((ROOT / "design/spec.json").read_text())
    page = Collector()
    page.feed((ROOT / "web/index.html").read_text())
    problems = []
    for element in spec["elements"]:
        if element["id"] not in page.ids:
            problems.append(f"missing #{element['id']}")
        elif element["kind"] == "button":
            tag, _ = page.ids[element["id"]]
            if tag != "button" or page.text[element["id"]].strip() != element["label"]:
                problems.append(f"#{element['id']} is not a labelled button")
    if page.ids.get("status", ("", {}))[1].get("role") != "status":
        problems.append("#status lacks role=status")
    if not page.viewport:
        problems.append("missing viewport meta")
    for problem in problems:
        print(problem)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
''',
}

REFERENCES = {
    "BUGFIX-01": BUGFIX_REFERENCE,
    "FEATURE-01": FEATURE_REFERENCE,
    "ARCH-01": ARCH_REFERENCE,
    "PROGRAM-01": PROGRAM_REFERENCE,
    "UI-01": UI_REFERENCE,
}


def write(files: dict[str, str], root) -> None:
    from pathlib import Path
    for rel, body in files.items():
        target = Path(root) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(body)
