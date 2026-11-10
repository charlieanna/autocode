"""Full-screen terminal chat for `autocode chat`, built on the standard library's curses.

The chat client (autocode_chat_client) owns every decision. It speaks to a person
through two callables: one line at a time (``say``) and one reply at a time
(``ask``), and hands over each status view it reads (``observe``) and each wait
between polls (``wait``). This module renders those callables as a chat screen: a
splash, a scrollable transcript with a colored speaker for each block, a side panel
with the run's requirements, tasks and log (autocode_chat_panel), the current
question or activity above a boxed composer with a ``/`` command menu, and a hint
bar. It reads no run, imports no controller and adds no dependency: where a
terminal cannot run curses, the client keeps its line mode.
"""

from __future__ import annotations

import contextlib
import curses
import json
import locale
import os
import re
import signal
import subprocess
import sys
import textwrap
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

try:
    from . import autocode_chat_attention as attention
    from . import autocode_chat_attention_panel as attention_panel
    from . import autocode_chat_context as contexts
    from . import autocode_chat_inventory as inventory
    from . import autocode_chat_messages as messages
    from . import autocode_chat_meter as meter
    from . import autocode_chat_observation as observation
    from . import autocode_chat_panel as panel
    from . import autocode_chat_stages as stages
    from . import autocode_chat_stages_panel as stages_panel
    from . import autocode_chat_verification as verification
    from . import autocode_client_actions as actions
    from .autocode_util import replace_text
except ImportError:
    import autocode_chat_attention as attention
    import autocode_chat_attention_panel as attention_panel
    import autocode_chat_context as contexts
    import autocode_chat_inventory as inventory
    import autocode_chat_messages as messages
    import autocode_chat_meter as meter
    import autocode_chat_observation as observation
    import autocode_chat_panel as panel
    import autocode_chat_stages as stages
    import autocode_chat_stages_panel as stages_panel
    import autocode_chat_verification as verification
    import autocode_client_actions as actions
    from autocode_util import replace_text

PROGRESS_PREFIX = "· "
HISTORY_LIMIT = 100
HISTORY_FILE = "chat-history.jsonl"  # in the project's .autocode directory
BRAND = "AutoCode chat"
TAGLINE = "Plan, build and test, with a reviewer at every step."
EXAMPLES = (  # a new chat's welcome: the kinds of request AutoCode takes, in a person's words
    "Fix the failing test in tests/test_api.py",
    "Add a --json option to the export command",
    "Why does sign-in time out on slow networks?",
)
NEW_CHAT_PLACEHOLDER = "Ask for a change, a fix, or a question…"
REPLY_PLACEHOLDER = "Type your reply, then Enter"
# The reply box while AutoCode works: what typing does now. The footer already names "/ commands", and
# the status row says what is happening, so the box says neither again.
WORKING_PLACEHOLDER = "AutoCode asks here when it needs you"
STARTING = "Starting the run…"
QUESTION_COMING = "AutoCode has a question for you…"
# Enter while AutoCode works and offers no message: the text is kept, not sent, and says when it can be.
NOT_SENT = ("Not sent yet", "AutoCode is still working. Your text stays here until AutoCode asks you something.")
NOT_SENT_STEP_ENDED = (
    "Not sent yet",
    "AutoCode finished this step as you pressed Enter. Your text stays here; press Enter again when it asks you.",
)
# A question any text answers (autocode_chat_client's Requirements and resolver questions): a draft typed
# while AutoCode worked is the person's answer to it, so it stays in the box. An approval or a choice is
# never answered by text typed before it was shown.
FREE_REPLY = re.compile(
    r"(?:You(?: \((?:1-\d+)?(?:, )?(?:Enter for '.*')?\))?|Your reply \(Enter to leave it for now\)):"
)
STARTUP_GRACE_SECONDS = 30.0  # a launched run's first failed reads are its start, not an outage
MIN_ROWS, MIN_COLS = 12, 32  # below either the chat cannot be drawn


def too_small(rows: int, cols: int) -> str:
    return f"Terminal too small: need {MIN_COLS} columns, {MIN_ROWS} rows; now {cols}x{rows}."


LIVE_SHARE = 0.45  # of the conversation's rows the live log takes
PANEL_WRAP = {"Now": 3, "Acceptance checks": 2, "Tasks": 2}  # side panel rows a section's line may take
AGENT_STYLES = 4  # agents in the live log cycle through this many colors
COMPOSER_ROWS = 6  # the reply box grows to this many rows as the text wraps, then scrolls with the cursor
MENU_ROWS = 10  # at most; fewer where the screen is short, scrolled to keep the choice in view
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
PASTE_START, PASTE_END = "\x1b[200~", "\x1b[201~"  # bracketed paste, so a pasted newline is not Enter
ESCAPE = "\x1b"  # the Esc key on its own
WORDMARK = (
    "▄▀█ █░█ ▀█▀ █▀█ █▀▀ █▀█ █▀▄ █▀▀",
    "█▀█ █▄█ ░█░ █▄█ █▄▄ █▄█ █▄▀ ██▄",
)
_ESCAPE = re.compile(r"\x1b(?:\[[0-9;?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\)|[@-_])")
_LABELS = {"user": "You", "assistant": "AutoCode", "notice": "Notice", "info": ""}
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
        ("AutoCode stopped", "AutoCode needs you", "Left the chat", "Nothing sent", "Plan not approved", "Recovery ·")
    ):
        return "notice"
    return "plain"


TAB_MARKS = {"working": "●", "needs you": "!", "done": "✓", "unavailable": "unavailable"}
ASCII_TAB_MARKS = {"working": "*", "needs you": "!", "done": "+", "unavailable": "unavailable"}
CTRL_T = "\x14"
# Ctrl-← and Ctrl-→ as terminfo names them (xterm-style terminals); other terminals use /tab.
TAB_KEYS = {b"kLFT5": "previous", b"kRIT5": "next", b"kLFT3": "previous", b"kRIT3": "next"}
# The same two keys as the bytes terminals send when their terminfo defines no extended key: the
# Escape tail, after the leading Escape is gone. rxvt-unicode orders the modifier differently and
# xterm has the application-cursor form, so all three spellings arrive here (#1997).
CSI_TABS = {
    "[1;5C": "next",
    "[1;5D": "previous",
    "[5;5C": "next",
    "[5;5D": "previous",
    "[[C": "next",
    "[[D": "previous",
}


@dataclass(frozen=True)
class CsiTab:
    """Ctrl-←/→ read from the bytes the terminal sent, because curses named no key for them."""

    direction: str


def csi_tab(read, tail: list) -> CsiTab | None:
    """The tab step the characters after an Escape name, reading on while one still could.

    ``tail`` holds what an earlier probe already read after the Escape; it keeps every character this
    does not turn into a tab step, so a key sequence that is not one of these is still queued for the
    handler as it was before (#1997: without this the bytes land as text in the composer).
    """
    keys: list = []
    text = "".join(key for key in tail if isinstance(key, str))
    while any(name.startswith(text) for name in CSI_TABS):
        if text in CSI_TABS:
            return CsiTab(CSI_TABS[text])
        key = read()
        if key is None:
            break
        keys.append(key)
        if not isinstance(key, str):
            break
        text += key
    tail.extend(keys)
    return None


def tab_step(key) -> str | None:
    """What a key does to the tabs: "new", "next", "previous", or None for any other key."""
    if key == CTRL_T:
        return "new"
    if isinstance(key, CsiTab):
        return key.direction
    if isinstance(key, int) and key > 255:
        with contextlib.suppress(curses.error, ValueError):
            return TAB_KEYS.get(curses.keyname(key))
    return None


def read_history(path: Path) -> list[str]:
    """The project's earlier replies, oldest first; none when the file is missing or unreadable."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    kept = []
    for line in lines[-HISTORY_LIMIT:]:
        with contextlib.suppress(ValueError):
            if isinstance(text := json.loads(line), str):
                kept.append(text)
    return kept


def keep_history(path: Path, texts: list[str]) -> bool:
    """Add replies to the project's history in ``.autocode``, once AutoCode has made that directory
    (the chat never creates it); bounded to the last HISTORY_LIMIT. False: nothing could be written yet."""
    if not path.parent.is_dir():
        return False
    try:
        lines = [*read_history(path), *texts][-HISTORY_LIMIT:]
        replace_text(path, "".join(json.dumps(line) + "\n" for line in lines))
    except OSError:
        return False
    return True


def worth_keeping(text: str) -> bool:
    """A request or a message is recalled later; a bare yes, no or option number is not."""
    word = text.strip().lower()
    return bool(word) and word not in ("y", "yes", "n", "no") and not word.isdigit()


def composer_label(prompt: str, asking: bool) -> str:
    """What the box is for: starting a chat, answering a question, or waiting."""
    if not asking:
        return "AutoCode"
    prompt = prompt.lower()
    if "new request" in prompt or "what do you want done" in prompt:
        return "New chat"
    return "Reply"


def free_reply(prompt: str) -> bool:
    """Whether any text answers this question, so a draft typed before it was asked may answer it."""
    return bool(FREE_REPLY.fullmatch(" ".join(prompt.split())))


def _same(block: tuple[str, str], other: tuple[str, str]) -> bool:
    return block[0] == other[0] and " ".join(block[1].split()) == " ".join(other[1].split())


FENCE = "```"
INLINE = re.compile(r"`([^`\n]+)`|\*\*([^*\n]+)\*\*")  # `code` and **bold** in a message line
INLINE_STYLES = ("assistant-body", "plain", "notice-body")
PROSE_EMPHASIS = re.compile(
    r"`[^`\n]*`|(?<![\w\\])_([^\s_](?:[^\n]*?[^\s_])?)_(?![\w_])"
    r"|(?<![\w\\*])\*([^\s*](?:[^\n]*?[^\s*])?)\*(?![\w*]|\.\w)"
)


def prose_text(text: str) -> str:
    """Remove paired prose italics, preserving inline code, identifiers and operators."""
    return PROSE_EMPHASIS.sub(lambda match: match.group(1) or match.group(2) or match.group(), text)


def inline_pieces(text: str) -> list[tuple[str, str]]:
    """A message line as ``(kind, text)`` pieces: ``code`` for a backticked span, ``bold`` for a
    starred one (their marks dropped), ``text`` for the rest."""
    pieces: list[tuple[str, str]] = []
    at = 0
    for match in INLINE.finditer(text):
        if match.start() > at:
            pieces.append(("text", text[at : match.start()]))
        pieces.append(("code", match.group(1)) if match.group(1) is not None else ("bold", match.group(2)))
        at = match.end()
    if at < len(text):
        pieces.append(("text", text[at:]))
    return pieces


def wrapped_rows(text: str, width: int, most: int) -> list[str]:
    """``text`` in at most ``most`` rows of ``width``; the last one ends in an ellipsis when cut."""
    if most <= 1:
        return [text]
    lines = wrap(text, width)
    if len(lines) <= most:
        return lines
    return [*lines[: most - 1], truncate(" ".join(lines[most - 1 :]), width)]


def _diff_style(line: str) -> str | None:
    """A unified diff line's kind, for its color: a file header, a hunk, an addition or a removal."""
    if line.startswith(("diff --git", "+++", "---", "index ")):
        return "diff-file"
    if line.startswith("@@"):
        return "diff-hunk"
    if line.startswith("+"):
        return "diff-add"
    if line.startswith("-"):
        return "diff-del"
    return None


