"""Full-screen terminal chat for `autocode chat`, built on the standard library's curses.

The chat client (autocode_chat_client) owns every decision. It speaks to a person
through two callables: one line at a time (``say``) and one reply at a time
(``ask``). This module renders those callables: a scrollable transcript, the
current stage on a status row, and a composer with editing and history. It reads
no run, imports no controller and adds no dependency: where a terminal cannot
run curses, the client keeps its line mode.
"""

from __future__ import annotations

import contextlib
import curses
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

PROGRESS_PREFIX = "· "
HISTORY_LIMIT = 100
HINT = "Enter send · Up/Down history · PgUp/PgDn scroll · Ctrl-D leave"
_ESCAPE = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-_])")


def safe_text(text: str) -> str:
    """Model and plan text as cells, without escape sequences or other control characters."""
    text = _ESCAPE.sub("", text).expandtabs(4)
    return "".join(char if char in "\n" or char.isprintable() else " " for char in text)


def display_width(text: str) -> int:
    """Terminal cells ``text`` occupies, counting wide and combining characters."""
    return sum(_char_width(char) for char in text)


def _char_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def clip(text: str, width: int) -> str:
    """``text`` cut to ``width`` cells, without an ellipsis."""
    if width <= 0:
        return ""
    used, kept = 0, []
    for char in text:
        cells = _char_width(char)
        if used + cells > width:
            break
        kept.append(char)
        used += cells
    return "".join(kept)


def truncate(text: str, width: int) -> str:
    """``text`` cut to ``width`` cells, ending in an ellipsis when it does not fit."""
    if width <= 0:
        return ""
    if display_width(text) <= width:
        return text
    return clip(text, width - 1) + "…"


def wrap(text: str, width: int) -> list[str]:
    """Wrap each source line to ``width`` cells, breaking at spaces when possible."""
    width = max(1, width)
    lines = []
    for source in text.split("\n"):
        if not source:
            lines.append("")
            continue
        start = 0
        while start < len(source):
            used, end = 0, start
            while end < len(source):
                cells = _char_width(source[end])
                if used + cells > width:
                    break
                used += cells
                end += 1
            if end == start:
                end = start + 1  # a single wide character at width 1 still has to advance
            if end < len(source) and source[end] != " ":
                # A space at the boundary is a break without being part of the line;
                # otherwise back up to the last space that fits.
                space = source.rfind(" ", start, end)
                if space > start:
                    end = space
            lines.append(source[start:end].rstrip())
            start = end
            while start < len(source) and source[start] == " ":
                start += 1
    return lines


def role_of(text: str) -> str:
    """The speaker a transcript block belongs to, from the prefix the client prints."""
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    if first.startswith("You:"):
        return "user"
    if first.startswith("AutoCode:"):
        return "assistant"
    if first.startswith(
        ("AutoCode stopped", "AutoCode needs you", "Left the chat", "Nothing sent", "Plan not approved")
    ):
        return "notice"
    return "plain"


@dataclass
class Transcript:
    """What the client has said, and the latest progress line it reported."""

    blocks: list[tuple[str, str]] = field(default_factory=list)
    activity: str = ""

    def add(self, text: str) -> bool:
        """Add a spoken block; a progress line updates the status row instead."""
        text = safe_text(text).strip("\n")
        if not text.strip():
            return False
        leading = text.lstrip()
        if leading.startswith(PROGRESS_PREFIX):
            self.activity = leading[len(PROGRESS_PREFIX) :].strip()
            return False
        self.activity = ""
        self.blocks.append((role_of(text), text))
        return True

    def lines(self, width: int) -> list[tuple[str, str]]:
        rendered: list[tuple[str, str]] = []
        for role, text in self.blocks:
            rendered.extend((role, line) for line in wrap(text, width))
            rendered.append(("plain", ""))
        return rendered


