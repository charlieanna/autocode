"""Full-screen terminal chat for `autocode chat`, built on the standard library's curses.

The chat client (autocode_chat_client) owns every decision. It speaks to a person
through two callables: one line at a time (``say``) and one reply at a time
(``ask``). This module renders those callables as a chat screen: a splash, a
scrollable transcript with a colored speaker for each block, the current
question or activity above a boxed composer, and a hint bar. It reads no run,
imports no controller and adds no dependency: where a terminal cannot run
curses, the client keeps its line mode.
"""

from __future__ import annotations

import contextlib
import curses
import locale
import re
import sys
import threading
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

PROGRESS_PREFIX = "· "
HISTORY_LIMIT = 100
BRAND = "AutoCode chat"
TAGLINE = "Plan, build and test, with a reviewer at every step."
NEW_CHAT_PLACEHOLDER = "Ask for a change, a fix, or a question…"
REPLY_PLACEHOLDER = "Type your reply, then Enter"
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
PASTE_START, PASTE_END = "\x1b[200~", "\x1b[201~"  # bracketed paste, so a pasted newline is not Enter
WORDMARK = (
    "▄▀█ █░█ ▀█▀ █▀█ █▀▀ █▀█ █▀▄ █▀▀",
    "█▀█ █▄█ ░█░ █▄█ █▄▄ █▄█ █▄▀ ██▄",
)
_ESCAPE = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-_])")
_LABELS = {"user": "You", "assistant": "AutoCode", "notice": "Notice"}
_PREFIXES = {"user": "You:", "assistant": "AutoCode:"}


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


def composer_label(prompt: str, asking: bool) -> str:
    """What the box is for: starting a chat, answering a question, or waiting."""
    if not asking:
        return "AutoCode"
    prompt = prompt.lower()
    if "new request" in prompt or "what do you want done" in prompt:
        return "New chat"
    return "Reply"


def _same(block: tuple[str, str], other: tuple[str, str]) -> bool:
    return block[0] == other[0] and " ".join(block[1].split()) == " ".join(other[1].split())


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
        role = role_of(text)
        if role == "user" and self.blocks and _same(self.blocks[-1], (role, text)):
            return False  # the client repeating what the person just typed
        self.blocks.append((role, text))
        return True

    def closing(self) -> list[str]:
        """What to leave on the terminal after the screen closes: the client's last words, whole.

        Those are the blocks after the last conversation message (a hand-off's reason and
        the command to run, where to pick the run up, Done.); the last block when there are none.
        """
        tail: list[str] = []
        for role, text in reversed(self.blocks):
            if role in ("user", "assistant"):
                break
            tail.insert(0, text)
        return tail or [text for _, text in self.blocks[-1:]]

    def lines(self, width: int) -> list[tuple[str, str]]:
        """The transcript as ``(style, text)`` rows, labels over indented bodies."""
        rendered: list[tuple[str, str]] = []
        for role, text in self.blocks:
            label = _LABELS.get(role)
            if label is None:
                rendered.extend(("plain", line) for line in wrap(text, width))
            else:
                body = text
                prefix = _PREFIXES.get(role)
                if prefix and body.lstrip().startswith(prefix):
                    body = body.lstrip()[len(prefix) :].lstrip()
                rendered.append((f"{role}-label", label))
                rendered.extend((f"{role}-body", "  " + line) for line in wrap(body, max(1, width - 2)))
            rendered.append(("plain", ""))
        return rendered


@dataclass
class Composer:
    """One editable reply, with history and a cursor as a character index; a paste may hold newlines."""

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


