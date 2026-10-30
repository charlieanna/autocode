"""/stages in the terminal chat: one list per stage, like a dropdown, over `autocode stages list --json`."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_chat_client as chat
import autocode_chat_stages as stages
import autocode_chat_stages_panel as stages_panel
import autocode_chat_tui as tui
import autocode_stage_choices as stage_choices

from tests.test_chat_tui import screen_ui

HAIKU, SONNET, OPUS = "claude-haiku-4-5-20251001", "claude-sonnet-5-5", "claude-opus-5-5"
DOWN, UP, ENTER, ESC = tui.curses.KEY_DOWN, tui.curses.KEY_UP, "\n", "\x1b"


def option(model, *, unavailable=None, levels=("low", "high")):
    return {
        "model": model,
        "effort_choices": list(levels),
        "family": "glm",
        "context": 200000,
        "account": "Z.AI Coding Plan",
        "unavailable": unavailable,
    }


def view(**stage):
    builder = {
        "id": "terra",
        "label": "Builder",
        "does": "Writes the code.",
        "advice": "A fast model at medium.",
        "default": {"model": "a", "effort": "high", "offered": True},
        "chosen": None,
        "options": [option("a"), option("b", unavailable="the Tester runs b")],
        "set": {
            "next_command": [
                "stages",
                "set",
                "terra",
                "--backend",
                "x",
                "--json",
                "--model",
                "MODEL",
                "--effort",
                "EFFORT",
            ]
        },
    } | stage
    return {"version": 1, "backend": "x", "label": "X", "options_stop": None, "stages": [builder]}


class MenuTests(unittest.TestCase):
    """The drawer's lists, from the view alone."""

    def test_a_choice_fills_only_the_views_own_command_with_a_listed_model_and_level(self):
        stage = view()["stages"][0]
        self.assertEqual(
            ["stages", "set", "terra", "--backend", "x", "--json", "--model", "a", "--effort", "low"],
            stages.fill(stage, "a", "low"),
        )
        for model, effort, error in (
            ("zzz", "low", "not offered zzz"),
            ("b", "low", "cannot run b: the Tester runs b"),
            ("a", "max", "a takes low, high"),
        ):
            with self.subTest(model=model), self.assertRaisesRegex(ValueError, error):
                stages.fill(stage, model, effort)
        unnamed = view(set={"next_command": ["stages", "set", "terra"]})["stages"][0]
        with self.assertRaisesRegex(ValueError, "names no choice"):
            stages.fill(unnamed, "a", "low")

    def test_the_drawer_opens_a_stages_models_then_levels_and_saves_the_choice(self):
        sent = []
        saved = view(chosen={"model": "a", "effort": "low"})
        menu = stages.Menu(view(), save=lambda argv: sent.append(argv) or saved)
        self.assertEqual([("Builder: a at high (default)", True)], menu.rows())
        self.assertEqual(["Writes the code.", "Advice: A fast model at medium."], menu.detail())
        menu.enter()
        self.assertEqual(("models", 0), (menu.level, menu.selected))
        self.assertEqual("Builder: choose its model", menu.title())
        self.assertEqual(
            [
                ("a  (glm; 200k context; Z.AI Coding Plan)", True),
                ("b  (glm; 200k context; Z.AI Coding Plan)  not available: the Tester runs b", False),
            ],
            menu.rows(),
        )
        menu.move(1)
        menu.enter()
        self.assertEqual("Not available: the Tester runs b.", menu.note, "the reason names the stage to change")
        self.assertEqual("models", menu.level)
        menu.move(-1)
        menu.enter()
        self.assertEqual(("levels", 1), (menu.level, menu.selected), "the default level is where the list opens")
        self.assertEqual("Builder on a: choose its reasoning level", menu.title())
        self.assertTrue(menu.back())
        self.assertEqual("models", menu.level)
        menu.enter()
        menu.move(-1)
        menu.enter()
        self.assertEqual(["--model", "a", "--effort", "low"], sent[-1][-4:])
        self.assertEqual(("stages", "Saved: the Builder runs a at low in new chats."), (menu.level, menu.note))
        self.assertEqual("Builder: a at low (your choice)", menu.rows()[0][0])
        menu.enter()
        self.assertEqual(("models", stages.HINT), (menu.level, menu.note), "the last save's note does not follow")
        menu.back()
        self.assertFalse(menu.back(), "Esc on the stages list closes the drawer")

    def test_a_refusal_or_an_unlisted_backend_is_said_in_the_drawer(self):
        menu = stages.Menu(view(), save=mock.Mock(side_effect=ValueError("the Tester runs a")))
        menu.enter()
        menu.enter()
        menu.enter()
        self.assertEqual(("levels", "the Tester runs a"), (menu.level, menu.note))
        menu.save = lambda argv: {"no": "view"}
        menu.enter()
        self.assertIn("no stage view came back", menu.note)
        empty = stages.Menu({**view(options=[]), "options_stop": "no login"}, save=mock.Mock())
        empty.enter()
        self.assertEqual("Models cannot be listed: no login", empty.note)
        hidden = view(default={"model": "openai/gpt-6-sol", "offered": False})
        self.assertIn("chosen from your accounts when a chat starts", stages.text(hidden))