def body_rows(style: str, body: str, width: int, *, diff: bool = False) -> list[tuple[str, str]]:
    """A block's body as indented ``(style, text)`` rows. A fenced code block is drawn as code under a
    thin rule naming its language, and a diff's lines in their kind's color; the rest keeps ``style``.
    Each source line wraps on its own, so a wrapped line keeps its line's style."""
    rows: list[tuple[str, str]] = []
    fenced = patch = False
    for line in body.split("\n"):
        kind = style
        if diff:
            # Only the patch is colored, not the file list and words before it.
            patch = patch or line.startswith(("diff --git", "--- ", "@@"))
            kind = (_diff_style(line) if patch else None) or style
        elif line.strip().startswith(FENCE):
            language = line.strip()[len(FENCE) :].strip()
            fenced = not fenced
            rule = "─" * max(1, min(width - 2, 40) - len(language) - (1 if language else 0))
            rows.append(("code-fence", "  " + (f"{language} {rule}" if fenced and language else rule)))
            continue
        elif fenced:
            kind = "code"
        if not diff and not fenced and kind != "code":
            line = prose_text(line)
        rows.extend((kind, "  " + part) for part in wrap(line, max(1, width - 2)))
    return rows


@dataclass
class Transcript:
    """What the client has said, and the latest progress line it reported."""

    blocks: list[tuple[str, str]] = field(default_factory=list)
    activity: str = ""
    live: str = ""  # an assistant reply still being written (the view's streaming message), shown last
    thinking: dict[str, str] = field(default_factory=dict)  # each working agent's newest thought (#2699)
    asked: int = 0  # how many blocks there were when the last question was put to the person
    block_starts: list[int] = field(default_factory=list)  # set by lines(): where each spoken block begins
    progress_blocks: dict[str, int] = field(default_factory=dict)
    _listing_index: int | None = field(default=None, init=False, repr=False)
    _revision: int = field(default=0, init=False, repr=False)
    _lines_width: int | None = field(default=None, init=False, repr=False)
    _lines_revision: int = field(default=-1, init=False, repr=False)
    _lines_live: str | None = field(default=None, init=False, repr=False)
    _lines_cache: list[tuple[str, str]] = field(default_factory=list, init=False, repr=False)
    _thoughts: dict[str, set[str]] = field(default_factory=dict, init=False, repr=False)  # thought ids per agent
    _folds: dict[str, int] = field(default_factory=dict, init=False, repr=False)  # each agent's folded block

    @property
    def last_block_start(self) -> int | None:
        """The newest spoken block's first row, for anyone placing a block's heading."""
        return self.block_starts[-1] if self.block_starts else None

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
        self._revision += 1
        return True

    def info(self, title: str, body: str, *, coalesce: bool = False) -> None:
        """What a ``/`` command shows: the chat's own words, not the run's conversation."""
        block = ("info", f"{title}\n{safe_text(body)}")
        if coalesce and self.blocks and self.blocks[-1] == block:
            return
        self.blocks.append(block)
        self._revision += 1

    def show_listing(self, body: str) -> None:
        """Replace the local picker without making its rows durable chat messages."""
        self.dismiss_listing()
        self.info("/runs", body)
        self._listing_index = len(self.blocks) - 1

    def dismiss_listing(self) -> bool:
        """Remove only the tagged picker, retaining conversation, prompts and progress identities."""
        index = self._listing_index
        if index is None:
            return False
        del self.blocks[index]
        self.progress_blocks = {
            key: position - 1 if position > index else position for key, position in self.progress_blocks.items()
        }
        self.asked -= int(index < self.asked)
        self._listing_index = None
        self._revision += 1
        return True

    def progress(self, view: dict) -> None:
        """Keep readable agent updates in the same history, replacing streamed snapshots in place."""
        self.relabel_stops(view)
        current: dict[str, str] = {}
        for row in panel.log_entries(view):
            text = panel.live_text(row["text"])
            if row["kind"] == "thinking" and text.strip() and row.get("status") != "streaming":
                # The newest thought of each agent working now is shown live under the conversation; the
                # earlier ones stay in /live (#2699).
                current[row["agent"]] = text
                self._thoughts.setdefault(row["agent"], set()).add(row["id"])
            if row["kind"] != "message" or row.get("status") == "streaming" or not text.strip():
                continue
            block = ("info", f"{safe_text(row['agent'])}\n{safe_text(text)}")
            index = self.progress_blocks.get(row["id"])
            if index is None:
                self.progress_blocks[row["id"]] = len(self.blocks)
                self.blocks.append(block)
                self._revision += 1
            elif self.blocks[index] != block:
                self.blocks[index] = block
                self._revision += 1
        self._fold_thinking(current)

    def _fold_thinking(self, current: dict[str, str]) -> None:
        """An agent that left the view has finished its stage: its live thought folds into one line of
        history that says how many thoughts there were and where to read them (#2699). An agent back at
        work opens a new live section."""
        for agent in list(self.thinking):
            if agent in current:
                continue
            count = len(self._thoughts.get(agent, ()))
            noun = "thought" if count == 1 else "thoughts"
            block = (
                "info",
                f"{safe_text(agent)} · thinking\n▸ {count} {noun} folded; /live {safe_text(agent)} shows them",
            )
            index = self._folds.get(agent)
            if index is not None and index == len(self.blocks) - 1:
                self.blocks[index] = block
            else:
                self._folds[agent] = len(self.blocks)
                self.blocks.append(block)
            self._revision += 1
        for agent in current:
            if agent not in self.thinking:
                self._folds.pop(agent, None)  # back at work: its next fold is a new line of history
        if current != self.thinking:
            self.thinking = current
            self._revision += 1

    def relabel_stops(self, view: dict) -> None:
        """A fresh running observation makes older stop diagnostics history, preserving their detail."""
        if view.get("status") != "RUNNING" or not actions.working(view):
            return
        for index, (role, text) in enumerate(self.blocks):
            for expected_role, prefix, label in (
                ("plain", "Coordination stopped:", "Earlier coordination error"),
                ("notice", "AutoCode stopped:", "Earlier stop"),
            ):
                if role == expected_role and text.lstrip().startswith(prefix):
                    reason = text.lstrip()[len(prefix) :].lstrip()
                    self.blocks[index] = ("info", label + "\n" + reason)
                    self._revision += 1
                    break

    def visible_progress(self, width: int, height: int, scroll: int) -> set[tuple[str, str]]:
        """Agent updates whose body intersects the current transcript viewport."""
        if height <= 0:
            return set()
        lines = self.lines(width)
        end = len(lines) - min(scroll, max(0, len(lines) - height))
        start = max(0, end - height)
        visible = set()
        for index in self.progress_blocks.values():
            agent, _, text = self.blocks[index][1].partition("\n")
            first = self.block_starts[index] + 1
            last = first + len(body_rows("info-body", text, width))
            if first < end and last > start:
                visible.add((agent, text.strip()))
        return visible

    def closing(self) -> list[str]:
        """What to leave on the terminal after the screen closes: the client's last words, whole.

        Those are the blocks after the last conversation message (a hand-off's reason and
        the command to run, where to pick the run up, Done.); the last block when there are none.
        Content already shown for the last question (a run list or plans to approve) is
        never printed again, even when conversation messages precede it.
        """
        said = [
            (index, role, text)
            for index, (role, text) in enumerate(self.blocks)
            if index >= self.asked and role != "info"
        ]
        tail: list[tuple[int, str]] = []
        for index, role, text in reversed(said):
            if role in ("user", "assistant"):
                return [text for _, text in tail] or [text]
            tail.insert(0, (index, text))
        return [text for index, text in tail if index >= self.asked]

    def lines(self, width: int) -> list[tuple[str, str]]:
        """The transcript as ``(style, text)`` rows, labels over indented bodies, remembering
        where the newest spoken block begins so a tall one can anchor to its top."""
        if self._lines_width == width and self._lines_revision == self._revision and self._lines_live is self.live:
            return self._lines_cache
        rendered: list[tuple[str, str]] = []
        starts: list[int] = []
        talked = False  # once the person has spoken, the client's own words are AutoCode's turn
        previous = ""
        progress_indices = set(self.progress_blocks.values())
        for index, (role, text) in enumerate(self.blocks):
            starts.append(len(rendered))
            label = _LABELS.get(role)
            talked = talked or role == "user"
            if label is None and talked:
                # The client's unprefixed words (a plan to approve, a spend confirmation) are
                # AutoCode speaking: under its label, or continuing the AutoCode turn before them.
                if previous not in ("assistant", "plain"):
                    rendered.append(("assistant-label", _LABELS["assistant"]))
                rendered.extend(body_rows("plain", text.strip("\n"), width))
            elif label is None:
                rendered.extend(("plain", line) for line in wrap(text, width))
            else:
                body = text
                prefix = _PREFIXES.get(role)
                if prefix and body.lstrip().startswith(prefix):
                    body = body.lstrip()[len(prefix) :].lstrip()
                if role == "info":
                    label, _, body = text.partition("\n")
                rendered.append((f"{role}-label", label))
                if role == "user":
                    rendered.extend((f"{role}-body", "  " + line) for line in wrap(body, max(1, width - 2)))
                else:
                    body_style = "assistant-body" if index in progress_indices else f"{role}-body"
                    rendered.extend(
                        body_rows(body_style, body, width, diff=role == "info" and label.startswith("/diff"))
                    )
            rendered.append(("plain", ""))
            previous = role
        for agent, thought in self.thinking.items():
            # One line of live thinking per working agent; /live NAME unfolds the rest (#2699).
            rendered.append(("info-label", f"{safe_text(agent)} · thinking"))
            rendered.extend(body_rows("live-thinking", prose_text(safe_text(thought)), width))
            rendered.append(("plain", ""))
        if self.live.strip():
            rendered.append(("assistant-label", "AutoCode · writing…"))
            text = safe_text(self.live).strip("\n")
            rendered.extend(body_rows("assistant-body", text, width))
            rendered.append(("plain", ""))
        self.block_starts = starts
        self._lines_width = width
        self._lines_revision = self._revision
        self._lines_live = self.live
        self._lines_cache = rendered
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

    def line_up(self) -> bool:
        """Move to the line above, keeping the column where it fits; False on the first line."""
        start = self.text.rfind("\n", 0, self.cursor) + 1
        if not start:
            return False
        above = self.text.rfind("\n", 0, start - 1) + 1
        self.cursor = min(above + self.cursor - start, start - 1)
        return True

    def line_down(self) -> bool:
        """Move to the line below, keeping the column where it fits; False on the last line."""
        start = self.text.rfind("\n", 0, self.cursor) + 1
        end = self.text.find("\n", self.cursor)
        if end < 0:
            return False
        below_end = self.text.find("\n", end + 1)
        self.cursor = min(end + 1 + self.cursor - start, len(self.text) if below_end < 0 else below_end)
        return True

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


def read_paste(read, consumed: list | None = None) -> str | None:
    """After an escape key, the text of a bracketed paste; None when the keys were something else.

    ``read`` returns the next key, or None when no key follows promptly. A pasted
    line break stays in the text instead of submitting the reply. When the keys are
    not a paste, the ones read are added to ``consumed`` so the caller can still use them.
    """
    keys: list = []
    opening = ""
    while len(keys) < len(PASTE_START) - 1 and PASTE_START.startswith("\x1b" + opening):
        key = read()
        if key is None:
            break
        keys.append(key)
        if not isinstance(key, str):
            break
        opening += key
    if len(opening) != len(keys) or "\x1b" + opening != PASTE_START:
        if consumed is not None:
            consumed.extend(keys)
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


