"""tools/autocode_component_runtime.py: a component's runtime block is validated
strictly, ordered, and turned into Builder brief sentences; records without one
keep their original brief prefix and saved-build identity. Pure: no docker, no model."""
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
CHILD_FLOW_BOUNDARY = (
    "The child's mandatory end_to_end_flow must be executable and verified in this component run: "
    "start this component locally, exercise its public interface, and check its result. "
    "Keep a service's real subprocess health and request/response flow local to this component. "
    "The integration owner separately builds containers, runs Compose and checks cross-service smoke; "
    "do not put those deferred integration operations in the child's mandatory flow. "
    "Deliver the component's Dockerfile when requested, without requiring an image build in a child "
    "whose permissions prohibit it. Read-only design and interface inputs outside the owned directory "
    "remain inputs, not deliverable or affected paths.")
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
    def test_architecture_prompt_uses_the_validated_runtime_and_smoke_grammar(self):
        contract = runtime.architecture_contract('Design architecture/components.json with runtime blocks and smoke.json')
        self.assertEqual(runtime.START_IMAGE, contract['start_image'])
        self.assertEqual({kind: sorted({'kind', *keys}) for kind, keys in runtime.KIND_KEYS.items()}, contract['runtime_kinds'])
        self.assertEqual(list(runtime.STEP_KEYS), contract['smoke_step_keys'])
        self.assertIn('cross-component write/read', contract['instruction'])
        self.assertEqual({}, runtime.architecture_contract('Design architecture/components.json only'))

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
            # json.loads gives a lone surrogate for "\ud800"; no file can carry it as UTF-8 and Compose refuses its escape.
            ("env lone surrogate", {**SERVICE, "env": {"N": json.loads('"a\\ud800"')}}, "store",
             "env N must not contain a lone surrogate"),
            ("start lone low surrogate", {**GATEWAY, "start": "echo \udc80"}, "gateway",
             "start must not contain a lone surrogate"),
            ("worker health lone surrogate", {**WORKER, "health": ["sh", "-c", "\udbff"]}, "mailer",
             "health[2] must not contain a lone surrogate"),
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
        self.assertEqual(LEGACY_STORE_BRIEF + " " + CHILD_FLOW_BOUNDARY,
                         mc.component_brief(arch.components["store"], arch))
        self.assertEqual(LEGACY_GATEWAY_BRIEF + " " + CHILD_FLOW_BOUNDARY,
                         mc.component_brief(arch.components["gateway"], arch))
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
        self.assertTrue(brief.endswith(" " + CHILD_FLOW_BOUNDARY))
        for text in ("listen on 0.0.0.0", "PORT environment variable (which will be 8002)", "answer GET /health",
                     "STORE_URL", "http://store:8001", 'the shell command "python3 server.py" in /app',
                     "python:3.12-slim", "GREETING=hello"):
            self.assertIn(text, brief)
        # The runtime sentences follow the ownership line, which ends in a full stop, and
        # come before the contract lines; without them the brief is the legacy one.
        added = " ".join(runtime.brief_lines("gateway", arch.runtimes))
        self.assertTrue(brief.split(" When the combined system runs", 1)[0].endswith(
            "Own only the directory components/gateway/; do not create or edit any file outside it."))
        self.assertEqual(LEGACY_GATEWAY_BRIEF + " " + CHILD_FLOW_BOUNDARY, brief.replace(" " + added, "", 1))

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


NOTES_SMOKE = {"version": 1, "steps": [
    {"name": "create a note through the gateway", "service": "gateway", "method": "POST", "path": "/notes",
     "body": {"text": "hello"}, "expect_status": 201, "expect_json": {"text": "hello"}, "capture": {"note_id": "id"}},
    {"name": "read it back through the gateway", "service": "gateway", "method": "GET",
     "path": "/notes/{{note_id}}", "expect_status": 200, "expect_json": {"text": "hello"}},
    {"name": "the store holds it", "service": "store", "method": "GET", "path": "/notes/{{note_id}}",
     "expect_status": 200, "expect_json": {"text": "hello"}}]}


def step(**overrides):
    return {"service": "gateway", "method": "GET", "path": "/health", "expect_status": 200, **overrides}


