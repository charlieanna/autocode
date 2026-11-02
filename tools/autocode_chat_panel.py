"""What the full-screen chat shows beside the conversation, from the status view alone.

The terminal chat (autocode_chat_client) reads ``autocode --status`` and hands each
view to its renderer. This module turns a view into the side panel's sections (where
the run is, its requirements, its tasks and open problems), keeps a log of how the run
moved from one view to the next, and lists the ``/`` commands the chat offers. It is
pure: it reads only the view it is given, never a run's files, and it decides nothing
about the run. The commands that act are the client's, and each takes only an action
the view names: ``/pause`` is the view's pause offer, ``/diff`` a saved step's
``compare`` and ``/undo`` its ``restore`` (``view["code_checkpoints"]``).
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

try:
    from . import autocode_chat_changeset as changeset
    from . import autocode_chat_cost as cost
    from . import autocode_chat_meter as meter
    from . import autocode_chat_stage_policy as stage_policy
    from . import autocode_chat_verification as verification
except ImportError:
    import autocode_chat_changeset as changeset
    import autocode_chat_cost as cost
    import autocode_chat_meter as meter
    import autocode_chat_stage_policy as stage_policy
    import autocode_chat_verification as verification

LOG_LIMIT = 200
STAGE_ROWS = 4  # the newest stages the panel lists; /details has every attempt
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
ASCII_MARKS = {
    "✓": "+",
    "▸": ">",
    "·": "-",
    "✗": "x",
    "?": "?",
    "±": "~",
    "$": "$",
    "∴": ":",
    "»": ">",
    "●": "*",
    "↻": "~",
}
EVENT_MARKS = {"edit": "±", "tool": "·"}  # user-facing activity; internal command text stays hidden
EVENT_ROWS = 6  # how many of the newest events the panel lists
LIVE_MARKS = {"thinking": "∴", "message": "»", "edit": "±", "tool": "·"}  # visible live-log entry kinds
LIVE_KINDS = {"thinking": ("thinking",), "messages": ("message",), "tools": ("edit", "tool")}
LIVE_LIMIT = 500  # entries the live log keeps


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


@dataclass(frozen=True)
class Confirm:
    """A client command that waits for the person's yes: what it will do, then the question."""

    text: str
    question: str
    then: Callable[[], str]


COMMANDS = (
    Command("/plan", "Show the plan's requirements and how each stands"),
    Command("/tasks", "Show the task list"),
    Command("/log", "Show how the run has moved so far"),
    Command("/details", "Why the run is where it is"),
    Command("/live", "Show or hide the live agent log; /live NAME, thinking or tools filters it"),
    Command("/diff", "The saved code steps; /diff N shows what changed since step N"),
    Command("/undo", "Go back to a saved step on a new branch: /undo N"),
    Command("/changeset", "This run's change set: every run, and approving or checking them together"),
    Command("/runs", "Your chats in every project, open and finished; /open N opens one"),
    Command("/open", "Open chat N from /runs and carry on there"),
    Command("/new", "Start a new chat in a new tab (Ctrl-T)"),
    Command("/tab", "Switch to tab N; Ctrl-←/→ step through the tabs"),
    Command("/mode", "The mode new chats start in: /mode ID"),
    Command("/engine", "The engine new chats run on: /engine NAME"),
    Command("/connect", "Backends: check one, sign in, or use it for new chats"),
    Command("/stages", "Each stage's model and reasoning level for new chats"),
    Command("/repos", "Show configured source roots and discovered repositories"),
    Command("/attention", "Saved alerts: inspect, acknowledge or open (also F8)"),
    Command("/panel", "Show or hide the side panel"),
    Command("/pause", "Ask AutoCode to pause after the current step"),
    Command("/help", "List these commands"),
    Command("/quit", "Leave the chat; the run stays as it is"),
)


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(value) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