def composer_rows(text: str, cursor: int, width: int, most: int) -> tuple[list[str], int, int]:
    """``text`` as the reply box shows it: wrapped at ``width`` cells and at its own line breaks, at
    most ``most`` rows kept around the cursor; with the cursor's row and column among those rows.
    Breaks at spaces when possible, as ``wrap`` does, so words are not split mid-word."""
    width, most = max(1, width), max(1, most)
    rows: list[str] = []
    at_row = at_col = 0
    row, used = "", 0
    for index, char in enumerate(text + "\0"):  # a sentinel closes the last row and places a cursor at the end
        if index == cursor:
            if used >= width and char != "\n":
                rows.append(row)
                row, used = "", 0
            at_row, at_col = len(rows), used
        if char in ("\n", "\0"):
            rows.append(row)
            row, used = "", 0
        else:
            cells = _char_width(char)
            if used + cells > width:
                # Break at the last space in this row if one exists, so words stay whole.
                space = row.rfind(" ")
                if space > 0:
                    overflow = row[space:]  # keep the space at the start of the next row
                    row = row[:space]
                    rows.append(row)
                    row, used = overflow, sum(_char_width(c) for c in overflow)
                else:
                    rows.append(row)
                    row, used = "", 0
                if index == cursor:
                    at_row, at_col = len(rows), used
            row += char
            used += cells
    if at_row >= len(rows):
        rows.append("")
    first = min(max(0, at_row - most + 1), max(0, len(rows) - most))
    return rows[first : first + most], at_row - first, at_col


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
    body_width: int = 0  # the transcript's width; the panel takes the rest
    panel_col: int | None = None
    live_start: int | None = None  # the live log's title row, below the transcript; it ends at panel_end
    panel_end: int = 0  # where the side panel ends: the transcript's end, or the live log's
    notes_start: int | None = None
    notes: list[str] = field(default_factory=list)
    box_rows: int = 1  # the reply box's text rows, from box_text down; it grows with a long reply


def _unicode_terminal() -> bool:
    encoding = getattr(sys.stdout, "encoding", "") or locale.getpreferredencoding(False)
    return "utf" in encoding.lower()


