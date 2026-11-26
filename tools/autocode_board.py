"""`autocode board`: every AutoCode run on this machine, live, in one terminal screen.

`autocode runs --all` prints the runs once. The board keeps that list on screen and
rereads it every few seconds, so several runs working at once, in one project or in
many, can be watched from one place: which need a person, which are working and at
what stage, and the command each one accepts next. Enter opens one run: its
pipeline, its tasks, what each working agent is doing (a parallel Builder is a line
of its own), its spend, what it needs and, while it waits for approval, its plan.

The board is a read-only client of the task-run interface (docs/task-run.md). It
starts `autocode` as an argument array with no shell and reads only
`runs --all --json` and `--status`, through the terminal client's bounded reader
(autocode_chat_polling). It takes no action: it shows a run's `next_command` as
text for the person to run, so an approval stays bound to the plan the person read
where they approve it. Nothing here imports the runner or reads a run's saved state.

The screens are pure functions from what was read to lines of text (`list_lines`,
`run_lines`); curses only draws them.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import shlex
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path

try:
    from . import autocode_chat_polling as polling
except ImportError:
    import autocode_chat_polling as polling

AUTOCODE = (sys.executable, str(Path(__file__).resolve().parent / "autocode.py"))
# Seconds between rereads of the list, and of the run that is open. While a run is open its list is
# off screen and is reread only now and then; going back to the list rereads it at once.
INTERVAL = 5.0
OPEN_INTERVAL = 3.0
HIDDEN_INTERVAL = 30.0
# The reader's limits (autocode_chat_polling). A listing of every project's runs takes as long as there
# are runs to read (five seconds for 158 of them on the owner's laptop), and nothing waits on it: the
# board draws and takes keys while it reads. So a read gets a long first limit, twice that after a
# timeout, and a slow read is followed by a rest REST times its own length, so that rereading the list
# never takes more than about a quarter of one processor.
READ_TIMEOUT = 20.0
MAX_READ_TIMEOUT = 120.0
REST = 3.0
# The buckets `autocode runs` sorts its rows into (docs/cli.md), in its order. The first two are the
# runs not yet settled, which is what the board shows until the person asks for the rest.
NEEDS, WORKING, SETTLED, UNREADABLE = "needs you", "working", "settled", "unreadable"
BUCKETS = (NEEDS, WORKING, SETTLED, UNREADABLE)
STAMP = "%Y-%m-%dT%H:%M:%S"  # a row's updated_at, in local time
# How a line is drawn; curses maps each to an attribute.
TITLE, HEAD, PLAIN, DIM, PICKED, ALERT = "title", "head", "plain", "dim", "picked", "alert"
Line = tuple[str, str]
LIST_KEYS = "Up/Down/PgUp/PgDn move · Enter open · n needs you only · a all runs · r reread · q quit"
RUN_KEYS = "Up/Down/PgUp/PgDn/End scroll · Esc back to the list · r reread · q quit"
SCROLL = {"up": -1, "down": 1, "pageup": -1, "pagedown": 1}
# The widest the status and project columns of the list get; each is as wide as the rows shown need.
LABEL_WIDTH, PROJECT_WIDTH = 28, 24
ESCAPED = re.compile(r"\\([^A-Za-z0-9\s])")  # Markdown's backslash before a punctuation mark, dropped for plain text
NO_TERMINAL = "autocode board needs a terminal. For one listing, run: autocode runs --all --active"


@dataclass
class Board:
    """What the board has read and what the person is looking at."""

    home: str  # the folder the list is read from; its project's own registry is read too
    rows: list[dict] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    loaded: bool = False
    error: str = ""
    picked: str | None = None  # the run_dir of the highlighted row, kept across rereads
    needs_only: bool = False
    everything: bool = False  # settled and unreadable runs too
    opened: tuple[str, str] | None = None  # (workspace, run_dir) of the run on the run screen
    view: dict | None = None
    view_error: str = ""
    scroll: int = 0
    page: int = 10  # the lines one screenful scrolls by; frame() sets it from the terminal's height
    asked: float | None = None  # when the board began reading, for the wait it shows before the first list
    read_at: float | None = None  # when the list on screen was read


def shown(board: Board) -> list[dict]:
    """The rows on the list screen, in the order `autocode runs` gave them."""
    wanted = (NEEDS,) if board.needs_only else BUCKETS if board.everything else (NEEDS, WORKING)
    return [row for row in board.rows if row.get("bucket") in wanted]


def picked(board: Board) -> dict | None:
    """The highlighted row: the one picked before, or the first shown."""
    rows = shown(board)
    return next((row for row in rows if row.get("run_dir") == board.picked), rows[0] if rows else None)


def targets(board: Board) -> dict:
    """What the reader keeps fresh: the list, and the open run's status."""
    if board.opened:
        return {(board.home, None): HIDDEN_INTERVAL, board.opened: OPEN_INTERVAL}
    return {(board.home, None): INTERVAL}


