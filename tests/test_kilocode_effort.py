"""Kilo must not silently discard an explicitly selected reasoning variant."""

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from providers import kilocode

from scenarios.harness import profiles
from tests import test_kilocode_policy as native_policy


class KiloEffortTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(self.root)}):
            self.provider = kilocode.load("kilocode")
        self.env = {"PATH": "/fixture/bin", "RETAINED": "yes"}
        # This unit tests effort admission; native policy is exercised separately.
        policy = patch.object(
            kilocode.kilocode_policy, "restrict", side_effect=lambda argv, child, *a, **kw: (argv, child)
        )
        policy.start()
        self.addCleanup(policy.stop)

    def listing(self, variants):
        return "fixture/model\n" + json.dumps({"variants": variants})

    def launch(self, effort):
        return self.provider.launch("glm", self.root, self.root, None, "fixture/model", effort, True, env=self.env)

    def test_unsupported_medium_is_refused_before_a_provider_command_is_returned(self):
        listing = self.listing({name: {"reasoningEffort": name} for name in ("low", "high", "max")})
        before = dict(self.env)
        with (
            patch.object(
                kilocode.command.env_prep, "preflight_run", return_value=subprocess.CompletedProcess([], 0, listing, "")
            ),
            self.assertRaisesRegex(RuntimeError, "medium.*high, low, max"),
        ):
            self.launch("medium")
        self.assertEqual(before, self.env)

    def test_supported_and_custom_variants_are_forwarded_unchanged(self):
        for effort in ("low", "high", "max", "operator-custom"):
            with (
                self.subTest(effort=effort),
                patch.object(
                    kilocode.command.env_prep,
                    "preflight_run",
                    return_value=subprocess.CompletedProcess([], 0, self.listing({effort: {}}), ""),
                ),
            ):
                argv, _, _ = self.launch(effort)
                self.assertEqual(effort, argv[argv.index("--variant") + 1])
                self.assertEqual("fixture/model", argv[argv.index("--model") + 1])

    def test_stock_kilo_profile_glm_flags_pass_launch_admission_unchanged(self):
        flags = profiles.flags(profiles.resolve("glm-mimo-kilo"))
        self.assertEqual("kilocode", flags[flags.index("--provider") + 1])
        for role in ("requirements", "planner", "validator", "completion"):
            model = flags[flags.index(profiles.MODEL_FLAGS[role]) + 1]
            effort = flags[flags.index(profiles.EFFORT_FLAGS[role]) + 1]
            listing = model + "\n" + json.dumps({"variants": {name: {} for name in ("low", "high", "max")}})
            with (
                self.subTest(role=role),
                patch.object(
                    kilocode.command.env_prep,
                    "preflight_run",
                    return_value=subprocess.CompletedProcess([], 0, listing, ""),
                ),
            ):
                argv, _, _ = self.provider.launch("glm", self.root, self.root, None, model, effort, True, env=self.env)
                self.assertEqual(model, argv[argv.index("--model") + 1])
                self.assertEqual(effort, argv[argv.index("--variant") + 1])
                self.assertEqual(self.provider.DEFAULT_REASONING_EFFORTS["glm"], effort)

    def test_unspecified_effort_keeps_the_deliberate_provider_default_without_listing(self):
        with patch.object(kilocode.command.env_prep, "preflight_run") as listing:
            argv, _, _ = self.launch(None)
        self.assertNotIn("--variant", argv)
        listing.assert_not_called()

    def test_missing_empty_or_unreadable_variant_metadata_cannot_authorize_explicit_effort(self):
        for output, code in (("fixture/model\n{}", 0), (self.listing({}), 0), ("bad listing", 0), ("", 1)):
            with self.subTest(output=output):
                with (
                    patch.object(
                        kilocode.command.env_prep,
                        "preflight_run",
                        return_value=subprocess.CompletedProcess([], code, output, "private diagnostic"),
                    ),
                    self.assertRaises(RuntimeError) as raised,
                ):
                    self.launch("medium")
                self.assertNotIn("private diagnostic", str(raised.exception))


@unittest.skipUnless(
    sys.platform == "linux" and os.environ.get("AUTOCODE_KILO_LOCAL_SSE_TEST") == "1",
    "Opt-in original Kilo 7.8.3 and loopback-only network; no paid models",
)
class NativeKiloEffortTests(unittest.TestCase):
    setUp = native_policy.NativeKiloPolicyTests.setUp
    configured = native_policy.NativeKiloPolicyTests.configured

    def test_medium_sends_no_request_and_high_reaches_the_actual_native_http_request(self):
        requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                requests.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                for delta, finish in (({"role": "assistant", "content": "Checked."}, None), ({}, "stop")):
                    row = {
                        "id": "effort-fixture",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "check",
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                    }
                    self.wfile.write(("data: " + json.dumps(row) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        env = self.configured()
        config = json.loads(env["KILO_CONFIG_CONTENT"])
        provider = config["provider"]["localfixture"]
        provider["options"]["baseURL"] = f"http://127.0.0.1:{server.server_port}/v1"
        provider["models"]["check"].update(
            reasoning=True, variants={level: {"reasoningEffort": level} for level in ("low", "high", "max")}
        )
        env["KILO_CONFIG_CONTENT"] = json.dumps(config)
        with self.assertRaisesRegex(kilocode.KiloEffortError, "medium.*high, low, max"):
            self.provider.launch("glm", self.workspace, self.root, None, "localfixture/check", "medium", True, env=env)
        self.assertEqual([], requests, "An unsupported variant must stop before inference")
        argv, child, _ = self.provider.launch(
            "glm", self.workspace, self.root, None, "localfixture/check", "high", True, env=env
        )
        result = subprocess.run(
            argv, input="Return Checked.", cwd=self.workspace, env=child, capture_output=True, text=True, timeout=60
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertTrue(requests, result.stdout)
        self.assertTrue(all(row.get("reasoning_effort") == "high" for row in requests), requests)
