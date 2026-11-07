"""The connected-account default (#2049): a new run that nobody routed takes the backend this
machine has signed in to, when OpenCode is not installed."""

import argparse
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_connected_engine as connected
import autocode_direct_config
import autocode_providers

LOGGED_IN = {
    ("claude", "--version"): (0, "2.1.299 (Claude Code)\n"),
    ("claude", "auth", "status", "--json"): (
        0,
        '{"loggedIn": true, "authMethod": "claude.ai", "apiProvider": "firstParty", "email": "x@y"}',
    ),
}
LOGGED_OUT = {**LOGGED_IN, ("claude", "auth", "status", "--json"): (1, '{"loggedIn": false}')}
CODEX_IN = {("codex", "login", "status"): (0, "")}
CODEX_OUT = {("codex", "login", "status"): (1, "")}


def fake_runner(outputs):
    def runner(cmd, cwd=None):
        code, out = outputs.get(tuple(cmd), (127, ""))
        return subprocess.CompletedProcess(cmd, code, out, "")

    return runner


def on_path(*names):
    return lambda name: f"/bin/{name}" if name in names else None


class ChooseTests(unittest.TestCase):
    def setUp(self):
        config = tempfile.TemporaryDirectory()
        self.addCleanup(config.cleanup)
        self.config = Path(config.name)
        patcher = patch.dict(os.environ, {"XDG_CONFIG_HOME": config.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("AUTOCODE_PROVIDER", "AUTOCODE_PASS_ENV", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
            os.environ.pop(name, None)

    def choose(self, which, outputs):
        return connected.choose(which=which, runner=fake_runner(outputs))

    def test_an_installed_opencode_keeps_the_default(self):
        self.assertIsNone(self.choose(on_path("opencode", "claude", "codex"), {**LOGGED_IN, **CODEX_IN}))

    def test_a_logged_in_claude_code_is_taken_first(self):
        chosen = self.choose(on_path("claude", "codex"), {**LOGGED_IN, **CODEX_IN})
        self.assertEqual(
            ("claude", "opencode", "claude", "Claude Code"),
            tuple(chosen[k] for k in ("id", "engine", "provider", "label")),
        )
        self.assertIn("Claude Code is connected", chosen["reason"])
        self.assertIn("OpenCode is not installed", chosen["reason"])
        self.assertEqual([], chosen["skipped"])
        # The reason never carries the account (login_check returns no email; nothing else is read).
        self.assertNotIn("x@y", json.dumps(chosen))

    def test_a_logged_out_claude_code_passes_to_a_logged_in_codex_and_says_why(self):
        chosen = self.choose(on_path("claude", "codex"), {**LOGGED_OUT, **CODEX_IN})
        self.assertEqual(("codex", "codex", None), (chosen["id"], chosen["engine"], chosen["provider"]))
        (skipped,) = chosen["skipped"]
        self.assertEqual("claude", skipped["id"])
        self.assertIn("not logged in", skipped["why"])

    def test_a_configured_direct_connection_is_never_the_automatic_choice(self):
        # #2049: the four offered backends are Claude Code, Codex, OpenCode and Kilo; the retired
        # direct connection runs only when a run names --provider autocode.
        direct = self.config / "autocode" / "direct"
        direct.mkdir(parents=True)
        (direct / "connection.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "transport": "compatible",
                    "api_provider": "openai",
                    "endpoint": "https://api.openai.com/v1",
                    "roles": {
                        role: "glm-5.3" for role in ("astra", "terra", "sol", "completion", "glm", "plan_reviewer")
                    },
                }
            )
        )
        (direct / "auth.json").write_text("{}")
        for path in (direct / "connection.json", direct / "auth.json"):
            path.chmod(0o600)
        self.assertEqual("configured", autocode_direct_config.describe()["connection_status"])
        self.assertIsNone(self.choose(on_path("claude", "codex"), {**LOGGED_OUT, **CODEX_OUT}))
        self.assertNotIn("autocode", [entry[0] for entry in connected.ORDER])

    def test_nothing_connected_keeps_the_default_and_its_own_refusal(self):
        self.assertIsNone(self.choose(on_path("claude", "codex"), {**LOGGED_OUT, **CODEX_OUT}))
        self.assertIsNone(self.choose(on_path(), {}))

    def test_a_billing_override_on_claude_code_is_not_a_connected_subscription(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_USE_BEDROCK": "1"}):
            chosen = self.choose(on_path("claude", "codex"), {**LOGGED_IN, **CODEX_IN})
        self.assertEqual("codex", chosen["id"])
        self.assertIn("claude", [row["id"] for row in chosen["skipped"]])


class ExplicitChoiceTests(unittest.TestCase):
    def setUp(self):
        config = tempfile.TemporaryDirectory()
        self.addCleanup(config.cleanup)
        self.config = Path(config.name)
        patcher = patch.dict(os.environ, {"XDG_CONFIG_HOME": config.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("AUTOCODE_PROVIDER", None)

    def test_only_an_unrouted_run_is_chosen_for(self):
        self.assertFalse(connected.explicit_choice(argparse.Namespace(engine=None, provider=None)))
        self.assertTrue(connected.explicit_choice(argparse.Namespace(engine="opencode", provider=None)))
        self.assertTrue(connected.explicit_choice(argparse.Namespace(engine=None, provider="kilocode")))
        with patch.dict(os.environ, {"AUTOCODE_PROVIDER": "kilocode"}):
            self.assertTrue(connected.explicit_choice(argparse.Namespace(engine=None, provider=None)))
        (self.config / "autocode").mkdir()
        (self.config / "autocode" / "config.toml").write_text('default_provider = "kilocode"\n')
        self.assertTrue(connected.explicit_choice(argparse.Namespace(engine=None, provider=None)))
        self.assertEqual("kilocode", autocode_providers.default_name())


if __name__ == "__main__":
    unittest.main()