def onscreen(board: Board) -> tuple:
    """The one read the screen in front of the person shows: what `r`, Enter and going back read again."""
    return board.opened or (board.home, None)


def apply(board: Board, results: list[tuple], now: float | None = None) -> None:
    """Take the reader's results. A failed read keeps what was last shown and says so."""
    for key, text, error in results:
        listing = key[1] is None
        try:
            read = json.loads(text) if not error else None
        except ValueError:
            read, error = None, "the read was not JSON"
        if listing:
            if isinstance(read, dict) and isinstance(read.get("runs"), list):
                board.rows = [row for row in read["runs"] if isinstance(row, dict)]
                counts = read.get("counts")
                board.counts = counts if isinstance(counts, dict) else {}
                board.loaded, board.error, board.read_at = True, "", now
                if board.picked is None and (first := picked(board)):
                    board.picked = first.get("run_dir")  # the highlight stays on its run when new runs appear
            else:
                board.error = error or "the list was not a runs listing"
        elif key == board.opened:
            if isinstance(read, dict) and isinstance(read.get("view"), dict):
                board.view, board.view_error = read["view"], ""
            else:
                board.view_error = error or "the read was not a status view"


def press(board: Board, key: str) -> str | None:
    """Apply one key. Returns "quit", "reread" (read again now) or None."""
    if key == "q":
        return "quit"
    if key == "r":
        return "reread"
    if board.opened:
        if key in ("back", "left"):
            board.opened, board.view, board.view_error, board.scroll = None, None, "", 0
            return "reread"  # the list was reread only now and then while it was off screen
        if key in SCROLL:
            board.scroll = max(0, board.scroll + SCROLL[key] * (board.page if key.startswith("page") else 1))
        elif key in ("home", "end"):
            board.scroll = 0 if key == "home" else sys.maxsize  # frame() stops it at the last screenful
        return None
    rows = shown(board)
    current = picked(board)
    if (key in SCROLL or key in ("home", "end")) and current is not None:
        step = len(rows) if key in ("home", "end") else board.page if key.startswith("page") else 1
        at = rows.index(current) + (step if key in ("down", "pagedown", "end") else -step)
        board.picked = rows[min(max(at, 0), len(rows) - 1)].get("run_dir")
    elif key == "n":
        board.needs_only = not board.needs_only
    elif key == "a":
        board.everything, board.needs_only = not board.everything, False
    elif key == "enter" and current is not None:
        if isinstance(current.get("workspace"), str) and isinstance(current.get("run_dir"), str):
            board.opened = (current["workspace"], current["run_dir"])
            board.view, board.view_error, board.scroll = None, "", 0
            return "reread"
    return None


def age(stamped, now: float) -> str:
    """How long since a row's checkpoint was written."""
    try:
        seconds = max(0.0, now - time.mktime(time.strptime(stamped, STAMP)))
    except (TypeError, ValueError):
        return ""
    return _ago(seconds)


def _ago(seconds: float) -> str:
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds >= size:
            return f"{int(seconds // size)}{unit}"
    return f"{int(max(0.0, seconds))}s"