# The commands the chat client carries out: /diff and /undo through actions its fresh status view
# names, /runs, /open and /new through the run lists (`autocode runs --json`), /mode, /engine and
# /connect through `modes list --json` and `connections list --json` (autocode_chat_setup), /stages through
# `stages list --json` (autocode_chat_stages), /changeset
# through `changeset status --json` (autocode_chat_changeset).
CLIENT_COMMANDS = (
    "/repos",
    "/diff",
    "/undo",
    "/changeset",
    "/runs",
    "/open",
    "/new",
    "/tab",
    "/mode",
    "/engine",
    "/connect",
    "/stages",
    "/attention",
)
# Leaving a working run requires its current durable-detach capability.
SWITCHING_COMMANDS = ("/open", "/new", "/tab")


# What needs a run to act on: none of these does anything before the first chat is open. ``/new``
# is not one: before a chat it says the person is already in a new chat (#1759).
RUN_COMMANDS = (
    "/plan",
    "/tasks",
    "/log",
    "/details",
    "/live",
    "/diff",
    "/undo",
    "/changeset",
    "/tab",
    "/panel",
    "/pause",
)


def commands(
    *, asking: bool, client: bool = True, run: bool = True, detachable: bool = False, draft: bool = False
) -> list[Command]:
    """The commands that do something now. ``/pause`` only while AutoCode works: the client then
    takes the pause its fresh status view offers, or says there is none. ``/quit`` and ``/undo``
    only at a question: leaving while AutoCode works would leave it running unseen, and the view
    offers going back only at a pause; ``/open`` and ``/new`` too, as they leave this run. Without a
    client the client's commands are not offered, and before a chat is open (``run`` false) only those
    that work without one: opening a chat, /new (which says this already is one), choosing what a new
    one starts with, help and leaving. ``draft``: the client's tab that Ctrl-T opened, whose request is not
    sent yet; it has no run, but /tab still works there, as nothing is left running by leaving it (#1997)."""
    hidden = {"/pause"} if asking else {"/quit", "/undo", *SWITCHING_COMMANDS}
    if detachable:
        hidden.discard("/quit")
        hidden.difference_update(SWITCHING_COMMANDS)
    if not client:
        hidden.update(CLIENT_COMMANDS)
    if not run:
        hidden.update(RUN_COMMANDS)
        if draft:
            hidden.discard("/tab")
    return [command for command in COMMANDS if command.name not in hidden]


def matching(prefix: str, available: list[Command]) -> list[Command]:
    """The commands a typed ``/word`` could be, in menu order."""
    word = prefix.strip().lower()
    return [command for command in available if command.name.startswith(word)]


def needs_you(view: dict | None) -> str | None:
    """What the run waits on a person for. Nothing, while it continues on its own: its own words then
    name an internal step (AutoResolver reconciling an attempt), not a request."""
    view = view or {}
    needs = _mapping(view.get("progress")).get("needs_you")
    if not needs or needs == NOTHING_NEEDED or _mapping(view.get("needs")).get("kind") == "continue":
        return None
    return str(needs)


def events(view: dict | None) -> list[dict]:
    """The running attempt's user-facing edits and tool calls, oldest first (``chat.activity``)."""
    rows = _rows(_mapping(_mapping(view).get("chat")).get("activity"))
    return [row for row in rows if row.get("kind") in EVENT_MARKS and isinstance(row.get("text"), str)]


def event_row(event: dict) -> Row:
    failed = event.get("status") == "failed"
    style = "bad" if failed else "accent" if event.get("status") == "running" else "plain"
    return Row("✗" if failed else EVENT_MARKS[event["kind"]], event["text"], style)


def doing(view: dict | None) -> str | None:
    """The newest event as a short phrase for the status row, or None when the view has none."""
    activity_rows = _rows(_mapping(_mapping(view).get("chat")).get("activity"))
    if activity_rows and activity_rows[-1].get("kind") == "command":
        return None
    shown = events(view)
    if not shown:
        return None
    event = shown[-1]
    verb = {"command": "Running ", "edit": "Editing: ", "tool": "Using "}[event["kind"]]
    return verb + event["text"]


def _utc_now():
    return datetime.now(UTC)