class SmokeTests(unittest.TestCase):
    """ARCHITECTURE/smoke.json: the declarative end-to-end check of the combined system."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="component-smoke-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)

    def load_text(self, text):
        (self.root / "smoke.json").write_text(text)
        return runtime.load_smoke(self.root)

    def load_document(self, document):
        return self.load_text(json.dumps(document))

    def test_the_notes_check_loads_with_its_captures(self):
        smoke = self.load_document(NOTES_SMOKE)
        self.assertEqual(self.root / "smoke.json", smoke.path)
        create, read, held = smoke.steps
        self.assertEqual(("create a note through the gateway", "gateway", "POST", "/notes", 201),
                         (create.name, create.service, create.method, create.path, create.expect_status))
        self.assertEqual(({"text": "hello"}, True, {"text": "hello"}, True, (("note_id", "id"),)),
                         (create.body, create.has_body, create.expect_json, create.has_expect_json, create.capture))
        self.assertEqual((None, False, ()), (read.body, read.has_body, read.capture))
        self.assertEqual("/notes/{{note_id}}", held.path)
        runtime.check_smoke(smoke, runtimes(store=SERVICE, gateway=GATEWAY))

    def test_defaults_and_explicit_nulls(self):
        smoke = self.load_document({"version": 1, "steps": [
            step(), step(method="POST", path="/echo", body=None, expect_json=None, expect_status=204)]})
        first, second = smoke.steps
        self.assertEqual(("step 1", False, False, ()), (first.name, first.has_body, first.has_expect_json,
                                                        first.capture))
        self.assertEqual(("step 2", True, None, True, None), (second.name, second.has_body, second.body,
                                                              second.has_expect_json, second.expect_json))

    def test_refused_files_name_the_step_and_the_key(self):
        many = [step(name=f"s{index}") for index in range(31)]
        cases = [
            ("not JSON", "{", "is not valid JSON"),
            ("duplicate key", '{"version": 1, "version": 1, "steps": []}', "the key 'version' appears twice"),
            ("NaN", '{"version": 1, "steps": [{"expect_status": NaN}]}', "NaN is not a JSON value"),
            ("1e999 in a body", '{"version": 1, "steps": [{"body": {"a": 1e999}}]}',
             "the number 1e999 is too large to send as JSON"),
            ("-1e999 in expect_json", '{"version": 1, "steps": [{"expect_json": [-1e999]}]}',
             "the number -1e999 is too large to send as JSON"),
            ("not an object", [NOTES_SMOKE], "smoke.json must be a JSON object"),
            ("unknown top-level key", {**NOTES_SMOKE, "stpes": []}, "unknown key 'stpes' (did you mean 'steps'?)"),
            ("version 2", {**NOTES_SMOKE, "version": 2}, "version must be 1 (found 2)"),
            ("version true", {**NOTES_SMOKE, "version": True}, "version must be 1 (found True)"),
            ("version 1.0", {**NOTES_SMOKE, "version": 1.0}, "version must be 1 (found 1.0)"),
            ("no version", {"steps": NOTES_SMOKE["steps"]}, "version must be 1 (found None)"),
            ("no steps", {"version": 1}, "steps must be a list of 1 to 30 steps"),
            ("empty steps", {"version": 1, "steps": []}, "steps must be a list of 1 to 30 steps (found 0 steps)"),
            ("31 steps", {"version": 1, "steps": many}, "(found 31 steps)"),
            ("step not an object", {"version": 1, "steps": ["GET /health"]}, "step 1 must be a JSON object"),
            ("unknown step key", {"version": 1, "steps": [step(expct_status=200)]},
             "smoke.json step 1: unknown key 'expct_status' (did you mean 'expect_status'?)"),
            ("unknown key names the step", {"version": 1, "steps": [step(name="probe", header="x")]},
             "smoke.json step 1 (probe): unknown key 'header'"),
            ("no service", {"version": 1, "steps": [{"method": "GET", "path": "/", "expect_status": 200}]},
             "step 1 needs service"),
            ("service not a string", {"version": 1, "steps": [step(service=["gateway"])]},
             "service must be the id of a component of kind service"),
            ("no method", {"version": 1, "steps": [{"service": "gateway", "path": "/", "expect_status": 200}]},
             "step 1 needs method"),
            ("method TRACE", {"version": 1, "steps": [step(method="TRACE")]},
             "method must be one of GET, POST, PUT, PATCH, DELETE (found 'TRACE')"),
            ("method lower case", {"version": 1, "steps": [step(method="get")]}, "method must be one of"),
            ("body on GET", {"version": 1, "steps": [step(body={"a": 1})]},
             "step 1 is a GET request, which sends no body"),
            ("body on DELETE", {"version": 1, "steps": [step(method="DELETE", body=None)]},
             "is a DELETE request, which sends no body"),
            ("no expect_status", {"version": 1, "steps": [{"service": "gateway", "method": "GET", "path": "/"}]},
             "step 1 needs expect_status"),
            ("bool status", {"version": 1, "steps": [step(expect_status=True)]},
             "expect_status must be an integer HTTP status from 100 to 599 (found True)"),
            ("status 99", {"version": 1, "steps": [step(expect_status=99)]}, "expect_status must be an integer"),
            ("status 600", {"version": 1, "steps": [step(expect_status=600)]}, "expect_status must be an integer"),
            ("status '200'", {"version": 1, "steps": [step(expect_status="200")]}, "expect_status must be an integer"),
            ("path without /", {"version": 1, "steps": [step(path="health")]}, "path must start with '/'"),
            ("path not a string", {"version": 1, "steps": [step(path=None)]}, "path must start with '/'"),
            ("path with ://", {"version": 1, "steps": [step(path="/x?next=http://evil")]},
             "path must be a path on the step's service, with no '://' or '@'"),
            ("path with @", {"version": 1, "steps": [step(path="/@evil")]}, "with no '://' or '@'"),
            ("path with a space", {"version": 1, "steps": [step(path="/notes /1")]}, "printable ASCII with no spaces"),
            ("path with a tab", {"version": 1, "steps": [step(path="/notes\t1")]}, "printable ASCII with no spaces"),
            ("path with a control character", {"version": 1, "steps": [step(path="/notes\x7f")]},
             "printable ASCII with no spaces"),
            ("path not ASCII", {"version": 1, "steps": [step(path="/café")]}, "printable ASCII with no spaces"),
            ("path too long", {"version": 1, "steps": [step(path="/" + "a" * 300)]}, "at most 300 characters"),
            ("variable used before it is captured", {"version": 1, "steps": [
                step(path="/notes/{{note_id}}"), step(method="POST", path="/notes", capture={"note_id": "id"})]},
             "step 1 path uses {{note_id}}, but no earlier step captures note_id"),
            ("variable used in the step that captures it", {"version": 1, "steps": [
                step(method="POST", path="/notes/{{note_id}}", capture={"note_id": "id"})]},
             "no earlier step captures note_id"),
            ("variable in a body before capture", {"version": 1, "steps": [
                step(method="POST", body={"ids": ["{{note_id}}"]})]},
             "step 1 body uses {{note_id}}, but no earlier step captures note_id"),
            ("malformed placeholder", {"version": 1, "steps": [
                step(method="POST", capture={"note_id": "id"}), step(path="/notes/{{Note_id}}")]},
             "'{{' may only open a captured variable such as {{note_id}}"),
            ("unclosed placeholder in a body", {"version": 1, "steps": [step(method="POST", body="{{note_id")]},
             "'{{' may only open a captured variable"),
            ("placeholder in expect_json", {"version": 1, "steps": [
                step(method="POST", capture={"note_id": "id"}), step(expect_json={"id": "{{note_id}}"})]},
             "step 2 expect_json must not contain '{{' (found '{{note_id}}'): captured values are filled in only "
             "in the path and in string values of the body"),
            ("uncaptured placeholder in expect_json", {"version": 1, "steps": [
                step(expect_json=[{"ids": ["{{nope}}"]}])]}, "step 1 expect_json must not contain '{{'"),
            ("placeholder in an expect_json key", {"version": 1, "steps": [step(expect_json={"{{x": 1})]},
             "step 1 expect_json must not contain '{{' (found '{{x')"),
            ("placeholder in a body key", {"version": 1, "steps": [
                step(method="POST", capture={"note_id": "id"}), step(method="POST", body={"{{note_id}}": "x"})]},
             "step 2 body key must not contain '{{' (found '{{note_id}}')"),
            ("malformed placeholder in a nested body key", {"version": 1, "steps": [
                step(method="POST", body={"a": [{"{{nope": 2}]})]}, "step 1 body key must not contain '{{'"),
            ("placeholder in a name", {"version": 1, "steps": [
                step(method="POST", capture={"note_id": "id"}), step(name="read {{note_id}}")]},
             "step 2 name must not contain '{{'"),
            ("capture not an object", {"version": 1, "steps": [step(capture=["id"])]},
             "capture must be a JSON object of variable names"),
            ("capture variable upper case", {"version": 1, "steps": [step(capture={"Note": "id"})]},
             "capture variable 'Note' must be lower case"),
            ("capture key empty", {"version": 1, "steps": [step(capture={"note_id": ""})]},
             "capture note_id must name a top-level key of the JSON response"),
            ("capture key not a string", {"version": 1, "steps": [step(capture={"note_id": 1})]},
             "capture note_id must name a top-level key"),
            ("captured twice", {"version": 1, "steps": [step(capture={"note_id": "id"}),
                                                       step(capture={"note_id": "id"})]},
             "step 2 captures note_id, which step 1 already captures"),
            ("duplicate names", {"version": 1, "steps": [step(name="probe"), step(name="probe")]},
             "steps 1 and 2 are both named 'probe'"),
            ("a name that repeats a default", {"version": 1, "steps": [step(), step(name="step 1")]},
             "steps 1 and 2 are both named 'step 1'"),
            ("empty name", {"version": 1, "steps": [step(name="")]}, "name must be a string of 1 to 80 printable"),
            ("long name", {"version": 1, "steps": [step(name="n" * 81)]}, "name must be a string of 1 to 80"),
            ("name with a line break", {"version": 1, "steps": [step(name="a\nb")]}, "name must be a string"),
        ]
        for name, document, message in cases:
            with self.subTest(name):
                with self.assertRaises(ValueError) as caught:
                    self.load_text(document if isinstance(document, str) else json.dumps(document))
                self.assertIn(message, str(caught.exception))

    def test_a_missing_file_is_refused_by_path(self):
        with self.assertRaises(ValueError) as caught:
            runtime.load_smoke(self.root)
        self.assertIn(f"missing {self.root / 'smoke.json'}", str(caught.exception))

    def test_each_step_must_address_a_service(self):
        smoke = self.load_document({"version": 1, "steps": [step(name="probe", service="target")]})
        cases = [
            ("unknown", runtimes(gateway=GATEWAY, store=SERVICE),
             "smoke.json step 1 (probe) service 'target' is not a component that declares a runtime block"),
            ("no block", runtimes(target=None), "service 'target' declares no runtime block"),
            ("library", runtimes(target={"kind": "library"}), "'target' is a library, which is never started"),
            ("worker", runtimes(target=WORKER), "'target' is a worker, which has no HTTP port"),
            ("database", runtimes(target=DATABASE), "'target' is a database, whose port is never published"),
        ]
        for name, blocks, message in cases:
            with self.subTest(name):
                with self.assertRaises(ValueError) as caught:
                    runtime.check_smoke(smoke, blocks)
                self.assertIn(message, str(caught.exception))
        runtime.check_smoke(smoke, runtimes(target=SERVICE))

    def test_render_step_fills_in_captured_values(self):
        smoke = self.load_document({"version": 1, "steps": [
            step(method="POST", path="/notes", capture={"note_id": "id", "owner": "owner"}),
            step(method="PUT", path="/notes/{{note_id}}?owner={{owner}}",
                 body={"id": "{{note_id}}", "label": "note {{note_id}} of {{owner}}", "tags": ["{{owner}}", 1],
                       "nested": {"same": "{{note_id}}"}, "count": 2, "none": None}),
            step(path="/notes/{{note_id}}")]})
        _, put, get = smoke.steps
        path, body = runtime.render_step(put, {"note_id": 42, "owner": "a b/c&d"})
        self.assertEqual("/notes/42?owner=a%20b%2Fc%26d", path)
        self.assertEqual({"id": 42, "label": "note 42 of a b/c&d", "tags": ["a b/c&d", 1], "nested": {"same": 42},
                          "count": 2, "none": None}, body)
        self.assertEqual("{{note_id}}", put.body["id"], "the step itself is never changed")
        self.assertEqual(("/notes/caf%C3%A9", None), runtime.render_step(get, {"note_id": "café"}))
        self.assertEqual(("/notes", None), runtime.render_step(smoke.steps[0], {}))
        for captured, message in (({}, "{{note_id}} was not captured"),
                                  ({"note_id": True}, "must be a string or an integer (found True)"),
                                  ({"note_id": 1.5}, "must be a string or an integer")):
            with self.subTest(message):
                with self.assertRaises(ValueError) as caught:
                    runtime.render_step(get, captured)
                self.assertIn(message, str(caught.exception))

    def test_smoke_json_is_not_part_of_the_saved_build_identity(self):
        rows = notes_rows()
        rows[0]["runtime"] = SERVICE
        rows[1]["runtime"] = GATEWAY
        directory = write_architecture(self.root / "notes", rows)
        before = mc.Architecture.load(directory).fingerprint()
        (directory / "smoke.json").write_text(json.dumps(NOTES_SMOKE))
        self.assertEqual(before, mc.Architecture.load(directory).fingerprint())
        (directory / "smoke.json").write_text(json.dumps({**NOTES_SMOKE, "steps": NOTES_SMOKE["steps"][:1]}))
        self.assertEqual(before, mc.Architecture.load(directory).fingerprint())


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
