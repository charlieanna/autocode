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
        {"id": "quick", "label": "Quick plan", "description": "Plan at once.", "start_args": ["--no-spec-first"]},
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
        self.assertEqual("quick", setup.choose_mode(MODES, "Quick plan"))
        self.assertIsNone(setup.choose_mode(MODES, "fast"))


class SavedChoiceTests(unittest.TestCase):
    def test_a_choice_is_kept_only_once_the_project_has_autocode_and_read_back(self):
        with tempfile.TemporaryDirectory() as folder:
            chosen = setup.NewChat("quick", ["--engine", "claude"])
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
        chosen = setup.NewChat("quick", ["--engine", "claude"])
        self.assertIn("  quick  Quick plan  (current)\n      Plan at once.", setup.modes_text(MODES, chosen))
        self.assertEqual(
            "New chats: Quick plan · Claude Code. /mode and /engine change them.", setup.summary(MODES, chosen)
        )
        self.assertIn("  claude  Claude Code  (current)", setup.engines_text(chosen))

    def test_a_new_chat_runs_on_the_default_connection_unless_it_names_one(self):
        rows = [{"id": "opencode"}, {"id": "kilo_glm_mimo_gpt", "default": True}]
        default = setup.default_connection(rows)
        self.assertEqual("kilo_glm_mimo_gpt", default)
        self.assertIn("· kilo_glm_mimo_gpt.", setup.summary(MODES, setup.NewChat(), default))
        self.assertIn("· Claude Code.", setup.summary(MODES, setup.NewChat(None, ["--engine", "claude"]), default))
        self.assertIn("· OpenCode.", setup.summary(MODES, setup.NewChat(), None))

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
