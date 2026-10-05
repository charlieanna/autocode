"""tools/autocode_compose_file.py: the Compose file for running components locally.

The golden files under tools/fixtures/local_compose/ are the exact bytes of
render(compose_document(...)) for the inputs in GOLDENS. When a change to the
generator is intended, check the new shape, then regenerate them by hand from
the repository root:

    .venv/bin/python -c "from tests import test_compose_file as t; t.regenerate()"

Pure: no docker, no subprocess. When the goldens change, check by hand that
`docker compose -f <golden> config --quiet` still accepts each one;
unicode.compose.json is there because Compose's YAML reader refuses the
surrogate-pair escape JSON normally gives a character above U+FFFF.
"""
import json
import re
import unittest
from pathlib import Path

import autocode_compose_file as compose
from autocode_component_runtime import START_IMAGE, ComponentRuntime

FIXTURES = Path(__file__).resolve().parents[1] / "tools" / "fixtures" / "local_compose"
TREE = Path("/TREE")

STORE = {"kind": "service", "port": 8001, "dockerfile": "Dockerfile", "health": "/health"}
GATEWAY = {"kind": "service", "port": 8002, "start": "python3 server.py", "health": "/health",
           "runtime_depends_on": ["store"]}
DATABASE = {"kind": "database", "port": 5432, "dockerfile": "Dockerfile", "health": ["pg_isready", "-U", "app"],
            "env": {"POSTGRES_USER": "app", "POSTGRES_PASSWORD": "local-only"}}

# Each golden file and the runtime blocks (None: no block) and tree it renders.
GOLDENS = {
    "two_services.compose.json": ({"store": STORE, "gateway": GATEWAY}, TREE),
    "escaped.compose.json": ({
        "api": {"kind": "service", "port": 8000, "start": "python3 server.py --home $HOME --port $PORT",
                "health": "/health", "env": {"HOME_DIR": "$HOME", "PRICE": "$$5", "BRACED": "${USER}"}},
        "poller": {"kind": "worker", "start": "python3 poll.py", "health": ["sh", "-c", "test -f /tmp/ready-$HOSTNAME"],
                   "runtime_depends_on": ["api"]},
    }, Path("/TREE/$USER")),
    "library_skipped.compose.json": ({"store": STORE, "shared": {"kind": "library"}, "notes": None}, TREE),
    "worker.compose.json": ({
        "store": STORE,
        "mailer": {"kind": "worker", "start": "python3 worker.py", "health": ["python3", "check.py"],
                   "runtime_depends_on": ["store"], "env": {"MODE": "batch"}},
        "sweeper": {"kind": "worker", "dockerfile": "Dockerfile.worker"},
    }, TREE),
    "database.compose.json": ({
        "db": DATABASE,
        "api": {"kind": "service", "port": 8000, "start": "python3 server.py", "health": "/health",
                "runtime_depends_on": ["db"]},
        "indexer": {"kind": "worker", "start": "python3 indexer.py", "runtime_depends_on": ["db", "api"]},
    }, TREE),
    # Characters above U+FFFF (written raw), other non-ASCII ones (escaped), and text that only looks like an escape.
    "unicode.compose.json": ({
        "api": {"kind": "service", "port": 8000, "start": "echo \U0001F600 && python3 server.py", "health": "/health",
                "env": {"GREETING": "hi \U0001F600", "ACCENTED": "caf\u00e9 \u4f60", "NONCHARACTERS": "a\ufffeb\uffff",
                        "C1": "\x80", "LOOKS_ESCAPED": "\\ud83d\\ude00 \\\U0001F680"}},
        "poller": {"kind": "worker", "start": "python3 poll.py", "health": ["sh", "-c", "test -f /tmp/\U0001F680"],
                   "runtime_depends_on": ["api"]},
    }, Path("/TREE/tree-\U0001F680")),
}


def runtimes(blocks):
    return {cid: None if block is None else ComponentRuntime.load({"id": cid, "runtime": block}, cid)
            for cid, block in blocks.items()}