def _label(row: dict) -> str:
    label = row.get("pause_category_label") or row.get("pause_category")
    if not label and row.get("ready_to_continue"):
        label = "Ready to continue"
    return str(label or row.get("status") or "unreadable")


def _stage(row: dict) -> str:
    if row.get("reason"):
        return str(row["reason"])
    stage = (row.get("phase") or row.get("next_stage")) if row.get("bucket") == WORKING else None
    step = f"iteration {row['iteration']}" if row.get("iteration") else None
    return " · ".join(str(part) for part in (stage, step) if part)


def _one(text) -> str:
    """A value from a row or a view as one line."""
    return " ".join(str(text).split())


def _fit(text: str, width: int) -> str:
    """``text`` cut to ``width`` columns; its own spacing is kept, since lines are laid out in columns."""
    return text if len(text) <= width else text[: max(0, width - 1)] + "…"


def _summary(board: Board) -> str:
    counts = board.counts
    parts = [f"{counts.get(bucket, 0)} {bucket}" for bucket in (NEEDS, WORKING, SETTLED)]
    if counts.get(UNREADABLE):
        parts.append(f"{counts[UNREADABLE]} {UNREADABLE}")
    return " · ".join(parts)


def _list_head(board: Board, width: int, now: float) -> list[Line]:
    """The lines that stay at the top of the list screen: the counts, when they were read, a failed read."""
    title = "AutoCode runs"
    if board.loaded:
        counts = _summary(board)
        read = f"read {_ago(now - board.read_at)} ago" if board.read_at is not None else ""
        # What a narrow window drops first is the board's name, and last how old the list is.
        fits = (f"AutoCode runs   {counts}   {read}".rstrip(), f"{counts}   {read}".rstrip(), f"{read}   {counts}")
        title = next((text for text in fits if len(text) <= width), fits[-1] if read else fits[0])
    lines: list[Line] = [(_fit(title, width), TITLE)]
    if board.error:
        kept = " Showing the last one read." if board.loaded else ""
        lines.append((_fit(f"Could not read the list: {board.error}. Trying again.{kept}", width), ALERT))
    return lines