def stage_section(view: dict | None, now=None) -> Section | None:
    """Each stage the run has launched: how it went, the model it ran on, its time, tokens and cost.
    Each takes a row for how it went and one for the model it ran on and the tokens it used."""
    ran = meter.stages(view, now or _utc_now())
    if not ran:
        return None
    rows: list[Row] = []
    if len(ran) > STAGE_ROWS:
        rows.append(Row("·", f"{len(ran) - STAGE_ROWS} earlier stages: /details", "dim"))
    for row in ran[-STAGE_ROWS:]:
        head, detail, style = meter.stage_lines(row)
        mark, _, text = head.partition(" ")
        rows.append(Row(mark, text, style))
        if detail.strip():  # the model it ran on and what it used; a stage with no model has none
            rows.append(Row(" ", detail.strip(), "dim"))
    return Section("Stages", meter.total_text(meter.run_total(view)), tuple(rows))


def sections(view: dict | None, *, launched: bool = False) -> list[Section]:
    """The panel for one view: where the run is, what it is doing, then its requirements, tasks and open problems.

    ``launched``: the client has just started the run's next step, so a view that still waits on the
    person is the one read before the step saved its first state; it says so instead of asking again."""
    view = view or {}
    progress = _mapping(view.get("progress"))
    lines = activity(view)
    now = lines[0] if lines else progress.get("headline") or str(view.get("status") or "Starting").replace("_", " ")
    rows = [Row("·", line, "dim") for line in lines[1:]]
    if needs := needs_you(view):
        if launched:
            now, rows = "Starting the next step…", []
        else:
            rows.insert(0, Row("!", needs, "warn"))
    if view.get("status") in ("TASK_COMPLETE", "COMPLETE"):
        now = verification.cue(view)
    if trouble := meter.struggling(view, _utc_now()):
        rows.insert(0, Row("!", trouble, "warn"))
    result = [Section("Now", now, tuple(rows))]
    if ran := stage_section(view):
        result.append(ran)
    if proof := verification.compact(view):
        summary, entries = proof
        result.append(Section(verification.title(view), summary, tuple(Row("·", line, "dim") for line in entries)))
    if usage := cost.compact(view):
        summary, entries = usage
        result.append(Section("Usage", summary, tuple(Row("·", line, "dim") for line in entries)))
    if path := stage_policy.compact(view):  # which planning path a bug-fix run took and why (#1884 F2)
        summary, entries = path
        result.append(Section("Plan path", summary, tuple(Row("·", line, "dim") for line in entries)))
    if linked := changeset.compact(view):  # the other runs of this run's change set (#2066)
        summary, entries = linked
        result.append(Section("Change set", summary, tuple(Row("·", line, "dim") for line in entries)))
    if coordinated := _mapping(view.get("coordination")):
        summary = str(coordinated.get("phase", "discovering")).replace("_", " ")
        if coordinated.get("active"):
            summary += " · " + str(coordinated["active"])
        result.append(
            Section(
                "Coordinated bug fix",
                summary,
                tuple(
                    Row("·", f"{row['target']}: {row.get('pr_url') or row['status']}", "plain")
                    for row in _rows(coordinated.get("members"))
                ),
            )
        )
    if recent := events(view):
        result.append(Section("Activity", "", tuple(event_row(event) for event in recent[-EVENT_ROWS:])))
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


def activity(view: dict | None) -> list[str]:
    """What the run is doing, as the view's ``chat.progress`` lines say: the current step, then its
    recent attempts. The task and requirement counts it repeats are left to their own sections."""
    view = view or {}
    lines = [str(line) for line in _mapping(view.get("chat")).get("progress") or [] if line]
    progress = _mapping(view.get("progress"))
    counts = " · ".join(
        str(row.get("label"))
        for row in (_mapping(progress.get("tasks")), _mapping(progress.get("requirements")))
        if row.get("total") and row.get("label")
    )
    return [line for number, line in enumerate(lines) if number == 0 or line != counts]


def newest(lines: list[str]) -> str | None:
    """The latest attempt the activity lines name: the last of "Recent steps: A · B · C"."""
    if not lines:
        return None
    last = lines[-1].rsplit(" · ", 1)[-1]
    return last.split(": ", 1)[-1] or None


def steps(view: dict | None) -> list[dict]:
    """The run's saved code steps, in order; ``/diff N`` and ``/undo N`` count from 1."""
    return _rows(_mapping((view or {}).get("code_checkpoints")).get("rows"))


