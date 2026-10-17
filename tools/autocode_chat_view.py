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
except ImportError:
    import autocode_chat_history as history
    import autocode_pause_authority as pause_authority


def project(state: RunState, public, *, run_dir=None, workspace=None):
    """Expose only actions admitted at this checkpoint; the CLI rechecks on write."""
    turns = state.get("turns") or []
    messages = history.messages(state, completed=public.get("done", False))

    need = public.get("needs") or {}
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
    return {"messages": messages, "context": context, "input": message_input}
