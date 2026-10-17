"""Direct setup, provider API protocol and evidence tests; no live model requests."""

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
        self.paths = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append((dict(self.headers), body))
                owner.paths.append(self.path)
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

    def test_named_provider_setup_selects_protocol_and_does_not_import_kilo_keys(self):
        for provider in ("mimo", "zai", "openai", "claude"):
            with self.subTest(provider=provider):
                replies = iter([provider, "https://models.example.invalid/v1", "producer", "reviewer"])
                with (
                    mock.patch("builtins.input", side_effect=lambda _prompt: next(replies)),
                    mock.patch.object(config.os, "isatty", return_value=True),
                    mock.patch.object(config.getpass, "getpass", return_value="explicit-new-key"),
                    contextlib.redirect_stdout(io.StringIO()),
                ):
                    config.setup()
                self.assertEqual(provider, config.load()["api_provider"])
                self.assertEqual("anthropic" if provider == "claude" else "compatible", config.load()["transport"])
                self.assertEqual("explicit-new-key", config.api_key())
        with self.assertRaises(ValueError):
            config.save(settings(transport_name="bedrock"), "unused")

    def test_mimo_and_zai_setup_require_explicit_billing_endpoint(self):
        for provider in ("mimo", "zai"):
            replies = iter([provider, ""])
            with (
                self.subTest(provider=provider),
                mock.patch("builtins.input", side_effect=lambda _prompt: next(replies)),
                contextlib.redirect_stdout(io.StringIO()),
                self.assertRaises(ValueError),
            ):
                config.setup()
        self.assertFalse((config.directory() / "auth.json").exists())

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

    def test_named_chat_protocols_preserve_reasoning_and_tool_results(self):
        for provider in ("mimo", "zai", "openai"):
            with self.subTest(provider=provider):

                def respond(body):
                    limit = "max_tokens" if provider == "zai" else "max_completion_tokens"
                    self.assertEqual(8192, body[limit])
                    self.assertNotIn("max_completion_tokens" if provider == "zai" else "max_tokens", body)
                    if body["messages"][-1]["role"] == "tool":
                        self.assertEqual("opaque-reasoning", body["messages"][-2]["reasoning_content"])
                        result = json.loads(body["messages"][-1]["content"])
                        self.assertEqual({"exit_code": 7, "output": "actual-output"}, result)
                        message, stop = {"role": "assistant", "content": '{"summary":"done"}'}, "stop"
                    else:
                        message, stop = (
                            {
                                "role": "assistant",
                                "content": None,
                                "reasoning_content": "opaque-reasoning",
                                "tool_calls": [
                                    {
                                        "id": "tool-1",
                                        "type": "function",
                                        "function": {"name": "shell", "arguments": '{"command":"actual-command"}'},
                                    }
                                ],
                            },
                            "tool_calls",
                        )
                    return 200, {"choices": [{"message": message, "finish_reason": stop}]}

                fixture = HttpFixture(respond)
                try:
                    value = {**settings(fixture.url), "api_provider": provider}
                    execute = mock.Mock(return_value=(7, "actual-output"))
                    with contextlib.redirect_stdout(io.StringIO()):
                        runtime.run(value, "explicit-key", "task", "model", "", self.root / "report.json", execute)
                    execute.assert_called_once_with("actual-command")
                    self.assertEqual(["/v1/chat/completions"] * 2, fixture.paths)
                    for headers, _ in fixture.requests:
                        if provider == "mimo":
                            self.assertEqual("explicit-key", headers["Api-Key"])
                            self.assertNotIn("Authorization", headers)
                        else:
                            self.assertEqual("Bearer explicit-key", headers["Authorization"])
                finally:
                    fixture.close()

    def test_claude_messages_tool_roundtrip_preserves_signed_blocks_and_cache_usage(self):
        original = [
            {"type": "thinking", "thinking": "opaque thought", "signature": "signature-value"},
            {"type": "redacted_thinking", "data": "opaque-data"},
            {"type": "tool_use", "id": "call-1", "name": "shell", "input": {"command": "actual-command"}},
        ]

        def respond(body):
            self.assertEqual(8192, body["max_tokens"])
            self.assertEqual("shell", body["tools"][0]["name"])
            self.assertEqual("object", body["tools"][0]["input_schema"]["type"])
            self.assertEqual("text", body["system"][0]["type"])
            if body["messages"][-1]["content"][0]["type"] == "tool_result":
                self.assertEqual(original, body["messages"][-2]["content"])
                result = body["messages"][-1]["content"][0]
                self.assertEqual("tool_result", result["type"])
                self.assertEqual("call-1", result["tool_use_id"])
                self.assertEqual({"exit_code": 7, "output": "actual-output"}, json.loads(result["content"]))
                blocks, stop = [{"type": "text", "text": '{"summary":"done"}'}], "end_turn"
            else:
                blocks, stop = original, "tool_use"
            return 200, {
                "type": "message",
                "role": "assistant",
                "content": blocks,
                "stop_reason": stop,
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 3,
                    "cache_read_input_tokens": 5,
                    "cache_creation_input_tokens": 2,
                },
            }

        fixture = HttpFixture(respond)
        self.addCleanup(fixture.close)
        value = {**settings(fixture.url, "anthropic"), "api_provider": "claude"}
        config.save(value, "explicit-key")
        output = io.StringIO()
        execute = mock.Mock(return_value=(7, "actual-output"))
        with contextlib.redirect_stdout(output):
            runtime.run(value, "explicit-key", "task", "model", "", self.root / "report.json", execute)
        execute.assert_called_once_with("actual-command")
        rows = providers.resolve("autocode").normalized_events(
            [json.loads(line) for line in output.getvalue().splitlines()]
        )
        self.assertEqual({"input_tokens": 34, "output_tokens": 6, "cached_input_tokens": 10}, rows[-1]["usage"])
        self.assertEqual({"summary": "done"}, json.loads((self.root / "report.json").read_text()))
        self.assertEqual(["/v1/messages"] * 2, fixture.paths)
        for headers, _ in fixture.requests:
            self.assertEqual("explicit-key", headers["X-Api-Key"])
            self.assertEqual("2023-06-01", headers["Anthropic-Version"])
        self.assertNotIn("signature-value", output.getvalue())
        with self.assertRaises(transport.TransportError):
            transport.anthropic(value, "explicit-key", [], runtime.TOOLS, "model", "high")
        self.assertEqual(2, len(fixture.requests))

    def test_claude_http_errors_and_unsupported_content_fail_closed(self):
        for status in (401, 403, 429, 307):
            fixture = HttpFixture(lambda _body: (status, {"error": "fixture-secret"}))
            try:
                with self.assertRaises(transport.TransportError) as failure:
                    transport.anthropic(
                        settings(fixture.url, "anthropic"), "fixture-secret", [], runtime.TOOLS, "model", ""
                    )
                self.assertEqual(status, failure.exception.code)
                self.assertNotIn("fixture-secret", str(failure.exception))
                self.assertEqual(1, len(fixture.requests))
            finally:
                fixture.close()
        fixture = HttpFixture(
            lambda _body: (
                200,
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "server_tool_use", "id": "unsupported"}],
                    "stop_reason": "end_turn",
                },
            )
        )
        self.addCleanup(fixture.close)
        with self.assertRaises(transport.TransportError):
            transport.anthropic(settings(fixture.url, "anthropic"), "key", [], runtime.TOOLS, "model", "")

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
