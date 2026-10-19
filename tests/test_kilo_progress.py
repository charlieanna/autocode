"""Kilo native progress registration; real streams are qualified separately."""

import copy
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import autocode_process as processes
from autocode_activity import ActivityMonitor
from providers import kilocode

ACTIVITY = Path(kilocode.__file__).with_name("opencode_activity.mjs").resolve().as_uri()
REQUIREMENTS = Path(kilocode.__file__).with_name("requirements_stream.mjs").resolve().as_uri()


class KiloProgressTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(self.root)}):
            self.provider = kilocode.load("kilocode")

    def launch(self, role="glm", *, configuration=None, schema=None):
        inherited = {
            "KILO_CONFIG_CONTENT": json.dumps(configuration or {}),
            "UNCHANGED_SENTINEL": "retained",
        }
        original = copy.deepcopy(inherited)
        with patch.object(self.provider, "_preflight_environment", return_value=inherited.copy()):
            result = self.provider.launch(
                role,
                self.root,
                self.root,
                "ses_saved",
                "fixture/model",
                "medium",
                False,
                planning=True,
                schema=schema,
            )
        self.assertEqual(original, inherited, "The caller environment must remain unchanged")
        return result

    def test_ordinary_roles_register_the_real_activity_hook_without_changing_launch_authority(self):
        config = {
            "plugin": ["file:///existing.mjs"],
            "permission": {"read": "deny", "bash": "deny"},
            "agent": {"code": {"permission": {"edit": "deny"}}},
            "model": "retained/model",
        }
        for role in ("glm", "astra", "plan_reviewer", "terra", "sol", "completion"):
            with self.subTest(role=role):
                argv, env, saved = self.launch(role, configuration=config)
                expected = {**config, "plugin": ["file:///existing.mjs", ACTIVITY]}
                self.assertEqual(expected, json.loads(env["KILO_CONFIG_CONTENT"]))
                self.assertEqual("retained", env["UNCHANGED_SENTINEL"])
                self.assertEqual(
                    [
                        "kilo",
                        "run",
                        "--auto",
                        "--dir",
                        str(self.root),
                        "--model",
                        "fixture/model",
                        "--variant",
                        "medium",
                        "--format",
                        "json",
                        "--session",
                        "ses_saved",
                    ],
                    argv,
                )
                self.assertEqual({"provider": "kilocode", "sandbox": "read-only", "report": ""}, saved)

    def test_existing_hooks_and_plugin_options_are_retained_without_duplicate_activity_registration(self):
        configured = [["file:///option-plugin.mjs", {"retained": True}], ACTIVITY, ACTIVITY]
        _, env, _ = self.launch(configuration={"plugin": configured})
        self.assertEqual([configured[0], ACTIVITY], json.loads(env["KILO_CONFIG_CONTENT"])["plugin"])

    def test_requirements_display_hook_is_preserved_alongside_activity(self):
        schema = self.root / "schema.json"
        schema.write_text(json.dumps({"properties": {"assistant_reply": {"type": "string"}}}))
        _, env, _ = self.launch("requirements", configuration={"plugin": [REQUIREMENTS, ACTIVITY]}, schema=schema)
        self.assertEqual([ACTIVITY, REQUIREMENTS], json.loads(env["KILO_CONFIG_CONTENT"])["plugin"])
        _, env, _ = self.launch("requirements", schema=None)
        self.assertEqual([ACTIVITY], json.loads(env["KILO_CONFIG_CONTENT"])["plugin"])

    def test_invalid_inline_configuration_refuses_instead_of_dropping_policy(self):
        for config in (["not an object"], {"plugin": {}}, {"plugin": "not an array"}):
            with self.subTest(configuration=config), self.assertRaisesRegex(ValueError, "plugin array"):
                self.launch(configuration=config)


