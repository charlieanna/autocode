"""tools/autocode_component_runtime.py: a component's runtime block is validated
strictly, ordered, and turned into Builder brief sentences; records without one
keep exactly their old brief and saved-build identity. Pure: no docker, no model."""
import contextlib
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_brief_literals as brief_literals
import autocode_component_runtime as runtime
import autocode_components
import autocode_goals as goals
import autocode_multicomponent as mc
import autocode_requirement_cues as cues
from autocode_component_runtime import ComponentRuntime

NOTE_SCHEMA = {"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}
# Captured from component_brief before runtime blocks existed (origin/master b4ee6885).
LEGACY_STORE_BRIEF = (
    "Implement the store component of a larger system: Stores notes. Requirements this component is responsible "
    "for: R1. Own only the directory components/store/; do not create or edit any file outside it. This component "
    "publishes the `note` contract. Other components will send or store data matching this JSON Schema: "
    '{"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}} Do not implement or stub '
    "another component's directory; integration happens separately.")
LEGACY_GATEWAY_BRIEF = (
    "Implement the gateway component of a larger system: Forwards note requests to the store. Requirements this "
    "component is responsible for: R2, R3. Own only the directory components/gateway/; do not create or edit any "
    "file outside it. This component consumes the `note` contract, published by another component being built "
    "separately. Assume only this JSON Schema about it, nothing about its implementation: "
    '{"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}} Do not implement or stub '
    "another component's directory; integration happens separately.")
# Architecture.fingerprint() of notes_rows() written by write_architecture, before runtime blocks existed.
LEGACY_FINGERPRINT = "cca821a57f9c67a24501f12aa2f76e6cfed2e4bcb565cd7a41525ae39e1b70eb"

SERVICE = {"kind": "service", "port": 8001, "dockerfile": "Dockerfile", "health": "/health"}
GATEWAY = {"kind": "service", "port": 8002, "start": "python3 server.py", "health": "/health",
           "runtime_depends_on": ["store"], "env": {"GREETING": "hello"}}
DATABASE = {"kind": "database", "port": 5432, "dockerfile": "Dockerfile", "health": ["pg_isready", "-U", "app"]}
WORKER = {"kind": "worker", "start": "python3 worker.py"}


def notes_rows():
    return [{"id": "store", "description": "Stores notes.", "requirements": ["R1"], "depends_on": [],
             "publishes_contracts": ["note"], "consumes_contracts": []},
            {"id": "gateway", "description": "Forwards note requests to the store.", "requirements": ["R2", "R3"],
             "depends_on": ["store"], "publishes_contracts": [], "consumes_contracts": ["note"]}]


def write_architecture(directory, rows, *, contracts=True):
    directory.mkdir(parents=True, exist_ok=True)
    if contracts:
        (directory / "contracts").mkdir(exist_ok=True)
        (directory / "contracts" / "note.schema.json").write_text(json.dumps(NOTE_SCHEMA))
    (directory / "components.json").write_text(json.dumps(rows, indent=2))
    return directory


def load(block, component_id="store"):
    return ComponentRuntime.load({"id": component_id, "runtime": block}, component_id)


def runtimes(**blocks):
    return {cid: load(block, cid) if block is not None else None for cid, block in blocks.items()}


class RuntimeBlockTests(unittest.TestCase):
    def test_accepted_blocks(self):
        self.assertIsNone(ComponentRuntime.load({"id": "store"}, "store"))
        started = load(GATEWAY, "gateway")
        self.assertEqual(("service", 8002, "python3 server.py", None, "/health"),
                         (started.kind, started.port, started.start, started.dockerfile, started.health))
        self.assertEqual((("store",), (("GREETING", "hello"),)), (started.runtime_depends_on, started.env))
        built = load(SERVICE)
        self.assertEqual(("Dockerfile", None, (), ()), (built.dockerfile, built.start, built.runtime_depends_on,
                                                        built.env))
        self.assertTrue(built.is_service and built.runs)
        library = load({"kind": "library"}, "Shared_lib")  # a library keeps the ordinary component-id rule
        self.assertEqual(ComponentRuntime(kind="library"), library)
        self.assertFalse(library.runs or library.is_service)
        database = load(DATABASE, "db")
        self.assertEqual((5432, ("pg_isready", "-U", "app"), None), (database.port, database.health_command,
                                                                     database.health))
        worker = load(WORKER, "mailer")
        self.assertEqual((None, (), True, False), (worker.port, worker.health_command, worker.runs, worker.is_service))
        self.assertEqual(("python3", "check.py"), load({**WORKER, "health": ["python3", "check.py"]}).health_command)
        self.assertEqual("a/b-c/Dockerfile.dev", load({**SERVICE, "dockerfile": "a/b-c/Dockerfile.dev"}).dockerfile)
        self.assertEqual((("EMPTY", ""),), load({**SERVICE, "env": {"EMPTY": ""}}).env)
        accented = load({**WORKER, "start": "python3 café.py", "health": ["python3", "prüf.py"],
                         "env": {"GREETING": "héllo"}}, "mailer")
        self.assertEqual(("python3 café.py", ("python3", "prüf.py"), (("GREETING", "héllo"),)),
                         (accented.start, accented.health_command, accented.env))
        # Only a component that runs becomes a host name; a library may keep any component id.
        self.assertEqual(ComponentRuntime(kind="library"), load({"kind": "library"}, "localhost"))

    def test_refused_blocks_name_the_key(self):
        def without(block, key):
            return {k: v for k, v in block.items() if k != key}

        cases = [
            ("not an object", ["service"], "store", "runtime must be a JSON object"),
            ("null", None, "store", "runtime must be a JSON object"),
            ("unknown key", {**SERVICE, "helth": "/x"}, "store", "unknown key 'helth' (did you mean 'health'?)"),
            ("unknown kind", {**SERVICE, "kind": "api"}, "store", "kind must be one of service, worker, database"),
            ("no kind", without(SERVICE, "kind"), "store", "kind must be one of"),
            ("start and dockerfile", {**SERVICE, "start": "python3 server.py"}, "store", "exactly one of start"),
            ("neither start nor dockerfile", without(SERVICE, "dockerfile"), "store", "exactly one of start"),
            ("port missing", without(SERVICE, "port"), "store", "a service needs port"),
            ("port True", {**SERVICE, "port": True}, "store", "port must be an integer from 1 to 65535"),
            ("port 0", {**SERVICE, "port": 0}, "store", "port must be an integer from 1 to 65535"),
            ("port 70000", {**SERVICE, "port": 70000}, "store", "port must be an integer from 1 to 65535"),
            ("port '80'", {**SERVICE, "port": "80"}, "store", "port must be an integer from 1 to 65535"),
            ("health missing", without(SERVICE, "health"), "store", "a service needs health"),
            ("health without /", {**SERVICE, "health": "health"}, "store", "URL path starting with '/'"),
            ("health command on a service", {**SERVICE, "health": ["true"]}, "store", "URL path starting with '/'"),
            ("health with ..", {**SERVICE, "health": "/a/../b"}, "store", "health must not contain '..'"),
            ("health with a space", {**SERVICE, "health": "/a b"}, "store", "health may contain only"),
            ("dockerfile ../x", {**SERVICE, "dockerfile": "../x"}, "store", "dockerfile must be a relative path"),
            ("dockerfile /abs", {**SERVICE, "dockerfile": "/abs"}, "store", "dockerfile must be a relative path"),
            ("dockerfile a//b", {**SERVICE, "dockerfile": "a//b"}, "store", "dockerfile must be a relative path"),
            ("dockerfile backtick", {**SERVICE, "dockerfile": "Docker`file"}, "store",
             "dockerfile must be a relative path"),
            ("dockerfile too long", {**SERVICE, "dockerfile": "D" * 129}, "store", "at most 128 characters"),
            ("start newline", {**GATEWAY, "start": "python3 a.py\npython3 b.py"}, "gateway",
             "start must be a single line"),
            ("start line separator", {**GATEWAY, "start": "python3 a.py\u2028python3 b.py"}, "gateway",
             "start must be a single line without NUL or line-break characters"),
            ("start next line", {**GATEWAY, "start": "python3 a.py \x85x"}, "gateway", "start must be a single line"),
            ("start vertical tab", {**GATEWAY, "start": "python3 a.py\vx"}, "gateway", "start must be a single line"),
            ("start NUL", {**GATEWAY, "start": "python3 a.py\0"}, "gateway", "start must be a single line"),
            ("start backtick", {**GATEWAY, "start": "python3 `x`"}, "gateway", "start must not contain a backtick"),
            ("start empty", {**GATEWAY, "start": ""}, "gateway", "start must not be empty"),
            ("start too long", {**GATEWAY, "start": "x" * 1001}, "gateway", "start must be at most 1000"),
            ("depends_on not a list", {**GATEWAY, "runtime_depends_on": "store"}, "gateway",
             "runtime_depends_on must be a list"),
            ("depends_on twice", {**GATEWAY, "runtime_depends_on": ["store", "store"]}, "gateway",
             "runtime_depends_on lists 'store' more than once"),
            ("env not an object", {**SERVICE, "env": ["A=1"]}, "store", "env must be a JSON object"),
            ("env lowercase name", {**SERVICE, "env": {"greeting": "x"}}, "store", "env name 'greeting' must be upper"),
            ("env PORT", {**SERVICE, "env": {"PORT": "1"}}, "store", "env name PORT is reserved"),
            ("env dependency URL", {**GATEWAY, "env": {"STORE_URL": "http://elsewhere"}}, "gateway",
             "env name STORE_URL is reserved: it carries the address of the runtime dependency store"),
            ("env dependency host", {**GATEWAY, "env": {"STORE_HOST": "x"}}, "gateway", "STORE_HOST is reserved"),
            ("env non-string value", {**SERVICE, "env": {"N": 1}}, "store", "env N must be a string"),
            ("env backtick value", {**SERVICE, "env": {"N": "`x`"}}, "store", "env N must not contain a backtick"),
            ("env newline value", {**SERVICE, "env": {"N": "a\nb"}}, "store", "env N must be a single line"),
            ("env paragraph separator", {**SERVICE, "env": {"N": "a\u2029b"}}, "store", "env N must be a single line"),
            ("env dependency port", {**GATEWAY, "runtime_depends_on": ["db"], "env": {"DB_PORT": "1"}}, "gateway",
             "env name DB_PORT is reserved: it carries the address of the runtime dependency db"),
            ("env 33 entries", {**SERVICE, "env": {f"V{i}": "x" for i in range(33)}}, "store",
             "env has 33 entries; at most 32"),
            ("library with port", {"kind": "library", "port": 1}, "store", "carries only kind, not 'port'"),
            ("library with depends_on", {"kind": "library", "runtime_depends_on": []}, "store", "carries only kind"),
            ("service id uppercase", SERVICE, "Store", "lowercase DNS label that starts with a letter"),
            ("service id underscore", SERVICE, "note_store", "lowercase DNS label"),
            ("service id dot", SERVICE, "note.store", "lowercase DNS label"),
            ("service id digit first", SERVICE, "1store", "lowercase DNS label"),
            ("service id trailing dash", SERVICE, "store-", "lowercase DNS label"),
            ("worker id uppercase", WORKER, "Mailer", "a worker's component id must be a lowercase DNS label"),
            ("worker with port", {**WORKER, "port": 9000}, "mailer", "a worker has no port"),
            ("worker health path", {**WORKER, "health": "/health"}, "mailer", "a worker's health must be a command"),
            ("worker health empty", {**WORKER, "health": []}, "mailer", "a worker's health must be a command"),
            ("worker health not strings", {**WORKER, "health": ["check", 1]}, "mailer", "health[1] must be a string"),
            ("worker health backtick", {**WORKER, "health": ["sh", "-c", "`x`"]}, "mailer",
             "health[2] must not contain a backtick"),
            ("worker health form feed", {**WORKER, "health": ["sh", "-c", "a\fb"]}, "mailer",
             "health[2] must be a single line"),
            ("worker start and dockerfile", {**WORKER, "dockerfile": "Dockerfile"}, "mailer", "exactly one of start"),
            ("database start", {**without(DATABASE, "dockerfile"), "start": "postgres"}, "db",
             "a database needs a dockerfile, not start"),
            ("database no dockerfile", without(DATABASE, "dockerfile"), "db", "a database needs dockerfile"),
            ("database no health", without(DATABASE, "health"), "db", "a database needs health"),
            ("database health path", {**DATABASE, "health": "/health"}, "db", "a database's health must be a command"),
            ("database port 0", {**DATABASE, "port": 0}, "db", "port must be an integer"),
            ("database no port", without(DATABASE, "port"), "db",
             "a database needs port, the container port it accepts connections on"),
            ("service id localhost", SERVICE, "localhost",
             "a service cannot have the id 'localhost': inside every container that name means the container itself"),
            ("worker id ip6-localhost", WORKER, "ip6-localhost", "a worker cannot have the id 'ip6-localhost'"),
            ("database id ip6-loopback", DATABASE, "ip6-loopback", "a database cannot have the id 'ip6-loopback'"),
            ("service id ip6-allrouters", SERVICE, "ip6-allrouters", "cannot have the id 'ip6-allrouters'"),
        ]
        for name, block, component_id, message in cases:
            with self.subTest(name):
                with self.assertRaises(ValueError) as caught:
                    load(block, component_id)
                self.assertIn(message, str(caught.exception))

    def test_a_misspelled_runtime_key_is_caught_and_other_row_keys_are_ignored(self):
        for key in ("runtme", "run_time", "runtimes", "Runtime"):
            with self.subTest(key):
                self.assertEqual(key, runtime.near_miss_runtime_key({"id": "a", key: {"kind": "library"}}))
        self.assertIsNone(runtime.near_miss_runtime_key(
            {"id": "a", "runtime": {}, "requirements": [], "ui_run": "x", "routine": 1, "notes": ""}))


class StartOrderTests(unittest.TestCase):
    def test_layers_follow_runtime_dependencies(self):
        chain = runtimes(a={**SERVICE, "runtime_depends_on": ["b"]}, b={**SERVICE, "runtime_depends_on": ["c"]},
                         c=SERVICE, lib={"kind": "library"}, plain=None)
        self.assertEqual([["c"], ["b"], ["a"]], runtime.start_layers(chain))
        diamond = runtimes(top={**SERVICE, "runtime_depends_on": ["right", "left"]},
                           left={**SERVICE, "runtime_depends_on": ["db"]},
                           right={**SERVICE, "runtime_depends_on": ["db"]},
                           db=DATABASE, mailer={**WORKER, "runtime_depends_on": ["db"]})
        self.assertEqual([["db"], ["left", "mailer", "right"], ["top"]], runtime.start_layers(diamond))
        self.assertEqual([], runtime.start_layers(runtimes(lib={"kind": "library"}, plain=None)))

    def test_bad_runtime_dependencies_are_refused(self):
        cases = [
            ("unknown", runtimes(a={**SERVICE, "runtime_depends_on": ["ghost"]}),
             "a runtime_depends_on unknown component 'ghost'"),
            ("no block", runtimes(a={**SERVICE, "runtime_depends_on": ["b"]}, b=None),
             "a runtime_depends_on b, which declares no runtime block"),
            ("library", runtimes(a={**SERVICE, "runtime_depends_on": ["b"]}, b={"kind": "library"}),
             "a runtime_depends_on b, a library, which is never started"),
            ("worker", runtimes(a={**SERVICE, "runtime_depends_on": ["b"]}, b=WORKER),
             "a runtime_depends_on b, a worker, which has no address"),
            ("self", runtimes(a={**SERVICE, "runtime_depends_on": ["a"]}), "a lists itself in runtime_depends_on"),
            ("cycle", runtimes(a={**SERVICE, "runtime_depends_on": ["b"]}, b={**SERVICE, "runtime_depends_on": ["c"]},
                               c={**SERVICE, "runtime_depends_on": ["a"]}, d=SERVICE),
             "runtime dependency cycle among ['a', 'b', 'c']"),
        ]
        for name, blocks, message in cases:
            with self.subTest(name):
                with self.assertRaises(ValueError) as caught:
                    runtime.start_layers(blocks)
                self.assertIn(message, str(caught.exception))


class EnvironmentTests(unittest.TestCase):
    def test_variable_names_and_generated_environment(self):
        self.assertEqual("LINK_API_URL", runtime.url_variable("link-api"))
        self.assertEqual("STORE_URL", runtime.url_variable("store"))
        blocks = runtimes(store=SERVICE, gateway=GATEWAY, db=DATABASE,
                          mailer={**WORKER, "runtime_depends_on": ["db", "store"], "env": {"MODE": "batch"}},
                          cache={**DATABASE, "port": 6379}, reader={**SERVICE, "runtime_depends_on": ["cache"]})
        self.assertEqual({"PORT": "8002", "STORE_URL": "http://store:8001", "GREETING": "hello"},
                         runtime.service_environment("gateway", blocks))
        self.assertEqual(["PORT", "STORE_URL", "GREETING"], list(runtime.service_environment("gateway", blocks)))
        self.assertEqual({"DB_HOST": "db", "DB_PORT": "5432", "STORE_URL": "http://store:8001", "MODE": "batch"},
                         runtime.service_environment("mailer", blocks))
        self.assertEqual({"PORT": "5432"}, runtime.service_environment("db", blocks))
        self.assertEqual({"PORT": "8001", "CACHE_HOST": "cache", "CACHE_PORT": "6379"},
                         runtime.service_environment("reader", blocks))

    def test_running_the_system_needs_every_component_declared_and_a_service(self):
        runtime.require_runnable(["store", "lib"], runtimes(store=SERVICE, lib={"kind": "library"}))
        with self.assertRaises(ValueError) as caught:
            runtime.require_runnable(["store", "gateway", "lib"], runtimes(store=SERVICE, gateway=None))
        self.assertIn("missing on gateway, lib", str(caught.exception))
        self.assertIn('{"kind": "library"}', str(caught.exception))
        with self.assertRaisesRegex(ValueError, "at least one component of kind service"):
            runtime.require_runnable(["lib", "db"], runtimes(lib={"kind": "library"}, db=DATABASE))


class ArchitectureTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="component-runtime-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def test_a_record_without_runtime_blocks_keeps_its_brief_and_identity(self):
        directory = write_architecture(self.root / "notes", notes_rows())
        arch = mc.Architecture.load(directory)
        self.assertEqual(LEGACY_STORE_BRIEF, mc.component_brief(arch.components["store"], arch))
        self.assertEqual(LEGACY_GATEWAY_BRIEF, mc.component_brief(arch.components["gateway"], arch))
        self.assertEqual(LEGACY_FINGERPRINT, arch.fingerprint())
        self.assertEqual({}, arch.runtimes)
        self.assertIsNone(arch.components["store"].runtime)
        bare = write_architecture(self.root / "bare", notes_rows(), contracts=False)
        legacy = hashlib.sha256(b"components.json\0" + (bare / "components.json").read_bytes() + b"\0").hexdigest()
        self.assertEqual(legacy, mc.Architecture.load(bare).fingerprint())

    def test_runtime_blocks_load_into_the_record_and_change_its_identity(self):
        rows = notes_rows()
        rows[0]["runtime"] = SERVICE
        rows[1]["runtime"] = GATEWAY
        directory = write_architecture(self.root / "notes", rows)
        arch = mc.Architecture.load(directory)
        self.assertEqual(load(SERVICE), arch.components["store"].runtime)
        self.assertEqual({"store", "gateway"}, set(arch.runtimes))
        self.assertNotEqual(LEGACY_FINGERPRINT, arch.fingerprint())
        first = arch.fingerprint()
        rows[1]["runtime"] = {**GATEWAY, "port": 8003}
        write_architecture(directory, rows)
        self.assertNotEqual(first, mc.Architecture.load(directory).fingerprint())

    def test_invalid_blocks_are_refused_at_load_with_the_component_named(self):
        cases = [
            ("bad block", "runtime", {**GATEWAY, "health": "health"}, "component gateway runtime: a service's health"),
            ("misspelled key", "runtme", {"kind": "library"},
             "component gateway: did you mean runtime? (found 'runtme')"),
            ("unknown dependency", "runtime", {**GATEWAY, "runtime_depends_on": ["ghost"]},
             "runtime: gateway runtime_depends_on unknown component 'ghost'"),
            ("dependency without a block", "runtime", GATEWAY,
             "runtime: gateway runtime_depends_on store, which declares no runtime block"),
        ]
        for name, key, block, message in cases:
            with self.subTest(name):
                rows = notes_rows()
                rows[1][key] = block
                directory = write_architecture(self.root / name.replace(" ", "-"), rows)
                with self.assertRaises(mc.ArchitectureError) as caught:
                    mc.Architecture.load(directory)
                self.assertIn(message, str(caught.exception))


class BriefTests(unittest.TestCase):
    def blocks(self):
        return runtimes(store=SERVICE, gateway=GATEWAY, db=DATABASE, lib={"kind": "library"},
                        mailer={**WORKER, "health": ["python3", "check.py"], "runtime_depends_on": ["db", "store"]},
                        cache={**DATABASE, "port": 6379}, reader={**SERVICE, "runtime_depends_on": ["cache"]})

    def architecture(self, directory):
        """The notes record with a runtime block on each component: store publishes the
        note contract and gateway consumes it, as most multi-component builds do."""
        rows = notes_rows()
        rows[0]["runtime"] = SERVICE
        rows[1]["runtime"] = GATEWAY
        return mc.Architecture.load(write_architecture(Path(directory), rows))

    def test_a_service_is_told_how_it_runs_and_how_to_reach_its_dependencies(self):
        with tempfile.TemporaryDirectory() as tmp:
            arch = self.architecture(tmp)
            brief = mc.component_brief(arch.components["gateway"], arch)
        self.assertTrue(brief.startswith("Implement the gateway component"))
        self.assertTrue(brief.endswith(" Do not implement or stub another component's directory; integration happens "
                                       "separately."))
        for text in ("listen on 0.0.0.0", "PORT environment variable (which will be 8002)", "answer GET /health",
                     "STORE_URL", "http://store:8001", 'the shell command "python3 server.py" in /app',
                     "python:3.12-slim", "GREETING=hello"):
            self.assertIn(text, brief)
        # The runtime sentences follow the ownership line, which ends in a full stop, and
        # come before the contract lines; without them the brief is the legacy one.
        added = " ".join(runtime.brief_lines("gateway", arch.runtimes))
        self.assertTrue(brief.split(" When the combined system runs", 1)[0].endswith(
            "Own only the directory components/gateway/; do not create or edit any file outside it."))
        self.assertEqual(LEGACY_GATEWAY_BRIEF, brief.replace(" " + added, "", 1))

    def test_each_kind_is_described_accurately(self):
        blocks = self.blocks()
        store = " ".join(runtime.brief_lines("store", blocks))
        self.assertIn("long-lived HTTP service", store)
        self.assertIn("Provide components/store/Dockerfile", store)
        self.assertIn("its image must start the service", store)
        worker = " ".join(runtime.brief_lines("mailer", blocks))
        self.assertIn("long-running worker process", worker)
        self.assertIn("with no HTTP port", worker)
        self.assertIn("must keep running until it is stopped", worker)
        self.assertIn('the health check command ["python3", "check.py"], run inside its container, must exit', worker)
        self.assertIn("DB_HOST environment variable (it will be db)", worker)
        self.assertIn("DB_PORT environment variable (it will be 5432)", worker)
        self.assertNotIn("0.0.0.0", worker)
        database = " ".join(runtime.brief_lines("db", blocks))
        self.assertIn("runs as a database", database)
        self.assertIn("accept connections on port 5432", database)
        self.assertIn('Once it accepts connections, the health check command ["pg_isready", "-U", "app"]', database)
        self.assertIn("its image must start the database", database)
        self.assertIn("accept connections on port 6379", " ".join(runtime.brief_lines("cache", blocks)))
        self.assertIn("CACHE_HOST environment variable (it will be cache) and the port in the CACHE_PORT environment "
                      "variable (it will be 6379); never hard-code that host or port.",
                      " ".join(runtime.brief_lines("reader", blocks)))
        self.assertEqual(["When the combined system runs, this component is not started on its own."],
                         runtime.brief_lines("lib", blocks))
        self.assertEqual([], runtime.brief_lines("store", {}))

    def test_the_brief_states_non_ascii_commands_as_they_run(self):
        blocks = runtimes(mailer={**WORKER, "start": "python3 café.py", "health": ["python3", "prüf.py"]})
        worker = " ".join(runtime.brief_lines("mailer", blocks))
        self.assertIn('the container runs the shell command "python3 café.py" in /app', worker)
        self.assertIn('the health check command ["python3", "prüf.py"], run inside its container', worker)
        self.assertNotIn("\\u", worker)
        self.assertEqual(1, len(worker.splitlines()))

    def test_runtime_sentences_are_traced_obligations_without_literals(self):
        blocks = self.blocks()
        for cid in blocks:
            with self.subTest(cid):
                lines = runtime.brief_lines(cid, blocks)
                self.assertEqual([], brief_literals.literals([" ".join(lines)]))
                self.assertNotIn("`", " ".join(lines))
        for cid in ("mailer", "db"):
            with self.subTest(f"{cid} opening sentence is a cue sentence"):
                opening = runtime.brief_lines(cid, blocks)[0]
                self.assertIn(opening, cues.cue_sentences(" ".join(runtime.brief_lines(cid, blocks))))

    def test_a_contract_bearing_component_must_trace_its_runtime_sentence(self):
        # In the real brief, beside a contract line that ends in raw JSON: the service
        # sentence stays its own cue sentence, so a requirements handoff that quotes
        # everything else, the schema sentence included, is refused until it is traced.
        with tempfile.TemporaryDirectory() as tmp:
            arch = self.architecture(tmp)
            briefs = {cid: mc.component_brief(arch.components[cid], arch) for cid in ("store", "gateway")}
        for cid, brief in briefs.items():  # store publishes the note contract, gateway consumes it
            with self.subTest(cid):
                service = runtime.brief_lines(cid, arch.runtimes)[0]
                sentences = cues.cue_sentences(brief)
                self.assertIn(service, sentences)
                others = [sentence for sentence in sentences if sentence != service]
                self.assertTrue(any("JSON Schema" in sentence for sentence in others), others)
                report = {"requirements": [{"id": f"R{index}", "text": quote, "source_quote": quote}
                                           for index, quote in enumerate(others, 1)], "ignored_statements": []}
                with self.assertRaises(ValueError) as caught:
                    goals.check_requirement_handoff({"task": brief}, report)
                self.assertIn("neither quoted nor explicitly ignored", str(caught.exception))
                self.assertIn("listen on 0.0.0.0", str(caught.exception))
                report["ignored_statements"] = [service]
                goals.check_requirement_handoff({"task": brief}, report)


class CliRefusalTests(unittest.TestCase):
    """`autocode components` refuses a bad block before any component starts."""

    def test_invalid_runtime_blocks_are_usage_errors(self):
        for name, key, block in (("invalid block", "runtime", {**SERVICE, "port": 0}),
                                 ("misspelled key", "runtme", SERVICE)):
            with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
                rows = notes_rows()
                rows[0][key] = block
                write_architecture(repo / "architecture", rows)
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as caught:
                    autocode_components.cli(["architecture", "--workspace", str(repo)])
                self.assertEqual(2, caught.exception.code)
                self.assertTrue(stderr.getvalue().startswith("usage:"), stderr.getvalue())
                self.assertIn("component store", stderr.getvalue())
                self.assertFalse((repo / ".autocode-components").exists())


if __name__ == "__main__":
    unittest.main()
