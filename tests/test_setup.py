"""`autocode setup` (#2049): check the four backends, offer their own sign-in, say what new runs use."""

import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_setup as setup


def row(name, label, status, *, login=True, detail="", missing=False):
    found = {"id": name, "label": label, "connection_status": status, "detail": detail, "actions": []}
    if missing:
        found.update(connection_status="unavailable", detail="Backend executable is not on PATH. Install it.")
        return found
    found["actions"].append(
        {"id": "check", "label": "Check", "next_command": ["connections", "check", "--provider", name, "--json"]}
    )
    if login:
        found["actions"].append(
            {"id": "login", "label": "Sign in", "next_command": ["connections", "login", "--provider", name]}
        )
    return found


CLAUDE_IN = row("claude", "Claude Code", "credentials_present")
CODEX_OUT = row("codex", "Codex", "sign_in_required")
OPENCODE_MISSING = row("opencode", "OpenCode", "", missing=True)
KILO_MISSING = row("kilocode", "Kilo", "", missing=True)
CLAUDE_CHOSEN = {"id": "claude", "engine": "opencode", "provider": "claude", "label": "Claude Code", "reason": "x"}


class SetupTests(unittest.TestCase):
    def setUp(self):
        config = tempfile.TemporaryDirectory()
        self.addCleanup(config.cleanup)
        self.config = Path(config.name)
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": config.name})
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("AUTOCODE_PROVIDER", None)

    def test_each_backend_is_checked_and_a_missing_one_gets_its_install_command(self):
        views = {"claude": CLAUDE_IN, "codex": CODEX_OUT, "opencode": OPENCODE_MISSING, "kilocode": KILO_MISSING}
        calls = []

        def view(name, *, check):
            calls.append((name, check))
            return views[name]

        found = setup.rows(view)
        self.assertEqual([(name, True) for name, _ in setup.BACKENDS], calls)
        install = {item["id"]: item.get("install_command") for item in found}
        self.assertEqual("npm install -g opencode-ai@1.18.33", install["opencode"])
        self.assertEqual("npm install -g @kilocode/cli@7.8.3", install["kilocode"])
        self.assertIsNone(install["claude"])
        self.assertIsNone(install["codex"])

    def test_new_runs_use_signed_in_opencode_first_then_the_connected_engine(self):
        opencode_in = row("opencode", "OpenCode", "credentials_present")
        self.assertEqual("opencode", setup.new_runs_use([CLAUDE_IN, opencode_in], choose=lambda: CLAUDE_CHOSEN)["id"])
        use = setup.new_runs_use([CLAUDE_IN, OPENCODE_MISSING], choose=lambda: CLAUDE_CHOSEN)
        self.assertEqual(("claude", "Claude Code"), (use["id"], use["label"]))
        self.assertIsNone(setup.new_runs_use([OPENCODE_MISSING], choose=lambda: None))

    def test_a_configured_default_provider_is_what_new_runs_use(self):
        (self.config / "autocode").mkdir()
        (self.config / "autocode" / "config.toml").write_text('default_provider = "kilocode"\n')
        use = setup.new_runs_use([CLAUDE_IN], choose=lambda: CLAUDE_CHOSEN)
        self.assertEqual("kilocode", use["id"])

    def test_the_report_names_sign_in_and_install_commands_and_where_api_keys_go(self):
        found = [CLAUDE_IN, CODEX_OUT, {**OPENCODE_MISSING, "install_command": "npm install -g opencode-ai@1.18.33"}]
        shown = setup.text(found, {"id": "claude", "label": "Claude Code", "reason": "Claude Code is connected"})
        self.assertIn("✔ Claude Code: signed in", shown)
        self.assertIn("Codex: installed, not signed in", shown)
        self.assertIn("Sign in: autocode connections login --provider codex", shown)
        self.assertIn("OpenCode: not installed. Install it with: npm install -g opencode-ai@1.18.33", shown)
        self.assertIn("New runs use Claude Code", shown)
        nothing = setup.text([CODEX_OUT], None)
        self.assertIn("Nothing is connected yet", nothing)
        self.assertIn("API keys", nothing)
        self.assertIn("OpenCode or Kilo", nothing)

    def test_sign_in_is_offered_only_for_installed_signed_out_backends_and_runs_their_own_login(self):
        asked, logins = [], []

        def ask(prompt):
            asked.append(prompt)
            return "y"

        def login(argv):
            logins.append(argv)
            return 0

        tried = setup._sign_in(
            [CLAUDE_IN, CODEX_OUT, OPENCODE_MISSING, row("kilocode", "Kilo", "sign_in_required", login=False)],
            ask=ask,
            login=login,
        )
        self.assertTrue(tried)
        self.assertEqual(["Sign in to Codex now? [y/N] "], asked)
        self.assertEqual([["login", "--provider", "codex"]], logins)
        self.assertFalse(setup._sign_in([CODEX_OUT], ask=lambda _prompt: "", login=login))
        self.assertEqual(1, len(logins))

    def test_json_reports_without_asking_and_exits_by_whether_a_run_could_start(self):
        for found, chosen, code in (
            ([CLAUDE_IN, CODEX_OUT, OPENCODE_MISSING, KILO_MISSING], CLAUDE_CHOSEN, 0),
            ([CODEX_OUT, OPENCODE_MISSING, KILO_MISSING], None, 1),
        ):
            output = io.StringIO()
            with (
                self.subTest(code=code),
                mock.patch.object(setup, "rows", return_value=found),
                mock.patch.object(setup.connected_engine, "choose", return_value=chosen),
                mock.patch.object(setup, "_sign_in", side_effect=AssertionError("--json asked a question")),
                mock.patch.object(setup.connections, "say_routes", side_effect=AssertionError("listed routes")),
                contextlib.redirect_stdout(output),
            ):
                self.assertEqual(code, setup.cli(["--json"]))
            payload = json.loads(output.getvalue())
            self.assertEqual(1, payload["version"])
            self.assertEqual([item["id"] for item in found], [item["id"] for item in payload["backends"]])
            self.assertEqual(chosen and chosen["id"], (payload["new_runs_use"] or {}).get("id"))

    def test_routes_come_from_a_listed_tool_and_an_engine_names_its_defaults(self):
        listed = []
        with mock.patch.object(
            setup.connections, "say_routes", side_effect=lambda stream, provider: listed.append(provider)
        ):
            setup.say_routes({"id": "kilocode", "label": "Kilo"})
            shown = io.StringIO()
            setup.say_routes({"id": "claude", "label": "Claude Code"}, stream=shown)
        self.assertEqual(["kilocode"], listed)
        self.assertIn("Claude Code's default model", shown.getvalue())

    def test_with_no_hub_installed_setup_offers_opencode_once_and_installs_it_with_npm(self):
        found = [CLAUDE_IN, CODEX_OUT, OPENCODE_MISSING, KILO_MISSING]
        found = [
            {**item, "install_command": setup.INSTALL[item["id"]]}
            if item.get("detail", "").startswith("Backend executable")
            else item
            for item in found
        ]
        asked, ran, said = [], [], []
        npm = lambda name: "/bin/npm" if name == "npm" else None  # noqa: E731
        installed = setup._install(
            found,
            ask=lambda prompt: asked.append(prompt) or "",
            which=npm,
            run=lambda argv: ran.append(argv) or 0,
            out=said.append,
        )
        self.assertTrue(installed)
        self.assertEqual(1, len(asked))
        self.assertIn("Install OpenCode 1.18.33 now", asked[0])
        self.assertEqual([["npm", "install", "-g", "opencode-ai@1.18.33"]], ran)
        self.assertEqual(["Installed OpenCode 1.18.33."], said)
        ran.clear()
        self.assertFalse(
            setup._install(found, ask=lambda _prompt: "n", which=npm, run=lambda argv: ran.append(argv) or 0)
        )
        self.assertEqual([], ran)
        # No terminal (no ask) and no --install: nothing is installed or asked.
        self.assertFalse(setup._install(found, which=npm, run=lambda argv: ran.append(argv) or 0))
        self.assertEqual([], ran)

    def test_an_installed_hub_is_never_reinstalled_and_install_names_kilo_explicitly(self):
        opencode_in = row("opencode", "OpenCode", "credentials_present")
        kilo_missing = {**KILO_MISSING, "install_command": setup.INSTALL["kilocode"]}
        ran = []
        npm = lambda name: "/bin/npm" if name == "npm" else None  # noqa: E731
        ask = lambda _prompt: self.fail("setup asked although OpenCode is installed")  # noqa: E731
        self.assertFalse(setup._install([opencode_in, kilo_missing], ask=ask, which=npm, run=ran.append))
        self.assertTrue(
            setup._install(
                [opencode_in, kilo_missing],
                wanted="kilocode",
                which=npm,
                run=lambda argv: ran.append(argv) or 0,
                out=lambda _line: None,
            )
        )
        self.assertEqual([["npm", "install", "-g", "@kilocode/cli@7.8.3"]], ran)
        self.assertFalse(setup._install([opencode_in, kilo_missing], wanted="opencode", which=npm, run=ran.append))

    def test_without_npm_or_when_npm_fails_setup_says_what_to_run(self):
        missing = [
            {**OPENCODE_MISSING, "install_command": setup.INSTALL["opencode"]},
            {**KILO_MISSING, "install_command": setup.INSTALL["kilocode"]},
        ]
        said = []
        self.assertFalse(
            setup._install(missing, wanted="opencode", which=lambda _name: None, run=self.fail, out=said.append)
        )
        self.assertIn("Install Node.js (https://nodejs.org), then run: npm install -g opencode-ai@1.18.33", said[0])
        said.clear()
        npm = lambda name: "/bin/npm" if name == "npm" else None  # noqa: E731
        self.assertFalse(setup._install(missing, wanted="opencode", which=npm, run=lambda _argv: 243, out=said.append))
        self.assertIn("exit 243", said[0])
        self.assertIn("Run it yourself: npm install -g opencode-ai@1.18.33", said[0])


