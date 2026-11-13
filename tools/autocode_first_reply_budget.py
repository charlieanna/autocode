"""Bounded per-pass reply budget for ``requirements_gather`` (#3345, Proposal 1).

Keeps the first user interaction from stalling inside long codebase reads: a single
``requirements_gather`` pass gets a bounded wall-time and completed tool-call allowance
enforced by the activity monitor, and a budget stop (not a person's Stop now or
intervention) transitions once to a report-only finish synthesized from collected
evidence. Lower-layer module: imports stdlib only.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .autocode_run_state import RunState

STAGE = "requirements_gather"
DEFAULT_MAX_SECONDS = 360
DEFAULT_MAX_TOOL_CALLS = 12
MAX_PARTIAL_BYTES = 32 * 1024
MAX_NOTES = 8
MAX_COMMANDS = 16
MAX_PATHS = 24
MAX_ITEM_CHARS = 800

_CODEX_TOOL_TYPES = frozenset(
    {
        "command_execution",
        "mcp_tool_call",
        "web_search",
        "file_change",
        "collab_agent_tool_call",
        "tool_call",
        "function_call",
    }
)
_PATH_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_.:/-])((?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+(?:\.[A-Za-z0-9_]+)?)(?=$|[\s'\":,);])"
)


def applies(state: RunState, stage: str, *, report_only: bool = False) -> bool:
    """True for a live (non-repair) ``requirements_gather`` stage."""
    del state
    return stage == STAGE and not report_only


def limits(state: RunState) -> tuple[int, int]:
    """Return ``(max_seconds, max_tool_calls)`` for a ``requirements_gather`` pass."""
    cfg = (state.get("settings") or {}).get("limits") or {}
    raw_secs = cfg.get("first_reply_timeout_seconds")
    max_secs = int(raw_secs) if isinstance(raw_secs, (int, float)) and raw_secs > 0 else DEFAULT_MAX_SECONDS
    stage_secs = cfg.get("stage_timeout_seconds")
    if isinstance(stage_secs, (int, float)) and stage_secs > 0:
        max_secs = min(max_secs, int(stage_secs))
    raw_calls = cfg.get("first_reply_max_tool_calls")
    max_calls = int(raw_calls) if isinstance(raw_calls, int) and raw_calls > 0 else DEFAULT_MAX_TOOL_CALLS
    return max_secs, max_calls


class ProgressBudget:
    """ActivityMonitor progress hook counting unique completed provider tool calls."""

    def __init__(self, max_tool_calls: int) -> None:
        self.max_tool_calls = max(1, int(max_tool_calls))
        self._lock = threading.Lock()
        self._completed: set[str] = set()

    @property
    def completed_count(self) -> int:
        with self._lock:
            return len(self._completed)

    def observe(self, row: dict[str, Any]) -> None:
        if not isinstance(row, dict):
            return
        event_type = row.get("type")
        key: str | None = None
        item = row.get("item")
        if isinstance(item, dict) and event_type in ("item.completed", "item.updated"):
            ident = item.get("id")
            if (
                isinstance(ident, str)
                and ident
                and item.get("type") in _CODEX_TOOL_TYPES
                and (event_type == "item.completed" or item.get("status") in ("completed", "failed"))
            ):
                key = "codex:" + ident
        else:
            part = row.get("part")
            if isinstance(part, dict) and event_type == "tool_use" and isinstance(part.get("tool"), str):
                ident = part.get("id") or part.get("callID")
                phase = (part.get("state") or {}).get("status") if isinstance(part.get("state"), dict) else None
                if isinstance(ident, str) and ident and phase in ("completed", "error"):
                    key = "opencode:" + ident
        if key is not None:
            with self._lock:
                self._completed.add(key)

    def reason(self) -> str | None:
        with self._lock:
            if len(self._completed) >= self.max_tool_calls:
                return f"Requirements pass reached its {self.max_tool_calls}-tool-call reply budget; finish from collected evidence"
        return None


def handoff_spec(state: RunState) -> dict[str, Any]:
    """Prompt metadata advertising the per-pass exploration budget for ``requirements_gather``."""
    max_seconds, max_tool_calls = limits(state)
    return {
        "max_seconds": max_seconds,
        "max_tool_calls": max_tool_calls,
        "instruction": (
            f"This Requirements pass has a per-pass reply budget ({max_seconds}s wall time and "
            f"{max_tool_calls} completed tool calls) so the user receives blocking questions or a "
            "grounded handoff promptly. Spend at most a few targeted file reads or searches to resolve "
            "genuine ambiguity; leave exhaustive file-by-file planning exploration to `draft_plan`."
        ),
    }


def is_budget_stop(record: dict[str, Any]) -> bool:
    """True when a live ``requirements_gather`` attempt stopped due to its reply budget (never person/idle)."""
    return bool(
        isinstance(record.get("first_reply_budget_active"), dict)
        and not record.get("report_only")
        and (record.get("original_stage") or record.get("stage")) == STAGE
        and record.get("timed_out")
        and record.get("timeout_kind") in ("progress", "stage")
    )


def _trim_text(value: str, limit: int = MAX_ITEM_CHARS) -> str:
    text = value.strip()
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _paths_from_text(text: str) -> list[str]:
    paths: list[str] = []
    for match in _PATH_TOKEN.finditer(text):
        candidate = match.group(1).lstrip("./")
        if candidate and "://" not in text[max(0, match.start() - 6) : match.start() + 3]:
            paths.append(candidate)
    return paths


def extract_partial_evidence(events_path: Path | str, record: dict[str, Any]) -> dict[str, Any]:
    """Extract a bounded (<= 32 KiB) summary of completed exploration from stopped stage events."""
    path = Path(events_path)
    commands: list[dict[str, Any]] = []
    notes: list[str] = []
    seen_paths: list[str] = []
    completed_ids: set[str] = set()
    try:
        raw_lines = path.read_text(errors="replace").splitlines()
    except OSError:
        raw_lines = []
    for line in raw_lines:
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            row = json.loads(stripped)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        event_type = row.get("type")
        item = row.get("item")
        if isinstance(item, dict) and event_type in ("item.completed", "item.updated"):
            kind = item.get("type")
            ident = item.get("id")
            if (
                kind in _CODEX_TOOL_TYPES
                and isinstance(ident, str)
                and ident
                and (event_type == "item.completed" or item.get("status") in ("completed", "failed"))
            ):
                completed_ids.add("codex:" + ident)
                cmd = item.get("command") or item.get("title") or kind
                out = item.get("aggregated_output") or item.get("output") or ""
                for found in _paths_from_text(f"{cmd}\n{out}"):
                    if found not in seen_paths and len(seen_paths) < MAX_PATHS:
                        seen_paths.append(found)
                if len(commands) < MAX_COMMANDS:
                    commands.append(
                        {
                            "command": _trim_text(str(cmd), 240),
                            "exit_code": item.get("exit_code"),
                            "output_excerpt": _trim_text(str(out), 400),
                        }
                    )
            elif kind in ("agent_message", "reasoning") and isinstance(item.get("text"), str) and item["text"].strip():
                if len(notes) < MAX_NOTES:
                    notes.append(_trim_text(item["text"]))
        elif isinstance(row.get("part"), dict):
            part = row["part"]
            ident = part.get("id") or part.get("callID")
            if event_type == "tool_use" and isinstance(ident, str) and ident:
                state_obj = part.get("state") or {}
                if isinstance(state_obj, dict) and state_obj.get("status") in ("completed", "error"):
                    completed_ids.add("opencode:" + ident)
                    tool_name = str(part.get("tool") or "tool")
                    tool_input = state_obj.get("input") or {}
                    cmd = tool_input.get("command") if isinstance(tool_input, dict) else ""
                    out = state_obj.get("output") or ""
                    for found in _paths_from_text(f"{cmd or tool_input}\n{out}"):
                        if found not in seen_paths and len(seen_paths) < MAX_PATHS:
                            seen_paths.append(found)
                    if len(commands) < MAX_COMMANDS:
                        commands.append(
                            {
                                "command": _trim_text(str(cmd or tool_name), 240),
                                "status": state_obj.get("status"),
                                "output_excerpt": _trim_text(str(out), 400),
                            }
                        )
            elif event_type == "text" and isinstance(part.get("text"), str) and part["text"].strip():
                if len(notes) < MAX_NOTES:
                    notes.append(_trim_text(part["text"]))
    payload: dict[str, Any] = {
        "complete_report": False,
        "stop_kind": record.get("timeout_kind"),
        "stop_reason": record.get("timeout_reason"),
        "budget": dict(record.get("first_reply_budget_active") or {}),
        "completed_tool_calls": len(completed_ids),
        "inspected_paths": seen_paths,
        "inspected_commands": commands,
        "assistant_notes": notes,
    }
    while len(json.dumps(payload, separators=(",", ":")).encode("utf-8")) > MAX_PARTIAL_BYTES:
        if len(payload["inspected_commands"]) > 1:
            payload["inspected_commands"].pop()
        elif payload["assistant_notes"]:
            payload["assistant_notes"].pop()
        elif payload["inspected_paths"]:
            payload["inspected_paths"].pop()
        else:
            break
    return payload
