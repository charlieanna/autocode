"""Chat-facing live activity of the running attempt, from its own provider event file.

``view["chat"]["activity"]`` lists what the running stage is doing right now: the commands it runs,
the files it edits and the other tools it calls, newest last. It is display only, like
``chat.progress``: never proof that a command passed, a change is right or a requirement is met, and
never authority to approve or retry. Reasoning, model prose and tool output are never copied; a
command is shown as its first line, bounded. Only the tail of the attempt's event file is read, and
only a file inside the run directory. It is empty unless the attempt is the one supervised now.
``view["chat"]["last_output_at"]`` (``last_output``) is when that file was last written, under the
same guards: the one fact that tells a quiet stage from a busy one.

Three event shapes are read, the ones the providers write: Codex-style items (also what the built-in
Claude provider writes), and OpenCode or Kilo ``tool_use`` parts. A row of any other shape is ignored.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

LIMIT = 20
READ_BYTES = 262144
TEXT_LIMIT = 160
PATH_LIMIT = 8
EDIT_TOOLS = ("edit", "write", "patch", "multiedit", "apply_patch", "applypatch")
SHELL_TOOLS = ("bash", "shell")
TARGET_KEYS = ("filePath", "file_path", "path", "pattern", "query", "url")  # what a read or search names
FILE_VERBS = {"add": "added", "update": "edited", "delete": "deleted"}
# Codex item types other than commands and file changes that are a tool call, with how they are named.
TOOL_ITEMS = {
    "mcp_tool_call": "MCP tool",
    "web_search": "web search",
    "collab_agent_tool_call": "agent tool",
    "tool_call": "tool",
    "function_call": "tool",
}


def _line(value, limit: int = TEXT_LIMIT) -> str:
    """The first line of a value, with control characters gone, bounded."""
    text = "".join(char if char.isprintable() else " " for char in " ".join(str(value or "").split("\n", 1)[0].split()))
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _relative(path, workspace) -> str:
    text = str(path or "")
    if workspace and text.startswith(str(workspace).rstrip("/") + "/"):
        text = text[len(str(workspace).rstrip("/")) + 1 :]
    return _line(text, 200)


def _quote(value) -> str:
    return f"“{_line(value, 60)}”"


# Plain words for the tools an agent uses to look around, by lower-case tool name: OpenCode's and
# Kilo's names and Claude Code's built-in ones (Read, Grep, Glob, WebFetch, Task). Each tool gives
# its line ("Searched tools for “context_packet”") and the short phrase that groups repeats of it
# ("Searched code"). A tool not named here keeps its own name.
def _describe(tool: str, given: dict, workspace) -> tuple[str, str] | None:
    def field(*keys):
        return next((given[key] for key in keys if isinstance(given.get(key), str) and given[key].strip()), "")

    path = _relative(field("filePath", "file_path", "path"), workspace)
    pattern, query, url = field("pattern"), field("query"), field("url")
    if tool in ("read", "view"):
        return (f"Read {path}" if path else "Read a file"), "Read files"
    if tool in ("grep", "search", "codesearch"):
        target = pattern or query
        where = f" {path}" if path else ""
        return (
            f"Searched{where} for {_quote(target)}" if target else f"Searched{where or ' the code'}"
        ), "Searched code"
    if tool == "glob":
        where = f" in {path}" if path else ""
        return (
            f"Looked for files matching {_line(pattern, 60)}{where}" if pattern else "Looked for files"
        ), "Listed files"
    if tool in ("list", "ls"):
        return (f"Listed {path}" if path else "Listed files"), "Listed files"
    if tool == "websearch":
        return (f"Searched the web for {_quote(query)}" if query else "Searched the web"), "Searched the web"
    if tool == "webfetch":
        return (f"Fetched {_line(url, 120)}" if url else "Fetched a web page"), "Fetched web pages"
    if tool in ("todowrite", "todoread"):
        return "Updated its to-do list" if tool == "todowrite" else "Read its to-do list", "Updated its to-do list"
    if tool == "task":
        what = field("description")
        return (f"Asked a helper agent to {_line(what, 100)}" if what else "Asked a helper agent"), "Used helper agents"
    return None


# What an agent is doing now, from its newest entry's group (chat.now).
DOING = {
    "Read files": "reading files",
    "Searched code": "searching the code",
    "Listed files": "looking through files",
    "Searched the web": "searching the web",
    "Fetched web pages": "reading a web page",
    "Updated its to-do list": "planning its steps",
    "Used helper agents": "working with a helper agent",
}


def _arguments(value) -> dict:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            return {}
    return value if isinstance(value, dict) else {}


def _outcome(status, exit_code=None) -> str:
    if status in ("failed", "declined", "error") or (type(exit_code) is int and exit_code != 0):
        return "failed"
    if status in ("in_progress", "pending", "running"):
        return "running"
    return "done"


def _edit(paths: list[str], verbs: list[str]) -> dict:
    shown = paths[:PATH_LIMIT]
    named = ", ".join(f"{verb} {path}" for verb, path in zip(verbs, shown, strict=False))
    more = f" and {len(paths) - PATH_LIMIT} more" if len(paths) > PATH_LIMIT else ""
    return {"kind": "edit", "text": _line(named + more, 2 * TEXT_LIMIT), "paths": shown}


def item_event(row: dict, workspace) -> tuple[str, dict] | None:
    """A Codex-style item (``item.started``, ``item.updated`` or ``item.completed``) as an event."""
    item = row.get("item")
    if not isinstance(item, dict) or not isinstance(item.get("id"), (str, int)):
        return None
    kind, state = item.get("type"), _outcome(item.get("status"), item.get("exit_code"))
    if row.get("type") == "item.started":
        state = "running"
    if kind == "command_execution" and isinstance(item.get("command"), str):
        return str(item["id"]), {"kind": "command", "text": _line(item["command"]), "status": state}
    if kind == "file_change":
        changes = [c for c in item.get("changes") or [] if isinstance(c, dict) and isinstance(c.get("path"), str)]
        if not changes:
            return None
        paths = [_relative(c["path"], workspace) for c in changes]
        verbs = [FILE_VERBS.get(str(c.get("kind")), "edited") for c in changes]
        return str(item["id"]), {**_edit(paths, verbs), "status": state}
    if kind == "web_search":
        found = _describe("websearch", {"query": item.get("query")}, workspace)
        if found is not None:
            return str(item["id"]), {"kind": "tool", "text": found[0], "summary": found[1], "status": state}
    if kind in TOOL_ITEMS:
        name = item.get("name") or item.get("tool") or item.get("query")
        if kind in ("tool_call", "function_call") and isinstance(name, str):
            # Claude Code reports its built-in tools by their own names (Read, Grep, Task).
            found = _describe(name.lower(), _arguments(item.get("arguments")), workspace)
            if found is not None:
                return str(item["id"]), {"kind": "tool", "text": found[0], "summary": found[1], "status": state}
        text = _line(name or TOOL_ITEMS[kind])
        return str(item["id"]), {"kind": "tool", "text": text, "summary": f"Used {text}", "status": state}
    return None


def part_event(row: dict, workspace) -> tuple[str, dict] | None:
    """An OpenCode or Kilo ``tool_use`` row as an event."""
    part = row.get("part")
    if row.get("type") != "tool_use" or not isinstance(part, dict) or not isinstance(part.get("id"), str):
        return None
    tool = str(part.get("tool") or "").lower()
    state: dict = part["state"] if isinstance(part.get("state"), dict) else {}
    given: dict = state["input"] if isinstance(state.get("input"), dict) else {}
    outcome = _outcome(state.get("status"))
    if tool in SHELL_TOOLS and isinstance(given.get("command"), str):
        return part["id"], {"kind": "command", "text": _line(given["command"]), "status": outcome}
    if tool in EDIT_TOOLS:
        path = given.get("filePath") or given.get("file_path") or given.get("path")
        if isinstance(path, str) and path:
            return part["id"], {
                **_edit([_relative(path, workspace)], ["added" if tool == "write" else "edited"]),
                "status": outcome,
            }
    if not tool:
        return None
    found = _describe(tool, given, workspace)
    if found is not None:
        return part["id"], {"kind": "tool", "text": found[0], "summary": found[1], "status": outcome}
    target = next((given[key] for key in TARGET_KEYS if isinstance(given.get(key), str) and given[key]), "")
    text = _line(f"{part.get('tool')} {_relative(target, workspace)}" if target else part.get("tool"))
    return part["id"], {"kind": "tool", "text": text, "summary": _line(f"Used {part.get('tool')}"), "status": outcome}


def rows(path: Path) -> list[dict]:
    """The JSON object rows in the tail of an event file, oldest first; an unreadable file has none."""
    try:
        with path.open("rb") as source:
            size = source.seek(0, 2)
            source.seek(max(0, size - READ_BYTES))
            raw = source.read(READ_BYTES).decode("utf-8", errors="replace").splitlines()
        lines = raw[1:] if size > READ_BYTES else raw  # the first line of a tail may be cut
    except OSError:
        return []
    found = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            found.append(row)
    return found


def read(path: Path, workspace=None) -> list[dict]:
    """The newest events in an attempt's event file, oldest first; a repeated item is one event."""
    events: dict[str, dict] = {}
    for row in rows(path):
        found = item_event(row, workspace) if isinstance(row.get("item"), dict) else part_event(row, workspace)
        if found is None:
            continue
        key, event = found
        events[f"{event['kind']}:{key}"] = event  # a later row for the same item replaces its state
    return list(events.values())[-LIMIT:]


