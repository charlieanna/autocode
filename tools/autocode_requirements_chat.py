"""Requirements conversation policy and bounded assistant-text projection.

The existing task controller owns dispatch and sessions. Requirements is the first
stage of every run and follow-up turn that has it (#1860), and its report names the
job (#1846): a question goes straight to the Analyst on every flow, and any other job
starts once nothing stands in front of it (``start_named_job``). In a chat run
(spec-first) Requirements answers every message first, whatever it says; until
it finds work to do, the person's newest reply becomes the run's task, and a job
other than a question starts once the person approves what Requirements understood.
Only a chat report that names no job leaves the job to the recognizer after that approval.
This module also reads only the active stage's assistant reply, never tool
output or reasoning. A partial reply carries no planning authority.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_plan_chat as plan_chat
    from . import autocode_requirements_review as requirements_review
    from . import autocode_task_input as task_input
    from . import autocode_workflows as workflows
except ImportError:
    import autocode_plan_chat as plan_chat
    import autocode_requirements_review as requirements_review
    import autocode_task_input as task_input
    import autocode_workflows as workflows

STAGE = "requirements_gather"


def enabled(state: RunState):
    return bool(
        (state.get("settings") or {}).get("spec_first") and (not state.get("goal_contract") or plan_chat.pending(state))
    )


def _first_conversation(state: RunState):
    return enabled(state) and not state.get("turns") and "workflow" in state


def gather_first(state: RunState):
    """Gather an unpinned chat brief; an explicitly selected review starts its named job."""
    if not _first_conversation(state) or state.get("stages"):
        return False
    # An explicit review already names the job and supplies its brief. It has no
    # implementation plan to approve; ambiguous/unpinned chats still use intake.
    workflow = state.get("workflow") or {}
    if workflow.get("kind") == "review" and workflow.get("source") == "user":
        return False
    intake = state.get("design_intake") or {}
    if intake.get("status") == "PENDING":
        intake["return_stage"] = STAGE
    else:
        state["next_stage"] = STAGE
    return True


def opener(state: RunState):
    """The message a chat run opened with, kept as task:0 after a later reply became the task."""
    if not (state.get("settings") or {}).get("spec_first"):
        return None
    tasks = [row for row in state.get("source_log") or [] if str(row.get("id", "")).startswith("task:")]
    if len(tasks) == 1 and tasks[0].get("kind") == "task" and tasks[0].get("text"):
        return str(tasks[0]["text"])
    return None


def adopt_reply(state: RunState):
    """Until Requirements finds work in the conversation, the person's newest reply becomes the task."""
    report = (state.get("requirements_handoff") or {}).get("report")
    if not _first_conversation(state) or not isinstance(report, dict) or report.get("requirements"):
        return False
    # Answering one of several questions waits for the rest, which stay with this report.
    if state.get("pending_questions"):
        return False
    events = state.get("user_events") or []
    event = events[-1] if events else {}
    if event.get("actor") != "user_cli" or event.get("kind") not in ("brief_feedback", "answer"):
        return False
    if str(event.get("question_id", "")).startswith(requirements_review.PREFIX):
        return False
    text = str(event.get("text") or "").strip()
    if not text or text == state.get("task"):
        return False
    # The source log keeps the opening message as task:0; the reply is saved there as its own source.
    task_input.assign(state, text)
    state.update({"next_stage": STAGE, "phase": "DISCOVERING"})
    return True


def recognize_after_approval(state: RunState):
    """Once the person approves what Requirements understood, start the job it named, else recognize it.

    Recognition keeps ``workflow.then`` as the build pipeline's entry, so a later request is gathered
    again; ``requirements_approved`` tells workflows.apply that this one already was. A job with its
    own first stage starts there once; when it hands back for planning, the approval stands.
    """
    # Every turn of a chat begins at Requirements (#1860), so a follow-up's approval starts its job too.
    if not enabled(state) or "workflow" not in state or state.get("pending_questions"):
        return False
    if state.get("next_stage") != requirements_review.NEXT or not state.get("requirements_handoff"):
        return False
    answer = (state.get("answers") or {}).get(requirements_review.review_question(state)["id"]) or {}
    if str(answer.get("text", "")).strip().lower() != requirements_review.APPROVE:
        return False
    kind = state["workflow"].get("kind")
    handoff = state["requirements_handoff"]
    if kind is None and workflows.named_by_requirements(handoff.get("report") or {}):
        # Requirements named the job the person just approved (#1846): start it, no second model call.
        state["workflow"]["requirements_approved"] = True
        workflows.from_requirements(state, handoff.get("report") or {}, {"output": handoff.get("output")})
    elif kind is None:
        workflows.begin(state, state["workflow"].get("then") or STAGE)
        state["workflow"]["requirements_approved"] = True
    elif kind in workflows.FIRST_STAGE and not workflows.ran_this_turn(state, workflows.FIRST_STAGE[kind]):
        state["next_stage"] = workflows.FIRST_STAGE[kind]
    else:
        return False
    return True


def answer_now(state: RunState, record: dict) -> bool:
    """A run whose Requirements understood a clear question starts the Analyst at once (#1846, #1860).

    Nothing is planned or built for a question, so there is nothing for the person to approve: asking
    them to "prepare a plan" first only invited a yes to a plan that never came. Unanswered open
    questions still stop the run, and a question the Analyst already answered in this turn is not
    answered again."""
    if "workflow" not in state or requirements_review.job(state) != "discuss":
        return False
    report = (state.get("requirements_handoff") or {}).get("report") or {}
    answered = state.get("answers") or {}
    if any(row.get("id") not in answered for row in report.get("open_questions") or []):
        return False
    first = workflows.FIRST_STAGE["discuss"]
    if workflows.ran_this_turn(state, first):
        return False
    if state["workflow"].get("kind") is None:
        workflows.from_requirements(state, report, record)
    else:
        # The handoff was just accepted as running (autocode_requirements_handoff); only the stage changes.
        state["next_stage"] = first
    return True


def start_named_job(state: RunState, record: dict) -> bool:
    """Start the job Requirements named once no question stands in front of it (#1860).

    A plain CLI run has no confirmation stop, and neither has a follow-up turn whose earlier plan
    still stands; a chat's first handoff waits for the person's approval instead
    (recognize_after_approval). A report that names no job plans as a build. A job already known
    (pinned with --workflow, or recognized) keeps the stage the handoff chose."""
    if (state.get("workflow") or {}).get("kind") is not None or "workflow" not in state:
        return False
    workflows.from_requirements(state, (state.get("requirements_handoff") or {}).get("report") or {}, record)
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
    if not isinstance(active, dict):
        return ""
    if active.get("stage") == STAGE + "_report_repair":
        # Keep the already displayed reply provisional while its structured
        # report is repaired. This grants no approval or report authority.
        active = (state.get("pending_report_repair") or {}).get("original") or {}
    if (
        not isinstance(active, dict)
        or not enabled(state)
        or active.get("stage") != STAGE
        or not run_dir
        or not active.get("events")
    ):
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
    if live:
        return live
    # Later messages replace earlier ones, so a short intermediate reply is shown
    # only until the next one (or the final report) lands. Growing item.updated
    # text is the same story: the newest extract wins.
    reply = ""
    for text in texts.values():
        extracted = partial_string(text)
        if extracted:
            reply = extracted
    return reply[:12000]