def finished_text(view: dict | None) -> str:
    """A finished run's closing line: the files its saved steps changed, and where to see the change.
    Empty when no step recorded a file, so nothing is claimed that the view does not name."""
    files: list[str] = []
    for row in steps(view):
        files.extend(str(path) for path in row.get("changed_files") or [] if str(path) not in files)
    if not files:
        return ""
    named = ", ".join(files[:6]) + (f" and {len(files) - 6} more" if len(files) > 6 else "")
    return f"Changed {len(files)} file{'' if len(files) == 1 else 's'}: {named}. /diff shows what changed."


def steps_text(view: dict | None, command: str) -> str:
    """The saved steps a bare ``/diff`` or ``/undo`` lists."""
    rows = steps(view)
    if not rows:
        return "There are no saved code steps yet. AutoCode saves one each time the Builder changes code." + (
            " Type /diff now to see what the Builder has changed so far."
            if command == "/diff" and working_offered(view)
            else ""
        )
    lines = []
    for number, row in enumerate(rows, 1):
        files = [str(path) for path in row.get("changed_files") or []]
        named = {action.get("id") for action in _rows(row.get("actions"))}
        note = "" if "compare" in named else f" (unavailable: {row.get('reason') or 'not saved'})"
        lines.append(f"{number}. {row.get('label') or row.get('id')}{note}")
        if files:
            lines.append("   " + ", ".join(files[:6]) + (f" and {len(files) - 6} more" if len(files) > 6 else ""))
    blocked = _mapping((view or {}).get("code_checkpoints")).get("blocked_reason")
    if command == "/undo":
        lines.append("Type /undo N to go back to step N." if not blocked else blocked_text(view, blocked))
    else:
        lines.append("Type /diff N to see what changed since step N.")
        if working_offered(view):
            lines.append("Type /diff now to see what the Builder has changed so far.")
    return "\n".join(lines)


def blocked_text(view: dict | None, reason: str) -> str:
    """Why going back is not offered. A finished run is not waiting on a pause, whatever the reason says."""
    if (view or {}).get("done"):
        return "Going back is offered only at a pause, and this run has finished."
    return f"Going back is not possible now: {reason}"


def working_offered(view: dict | None) -> bool:
    """Whether the view names the display-only diff of what the running Builder has changed so far."""
    return bool(_mapping(_mapping((view or {}).get("code_checkpoints")).get("working")).get("next_command"))


def comparison_text(
    comparison: dict, number: int | str = "that", *, limit: int = 400, since: str | None = None, working: bool = False
) -> str:
    """What a ``compare`` (or the running Builder's ``working``) returned: the files that differ, then the diff."""
    files = [str(path) for path in comparison.get("changed_files") or []]
    since = since or (f"step {number}" if str(number).isdigit() else "that step")
    if not files and working:
        return f"The Builder has not changed any files since {since}."
    if not files:
        return f"Nothing has changed since {since}: the files match it. /diff lists what each step changed."
    lines = [f"{len(files)} file{'' if len(files) == 1 else 's'} changed since {since}:"]
    lines.extend(f"  {path}" for path in files)
    patch = str(comparison.get("patch") or "").splitlines()
    if patch:
        lines.append("")
        lines.extend(patch[:limit])
    if len(patch) > limit or comparison.get("truncated"):
        lines.append("(The diff is longer than this; the full changes are in the run's branch.)")
    return "\n".join(lines)


def text(name: str, view: dict | None, log: StageLog, available: list[Command] | None = None) -> str:
    """What ``/plan``, ``/tasks``, ``/log``, ``/details`` and ``/help`` print into the conversation."""
    view = view or {}
    if name == "/help":
        commands = available if available is not None else COMMANDS
        return "\n".join(f"{command.name:<9} {command.description}" for command in commands)
    if name == "/log":
        return "\n".join(log.lines) or "Nothing has happened yet."
    if name == "/details":
        explanation = _mapping(view.get("explanation")).get("explanation")
        said = str(explanation or (view.get("progress") or {}).get("line") or "AutoCode has said nothing more yet.")
        more = "\n\n".join(filter(None, (verification.details(view), cost.details(view), stage_policy.details(view))))
        return said + ("\n\n" + more if more else "")
    wanted = {"/plan": "Requirements", "/tasks": "Tasks"}[name]
    for section in sections(view):
        if section.title == wanted:
            return "\n".join([section.summary, *(f"{row.mark} {row.text}" for row in section.rows)])
    return "There is no plan yet." if name == "/plan" else "There is no task list yet."