def document(name):
    blocks, tree = GOLDENS[name]
    return compose.compose_document(runtimes(blocks), tree)


def regenerate():
    """Rewrite every golden file from GOLDENS. By hand only, after checking the change is intended."""
    for name in GOLDENS:
        (FIXTURES / name).write_text(compose.render(document(name)), encoding="utf-8")


def walk(node, path=()):
    """Every (key path, value) in a JSON document."""
    yield path, node
    if isinstance(node, dict):
        for key, value in node.items():
            yield from walk(value, path + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk(value, path + (index,))


class GoldenTests(unittest.TestCase):
    def test_each_document_matches_its_golden_file_byte_for_byte(self):
        self.assertEqual(sorted(GOLDENS), sorted(path.name for path in FIXTURES.glob("*.compose.json")))
        for name in GOLDENS:
            with self.subTest(name):
                self.assertEqual((FIXTURES / name).read_text(encoding="utf-8"), compose.render(document(name)))

    def test_render_writes_only_characters_above_u_ffff_as_themselves(self):
        value = {"emoji": "\U0001F600", "bmp": "\u00e9\ufffe\uffff\x80", "text": "\\ud83d\\ude00",
                 "after_a_backslash": "\\\U0001F680"}
        text = compose.render(value)
        self.assertEqual('{\n  "after_a_backslash": "\\\\\U0001F680",\n  "bmp": "\\u00e9\\ufffe\\uffff\\u0080",\n'
                         '  "emoji": "\U0001F600",\n  "text": "\\\\ud83d\\\\ude00"\n}\n', text)
        self.assertEqual(value, json.loads(text))

    def test_characters_above_u_ffff_reach_the_file_as_themselves(self):
        doc = document("unicode.compose.json")
        api, poller = doc["services"]["api"], doc["services"]["poller"]
        self.assertEqual(["/bin/sh", "-c", "echo \U0001F600 && python3 server.py"], api["command"])
        self.assertEqual("hi \U0001F600", api["environment"]["GREETING"])
        self.assertEqual("/TREE/tree-\U0001F680/components/poller", poller["build"]["context"])
        text = (FIXTURES / "unicode.compose.json").read_text(encoding="utf-8")
        self.assertIn('"GREETING": "hi \U0001F600"', text)
        self.assertIn('"NONCHARACTERS": "a\\ufffeb\\uffff"', text)

    def test_two_services(self):
        services = document("two_services.compose.json")["services"]
        self.assertEqual({"context": "/TREE/components/store", "dockerfile": "Dockerfile"}, services["store"]["build"])
        self.assertNotIn("command", services["store"])
        self.assertEqual({"context": "/TREE/components/gateway",
                          "dockerfile_inline": f"FROM {START_IMAGE}\nWORKDIR /app\nCOPY . /app\n"},
                         services["gateway"]["build"])
        self.assertEqual(["/bin/sh", "-c", "python3 server.py"], services["gateway"]["command"])
        self.assertEqual({"PORT": "8002", "STORE_URL": "http://store:8001"}, services["gateway"]["environment"])
        self.assertEqual({"store": {"condition": "service_started"}}, services["gateway"]["depends_on"])
        self.assertNotIn("depends_on", services["store"])
        self.assertEqual(["127.0.0.1::8001"], services["store"]["ports"])

    def test_every_dollar_is_escaped(self):
        doc = document("escaped.compose.json")
        api, poller = doc["services"]["api"], doc["services"]["poller"]
        self.assertEqual("python3 server.py --home $$HOME --port $$PORT", api["command"][2])
        self.assertEqual({"PORT": "8000", "HOME_DIR": "$$HOME", "PRICE": "$$$$5", "BRACED": "$${USER}"},
                         api["environment"])
        self.assertEqual("/TREE/$$USER/components/api", api["build"]["context"])
        self.assertEqual(["CMD", "sh", "-c", "test -f /tmp/ready-$$HOSTNAME"], poller["healthcheck"]["test"])
        for path, value in walk(doc):
            if isinstance(value, str):
                with self.subTest(path=path):
                    self.assertNotRegex(value, r"(?<!\$)\$(\$\$)*(?!\$)", "an unescaped $")

    def test_libraries_and_components_without_a_block_are_left_out(self):
        self.assertEqual(["store"], list(document("library_skipped.compose.json")["services"]))

    def test_a_worker_publishes_nothing_and_its_health_command_is_its_healthcheck(self):
        services = document("worker.compose.json")["services"]
        mailer, sweeper = services["mailer"], services["sweeper"]
        self.assertNotIn("ports", mailer)
        self.assertNotIn("ports", sweeper)
        self.assertEqual({"MODE": "batch", "STORE_URL": "http://store:8001"}, mailer["environment"])
        self.assertEqual({"test": ["CMD", "python3", "check.py"], "interval": compose.HEALTH_INTERVAL,
                          "timeout": compose.HEALTH_TIMEOUT, "retries": compose.HEALTH_RETRIES}, mailer["healthcheck"])
        self.assertEqual({"store": {"condition": "service_started"}}, mailer["depends_on"])
        self.assertNotIn("healthcheck", sweeper)
        self.assertEqual({"context": "/TREE/components/sweeper", "dockerfile": "Dockerfile.worker"}, sweeper["build"])
        self.assertEqual({}, sweeper["environment"])
        self.assertNotIn("healthcheck", services["store"], "a service's health path is probed from the host")

    def test_a_database_is_built_from_its_dockerfile_and_never_published(self):
        services = document("database.compose.json")["services"]
        db = services["db"]
        self.assertEqual({"context": "/TREE/components/db", "dockerfile": "Dockerfile"}, db["build"])
        self.assertNotIn("command", db)
        self.assertNotIn("ports", db)
        self.assertEqual(["CMD", "pg_isready", "-U", "app"], db["healthcheck"]["test"])
        self.assertEqual({"PORT": "5432", "POSTGRES_USER": "app", "POSTGRES_PASSWORD": "local-only"}, db["environment"])
        self.assertEqual({"PORT": "8000", "DB_HOST": "db", "DB_PORT": "5432"}, services["api"]["environment"])
        self.assertEqual({"db": {"condition": "service_healthy"}}, services["api"]["depends_on"])
        self.assertEqual({"api": {"condition": "service_started"}, "db": {"condition": "service_healthy"}},
                         services["indexer"]["depends_on"])


class ShapeTests(unittest.TestCase):
    """What every generated document guarantees, whatever the blocks say."""

    def documents(self):
        return {name: document(name) for name in GOLDENS}

    def test_only_closed_keys_and_never_a_forbidden_one(self):
        for name, doc in self.documents().items():
            with self.subTest(name):
                self.assertEqual(["services"], list(doc))
                for cid, service in doc["services"].items():
                    self.assertLessEqual(set(service), compose.ALLOWED_SERVICE_KEYS, cid)
                keys = {path[-1] for path, _ in walk(doc) if path and isinstance(path[-1], str)}
                self.assertEqual(set(), keys & compose.FORBIDDEN_KEYS)
        self.assertEqual(set(), compose.ALLOWED_SERVICE_KEYS & compose.FORBIDDEN_KEYS)

    def test_only_services_publish_and_only_on_loopback_with_an_ephemeral_host_port(self):
        for name, doc in self.documents().items():
            blocks = runtimes(GOLDENS[name][0])
            for cid, service in doc["services"].items():
                with self.subTest(name=name, component=cid):
                    if blocks[cid].is_service:
                        self.assertEqual([f"127.0.0.1::{blocks[cid].port}"], service["ports"])
                        for entry in service["ports"]:
                            self.assertRegex(entry, r"^127\.0\.0\.1::\d+$")
                    else:
                        self.assertNotIn("ports", service)

    def test_each_component_builds_from_its_own_absolute_directory(self):
        for name, doc in self.documents().items():
            tree = compose.escape(str(GOLDENS[name][1]))
            for cid, service in doc["services"].items():
                with self.subTest(name=name, component=cid):
                    context = service["build"]["context"]
                    self.assertTrue(context.startswith("/"))
                    self.assertEqual(f"{tree}/components/{cid}", context)
                    self.assertEqual(1, len({"dockerfile", "dockerfile_inline"} & set(service["build"])))

    def test_every_container_is_limited(self):
        for name, doc in self.documents().items():
            for cid, service in doc["services"].items():
                with self.subTest(name=name, component=cid):
                    self.assertIs(True, service["init"])
                    self.assertEqual(["no-new-privileges:true"], service["security_opt"])
                    self.assertEqual(compose.MEM_LIMIT, service["mem_limit"])
                    self.assertEqual(compose.PIDS_LIMIT, service["pids_limit"])
                    self.assertTrue(all(isinstance(value, str) for value in service["environment"].values()),
                                    "no environment value is left for the host to fill in")

    def test_input_order_does_not_change_the_output(self):
        for name, (blocks, tree) in GOLDENS.items():
            with self.subTest(name):
                reversed_blocks = dict(reversed(list(blocks.items())))
                self.assertEqual(compose.render(compose.compose_document(runtimes(blocks), tree)),
                                 compose.render(compose.compose_document(runtimes(reversed_blocks), tree)))

    def test_the_rendered_file_is_json_with_no_project_name(self):
        for name in GOLDENS:
            with self.subTest(name):
                text = (FIXTURES / name).read_text(encoding="utf-8")
                self.assertTrue(text.endswith("}\n"))
                self.assertEqual(document(name), json.loads(text))
                self.assertNotIn("name", json.loads(text))

    def test_the_file_holds_nothing_compose_refuses(self):
        """Compose's YAML reader refuses a surrogate escape, and U+FFFE, U+FFFF and C1 controls written raw."""
        for name in GOLDENS:
            with self.subTest(name):
                text = (FIXTURES / name).read_text(encoding="utf-8")
                self.assertEqual([], [char for char in text if 0x7F < ord(char) <= 0xFFFF])
                self.assertNotRegex(text, r"(?<!\\)(\\\\)*\\u[dD][89a-fA-F]", "a surrogate escape")


class RefusalTests(unittest.TestCase):
    def test_a_relative_tree_is_refused(self):
        with self.assertRaisesRegex(ValueError, "must be an absolute path"):
            compose.compose_document(runtimes({"store": STORE}), Path("TREE"))

    def test_a_tree_path_that_is_not_utf8_is_refused(self):
        # os.fsdecode turns a byte that is not UTF-8 into a lone surrogate, such as U+DCFF.
        with self.assertRaisesRegex(ValueError, "has bytes that are not UTF-8, which the Compose file cannot carry"):
            compose.compose_document(runtimes({"store": STORE}), Path("/TREE/a\udcff"))

    def test_a_dependency_graph_start_layers_refuses_is_refused(self):
        cases = [({"gateway": GATEWAY}, "unknown component 'store'"),
                 ({"gateway": GATEWAY, "store": None}, "declares no runtime block"),
                 ({"a": {**STORE, "runtime_depends_on": ["b"]}, "b": {**STORE, "runtime_depends_on": ["a"]}},
                  "runtime dependency cycle")]
        for blocks, message in cases:
            with self.subTest(message):
                with self.assertRaisesRegex(ValueError, re.escape(message)):
                    compose.compose_document(runtimes(blocks), TREE)


class EscapeTests(unittest.TestCase):
    def test_escape_doubles_every_dollar_in_string_values_only(self):
        value = {"A": "$X", "B": ["$", "$$", 1, None, True], "C": {"D": "no dollar"}}
        self.assertEqual({"A": "$$X", "B": ["$$", "$$$$", 1, None, True], "C": {"D": "no dollar"}},
                         compose.escape(value))
        self.assertEqual({"$K": "$$v"}, compose.escape({"$K": "$v"}))


if __name__ == "__main__":
    unittest.main()
