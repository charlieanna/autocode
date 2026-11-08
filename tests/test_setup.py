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
        use = setup.new_runs_use([row("kilocode", "Kilo", "credentials_present")], choose=lambda: CLAUDE_CHOSEN)
        self.assertEqual(("kilocode", "Kilo"), (use["id"], use["label"]))

    def test_an_unavailable_explicit_backend_is_not_reported_as_usable_or_overwritten(self):
        (self.config / "autocode").mkdir()
        (self.config / "autocode" / "config.toml").write_text('default_provider = "kilocode"\n')
        missing = {**KILO_MISSING, "install_command": setup.INSTALL["kilocode"]}
        use = setup.new_runs_use([CLAUDE_IN, missing], choose=lambda: CLAUDE_CHOSEN)
        self.assertIsNone(use["id"])
        self.assertEqual("kilocode", use["configured_backend"])
        self.assertIn("install it with `npm install -g @kilocode/cli@7.8.3`", use["reason"])
        shown = setup.text([CLAUDE_IN, missing], use)
        self.assertIn("New runs cannot start", shown)
        self.assertNotIn("New runs use Claude Code", shown)

    def test_an_explicit_signed_out_backend_names_its_sign_in_command(self):
        os.environ["AUTOCODE_PROVIDER"] = "codex"
        use = setup.new_runs_use([CODEX_OUT])
        self.assertIsNone(use["id"])
        self.assertIn("autocode connections login --provider codex", use["reason"])

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
        asked.clear()
        setup._sign_in([row("opencode", "OpenCode", "sign_in_required")], ask=ask, login=login)
        self.assertEqual(["Connect an account in OpenCode now (ChatGPT, Z.ai, MiMo or an API key)? [y/N] "], asked)

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

    def test_setup_offers_the_stage_picker_once_defaulting_to_yes_until_choices_are_saved(self):
        claude = {"id": "claude", "label": "Claude Code"}
        for saved, answer, picks in (({}, "", True), ({}, "n", False), ({"claude": {"terra": {}}}, "", False)):
            asked, chosen = [], []
            with self.subTest(saved=bool(saved), answer=answer):
                offered = setup.offer_stages(
                    claude,
                    ask=lambda prompt: asked.append(prompt) or answer,
                    choose=lambda backend: chosen.append(backend) or True,
                    load=lambda: saved,
                )
                self.assertEqual(picks, offered)
                self.assertEqual(["claude"] if picks else [], chosen)
                self.assertEqual(1, len(asked))
                self.assertIn("[y/N]" if saved else "[Y/n]", asked[0])
        broken = io.StringIO()
        with contextlib.redirect_stdout(broken):
            self.assertFalse(setup.offer_stages(claude, ask=None, load=mock.Mock(side_effect=ValueError("bad TOML"))))
        self.assertIn("cannot be read: bad TOML", broken.getvalue())

    def test_in_a_terminal_setup_ends_with_the_stage_picker_or_the_routes_when_it_is_declined(self):
        found = [CLAUDE_IN, CODEX_OUT, OPENCODE_MISSING, KILO_MISSING]
        for offered, code, routes in ((True, 0, []), (False, 0, ["claude"]), (KeyboardInterrupt(), 130, [])):
            listed = []
            with (
                self.subTest(offered=offered),
                mock.patch.object(setup, "_interactive", return_value=True),
                mock.patch.object(setup, "rows", return_value=found),
                mock.patch.object(setup, "_install", return_value=False),
                mock.patch.object(setup, "_sign_in", return_value=False),
                mock.patch.object(setup.connected_engine, "choose", return_value=CLAUDE_CHOSEN),
                mock.patch.object(setup, "offer_stages", side_effect=[offered]) as offer,
                mock.patch.object(setup, "say_routes", side_effect=lambda use: listed.append(use["id"])),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(code, setup.cli([]))
                self.assertEqual("claude", offer.call_args.args[0]["id"])
            self.assertEqual(routes, listed)

    def test_after_installing_kilo_setup_signs_in_on_enter_and_says_when_another_backend_runs(self):
        """It installed Kilo, offered "Sign in to Kilo now? [y/N]", which read as the Kilo Gateway
        alone and took Enter as no, and then said
        "New runs use Claude Code" without saying why Kilo, the hub just chosen, was passed over."""
        kilo_out = {**row("kilocode", "Kilo", "sign_in_required"), "actions": [{"id": "login"}]}
        kilo_in = row("kilocode", "Kilo", "credentials_present")
        before = [CLAUDE_IN, CODEX_OUT, OPENCODE_MISSING, KILO_MISSING]
        installed = [CLAUDE_IN, CODEX_OUT, OPENCODE_MISSING, kilo_out]
        signed_in = [CLAUDE_IN, CODEX_OUT, OPENCODE_MISSING, kilo_in]
        output, prompts = io.StringIO(), []
        with (
            mock.patch.object(setup, "_interactive", return_value=True),
            mock.patch.object(setup, "rows", side_effect=[before, installed, signed_in]),
            mock.patch.object(setup, "_install", return_value="kilocode"),
            mock.patch("builtins.input", side_effect=lambda prompt: prompts.append(prompt) or ""),
            mock.patch.object(setup.connections, "cli", return_value=0) as login,
            mock.patch.object(setup.connected_engine, "choose", return_value=CLAUDE_CHOSEN),
            mock.patch.object(setup, "offer_stages", return_value=True),
            contextlib.redirect_stdout(output),
        ):
            self.assertEqual(0, setup.cli([]))
        self.assertEqual(
            [
                "Sign in to Codex now? [y/N] ",
                "Connect an account in Kilo now (ChatGPT, Z.ai, an API key or Kilo Gateway)? [Y/n] ",
            ],
            prompts,
        )
        login.assert_called_once_with(["login", "--provider", "kilocode"])
        shown = output.getvalue()
        self.assertIn(
            "New runs use Claude Code, not Kilo: Claude Code comes first when several backends are connected. "
            'To run on Kilo, pass `--provider kilocode` for one task, or add `default_provider = "kilocode"` '
            "to ~/.config/autocode/config.toml for every task.",
            shown,
        )

    def test_the_hub_note_says_nothing_when_new_runs_use_the_hub_or_nothing_was_installed(self):
        kilo_in = row("kilocode", "Kilo", "credentials_present")
        kilo_out = row("kilocode", "Kilo", "sign_in_required")
        kilo_use = {"id": "kilocode", "label": "Kilo", "reason": "Kilo is connected"}
        claude_use = {"id": "claude", "label": "Claude Code", "reason": "Claude Code is connected"}
        self.assertIsNone(setup.hub_note(None, [CLAUDE_IN, kilo_in], claude_use))
        self.assertIsNone(setup.hub_note("kilocode", [kilo_in], kilo_use))
        self.assertIsNone(setup.hub_note("kilocode", [kilo_out], None))
        self.assertEqual(
            "Kilo is installed but has no account connected, so new runs cannot use it yet. "
            "Connect one with: autocode connections login --provider kilocode",
            setup.hub_note("kilocode", [CLAUDE_IN, kilo_out], claude_use),
        )
        named = {"id": "claude", "label": "Claude Code", "reason": "named by AUTOCODE_PROVIDER"}
        self.assertIn(
            "New runs use Claude Code, not Kilo: Claude Code is named by AUTOCODE_PROVIDER.",
            setup.hub_note("kilocode", [CLAUDE_IN, kilo_in], named),
        )

    def test_saved_stage_choices_are_what_setup_lists_instead_of_the_selected_routes(self):
        shown = io.StringIO()
        current = {"label": "OpenCode", "file": "/x/stages.toml", "options_stop": None, "stages": []}
        with mock.patch.object(setup.connections, "say_routes", side_effect=AssertionError("listed routes")):
            setup.say_routes(
                {"id": "opencode", "label": "OpenCode"},
                stream=shown,
                load=lambda: {"opencode": {"terra": {"model": "m", "effort": None}}},
                view=lambda backend: current,
            )
        self.assertIn("Stages on OpenCode", shown.getvalue())
        unsaved = io.StringIO()
        setup.say_routes({"id": "claude", "label": "Claude Code"}, stream=unsaved, load=lambda: {})
        self.assertIn("autocode stages choose", unsaved.getvalue())
        broken = io.StringIO()
        setup.say_routes({"id": "claude"}, stream=broken, load=mock.Mock(side_effect=ValueError("bad TOML")))
        self.assertEqual("Your stage choices cannot be read: bad TOML\n", broken.getvalue())

    def test_with_no_hub_installed_setup_asks_which_hub_and_installs_the_one_chosen(self):
        """It offered only OpenCode ("Install OpenCode now? [Y/n]"), so a person who uses Kilo
        could only say no and install Kilo by hand."""
        found = [CLAUDE_IN, CODEX_OUT, OPENCODE_MISSING, KILO_MISSING]
        found = [
            {**item, "install_command": setup.INSTALL[item["id"]]}
            if item.get("detail", "").startswith("Backend executable")
            else item
            for item in found
        ]
        npm_argv = {"opencode": ["npm", "install", "-g", "opencode-ai@1.18.33"]}
        npm_argv["kilocode"] = ["npm", "install", "-g", "@kilocode/cli@7.8.3"]
        for answer, hub in [
            ("", "opencode"),
            ("1", "opencode"),
            ("OpenCode", "opencode"),
            ("2", "kilocode"),
            ("02", "kilocode"),
            ("٢", "kilocode"),
            (" kilo ", "kilocode"),
        ]:
            with self.subTest(answer=answer):
                asked, ran, said = [], [], []
                command = setup.EXECUTABLES[hub]
                npm = lambda name, ran=ran, command=command: {  # noqa: E731
                    "npm": "/bin/npm",
                    command: f"/bin/{command}" if ran else None,
                }.get(name)
                installed = setup._install(
                    found,
                    ask=lambda prompt, asked=asked: asked.append(prompt) or answer,
                    which=npm,
                    run=lambda argv, ran=ran: ran.append(argv) or 0,
                    out=said.append,
                )
                self.assertTrue(installed)
                self.assertEqual(["Install which? [1-3, Enter for 1] "], asked)
                self.assertIn("1. OpenCode 1.18.33", said[0])
                self.assertIn("2. Kilo 7.8.3", said[0])
                self.assertIn("(npm install -g @kilocode/cli@7.8.3)", said[0])
                self.assertIn("3. Neither", said[0])
                self.assertEqual([npm_argv[hub]], ran)
                self.assertEqual(f"Installed {setup.HUBS[hub]}.", said[-1])
        for answer in ("3", "n", "neither", "kilo code"):
            with self.subTest(answer=answer):
                ran, said = [], []
                self.assertFalse(
                    setup._install(
                        found,
                        ask=lambda _prompt, answer=answer: answer,
                        which=lambda name: "/bin/npm" if name == "npm" else None,
                        run=lambda argv, ran=ran: ran.append(argv) or 0,
                        out=said.append,
                    )
                )
                self.assertEqual([], ran)
        self.assertIn("'kilo code' is not one of the choices; nothing was installed", said[-1])
        # No terminal (no ask) and no --install: nothing is installed or asked.
        ran = []
        self.assertFalse(setup._install(found, which=lambda _name: "/bin/npm", run=lambda argv: ran.append(argv) or 0))
        self.assertEqual([], ran)

    def test_invalid_numeric_hub_choices_return_without_installing(self):
        found = [
            CLAUDE_IN,
            {**OPENCODE_MISSING, "install_command": setup.INSTALL["opencode"]},
            {**KILO_MISSING, "install_command": setup.INSTALL["kilocode"]},
        ]
        install = setup._install
        for name, answer in (("superscript digit", "²"), ("oversized number", "9" * 6000)):
            with self.subTest(choice=name):
                output = io.StringIO()
                npm = mock.Mock(return_value=0)
                with (
                    mock.patch.object(setup, "_interactive", return_value=True),
                    mock.patch.object(setup, "rows", return_value=found),
                    mock.patch.object(setup, "new_runs_use", return_value=CLAUDE_CHOSEN),
                    mock.patch.object(setup, "offer_stages", return_value=True),
                    mock.patch("builtins.input", return_value=answer),
                    mock.patch.object(
                        setup,
                        "_install",
                        side_effect=lambda found, **options: install(
                            found, **options, which=lambda name: "/bin/npm" if name == "npm" else None, run=npm
                        ),
                    ),
                    contextlib.redirect_stdout(output),
                ):
                    self.assertEqual(0, setup.cli([]))
                npm.assert_not_called()
                self.assertIn("is not one of the choices; nothing was installed", output.getvalue())

    def test_an_installed_hub_is_never_reinstalled_and_install_names_kilo_explicitly(self):
        opencode_in = row("opencode", "OpenCode", "credentials_present")
        kilo_missing = {**KILO_MISSING, "install_command": setup.INSTALL["kilocode"]}
        ran = []
        npm = lambda name: {"npm": "/bin/npm", "kilo": "/bin/kilo" if ran else None}.get(name)  # noqa: E731
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
        with mock.patch.object(setup.sys, "platform", "freebsd"):
            self.assertFalse(
                setup._install(missing, wanted="opencode", which=lambda _name: None, run=self.fail, out=said.append)
            )
        self.assertIn("Install Node.js (https://nodejs.org), then run: npm install -g opencode-ai@1.18.33", said[0])
        said.clear()
        with mock.patch.object(setup.sys, "platform", "linux"):
            setup._install(missing, wanted="opencode", which=lambda _name: None, run=self.fail, out=said.append)
        self.assertIn("Install Node.js with `sudo apt install nodejs npm` on Debian or Ubuntu or from", said[0])
        said.clear()
        npm = lambda name: "/bin/npm" if name == "npm" else None  # noqa: E731
        self.assertFalse(setup._install(missing, wanted="opencode", which=npm, run=lambda _argv: 243, out=said.append))
        self.assertIn("exit 243", said[0])
        self.assertIn("Run it yourself: npm install -g opencode-ai@1.18.33", said[0])

    def test_without_npm_setup_does_not_offer_an_install_it_cannot_make(self):
        """It asked which hub to install and only then said npm was missing."""
        missing = [
            {**OPENCODE_MISSING, "install_command": setup.INSTALL["opencode"]},
            {**KILO_MISSING, "install_command": setup.INSTALL["kilocode"]},
        ]
        said = []
        with mock.patch.object(setup.sys, "platform", "darwin"):
            installed = setup._install(
                missing, ask=lambda _prompt: self.fail("offered without npm"), which=lambda _name: None, out=said.append
            )
        self.assertFalse(installed)
        self.assertEqual(
            [
                "OpenCode and Kilo install with npm, which is not on PATH. Install Node.js with `brew install node` "
                "or from https://nodejs.org, then run `autocode setup` again."
            ],
            said,
        )

    def test_an_install_outside_path_names_the_folder_instead_of_saying_installed(self):
        """npm installed OpenCode into its global bin, which was not on PATH: setup said "Installed
        OpenCode 1.18.33." and then listed it as not installed."""
        missing = [
            {**OPENCODE_MISSING, "install_command": setup.INSTALL["opencode"]},
            {**KILO_MISSING, "install_command": setup.INSTALL["kilocode"]},
        ]
        said, ran = [], []
        npm = lambda name: "/bin/npm" if name == "npm" else None  # noqa: E731
        installed = setup._install(
            missing,
            ask=lambda _prompt: "y",
            which=npm,
            run=lambda argv: ran.append(argv) or 0,
            out=said.append,
            npm_bin=lambda: "/opt/node22/bin",
        )
        self.assertFalse(installed, "a hub AutoCode cannot run is not reported installed")
        self.assertEqual([["npm", "install", "-g", "opencode-ai@1.18.33"]], ran)
        self.assertEqual(2, len(said), said)
        self.assertIn("Installed OpenCode 1.18.33 in /opt/node22/bin, which is not on your PATH", said[1])
        self.assertIn('export PATH="/opt/node22/bin:$PATH"', said[1])
        said.clear()
        setup._install(
            missing, wanted="opencode", which=npm, run=lambda _argv: 0, out=said.append, npm_bin=lambda: None
        )
        self.assertIn("npm prefix -g", said[0])


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