@dataclass
class Composer:
    """One editable reply line, with history and a cursor as a character index."""

    text: str = ""
    cursor: int = 0
    history: list[str] = field(default_factory=list)
    index: int | None = None
    stash: str = ""

    def reset(self) -> None:
        self.text, self.cursor, self.index, self.stash = "", 0, None, ""

    def insert(self, text: str) -> None:
        self.text = self.text[: self.cursor] + text + self.text[self.cursor :]
        self.cursor += len(text)

    def backspace(self) -> None:
        if self.cursor:
            self.text = self.text[: self.cursor - 1] + self.text[self.cursor :]
            self.cursor -= 1

    def delete(self) -> None:
        self.text = self.text[: self.cursor] + self.text[self.cursor + 1 :]

    def left(self) -> None:
        self.cursor = max(0, self.cursor - 1)

    def right(self) -> None:
        self.cursor = min(len(self.text), self.cursor + 1)

    def home(self) -> None:
        self.cursor = 0

    def end(self) -> None:
        self.cursor = len(self.text)

    def clear(self) -> None:
        self.text, self.cursor = "", 0

    def delete_word(self) -> None:
        start = self.cursor
        while start and self.text[start - 1].isspace():
            start -= 1
        while start and not self.text[start - 1].isspace():
            start -= 1
        self.text = self.text[:start] + self.text[self.cursor :]
        self.cursor = start

    def previous(self) -> None:
        if not self.history:
            return
        if self.index is None:
            self.stash, self.index = self.text, len(self.history) - 1
        else:
            self.index = max(0, self.index - 1)
        self.text = self.history[self.index]
        self.cursor = len(self.text)

    def next(self) -> None:
        if self.index is None:
            return
        self.index += 1
        if self.index >= len(self.history):
            self.index, self.text = None, self.stash
        else:
            self.text = self.history[self.index]
        self.cursor = len(self.text)

    def submit(self) -> str:
        text = self.text
        if text.strip():
            self.history = [*self.history, text][-HISTORY_LIMIT:]
        self.reset()
        return text


def window(text: str, cursor: int, width: int) -> tuple[str, int]:
    """The part of ``text`` that fits ``width`` cells with ``cursor`` visible, and its column."""
    if width <= 0:
        return "", 0
    before, after = text[:cursor], text[cursor:]
    if display_width(before) < width:
        return clip(before + after, width), display_width(before)
    kept: list[str] = []
    used = 0
    for char in reversed(before):
        cells = _char_width(char)
        if used + cells > width - 1:
            break
        kept.append(char)
        used += cells
    kept.reverse()
    shown = "".join(kept)
    return clip(shown + after, width), display_width(shown)