def read_paste(read) -> str | None:
    """After an escape key, the text of a bracketed paste; None when the keys were something else.

    ``read`` returns the next key, or None when no key follows promptly. A pasted
    line break stays in the text instead of submitting the reply.
    """
    opening = ""
    while PASTE_START.startswith("\x1b" + opening) and len(opening) < len(PASTE_START) - 1:
        key = read()
        if not isinstance(key, str):
            return None
        opening += key
    if "\x1b" + opening != PASTE_START:
        return None
    text = ""
    while not text.endswith(PASTE_END):
        key = read()
        if key is None:  # the terminal stopped sending without closing the paste
            break
        if isinstance(key, str):
            text += key
    return safe_text(text.removesuffix(PASTE_END).replace("\r\n", "\n").replace("\r", "\n")).rstrip("\n")


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


@dataclass(frozen=True)
class Pasted:
    """Text the terminal delivered as one paste."""

    text: str


@dataclass(frozen=True)
class Glyphs:
    """The box-drawing set; ASCII when the terminal cannot show the rounded one."""

    bar: str
    rule: str
    prompt: str
    top_left: str
    top_right: str
    bottom_left: str
    bottom_right: str
    vertical: str
    spinner: str
    unicode: bool


UNICODE_GLYPHS = Glyphs("▌", "─", "❯", "╭", "╮", "╰", "╯", "│", SPINNER, True)
ASCII_GLYPHS = Glyphs("|", "-", ">", "+", "+", "+", "+", "|", "", False)


@dataclass(frozen=True)
class Layout:
    """Where each part of the screen goes, for the current size and question."""

    width: int
    body_start: int
    body_end: int
    status_start: int
    box_top: int
    box_text: int
    box_bottom: int
    hints_row: int
    question: list[str]