class ChatTUI:
    """The full-screen renderer behind one ChatClient; use ``ask`` and ``say``."""

    def __init__(self):
        self._screen = None
        self._previous_mouse_mask: int | None = None
        self._transcript = Transcript()
        self._composer = Composer()
        self._contexts = contexts.Contexts()
        self._context_key: tuple | None = None
        self._draft_context: tuple | None = None
        self._draft_unasked = False  # the draft was begun while AutoCode worked, not at a question
        self._task_conversation: dict | None = None
        self._restore_context: tuple | None = None
        self._restored_session = False
        self._observation = observation.Read()
        self._attention_view: dict = {}
        self._attention_menu: attention.Menu | None = None
        self._attention_top = 0
        self._stage_menu: stages.Menu | None = None  # /stages: one list per stage, like a dropdown
        self._stage_top = 0
        self._prompt = ""
        self._asking = False
        self.idle: Callable[[], None] | None = None
        self._scroll = 0
        self._anchored = False  # the tail-anchor this chat chose itself; the person's paging owns the scroll otherwise
        self._closed = False
        self._context = ""
        self._glyphs = UNICODE_GLYPHS if _unicode_terminal() else ASCII_GLYPHS
        self._styles: dict[str, int] = {}
        self._frame = 0
        self._lock = threading.RLock()
        self._stopping = threading.Event()
        self._ticker: threading.Thread | None = None
        self._view: dict | None = None
        self._launch_execution: dict | None = None
        self._log = panel.StageLog()
        self._live = panel.LiveLog()  # what each agent thinks and does, from the view's chat.log
        self._live_on: bool | None = None  # detailed agent pane appears only on request
        self._panel_on = None  # None: shown on a wide window (#3070); /panel then shows or hides it
        self._cost_cap: float | None = None  # session cost cap: warn and pause when hit (#3002)
        self._cost_cap_warned = False
        self._pause_requested = False
        self._menu_index = 0
        self._waiting = False
        self._launched = False  # a step is running since the last question; its first views may predate it
        self._launched_at: float | None = None  # when the first step began, before any status was read
        # The client's own commands (/diff, /undo), and one of them waiting for the person's yes.
        self._act: Callable[[str, str], str | panel.Confirm | attention.Menu] | None = None
        self.send_message = None
        self._message_retry = None
        self._message_sending = False
        self._message_keys = []
        self._run_open = True  # whether a chat is open: the run-only commands wait for one
        # Whether the bound act is a chat client's (bind run=True): a tab that Ctrl-T opened has no run
        # yet, but that client still serves /tab, so leaving it is allowed (#1997).
        self._client_bound = False
        self._task_usage = meter.TaskUsage()
        self._tabs: list[dict] = []  # the session's chats from the client, drawn once there are two
        self._pending: panel.Confirm | None = None
        self._queued: list = []  # keys read while telling an escape key from a paste
        self._history_file: Path | None = None  # the project's ↑/↓ history, kept between chats
        self._unsaved: list[str] = []  # replies typed before the project's .autocode exists
        self._open()

    # ---- lifecycle -------------------------------------------------------------------------

    def _open(self) -> None:
        with contextlib.suppress(Exception):
            locale.setlocale(locale.LC_ALL, "")
        try:
            self._screen = curses.initscr()
            curses.noecho()
            curses.cbreak()
            # Keep a typed or pasted CR as CR: in curses' default nl mode the terminal turns it into NL, so a
            # pasted CRLF reached read_paste as two line breaks (#1758). Enter then arrives as "\r", taken as Enter.
            curses.nonl()
            with contextlib.suppress(curses.error):
                self._screen.keypad(True)
            with contextlib.suppress(curses.error):
                curses.curs_set(0)
            self._start_colors()
            self._bracketed_paste(True)
            self._mouse_reporting(True)
        except Exception as error:  # a terminal without curses support keeps line mode
            with contextlib.suppress(Exception):
                curses.endwin()
            raise RuntimeError(f"the terminal cannot run the full-screen chat: {error}") from None
        self._ticker = threading.Thread(target=self._tick, name="autocode-chat-tui", daemon=True)
        self._ticker.start()

    def set_context(self, workspace: Path, run_dir: Path | None = None) -> None:
        self._context_key = contexts.identity(workspace, run_dir)
        history_file = Path(workspace) / ".autocode" / HISTORY_FILE
        if self._history_file != history_file:
            self._history_file = history_file
            self._composer.history = read_history(self._history_file)
        self._context = contexts.label(workspace, run_dir)
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
            self._mouse_reporting(False)
            self._bracketed_paste(False)
            with contextlib.suppress(curses.error):
                screen.keypad(False)
            with contextlib.suppress(curses.error):
                curses.nocbreak()
                curses.echo()
                curses.nl()
                curses.curs_set(1)
            with contextlib.suppress(curses.error):
                curses.endwin()
        self._save_history()  # a first request, typed before its run made .autocode
        for text in self._transcript.closing():
            print(text.strip("\n"))

    @staticmethod
    def _wheel_down() -> int:
        """The mouse-event bit a wheel turned down sets. ncurses built with mouse version 1 (the
        library Python links on macOS) has no BUTTON5_PRESSED and reports button 5 in the
        REPORT_MOUSE_POSITION bit, so asking for BUTTON4/5 alone lost every wheel-down there: a
        person could scroll the transcript up and never back down. The xterm mode ncurses enables
        (1000) reports presses only, so on that library the bit carries no pointer motion."""
        return getattr(curses, "BUTTON5_PRESSED", 0) or getattr(curses, "REPORT_MOUSE_POSITION", 0)

    def _mouse_reporting(self, on: bool) -> None:
        """Request wheel events while the chat owns the terminal; restore its prior mask."""
        with contextlib.suppress(curses.error):
            if on and self._previous_mouse_mask is None:
                wheels = getattr(curses, "BUTTON4_PRESSED", 0) | self._wheel_down()
                _, self._previous_mouse_mask = curses.mousemask(wheels)
            elif not on and self._previous_mouse_mask is not None:
                curses.mousemask(self._previous_mouse_mask)
                self._previous_mouse_mask = None

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
            if isinstance(text, inventory.RefreshNotice):
                return
            if isinstance(text, inventory.Listing):
                if isinstance(text, inventory.ListingUpdate) and self._transcript._listing_index is None:
                    return
                self._transcript.show_listing(text)
                layout = self._layout(*self._size())
                lines = self._transcript.lines(layout.body_width or layout.width)
                start = self._transcript.block_starts[-1]
                self._scroll = max(0, len(lines) - start - (layout.body_end - layout.body_start))
                self._anchored = False
                self._draw()
                return
            anchor = self._history_anchor()
            added = self._transcript.add(text)
            self._keep_history_anchor(anchor)
            if added and (anchor is None or self._anchored):
                # While the reply grows, only the newest block anchors: a short block after a
                # tall one (a pause acknowledgement, the next command's answer) takes the view
                # back to the tail so it is not hidden below the fold. The person's own paged
                # position (anchor kept, not anchored) is never disturbed.
                self._scroll = 0
                self._anchored = False
                self._anchor_tall_block()
            self._draw()

    def _anchor_tall_block(self, *, for_question: bool = False) -> None:
        """Open the reply's tall block at its top, in the scroll's own coordinates.

        A long approval now carries its milestones and flow (#2434); following the tail would
        draw only its end. The candidates are the run's own spoken blocks since the person last
        replied — the reply they were last asked to read: a listing the person asked for
        (/details, /runs) is theirs to page, and a block they answered before that reply is
        never reached back for. While the reply grows, only its newest block anchors, so a short
        block after a tall one (a pause acknowledgement, the next command's answer) takes the
        view back to the tail and stays visible; a question may cross the reply's closing
        blocks (where the recorded plan lives, a change-set hint) to open the tall plan they
        follow, but never crosses a block of half the body or more, which is the reply's own
        content and stays on screen. The anchor is plain scroll state, so the History header shows it, PageDown
        walks forward through the block toward the live tail, PageUp walks back, and the tail
        is followed again exactly when the person pages back to it.
        """
        layout = self._layout(*self._size())
        lines = self._transcript.lines(layout.body_width or layout.width)
        height = max(0, layout.body_end - layout.body_start)
        starts = self._transcript.block_starts
        blocks = self._transcript.blocks
        if not height or not starts:
            return
        spoken = max((index for index, (role, _) in enumerate(blocks) if role == "user"), default=-1)
        first = 0 if for_question else len(starts) - 1
        for index in range(len(starts) - 1, first - 1, -1):
            # The run's own spoken reply — prefixed words and the unprefixed plans and
            # acknowledgements the client says — anchors; a listing the person asked for
            # (/details, /runs) is theirs to page, and operational notices never pin the view.
            if index <= spoken or blocks[index][0] not in ("assistant", "plain"):
                continue
            start = starts[index]
            end = starts[index + 1] if index + 1 < len(starts) else len(lines)
            if end - start > height:
                self._scroll = len(lines) - start - height
                self._anchored = True
                return
            if 2 * (end - start) >= height:
                # A block this big is what the question is about (a plan that fits), not a
                # closing note: reaching past it to an older tall block (a repeated summary)
                # would scroll it off the screen the person is asked to approve.
                return

    def _history_anchor(self) -> tuple[int, int] | None:
        if not self._scroll:
            return None
        layout = self._layout(*self._size())
        count = len(self._transcript.lines(layout.body_width or layout.width))
        return count, max(0, layout.body_end - layout.body_start)

    def _keep_history_anchor(self, before: tuple[int, int] | None) -> None:
        if before is None:
            return
        after = self._history_anchor()
        if after is not None:
            self._scroll = max(1, self._scroll + after[0] - before[0] + before[1] - after[1])

    def _save_history(self, *texts: str) -> None:
        self._unsaved.extend(texts)
        if self._history_file is not None and keep_history(self._history_file, self._unsaved):
            self._unsaved = []

    def outside(self, argv: list[str], label: str = "AutoCode") -> int:
        """Run an interactive command (a backend's sign-in or setup) on the terminal itself: the screen
        steps aside, the command gets the keyboard and Ctrl-C, and the chat comes back afterwards."""
        with self._lock:
            screen = self._screen
            if self._closed or screen is None:
                return subprocess.call(argv)
            self._mouse_reporting(False)
            self._bracketed_paste(False)
            with contextlib.suppress(curses.error):
                curses.endwin()
            print(f"\n{label}: the chat comes back when it finishes.\n", flush=True)
            previous = signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl-C is for the command, not the chat
            try:
                code = subprocess.call(argv, preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_DFL))
            except OSError as error:
                print(f"Could not run it: {error}")
                code = 127
            finally:
                signal.signal(signal.SIGINT, previous)
                with contextlib.suppress(curses.error):
                    screen.clearok(True)
                    screen.refresh()
                self._bracketed_paste(True)
                self._mouse_reporting(True)
            self._draw()
            return code

    def tabs(self, rows: list[dict]) -> None:
        """This session's chats, as the client numbers them: drawn as tabs once there is more than one."""
        with self._lock:
            if self._tabs == rows:
                return
            self._tabs = list(rows)
            self._draw()

    def _tab_key(self, step: str, asking: bool) -> str | None:
        """Ctrl-T opens a new chat, Ctrl-←/→ the next or previous tab; at a question only, as /new and
        /tab, so a step is never left running unwatched. Before a chat Ctrl-T answers as /new does there.
        A tab Ctrl-T opened, whose request is not sent yet, leaves nothing running: it can be left (#1997)."""
        name, argument = ("/new", "") if step == "new" else ("/tab", step)
        if name == "/new" and self._act is not None:
            # Ctrl-T opens a new tab; it never disturbs the current run, so it works at any time.
            return self._run(name, asking)
        draft = self._draft_tab(asking)
        if not draft and ((not asking and not self._can_detach()) or self._act is None or not self._run_open):
            if self._restored_session and self._view is None:
                self._transcript.info(name, "Reading this tab's current status; try switching again once it arrives.")
                return None
            why = "a chat is open" if not self._run_open else "AutoCode asks you something (or after /pause)"
            self._transcript.info(name, f"Tabs switch once {why}.")
            return None
        return self._run(name, asking, argument)

    def switch(self, workspace: Path, run_dir: Path | None) -> bool:
        """Restore this session's display context, never an old executable view/action."""
        with self._lock:
            if self._transcript.dismiss_listing():
                self._scroll, self._anchored = 0, False
            fields = (
                "_transcript",
                "_composer",
                "_log",
                "_live",
                "_scroll",
                "_anchored",
                "_launched",
                "_live_on",
                "_panel_on",
                "_prompt",
                "_draft_context",
                "_draft_unasked",
                "_history_file",
                "_unsaved",
                "_observation",
                "_message_retry",
            )
            if self._context_key is not None:
                self._contexts.keep(
                    self._context_key,
                    {
                        **{name: getattr(self, name) for name in fields},
                        "context": self._draft_identity() or self._restore_context,
                    },
                )
            saved = self._contexts.get(contexts.identity(workspace, run_dir))
            if saved:
                for name in fields:
                    setattr(self, name, saved[name])
            else:
                self._transcript, self._composer = Transcript(), Composer()
                self._log, self._live = panel.StageLog(), panel.LiveLog()
                self._scroll, self._anchored, self._launched = 0, False, False
                self._live_on, self._panel_on = None, None
                self._prompt, self._draft_context, self._draft_unasked = "", None, False
                self._history_file, self._unsaved = None, []
                self._observation = observation.Read()
                self._message_retry = None
            self._restore_context = saved["context"] if saved else None
            self._restored_session = saved is not None
            # Refresh before offering decisions. Confirm closures and queued keys
            # from another run must never transfer with a display snapshot.
            self._view, self._pending, self._queued, self._menu_index = None, None, [], 0
            self._task_conversation = None
            self._launch_execution = None
            self._attention_menu = None
            self._stage_menu = None
            self._run_open = run_dir is not None
            self.set_context(workspace, run_dir)
            return saved is not None

    def bind(self, act: Callable[[str, str], str | panel.Confirm | attention.Menu], *, run: bool = True) -> None:
        """The client that carries out ``/diff`` and ``/undo``: act(name, argument) returns what to
        show, or a ``Confirm`` to ask first. It may raise to leave the question being asked.
        ``run`` false: no chat is open yet, so the menu offers only what works without one."""
        with self._lock:
            self._act = act
            self._run_open = run
            self._client_bound = run

    def stream(self, text: str | None) -> None:
        """The reply AutoCode is writing now, from the view's streaming message; None once it is done."""
        with self._lock:
            if self._closed or self._transcript.live == (text or ""):
                return
            anchor = self._history_anchor()
            self._transcript.live = text or ""
            self._keep_history_anchor(anchor)
            self._draw()

    def observe_coordination(self, snapshot: dict) -> None:
        """The core's complete task ledger inventory, separate from the active repository view."""
        with self._lock:
            self._task_usage.accept(snapshot)
            self._draw()

    def observe_conversation(self, summary: dict) -> None:
        """Keep the task's named composer offer while member progress is displayed."""
        with self._lock:
            self._task_conversation = summary
            self._draw()

    def observe(self, view: dict) -> None:
        """The status view the client just read: the panel and the log draw from it alone."""
        with self._lock:
            self._observation.accept(view, time.monotonic())
            self._task_usage.observe(view)
            if self._view == view:
                self._draw()
                return
            if actions.working(view) and not actions.working(self._view or {}) and self._transcript.dismiss_listing():
                self._scroll, self._anchored = 0, False
            anchor = self._history_anchor()
            self._view = view
            self._launch_execution = None
            self._log.observe(view)
            self._live.observe(view)
            self._transcript.progress(view)
            if view.get("workspace") and view.get("run_dir"):  # the run the chat talks to, after going back too
                self._context_key = contexts.identity(view["workspace"], view["run_dir"])
                self._context = contexts.label(view["workspace"], view["run_dir"])
            self._keep_history_anchor(anchor)
            self._check_cost_cap(view)
            self._draw()

    def _check_cost_cap(self, view: dict) -> None:
        """Warn and pause when the session cost hits the configured cap (#3002)."""
        if self._cost_cap is None:
            return
        cost = view.get("cost") or {}
        spent = cost.get("spent_usd")
        if not isinstance(spent, (int, float)) or spent < self._cost_cap:
            self._cost_cap_warned = False
            return
        if self._cost_cap_warned:
            return
        self._cost_cap_warned = True
        self._transcript.info(
            "Cost cap reached",
            f"Session spend ${spent:.2f} has reached your ${self._cost_cap:.2f} cap. "
            "The run is paused. Use /cap to change the cap or resume to continue.",
        )
        self._pause_requested = True

    def status_unavailable(self, error: str) -> None:
        """A failed read does not establish that the worker stopped."""
        with self._lock:
            self._observation.fail(error)
            self._draw()

    def attention(self, summary: dict) -> None:
        with self._lock:
            if summary == self._attention_view:
                return
            self._attention_view = summary
            if self._attention_menu is not None and summary.get("notice"):
                self._attention_menu.note = summary["notice"]
            self._draw()

    def worker_launch(self, receipt: dict) -> None:
        """Only the core launch receipt can permit detach before the first status view."""
        with self._lock:
            self._launch_execution = receipt.get("execution")
            self._draw()

    def _draft_tab(self, asking: bool) -> bool:
        """The client's tab that Ctrl-T opened, its request not sent yet: no run, nothing running, so
        /tab (and the tab keys) may leave it; before any chat there is no client and no other tab (#1997)."""
        return asking and self._client_bound and not self._run_open

    def _can_detach(self) -> bool:
        execution = self._launch_execution or (self._view or {}).get("execution") or {}
        return execution.get("mode") == "durable_local" and execution.get("detach_supported") is True

    def _typed_ahead_for(self, prompt: str, current) -> bool:
        """A draft the person began while this chat's run worked answers its next free-text question.

        They read AutoCode's reply and started their answer before the question's box opened: moving
        that text into history behind an "Earlier draft kept" notice lost it for no reason. Only a
        question any text answers keeps it, and only in the chat it was typed in; an approval or a
        choice never takes text typed before it was shown."""
        typed = self._draft_context
        return (
            self._draft_unasked
            and free_reply(prompt)
            and typed is not None
            and current is not None
            and typed[0] == current[0]
            and typed[1] in (None, current[1])
        )

    def _draft_identity(self):
        current = contexts.draft_identity(self._view)
        if current is not None:
            return current
        if self._context_key is not None and self._context_key[1] is None and not self._run_open:
            return (*self._context_key, "local-new-request")  # UI data, never a core run/action token
        return None

    def wait(self, seconds: float) -> str | None:
        """The client's pause between polls while AutoCode works: the composer takes ``/`` commands.

        Returns "pause" when the person asked for the view's pause; the client takes it.
        """
        if self._closed or self._screen is None:
            time.sleep(seconds)
            return None
        deadline = time.monotonic() + seconds
        if not self._asking:
            self._launched = True
            if self._launched_at is None:
                self._launched_at = time.monotonic()
        was_waiting = self._waiting
        self._waiting = True  # this loop animates the marker; the ticker must not draw beside a key read
        try:
            while (left := deadline - time.monotonic()) > 0:
                if self._pause_requested:
                    self._pause_requested = False
                    return "pause"
                if self.idle is not None:
                    self.idle()
                with self._lock:  # curses is not thread-safe: read, handle and draw as one step
                    if self._closed or self._screen is None:
                        return None
                    self._screen.timeout(max(1, int(min(left, 0.1) * 1000)))
                    key = self._read_key()
                    action = self._handle(key, asking=False)
                    if key is not None or self._animate():
                        self._frame += 1
                        self._draw()
                if action is not None:
                    return action
        finally:
            self._waiting = was_waiting
            with self._lock, contextlib.suppress(curses.error, AttributeError):
                self._screen.timeout(100 if self._asking or was_waiting else -1)
        return None

    def _discard_typeahead(self) -> bool:
        """Drop unseen answers, retaining an exit on an empty draft at the prompt boundary."""
        exit_requested = False
        self._screen.timeout(0)
        try:
            for _ in range(4096):
                key = self._read_key()
                if key is None:
                    break
                if key == "\x04" and not self._composer.text:
                    exit_requested = True
        finally:
            with contextlib.suppress(curses.error):
                curses.flushinp()
            self._screen.timeout(100 if self._asking or self._waiting else -1)
        return exit_requested

    def ask(self, prompt: str) -> str:
        with self._lock:
            if self._closed:
                raise EOFError
            asked_context = self._context_key
            reading = self._history_anchor() if not self._anchored else None
            if self._discard_typeahead():
                raise EOFError
            if self._launched:
                with contextlib.suppress(curses.error):
                    curses.beep()  # AutoCode worked and now needs you: the terminal's own alert
            self._transcript.live = ""
            self._transcript.asked = len(self._transcript.blocks)
            prompt = " ".join(prompt.split())
            current_context = self._draft_identity()
            keep_context = (
                self._restored_session
                and self._restore_context is not None
                and self._restore_context == current_context
                and prompt == self._prompt
            )
            keep_draft = bool(self._composer.text) and (
                (self._draft_context is not None and self._draft_context == current_context and prompt == self._prompt)
                or self._typed_ahead_for(prompt, current_context)
            )
            if self._composer.text and not keep_draft:
                self._composer.history = [*self._composer.history, self._composer.text][-HISTORY_LIMIT:]
                self._transcript.info(
                    "Earlier draft kept",
                    "The prompt changed. After checking this question, press Up to recover your earlier draft.",
                )
            self._prompt = prompt
            self._asking = True
            self._launched = False
            self._pending = None
            if not keep_draft:
                self._composer.reset()
            if reading is not None:
                # A question changes the body height, but does not take ownership
                # of a plan the person has already paged into.
                self._keep_history_anchor(reading)
            elif not keep_context:
                self._scroll = 0
                self._anchored = False
                # The question's own rows shrink the body after say() anchored the reply with
                # the taller layout, and this prompt is where the person reads it: re-anchor,
                # across the reply's closing blocks, so a reply that outgrows the screen still
                # opens at its top when its question shows. A reply that fits keeps following
                # the tail.
                self._anchor_tall_block(for_question=True)
            self._restored_session = False
            self._draft_context = current_context
            self._frame = 0
            self._cursor(1)
        try:
            self._screen.timeout(100)
            self._draw()
            while True:
                if self.idle is not None:
                    self.idle()
                key = self._read_key()
                answer = self._handle(key, asking=True)
                if key is not None:
                    self._draw()
                if answer is not None:
                    if composer_label(prompt, True) == "New chat" and answer.strip():
                        # The request starts a run now: the screen says "Starting the run…" from this
                        # key on, not "New chat" and then "Reading run status" first.
                        self._launched, self._launched_at = True, time.monotonic()
                    return answer
        finally:
            with self._lock:
                self._asking = False
                self._screen.timeout(-1)
                if self._context_key == asked_context:
                    self._prompt = ""
                    self._pending = None
                self._frame = 0
                self._cursor(0)
                self._draw()

    # ---- the keyboard ----------------------------------------------------------------------

    def _read_key(self):
        if self._queued:
            return self._queued.pop(0)
        key = self._read_next()
        if key != "\x1b":
            return key
        self._screen.timeout(50)
        consumed: list = []
        try:
            pasted = read_paste(self._read_next, consumed)
        finally:
            self._screen.timeout(100 if self._asking or self._waiting else -1)
        if pasted is not None:
            return Pasted(pasted)
        # A paste probe that read characters and rejected them can still be a Ctrl-arrow: those
        # terminals send the pair as bytes curses never names a key for (#1997).
        if consumed:
            tab = csi_tab(self._read_next, consumed)
            if tab is not None:
                return tab
        self._queued.extend(consumed)  # an escape key on its own, then whatever followed it
        return ESCAPE

    @staticmethod
    @contextlib.contextmanager
    def _nonblocking_input():
        """Keep ncurses' direct reads of partial mouse packets from blocking the poll loop.

        The window timeout bounds waiting for a key, but not ncurses' subsequent
        reads of X10 mouse bytes. Restore descriptor flags before handling or drawing.
        """
        try:
            import fcntl

            fd = sys.stdin.fileno()
            flags = fcntl.fcntl(fd, fcntl.F_GETFL)
            fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
        except (ImportError, OSError, ValueError):
            yield
            return
        try:
            yield
        finally:
            fcntl.fcntl(fd, fcntl.F_SETFL, flags)

    def _read_next(self):
        with self._nonblocking_input():
            try:
                return self._screen.get_wch()
            except curses.error:
                return None

    def _handle(self, key, *, asking: bool) -> str | None:
        """One key: at a question, the answer once Enter sends one; while working, "pause" or None."""
        if self._attention_menu is not None:
            attention_panel.handle(self, key, asking=asking)
            return None
        if self._stage_menu is not None:
            stages_panel.handle(self, key)
            return None
        if key == curses.KEY_MOUSE:
            self._mouse_scroll()
            return None
        if self._message_sending and key not in (curses.KEY_PPAGE, curses.KEY_NPAGE, "\x03"):
            if key not in (None, curses.KEY_ENTER, 10, 13, "\n", "\r"):
                self._message_keys.append(key)
            return None
        if key == curses.KEY_F8 and self._act is not None:
            self._run("/attention", asking)
            return None
        composer = self._composer
        if key is None:  # a key curses could not read; redraw and wait
            return None
        if not composer.text:
            self._draft_context = self._draft_identity()
            self._draft_unasked = not asking
        menu = self._menu(asking)
        if not (menu and key in (curses.KEY_UP, curses.KEY_DOWN, "\t")):
            self._menu_index = 0
        if isinstance(key, Pasted):
            composer.insert(key.text)
            return None
        if (tab := tab_step(key)) is not None:  # Ctrl-T, Ctrl-←/→: the tabs, at a question
            return self._tab_key(tab, asking)
        if key == ESCAPE:  # Esc closes the menu or cancels a question a command asked
            if self._pending is not None:
                self._transcript.info(self._pending.question, "Nothing changed.")
                self._pending = None
            elif composer.text.startswith("/"):
                composer.clear()
            elif self._transcript.dismiss_listing():
                self._scroll, self._anchored = 0, False
            return None
        if menu and key in (curses.KEY_UP, curses.KEY_DOWN):
            step = -1 if key == curses.KEY_UP else 1
            self._menu_index = (self._menu_index + step) % len(menu)
            return None
        if menu and key == "\t":
            composer.clear()
            composer.insert(menu[self._menu_index % len(menu)].name)
            return None
        if isinstance(key, int):
            if key in (curses.KEY_ENTER, 10, 13):
                return self._enter(asking)
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
                if not composer.line_up():
                    composer.previous()
            elif key == curses.KEY_DOWN:
                if not composer.line_down():
                    composer.next()
            elif key == curses.KEY_PPAGE:
                self._scroll_by(self._page())
            elif key == curses.KEY_NPAGE:
                self._scroll_by(-self._page())
            return None
        if key in ("\n", "\r"):
            return self._enter(asking)
        if key in ("\b", "\x7f"):
            composer.backspace()
        elif key == "\x04":  # Ctrl-D: end of input on an empty line, otherwise delete forward
            if composer.text:
                composer.delete()
            elif asking or self._can_detach():
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

    def _enter(self, asking: bool) -> str | None:
        """Enter: run a ``/`` command, or send the reply (at a question only). After a backslash,
        Enter starts a new line in the reply instead, as in Claude Code's terminal."""
        if self._message_sending:
            return None
        if self._pending is not None:
            self._decide(self._pending)
            return None
        composer = self._composer
        if composer.cursor and composer.text[composer.cursor - 1] == "\\" and not composer.text.startswith("/"):
            composer.backspace()
            composer.insert("\n")
            return None
        text = self._composer.text
        word, _, argument = text.strip().partition(" ") if text.startswith("/") else ("", "", "")
        word = word.lower()
        available = panel.commands(
            asking=asking,
            client=self._act is not None,
            run=self._run_open,
            detachable=self._can_detach(),
            draft=self._draft_tab(asking),
        )
        menu = self._menu(asking)
        if word and not text.startswith("//"):
            names = [command.name for command in available]
            if word not in names and menu and " " not in text.strip():
                word = menu[self._menu_index % len(menu)].name
            if word in names:
                self._composer.submit()
                return self._run(word, asking, argument.strip())
            known = any(command.name == word for command in panel.COMMANDS)
            why = "is not available right now" if known else "is not a command"
            self._transcript.info(
                word, f"{word} {why}. Type / to see the commands; // sends text that starts with /.", coalesce=True
            )
            if " " not in text.strip():
                self._composer.clear()  # a mistyped command; a sentence stays, to fix with //
            return None
        sent = text[1:] if text.startswith("//") else text
        retry = self._message_retry and self._message_retry[0] == sent
        if retry or (not asking and self._can_message()):
            if not sent.strip():
                return None
            shown = self._message_retry[1] if retry else self._task_conversation or self._view
            self._message_sending = True
            try:
                self.send_message(sent, shown)
            except messages.MessageClosed:
                # The step ended between the box's offer and this Enter, usually at the question it
                # was finishing: the text waits in the box for that question instead (_typed_ahead_for).
                self._message_retry, self._draft_unasked = None, True
                self._transcript.info(*NOT_SENT_STEP_ENDED, coalesce=True)
                return None
            except messages.MessageError as error:
                self._message_retry = (sent, shown)
                self._transcript.info("Message not confirmed", str(error))
                return None
            finally:
                self._message_sending = False
                self._queued.extend(self._message_keys)
                self._message_keys.clear()
            self._message_retry = None
            self._composer.text = sent
            self._submit()
            self._asking = asking
            self._transcript.info("Message saved", "Requirements will reply after the current step is saved.")
            return None
        if not asking:
            if self._view is None and not self._launched:
                self._transcript.info("Reading run status", "Your text is kept; wait for the run's current view.")
            else:
                self._transcript.info(*NOT_SENT, coalesce=True)
            return None
        if text.startswith("//"):
            self._composer.text, self._composer.cursor = text[1:], max(0, self._composer.cursor - 1)
        return self._submit()

    def _can_message(self) -> bool:
        """A typed message is available only through a core-named pending action."""
        task_input = ((self._task_conversation or {}).get("conversation") or {}).get("input") or {}
        return self.send_message is not None and (
            task_input.get("kind") == "coordination_message"
            or (((self._view or {}).get("chat") or {}).get("input") or {}).get("kind") == "message"
        )

    def _run(self, name: str, asking: bool, argument: str = "") -> str | None:
        """A ``/`` command: "pause" goes back to the client, /diff and /undo are the client's,
        the rest only change what is shown."""
        if name == "/pause":
            # A command the person ran answers at the tail, like every other command here:
            # the pause's acknowledgement must not land below a view they paged to.
            self._scroll, self._anchored = 0, False
            return "pause"
        if name in panel.CLIENT_COMMANDS and self._act is not None:
            result = self._act(name, argument)
            if isinstance(result, inventory.Listing):
                self.say(result)
                return None
            if isinstance(result, attention.Menu):
                attention_panel.show(self, result)
                return None
            if isinstance(result, stages.Menu):
                stages_panel.show(self, result)
                return None
            if isinstance(result, panel.Confirm):
                self._transcript.info(name, result.text)
                self._pending = result
            else:
                self._transcript.info(name, result, coalesce=True)
            self._scroll, self._anchored = 0, False
            return None
        if name == "/quit":
            raise EOFError
        if name == "/live":
            self._live_command(argument)
            return None
        if name == "/cap":
            try:
                value = float(argument.strip()) if argument.strip() else None
            except ValueError:
                self._transcript.info("/cap", "Usage: /cap N where N is a dollar amount, or /cap 0 to remove the cap.")
                return None
            if value is not None and value <= 0:
                self._cost_cap = None
                self._cost_cap_warned = False
                self._transcript.info("/cap", "Session cost cap removed.")
            elif value is not None:
                self._cost_cap = value
                self._cost_cap_warned = False
                self._transcript.info(
                    "/cap", f"Session cost cap set to ${value:.2f}. The run pauses with a warning when it reaches this."
                )
            else:
                current = f"${self._cost_cap:.2f}" if self._cost_cap else "none"
                self._transcript.info("/cap", f"Session cost cap: {current}. Usage: /cap N (0 removes it).")
            return None
        if name == "/panel":
            self._panel_on = not self._panel_visible(*self._size())
            if self._panel_on and self._size()[1] < 60:
                self._transcript.info(
                    "/panel", "The terminal is too narrow for the panel; /plan and /tasks show it here."
                )
            return None
        available = (
            panel.commands(
                asking=asking,
                client=self._act is not None,
                run=self._run_open,
                detachable=self._can_detach(),
                draft=self._draft_tab(asking),
            )
            if name == "/help"
            else None
        )
        text = panel.text(name, self._view, self._log, available=available)
        if name == "/details":
            text += "\n\n" + self._observation.details(time.monotonic())
        self._transcript.info(name, text)
        self._scroll, self._anchored = 0, False
        return None

    def _live_command(self, argument: str) -> None:
        """``/live`` shows or hides the live log; ``/live off`` hides it, ``/live all`` drops the filter, and
        anything else (an agent's name, thinking, messages or tools) keeps only what matches."""
        word = argument.strip()
        if not word:
            self._live_on = not self._live_visible(*self._size())
        elif word.lower() == "off":
            self._live_on = False
        else:
            self._live.filter = "" if word.lower() == "all" else word
            self._live_on = True
            if self._live.filter and not self._live.shown() and self._live.entries:
                agents = ", ".join(self._live.agents()) or "none yet"
                self._transcript.info("/live", f"Nothing matches {word!r} yet. Agents so far: {agents}.")
        if self._live_on and not self._live_visible(*self._size()):
            self._transcript.info("/live", self._live.text())
            self._scroll, self._anchored = 0, False

    def _decide(self, pending: panel.Confirm) -> None:
        """The person's answer to a client command's question; anything but yes changes nothing."""
        self._pending = None
        reply = self._composer.submit().strip().lower()
        self._scroll, self._anchored = 0, False
        if reply not in ("y", "yes"):
            self._transcript.info(pending.question, "Nothing changed.")
            return
        self._transcript.info(pending.question, pending.then())

    def _menu(self, asking: bool) -> list[panel.Command]:
        """The commands the composer's ``/word`` could be, while it is still one word."""
        text = self._composer.text
        if self._pending is not None or not text.startswith("/") or text.startswith("//"):
            return []
        if any(char.isspace() for char in text):
            return []
        return panel.matching(
            text,
            panel.commands(
                asking=asking,
                client=self._act is not None,
                run=self._run_open,
                detachable=self._can_detach(),
                draft=self._draft_tab(asking),
            ),
        )

    def _submit(self) -> str:
        text = self._composer.submit()
        if worth_keeping(text):
            self._save_history(text)
        if text:
            self._transcript.add(f"You: {text}")
        self._scroll, self._anchored = 0, False
        self._asking = False
        self._frame = 0
        return text

    def _page(self) -> int:
        layout = self._layout(*self._size())
        return max(1, (layout.body_end - layout.body_start) - 1)

    def _mouse_scroll(self) -> None:
        """Wheel over the transcript uses the same history anchoring as Page Up/Down."""
        try:
            _, x, y, _, buttons = curses.getmouse()
        except curses.error:
            return
        layout = self._layout(*self._size())
        if not (0 <= x < layout.body_width and layout.body_start <= y < layout.body_end):
            return
        if buttons & getattr(curses, "BUTTON4_PRESSED", 0):
            self._scroll_by(3)
        elif buttons & self._wheel_down():
            self._scroll_by(-3)

    def _scroll_by(self, delta: int) -> None:
        self._anchored = False  # the person's paging owns the view from here
        self._scroll = max(0, self._scroll + delta)

    # ---- drawing ---------------------------------------------------------------------------

    def _size(self) -> tuple[int, int]:
        if self._screen is None:
            return 24, 80
        rows, cols = self._screen.getmaxyx()
        return (rows or 24), (cols or 80)

    def _layout(self, rows: int, cols: int) -> Layout:
        header_rows = 3
        width = max(1, cols - 1)
        hints_row = rows - 1
        box_bottom = rows - 2
        box_rows = 1
        if self._composer.text:
            shown, _, _ = composer_rows(self._composer.text, self._composer.cursor, max(1, width - 6), COMPOSER_ROWS)
            box_rows = max(1, min(len(shown), (rows - 8) // 2))
        box_text = box_bottom - box_rows
        box_top = box_text - 1
        status_row = box_top - 1
        question: list[str] = []
        prompt = f"{self._pending.question} [y/N]" if self._pending is not None else self._prompt
        if self._asking and prompt:
            wrapped = wrap(f"? {prompt}", max(1, width - 2))
            limit = min(4, max(1, status_row - 3))
            question = wrapped[:limit]
            if len(wrapped) > limit:
                question[-1] = truncate(question[-1], max(1, width - 4))
        status_start = status_row - len(question) + 1 if question else status_row
        panel_width = min(44, max(28, cols // 3)) if self._panel_visible(rows, cols) else 0
        panel_end = max(header_rows, status_start - 1)
        live_start = None
        if self._live_visible(rows, cols) and panel_end - header_rows >= 10:
            live_start = panel_end - max(4, int((panel_end - header_rows) * LIVE_SHARE))
        body_width = width - panel_width - (2 if panel_width else 0)
        # A reading measure: prose is hard to scan past ~120 columns, so the transcript
        # stays within a comfortable width and the screen uses the rest for the panel.
        body_width = min(body_width, 120)
        notes = []
        notes_start = None
        room = panel_end - header_rows
        if not self._asking and live_start is None and room >= 7:
            latest = panel.recent_notes(self._view, limit=2 if room >= 12 else 1)
            while latest:
                stage = ((self._view or {}).get("progress") or {}).get("stage")
                notes = [f"{stage} · latest updates"]
                for note in latest:
                    notes.extend(wrapped_rows(prose_text(note), max(1, body_width - 2), 2))
                visible = self._transcript.visible_progress(body_width, room - len(notes), self._scroll)
                remaining = [note for note in latest if (stage, safe_text(note).strip()) not in visible]
                if remaining != latest:
                    latest, notes = remaining, []
                    continue  # Removing a duplicate gives the transcript more room; recheck it.
                notes_start = panel_end - len(notes)
                break
        return Layout(
            body_width=body_width,
            panel_col=width - panel_width if panel_width else None,
            live_start=live_start,
            panel_end=panel_end,
            width=width,
            body_start=header_rows,
            body_end=notes_start if notes_start is not None else live_start if live_start is not None else panel_end,
            status_start=status_start,
            box_top=box_top,
            box_text=box_text,
            box_bottom=box_bottom,
            box_rows=box_rows,
            hints_row=hints_row,
            question=question,
            notes_start=notes_start,
            notes=notes,
        )

    def _panel_visible(self, rows: int, cols: int) -> bool:
        if not (cols >= 60 and rows >= 16):
            return False
        # Auto-show the panel on wide windows (#3070): pipeline, live log and spend
        # details appear when there is room, without /panel. Once /panel shows or
        # hides it, that choice holds at every width.
        if self._panel_on is not None:
            return self._panel_on
        return cols >= 100

    def _live_visible(self, rows: int, cols: int) -> bool:
        if rows < 16 or cols < 40:
            return False
        return bool(self._live_on)

    def _draw(self) -> None:
        with self._lock:
            screen = self._screen
            if self._closed or screen is None:
                return
            rows, cols = self._size()
            width = max(1, cols - 1)
            try:
                screen.erase()
                if rows < MIN_ROWS or cols < MIN_COLS:
                    for line, text in enumerate(textwrap.wrap(too_small(rows, cols), width)[:rows]):
                        self._put(line, 0, text, curses.A_BOLD)
                    screen.refresh()
                    return
                layout = self._layout(rows, cols)
                self._header(layout)
                self._body(layout)
                self._recent_activity(layout)
                self._live_pane(layout)
                self._side_panel(layout)
                self._question_or_status(layout)
                self._command_menu(layout)
                attention_panel.draw(self, layout, truncate)
                stages_panel.draw(self, layout, truncate)
                self._hints(layout)
                self._composer_box(layout)  # leave the terminal cursor at the reply after footer writes
                screen.refresh()
            except curses.error:
                pass

    def _new_chat(self) -> bool:
        """A new chat's request not sent yet: no run exists, so there is no status to read."""
        return self._context_key is not None and self._context_key[1] is None and not self._launched

    def _starting(self) -> bool:
        """A run just launched and not yet read: its first failed reads are its start, not an outage."""
        return (
            self._observation.received is None
            and self._launched_at is not None
            and time.monotonic() - self._launched_at < STARTUP_GRACE_SECONDS
        )

    def _attention_badge(self) -> str:
        """The F8 count of decisions waiting for the person, in plain words (#2559)."""
        if self._attention_view.get("loading"):
            return ""  # the first read of saved alerts has not finished: nothing to report yet
        if error := self._attention_view.get("error"):
            # Say why, not just that: an unexplained permanent badge reads as a broken panel.
            reason = " ".join(str(error).split())[:60]
            return f" F8: alerts unavailable: {reason}"
        new = max(0, self._attention_view.get("unread", 0) - self._attention_view.get("shown_unread", 0))
        unchecked = self._attention_view.get("unknown", 0)
        if new:
            return f" F8: {new} new alert{'s' if new != 1 else ''}" + (f", {unchecked} unchecked" if unchecked else "")
        if unchecked:
            return f" F8: {unchecked} unchecked alert{'s' if unchecked != 1 else ''}"
        return ""

    def _task_total(self) -> dict | None:
        total = self._task_usage.total((self._view or {}).get("run_dir"))
        if total is None and (self._view or {}).get("coordination"):
            # The first source read precedes the coordinator's full ledger inventory.
            # Never present that source-only amount as the whole task's spend.
            return {
                "spent_usd": None,
                "input_tokens": 0,
                "output_tokens": 0,
                "cost_complete": False,
                "tokens_complete": False,
            }
        return total

    def _worked(self) -> str:
        """How long this chat's run has worked in all, from its own attempt records (meter.run_seconds)."""
        return meter.worked_text(self._view, datetime.now(UTC))

    def _spend(self) -> str:
        """What this chat's run has used, and with more than one chat open, the session's total."""
        task = self._task_total()
        if task is not None:
            return "task " + meter.total_text(task, show_pending=True)
        here = meter.run_total(self._view)
        if len(self._tabs) < 2:
            return "chat " + (meter.total_text(here) or "$0")
        totals = [here if row.get("current") else row.get("total") for row in self._tabs]
        session = meter.total_text(meter.session_total(totals))
        if not session:
            return ""
        return f"chat {meter.total_text(here) or '$0'} · session {session}"

    def _header(self, layout: Layout) -> None:
        brand = f"{self._glyphs.bar} {BRAND}"
        self._put(0, 0, truncate(brand, layout.width), self._style("brand"))
        used = display_width(brand)
        context = f"  {self._context}" if self._context else ""
        badge = self._attention_badge()
        remaining = max(0, layout.width - used - display_width(badge))
        if context and remaining:
            self._put(0, used, truncate(context, remaining), self._style("dim"))
        if badge:
            self._put(
                0,
                max(used, layout.width - display_width(badge)),
                truncate(badge, layout.width - used),
                self._style("dim"),
            )
        unavailable = bool(self._observation.error) or any(
            row.get("current") and row.get("state") == "unavailable" for row in self._tabs
        )
        label = "History · " if self._scroll else ""
        if self._status_row(layout) and not self._scroll and not unavailable:
            # The status row above the reply box says what is happening now; this row said the same
            # words a second time ("Requirements working" twice). It speaks again at a question, where
            # that row gives way, and when the person pages back into the history.
            pass
        elif self._view is None and (self._new_chat() or self._starting()):
            status = "New chat" if self._new_chat() else STARTING
            self._put(2, 0, truncate(f"{label}{status}", layout.width), self._style("panel-now"))
        else:
            cue = verification.cue(self._view, unavailable=unavailable)
            task = panel.task_progress(self._view, self._task_conversation)
            if task and not unavailable:
                cue = task
            stage = None if task else ((self._view or {}).get("progress") or {}).get("stage")
            if isinstance(stage, str) and stage.strip() and stage.strip() not in cue:
                cue = f"{stage.strip()} · {cue}"
            style = self._style("panel-warn" if unavailable else "panel-now")
            line = f"{label}{cue} ({self._observation.compact(time.monotonic())})"
            self._put(2, 0, truncate(line, layout.width), style)
        if len(self._tabs) < 2:
            self._put(1, 0, self._glyphs.rule * layout.width, self._style("dim"))
            return
        col = 0
        current_index = next((i for i, row in enumerate(self._tabs) if row.get("current")), 0)
        # Scroll the tab window so the active tab is always visible: tabs before it are
        # dropped when the bar is full, and "…" marks the hidden ones on each side.
        start = 0
        labels = []
        for row in self._tabs:
            state = str(row.get("state") or "")
            mark = (TAB_MARKS if self._glyphs.unicode else ASCII_TAB_MARKS).get(state, "")
            title = truncate(row["title"], max(8, layout.width // (len(self._tabs) + 1)))
            label = f" {row['number']} {title} {mark} ".replace("  ", " ")
            labels.append(label)
        # Find the window that includes current_index and fills the bar.
        widths = [display_width(label) for label in labels]
        used_before = sum(widths[i] + 1 for i in range(current_index))
        if used_before + widths[current_index] + 2 > layout.width:
            # Drop leading tabs until the current one fits.
            start = current_index
            while start > 0 and sum(widths[i] + 1 for i in range(start, current_index + 1)) + 2 > layout.width:
                start -= 1
        if start > 0:
            self._put(1, col, "…", self._style("dim"))
            col += 2
        for index in range(start, len(self._tabs)):
            label = labels[index]
            if col + display_width(label) > layout.width:
                self._put(1, col, "…", self._style("dim"))
                break
            self._put(
                1,
                col,
                label,
                self._style("brand" if self._tabs[index].get("current") else "dim")
                | (curses.A_REVERSE if self._tabs[index].get("current") else 0),
            )
            col += display_width(label) + 1

    def _body(self, layout: Layout) -> None:
        height = max(0, layout.body_end - layout.body_start)
        width = layout.body_width or layout.width
        lines = self._transcript.lines(width)
        if self._view is not None and not self._new_chat():
            lines = [(role, text) for role, text in lines if not text.startswith("New chats:")]
        if not lines:
            self._hero(layout, height)
            return
        if not height:
            return
        if self._welcome() and len(lines) < height:
            # A new chat's notices (the mode it starts in) sit at the top; the welcome fills the rest.
            self._hero(layout, height - len(lines), top=layout.body_start + len(lines))
        self._scroll = min(self._scroll, max(0, len(lines) - height))
        end = len(lines) - self._scroll
        start = max(0, end - height)
        for row, (role, text) in enumerate(lines[start:end], layout.body_start):
            if role.endswith("-label"):
                self._put(row, 0, truncate(f"{self._glyphs.bar} {text}", width), self._style(role))
            elif role == "user-body":
                padded = text + " " * max(0, width - display_width(text))
                self._put(row, 0, truncate(padded, width), self._style(role))
            elif role in INLINE_STYLES and INLINE.search(text):
                self._put_inline(row, text, width, role)
            else:
                self._put(row, 0, truncate(text, width), self._style(role))

    def _recent_activity(self, layout: Layout) -> None:
        """Keep a few current prose updates visible while the conversation is scrolled."""
        if layout.notes_start is None:
            return
        for offset, text in enumerate(layout.notes):
            self._put(
                layout.notes_start + offset,
                0 if offset == 0 else 2,
                truncate(text, layout.body_width - (0 if offset == 0 else 2)),
                self._style("panel-title" if offset == 0 else "assistant-body"),
            )

    def _put_inline(self, row: int, text: str, width: int, role: str) -> None:
        """A message line with its `code` and **bold** spans drawn as such, clipped at ``width``."""
        col = 0
        for kind, piece in inline_pieces(text):
            room = width - col
            if room <= 0:
                return
            shown = truncate(piece, room)
            style = (
                self._style("code-inline")
                if kind == "code"
                else self._style(role) | curses.A_BOLD
                if kind == "bold"
                else self._style(role)
            )
            self._put(row, col, shown, style)
            col += display_width(shown)

    def _side_panel(self, layout: Layout) -> None:
        """Fixed status and totals; detailed history belongs to the conversation's / commands."""
        if layout.panel_col is None:
            return
        col, width = layout.panel_col, layout.width - layout.panel_col
        top, bottom = layout.body_start, layout.panel_end
        for row in range(top, bottom):
            self._put(row, col - 2, self._glyphs.vertical, self._style("border"))
        rows: list[tuple[str, str]] = []
        task = panel.task_progress(self._view, self._task_conversation)
        for section in panel.compact_sections(self._view, launched=self._launched, task_total=self._task_total()):
            if task and section.title == "Stage":
                continue
            if task and section.title == "Now":
                section = panel.Section("Now", task)
            rows.append(
                (
                    "panel-title",
                    f"{section.title.upper()}  {section.summary}".rstrip()
                    if section.title != "Now"
                    else "LAST KNOWN"
                    if self._observation.error
                    else "NOW",
                )
            )
            if section.title == "Now":
                rows.append(("panel-now", section.summary))
            # What the run is doing now, and each requirement and task, wraps so it is read rather than
            # cut at the panel's edge; the rest keep one row each.
            lines_each = PANEL_WRAP.get(section.title, 1)
            for item in section.rows:
                mark = item.mark if self._glyphs.unicode else panel.ascii_mark(item.mark)
                for number, line in enumerate(wrapped_rows(item.text, max(1, width - 2), lines_each)):
                    rows.append((f"panel-{item.style}", f"{mark if number == 0 else ' '} {line}"))
            rows.append(("plain", ""))
        room = bottom - top
        for row, (style, text) in enumerate(rows[:room], top):
            self._put(row, col, truncate(text, width), self._style(style))

    def live_rows(self, width: int) -> list[list[tuple[str, str]]]:
        """The live log as screen rows, oldest first: each row is (text, style) pieces, the agent's name
        first in its own color, and an entry's further lines indented under it."""
        colors = {agent: f"agent-{number % AGENT_STYLES}" for number, agent in enumerate(self._live.agents())}
        rows: list[list[tuple[str, str]]] = []
        for entry in self._live.shown():
            mark = panel.LIVE_MARKS[entry["kind"]]
            mark = mark if self._glyphs.unicode else panel.ascii_mark(mark)
            style = (
                "panel-bad"
                if entry.get("status") == "failed"
                else "live-thinking"
                if entry["kind"] == "thinking"
                else "live-plain"
            )
            agent, text = safe_text(entry["agent"]), safe_text(panel.live_text(entry["text"]))
            if entry["kind"] in ("message", "thinking"):
                text = prose_text(text)
            lines = wrap(f"{agent} {mark} {text}", width)
            first = lines[0]
            if first.startswith(agent):
                rows.append([(agent, colors.get(entry["agent"], "agent-0")), (first[len(agent) :], style)])
            else:
                rows.append([(first, style)])
            rest = " ".join(lines[1:]).strip()
            rows.extend([[("  " + line, style)] for line in wrap(rest, max(1, width - 2))] if rest else [])
        return rows

    def _live_pane(self, layout: Layout) -> None:
        """The live log under the conversation: a title rule, then the newest rows; typing goes on below."""
        if layout.live_start is None:
            return
        width = layout.body_width or layout.width
        title = f" {self._live.title()} "
        hint = " /live to hide "
        rule = self._glyphs.rule
        line = rule * 2 + title + rule * max(0, width - 2 - display_width(title) - display_width(hint)) + hint
        self._put(layout.live_start, 0, truncate(line, width), self._style("border"))
        self._put(layout.live_start, 2, truncate(title, max(0, width - 2)), self._style("panel-title"))
        room = layout.panel_end - layout.live_start - 1
        rows = self.live_rows(width)
        if not rows:
            waiting = (
                "Nothing matches the filter yet; /live all shows every agent."
                if self._live.filter
                else "Working; waiting for a plain-English progress update."
                if (self._view or {}).get("status") == "RUNNING"
                else ("No agent is working right now. Progress updates appear here when work starts.")
            )
            self._put(layout.live_start + 1, 0, truncate(waiting, width), self._style("dim"))
            return
        for row, pieces in enumerate(rows[-room:], layout.live_start + 1):
            col = 0
            for text, style in pieces:
                shown = truncate(text, width - col)
                self._put(row, col, shown, self._style(style))
                col += display_width(shown)

    def _command_menu(self, layout: Layout) -> None:
        """The ``/`` commands matching what is typed, just above the composer."""
        menu = self._menu(self._asking)
        if not menu:
            return
        available_rows = layout.box_top - layout.body_start
        cut = len(menu) > min(MENU_ROWS, available_rows)
        # A cut list keeps a row for its count: without it, the commands past the window are
        # invisible, with nothing saying they exist.
        rows = max(1, min(MENU_ROWS, len(menu), available_rows - 1 if cut and available_rows > 1 else available_rows))
        chosen = self._menu_index % len(menu)
        start = min(max(0, chosen - rows + 1), len(menu) - rows)  # every entry is reachable: the window follows
        shown = menu[start : start + rows]
        below, above = len(menu) - (start + rows), start
        width = min(layout.width - 2, 64)
        first = layout.box_top - len(shown) - (1 if cut else 0)
        for row in range(first, layout.box_top):  # the rows under the menu, the status line among them
            self._put(row, 0, " " * layout.width)
        for offset, command in enumerate(shown):
            selected = start + offset == chosen
            line = f" {command.name:<9} {command.description}"
            line = truncate(line, width)
            line += " " * max(0, width - display_width(line))
            self._put(first + offset, 2, line, self._style("menu-selected" if selected else "menu"))
        if cut:
            parts = [f"↑ {above} above"] if above else []
            if below:
                parts.append(f"↓ {below} more")
            more = " " + " · ".join(parts) + "; ↑↓ chooses"
            self._put(first + len(shown), 2, truncate(more, width), self._style("dim"))

    def _welcome(self) -> bool:
        """Nothing said in this chat yet and nothing started: the welcome still has the screen."""
        return not self._launched and not any(role in ("user", "assistant") for role, _ in self._transcript.blocks)

    def _hero(self, layout: Layout, height: int, *, top: int | None = None) -> None:
        if height <= 0:
            return
        width = layout.body_width or layout.width
        brand_rows = 2 if self._glyphs.unicode and width >= 35 else 1
        hero: list[tuple[str, str]] = []
        if brand_rows == 2:
            hero.extend(("brand", line) for line in WORDMARK)
        else:
            hero.append(("brand", "AutoCode"))
        hero.append(("plain", ""))
        tagline = TAGLINE if display_width(TAGLINE) <= width else "Plan, build and test."
        hero.append(("dim", tagline))
        if self._context and top is None:
            hero.append(("dim", self._context))
        # The examples only where they fit whole, with a blank row above and below the welcome.
        examples = [("plain", ""), ("dim", "Try asking:"), *(("dim", f"  {text}") for text in EXAMPLES)]
        if len(hero) + len(examples) + 2 <= height and all(display_width(text) <= width for _, text in examples):
            hero.extend(examples)
        hero = hero[:height]
        first = (layout.body_start if top is None else top) + max(0, (height - len(hero)) // 2)
        block = max((display_width(text) for role, text in hero if role == "dim" and text.startswith("  ")), default=0)
        left = max(0, (width - block) // 2)
        for offset, (role, text) in enumerate(hero):
            if text.startswith("  ") or text == "Try asking:":
                col = left  # the examples line up under one another
            else:
                col = max(0, (width - display_width(text)) // 2)
            self._put(first + offset, col, truncate(text, width - col), self._style(role))

    def _status_row(self, layout: Layout) -> bool:
        """Whether the spinner row above the reply box is drawn: whenever no question holds the box and
        a run is starting or running (a new chat's request not sent yet has nothing to report)."""
        return not layout.question and not self._asking and not (self._view is None and self._new_chat())

    def _question_or_status(self, layout: Layout) -> None:
        if layout.question:
            for offset, line in enumerate(layout.question):
                self._put(layout.status_start + offset, 2, truncate(line, layout.width - 2), self._style("question"))
            return
        if not self._status_row(layout):
            return
        spinner = self._glyphs.spinner
        marker = spinner[self._frame % len(spinner)] if spinner else "*"
        reading = self._view is None
        task = panel.task_progress(self._view, self._task_conversation)
        text = (
            STARTING
            if reading and self._starting()
            else f"Status unavailable; {self._observation.outage()}"
            if self._observation.error
            else STARTING
            if reading and self._launched
            else "Reading run status…"
            if reading
            else task
            # The run reached a question and the box opens with it in a moment: "Waiting for you"
            # beside a spinner, over a box that would not take the reply yet, read as a contradiction.
            or (QUESTION_COMING if panel.needs_you(self._view) else None)
            or panel.doing(self._view)
            or panel.current_line(self._view)
            or panel.headline(self._view)
            or self._transcript.activity
            or "AutoCode is working…"
        )
        now = datetime.now(UTC)
        trouble = "" if reading or task else meter.struggling(self._view, now)
        if trouble:
            text = trouble  # a stage on its second attempt or later says so where the person looks
        # How long since the working stage last wrote anything (panel.quiet): a quiet one is flagged.
        silent = None if reading or self._observation.error else panel.quiet(self._view, now)
        if silent:
            text = f"{text} · {silent[0]}"
        lead = f"  {marker} {text}"
        style = self._style("panel-warn" if trouble or (silent and silent[1]) else "working")
        self._put(layout.status_start, 0, truncate(lead, layout.width), style)
        if reading:
            return
        spend = self._spend()
        tail = (
            "ctrl-c cancel coordination after this step"
            if (self._view or {}).get("coordination")
            else "ctrl-c pause after this step"
        )
        # Live cost counter on the status line (#3002): always visible while the run works.
        right = f"{spend} · {tail}" if spend else tail
        room = layout.width - display_width(lead) - 2
        if room >= display_width(right):
            self._put(layout.status_start, layout.width - display_width(right), right, self._style("dim"))
        elif room >= display_width(tail):
            self._put(layout.status_start, layout.width - display_width(tail), tail, self._style("dim"))

    def _composer_box(self, layout: Layout) -> None:
        glyphs = self._glyphs
        width = layout.width
        active = self._asking
        label = composer_label(self._prompt, active)
        if not active and self._can_message():
            label = "Message"
        border = self._style("border-active" if active else "border")
        self._put(layout.box_top, 0, self._box_top(width, label), border)
        self._put(layout.box_top, 3, label, self._style("brand" if active else "dim"))
        for row in range(layout.box_text, layout.box_text + layout.box_rows):
            self._put(row, 0, glyphs.vertical, border)
            self._put(row, width - 1, glyphs.vertical, border)
        self._put(layout.box_bottom, 0, glyphs.bottom_left + glyphs.rule * (width - 2) + glyphs.bottom_right, border)
        inner = max(1, width - 6)
        self._put(layout.box_text, 2, glyphs.prompt, self._style("prompt" if active else "dim"))
        if not active and not self._composer.text:
            placeholder = (
                "Reading run status…"
                if self._view is None and not self._launched
                else "Type a message for AutoCode, then Enter"
                if self._can_message()
                else WORKING_PLACEHOLDER
            )
            self._put(layout.box_text, 4, truncate(placeholder, inner), self._style("placeholder"))
            return
        row = 0
        if self._composer.text:
            # The whole reply, wrapped and on its own lines, as a person reads what they typed.
            shown, row, cursor = composer_rows(self._composer.text, self._composer.cursor, inner, layout.box_rows)
            for offset, line in enumerate(shown):
                self._put(layout.box_text + offset, 4, line, self._style("input"))
        else:
            cursor = 0
            placeholder = NEW_CHAT_PLACEHOLDER if label == "New chat" else REPLY_PLACEHOLDER
            self._put(layout.box_text, 4, truncate(placeholder, inner), self._style("placeholder"))
        with contextlib.suppress(curses.error):
            self._screen.move(layout.box_text + row, min(width - 2, 4 + cursor))

    def _box_top(self, width: int, label: str) -> str:
        glyphs = self._glyphs
        title = f" {label} "
        fill = max(0, width - 3 - display_width(title))
        return glyphs.top_left + glyphs.rule + title + glyphs.rule * fill + glyphs.top_right

    def _hints(self, layout: Layout) -> None:
        asking_segments: list[str] = []
        menu_open = bool(self._menu(self._asking)) and self._composer.text.startswith("/")
        if self._asking:
            newline = "\\⏎ newline" if self._glyphs.unicode else "\\enter newline"
            history = "↑↓ chooses" if menu_open else ("↑↓ history" if self._glyphs.unicode else "up/down history")
            # The optional hints give way first, in this order, when the row is short of room:
            # "ctrl-d quit" is the way out of the full screen and "/ commands" the menu, so a
            # stage and cost prefix must never push them off the edge of the footer.
            asking_segments = ["enter send", newline, "/ commands", history, "pgup/pgdn scroll", "ctrl-d quit"]
            hints = (" · " if self._glyphs.unicode else " | ").join(asking_segments)
        elif self._view and self._view.get("coordination"):
            detach = "ctrl-d detaches · " if self._can_detach() else ""
            hints = f"/ commands · {detach}ctrl-c cancels coordination after this step"
        elif self._can_detach():
            hints = "/ commands · ctrl-d detaches · ctrl-c asks for a boundary pause"
        elif self._view is None:
            hints = "/ commands"  # the status row says it is starting or reading the run
        else:
            hints = "/ commands · ctrl-c asks AutoCode to pause after this step"
        stage = (
            "Coordination"
            if panel.task_progress(self._view, self._task_conversation)
            else ((self._view or {}).get("progress") or {}).get("stage")
        )
        stage = stage.strip() if isinstance(stage, str) else ""
        spend = self._spend()
        status = " · ".join(value for value in (stage, self._worked(), spend) if value)
        # The working time goes first when the footer is short of room: the navigation and the
        # boundary action matter more, and the Stages section carries the time too.
        if display_width(status + " · " + hints) > layout.width - 2:
            status = " · ".join(value for value in (stage, spend) if value)
        prefix = f"{status} · " if status else ""
        if self._asking and layout.width >= 72 and display_width(prefix + hints) > layout.width - 2:
            for droppable in (asking_segments[1], asking_segments[4], asking_segments[3]):
                asking_segments.remove(droppable)
                hints = (" · " if self._glyphs.unicode else " | ").join(asking_segments)
                if display_width(prefix + hints) <= layout.width - 2:
                    break
        # At narrow widths the stage already appears on the status row. Keep the
        # navigation and boundary action visible instead of letting a stage/cost
        # prefix push them past the right edge of the footer.
        if layout.width < 72:
            if layout.width < 48:
                if self._asking:
                    hints = "/ · ↑↓ history · ^D quit"
                elif self._can_detach():
                    hints = "/ · ^D detach · ^C pause"
                elif self._view and self._view.get("coordination"):
                    hints = "/ commands · ^C cancel"
                elif self._view is None:
                    hints = "/ commands"
                else:
                    hints = "/ commands · ^C pause"
            elif self._asking:
                hints = "/ commands · ↑↓ history · ctrl-d quit"
            elif self._can_detach():
                hints = "/ commands · ctrl-d detach · ctrl-c pause"
            elif self._view and self._view.get("coordination"):
                hints = "/ commands · ctrl-c cancel after step"
            elif self._view is None:
                hints = "/ commands"
            else:
                hints = "/ commands · ctrl-c pause after step"
        elif status:
            hints = status + " · " + hints
        self._put(layout.hints_row, 2, truncate(hints, layout.width - 2), self._style("plain"))
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

    def _animate(self) -> bool:
        return bool(
            not self._asking
            and self._view is not None
            and (self._launched or (self._view or {}).get("liveness", {}).get("kind") in ("supervised", "unsupervised"))
        )

    def _tick(self) -> None:
        """Animate the working marker; never draws while a question waits for a key."""
        while not self._stopping.wait(0.12):
            with self._lock:
                if self._closed or self._asking or self._waiting or not self._animate():
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
            "placeholder": 0,
            "question": curses.A_BOLD,
            "working": 0,
            "info-label": curses.A_BOLD,
            "info-body": 0,
            "panel-title": curses.A_BOLD,
            "panel-now": 0,
            "panel-plain": 0,
            "panel-dim": curses.A_DIM,
            "panel-good": 0,
            "panel-bad": curses.A_BOLD,
            "panel-accent": curses.A_BOLD,
            "panel-warn": curses.A_BOLD,
            "live-thinking": curses.A_DIM,
            "live-plain": 0,
            **{f"agent-{number}": curses.A_BOLD for number in range(AGENT_STYLES)},
            "code": 0,
            "code-inline": curses.A_BOLD,
            "code-fence": curses.A_DIM,
            "diff-add": 0,
            "diff-del": curses.A_DIM,
            "diff-hunk": curses.A_BOLD,
            "diff-file": curses.A_BOLD,
            "menu": curses.A_REVERSE | curses.A_DIM,
            "menu-selected": curses.A_REVERSE,
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
        pair("info-label", quiet if quiet != -1 else accent, -1, curses.A_BOLD)
        pair("panel-title", quiet if quiet != -1 else accent, -1, curses.A_BOLD)
        pair("panel-now", accent, -1)
        pair("panel-good", good, -1)
        pair("panel-bad", curses.COLOR_RED, -1, curses.A_BOLD)
        pair("panel-accent", accent, -1, curses.A_BOLD)
        pair("panel-warn", warn, -1, curses.A_BOLD)
        pair("live-thinking", quiet, -1, curses.A_DIM if quiet == -1 else 0)
        code = 180 if curses.COLORS >= 256 else curses.COLOR_MAGENTA
        pair("code", code, -1)
        pair("code-inline", code, -1)
        pair("code-fence", quiet if quiet != -1 else code, -1, curses.A_DIM if quiet == -1 else 0)
        pair("diff-add", good, -1)
        pair("diff-del", curses.COLOR_RED, -1)
        pair("diff-hunk", accent, -1)
        pair("diff-file", -1, -1, curses.A_BOLD)
        for number, color in enumerate((accent, good, warn, curses.COLOR_MAGENTA)[:AGENT_STYLES]):
            pair(f"agent-{number}", color, -1, curses.A_BOLD)
