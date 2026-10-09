"""tools/autocode_local_run.py: starting, smoke-checking and tearing down the combined system.

Docker is never needed: docker is a FakeDocker runner that records each command and
reports container states, services are real stdlib HTTP servers on 127.0.0.1 standing
in for containers, and waiting uses a fake clock. The CLI-level path with a fake
`docker` executable is CliTests.test_cli_runs_the_integrated_system_locally in
test_multicomponent; the refusals before any build are here.
"""
import contextlib
import io
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import unittest
import psutil
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import autocode_components
import autocode_local_run as lr
import autocode_multicomponent as mc
from autocode_component_runtime import ComponentRuntime
from autocode_compose_file import compose_document, render

FAKE_DOCKER = Path(__file__).resolve().parents[1] / "tools" / "fixtures" / "fake_docker.py"


def service(port=8000, health="/health", depends=(), start="python3 server.py"):
    return ComponentRuntime(kind="service", port=port, start=start, health=health,
                            runtime_depends_on=tuple(depends))


def database():
    return ComponentRuntime(kind="database", port=5432, dockerfile="Dockerfile", health_command=("pg_isready",))


def worker(depends=()):
    return ComponentRuntime(kind="worker", start="python3 work.py", runtime_depends_on=tuple(depends))


class FakeClock:
    def __init__(self):
        self.time = 0.0
        self.slept = 0.0

    def now(self):
        return self.time

    def sleep(self, seconds):
        if self.time > 10_000:
            raise AssertionError("waited forever on the fake clock")
        self.time += seconds
        self.slept += seconds


class FakeDocker:
    """Records every command. A service is running once an `up` started it; ``health``
    scripts what each `ps` reports as its Health, one entry per poll (the last repeats)."""

    def __init__(self, ports, health=None, states=None, fail_up=False):
        self.calls, self.ports, self.fail_up = [], ports, fail_up
        self.health = {cid: list(script) for cid, script in (health or {}).items()}
        self.states = states or {}
        self.started = []

    def __call__(self, argv, timeout):
        self.calls.append(list(argv))
        command = argv[6] if argv[:2] == ["docker", "compose"] else argv[1]
        args = argv[7:]
        if command == "up":
            if self.fail_up:
                return lr.CommandResult(1, "", "failed to solve: Dockerfile not found")
            self.started += [arg for arg in args if not arg.startswith("-")]
            return lr.CommandResult(0)
        if command == "ps":
            rows = []
            for cid in self.started:
                script = self.health.get(cid, [""])
                health = script.pop(0) if len(script) > 1 else script[0]
                rows.append({"Service": cid, "State": self.states.get(cid, "running"), "Health": health})
            return lr.CommandResult(0, "".join(json.dumps(row) + "\n" for row in rows))
        if command == "port":
            return lr.CommandResult(0, f"127.0.0.1:{self.ports[args[0]]}\n")
        if command == "logs":
            return lr.CommandResult(0, f"{args[-1]}-1  | boom\n")
        if command == "down":
            return lr.CommandResult(0)
        return lr.CommandResult(0, "ok")

    def commands(self):
        return [call[6] for call in self.calls if call[:2] == ["docker", "compose"]]


