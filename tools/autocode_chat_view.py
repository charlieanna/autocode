"""Read-only chat projection and named message actions for task-run clients.

The history comes from existing task/user receipts. This adds no conversation
controller, private journal or run-state key. Sending a message never approves
a plan; clients explicitly continue the ordinary run after a saved action.
"""

from __future__ import annotations

import hashlib
import json
import shlex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_chat_history as history
    from . import autocode_pause_authority as pause_authority
    from . import autocode_requirements_chat as requirements_chat
    from . import autocode_requirements_review as requirements_review
except ImportError:
    import autocode_chat_history as history
    import autocode_pause_authority as pause_authority
    import autocode_requirements_chat as requirements_chat
    import autocode_requirements_review as requirements_review


def project(state: RunState, public, *, run_dir=None, workspace=None):
    """Expose only actions admitted at this checkpoint; the CLI rechecks on write."""
    turns = state.get("turns") or []
    messages = history.messages(state, completed=public.get("done", False))

    need = public.get("needs") or {}
    text = requirements_chat.streaming_reply(state, run_dir)
    if text:
        messages.append({"id": "requirements-live", "role": "assistant", "text": text, "streaming": True})
    elif need.get("kind") == "answer" and requirements_chat.enabled(state):
        spoken = requirements_chat.reply(state)
        if not spoken:
            spoken = "\n\n".join(q.get("question", "") for q in need.get("questions", []))
        if spoken:
            messages.append({"id": "requirements-question", "role": "assistant", "text": spoken})
    status = public.get("status")
    action = None
    # A completed stage record may remain as active_stage: use the public
    # checkpoint, never infer liveness from the presence of a retained attempt.
    if status == "TASK_COMPLETE" and public.get("done"):
        action = ("follow_up", "Send follow-up", "--follow-up")
    elif (
        status in ("AWAITING_GOAL_APPROVAL", "WAITING_FOR_USER")
        and need.get("kind") in ("approve_plan", "answer")
        and need.get("resolver_scope") not in ("blocker", "operational_exhaustion")
        and need.get("request_kind") != "blocker"
        and (state.get("user_request") or {}).get("kind") != "human_review"
        and not pause_authority.feedback_refusal(state)
    ):
        action = ("feedback", "Send reply" if need.get("kind") == "answer" else "Send plan feedback", "--feedback")
    context = hashlib.sha256(
        json.dumps(
            [status, len(turns), need, state.get("displayed_goal"), len(state.get("user_events") or [])],
            sort_keys=True,
        ).encode()
    ).hexdigest()
    message_input = None
    if action and run_dir is not None and workspace is not None:
        kind, label, flag = action
        message_input = {
            "kind": kind,
            "label": label,
            "next_command": shlex.join(
                ["autocode", "--workspace", str(workspace), "--run-dir", str(run_dir), flag, "MESSAGE"]
            ),
        }
    review = None
    questions = need.get("questions") or []
    if (
        need.get("kind") == "answer"
        and state.get("requirements_handoff")
        and len(questions) == 1
        and questions[0].get("id") == requirements_review.review_question(state)["id"]
    ):
        review = {
            "question_id": questions[0]["id"],
            "answer": "plan",
            "label": "Approve understanding and prepare plan",
            "summary": requirements_review.review_question(state)["question"],
        }
    return {
        "messages": messages,
        "context": context,
        "input": message_input,
        "review": review,
        "phase": "requirements"
        if requirements_chat.enabled(state) and state.get("next_stage") in ("requirements_gather", "astra_discovery")
        else None,
    }
