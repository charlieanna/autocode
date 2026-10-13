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


if __name__ == "__main__":
    unittest.main()