def list_lines(board: Board, width: int, now: float) -> tuple[list[Line], int | None]:
    """The list screen, and the index of the highlighted row's line."""
    lines = _list_head(board, width, now)
    if not board.loaded:
        waited = int(now - board.asked) if board.asked is not None else 0
        return [*lines, ("", PLAIN), ("Reading the runs…" + (f" {waited}s" if waited >= 2 else ""), DIM)], None
    rows, current, at = shown(board), picked(board), None
    if not rows:
        total = board.counts.get("total", len(board.rows))
        what = "No run needs you" if board.needs_only else "No run needs you or is working"
        if not total:
            what = "No AutoCode run has been saved on this machine yet"
        elif board.everything:
            what = "No runs to show"
        hidden = "" if board.everything or not total else f" ({total} run(s) in all; press a to show them)"
        lines += [("", PLAIN), (_fit(what + hidden + ".", width), DIM)]
    # One width per column for every row shown, as wide as those rows need: the project column lines up
    # whatever the titles are, and a narrow terminal gives its room to the titles, not to padding.
    labels = max((len(_one(_label(row))) for row in rows), default=0)
    projects = max((len(_project(row)) for row in rows), default=0)
    label_width = min(LABEL_WIDTH, labels, max(8, width // 4))
    project_width = min(PROJECT_WIDTH, projects, max(8, width // 5))
    for bucket in BUCKETS:
        group = [row for row in rows if row.get("bucket") == bucket]
        if not group:
            continue
        lines += [("", PLAIN), (f"{bucket} ({len(group)})", HEAD)]
        for row in group:
            title = _one(row.get("title") or row.get("name") or "")
            head = f"{age(row.get('updated_at'), now):>4}  {_fit(_one(_label(row)), label_width):<{label_width}}  "
            room = max(8, width - 2 - len(head) - (project_width + 2 if project_width else 0))
            text = f"{head}{_fit(title, room):<{room}}  {_fit(_project(row), project_width)}".rstrip()
            if row is current:
                at = len(lines)
                lines.append((_fit("> " + text, width), PICKED))
                for detail in (_stage(row), row.get("needs_reason"), row.get("workspace")):
                    if detail:
                        lines += [(part, DIM) for part in _wrap(str(detail), width, indent=8)]
                if row.get("next_command"):
                    lines += [(part, DIM) for part in _command(str(row["next_command"]), width, indent=8)]
            else:
                lines.append((_fit("  " + text, width), ALERT if bucket == NEEDS else PLAIN))
    return lines, at


def _project(row: dict) -> str:
    return Path(str(row.get("workspace") or "")).name


def _wrap(text: str, width: int, *, indent: int = 2) -> list[str]:
    pad = " " * indent
    wrapped = textwrap.wrap(
        " ".join(text.split()), max(20, width), initial_indent=pad, subsequent_indent=pad, break_on_hyphens=False
    )
    return wrapped or [pad]


def _command(command: str, width: int, *, indent: int = 2) -> list[str]:
    """A command to copy and run, on as many lines as it needs and still one command when pasted.

    A line ends between two arguments with the shell's own line continuation. An argument longer than a
    line goes on from the first column of the next, where the shell joins the two halves. A quoted one
    cannot be joined that way, so a command with one is wrapped as any long text is.
    """
    pad = " " * indent
    try:
        words = [shlex.quote(word) for word in shlex.split(command)]
    except ValueError:
        words = []
    room = width - 2  # a line that is continued ends " \"
    if not words or any(word.startswith("'") and indent + 3 + len(word) > room for word in words):
        return _wrap(f"-> {command}", width, indent=indent)
    lines, line = [], f"{pad}->"
    for at, word in enumerate(words):
        if at and len(line) + 1 + len(word) > room:
            lines.append(line + " \\")
            line = f"{pad}  "
        line += " " + word
        while len(line) > room:
            lines.append(line[: width - 1] + "\\")
            line = line[width - 1 :]
    return [*lines, line]


def _money(value) -> str:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "unknown"
    return "under $0.01" if 0 < value < 0.005 else f"${value:.2f}"


def _plan(view: dict, width: int) -> list[Line]:
    """The plan a run waits to have approved, in full: the approval command below it is bound to this plan."""
    plan, need = view.get("displayed_plan") or {}, view.get("needs") or {}
    text = plan.get("review_markdown")
    if need.get("kind") != "approve_plan" or not isinstance(text, str) or plan.get("token") != need.get("token"):
        return []
    lines: list[Line] = [("", PLAIN), (f"Plan to approve (revision {plan.get('revision')})", HEAD)]
    for line in ESCAPED.sub(r"\1", text).splitlines():
        if line.startswith("#"):
            lines.append(("  " + _fit(line.lstrip("# "), width - 2), HEAD))
        else:
            lines += [(part, PLAIN) for part in _wrap(line, width)] if line.strip() else [("", PLAIN)]
    return lines


def run_lines(board: Board, width: int) -> list[Line]:
    """The run screen: one run's status view, top to bottom."""
    workspace, run_dir = board.opened or ("", "")
    view = board.view
    if view is None:
        note = f"Could not read this run: {board.view_error}." if board.view_error else "Reading the run…"
        return [(_fit(Path(run_dir).name, width), TITLE), (_fit(workspace, width), DIM), ("", PLAIN), (note, DIM)]
    task = next((line.strip() for line in str(view.get("task") or "").splitlines() if line.strip()), Path(run_dir).name)
    lines: list[Line] = [(_fit(task, width), TITLE)]
    where = (view.get("worktree") or {}).get("label")
    lines.append((_fit(" · ".join(str(part) for part in (workspace, where) if part), width), DIM))
    if board.view_error:
        lines.append((_fit(f"Could not reread this run: {board.view_error}. Showing the last read.", width), ALERT))
    status = view.get("status_label") or view.get("status") or ""
    lines += [("", PLAIN), (_fit(f"{status}", width), HEAD)]
    progress = view.get("progress") or {}
    if progress.get("line"):
        lines += [(part, PLAIN) for part in _wrap(str(progress["line"]), width)]

    pipeline = [row for row in view.get("pipeline") or [] if isinstance(row, dict)]
    if pipeline:
        lines += [("", PLAIN), ("Pipeline", HEAD)]
        for row in pipeline:
            model = " ".join(str(part) for part in (row.get("model"), row.get("reasoning_effort")) if part)
            spent = row.get("spent_usd")
            cost = "" if spent is None else _money(spent) + ("+" if row.get("spent_complete") is False else "")
            text = f"  {row.get('state') or '':<8} {row.get('label') or row.get('job') or '':<20} {model:<28} {cost}"
            lines.append((_fit(text.rstrip(), width), PICKED if row.get("state") == "working" else PLAIN))

    tasks = (progress.get("tasks") or {}).get("items") or []
    if tasks:
        lines += [
            ("", PLAIN),
            (_fit(f"Tasks   {(progress.get('tasks') or {}).get('label') or ''}".rstrip(), width), HEAD),
        ]
        for item in tasks:
            if isinstance(item, dict):
                state = "current" if item.get("current") else item.get("state") or ""
                text = f"  {state:<8} {item.get('id') or item.get('number') or ''}  {_one(item.get('label') or '')}"
                lines.append((_fit(text, width), PICKED if item.get("current") else PLAIN))

    chat = view.get("chat") or {}
    now = [entry for entry in chat.get("now") or [] if isinstance(entry, dict)]
    if now:
        lines += [("", PLAIN), (f"Working now ({len(now)})", HEAD)]
        lines += [
            (_fit(f"  {_one(entry.get('agent') or 'Agent')}: {_one(entry.get('text') or '')}", width), PLAIN)
            for entry in now
        ]
    if chat.get("last_output_at"):
        lines.append((_fit(f"  last output at {chat['last_output_at']}", width), DIM))

    total = view.get("cost") or {}
    if total:
        cap = total.get("cap_usd")
        spent = _money(total.get("spent_usd")) + ("+" if total.get("complete") is False else "")
        lines += [
            ("", PLAIN),
            (_fit(f"Cost   {spent}" + (f" of a {_money(cap)} cap" if cap else ", no cap"), width), HEAD),
        ]

    summary = view.get("summary") or {}
    if summary.get("sentence"):
        lines += [("", PLAIN), ("Result", HEAD)] + [(part, PLAIN) for part in _wrap(str(summary["sentence"]), width)]

    lines += _plan(view, width)
    command = view.get("next_command")
    needs = (view.get("needs") or {}).get("kind")
    if command and needs and needs != "continue":
        lines += [("", PLAIN), ("Needs you", HEAD)]
        explained = (view.get("explanation") or {}).get("explanation")
        if explained:
            lines += [(part, PLAIN) for part in _wrap(str(explained), width)]
        lines += [(part, ALERT) for part in _command(str(command), width)]
    elif command:
        lines += [("", PLAIN), ("Next", HEAD)] + [(part, DIM) for part in _command(str(command), width)]
    return lines


def frame(board: Board, width: int, height: int, now: float) -> list[Line]:
    """Exactly what the screen shows: the lines that fit, the highlighted row in view, the keys last."""
    body = max(1, height - 2)
    if board.opened:
        lines, at, keys = run_lines(board, width), None, RUN_KEYS
        kept = 2 + bool(board.view is not None and board.view_error)  # the task, where it runs, a failed reread
    else:
        lines, at = list_lines(board, width, now)
        keys, kept = LIST_KEYS, len(_list_head(board, width, now))
    # The first lines say what is on screen and stay there; the rest scrolls under them. A window too
    # short to spare them scrolls everything.
    kept = kept if body >= kept + 4 else 0
    top, lines = lines[:kept], lines[kept:]
    room = body - len(top)
    board.page = max(1, room - 2)
    if board.opened:
        board.scroll = min(board.scroll, max(0, len(lines) - room))
        first = board.scroll
    else:
        at = None if at is None else at - kept
        first = 0 if at is None or at < room - 3 else min(at - room // 2, max(0, len(lines) - room))
    window = top + lines[first : first + room]
    window += [("", PLAIN)] * (body - len(window))
    more = "more below · " if first + room < len(lines) else ""  # first: a narrow window cuts the end of the line
    return [*window, ("", PLAIN), (_fit(more + keys, width), DIM)]


KEYS = {
    "q": "q",
    "r": "r",
    "n": "n",
    "a": "a",
    "k": "up",
    "j": "down",
    " ": "pagedown",
    "G": "end",
    "g": "home",
    "\n": "enter",
    "\r": "enter",
    "\x1b": "back",
    # Backspace as the terminal sends it. curses names only the one its terminfo entry lists, and macOS's
    # entries list Ctrl-H while Terminal and iTerm2 send DEL, so there it arrives as a plain character.
    "\x7f": "back",
    "\b": "back",
}


def run(stdscr, board: Board, poller, *, clock=time.time) -> None:
    """Draw and reread until the person quits."""
    import curses

    named = {
        curses.KEY_UP: "up",
        curses.KEY_DOWN: "down",
        curses.KEY_LEFT: "left",
        curses.KEY_ENTER: "enter",
        curses.KEY_BACKSPACE: "back",
        curses.KEY_NPAGE: "pagedown",
        curses.KEY_PPAGE: "pageup",
        curses.KEY_END: "end",
        curses.KEY_HOME: "home",
    }
    styles = {
        TITLE: curses.A_BOLD,
        HEAD: curses.A_BOLD,
        PLAIN: curses.A_NORMAL,
        DIM: curses.A_DIM,
        PICKED: curses.A_REVERSE,
        ALERT: curses.A_BOLD,
    }
    with contextlib.suppress(curses.error):  # a terminal that cannot hide the cursor still shows the board
        curses.curs_set(0)
    curses.set_escdelay(25)  # Esc goes back at once; an arrow key's sequence arrives well inside this
    stdscr.timeout(200)
    board.asked = clock()
    while True:
        apply(board, poller.tick(targets(board)), clock())
        height, width = stdscr.getmaxyx()
        stdscr.erase()
        for y, (text, style) in enumerate(frame(board, max(20, width - 1), height, clock())[:height]):
            with contextlib.suppress(curses.error):  # the last cell of a window that just shrank
                stdscr.addstr(y, 0, text[: max(0, width - 1)], styles[style])
        stdscr.refresh()
        try:
            code = stdscr.get_wch()
        except curses.error:
            continue  # no key within the timeout: reread and redraw
        key = named.get(code) if isinstance(code, int) else KEYS.get(code)
        if key is None:
            continue  # a resize, or a key the board does not use
        done = press(board, key)
        if done == "quit":
            return
        if done == "reread":
            poller.refresh(onscreen(board))  # not the list behind an open run: it is read when it is shown


def parser() -> argparse.ArgumentParser:
    return argparse.ArgumentParser(
        prog="autocode board",
        description="Watch every AutoCode run on this machine in one terminal screen: which need you, which "
        "are working and at what stage, and the command each accepts next. Read-only: it starts, approves and "
        "resumes nothing. For one listing, or for scripts, use `autocode runs --all --active [--json]`.",
        allow_abbrev=False,
    )


def cli(argv: list[str] | None = None) -> int:
    parser().parse_args(argv)
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(NO_TERMINAL, file=sys.stderr)
        return 2
    import curses

    board = Board(home=os.getcwd())
    poller = polling.StatusPoller(
        AUTOCODE, env=dict(os.environ), timeout=READ_TIMEOUT, limit=MAX_READ_TIMEOUT, rest=REST
    )
    try:
        curses.wrapper(run, board, poller)
    except KeyboardInterrupt:
        pass  # Ctrl-C leaves the board; it owns no run
    finally:
        poller.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(cli())
