"""Count-only native tool diagnostics; identities and arguments stay in ephemeral memory.

This is observation, never completion or retry authority. Only known event shapes
are interpreted; neither prose nor command/output text determines an outcome.
"""

from __future__ import annotations

import json
import math

MAX_CALLS = 4096
MAX_ARGUMENT_BYTES = 65536
MAX_ARGUMENT_TOTAL = 1024 * 1024
MAX_EVENTS = 65536
VERSION = 1  # Bump whenever saved diagnostic interpretation changes.
FAMILIES = ("command", "read", "search", "edit", "agent", "other")
OUTCOMES = ("succeeded", "failed", "unknown")
ERROR_CLASSES = ("nonzero_exit", "tool_error")
ISSUES = (
    "event_stream_incomplete",
    "unsupported_stream",
    "missing_call_identity",
    "conflicting_call",
    "unknown_outcome",
    "arguments_unavailable",
    "observation_limit",
    "active_attempt",
    "saved_projection_unavailable",
    "conflicting_snapshots",
)
SCOPE = (
    "Observed tool calls in one stage attempt; repeats compare exact tool arguments "
    "and do not imply wasted work; no request correlation"
)
# Exact names only. Unknown names remain in the fixed other family.
TOOL_FAMILIES = {
    **dict.fromkeys(("bash", "shell"), "command"),
    **dict.fromkeys(("read",), "read"),
    **dict.fromkeys(("glob", "grep", "search", "websearch", "webfetch"), "search"),
    **dict.fromkeys(("edit", "write", "patch", "multiedit", "apply_patch", "applypatch"), "edit"),
    **dict.fromkeys(("task",), "agent"),
}
# Codex items that are not tool calls: messages, the model's plan and non-fatal notices.
NON_TOOL_ITEMS = ("agent_message", "reasoning", "plan", "todo_list", "error")
# AutoCode's own rows in a native stream; they describe no tool call.
NON_TOOL_ROWS = ("text", "reasoning", "autocode_progress", "autocode_compaction")
GENERIC_ITEMS = ("mcp_tool_call", "tool_call", "function_call", "collab_tool_call", "collab_agent_tool_call")
ITEM_FAMILIES = {
    "command_execution": "command",
    "file_change": "edit",
    "web_search": "search",
    "collab_tool_call": "agent",  # Codex exec's current name
    "collab_agent_tool_call": "agent",
    "mcp_tool_call": "other",
    "tool_call": "other",
    "function_call": "other",
}


def unavailable(issue="saved_projection_unavailable"):
    return {
        "version": VERSION,
        "basis": "unavailable",
        "observed_calls": 0,
        "families": dict.fromkeys(FAMILIES, 0),
        "outcomes": dict.fromkeys(OUTCOMES, 0),
        "error_classes": dict.fromkeys(ERROR_CLASSES, 0),
        "exact_argument_repeats": 0,
        "comparable_calls": 0,
        "complete": False,
        "issues": [issue],
        "scope": SCOPE,
    }


def is_current(value):
    """Only this algorithm's exact version may reuse an unchanged saved stream."""
    return isinstance(value, dict) and type(value.get("version")) is int and value["version"] == VERSION


def projection(value, *, active=False):
    """Whitelist saved diagnostics too: no caller-supplied text or new keys escape."""
    fallback = unavailable()
    if not is_current(value) or value.get("basis") not in ("native_events", "unavailable"):
        return fallback
    result = {**fallback, "basis": value["basis"]}
    for field, keys in (("families", FAMILIES), ("outcomes", OUTCOMES), ("error_classes", ERROR_CLASSES)):
        counts = value.get(field)
        if not isinstance(counts, dict) or any(type(counts.get(key)) is not int or counts[key] < 0 for key in keys):
            return fallback
        result[field] = {key: counts[key] for key in keys}
    for field in ("observed_calls", "exact_argument_repeats", "comparable_calls"):
        if type(value.get(field)) is not int or not 0 <= value[field] <= MAX_CALLS:
            return fallback
        result[field] = value[field]
    issues = value.get("issues")
    if not isinstance(issues, list) or any(not isinstance(issue, str) or issue not in ISSUES for issue in issues):
        return fallback
    if (
        sum(result["families"].values()) != result["observed_calls"]
        or sum(result["outcomes"].values()) != result["observed_calls"]
        or sum(result["error_classes"].values()) > result["outcomes"]["failed"]
        or not result["exact_argument_repeats"] <= result["comparable_calls"] <= result["observed_calls"]
    ):
        return fallback
    issues = set(issues)
    if result["outcomes"]["unknown"]:
        issues.add("unknown_outcome")
    if result["comparable_calls"] < result["observed_calls"]:
        issues.add("arguments_unavailable")
    result["issues"] = [issue for issue in ISSUES if issue in issues or active and issue == "active_attempt"]
    result["complete"] = value.get("complete") is True and result["basis"] == "native_events" and not result["issues"]
    return result


def _identity(value):
    return value if isinstance(value, str) and 0 < len(value) <= 1024 else None