class OpenCodeWithoutAccountTests(unittest.TestCase):
    """A new run passes over an installed OpenCode only when its own listing is plainly empty."""

    def setUp(self):
        config = tempfile.TemporaryDirectory()
        self.addCleanup(config.cleanup)
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": config.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def choose(self, auth_list):
        import subprocess

        import autocode_connected_engine as connected

        outputs = {
            ("opencode", "auth", "list"): auth_list,
            ("claude", "--version"): (0, "2.1.299 (Claude Code)\n"),
            ("claude", "auth", "status", "--json"): (
                0,
                '{"loggedIn": true, "authMethod": "claude.ai", "apiProvider": "firstParty"}',
            ),
        }

        def runner(cmd, cwd=None):
            code, out = outputs.get(tuple(cmd), (127, ""))
            return subprocess.CompletedProcess(cmd, code, out, "")

        which = lambda name: f"/bin/{name}" if name in ("opencode", "claude") else None  # noqa: E731
        return connected.choose(which=which, runner=runner)

    def test_an_opencode_with_no_account_passes_to_a_signed_in_claude_code(self):
        chosen = self.choose((0, "┌  Credentials ~/.local/share/opencode/auth.json\n│\n└  0 credentials\n"))
        self.assertEqual("claude", chosen["id"])
        self.assertEqual(["opencode"], [item["id"] for item in chosen["skipped"]])
        self.assertIn("OpenCode has no connected account", chosen["reason"])

    def test_any_listed_credential_or_an_unreadable_listing_keeps_opencode(self):
        for listing in (
            (0, "●  OpenAI oauth\n└  1 credentials\n"),
            (0, "1.18.33\n"),
            (0, "┌  Environment\n●  Anthropic ANTHROPIC_API_KEY\n"),
            (1, ""),
            (127, ""),
        ):
            with self.subTest(listing=listing):
                self.assertIsNone(self.choose(listing))


class MissingOpenCodeTests(unittest.TestCase):
    def test_a_run_with_no_opencode_names_autocode_setup(self):
        import providers.opencode as opencode

        with tempfile.TemporaryDirectory() as empty, self.assertRaises(RuntimeError) as refused:
            opencode._probe_version(Path(empty), env={"PATH": empty})
        self.assertIn("Run `autocode setup` to connect Claude Code, Codex, OpenCode or Kilo", str(refused.exception))


if __name__ == "__main__":
    unittest.main()
