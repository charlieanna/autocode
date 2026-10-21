"""Chat-facing live activity of the running attempt, from its own provider event file.

``view["chat"]["activity"]`` lists what the running stage is doing right now: the commands it runs,
the files it edits and the other tools it calls, newest last. It is display only, like
``chat.progress``: never proof that a command passed, a change is right or a requirement is met, and
never authority to approve or retry. Reasoning, model prose and tool output are never copied; a
command is shown as its first line, bounded. Only the tail of the attempt's event file is read, and
only a file inside the run directory. It is empty unless the attempt is the one supervised now.

Three event shapes are read, the ones the providers write: Codex-style items (also what the built-in
Claude provider writes), and OpenCode or Kilo ``tool_use`` parts. A row of any other shape is ignored.
"""

from __future__ import annotations

import json
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
    if kind in TOOL_ITEMS:
        name = item.get("name") or item.get("tool") or item.get("query")
        return str(item["id"]), {"kind": "tool", "text": _line(name or TOOL_ITEMS[kind]), "status": state}
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
    target = next((given[key] for key in TARGET_KEYS if isinstance(given.get(key), str) and given[key]), "")
    text = _line(f"{part.get('tool')} {_relative(target, workspace)}" if target else part.get("tool"))
    return part["id"], {"kind": "tool", "text": text, "status": outcome}


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


def project(state: RunState, public, run_dir, *, finished=False, stopped=False) -> list[dict]:
    """The running attempt's recent events, or none unless it is supervised and working right now."""
    if public.get("status") != "RUNNING" or (public.get("needs") or {}).get("kind") != "continue":
        return []
    active = state.get("active_stage") or {}
    if not isinstance(active, dict) or not active.get("events") or finished or stopped or run_dir is None:
        return []
    if (public.get("liveness") or {}).get("kind") != "supervised":
        return []  # activity that cannot be confirmed is not shown
    root = Path(run_dir).resolve()
    path = Path(active["events"]).resolve()
    if not path.is_relative_to(root):
        return []
    return read(path, state.get("workspace") or public.get("workspace"))
