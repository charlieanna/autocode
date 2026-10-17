"""Requirements conversation policy and bounded assistant-text projection.

The existing task controller owns dispatch and sessions. This module supplies
the opening question for a bare greeting, makes the first reply the run's task,
and reads only the active stage's assistant reply, never tool output or
reasoning. A partial reply carries no planning authority.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_task_input as task_input
except ImportError:
    import autocode_task_input as task_input

INTRO = "requirements-intro"
GREETING = "Hi! What would you like to work on today?"
STAGE = "requirements_gather"
RECOGNIZE = "recognize_workflow"


def enabled(state: RunState):
    return bool((state.get("settings") or {}).get("spec_first") and not state.get("goal_contract"))


def _greeted(state: RunState):
    text = str(state.get("task", "")).strip().casefold().rstrip("!?. ")
    return bool(
        enabled(state)
        and text in {"hi", "hello", "hey", "hi there", "hello there", "good morning", "good afternoon", "good evening"}
        and not state.get("requirements_handoff")
        and not state.get("stages")
        and not (state.get("workflow") or {}).get("kind")
    )


def opening(state: RunState):
    return _greeted(state) and not state.get("user_events")


def adopt_reply(state: RunState):
    """The person's first reply to the opening names the work: it becomes the task, then the job is recognized."""
    if not _greeted(state):
        return False
    events = state.get("user_events") or []
    if len(events) != 1 or events[0].get("actor") != "user_cli":
        return False
    event = events[0]
    if not (event.get("kind") == "brief_feedback" or event.get("question_id") == INTRO):
        return False
    text = str(event.get("text") or "").strip()
    if not text:
        return False
    # The source log keeps the greeting as task:0; the reply is saved there as its own source.
    task_input.assign(state, text)
    state.update({"next_stage": RECOGNIZE, "phase": "DISCOVERING"})
    return True


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
    if active.get("stage") == STAGE + "_report_repair":
        # Keep the already displayed reply provisional while its structured
        # report is repaired. This grants no approval or report authority.
        active = (state.get("pending_report_repair") or {}).get("original") or {}
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
    texts: dict[str, str] = {}
    live = ""
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("type") == "autocode_chat_reply" and isinstance(row.get("reply"), str):
            live = row["reply"][:12000]
        part_value = row.get("part")
        part: dict[str, object] = part_value if isinstance(part_value, dict) else {}
        part_text = part.get("text")
        if row.get("type") == "text" and isinstance(part_text, str):
            texts[str(part.get("id", len(texts)))] = part_text
        item_value = row.get("item")
        item: dict[str, object] = item_value if isinstance(item_value, dict) else {}
        if row.get("type") in ("item.updated", "item.completed") and item.get("type") == "agent_message":
            item_text = item.get("text", "")
            texts[str(item.get("id", len(texts)))] = item_text if isinstance(item_text, str) else ""
    return live or partial_string("\n".join(texts.values()))
