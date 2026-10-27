"""What a new terminal chat starts with: /mode, /engine and /connect's pure rules (autocode_chat_setup)."""

import tempfile
import unittest
from pathlib import Path

import autocode_chat_setup as setup

MODES = {
    "schema_version": 1,
    "default": "full",
    "modes": [
        {"id": "full", "label": "Full", "description": "Requirements first.", "start_args": ["--spec-first"]},
        # The core lists only "full" since 2026-10-22; the client offers whatever it lists, so a second,
        # made-up mode checks that a choice between modes still works.
        {"id": "other", "label": "Other mode", "description": "Another way.", "start_args": ["--max-iterations=2"]},
    ],
}
CONNECTIONS = {
    "version": 1,
    "connections": [
        {
            "id": "opencode",
            "label": "OpenCode",
            "connection_status": "not_checked",
            "detail": "Installed.",
            "actions": [
                {
                    "id": "check",
                    "label": "Check connection",
                    "next_command": ["connections", "check", "--provider", "opencode", "--json"],
                },
                {
                    "id": "login",
                    "label": "Sign in / Reconnect",
                    "next_command": ["connections", "login", "--provider", "opencode"],
                },
            ],
        },
        {
            "id": "kilocode",
            "label": "Kilo",
            "connection_status": "unavailable",
            "detail": "Not installed.",
            "actions": [],
        },
    ],
}


class EngineTests(unittest.TestCase):
    """The extension's applyEngine rules (vscode-extension/src/settings.ts)."""

    def test_an_engine_replaces_the_last_one_and_moves_the_provider_with_it(self):
        options = ["--max-iterations", "3", "--engine=opencode", "--provider", "kilocode"]
        self.assertEqual(
            ["--max-iterations", "3", "--provider", "kilocode", "--engine", "opencode"],
            setup.with_engine(options, "opencode"),
        )
        self.assertEqual(["--max-iterations", "3", "--engine", "codex"], setup.with_engine(options, "codex"))
        self.assertEqual(
            ["--max-iterations", "3", "--provider", "claude", "--engine", "claude"],
            setup.with_engine(options, "claude"),
        )

    def test_the_engine_new_chats_run_on(self):
        self.assertEqual("opencode", setup.engine_of([]))
        self.assertEqual("kilocode", setup.engine_of(["--provider", "kilocode"]))
        self.assertEqual("codex", setup.engine_of(["--provider", "kilocode", "--engine", "codex"]))

    def test_using_a_connection_sets_the_provider_and_drops_an_explicit_engine(self):
        self.assertEqual(
            ["--x", "--provider", "opencode"], setup.with_provider(["--x", "--engine", "codex"], "opencode")
        )

    def test_choices_by_id_or_label(self):
        self.assertEqual("claude", setup.choose_engine("Claude Code"))
        self.assertIsNone(setup.choose_engine("gpt"))
        self.assertEqual("other", setup.choose_mode(MODES, "Other mode"))
        self.assertIsNone(setup.choose_mode(MODES, "fast"))

    def test_a_mode_the_core_no_longer_lists_gives_way_to_its_default(self):
        self.assertEqual("other", setup.listed_mode(MODES, "other"))
        self.assertIsNone(setup.listed_mode(MODES, "quick"), "quick was removed on 2026-10-22")
        self.assertIsNone(setup.listed_mode(MODES, None))
        self.assertEqual("Full", setup.mode_label(MODES, setup.listed_mode(MODES, "quick")))


class SavedChoiceTests(unittest.TestCase):
    def test_a_choice_is_kept_only_once_the_project_has_autocode_and_read_back(self):
        with tempfile.TemporaryDirectory() as folder:
            chosen = setup.NewChat("other", ["--engine", "claude"])
            self.assertFalse(chosen.save(Path(folder)), "never creates .autocode")
            (Path(folder) / ".autocode").mkdir()
            self.assertTrue(chosen.save(Path(folder)))
            self.assertEqual(chosen, setup.NewChat.load(Path(folder)))

    def test_an_unreadable_choice_is_no_choice(self):
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / ".autocode").mkdir()
            (Path(folder) / ".autocode" / setup.SETTINGS_FILE).write_text('{"options": "--engine claude"}')
            self.assertEqual(setup.NewChat(), setup.NewChat.load(Path(folder)))