class RealCoreTests(unittest.TestCase):
    """The chat and the core agree: /stages reads the real `autocode stages list --json` and takes its `set`."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name)
        self.history = chat.History(self.home, env={"XDG_CONFIG_HOME": str(self.home / "config")})
        self.history.new = chat.setup.NewChat(None, ["--engine", "claude"])
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.home / "config")})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_line_chat_lists_every_stage_and_takes_stage_number_and_level(self):
        menu = self.history.slash("/stages", "")
        self.assertIsInstance(menu, stages.Menu)
        shown = str(menu)
        self.assertIn("Stages for new chats on Claude Code:", shown)
        self.assertIn("Builder: " + HAIKU + " at medium (default)", shown)
        self.assertIn("Advice: A fast, cheaper model at medium.", shown)
        self.assertIn(f"2) {OPUS} [low, medium, high, xhigh, max]", shown)
        self.assertIn("The Tester lists models 1 to 3.", self.history.slash("/stages", "tester 9 high"))
        self.assertIn("Type /stages STAGE N LEVEL", self.history.slash("/stages", "nobody 1 high"))
        self.assertIn("cannot run", self.history.slash("/stages", "builder 3 high"), "the Tester runs Sonnet")
        said = self.history.slash("/stages", "tester 2 high")
        self.assertIn(f"New chats run the Tester on {OPUS} at high.", said)
        self.assertEqual({"model": OPUS, "effort": "high"}, stage_choices.load()["claude"]["sol"])

    def test_the_full_screen_drawer_chooses_a_model_and_level_with_the_arrow_keys(self):
        self.history.slash("/stages", "tester 2 high")
        ui = screen_ui()
        ui.bind(self.history.slash, run=False)
        with mock.patch.object(tui.curses, "curs_set"), mock.patch.object(tui.curses, "flushinp"):
            ui._composer.insert("/stages")
            ui._handle(ENTER, asking=True)
            self.assertIsInstance(ui._stage_menu, stages.Menu)
            for key in (DOWN, DOWN, DOWN, ENTER):  # Requirements, Planner, Plan Reviewer, then the Builder
                ui._handle(key, asking=True)
            menu = ui._stage_menu
            self.assertEqual(("models", "Builder: choose its model"), (menu.level, menu.title()))
            self.assertEqual([HAIKU, OPUS, SONNET], [row["model"] for row in menu.current()["options"]])
            ui._handle(DOWN, asking=True)
            ui._handle(ENTER, asking=True)
            reason = "the Tester and the Completion Reviewer check this stage's work and run claude-opus-5-5; change both too"
            self.assertEqual(f"Not available: {reason}.", menu.note)
            drawn = {}
            ui._put = lambda row, col, text, attr=0: drawn.__setitem__(row, text)
            stages_panel.draw(ui, ui._layout(30, 60), tui.truncate)
            shown = " ".join(text.strip() for _, text in sorted(drawn.items()) if text.strip())
            self.assertIn(f"Not available: {reason}.", shown, "a narrow drawer wraps the reason instead of cutting it")
            self.assertIn("Advice: A fast, cheaper model at medium. It makes the most calls", shown)
            for key in (DOWN, ENTER, DOWN, ENTER):  # Sonnet, then one level up from its default medium
                ui._handle(key, asking=True)
            self.assertEqual(f"Saved: the Builder runs {SONNET} at high in new chats.", menu.note)
            drawn = []
            ui._put = lambda row, col, text, attr=0: drawn.append(text)
            stages_panel.draw(ui, ui._layout(30, 120), tui.truncate)
            self.assertIn(f"> Builder: {SONNET} at high (your choice)", drawn)
            ui._handle(ESC, asking=True)
            self.assertIsNone(ui._stage_menu)
        self.assertEqual({"model": SONNET, "effort": "high"}, stage_choices.load()["claude"]["terra"])
        self.assertEqual("", ui._composer.text)

    def test_without_an_engine_of_its_own_the_chat_lists_the_backend_a_new_run_takes(self):
        self.history.new = chat.setup.NewChat()
        read = []
        self.history.read = lambda argv: read.append(argv) or {"version": 1, "stages": []}
        self.history.slash("/stages", "")
        self.assertEqual([["stages", "list", "--json"]], read)
        self.history.read = lambda argv: {"stages": "no"}
        self.assertIn("returned no stage view", self.history.slash("/stages", ""))


if __name__ == "__main__":
    unittest.main()
