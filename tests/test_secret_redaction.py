"""Credentials must not leave the run directory in an export (#712)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode_util as util
from autocode_github import Client


class RedactionTests(unittest.TestCase):
    def test_known_token_shapes_are_replaced(self):
        samples = (
            "token ghp_abcdefghijklmnopqrstuvwxyz123456",
            "key github_pat_ABCDEFGHIJKLMNOPQRSTUV",
            "sk-ant-oia0123456789abcdef",
            "sk-0123456789abcdefghijklmnopqrstuv",
            "AKIAIOSFODNN7EXAMPLE",
            "Authorization: Bearer ya29.a0AfH6SMBx",
            "api_key=AIzaSyA-very-long-google-key",
            "password: hunter2secret",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                out = util.redact(sample)
                self.assertNotIn(sample.split()[-1] if " " in sample else sample, out)
                self.assertIn("[REDACTED]", out)

    def test_plain_text_is_untouched(self):
        for text in ("the token is not here", "PASS 5/5", "", "ghp_short"):
            self.assertEqual(text, util.redact(text))

    def test_marked_secret_values_are_removed(self):
        text = "config says my-very-secret-value and again my-very-secret-value"
        out = util.redact(text, secret_values=["my-very-secret-value"])
        self.assertNotIn("my-very-secret-value", out)
        self.assertEqual(2, out.count("[REDACTED]"))

    def test_a_canary_in_a_pr_body_never_escapes(self):
        canary = "ghp_CANARY0123456789abcdef"
        body = f"## Summary\n\nUses {canary} in the test fixture.\n"
        captured = {}

        class Transport:
            def __call__(self, method, url, headers, body):
                captured["payload"] = body
                return 201, {"number": 1}

        client = Client(token="unused")
        client.transport = Transport()
        client.open_pull_request("o", "r", head="h", base="b", title="t", body=body)
        sent = captured["payload"].decode()
        self.assertNotIn(canary, sent)
        self.assertIn("[REDACTED]", sent)


if __name__ == "__main__":
    unittest.main()