class ListingTests(unittest.TestCase):
    def test_modes_and_the_summary_name_the_current_choice(self):
        chosen = setup.NewChat("other", ["--engine", "claude"])
        self.assertIn("  other  Other mode  (current)\n      Another way.", setup.modes_text(MODES, chosen))
        self.assertEqual(
            "New chats: Other mode · Claude Code. /mode and /engine change them.", setup.summary(MODES, chosen)
        )
        self.assertIn("  claude  Claude Code  (current)", setup.engines_text(chosen))

    def test_a_new_chat_runs_on_the_default_connection_unless_it_names_one(self):
        rows = [{"id": "opencode"}, {"id": "kilo_glm_mimo_gpt", "default": True}]
        self.assertEqual("kilo_glm_mimo_gpt", setup.default_connection(rows))
        self.assertIn("· kilo_glm_mimo_gpt.", setup.summary(MODES, setup.NewChat(), rows))
        self.assertIn("· Claude Code.", setup.summary(MODES, setup.NewChat(None, ["--engine", "claude"]), rows))
        self.assertIn("· OpenCode.", setup.summary(MODES, setup.NewChat(), None))

    def test_the_banner_engine_and_connect_agree_on_the_default_connection(self):
        """#1756: with default_provider = "kilocode", the banner said kilocode, /engine OpenCode, and
        /connect marked nothing."""
        rows = [{**row, "default": row["id"] == "kilocode"} for row in setup.connections(CONNECTIONS)]
        chosen = setup.NewChat()
        self.assertIn("· Kilo.", setup.summary(MODES, chosen, rows), "the label, not the id")
        engines = setup.engines_text(chosen, rows)
        self.assertNotIn("(current)", engines, "no engine is the one new chats use")
        self.assertIn("New chats use the Kilo connection (kilocode)", engines)
        self.assertIn("  kilocode  Kilo: unavailable  (new chats use it)", setup.connections_text(rows, chosen))
        self.assertNotIn("OpenCode: not checked  (new chats use it)", setup.connections_text(rows, chosen))
        claude = setup.NewChat(None, ["--engine", "claude"])  # a choice of its own wins over the default
        self.assertIn("  claude  Claude Code  (current)", setup.engines_text(claude, rows))
        self.assertIn("  kilocode  Kilo: unavailable\n", setup.connections_text(rows, claude))

    def test_an_unusable_default_backend_is_said_before_the_first_question_with_what_to_type(self):
        """A host with Claude Code installed and no OpenCode: the chat said nothing until the first request
        failed with "OpenCode is not on PATH"."""
        rows = [
            {"id": "opencode", "label": "OpenCode", "connection_status": "unavailable", "default": True},
            {"id": "claude", "label": "Claude Code", "connection_status": "not_checked"},
            {"id": "autocode", "label": "AutoCode · Direct", "connection_status": "setup_required"},
        ]
        self.assertEqual(
            "OpenCode cannot start chats yet (unavailable). Installed here: Claude Code. "
            "Type /engine claude to run new chats on it.",
            setup.default_notice(setup.NewChat(), rows),
        )
        kilo = [{**row, "id": "kilocode", "label": "Kilo"} if row["id"] == "claude" else row for row in rows]
        self.assertIn("Type /connect kilocode use", setup.default_notice(setup.NewChat(), kilo))
        alone = [row for row in rows if row["id"] != "claude"]
        self.assertEqual(
            "OpenCode cannot start chats yet (unavailable). Run autocode doctor to see what to install.",
            setup.default_notice(setup.NewChat(), alone),
        )
        self.assertIsNone(setup.default_notice(setup.NewChat(None, ["--engine", "claude"]), rows), "chosen")
        usable = [{**row, "connection_status": "not_checked"} if row["id"] == "opencode" else row for row in rows]
        self.assertIsNone(setup.default_notice(setup.NewChat(), usable))
        self.assertIsNone(setup.default_notice(setup.NewChat(), []), "an unreadable listing says nothing")

    def test_connections_offer_their_own_actions_and_use_only_when_usable(self):
        rows = setup.connections(CONNECTIONS)
        text = setup.connections_text(rows, setup.NewChat(None, ["--provider", "opencode"]))
        self.assertIn("  opencode  OpenCode: not checked  (new chats use it)", text)
        self.assertIn("/connect opencode check|login|use", text)
        self.assertIn("  kilocode  Kilo: unavailable", text)
        self.assertNotIn("/connect kilocode", text)
        self.assertEqual("login", setup.action(rows, "opencode", "login")["id"])
        self.assertIsNone(setup.action(rows, "opencode", "setup"), "only an offered action is ever run")
        self.assertEqual([], setup.connections({"version": 2, "connections": CONNECTIONS["connections"]}))


if __name__ == "__main__":
    unittest.main()
