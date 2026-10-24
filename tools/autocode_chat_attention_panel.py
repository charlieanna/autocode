"""An attention drawer that leaves the editor's draft, cursor and viewport intact."""

import contextlib
import curses

try:
    from . import autocode_chat_attention as attention
except ImportError:
    import autocode_chat_attention as attention


def show(ui, menu):
    ui._attention_menu = menu
    ui._attention_top = 0
    ui._queued = []
    with contextlib.suppress(curses.error):
        curses.flushinp()  # type-ahead cannot acknowledge an alert nobody saw
    ui._cursor(0)


def close(ui):
    ui._attention_menu = None
    ui._cursor(1 if ui._asking else 0)


def handle(ui, key, *, asking):
    menu = ui._attention_menu
    if key in ("\x1b", "\x04", curses.KEY_F8):
        close(ui)
    elif key in (curses.KEY_UP, curses.KEY_PPAGE):
        menu.selected = max(0, menu.selected - (5 if key == curses.KEY_PPAGE else 1))
    elif key in (curses.KEY_DOWN, curses.KEY_NPAGE):
        menu.selected = min(max(0, len(menu.rows) - 1), menu.selected + (5 if key == curses.KEY_NPAGE else 1))
    elif key in ("r", "R"):
        show(ui, menu.refresh())
    elif key in ("a", "A") and menu.rows:
        menu.note = menu.acknowledge(menu.rows[menu.selected])
    elif key in ("\n", "\r", curses.KEY_ENTER) and menu.rows:
        if not asking and not ui._can_detach():
            menu.note = (
                "This viewer owns active work. Wait for its question or boundary pause before opening another run."
            )
        else:
            selected = menu.rows[menu.selected]
            close(ui)
            menu.open(selected)  # read-only selection; the selected chat reads fresh status


def draw(ui, layout, truncate):
    menu = ui._attention_menu
    if menu is None:
        return
    first, bottom = layout.body_start, layout.status_start
    width = layout.width
    # Cover only the conversation area; the original draft stays visible below.
    for row in range(first, bottom):
        ui._put(row, 0, " " * width)
    ui._put(
        first,
        0,
        truncate("ATTENTION — a acknowledge · Enter open · r refresh · Esc return to draft", width),
        ui._style("brand"),
    )
    summary = menu.summary
    details = f"{summary.get('unread', 0)} unread · {summary.get('unknown', 0)} unknown"
    if summary.get("error"):
        details += " · inventory unavailable; last saved items"
    if summary.get("overflow"):
        details += f" · {summary['overflow']} more; /runs lists all"
    ui._put(first + 1, 0, truncate(details, width), ui._style("dim"))
    room = max(0, bottom - first - 4)
    ui._attention_top = min(ui._attention_top, menu.selected)
    if room and menu.selected >= ui._attention_top + room:
        ui._attention_top = menu.selected - room + 1
    for offset, row in enumerate(menu.rows[ui._attention_top : ui._attention_top + room]):
        number = ui._attention_top + offset
        prefix = "> " if number == menu.selected else "  "
        ui._put(
            first + 2 + offset,
            0,
            truncate(prefix + attention.label(row), width),
            ui._style("brand" if number == menu.selected else "plain"),
        )
    if not menu.rows:
        ui._put(
            first + 2,
            0,
            truncate("No current saved alerts. Discovery continues in the background.", width),
            ui._style("dim"),
        )
    if bottom > first + 2:
        ui._put(bottom - 1, 0, truncate(menu.note, width), ui._style("dim"))
