"""Direct setup, HTTP/AWS protocol and evidence tests; no live model requests."""

from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

import autocode_connections as connections
import autocode_direct_config as config
import autocode_direct_runtime as runtime
import autocode_direct_transport as transport
import autocode_event_metrics as metrics
import autocode_providers as providers


class HttpFixture:
    def __init__(self, respond):
        self.requests = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append((dict(self.headers), body))
                code, value = respond(body)
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                if code == 307:
                    self.send_header("Location", "/credential-leak")
                self.end_headers()
                self.wfile.write(json.dumps(value).encode())

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/v1"

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def settings(endpoint="https://models.example.invalid/v1", transport_name="compatible"):
    return {
        "version": 1,
        "transport": transport_name,
        "endpoint": endpoint,
        "region": "us-east-1",
        "profile": "company",
        "roles": {
            role: "fixture/reviewer" if role in ("sol", "completion", "plan_reviewer") else "fixture/producer"
            for role in config.ROLES
        },
    }


class DirectConnectionsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        environment = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.root)})
        environment.start()
        self.addCleanup(environment.stop)

    def test_no_external_binary_still_offers_autocode_setup(self):
        with mock.patch.object(connections.shutil, "which", return_value=None):
            view = connections.view()
        native = next(row for row in view["connections"] if row["id"] == "autocode")
        self.assertEqual("setup_required", native["connection_status"])
        self.assertEqual(["connections", "setup"], native["actions"][0]["next_command"])

    def test_setup_stores_key_privately_and_never_returns_it_in_view_or_command(self):
        replies = iter(["compatible", "https://models.example.invalid/v1", "fixture/producer", "fixture/reviewer"])
        with (
            mock.patch("builtins.input", side_effect=lambda _prompt: next(replies)),
            mock.patch.object(config.os, "isatty", return_value=True),
            mock.patch.object(config.getpass, "getpass", return_value="fixture-secret"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(0, connections.cli(["setup"]))
        self.assertEqual(0o600, (config.directory() / "auth.json").stat().st_mode & 0o777)
        self.assertEqual(0o700, config.directory().stat().st_mode & 0o777)
        self.assertNotIn("fixture-secret", (config.directory() / "connection.json").read_text())
        self.assertNotIn("fixture-secret", json.dumps(connections.view()))
        facade = providers.resolve("autocode")
        argv, _, _ = facade.launch(
            "glm",
            self.root,
            self.root / ".autocode/runs/test",
            None,
            "fixture/producer",
            "",
            False,
            report=self.root / "report.json",
            planning=True,
        )
        self.assertEqual(sys.executable, argv[0])
        self.assertNotIn("fixture-secret", json.dumps(argv))
        self.assertNotIn("kilo", argv)
        self.assertIn("--planning", argv)

    def test_listing_does_not_open_auth_store_and_rotation_preserves_identity(self):
        config.save(settings(), "first-key")
        facade = providers.resolve("autocode")
        before = facade.local_settings()
        with mock.patch.object(config, "api_key", side_effect=AssertionError("metadata read auth store")):
            self.assertEqual("configured", connections.connection_view("autocode")["connection_status"])
        config.save(settings(), "rotated-key")
        self.assertEqual(before, facade.local_settings())
        config.save(settings("https://other.example.invalid/v1"), "rotated-key")
        self.assertNotEqual(before, facade.local_settings())

    def test_runtime_never_offers_an_unconfigured_model_escalation(self):
        import autocode_builder_policy as builder_policy

        config.save(settings(), "fixture-secret")
        facade = providers.resolve("autocode")
        self.assertEqual({"fixture/producer", "fixture/reviewer"}, set(facade.LISTED_MODELS))
        self.assertIsNone(builder_policy.configured(provider=facade)["strong_model"])
        with self.assertRaisesRegex(RuntimeError, "unavailable"):
            facade.check_models({"terra": {"model": "unconfigured/expensive-model"}})

    def test_missing_key_and_insecure_or_symlinked_store_fail_closed(self):
        with self.assertRaises(ValueError):
            config.save(settings())
        config.save(settings(), "fixture-secret")
        path = config.directory() / "auth.json"
        path.chmod(0o644)
        with self.assertRaises(ValueError):
            config.api_key()
        path.unlink()
        target = self.root / "secret"
        target.write_text('{"api_key":"fixture-secret"}')
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            config.api_key()

    def test_endpoint_rejects_credential_urls_and_remote_plain_http(self):
        for url in (
            "http://company.example/v1",
            "https://user:secret@company.example",
            "https://company.example/v1?api_key=secret",
            "file:///tmp/api",
            "https://company.example/#secret",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                config.save(settings(url), "fixture-secret")

    def test_api_base_url_normalizes_trailing_slash_and_rejects_malformed_metadata(self):
        config.save(settings("https://models.example.invalid/v1/"), "fixture-secret")
        self.assertEqual("https://models.example.invalid/v1", config.load()["endpoint"])
        self.assertEqual("fixture-secret", config.api_key())
        for value in (42, "https://models.example.invalid/\n"):
            with self.assertRaises(ValueError):
                config.save(settings(value), "fixture-secret")

    def test_changing_endpoint_cannot_reuse_a_key_implicitly(self):
        config.save(settings(), "fixture-secret")
        changed = settings("https://other.example.invalid/v1")
        with self.assertRaisesRegex(ValueError, "this endpoint"):
            config.save(changed)
        self.assertEqual(settings()["endpoint"], config.load()["endpoint"])
        # A concurrent older settings snapshot cannot pick up a newly saved
        # endpoint's key, either.
        old = config.load()
        config.save(changed, "other-fixture-secret")
        with self.assertRaisesRegex(ValueError, "this endpoint"):
            config.api_key(old)

    def test_cancelled_setup_returns_cleanly_without_creating_a_connection(self):
        with (
            mock.patch("builtins.input", side_effect=KeyboardInterrupt),
            contextlib.redirect_stdout(io.StringIO()) as output,
        ):
            self.assertEqual(130, connections.cli(["setup"]))
        self.assertIn("cancelled", output.getvalue())
        self.assertFalse((config.directory() / "connection.json").exists())

    def test_bedrock_setup_keeps_aws_credentials_in_aws_store(self):
        config.save(settings(transport_name="bedrock"))
        self.assertFalse((config.directory() / "auth.json").exists())
        with mock.patch.object(config.importlib.util, "find_spec", return_value=None):
            self.assertEqual("sdk_required", config.describe()["connection_status"])

    def test_edit_setup_can_rotate_key_without_replacing_role_assignments(self):
        original = settings()
        original["roles"]["astra"] = "fixture/special-planner"
        config.save(original, "original-key")
        with (
            mock.patch("builtins.input", return_value=""),
            mock.patch.object(config.os, "isatty", return_value=True),
            mock.patch.object(config.getpass, "getpass", return_value="rotated-key"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            config.setup()
        self.assertEqual(original["roles"], config.load()["roles"])
        self.assertEqual("rotated-key", config.api_key())

    def test_http_tool_roundtrip_and_real_exit_evidence_are_not_forged_by_model(self):
        def respond(body):
            self.assertEqual("fixture/producer", body["model"])
            if body["messages"][-1]["role"] == "tool":
                result = json.loads(body["messages"][-1]["content"])
                self.assertEqual(7, result["exit_code"])
                self.assertIn("ACTUAL_OUTPUT", result["output"])
                message, reason = {"role": "assistant", "content": '{"summary":"done"}'}, "stop"
            else:
                message, reason = (
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "real-command",
                                "type": "function",
                                "function": {"name": "shell", "arguments": json.dumps({"command": "fixture command"})},
                            }
                        ],
                    },
                    "tool_calls",
                )
            return 200, {
                "choices": [{"message": message, "finish_reason": reason}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 3},
            }

        server = HttpFixture(respond)
        self.addCleanup(server.close)
        config.save(settings(server.url), "fixture-secret")

        def execute(command):
            self.assertEqual("fixture command", command)
            actual = subprocess.run(
                [sys.executable, "-c", "print('ACTUAL_OUTPUT');raise SystemExit(7)"], capture_output=True, text=True
            )
            return actual.returncode, actual.stdout

        output = io.StringIO()
        report = self.root / "report.json"
        with contextlib.redirect_stdout(output):
            self.assertEqual(
                0, runtime.run(settings(server.url), "fixture-secret", "task", "fixture/producer", "", report, execute)
            )
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        normalized = providers.resolve("autocode").normalized_events(rows)
        summarized = metrics.summarize(normalized, {}, None)
        self.assertEqual(
            {"input_tokens": 20, "output_tokens": 6, "cached_input_tokens": None, "reasoning_output_tokens": None},
            summarized["provider_tokens"],
        )
        self.assertFalse(summarized["provider_tokens_partial"])
        self.assertEqual(
            7,
            next(
                row["item"]["exit_code"]
                for row in normalized
                if row.get("item", {}).get("type") == "command_execution" and row["type"] == "item.completed"
            ),
        )
        self.assertEqual({"summary": "done"}, json.loads(report.read_text()))
        self.assertNotIn("fixture-secret", output.getvalue())
        self.assertTrue(all(headers["Authorization"] == "Bearer fixture-secret" for headers, _ in server.requests))

    def test_http_auth_and_redirect_errors_do_not_retry_or_echo_secret(self):
        for status in (401, 403, 429, 307):
            with self.subTest(status=status):
                server = HttpFixture(lambda _body: (status, {"error": "fixture-secret"}))
                try:
                    with self.assertRaises(transport.TransportError) as failure:
                        transport.compatible(
                            settings(server.url), "fixture-secret", [], runtime.TOOLS, "fixture/producer", ""
                        )
                    self.assertEqual(status, failure.exception.code)
                    self.assertNotIn("fixture-secret", str(failure.exception))
                    self.assertEqual(1, len(server.requests))
                finally:
                    server.close()

    def test_bedrock_converse_uses_tool_result_and_refuses_unsupported_effort(self):
        client = mock.Mock()
        client.converse.return_value = {
            "output": {"message": {"content": [{"text": '{"summary":"done"}'}]}},
            "stopReason": "end_turn",
            "usage": {"inputTokens": 15, "outputTokens": 4},
        }
        messages = [
            {"role": "system", "content": "instructions"},
            {"role": "user", "content": "task"},
            {
                "role": "assistant",
                "tool_calls": [{"id": "x", "function": {"name": "shell", "arguments": '{"command":"true"}'}}],
            },
            {"role": "tool", "tool_call_id": "x", "content": "exit 0"},
        ]
        message, stop, usage = transport.bedrock(
            settings(transport_name="bedrock"), messages, runtime.TOOLS, "fixture/producer", "", client=client
        )
        self.assertEqual("stop", stop)
        self.assertEqual(15, usage["prompt_tokens"])
        sent = client.converse.call_args.kwargs
        self.assertEqual("x", sent["messages"][-1]["content"][0]["toolResult"]["toolUseId"])
        self.assertEqual("shell", sent["toolConfig"]["tools"][0]["toolSpec"]["name"])
        with self.assertRaises(transport.TransportError):
            transport.bedrock(
                settings(transport_name="bedrock"), [], runtime.TOOLS, "fixture/producer", "high", client=client
            )
        self.assertEqual(1, client.converse.call_count)

    def test_bedrock_error_does_not_disclose_raw_aws_response(self):
        client = mock.Mock()
        error = RuntimeError("fixture-secret")
        error.response = {"ResponseMetadata": {"HTTPStatusCode": 403}}
        client.converse.side_effect = error
        with self.assertRaises(transport.TransportError) as failure:
            transport.bedrock(settings(transport_name="bedrock"), [], runtime.TOOLS, "model", "", client=client)
        self.assertEqual(403, failure.exception.code)
        self.assertNotIn("fixture-secret", str(failure.exception))

    def test_length_and_malformed_tool_stops_never_write_completion_report(self):
        for response in (
            ({"content": '{"summary":"truncated"}'}, "length", {}),
            ({"tool_calls": []}, "tool_calls", {}),
        ):
            report = self.root / "report.json"
            with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(transport.TransportError):
                runtime.run(
                    settings(),
                    "fixture-key",
                    "task",
                    "model",
                    "",
                    report,
                    mock.Mock(),
                    complete=lambda *_args: response,
                )
            self.assertFalse(report.exists())
