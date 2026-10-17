"""Requirements conversation policy and bounded assistant-text projection.

The existing task controller owns dispatch and sessions. This module supplies
the opening question and reads only the active stage's assistant reply, never
tool output or reasoning. A partial reply carries no planning authority.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

INTRO = "requirements-intro"
GREETING = "Hi! What would you like to work on today?"
STAGE = "requirements_gather"


def enabled(state: RunState):
    return bool((state.get("settings") or {}).get("spec_first") and not state.get("goal_contract"))


def opening(state: RunState):
    text = str(state.get("task", "")).strip().casefold().rstrip("!?. ")
    return bool(
        enabled(state)
        and text in {"hi", "hello", "hey", "hi there", "hello there", "good morning", "good afternoon", "good evening"}
        and not state.get("requirements_handoff")
        and not state.get("user_events")
        and not state.get("stages")
        and not (state.get("workflow") or {}).get("kind")
    )


def question():
    return {
        "id": INTRO,
        "question": GREETING,
        "why": "Understand your goal before planning or running any models.",
        "options": [],
        "proposed_default": "",
        "kind": "decision",
        "category": "requested_outcome",
        "delegable": False,
    }


def accepts(state: RunState, proposal):
    return bool(
        opening(state)
        and proposal.get("scope") == "clarification"
        and proposal.get("next_stage") == STAGE
        and proposal.get("origin") == {"stage": STAGE, "kind": "conversation_opening"}
        and proposal.get("questions") == [question()]
    )


def queue_opening(state: RunState, queue):
    if not opening(state):
        return False
    queue(
        state,
        "clarification",
        {"stage": STAGE, "kind": "conversation_opening"},
        questions=[question()],
        phase="DISCOVERING",
        next_stage=STAGE,
    )
    return True


def reply(state: RunState):
    report = (state.get("requirements_handoff") or {}).get("report") or {}
    text = report.get("assistant_reply")
    return text.strip() if isinstance(text, str) else ""


def retain_reply(state: RunState, event):
    """Keep the assistant side of a requirements exchange beside its saved reply."""
    if enabled(state) and event.get("actor") == "user_cli":
        spoken = reply(state) or "\n\n".join(q["question"] for q in state.get("pending_questions", []))
        if spoken:
            event["assistant_reply"] = spoken


def partial_string(text):
    """Decode only the JSON assistant_reply string, including a truncated suffix."""
    match = re.search(r'"assistant_reply"\s*:\s*"', text)
    if not match:
        return ""
    start = match.end()
    escaped = False
    end = len(text)
    for index in range(start, len(text)):
        char = text[index]
        if char == '"' and not escaped:
            end = index
            break
        escaped = char == "\\" and not escaped
    fragment = text[start:end]
    # A stream can end inside a backslash or a six-character Unicode escape.
    for trim in range(min(6, len(fragment)) + 1):
        try:
            return json.loads('"' + (fragment[:-trim] if trim else fragment) + '"')[:12000]
        except (ValueError, TypeError):
            continue
    return ""


def streaming_reply(state: RunState, run_dir):
    active = state.get("active_stage") or {}
    if not enabled(state) or active.get("stage") != STAGE or not run_dir or not active.get("events"):
        return ""
    root = Path(run_dir).resolve()
    path = Path(active["events"]).resolve()
    if not path.is_relative_to(root):
        return ""
    try:
        with path.open("rb") as source:
            source.seek(0, 2)
            source.seek(max(0, source.tell() - 262144))
            lines = source.read(262144).decode("utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    texts = {}
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        part = row.get("part") if isinstance(row.get("part"), dict) else {}
        if row.get("type") == "text" and isinstance(part.get("text"), str):
            texts[str(part.get("id", len(texts)))] = part["text"]
        item = row.get("item") if isinstance(row.get("item"), dict) else {}
        if row.get("type") in ("item.updated", "item.completed") and item.get("type") == "agent_message":
            texts[str(item.get("id", len(texts)))] = item.get("text", "")
    return partial_string("\n".join(texts.values()))
