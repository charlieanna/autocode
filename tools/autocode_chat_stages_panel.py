"""The /stages drawer of the full-screen chat: one list per stage, like a dropdown (autocode_chat_stages).

It covers the conversation area and leaves the composer's draft as it was, as the attention drawer does.
"""

import contextlib
import curses
import textwrap


def show(ui, menu):
    ui._stage_menu = menu
    ui._stage_top = 0
    ui._queued = []
    with contextlib.suppress(curses.error):
        curses.flushinp()  # type-ahead cannot choose a model nobody saw
    ui._cursor(0)


def close(ui):
    ui._stage_menu = None
    ui._cursor(1 if ui._asking else 0)


def handle(ui, key):
    menu = ui._stage_menu
    if key in ("\x1b", "\x04") and not menu.back():
        close(ui)
    elif key in (curses.KEY_UP, curses.KEY_PPAGE):
        menu.move(-5 if key == curses.KEY_PPAGE else -1)
    elif key in (curses.KEY_DOWN, curses.KEY_NPAGE):
        menu.move(5 if key == curses.KEY_NPAGE else 1)
    elif key in ("\n", "\r", curses.KEY_ENTER):
        menu.enter()


def draw(ui, layout, truncate):
    menu = ui._stage_menu
    if menu is None:
        return
    first, bottom, width = layout.body_start, layout.status_start, layout.width
    for row in range(first, bottom):
        ui._put(row, 0, " " * width)
    ui._put(first, 0, truncate(menu.title(), width), ui._style("brand"))
    # What the stage does, the advice and the note are wrapped, not cut: a refusal's reason names the stage to change.
    below = [line for text in [*menu.detail(), menu.note] for line in textwrap.wrap(text, max(1, width)) or [""]]
    room = max(0, bottom - first - 2 - len(below))
    rows = menu.rows()
    ui._stage_top = min(ui._stage_top, menu.selected)
    if room and menu.selected >= ui._stage_top + room:
        ui._stage_top = menu.selected - room + 1
    for offset, (label, available) in enumerate(rows[ui._stage_top : ui._stage_top + room]):
        number = ui._stage_top + offset
        prefix = "> " if number == menu.selected else "  "
        style = "brand" if number == menu.selected else ("plain" if available else "dim")
        ui._put(first + 1 + offset, 0, truncate(prefix + label, width), ui._style(style))
    for offset, line in enumerate(below[-max(0, bottom - first - 1) :]):
        ui._put(max(first + 1, bottom - len(below)) + offset, 0, truncate(line, width), ui._style("dim"))