def events_file(state: RunState, public, run_dir, *, finished=False, stopped=False) -> Path | None:
    """The event file of the attempt supervised and working right now, inside the run directory; else None."""
    if public.get("status") != "RUNNING" or (public.get("needs") or {}).get("kind") != "continue":
        return None
    active = state.get("active_stage") or {}
    if not isinstance(active, dict) or not active.get("events") or finished or stopped or run_dir is None:
        return None
    if (public.get("liveness") or {}).get("kind") != "supervised":
        return None  # activity that cannot be confirmed is not shown
    root = Path(run_dir).resolve()
    path = Path(active["events"]).resolve()
    return path if path.is_relative_to(root) else None


def project(state: RunState, public, run_dir, *, finished=False, stopped=False) -> list[dict]:
    """The running attempt's recent events, or none unless it is supervised and working right now."""
    path = events_file(state, public, run_dir, finished=finished, stopped=stopped)
    if path is None:
        return []
    return read(path, state.get("workspace") or public.get("workspace"))


def last_output(state: RunState, public, run_dir, *, finished=False, stopped=False) -> str | None:
    """When the running attempt last wrote to its event file (``chat.last_output_at``), as a UTC timestamp.

    A stalled stage and a busy one look the same until one fails; this is the one fact that tells
    them apart, under the same guards as ``project``: only the attempt supervised and working now,
    only a file inside the run directory. It is the file's modification time, so it says when the
    provider last emitted anything, not what; it is never proof of progress or a liveness decision."""
    path = events_file(state, public, run_dir, finished=finished, stopped=stopped)
    if path is None:
        return None
    try:
        modified = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(modified, UTC).isoformat(timespec="seconds")
