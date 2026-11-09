"""Trailing provider auth-helper timeouts are named; other tails keep their classification."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import autocode_job_failure as job_failure
import autocode_provider_auth as provider_auth
import autocode_support as support

HELPER = "/Users/akothari/.gocode/bin/gocode"
AUTH_TAIL = "\n".join(
    [
        "ERROR: Reconnecting... 5/5",
        f"2026-10-12T20:27:04.621678Z ERROR codex_login::auth::manager: "
        f"Failed to resolve external auth: provider auth command `{HELPER}` timed out after 5000 ms",
        f"ERROR: provider auth command `{HELPER}` timed out after 5000 ms",
        f"ERROR: provider auth command `{HELPER}` timed out after 5000 ms",
    ]
)


class ProviderAuthReasonTests(unittest.TestCase):
    def write(self, text: str) -> Path:
        path = Path(self.root) / "stage.jsonl"
        path.write_text(text)
        return path

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_trailing_auth_timeout_is_named(self):
        path = self.write(AUTH_TAIL + "\n")
        reason = provider_auth.reason(path)
        self.assertIsNotNone(reason)
        self.assertIn(HELPER, reason)
        self.assertIn("5000 ms", reason)
        self.assertIn("timeout_ms", reason)

    def test_auth_text_before_a_json_event_is_not_trailing(self):
        path = self.write(
            AUTH_TAIL
            + "\n"
            + json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "still working"}})
            + "\n"
        )
        self.assertIsNone(provider_auth.reason(path))

    def test_rate_limit_and_capacity_tails_are_not_auth(self):
        for text in (
            "ERROR: exceeded retry limit, last status: 429 Too Many Requests\n",
            "ERROR: model is at capacity\n",
        ):
            with self.subTest(text=text.strip()):
                path = self.write(text)
                self.assertIsNone(provider_auth.reason(path))
                status = support.failure_status(path)
                self.assertIn(status, ("PAUSED_RATE_LIMIT", "PAUSED_PROVIDER_CAPACITY", "PAUSED_PROVIDER_UNCERTAIN"))

    def test_structured_auth_rejection_is_named_without_echoing_response_data(self):
        error = {
            "type": "error",
            "sessionID": "ses_auth",
            "error": {
                "name": "APIError",
                "data": {
                    "statusCode": 401,
                    "message": "Rejected credential: private-test-canary",
                    "responseBody": "private-test-canary; rate limit 429; budget exhausted",
                },
            },
        }
        path = self.write(json.dumps(error) + "\n")
        reason = provider_auth.reason(path)
        self.assertIsNotNone(reason)
        self.assertIn("authentication", reason)
        self.assertIn("401", reason)
        message = provider_auth.exit_message("Requirements", 1, path)
        self.assertIn("401", message)
        self.assertIn("no automatic replay", message)
        self.assertNotIn("private-test-canary", message)
        # Access checks can classify the rejected login; a launched stage still
        # requires reconciliation of its partial work before any retry.
        self.assertEqual("PAUSED_AUTH", support.failure_status(path))
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", support.failure_status(path, record={"stage": "terra"}))

    def test_authentication_is_not_inferred_from_tool_output_or_usage(self):
        for event in (
            {"type": "item.completed", "item": {"type": "command_execution", "aggregated_output": "HTTP 401"}},
            {"type": "turn.failed", "usage": {"input_tokens": 401}, "error": {"message": "connection reset"}},
            {"type": "error", "error": {"data": {"statusCode": 403, "message": "permission denied"}}},
        ):
            with self.subTest(event=event):
                path = self.write(json.dumps(event) + "\n")
                self.assertIsNone(provider_auth.reason(path))
                self.assertNotEqual("PAUSED_AUTH", support.failure_status(path))

    def test_typed_authentication_403_names_account_access_without_changing_retry_authority(self):
        rows = [
            {"type": "error", "error": {"data": {"statusCode": 403}, "message": "private-test-canary"}},
            {"type": "turn.failed", "error": {"code": "authentication_failed", "message": "private-test-canary"}},
        ]
        for ordered in (rows, rows[::-1]):
            with self.subTest(reverse=ordered != rows):
                path = self.write("\n".join(json.dumps(row) for row in ordered) + "\n")
                reason = provider_auth.reason(path)
                self.assertIsNotNone(reason)
                self.assertIn("account access denied (HTTP 403)", reason)
                self.assertEqual(reason, provider_auth.authentication_reason(iter(ordered)))
                message = provider_auth.exit_message("Planner", 1, path)
                self.assertIn(reason, message)
                self.assertIn("no automatic replay", message)
                self.assertNotIn("private-test-canary", message)
                self.assertEqual("PAUSED_AUTH", support.failure_status(path))
                self.assertEqual(
                    "PAUSED_PROVIDER_UNCERTAIN", support.failure_status(path, record={"stage": "glm_revise"})
                )

    def test_403_requires_a_provider_owned_terminal_authentication_code(self):
        forbidden = {"type": "error", "error": {"data": {"statusCode": 403}}}
        for terminal in (
            {"type": "turn.failed", "error": {"message": "authentication_failed oauth_org_not_allowed"}},
            {"type": "turn.failed", "error": {"code": "permission_denied"}},
            {"type": "error", "error": {"code": "authentication_failed"}},
            {"type": "item.completed", "error": {"code": "authentication_failed"}},
            {"type": "turn.failed", "error": {"data": {"code": "authentication_failed"}}},
            {"type": "turn.failed", "usage": {"input_tokens": 0, "output_tokens": 0}},
        ):
            with self.subTest(terminal=terminal):
                path = self.write("\n".join(json.dumps(row) for row in (forbidden, terminal)))
                self.assertIsNone(provider_auth.reason(path))
                self.assertNotEqual("PAUSED_AUTH", support.failure_status(path))
        self.assertIsNone(
            provider_auth.authentication_reason([{"type": "turn.failed", "error": {"code": "authentication_failed"}}])
        )


class ProviderAuthJobClassificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_job_failure_classifies_provider_auth(self):
        events = self.root / "stuck-investigation-01.jsonl"
        events.write_text(AUTH_TAIL + "\n")
        runtime = SimpleNamespace(support=support)
        record = {
            "stage": "investigate_stuck",
            "role": "astra",
            "route_role": "astra",
            "events": str(events),
            "exit_code": 1,
            "launch_route": {"model": "gpt-5.6-astra"},
        }
        kind, reason = job_failure._reason(runtime, record, "provider exited 1")
        self.assertEqual("provider_auth", kind)
        self.assertIn(HELPER, reason)
        self.assertIn("timeout_ms", reason)

    def test_job_failure_classifies_structured_auth_rejection(self):
        events = self.root / "investigation.jsonl"
        for status in (401, 403):
            with self.subTest(status=status):
                rows = [
                    {"type": "error", "error": {"data": {"statusCode": status}}},
                    {"type": "turn.failed", "error": {"code": "authentication_failed"}},
                ]
                events.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
                kind, reason = job_failure._reason(
                    SimpleNamespace(support=support),
                    {"stage": "investigate_bug", "role": "astra", "events": str(events), "exit_code": 1},
                    "provider exited 1",
                )
                self.assertEqual("provider_auth", kind)
                self.assertIn(str(status), reason)


if __name__ == "__main__":
    unittest.main()