def log_entries(view: dict | None) -> list[dict]:
    """The running agents' reasoning, messages and non-command tool activity (``chat.log``)."""
    rows = _rows(_mapping(_mapping(view).get("chat")).get("log"))
    return [
        row
        for row in rows
        if row.get("kind") in LIVE_MARKS
        and isinstance(row.get("id"), str)
        and isinstance(row.get("text"), str)
        and isinstance(row.get("agent"), str)
    ]


@dataclass
class LiveLog:
    """What each agent has thought, said and done, kept across views: the view holds only the newest
    entries, so each is kept by its id, and a later view of the same entry updates it in place."""

    entries: dict[str, dict] = field(default_factory=dict)
    filter: str = ""

    def observe(self, view: dict | None) -> bool:
        """Add the view's new entries and update changed ones; True when anything changed."""
        changed = False
        for row in log_entries(view):
            entry = {key: row.get(key) for key in ("agent", "kind", "text", "status")}
            if self.entries.get(row["id"]) != entry:
                self.entries[row["id"]] = entry
                changed = True
        for key in list(self.entries)[:-LIVE_LIMIT]:
            del self.entries[key]
        return changed

    def agents(self) -> list[str]:
        return list(dict.fromkeys(entry["agent"] for entry in self.entries.values()))

    def shown(self) -> list[dict]:
        """The entries the filter keeps: a kind (thinking, messages, tools) or part of an agent's name."""
        wanted = self.filter.strip().casefold()
        if not wanted:
            return list(self.entries.values())
        if wanted in LIVE_KINDS:
            return [entry for entry in self.entries.values() if entry["kind"] in LIVE_KINDS[wanted]]
        return [entry for entry in self.entries.values() if wanted in entry["agent"].casefold()]

    def title(self) -> str:
        return "LIVE · " + (self.filter.strip() or "all agents")

    def text(self, limit: int = 60) -> str:
        """The newest entries as plain lines, for where the pane does not fit."""
        lines = [f"{entry['agent']} {LIVE_MARKS[entry['kind']]} {entry['text']}" for entry in self.shown()[-limit:]]
        return "\n".join(lines) or "No agent is working right now."


def ascii_mark(mark: str) -> str:
    return ASCII_MARKS.get(mark, mark)