class NotesHandler(BaseHTTPRequestHandler):
    """A tiny notes service: /health, POST /notes, GET /notes/<id>."""

    def log_message(self, *args):
        pass

    def reply(self, status, document):
        body = json.dumps(document).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.server.seen.append(("GET", self.path))
        if self.path == "/health":
            return self.reply(self.server.health_status, {"ok": True})
        if self.path.startswith("/notes/") and self.path[7:] in self.server.notes:
            return self.reply(200, self.server.notes[self.path[7:]])
        return self.reply(404, {"error": "not found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        document = json.loads(self.rfile.read(length))
        self.server.seen.append(("POST", self.path, document))
        note_id = len(self.server.notes) + 7
        self.server.notes[str(note_id)] = {"id": note_id, **document}
        return self.reply(201, {"id": note_id, **document})


def start_server(test, health_status=200):
    server = ThreadingHTTPServer(("127.0.0.1", 0), NotesHandler)
    server.seen, server.notes, server.health_status = [], {}, health_status
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    test.addCleanup(server.server_close)
    test.addCleanup(server.shutdown)
    return server


def smoke(*steps):
    return {"version": 1, "steps": list(steps)}


NOTE_STEPS = (
    {"name": "create", "service": "api", "method": "POST", "path": "/notes", "body": {"text": "hello"},
     "expect_status": 201, "expect_json": {"text": "hello"}, "capture": {"note_id": "id"}},
    {"name": "read back", "service": "api", "method": "GET", "path": "/notes/{{note_id}}",
     "expect_status": 200, "expect_json": {"text": "hello"}},
)


class Workspace:
    def __init__(self, test, runtimes, smoke_document):
        temp = tempfile.TemporaryDirectory(prefix="local-run-")
        test.addCleanup(temp.cleanup)
        root = Path(temp.name).resolve()
        self.architecture, self.tree, self.workdir = root / "architecture", root / "tree", root / "work"
        self.architecture.mkdir()
        if smoke_document is not None:
            (self.architecture / "smoke.json").write_text(json.dumps(smoke_document))
        for cid, runtime in runtimes.items():
            if runtime is not None and runtime.runs:
                (self.tree / "components" / cid).mkdir(parents=True)
                if runtime.dockerfile:
                    (self.tree / "components" / cid / runtime.dockerfile).write_text("FROM scratch\n")
        self.runtimes = runtimes

    def run(self, docker, **options):
        plan = lr.prepare(self.architecture, self.runtimes)
        out = io.StringIO()
        local = lr.LocalRun(plan, self.tree, self.workdir, runner=docker, clock=options.pop("clock", FakeClock()),
                            out=out, project="autocode-test", **options)
        summary = local.run()
        return summary, out.getvalue()


class PrepareTests(unittest.TestCase):
    def test_every_component_needs_a_runtime_block(self):
        ws = Workspace(self, {"api": service(), "lib": None}, smoke(NOTE_STEPS[1] | {"path": "/x"}))
        with self.assertRaisesRegex(ValueError, "missing on lib"):
            lr.prepare(ws.architecture, ws.runtimes)

    def test_a_service_is_needed(self):
        ws = Workspace(self, {"jobs": worker()}, smoke(NOTE_STEPS[1] | {"path": "/x"}))
        with self.assertRaisesRegex(ValueError, "at least one component of kind service"):
            lr.prepare(ws.architecture, ws.runtimes)

    def test_smoke_json_is_needed(self):
        ws = Workspace(self, {"api": service()}, None)
        with self.assertRaisesRegex(ValueError, "missing .*smoke.json"):
            lr.prepare(ws.architecture, ws.runtimes)

    def test_a_smoke_step_must_go_to_a_service(self):
        ws = Workspace(self, {"api": service(depends=["db"]), "db": database()},
                       smoke({"service": "db", "method": "GET", "path": "/", "expect_status": 200}))
        with self.assertRaisesRegex(ValueError, "a database, whose port is never published"):
            lr.prepare(ws.architecture, ws.runtimes)

    def test_a_valid_architecture_gives_its_start_layers(self):
        ws = Workspace(self, {"api": service(depends=["db"]), "db": database(), "lib": ComponentRuntime("library")},
                       smoke(*NOTE_STEPS))
        self.assertEqual([["db"], ["api"]], lr.prepare(ws.architecture, ws.runtimes).layers)


class DockerCheckTests(unittest.TestCase):
    def test_selected_remote_context_is_refused_before_daemon_contact(self):
        calls = []
        def runner(argv, timeout):
            calls.append(argv)
            return lr.CommandResult(0, "2.29.0" if argv[1] == "compose" else "ssh://remote")
        with self.assertRaises(lr.DockerUnavailable):
            lr.check_docker(runner, env={"DOCKER_HOST": "unix:///ignored", "DOCKER_CONTEXT": "remote"})
        self.assertIn(["docker", "context", "inspect", "remote", "--format", "{{.Endpoints.docker.Host}}"], calls)
        self.assertFalse(any(argv[1] == "version" for argv in calls))

    def test_a_missing_docker_executable_is_a_clear_error(self):
        with self.assertRaisesRegex(lr.DockerUnavailable, "not installed or not on PATH.*Compose v2"):
            lr.subprocess_runner(["autocode-no-such-docker-here", "compose", "version"], 5)

    @staticmethod
    def runner(fail=None, compose="2.29.0", endpoint="unix:///var/run/docker.sock"):
        def run(argv, timeout):
            if argv[1:2] == ["--host"]:
                argv = [argv[0], *argv[3:]]
            if argv[1] == "compose":
                return lr.CommandResult(1, "", "nope") if fail == "compose" else lr.CommandResult(0, compose + "\n")
            if argv[1] == "version":
                return lr.CommandResult(1, "", "nope") if fail == "daemon" else lr.CommandResult(0, "27.0.0\n")
            if argv[1:3] == ["context", "inspect"]:
                return lr.CommandResult(1, "", "no context") if fail == "context" else lr.CommandResult(0, endpoint)
            raise AssertionError(argv)
        return run

    def test_missing_compose_plugin_and_unreachable_daemon_are_named(self):
        with self.assertRaisesRegex(lr.DockerUnavailable, "needs Compose v2"):
            lr.check_docker(self.runner("compose"), env={})
        with self.assertRaisesRegex(lr.DockerUnavailable, "daemon is not reachable"):
            lr.check_docker(self.runner("daemon"), env={})
        with self.assertRaisesRegex(lr.DockerUnavailable, "docker context inspect"):
            lr.check_docker(self.runner("context"), env={})
        lr.check_docker(self.runner(), env={})

    def test_compose_older_than_2_17_is_refused(self):
        for version in ("2.16.0", "v2.16.9", "1.29.2", "", "unknown"):
            with self.subTest(version=version), self.assertRaisesRegex(lr.DockerUnavailable, "Compose 2.17 or newer"):
                lr.check_docker(self.runner(compose=version), env={})
        for version in ("2.17.0", "v2.29.1-desktop.1", "5.3.1", "v2.100.0"):
            with self.subTest(version=version):
                lr.check_docker(self.runner(compose=version), env={})

    def test_a_daemon_on_another_machine_is_refused(self):
        # Ports would publish on that machine while probes and smoke requests go to 127.0.0.1 here.
        for env, endpoint in (({"DOCKER_HOST": "tcp://10.0.0.5:2376"}, "unix:///var/run/docker.sock"),
                              ({"DOCKER_HOST": "ssh://me@build-box"}, "unix:///var/run/docker.sock"),
                              ({}, "tcp://10.0.0.5:2376"), ({"DOCKER_HOST": ""}, "ssh://me@build-box\n")):
            with self.subTest(env=env, endpoint=endpoint), \
                    self.assertRaisesRegex(lr.DockerUnavailable, "needs a Docker daemon on this machine"):
                lr.check_docker(self.runner(endpoint=endpoint), env=env)
        for env, endpoint in (({"DOCKER_HOST": "unix:///run/user/1000/docker.sock"}, "tcp://ignored:1"),
                              ({}, "unix:///var/run/docker.sock\n"), ({}, "npipe:////./pipe/docker_engine")):
            with self.subTest(env=env, endpoint=endpoint):
                lr.check_docker(self.runner(endpoint=endpoint), env=env)

    def test_the_compose_directory_can_never_be_a_component_worktree(self):
        # Component worktrees are .autocode-components/<id>; the compose directory must not be one.
        with tempfile.TemporaryDirectory() as temp:
            work = lr.workdir(Path(temp))
            # workdir() resolves the workspace so /var and /private/var spellings match.
            self.assertEqual((Path(temp) / ".autocode-components" / ".local-run").resolve(), work.resolve())
            architecture = Path(temp) / "architecture"
            architecture.mkdir()
            (architecture / "components.json").write_text(json.dumps([{"id": work.name}]))
            with self.assertRaisesRegex(mc.ArchitectureError, "must be a plain name"):
                mc.Architecture.load(architecture)


class RunTests(unittest.TestCase):
    def test_preflight_endpoint_is_pinned_across_context_changes_and_recovery(self):
        socket = "unix:///validated local.sock"
        preflight_calls = []
        def preflight(argv, timeout):
            preflight_calls.append(argv)
            if argv[1] == "compose":
                return lr.CommandResult(0, "2.29.0")
            if argv[1] == "context":
                return lr.CommandResult(0, socket)
            return lr.CommandResult(0, "27.0.0")
        endpoint = lr.check_docker(preflight, env={"DOCKER_CONTEXT": "local"})
        self.assertEqual(socket, endpoint)
        self.assertEqual(["docker", "--host", socket, "version", "--format", "{{.Server.Version}}"], preflight_calls[-1])
        server = start_server(self)
        ws = Workspace(self, {"api": service()}, smoke(*NOTE_STEPS))
        docker = FakeDocker({"api": server.server_port})
        calls = []
        def runner(argv, timeout):
            calls.append(argv)
            self.assertEqual(["docker", "--host", socket], argv[:3])
            if "down" in argv:
                return lr.CommandResult(1, "", "cleanup failed")
            return docker([argv[0], *argv[3:]], timeout)
        plan = replace(lr.prepare(ws.architecture, ws.runtimes), endpoint=endpoint)
        with mock.patch.dict(os.environ, {"DOCKER_CONTEXT": "remote", "DOCKER_HOST": "ssh://remote"}):
            summary = lr.LocalRun(plan, ws.tree, ws.workdir, runner=runner, clock=FakeClock(), out=io.StringIO()).run()
        self.assertEqual("failed", summary["status"])
        self.assertTrue(all(step["ok"] for step in summary["steps"]))
        self.assertEqual(socket, summary["docker_endpoint"])
        self.assertEqual(["docker", "--host", socket], shlex.split(summary["stop_command"])[:3])
        self.assertTrue(any("up" in call for call in calls))
        self.assertTrue(any("down" in call for call in calls))

    def test_cleanup_failure_cannot_pass_and_recovery_preserves_quoted_paths(self):
        server = start_server(self)
        for failure in (lr.CommandResult(1, "", "network busy"), lr.CommandResult(124, "", "timeout"),
                        lr.DockerUnavailable("docker lost"), OSError("transport lost")):
            with self.subTest(failure=failure):
                ws = Workspace(self, {"api": service()}, smoke(*NOTE_STEPS))
                ws.workdir /= "path with spaces"
                docker = FakeDocker({"api": server.server_port})
                def runner(argv, timeout):
                    if argv[6:7] == ["down"]:
                        docker.calls.append(argv)
                        if isinstance(failure, Exception):
                            raise failure
                        return failure
                    return docker(argv, timeout)
                summary, said = ws.run(runner)
                self.assertEqual("failed", summary["status"])
                self.assertFalse(summary["torn_down"])
                self.assertFalse(summary["kept_running"])
                self.assertTrue(all(step["ok"] for step in summary["steps"]))
                self.assertIn("could not tear down", summary["cleanup_detail"])
                self.assertEqual(["docker", "compose", "-p", summary["project"], "-f", summary["compose_file"],
                                  *lr.LocalRun.DOWN], shlex.split(summary["stop_command"]))
                self.assertIn(summary["stop_command"], said)

    def test_helper_cleanup_uncertainty_survives_successful_down(self):
        ws = Workspace(self, {"api": service()}, smoke(*NOTE_STEPS))
        docker = FakeDocker({})
        def runner(argv, timeout):
            if argv[6:7] == ["up"]:
                raise lr.DockerCleanupUnavailable("owned helper cleanup unknown")
            return docker(argv, timeout)
        summary, _ = ws.run(runner)
        self.assertEqual("failed", summary["status"])
        self.assertFalse(summary["torn_down"])
        self.assertIn("owned helper", summary["cleanup_detail"])
        self.assertEqual("down", docker.commands()[-1])

    def test_unexpected_errors_and_signals_cleanup_and_restore_handlers(self):
        ws = Workspace(self, {"api": service()}, smoke(*NOTE_STEPS))
        for failure in (RuntimeError("unexpected"), KeyboardInterrupt(), SystemExit(143)):
            with self.subTest(failure=type(failure).__name__):
                docker = FakeDocker({})
                def runner(argv, timeout):
                    if argv[6:7] == ["ps"]:
                        raise failure
                    return docker(argv, timeout)
                before = signal.getsignal(signal.SIGTERM)
                if isinstance(failure, Exception):
                    summary, _ = ws.run(runner)
                    self.assertEqual("failed", summary["status"])
                else:
                    with self.assertRaises(type(failure)):
                        ws.run(runner)
                self.assertEqual("down", docker.commands()[-1])
                self.assertEqual(before, signal.getsignal(signal.SIGTERM))

    def test_timeout_and_sigterm_stop_detached_helpers_before_down_and_preserve_sentinel(self):
        import autocode_grader_process as supervisor
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                ws = Workspace(self, {"api": service()}, smoke(*NOTE_STEPS))
                pidfile = ws.workdir / "helper.pid"
                pidfile.parent.mkdir(parents=True, exist_ok=True)
                script = ("import subprocess,sys,time; from pathlib import Path; "
                          "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); "
                          f"Path({str(pidfile)!r}).write_text(str(p.pid)); time.sleep(60)")
                sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
                docker, stopped_at_down = FakeDocker({}), []
                sent = False
                real_clock = supervisor.time
                def alive():
                    try:
                        process = psutil.Process(int(pidfile.read_text()))
                        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
                    except (FileNotFoundError, psutil.NoSuchProcess):
                        return False
                def clock_tick():
                    nonlocal sent
                    if interrupted and pidfile.exists() and not sent:
                        sent = True
                        signal.raise_signal(signal.SIGTERM)
                    return real_clock.monotonic()
                def runner(argv, timeout):
                    if argv[6:7] == ["up"]:
                        return lr.subprocess_runner([sys.executable, "-c", script], 2)
                    if argv[6:7] == ["down"]:
                        stopped_at_down.append(not alive())
                    return docker(argv, timeout)
                clock = mock.Mock(wraps=real_clock)
                clock.monotonic.side_effect = clock_tick
                before = signal.getsignal(signal.SIGTERM)
                try:
                    with mock.patch.object(supervisor, "time", clock):
                        if interrupted:
                            with self.assertRaises(KeyboardInterrupt):
                                ws.run(runner)
                        else:
                            summary, _ = ws.run(runner)
                            self.assertEqual("failed", summary["status"])
                    self.assertTrue(pidfile.is_file())
                    self.assertEqual([True], stopped_at_down)
                    self.assertFalse(alive())
                    self.assertIsNone(sentinel.poll())
                    self.assertEqual(before, signal.getsignal(signal.SIGTERM))
                finally:
                    sentinel.kill()
                    sentinel.wait(timeout=5)
                    if alive():
                        process = psutil.Process(int(pidfile.read_text()))
                        process.kill()
                        process.wait(timeout=5)

    def test_layers_start_in_order_each_after_the_previous_is_ready_and_the_smoke_check_passes(self):
        server = start_server(self)
        ws = Workspace(self, {"api": service(depends=["db"]), "db": database(), "jobs": worker(depends=["api"])},
                       smoke(*NOTE_STEPS))
        docker = FakeDocker({"api": server.server_port}, health={"db": ["starting", "starting", "healthy"]})
        clock = FakeClock()
        summary, _ = ws.run(docker, clock=clock)

        self.assertEqual("passed", summary["status"], summary["detail"])
        ups = [call[7:] for call in docker.calls if call[6:7] == ["up"]]
        self.assertEqual([["-d", "--build", "--no-deps", "db"], ["-d", "--build", "--no-deps", "api"],
                          ["-d", "--build", "--no-deps", "jobs"]], ups)
        commands = docker.commands()
        # db is polled until healthy (two polls "starting") before api is started.
        self.assertEqual(["up", "ps", "ps", "ps", "up"], commands[:5])
        self.assertEqual(2.0, clock.slept)
        self.assertEqual(["db", "api", "jobs"], summary["ready"])
        self.assertEqual([True, True], [step["ok"] for step in summary["steps"]])
        self.assertEqual("/notes/7", summary["steps"][1]["path"])
        self.assertIn(("POST", "/notes", {"text": "hello"}), server.seen)
        self.assertIn(("GET", "/notes/7"), server.seen)
        self.assertEqual(["down", "-v", "--remove-orphans", "--rmi", "local"], docker.calls[-1][6:])
        self.assertTrue(summary["torn_down"])
        for call in docker.calls:
            self.assertEqual(["docker", "compose", "-p", "autocode-test", "-f", summary["compose_file"]], call[:6])

    def test_the_compose_file_is_written_outside_the_tree(self):
        server = start_server(self)
        ws = Workspace(self, {"api": service()}, smoke(*NOTE_STEPS))
        summary, _ = ws.run(FakeDocker({"api": server.server_port}))
        compose = Path(summary["compose_file"])
        self.assertEqual(ws.workdir / "autocode-test" / "compose.json", compose)
        self.assertEqual(render(compose_document(ws.runtimes, ws.tree)), compose.read_text())
        self.assertEqual(["components"], sorted(path.name for path in ws.tree.iterdir()))

    def test_a_service_that_never_answers_2xx_times_out_on_the_fake_clock_and_is_torn_down(self):
        server = start_server(self, health_status=503)
        ws = Workspace(self, {"api": service()}, smoke(*NOTE_STEPS))
        docker = FakeDocker({"api": server.server_port})
        clock = FakeClock()
        summary, said = ws.run(docker, clock=clock, health_timeout=5)

        self.assertEqual("failed", summary["status"])
        self.assertEqual("api", summary["failed_component"])
        self.assertIn("not ready within 5 s (GET /health answered 503)", summary["detail"])
        self.assertEqual(5.0, clock.slept)
        self.assertEqual([], summary["steps"])
        self.assertIn("boom", summary["logs"])
        self.assertIn(["logs", "--no-color", "--tail", "50", "api"], [call[6:] for call in docker.calls])
        self.assertIn("api-1  | boom", said)
        self.assertEqual("down", docker.commands()[-1])

    def test_an_unhealthy_or_exited_container_fails_at_once(self):
        for health, states, expected in ((["unhealthy"], {}, "db's health command reports unhealthy"),
                                         (["starting"], {"db": "exited"}, "db is not running (state exited)")):
            with self.subTest(expected=expected):
                ws = Workspace(self, {"api": service(depends=["db"]), "db": database()}, smoke(*NOTE_STEPS))
                docker = FakeDocker({}, health={"db": health}, states=states)
                clock = FakeClock()
                summary, _ = ws.run(docker, clock=clock)
                self.assertEqual(("failed", "db", expected), (summary["status"], summary["failed_component"],
                                                              summary["detail"]))
                self.assertEqual(0, clock.slept)
                self.assertEqual(1, docker.commands().count("up"))
                self.assertEqual("down", docker.commands()[-1])

    def test_the_smoke_check_stops_at_the_first_failing_step(self):
        server = start_server(self)
        ws = Workspace(self, {"api": service()}, smoke(
            NOTE_STEPS[0],
            {"name": "wrong id", "service": "api", "method": "GET", "path": "/notes/999", "expect_status": 200},
            {"name": "never sent", "service": "api", "method": "GET", "path": "/never", "expect_status": 200}))
        docker = FakeDocker({"api": server.server_port})
        summary, said = ws.run(docker)

        self.assertEqual("failed", summary["status"])
        self.assertEqual(("api", "wrong id"), (summary["failed_component"], summary["failed_step"]))
        self.assertEqual([True, False], [step["ok"] for step in summary["steps"]])
        self.assertEqual(404, summary["steps"][1]["status"])
        self.assertIn("expected status 200, got 404", summary["detail"])
        self.assertNotIn(("GET", "/never"), server.seen)
        self.assertIn("smoke step 2 (wrong id): FAILED", said)
        self.assertIn("boom", summary["logs"])
        self.assertEqual("down", docker.commands()[-1])

    def test_expect_json_and_captures_are_checked(self):
        server = start_server(self)
        for step, detail in (
                (NOTE_STEPS[0] | {"expect_json": {"text": "other"}}, "does not match expect_json"),
                (NOTE_STEPS[0] | {"capture": {"note_id": "missing"}}, "cannot capture note_id")):
            with self.subTest(detail=detail):
                ws = Workspace(self, {"api": service()}, smoke(step))
                summary, _ = ws.run(FakeDocker({"api": server.server_port}))
                self.assertEqual("failed", summary["status"])
                self.assertIn(detail, summary["detail"])

    def test_teardown_happens_on_keyboard_interrupt(self):
        server = start_server(self)
        ws = Workspace(self, {"api": service()}, smoke(*NOTE_STEPS))
        docker = FakeDocker({"api": server.server_port})
        plan = lr.prepare(ws.architecture, ws.runtimes)

        class Interrupted(lr.HttpClient):
            def request(self, method, port, path, body, timeout):
                if path != "/health":
                    raise KeyboardInterrupt
                return super().request(method, port, path, body, timeout)

        local = lr.LocalRun(plan, ws.tree, ws.workdir, runner=docker, http=Interrupted(), clock=FakeClock(),
                            out=io.StringIO())
        with self.assertRaises(KeyboardInterrupt):
            local.run()
        self.assertEqual("down", docker.commands()[-1])

    def test_a_failed_up_is_reported_and_torn_down(self):
        ws = Workspace(self, {"api": service()}, smoke(*NOTE_STEPS))
        docker = FakeDocker({}, fail_up=True)
        summary, _ = ws.run(docker)
        self.assertEqual("failed", summary["status"])
        self.assertIn("docker compose up failed for api: failed to solve", summary["detail"])
        self.assertEqual(["up", "logs", "down"], docker.commands())

    def test_a_failed_up_of_a_layer_collects_the_logs_of_every_component_in_it(self):
        ws = Workspace(self, {"api": service(), "web": service(port=8001)}, smoke(*NOTE_STEPS))
        docker = FakeDocker({}, fail_up=True)
        summary, said = ws.run(docker)
        self.assertEqual(("failed", None), (summary["status"], summary["failed_component"]))
        self.assertIn(["logs", "--no-color", "--tail", "50", "api", "web"], [call[6:] for call in docker.calls])
        self.assertIn("boom", summary["logs"])
        self.assertIn("last 50 log lines of api, web", said)

    def test_a_failed_ps_collects_the_logs_of_the_layer_being_waited_on(self):
        ws = Workspace(self, {"api": service(), "web": service(port=8001)}, smoke(*NOTE_STEPS))
        docker = FakeDocker({})

        def runner(argv, timeout):
            if argv[6:7] == ["ps"]:
                docker.calls.append(list(argv))
                return lr.CommandResult(1, "", "Error response from daemon")
            return docker(argv, timeout)
        summary, _ = ws.run(runner)
        self.assertIn("docker compose ps failed: Error response from daemon", summary["detail"])
        self.assertIn(["logs", "--no-color", "--tail", "50", "api", "web"], [call[6:] for call in docker.calls])
        self.assertEqual("down", docker.commands()[-1])

    def test_a_service_that_exits_right_after_starting_is_reported_as_not_running(self):
        # Compose still shows it running at the first ps; by the time its port is asked
        # for, it has exited and `port` fails.
        ws = Workspace(self, {"api": service()}, smoke(*NOTE_STEPS))
        docker = FakeDocker({})

        def runner(argv, timeout):
            if argv[6:7] == ["port"]:
                docker.calls.append(list(argv))
                docker.states["api"] = "exited"
                return lr.CommandResult(1, "", 'service "api" is not running')
            return docker(argv, timeout)
        summary, _ = ws.run(runner)
        self.assertEqual(("failed", "api", "api is not running (state exited)"),
                         (summary["status"], summary["failed_component"], summary["detail"]))
        self.assertIn("boom", summary["logs"])

    def test_keep_running_leaves_the_system_up_and_says_how_to_stop_it(self):
        server = start_server(self)
        ws = Workspace(self, {"api": service()}, smoke(*NOTE_STEPS))
        docker = FakeDocker({"api": server.server_port})
        summary, said = ws.run(docker, keep_running=True)
        self.assertEqual("passed", summary["status"])
        self.assertNotIn("down", docker.commands())
        self.assertFalse(summary["torn_down"])
        self.assertIn("down -v --remove-orphans --rmi local", summary["stop_command"])
        self.assertIn(summary["stop_command"], said)

    def test_a_missing_dockerfile_is_refused_before_starting_anything(self):
        ws = Workspace(self, {"api": service(depends=["db"]), "db": database()}, smoke(*NOTE_STEPS))
        (ws.tree / "components" / "db" / "Dockerfile").unlink()
        docker = FakeDocker({})
        summary, _ = ws.run(docker)
        self.assertEqual("failed", summary["status"])
        self.assertIn("its dockerfile", summary["detail"])
        self.assertEqual([], docker.calls)

    def test_ps_output_as_one_json_array_is_read_too(self):
        server = start_server(self)
        ws = Workspace(self, {"api": service()}, smoke(*NOTE_STEPS))
        docker = FakeDocker({"api": server.server_port})

        def runner(argv, timeout):
            result = docker(argv, timeout)
            if argv[6:7] == ["ps"]:
                rows = [json.loads(line) for line in result.stdout.splitlines()]
                return lr.CommandResult(0, json.dumps(rows))
            return result
        summary, _ = ws.run(runner)
        self.assertEqual("passed", summary["status"], summary["detail"])


class MatchTests(unittest.TestCase):
    def test_objects_match_on_their_keys_and_everything_else_must_be_equal(self):
        self.assertTrue(lr.matches({"a": 1}, {"a": 1, "b": 2}))
        self.assertTrue(lr.matches({"a": {"b": [1, 2]}}, {"a": {"b": [1, 2], "c": 3}}))
        self.assertFalse(lr.matches({"a": 1}, {"b": 1}))
        self.assertFalse(lr.matches([{"a": 1}], [{"a": 1, "b": 2}]))
        self.assertFalse(lr.matches([1, 2], [1, 2, 3]))
        self.assertFalse(lr.matches(True, 1))
        self.assertFalse(lr.matches(1, True))
        self.assertFalse(lr.matches("1", 1))
        self.assertFalse(lr.matches(None, 0))
        self.assertTrue(lr.matches(None, None))
        self.assertTrue(lr.matches(1, 1.0))


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.test", *args], cwd=cwd, check=True,
                   capture_output=True)


class CliRefusalTests(unittest.TestCase):
    """`autocode components ... --run-local` refuses before building any component."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="local-run-cli-")
        self.addCleanup(temp.cleanup)
        self.repo = Path(temp.name).resolve()
        git(self.repo, "init", "-q")
        self.arch = self.repo / "architecture"
        (self.arch / "contracts").mkdir(parents=True)
        (self.arch / "components.json").write_text(json.dumps([
            {"id": "api", "description": "the api", "requirements": ["R1"], "depends_on": [],
             "runtime": {"kind": "service", "port": 8000, "start": "python3 server.py", "health": "/health"}}]))
        (self.arch / "smoke.json").write_text(json.dumps(smoke(*NOTE_STEPS)))
        self.bindir = self.repo / "bin"
        self.bindir.mkdir()

    def cli(self, *args, path=None):
        stderr = io.StringIO()
        with mock.patch.dict(os.environ, {"PATH": str(path or self.bindir)}), \
                contextlib.redirect_stderr(stderr), self.assertRaises(SystemExit) as stop:
            autocode_components.cli(["architecture", "--workspace", str(self.repo), *args])
        self.assertEqual(2, stop.exception.code)
        self.assertFalse((self.repo / ".autocode-components").exists())
        return stderr.getvalue()

    def test_run_local_needs_integrate(self):
        self.assertIn("--run-local needs --integrate TARGET", self.cli("--run-local"))

    def test_keep_running_needs_run_local(self):
        self.assertIn("--keep-running needs --run-local", self.cli("--integrate", "out", "--keep-running"))

    def test_health_timeout_needs_run_local(self):
        self.assertIn("--health-timeout needs --run-local", self.cli("--integrate", "out", "--health-timeout", "5"))

    def test_a_health_timeout_that_is_not_a_positive_finite_number_is_refused(self):
        for value in ("nan", "inf", "-inf", "0", "-1"):
            with self.subTest(value=value):
                self.assertIn("--health-timeout must be a positive, finite number",
                              self.cli("--integrate", "out", "--run-local", f"--health-timeout={value}"))

    def test_a_remote_docker_daemon_is_refused(self):
        fake = self.bindir / "docker"
        fake.write_text(f"#!{sys.executable}\n" + FAKE_DOCKER.read_text())
        fake.chmod(0o755)
        log = self.repo / "docker.jsonl"
        with mock.patch.dict(os.environ, {"FAKE_DOCKER_LOG": str(log), "DOCKER_HOST": "tcp://10.0.0.5:2376"}):
            said = self.cli("--integrate", "out", "--run-local")
        self.assertIn("needs a Docker daemon on this machine", said)
        self.assertIn("tcp://10.0.0.5:2376", said)

    def test_a_missing_smoke_check_is_refused(self):
        (self.arch / "smoke.json").unlink()
        self.assertIn("missing", self.cli("--integrate", "out", "--run-local"))

    def test_missing_docker_is_refused_with_a_clear_error(self):
        said = self.cli("--integrate", "out", "--run-local")
        self.assertIn("docker is not installed or not on PATH", said)

    def test_an_unreachable_daemon_is_refused(self):
        fake = self.bindir / "docker"
        fake.write_text("#!/bin/sh\nif [ \"$1\" = --host ]; then shift 2; fi\nif [ \"$1\" = version ]; then echo 'Cannot connect to the Docker daemon' >&2; "
                        "exit 1; fi\nif [ \"$1\" = context ]; then echo unix:///var/run/docker.sock; exit 0; fi\necho 2.29.0\n")
        fake.chmod(0o755)
        said = self.cli("--integrate", "out", "--run-local")
        self.assertIn("daemon is not reachable", said)
        self.assertIn("Cannot connect to the Docker daemon", said)


if __name__ == "__main__":
    unittest.main()
