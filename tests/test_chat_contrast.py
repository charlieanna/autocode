"""Current stage messages, input guidance and usage remain normally readable."""

import unittest
from unittest import mock

import autocode_chat_tui as tui

from tests.test_chat_tui import screen_ui, styled


class ChatContrastTests(unittest.TestCase):
    def test_progress_and_input_guidance_never_use_faint_attributes(self):
        for colors in (0, 8, 256):
            with self.subTest(colors=colors):
                ui = object.__new__(tui.ChatTUI)
                with (
                    mock.patch.object(tui.curses, "has_colors", return_value=bool(colors)),
                    mock.patch.object(tui.curses, "COLORS", colors, create=True),
                    mock.patch.object(tui.curses, "start_color"),
                    mock.patch.object(tui.curses, "use_default_colors"),
                    mock.patch.object(tui.curses, "init_pair"),
                    mock.patch.object(tui.curses, "color_pair", side_effect=lambda number: number << 8),
                ):
                    ui._start_colors()
                for role in ("info-body", "placeholder", "assistant-body", "input"):
                    with self.subTest(role=role):
                        self.assertEqual(0, ui._style(role) & tui.curses.A_DIM)
                self.assertTrue(ui._style("border") & tui.curses.A_DIM, "decoration stays visually quiet")

    def test_stage_and_cost_footer_uses_normal_text(self):
        ui = screen_ui()
        ui._view = {
            "status": "RUNNING",
            "progress": {"stage": "Planner"},
            "cost": {"spent_usd": 1.25, "complete": True},
        }
        puts = styled(ui)
        footer = next(row for row in puts if row[0] == 29 and "Planner · chat $1.25" in row[2])
        self.assertEqual(ui._style("plain"), footer[3])