def _unicode_terminal() -> bool:
    encoding = getattr(sys.stdout, "encoding", "") or locale.getpreferredencoding(False)
    return "utf" in encoding.lower()


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
        self._glyphs = UNICODE_GLYPHS if _unicode_terminal() else ASCII_GLYPHS
        self._styles: dict[str, int] = {}
        self._frame = 0
        self._lock = threading.RLock()
        self._stopping = threading.Event()
        self._ticker: threading.Thread | None = None
        self._open()

    # ---- lifecycle -------------------------------------------------------------------------

    def _open(self) -> None:
        with contextlib.suppress(Exception):
            locale.setlocale(locale.LC_ALL, "")
        try:
            self._screen = curses.initscr()
            curses.noecho()
            curses.cbreak()
            with contextlib.suppress(curses.error):
                self._screen.keypad(True)
            with contextlib.suppress(curses.error):
                curses.curs_set(0)
            self._start_colors()
            self._bracketed_paste(True)
        except Exception as error:  # a terminal without curses support keeps line mode
            with contextlib.suppress(Exception):
                curses.endwin()
            raise RuntimeError(f"the terminal cannot run the full-screen chat: {error}") from None
        self._ticker = threading.Thread(target=self._tick, name="autocode-chat-tui", daemon=True)
        self._ticker.start()

    def set_context(self, workspace: Path, run_dir: Path | None = None) -> None:
        self._context = Path(workspace).name
        if run_dir is not None:
            self._context += f" · {Path(run_dir).name}"
        self._draw()

    def close(self) -> None:
        self._stopping.set()
        with self._lock:
            if self._closed:
                return
            self._closed = True
            screen, self._screen = self._screen, None
            if screen is None:
                return
            self._bracketed_paste(False)
            with contextlib.suppress(curses.error):
                screen.keypad(False)
            with contextlib.suppress(curses.error):
                curses.nocbreak()
                curses.echo()
                curses.curs_set(1)
            with contextlib.suppress(curses.error):
                curses.endwin()
        for text in self._transcript.closing():
            print(text.strip("\n"))

    @staticmethod
    def _bracketed_paste(on: bool) -> None:
        with contextlib.suppress(OSError, ValueError):
            sys.stdout.write("\x1b[?2004h" if on else "\x1b[?2004l")
            sys.stdout.flush()

    def __enter__(self) -> ChatTUI:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- what the chat client calls --------------------------------------------------------

    def say(self, text: str) -> None:
        with self._lock:
            if self._closed:
                return
            self._transcript.add(text)
            self._draw()

    def ask(self, prompt: str) -> str:
        with self._lock:
            if self._closed:
                raise EOFError
            with contextlib.suppress(curses.error):
                curses.flushinp()  # keys typed while AutoCode worked never answer a question nobody saw
            self._prompt = " ".join(prompt.split())
            self._asking = True
            self._composer.reset()
            self._scroll = 0
            self._frame = 0
            self._cursor(1)
        try:
            while True:
                self._draw()
                answer = self._handle(self._read_key())
                if answer is not None:
                    return answer
        finally:
            with self._lock:
                self._asking = False
                self._prompt = ""
                self._frame = 0
                self._cursor(0)
                self._draw()

    # ---- the keyboard ----------------------------------------------------------------------

    def _read_key(self):
        try:
            key = self._screen.get_wch()
        except curses.error:
            return None
        if key != "\x1b":
            return key
        self._screen.timeout(50)
        try:
            pasted = read_paste(self._read_next)
        finally:
            self._screen.timeout(-1)
        return Pasted(pasted) if pasted is not None else None

    def _read_next(self):
        try:
            return self._screen.get_wch()
        except curses.error:
            return None

    def _handle(self, key) -> str | None:
        composer = self._composer
        if key is None:  # a key curses could not read; redraw and wait
            return None
        if isinstance(key, Pasted):
            composer.insert(key.text)
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
        self._frame = 0
        return text

    def _page(self) -> int:
        layout = self._layout(*self._size())
        return max(1, (layout.body_end - layout.body_start) - 1)

    def _scroll_by(self, delta: int) -> None:
        self._scroll = max(0, self._scroll + delta)

    # ---- drawing ---------------------------------------------------------------------------

    def _size(self) -> tuple[int, int]:
        if self._screen is None:
            return 24, 80
        rows, cols = self._screen.getmaxyx()
        return (rows or 24), (cols or 80)

    def _layout(self, rows: int, cols: int) -> Layout:
        width = max(1, cols - 1)
        hints_row = rows - 1
        box_bottom = rows - 2
        box_text = rows - 3
        box_top = rows - 4
        status_row = box_top - 1
        question: list[str] = []
        if self._asking and self._prompt:
            wrapped = wrap(f"? {self._prompt}", max(1, width - 2))
            limit = min(4, max(1, status_row - 3))
            question = wrapped[:limit]
            if len(wrapped) > limit:
                question[-1] = truncate(question[-1], max(1, width - 4))
        status_start = status_row - len(question) + 1 if question else status_row
        return Layout(
            width=width,
            body_start=2,
            body_end=max(2, status_start - 1),
            status_start=status_start,
            box_top=box_top,
            box_text=box_text,
            box_bottom=box_bottom,
            hints_row=hints_row,
            question=question,
        )

    def _draw(self) -> None:
        with self._lock:
            screen = self._screen
            if self._closed or screen is None:
                return
            rows, cols = self._size()
            width = max(1, cols - 1)
            try:
                screen.erase()
                if rows < 12 or cols < 32:
                    self._put(0, 0, truncate("Terminal too small for the chat", width), curses.A_BOLD)
                    screen.refresh()
                    return
                layout = self._layout(rows, cols)
                self._header(layout)
                self._body(layout)
                self._question_or_status(layout)
                self._composer_box(layout)
                self._hints(layout)
                screen.refresh()
            except curses.error:
                pass

    def _header(self, layout: Layout) -> None:
        brand = f"{self._glyphs.bar} {BRAND}"
        self._put(0, 0, truncate(brand, layout.width), self._style("brand"))
        used = display_width(brand)
        context = f"  {self._context}" if self._context else ""
        if context and used + display_width(context) < layout.width:
            self._put(0, used, truncate(context, layout.width - used), self._style("dim"))
        self._put(1, 0, self._glyphs.rule * layout.width, self._style("dim"))

    def _body(self, layout: Layout) -> None:
        height = max(0, layout.body_end - layout.body_start)
        lines = self._transcript.lines(layout.width)
        if not lines:
            self._hero(layout, height)
            return
        if not height:
            return
        self._scroll = min(self._scroll, max(0, len(lines) - height))
        end = len(lines) - self._scroll
        start = max(0, end - height)
        for row, (role, text) in enumerate(lines[start:end], layout.body_start):
            if role.endswith("-label"):
                self._put(row, 0, truncate(f"{self._glyphs.bar} {text}", layout.width), self._style(role))
            elif role == "user-body":
                padded = text + " " * max(0, layout.width - display_width(text))
                self._put(row, 0, padded, self._style(role))
            else:
                self._put(row, 0, truncate(text, layout.width), self._style(role))

    def _hero(self, layout: Layout, height: int) -> None:
        if height <= 0:
            return
        brand_rows = 2 if self._glyphs.unicode and layout.width >= 35 else 1
        hero: list[tuple[str, str]] = []
        if brand_rows == 2:
            hero.extend(("brand", line) for line in WORDMARK)
        else:
            hero.append(("brand", "AutoCode"))
        hero.append(("plain", ""))
        tagline = TAGLINE if display_width(TAGLINE) <= layout.width else "Plan, build and test."
        hero.append(("dim", tagline))
        hero.append(("plain", ""))
        if self._context:
            hero.append(("dim", self._context))
        hero = hero[:height]
        top = layout.body_start + max(0, (height - len(hero)) // 2)
        for offset, (role, text) in enumerate(hero):
            col = max(0, (layout.width - display_width(text)) // 2)
            self._put(top + offset, col, truncate(text, layout.width - col), self._style(role))

    def _question_or_status(self, layout: Layout) -> None:
        if layout.question:
            for offset, line in enumerate(layout.question):
                self._put(layout.status_start + offset, 2, truncate(line, layout.width - 2), self._style("question"))
            return
        if self._asking:
            return
        spinner = self._glyphs.spinner
        marker = spinner[self._frame % len(spinner)] if spinner else "*"
        text = self._transcript.activity or "AutoCode is working…"
        lead = f"  {marker} {text}"
        self._put(layout.status_start, 0, truncate(lead, layout.width), self._style("working"))
        tail = "ctrl-c pause after this step"
        room = layout.width - display_width(lead) - 2
        if room >= display_width(tail):
            self._put(layout.status_start, layout.width - display_width(tail), tail, self._style("dim"))

    def _composer_box(self, layout: Layout) -> None:
        glyphs = self._glyphs
        width = layout.width
        active = self._asking
        label = composer_label(self._prompt, active)
        border = self._style("border-active" if active else "border")
        self._put(layout.box_top, 0, self._box_top(width, label), border)
        self._put(layout.box_top, 3, label, self._style("brand" if active else "dim"))
        self._put(layout.box_text, 0, glyphs.vertical, border)
        self._put(layout.box_text, width - 1, glyphs.vertical, border)
        self._put(layout.box_bottom, 0, glyphs.bottom_left + glyphs.rule * (width - 2) + glyphs.bottom_right, border)
        inner = max(1, width - 6)
        self._put(layout.box_text, 2, glyphs.prompt, self._style("prompt" if active else "dim"))
        if not active:
            self._put(layout.box_text, 4, truncate("AutoCode is working…", inner), self._style("placeholder"))
            return
        if self._composer.text:
            shown, cursor = window(self._composer.text, self._composer.cursor, inner)
            shown = shown.replace("\n", "↵" if glyphs.unicode else " ")
            self._put(layout.box_text, 4, truncate(shown, inner), self._style("input"))
        else:
            cursor = 0
            placeholder = NEW_CHAT_PLACEHOLDER if label == "New chat" else REPLY_PLACEHOLDER
            self._put(layout.box_text, 4, truncate(placeholder, inner), self._style("placeholder"))
        with contextlib.suppress(curses.error):
            self._screen.move(layout.box_text, min(width - 2, 4 + cursor))

    def _box_top(self, width: int, label: str) -> str:
        glyphs = self._glyphs
        title = f" {label} "
        fill = max(0, width - 3 - display_width(title))
        return glyphs.top_left + glyphs.rule + title + glyphs.rule * fill + glyphs.top_right

    def _hints(self, layout: Layout) -> None:
        if self._asking:
            hints = "enter send · ↑↓ history · pgup/pgdn scroll · ctrl-d quit"
            if not self._glyphs.unicode:
                hints = "enter send | up/down history | pgup/pgdn scroll | ctrl-d quit"
        else:
            hints = "ctrl-c asks AutoCode to pause after this step"
        self._put(layout.hints_row, 2, truncate(hints, layout.width - 2), self._style("dim"))
        right = "AutoCode"
        planted = 2 + display_width(hints)
        if planted + display_width(right) + 2 < layout.width:
            self._put(layout.hints_row, layout.width - display_width(right), right, self._style("brand"))

    def _put(self, row: int, col: int, text: str, attr: int = 0) -> None:
        if not text:
            return
        with contextlib.suppress(curses.error):
            self._screen.addstr(row, col, text, attr)

    def _style(self, role: str) -> int:
        return self._styles.get(role, 0)

    def _cursor(self, visible: int) -> None:
        with contextlib.suppress(curses.error):
            curses.curs_set(visible)

    def _tick(self) -> None:
        """Animate the working marker; never draws while a question waits for a key."""
        while not self._stopping.wait(0.12):
            with self._lock:
                if self._closed or self._asking:
                    continue
                self._frame += 1
                self._draw()

    def _start_colors(self) -> None:
        self._styles = {
            "dim": curses.A_DIM,
            "brand": curses.A_BOLD,
            "user-label": curses.A_BOLD,
            "user-body": 0,
            "assistant-label": curses.A_BOLD,
            "assistant-body": 0,
            "notice-label": curses.A_BOLD,
            "notice-body": 0,
            "plain": 0,
            "border": curses.A_DIM,
            "border-active": curses.A_BOLD,
            "prompt": curses.A_BOLD,
            "input": 0,
            "placeholder": curses.A_DIM,
            "question": curses.A_BOLD,
            "working": 0,
        }
        if not curses.has_colors():
            return
        with contextlib.suppress(curses.error):
            curses.start_color()
            curses.use_default_colors()
        if curses.COLORS >= 256:
            accent, good, warn, quiet, user_background = 45, 42, 214, 244, 236
        else:
            accent, good, warn, quiet, user_background = (
                curses.COLOR_CYAN,
                curses.COLOR_GREEN,
                curses.COLOR_YELLOW,
                -1,
                -1,
            )
        pairs = 0

        def pair(role: str, foreground: int, background: int = -1, attributes: int = 0) -> None:
            nonlocal pairs
            pairs += 1
            with contextlib.suppress(curses.error):
                curses.init_pair(pairs, foreground, background)
                self._styles[role] = curses.color_pair(pairs) | attributes

        pair("brand", accent, -1, curses.A_BOLD)
        pair("user-label", accent, -1, curses.A_BOLD)
        pair("user-body", 252 if user_background != -1 else -1, user_background)
        pair("assistant-label", good, -1, curses.A_BOLD)
        pair("assistant-body", -1, -1)
        pair("notice-label", warn, -1, curses.A_BOLD)
        pair("notice-body", warn, -1)
        pair("border", quiet if quiet != -1 else accent, -1, curses.A_DIM)
        pair("border-active", accent, -1, curses.A_BOLD)
        pair("prompt", accent, -1, curses.A_BOLD)
        pair("question", warn, -1, curses.A_BOLD)
        pair("working", accent, -1)