class ChatTUI:
    """The full-screen renderer behind one ChatClient; use ``ask`` and ``say``."""

    def __init__(self):
        self._screen = None
        self._transcript = Transcript()
        self._composer = Composer()
        self._prompt = ""
        self._asking = False
        self._scroll = 0
        self._closed = False
        self._context = ""
        self._colors: dict[str, int] = {}
        self._open()

    # ---- lifecycle -------------------------------------------------------------------------

    def _open(self) -> None:
        try:
            self._screen = curses.initscr()
            curses.noecho()
            curses.cbreak()
            with contextlib.suppress(curses.error):
                self._screen.keypad(True)
            with contextlib.suppress(curses.error):
                curses.curs_set(0)
            self._start_colors()
        except Exception as error:  # a terminal without curses support keeps line mode
            with contextlib.suppress(Exception):
                curses.endwin()
            raise RuntimeError(f"the terminal cannot run the full-screen chat: {error}") from None

    def set_context(self, workspace: Path, run_dir: Path | None = None) -> None:
        self._context = Path(workspace).name
        if run_dir is not None:
            self._context += f" · {Path(run_dir).name}"
        self._draw()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        screen, self._screen = self._screen, None
        if screen is None:
            return
        with contextlib.suppress(curses.error):
            screen.keypad(False)
        with contextlib.suppress(curses.error):
            curses.nocbreak()
            curses.echo()
            curses.curs_set(1)
        with contextlib.suppress(curses.error):
            curses.endwin()
        if self._transcript.blocks:
            print(self._transcript.blocks[-1][1].splitlines()[0])

    def __enter__(self) -> ChatTUI:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- what the chat client calls --------------------------------------------------------

    def say(self, text: str) -> None:
        if self._closed:
            return
        self._transcript.add(text)
        self._draw()

    def ask(self, prompt: str) -> str:
        if self._closed:
            raise EOFError
        words = " ".join(prompt.split())
        self._prompt = (words + " ") if words else ""
        self._asking = True
        self._composer.reset()
        self._scroll = 0
        self._cursor(1)
        try:
            while True:
                self._draw()
                answer = self._handle(self._read_key())
                if answer is not None:
                    return answer
        finally:
            self._asking = False
            self._prompt = ""
            self._cursor(0)
            self._draw()

    # ---- the keyboard ----------------------------------------------------------------------

    def _read_key(self):
        try:
            return self._screen.get_wch()
        except curses.error:
            return None

    def _handle(self, key) -> str | None:
        composer = self._composer
        if key is None:  # a key curses could not read; redraw and wait
            return None
        if isinstance(key, int):
            if key in (curses.KEY_ENTER, 10, 13):
                return self._submit()
            if key == curses.KEY_BACKSPACE:
                composer.backspace()
            elif key == curses.KEY_DC:
                composer.delete()
            elif key == curses.KEY_LEFT:
                composer.left()
            elif key == curses.KEY_RIGHT:
                composer.right()
            elif key == curses.KEY_HOME:
                composer.home()
            elif key == curses.KEY_END:
                composer.end()
            elif key == curses.KEY_UP:
                composer.previous()
            elif key == curses.KEY_DOWN:
                composer.next()
            elif key == curses.KEY_PPAGE:
                self._scroll_by(self._page())
            elif key == curses.KEY_NPAGE:
                self._scroll_by(-self._page())
            return None
        if key in ("\n", "\r"):
            return self._submit()
        if key in ("\b", "\x7f"):
            composer.backspace()
        elif key == "\x04":  # Ctrl-D: end of input on an empty line, otherwise delete forward
            if composer.text:
                composer.delete()
            else:
                raise EOFError
        elif key == "\x03":  # Ctrl-C keeps the client's pause/interrupt handling
            raise KeyboardInterrupt
        elif key == "\x15":  # Ctrl-U
            composer.clear()
        elif key == "\x01":  # Ctrl-A
            composer.home()
        elif key == "\x05":  # Ctrl-E
            composer.end()
        elif key == "\x17":  # Ctrl-W
            composer.delete_word()
        elif key == "\x0e":  # Ctrl-N
            composer.next()
        elif key == "\x10":  # Ctrl-P
            composer.previous()
        elif key.isprintable():
            composer.insert(key)
        return None

    def _submit(self) -> str:
        text = self._composer.submit()
        if text:
            self._transcript.add(f"You: {text}")
        self._scroll = 0
        self._asking = False
        return text

    def _page(self) -> int:
        rows, _ = self._size()
        return max(1, (rows - 3) - 1)

    def _scroll_by(self, delta: int) -> None:
        self._scroll = max(0, self._scroll + delta)

    # ---- drawing ---------------------------------------------------------------------------

    def _size(self) -> tuple[int, int]:
        if self._screen is None:
            return 24, 80
        rows, cols = self._screen.getmaxyx()
        return (rows or 24), (cols or 80)

    def _draw(self) -> None:
        screen = self._screen
        if self._closed or screen is None:
            return
        rows, cols = self._size()
        width = max(1, cols - 1)
        try:
            screen.erase()
            if rows < 6 or cols < 24:
                self._put(0, 0, truncate("Terminal too small for the chat", width), curses.A_BOLD)
                screen.refresh()
                return
            header = f"AutoCode chat  ·  {self._context}" if self._context else "AutoCode chat"
            header += f"  ·  {HINT}" if self._asking else "  ·  Ctrl-C pause after this step"
            self._put(0, 0, truncate(header, width), curses.A_REVERSE)
            lines = self._transcript.lines(width)
            body = rows - 3
            self._scroll = min(self._scroll, max(0, len(lines) - body))
            end = len(lines) - self._scroll
            start = max(0, end - body)
            for row, (role, text) in enumerate(lines[start:end], 1):
                self._put(row, 0, truncate(text, width), self._style(role))
            self._status(rows - 2, width)
            self._composer_row(rows - 1, width)
            screen.refresh()
        except curses.error:
            pass

    def _status(self, row: int, width: int) -> None:
        activity = self._transcript.activity
        if self._asking:
            text, style = self._prompt, self._style("assistant")
        elif activity:
            text, style = f"  · {activity}", self._style("status") | curses.A_DIM
        else:
            text, style = "AutoCode is working… Ctrl-C asks it to pause after this step", self._style("status")
        self._put(row, 0, truncate(text.rstrip(), width), style)

    def _composer_row(self, row: int, width: int) -> None:
        if not self._asking:
            return
        prompt = "You: "
        shown, cursor = window(self._composer.text, self._composer.cursor, width - display_width(prompt))
        self._put(row, 0, prompt, self._style("user"))
        self._put(row, display_width(prompt), shown)
        with contextlib.suppress(curses.error):
            self._screen.move(row, min(width, display_width(prompt) + cursor))

    def _put(self, row: int, col: int, text: str, attr: int = 0) -> None:
        if not text:
            return
        with contextlib.suppress(curses.error):
            self._screen.addstr(row, col, text, attr)

    def _style(self, role: str) -> int:
        return self._colors.get(role, 0)

    def _cursor(self, visible: int) -> None:
        with contextlib.suppress(curses.error):
            curses.curs_set(visible)

    def _start_colors(self) -> None:
        if not curses.has_colors():
            return
        with contextlib.suppress(curses.error):
            curses.start_color()
            curses.use_default_colors()
        for role, color in (
            ("user", curses.COLOR_CYAN),
            ("assistant", curses.COLOR_GREEN),
            ("notice", curses.COLOR_YELLOW),
            ("status", curses.COLOR_BLUE),
        ):
            pair = len(self._colors) + 1
            with contextlib.suppress(curses.error):
                curses.init_pair(pair, color, -1)
                self._colors[role] = curses.color_pair(pair)