@dataclass
class StageLog:
    """How the run moved: one timestamped line each time a view says something new."""

    lines: list[str] = field(default_factory=list)
    _last: dict = field(default_factory=dict)
    _seen: Counter = field(default_factory=Counter)  # how many times each (kind, text, status) has been in view

    def _events(self, view: dict | None) -> list[str]:
        """A line for each new user-facing event; command text is never included."""
        now = Counter((event["kind"], event["text"], event.get("status")) for event in events(view))
        said = []
        for (kind, text, status), count in now.items():
            earlier = sum(n for (k, t, _), n in self._seen.items() if (k, t) == (kind, text))
            if sum(n for (k, t, _), n in now.items() if (k, t) == (kind, text)) > earlier:
                said.append({"command": "Ran", "edit": "Edited", "tool": "Used"}[kind] + f": {text}")
            if status == "failed" and count > self._seen[(kind, text, status)]:
                said.append(f"Failed: {text}")
        if now:
            self._seen = now
        return said

    def observe(self, view: dict | None, now: float | None = None) -> bool:
        """Add what changed since the last view; True when a line was added."""
        progress = _mapping((view or {}).get("progress"))
        if not progress:
            return False
        lines = activity(view)
        facts = {
            "headline": lines[0] if lines else progress.get("headline"),
            "recent": newest(lines[1:]),
            "needs": needs_you(view),
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
            if key in ("headline", "recent"):
                said.append(str(value))
            elif key == "needs":
                said.append(f"Needs you: {value}")
            elif key == "problems" and (value or key in self._last):
                said.append(f"{value} open problem{'' if value == 1 else 's'}")
            elif key.startswith("task ") and value in ("working", "done"):
                said.append(f"Task {key[5:]} {'started' if value == 'working' else 'done'}")
            elif key.startswith("requirement ") and value != "unchecked":
                said.append(f"{key[12:]} {str(value).replace('_', ' ')}")
        said.extend(self._events(view))
        self._last = facts
        stamp = time.strftime("%H:%M:%S", time.localtime(now))
        self.lines.extend(f"{stamp} {line}" for line in said)
        del self.lines[:-LOG_LIMIT]
        return bool(said)


RUN_STATES = {"TASK_COMPLETE": "complete", "COMPLETE": "complete", "COMPLETED": "complete"}


def _readable(row: dict) -> bool:
    return row.get("availability") in (None, "available")


def run_rows(listed: list[dict], here=None) -> list[dict]:
    """The chats /runs numbers, grouped by project: this project first, then the others by their
    latest activity; newest first within each. Another project's chat that cannot be read (its run
    was deleted) is left out; ``hidden`` counts those."""
    seen: set[str] = set()
    groups: dict[str, list[dict]] = {}
    for row in listed:
        if not isinstance(row, dict) or not row.get("run_dir") or str(row["run_dir"]) in seen:
            continue
        seen.add(str(row["run_dir"]))
        workspace = str(row.get("workspace") or "")
        if here is not None and workspace != str(here) and not _readable(row):
            continue
        groups.setdefault(workspace, []).append(row)

    def newest(row: dict) -> tuple[str, str]:
        return (str(row.get("updated_at") or ""), str(row.get("run_dir")))

    for rows in groups.values():
        rows.sort(key=newest, reverse=True)
    order = [str(here)] * (str(here) in groups) + sorted(
        (workspace for workspace in groups if workspace != str(here)),
        key=lambda workspace: newest(groups[workspace][0]),
        reverse=True,
    )
    return [row for workspace in order for row in groups[workspace]]


def hidden(listed: list[dict], here) -> int:
    """How many of another project's chats /runs leaves out because they cannot be read."""
    return sum(
        1
        for row in listed
        if isinstance(row, dict) and str(row.get("workspace") or "") != str(here) and not _readable(row)
    )


def run_state(row: dict) -> str:
    """A chat's state in a few words: complete, what it needs you for, working, or unreadable."""
    if row.get("availability") not in (None, "available"):
        return "cannot be read"
    if done := RUN_STATES.get(str(row.get("status") or "")):
        return done
    if row.get("bucket") == "needs you":
        return f"needs you: {row.get('pause_category_label') or row.get('needs') or 'a decision'}"
    return str(row.get("bucket") or row.get("status") or "unknown")


def project_name(workspace: str, here=None) -> str:
    """A project's heading in /runs: its folder name, where it is, and whether it is this one."""
    path = workspace.replace(str(Path.home()), "~", 1) if workspace else "(unknown project)"
    name = Path(workspace).name if workspace else "Other"
    return f"{name}  {path}" + ("  (this project)" if here is not None and workspace == str(here) else "")


def runs_text(rows: list[dict], current=None, *, here=None, left_out: int = 0) -> str:
    """What /runs prints: each chat's number, request, state and when it last moved, under its project."""
    if not rows:
        return "No chats yet. Type a request to start one."
    lines: list[str] = []
    grouped = here is not None or len({str(row.get("workspace") or "") for row in rows}) > 1
    project = None
    for number, row in enumerate(rows, 1):
        workspace = str(row.get("workspace") or "")
        if grouped and workspace != project:
            project = workspace
            if lines:
                lines.append("")
            lines.append(project_name(workspace, here))
        title = row.get("title") or row.get("name") or row.get("run_dir")
        mark = "  (this chat)" if current is not None and str(row.get("run_dir")) == str(current) else ""
        when = str(row.get("updated_at") or "").replace("T", " ")[:16]
        lines.append(f"{number:>3}. {title}{mark}")
        linked = " · change set" if row.get("changeset") else ""
        lines.append(f"     {run_state(row)}" + (f" · {when}" if when else "") + linked)
    if sets := changeset.groups(rows):
        lines.extend(["", *sets])
    if left_out:
        lines.append(f"({left_out} more in other projects cannot be opened: their runs were removed.)")
    lines.append("Type /open N to open one, or /new to start another.")
    return "\n".join(lines)
