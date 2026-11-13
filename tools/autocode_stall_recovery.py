"""Carry what a stopped attempt already executed into its recovery context (#3309).

A provider stream stall archives a Tester attempt whose tool calls did run: the
commands, reads and their outcomes sit in the saved event log. The recovery
instruction already says to inspect retained partial work, but the relaunch got
no concrete record of it, so the model either re-read a multi-thousand-line log
or redid a 40-minute stage from nothing. ``executed`` lists the archived
attempt's completed tool calls, bounded and redacted, as context across both
OpenCode/Kilo ``tool_use`` parts and Codex/Claude ``item.completed`` items.
Every row is unverified and none is citable evidence: the new report still
binds its own executed checks, and this packet grants no verification or
completion authority.

Leaf module: reads one event log and depends only on ``autocode_util``.
"""

from __future__ import annotations

import json
from itertools import islice
from typing import Any

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util

MAX_CALLS = 40
MAX_DETAIL = 200
MAX_OUTPUT = 200
MAX_LINES = 100_000
REDACT_MARGIN = 1024
ITEM_TOOLS = frozenset(
    {
        "command_execution",
        "file_change",
        "tool_call",
        "function_call",
        "mcp_tool_call",
        "web_search",
        "collab_agent_tool_call",
    }
)

INSTRUCTION = (
    "The executed_work packet lists the archived attempt's completed tool calls and their "
    "outputs, unverified: use it to see what was already run and focus this attempt instead "
    "of re-exploring. It is context, never citable evidence; this report still binds its own "
    "executed checks."
)


def _rows(path: object) -> list[dict[str, Any]] | None:
    if not isinstance(path, str) or not path:
        return None
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = list(islice(handle, MAX_LINES))
    except OSError:
        return None
    rows: list[dict[str, Any]] = []
    for line in lines:  # a malformed or scalar line is skipped; the log as a whole still summarises
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


def _head(text: object, limit: int) -> str:
    raw = str(text or "").strip()
    value = util.redact(raw[: limit + REDACT_MARGIN])
    return value[:limit] + ("…" if len(raw) > limit or len(value) > limit else "")


def _tail(value: object) -> str:
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if not raw:
        return ""
    text = util.redact(raw[-(MAX_OUTPUT + REDACT_MARGIN) :])
    return ("…" + text[-MAX_OUTPUT:]) if len(raw) > MAX_OUTPUT or len(text) > MAX_OUTPUT else text


def _command_exit(part_state: dict[str, Any]) -> int | None:
    """Integer exit code from OpenCode 1.x metadata or OpenCode 2's nested metadata."""
    metadata = part_state.get("metadata")
    if not isinstance(metadata, dict):
        return None
    if "exit" in metadata:
        code = metadata["exit"]
        return code if type(code) is int else None
    nested = metadata.get("metadata")
    if isinstance(nested, dict) and type(nested.get("exit")) is int:
        return nested["exit"]
    return None


def _detail(tool: object, value: object) -> str:
    if isinstance(value, str) and value.lstrip().startswith("{"):
        try:
            parsed = json.loads(value)
        except ValueError:
            parsed = None
        if isinstance(parsed, dict):
            value = parsed
    if isinstance(value, dict):
        if isinstance(value.get("command"), str) and value["command"].strip():
            return _head(value["command"], MAX_DETAIL)
        if isinstance(value.get("filePath"), str):
            where = f"{value.get('offset', '')}:{value.get('limit', '')}"
            return _head(f"{value['filePath']} @{where}", MAX_DETAIL)
        path = (
            value.get("file_path")
            if isinstance(value.get("file_path"), str)
            else value.get("path")
            if isinstance(value.get("path"), str)
            else None
        )
        if path:
            if "offset" in value or "limit" in value:
                return _head(f"{path} @{value.get('offset', '')}:{value.get('limit', '')}", MAX_DETAIL)
            return _head(path, MAX_DETAIL)
    return _head(json.dumps(value, default=str) if value is not None else tool, MAX_DETAIL)