# This opt-in qualification uses the original Kilo CLI and an in-namespace fake
# HTTP server. Routine CI runs the deterministic tests above and the shared hook
# tests; it does not download a third-party CLI or contact a model account.
@unittest.skipUnless(
    sys.platform == "linux" and os.environ.get("AUTOCODE_KILO_LOCAL_SSE_TEST") == "1",
    "Requires opt-in Linux Kilo 7.8.3 with loopback-only networking; no model traffic",
)
class RealKiloProgressTests(unittest.TestCase):
    def setUp(self):
        links = {
            path.name: int((path / "flags").read_text().strip(), 16)
            for path in Path("/sys/class/net").iterdir()
            if (path / "flags").is_file()
        }
        self.assertEqual(
            ["lo"],
            sorted(name for name, flags in links.items() if flags & 1),
            "Run the local SSE qualification with loopback-only networking (--network none)",
        )
        artifacts = os.environ.get("BUILD_AUDIT_ARTIFACTS")
        if artifacts:
            self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + "-", dir=artifacts)).resolve()
        else:
            temporary = tempfile.TemporaryDirectory(prefix="kilo-progress-")
            self.addCleanup(temporary.cleanup)
            self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "fixture.txt").write_text("LOCAL_TOOL_RESULT_SENTINEL\n")
        self.env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"}
        for key, name in (
            ("HOME", "home"),
            ("XDG_CONFIG_HOME", "config"),
            ("XDG_DATA_HOME", "data"),
            ("XDG_CACHE_HOME", "cache"),
            ("XDG_STATE_HOME", "state"),
            ("TMPDIR", "tmp"),
        ):
            folder = self.root / name
            folder.mkdir()
            self.env[key] = str(folder)
        self.env.update(
            KILO_DISABLE_MODELS_FETCH="1",
            KILO_DISABLE_PROJECT_CONFIG="1",
            KILO_DISABLE_AUTOUPDATE="1",
            KILO_DISABLE_TELEMETRY="1",
            DO_NOT_TRACK="1",
        )
        self.kilo = shutil.which("kilo", path=self.env["PATH"])
        self.assertIsNotNone(self.kilo, "The opt-in qualification requires the installed Kilo CLI")
        version = subprocess.run(
            [self.kilo, "--version"],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=15,
            check=True,
        ).stdout.strip()
        self.assertEqual("7.8.3", version)
        self.version = version
        with patch.dict(os.environ, self.env, clear=True):
            self.provider = kilocode.load("kilocode")

    def stream(self, *, silent):
        release = threading.Event()
        entered = threading.Event()
        observations = []
        errors = []
        workspace = self.workspace

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_arguments):
                pass

            def do_POST(self):
                try:
                    request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "close")
                    self.end_headers()

                    def emit(delta, finish=None):
                        row = {
                            "id": "local-sse",
                            "object": "chat.completion.chunk",
                            "created": 1,
                            "model": "stream",
                            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                        }
                        self.wfile.write(("data: " + json.dumps(row) + "\n\n").encode())
                        self.wfile.flush()

                    # Title/helper calls, if any, stay local and do not own the
                    # controlled post-tool stream.
                    if not request.get("tools"):
                        emit({"role": "assistant", "content": "Local fixture"})
                        emit({}, "stop")
                    elif not any(row.get("role") == "tool" for row in request.get("messages", [])):
                        emit(
                            {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "local-read",
                                        "type": "function",
                                        "function": {
                                            "name": "read",
                                            "arguments": json.dumps(
                                                {
                                                    "filePath": str(workspace / "fixture.txt"),
                                                }
                                            ),
                                        },
                                    }
                                ],
                            }
                        )
                        emit({}, "tool_calls")
                    else:
                        tool_results = [row for row in request["messages"] if row.get("role") == "tool"]
                        observations.append(
                            {
                                "tool_result_present": any(
                                    "LOCAL_TOOL_RESULT_SENTINEL" in json.dumps(row) for row in tool_results
                                ),
                                "model": request["model"],
                            }
                        )
                        # Announce the controlled request before its first
                        # observable row; stdout may be consumed immediately.
                        entered.set()
                        emit({"role": "assistant"})
                        if not silent:
                            emit({"reasoning_content": "A genuine local reasoning delta"})
                        if not release.wait(30):
                            raise AssertionError("The native progress handshake did not finish")
                        if not silent:
                            emit({"reasoning_content": " and its final remainder"})
                            emit({"content": "Local fixture completed"})
                            emit({}, "stop")
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    self.close_connection = True
                except (BrokenPipeError, ConnectionResetError):
                    # The silent negative deliberately stops its owned CLI.
                    pass
                except Exception as error:
                    errors.append(repr(error))
                    entered.set()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        server.daemon_threads = True
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        configuration = {
            "enabled_providers": ["localfixture"],
            "model": "localfixture/stream",
            "small_model": "localfixture/stream",
            "permission": {"*": "deny", "read": "allow"},
            "provider": {
                "localfixture": {
                    "npm": "@ai-sdk/openai-compatible",
                    "name": "Local fixture",
                    "options": {
                        "baseURL": f"http://127.0.0.1:{server.server_port}/v1",
                        "apiKey": "dummy-local-fixture-not-a-credential",
                    },
                    "models": {
                        "stream": {
                            "name": "Local stream",
                            "reasoning": True,
                            "interleaved": {"field": "reasoning_content"},
                            "limit": {"context": 32000, "output": 1024},
                            "variants": {"medium": {}},
                        },
                    },
                },
            },
        }
        env = {**self.env, "KILO_CONFIG_CONTENT": json.dumps(configuration)}
        argv, env, _ = self.provider.launch(
            "glm",
            self.workspace,
            self.root,
            None,
            "localfixture/stream",
            "medium",
            False,
            planning=True,
            env=env,
        )
        self.assertEqual(["localfixture"], json.loads(env["KILO_CONFIG_CONTENT"])["enabled_providers"])
        rows, incoming = [], queue.Queue()
        errors_path = self.root / "stderr.txt"
        errors_handle = errors_path.open("wb")
        child = subprocess.Popen(
            argv,
            cwd=self.workspace,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=errors_handle,
            start_new_session=True,
        )
        tree = processes.ProcessTree(child.pid, lambda _rows: None)
        tree.sample()

        def read_output():
            for line in child.stdout:
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                rows.append(row)
                incoming.put(row)
            incoming.put(None)

        reader = threading.Thread(target=read_output, daemon=True)
        reader.start()
        child.stdin.write(b"Read fixture.txt, then answer with a short summary. Do not modify files.\n")
        child.stdin.close()
        events = self.root / "observed.jsonl"
        now = [0.0]
        monitor = ActivityMonitor(events, idle_seconds=300, tool_seconds=600, clock=lambda: now[0])

        def poll_at(instant, prefix=None):
            now[0] = instant
            events.write_text("".join(json.dumps(row) + "\n" for row in (rows if prefix is None else prefix)))
            return monitor.poll()

        try:
            if silent:
                self.assertTrue(entered.wait(30), errors_path.read_text(errors="replace"))
                self.assertFalse(errors, errors)
                observed = []
                while sum(row.get("type") == "step_start" for row in observed) < 2:
                    row = incoming.get(timeout=30)
                    self.assertIsNotNone(row, errors_path.read_text(errors="replace"))
                    observed.append(row)
                tree.sample()
                poll_at(0, observed)
                self.assertTrue(observations and observations[0]["tool_result_present"], observations)
                self.assertFalse(any(row.get("type") == "autocode_progress" for row in rows))
                snapshot = poll_at(300, observed)
                self.assertEqual("idle", monitor.expired()["kind"])
                self.assertEqual((300, 600), (snapshot["idle_limit_seconds"], snapshot["tool_limit_seconds"]))
                self.assertFalse(any(row["type"] == "turn.completed" for row in self.provider.normalized_events(rows)))
            else:
                observed = []
                while True:
                    row = incoming.get(timeout=30)
                    self.assertIsNotNone(row, errors_path.read_text(errors="replace"))
                    observed.append(row)
                    if row.get("type") == "autocode_progress":
                        break
                self.assertTrue(entered.is_set())
                self.assertFalse(release.is_set())
                self.assertTrue(observations and observations[0]["tool_result_present"], observations)
                self.assertFalse(errors, errors)
                tree.sample()
                # Consume the completed tool at t=0, then only the new native
                # delta at t=299. Tool completion cannot explain this refresh.
                poll_at(0, observed[:-1])
                snapshot = poll_at(299, observed)
                self.assertEqual("provider_active", snapshot["activity"])
                self.assertFalse(snapshot["process_fallback"])
                self.assertEqual(0, snapshot["active_tool_count"])
                self.assertIsNone(monitor.expired())
                self.assertFalse(any(row["type"] == "turn.completed" for row in self.provider.normalized_events(rows)))
                self.assertFalse(any(row.get("type") in ("text", "reasoning") for row in rows))
                for progress in (row for row in rows if row.get("type") == "autocode_progress"):
                    self.assertNotIn("genuine local", json.dumps(progress))
                    self.assertEqual("reasoning", progress["progress"]["kind"])
                    self.assertRegex(progress["progress"]["content_hash"], r"^[0-9a-f]{64}$")
                # Advancing fake time grants nothing without another genuine
                # delta; a quiet reachable stream still expires at the same bound.
                poll_at(599, observed)
                self.assertEqual("idle", monitor.expired()["kind"])
                release.set()
                self.assertEqual(0, child.wait(timeout=30), errors_path.read_text(errors="replace"))
                reader.join(timeout=5)
                self.assertFalse(reader.is_alive())
                normalized = self.provider.normalized_events(rows)
                self.assertTrue(any(row["type"] == "turn.completed" for row in normalized))
                self.assertTrue(any(row.get("type") == "text" for row in rows))
            self.assertEqual("LOCAL_TOOL_RESULT_SENTINEL\n", (self.workspace / "fixture.txt").read_text())
            print(
                json.dumps(
                    {
                        "kilo_version": self.version,
                        "case": "silent" if silent else "progress",
                        "local_post_tool_requests": len(observations),
                        "native_progress_rows": sum(row.get("type") == "autocode_progress" for row in rows),
                        "private_network_namespace": True,
                        "idle_limit": 300,
                        "tool_limit": 600,
                    }
                )
            )
        finally:
            release.set()
            tree.stop(child)
            child.wait(timeout=10)
            reader.join(timeout=5)
            child.stdout.close()
            errors_handle.close()
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
            events.write_text("".join(json.dumps(row) + "\n" for row in rows))
            self.assertEqual([], processes.live_processes(list(tree.known.values())))

    def test_real_kilo_exposes_native_reasoning_before_block_completion(self):
        self.stream(silent=False)

    def test_real_kilo_silent_post_tool_stream_retains_the_idle_deadline(self):
        self.stream(silent=True)


if __name__ == "__main__":
    unittest.main()
