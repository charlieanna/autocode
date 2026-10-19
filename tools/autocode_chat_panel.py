"""What the full-screen chat shows beside the conversation, from the status view alone.

The terminal chat (autocode_chat_client) reads ``autocode --status`` and hands each
view to its renderer. This module turns a view into the side panel's sections (where
the run is, its requirements, its tasks and open problems), keeps a log of how the run
moved from one view to the next, and lists the ``/`` commands the chat offers. It is
pure: it reads only the view it is given, never a run's files, and it decides nothing
about the run. The one command that acts, ``/pause``, is the view's own pause offer,
which the client takes only when its fresh status view offers it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

LOG_LIMIT = 200
NOTHING_NEEDED = "nothing needed from you"  # the progress view's needs_you when no one is waited on
TASK_MARKS = {"done": "✓", "working": "▸", "waiting": "·", "unknown": "·"}
REQUIREMENT_MARKS = {
    "checked": "✓",
    "checked_earlier": "✓",
    "reviewed": "✓",
    "failed": "✗",
    "awaiting_review": "?",
    "unchecked": "·",
}
ASCII_MARKS = {"✓": "+", "▸": ">", "·": "-", "✗": "x", "?": "?"}


@dataclass(frozen=True)
class Row:
    """One line of a panel section: a state mark, its text, and the style it is drawn in."""

    mark: str
    text: str
    style: str = "plain"


@dataclass(frozen=True)
class Section:
    title: str
    summary: str
    rows: tuple[Row, ...] = ()


@dataclass(frozen=True)
class Command:
    name: str
    description: str


COMMANDS = (
    Command("/plan", "Show the plan's requirements and how each stands"),
    Command("/tasks", "Show the task list"),
    Command("/log", "Show how the run has moved so far"),
    Command("/details", "Why the run is where it is"),
    Command("/panel", "Show or hide the side panel"),
    Command("/pause", "Ask AutoCode to pause after the current step"),
    Command("/help", "List these commands"),
    Command("/quit", "Leave the chat; the run stays as it is"),
)


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(value) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def commands(*, asking: bool) -> list[Command]:
    """The commands that do something now. ``/pause`` only while AutoCode works: the client then
    takes the pause its fresh status view offers, or says there is none. ``/quit`` only at a
    question, since leaving while AutoCode works would leave it running unseen."""
    hidden = "/pause" if asking else "/quit"
    return [command for command in COMMANDS if command.name != hidden]


def matching(prefix: str, available: list[Command]) -> list[Command]:
    """The commands a typed ``/word`` could be, in menu order."""
    word = prefix.strip().lower()
    return [command for command in available if command.name.startswith(word)]


def sections(view: dict | None) -> list[Section]:
    """The panel for one view: where the run is, then its requirements, tasks and open problems."""
    view = view or {}
    progress = _mapping(view.get("progress"))
    result = [Section("Now", progress.get("headline") or str(view.get("status") or "Starting").replace("_", " "))]
    needs = progress.get("needs_you")
    if needs and needs != NOTHING_NEEDED:
        result[0] = Section("Now", result[0].summary, (Row("!", str(needs), "warn"),))
    requirements = _mapping(progress.get("requirements"))
    if _rows(requirements.get("items")):
        result.append(
            Section(
                "Requirements",
                str(requirements.get("label") or ""),
                tuple(
                    Row(
                        REQUIREMENT_MARKS.get(str(row.get("state")), "·"),
                        f"{row.get('id')} {row.get('label')}",
                        "bad"
                        if row.get("state") == "failed"
                        else "good"
                        if row.get("state") != "unchecked"
                        else "plain",
                    )
                    for row in _rows(requirements.get("items"))
                ),
            )
        )
    tasks = _mapping(progress.get("tasks"))
    if _rows(tasks.get("items")):
        result.append(
            Section(
                "Tasks",
                str(tasks.get("label") or ""),
                tuple(
                    Row(
                        TASK_MARKS.get(str(row.get("state")), "·"),
                        f"{row.get('number')}. {row.get('label')}",
                        "good"
                        if row.get("state") == "done"
                        else "accent"
                        if row.get("state") == "working"
                        else "plain",
                    )
                    for row in _rows(tasks.get("items"))
                ),
            )
        )
    problems = _mapping(progress.get("problems"))
    if type(problems.get("open")) is int and problems["open"]:
        result.append(
            Section(
                "Problems",
                f"{problems['open']} open",
                tuple(
                    Row("✗" if row.get("blocking", True) else "·", str(row.get("finding") or row.get("id")), "bad")
                    for row in _rows(problems.get("items"))
                ),
            )
        )
    return result


def text(name: str, view: dict | None, log: StageLog) -> str:
    """What ``/plan``, ``/tasks``, ``/log``, ``/details`` and ``/help`` print into the conversation."""
    view = view or {}
    if name == "/help":
        return "\n".join(f"{command.name:<9} {command.description}" for command in COMMANDS)
    if name == "/log":
        return "\n".join(log.lines) or "Nothing has happened yet."
    if name == "/details":
        explanation = _mapping(view.get("explanation")).get("explanation")
        return str(explanation or (view.get("progress") or {}).get("line") or "AutoCode has said nothing more yet.")
    wanted = {"/plan": "Requirements", "/tasks": "Tasks"}[name]
    for section in sections(view):
        if section.title == wanted:
            return "\n".join([section.summary, *(f"{row.mark} {row.text}" for row in section.rows)])
    return "There is no plan yet." if name == "/plan" else "There is no task list yet."


def ascii_mark(mark: str) -> str:
    return ASCII_MARKS.get(mark, mark)


@dataclass
class StageLog:
    """How the run moved: one timestamped line each time a view says something new."""

    lines: list[str] = field(default_factory=list)
    _last: dict = field(default_factory=dict)

    def observe(self, view: dict | None, now: float | None = None) -> bool:
        """Add what changed since the last view; True when a line was added."""
        progress = _mapping((view or {}).get("progress"))
        if not progress:
            return False
        facts = {
            "headline": progress.get("headline"),
            "needs": None if progress.get("needs_you") == NOTHING_NEEDED else progress.get("needs_you"),
            **{
                f"task {row.get('number')}": row.get("state")
                for row in _rows(_mapping(progress.get("tasks")).get("items"))
            },
            **{
                f"requirement {row.get('id')}": row.get("state")
                for row in _rows(_mapping(progress.get("requirements")).get("items"))
            },
            "problems": _mapping(progress.get("problems")).get("open"),
        }
        said = []
        for key, value in facts.items():
            if self._last.get(key) == value or value in (None, ""):
                continue
            if key == "headline":
                said.append(str(value))
            elif key == "needs":
                said.append(f"Needs you: {value}")
            elif key == "problems" and (value or key in self._last):
                said.append(f"{value} open problem{'' if value == 1 else 's'}")
            elif key.startswith("task ") and value in ("working", "done"):
                said.append(f"Task {key[5:]} {'started' if value == 'working' else 'done'}")
            elif key.startswith("requirement ") and value != "unchecked":
                said.append(f"{key[12:]} {str(value).replace('_', ' ')}")
        self._last = facts
        stamp = time.strftime("%H:%M:%S", time.localtime(now))
        self.lines.extend(f"{stamp} {line}" for line in said)
        del self.lines[:-LOG_LIMIT]
        return bool(said)