def _part_call(row: dict[str, Any]) -> tuple[tuple[str, object, str] | None, dict[str, Any]] | None:
    part = row.get("part")
    if row.get("type") != "tool_use" or not isinstance(part, dict):
        return None
    state = part.get("state")
    if not isinstance(state, dict):
        return None
    raw_status = state.get("status")
    if raw_status not in ("completed", "error", "failed", "declined"):
        return None
    code = _command_exit(state)
    status = "failed" if raw_status == "completed" and code is not None and code != 0 else str(raw_status)
    call: dict[str, Any] = {
        "tool": part.get("tool"),
        "detail": _detail(part.get("tool"), state.get("input")),
        "status": status,
        "output_tail": _tail(state.get("output")),
    }
    if code is not None:
        call["exit_code"] = code
    part_id = part.get("id")
    key = ("part", row.get("sessionID"), part_id) if isinstance(part_id, str) and part_id else None
    return key, call


def _item_call(row: dict[str, Any]) -> tuple[tuple[str, object, str] | None, dict[str, Any]] | None:
    item = row.get("item")
    if row.get("type") != "item.completed" or not isinstance(item, dict):
        return None
    kind = item.get("type")
    if not isinstance(kind, str) or kind not in ITEM_TOOLS:
        return None
    raw_status = item.get("status")
    if raw_status in ("in_progress", "pending", "running"):
        return None
    raw_code = item.get("exit_code")
    code = raw_code if type(raw_code) is int else None
    failed = raw_status in ("failed", "declined", "error") or (code is not None and code != 0)
    status = "failed" if failed else "completed"
    if kind == "command_execution":
        tool: object = "command_execution"
        detail = _head(item.get("command") or kind, MAX_DETAIL)
    elif kind == "file_change":
        tool = "file_change"
        paths = [
            change["path"]
            for change in (item.get("changes") or [])
            if isinstance(change, dict) and isinstance(change.get("path"), str) and change["path"]
        ]
        detail = _head(", ".join(paths) if paths else kind, MAX_DETAIL)
    elif kind == "web_search":
        tool = "web_search"
        detail = _head(item.get("query") or kind, MAX_DETAIL)
    else:
        tool = (
            item["name"]
            if isinstance(item.get("name"), str) and item["name"]
            else item["tool"]
            if isinstance(item.get("tool"), str) and item["tool"]
            else kind
        )
        given = (
            item["display_input"]
            if isinstance(item.get("display_input"), dict)
            else item.get("arguments")
            if "arguments" in item
            else item.get("input")
        )
        detail = _detail(tool, given)
    raw_output = item.get("aggregated_output") if isinstance(item.get("aggregated_output"), str) else item.get("output")
    call: dict[str, Any] = {
        "tool": tool,
        "detail": detail,
        "status": status,
        "output_tail": _tail(raw_output),
    }
    if code is not None:
        call["exit_code"] = code
    item_id = item.get("id")
    key = (
        ("item", None, str(item_id))
        if isinstance(item_id, (str, int)) and not isinstance(item_id, bool) and str(item_id)
        else None
    )
    return key, call


def executed(record: object) -> dict[str, Any]:
    """The archived attempt's executed tool calls, bounded and redacted; never evidence."""
    rows = _rows(record.get("events") if isinstance(record, dict) else None)
    if rows is None:
        return {"available": False, "reason": "The archived event log is missing or unreadable"}
    seen: dict[tuple[str, object, str], dict[str, Any]] = {}
    more = False
    for index, row in enumerate(rows):
        parsed = _part_call(row) or _item_call(row)
        if parsed is None:
            continue
        key, call = parsed
        slot = key if key is not None else ("anon", None, str(index))
        if slot not in seen and len(seen) == MAX_CALLS:
            more = True
            continue
        seen[slot] = call
    calls = list(seen.values())
    if not calls:
        return {"available": False, "reason": "The archived event log holds no completed tool calls"}
    return {
        "available": True,
        "verification": "unverified",
        "calls": calls,
        "truncated": more,
        "instruction": INSTRUCTION,
    }