def _command_exit(tool_state):
    """Match supported OpenCode precedence without importing provider machinery."""
    metadata = tool_state.get("metadata")
    if not isinstance(metadata, dict):
        return None
    if "exit" in metadata:
        code = metadata["exit"]
    else:
        nested = metadata.get("metadata")
        code = nested.get("exit") if isinstance(nested, dict) else None
    return code if type(code) is int else None


def _outcome(status, exit_code, *, command=False):
    if status not in ("completed", "error", "failed", "declined"):
        return "unknown", None
    if type(exit_code) is int and exit_code != 0:
        return "failed", "nonzero_exit"
    if status in ("error", "failed", "declined"):
        return "failed", "tool_error"
    if command and type(exit_code) is not int:
        return "unknown", None
    if status == "completed":
        return "succeeded", None
    return "unknown", None


def _bounded_arguments(value):
    """Upper-bound compact ASCII JSON bytes before the serializer allocates them."""
    nodes, encoded_bytes = 0, 0

    def add(size):
        nonlocal encoded_bytes
        encoded_bytes += size
        return encoded_bytes <= MAX_ARGUMENT_BYTES

    def visit(item, depth):
        nonlocal nodes
        nodes += 1
        if nodes > 8192 or depth > 32:
            return False
        if isinstance(item, str):
            if len(item) > MAX_ARGUMENT_BYTES or not add(2):
                return False
            for char in item:
                code = ord(char)
                size = 2 if char in '"\\\b\f\n\r\t' else 1 if 32 <= code <= 126 else 6 if code <= 65535 else 12
                if not add(size):
                    return False
            return True
        if item is None or type(item) is bool:
            return add(4 if item is None or item is True else 5)
        if type(item) is int:
            # 30103/100000 is a strict upper bound on log10(2). Never convert
            # an arbitrarily large integer to decimal just to measure it.
            return add(item.bit_length() * 30103 // 100000 + 1 + int(item < 0))
        if type(item) is float:
            return math.isfinite(item) and add(32)
        if isinstance(item, dict):
            return add(2 + max(0, len(item) - 1) + len(item)) and all(
                isinstance(key, str) and visit(key, depth + 1) and visit(child, depth + 1)
                for key, child in item.items()
            )
        if isinstance(item, (list, tuple)):
            return add(2 + max(0, len(item) - 1)) and all(visit(child, depth + 1) for child in item)
        return False

    return visit(value, 0)


def summarize(rows, *, incomplete=False):
    """Aggregate known OpenCode/Kilo parts and Codex-style items, with strict bounds.

    OpenCode part IDs are scoped by session. Codex item IDs require their thread
    opener. Full structured inputs alone support argument comparison; a rendered
    command or file-change summary is not the original argument set.
    """
    calls, issues, signatures, conflicts, boundaries = {}, set(), set(), set(), set()
    opened, terminal, supported, thread, native_session = False, False, False, None, None
    argument_bytes = 0
    if incomplete:
        issues.add("event_stream_incomplete")
    for index, row in enumerate(rows):
        if index >= MAX_EVENTS:
            issues.add("observation_limit")
            break
        if not isinstance(row, dict):
            issues.add("event_stream_incomplete")
            continue
        kind = row.get("type")
        if kind in ("step_start", "step_finish"):
            part = row.get("part")
            marker = (
                kind,
                _identity(row.get("sessionID")),
                _identity(part.get("id")) if isinstance(part, dict) else None,
            )
            if not all(marker):
                issues.add("event_stream_incomplete")
            elif marker in boundaries:
                continue
            elif len(boundaries) < MAX_CALLS:
                boundaries.add(marker)
            else:
                issues.add("observation_limit")
        if kind in ("step_start", "thread.started"):
            supported, opened, terminal = True, True, False
            if kind == "thread.started":
                current_thread = _identity(row.get("thread_id"))
                if not current_thread or thread is not None and thread != current_thread:
                    issues.add("event_stream_incomplete")
                thread = current_thread
            else:
                session = _identity(row.get("sessionID"))
                if not session or native_session is not None and native_session != session:
                    issues.add("event_stream_incomplete")
                native_session = session
            continue
        if kind in ("step_finish", "turn.completed"):
            supported = True
            part = row.get("part")
            if kind == "step_finish" and (not native_session or row.get("sessionID") != native_session):
                issues.add("event_stream_incomplete")
            terminal = kind == "turn.completed" or isinstance(part, dict) and part.get("reason") == "stop"
            continue
        if kind == "turn.started":
            terminal = False
            continue
        if kind in ("error", "turn.failed", "usage.partial"):
            issues.add("event_stream_incomplete")
            terminal = False
            continue
        if kind == "tool_use":
            supported, terminal = True, False
            part = row.get("part")
            part = part if isinstance(part, dict) else {}
            state = part.get("state")
            state = state if isinstance(state, dict) else {}
            session = _identity(row.get("sessionID"))
            if session != native_session:
                issues.add("event_stream_incomplete")
            identity = _identity(part.get("id"))
            if part.get("sessionID", session) != session:
                identity = None
            name = part.get("tool")
            name = name if isinstance(name, str) and 0 < len(name) <= 1024 else None
            family = TOOL_FAMILIES.get(name, "other")
            raw = state.get("input")
            outcome, error = _outcome(
                state.get("status"),
                _command_exit(state),
                command=family == "command",
            )
            key = ("part", session, identity)
        elif kind in ("item.started", "item.updated", "item.completed"):
            item = row.get("item")
            item = item if isinstance(item, dict) else {}
            item_kind = item.get("type")
            if item_kind in NON_TOOL_ITEMS:
                continue
            if not isinstance(item_kind, str) or item_kind not in ITEM_FAMILIES:
                issues.add("unsupported_stream")
                continue
            supported, terminal = True, False
            session, identity = thread, _identity(item.get("id"))
            name = (item_kind, None, None)
            family = ITEM_FAMILIES[item_kind]
            if item_kind in GENERIC_ITEMS:
                tool_name = _identity(item.get("tool") if "tool" in item else item.get("name"))
                server = _identity(item.get("server"))
                server_required = item_kind == "mcp_tool_call" or "server" in item
                if not tool_name or not tool_name.strip() or server_required and (not server or not server.strip()):
                    # A generic family cannot identify the operation or MCP
                    # server. Keep its counts, but never compare anonymous tools.
                    name = None
                else:
                    name = (item_kind, server, tool_name)
                mapped = TOOL_FAMILIES.get(tool_name.lower()) if tool_name else None
                if item_kind in ("tool_call", "function_call") and mapped not in (None, "command"):
                    # Claude Code reports its built-in tools by their own names (Read, Grep, Task).
                    # An MCP server's tool of the same name is not the built-in, so it stays other,
                    # and a generic call has no exit code to make it a command.
                    family = mapped
            raw = item.get("arguments")
            status = item.get("status")
            if kind == "item.started":
                status = "running"
            if status is None and kind == "item.completed" and type(item.get("exit_code")) is int:
                status = "completed"
            if status is None and kind == "item.completed" and item_kind == "web_search":
                status = "completed"  # Codex completes a web search item when its results return.
            if family == "command" and type(item.get("exit_code")) is not int and item.get("exit_code_unknown"):
                # The Claude translator says it cannot vouch for this exit (backgrounded, interrupted).
                status = "unknown"
            outcome, error = _outcome(status, item.get("exit_code"), command=family == "command")
            key = ("item", session, identity)
        else:
            if kind not in NON_TOOL_ROWS:
                issues.add("unsupported_stream")
            continue
        if not session or not identity:
            issues.add("missing_call_identity")
            continue  # Never guess whether anonymous lifecycle rows are one call.
        if key not in calls and len(calls) >= MAX_CALLS:
            issues.add("observation_limit")
            continue
        signature = None
        previous = calls.get(key)
        if key in conflicts:
            continue
        if name is not None and outcome != "unknown" and isinstance(raw, dict):
            try:
                if not _bounded_arguments([name, raw]):
                    issues.add("observation_limit")
                    raise ValueError("argument observation bound")
                encoded = json.dumps(
                    [name, raw], sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
                )
                size = len(encoded)
                retained = argument_bytes - (len(previous[3]) if previous and previous[3] is not None else 0)
                if size <= MAX_ARGUMENT_BYTES and retained + size <= MAX_ARGUMENT_TOTAL:
                    signature = encoded
                else:
                    issues.add("observation_limit")
            except (ValueError, TypeError, RecursionError):
                pass
        current = (family, outcome, error, signature)
        if previous:
            if (
                previous[0] != family
                or previous[1] != "unknown"
                and outcome != "unknown"
                and previous[1:3] != current[1:3]
                or previous[3] is not None
                and signature is not None
                and previous[3] != signature
            ):
                issues.add("conflicting_call")
                conflicts.add(key)
                if previous[3] is not None:
                    argument_bytes -= len(previous[3])
                calls[key] = ("other", "unknown", None, None)
                continue
            current = (
                family,
                outcome if outcome != "unknown" else previous[1],
                error if outcome != "unknown" else previous[2],
                signature if signature is not None else previous[3],
            )
            if previous[3] is not None:
                argument_bytes -= len(previous[3])
        calls[key] = current
        if current[3] is not None:
            argument_bytes += len(current[3])
    result = unavailable()
    result.update(basis="native_events" if supported else "unavailable", issues=[])
    for family, outcome, error, signature in calls.values():
        result["families"][family] += 1
        result["outcomes"][outcome] += 1
        if error:
            result["error_classes"][error] += 1
        if outcome == "unknown":
            issues.add("unknown_outcome")
        if signature is None:
            issues.add("arguments_unavailable")
        else:
            result["comparable_calls"] += 1
            result["exact_argument_repeats"] += int(signature in signatures)
            signatures.add(signature)
    if not supported:
        issues.add("unsupported_stream")
    if not opened or not terminal:
        issues.add("event_stream_incomplete")
    result.update(observed_calls=len(calls), complete=not issues, issues=[issue for issue in ISSUES if issue in issues])
    return result
