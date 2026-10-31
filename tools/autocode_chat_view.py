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
    from . import autocode_acceptance_tests_job as acceptance_tests_job
    from . import autocode_chat_history as history
    from . import autocode_pause_authority as pause_authority
    from . import autocode_planning_feedback as planning_feedback
    from . import autocode_requirements_chat as requirements_chat
    from . import autocode_requirements_review as requirements_review
except ImportError:
    import autocode_acceptance_tests_job as acceptance_tests_job
    import autocode_chat_history as history
    import autocode_pause_authority as pause_authority
    import autocode_planning_feedback as planning_feedback
    import autocode_requirements_chat as requirements_chat
    import autocode_requirements_review as requirements_review


# The id prefix of the Requirements question the chat is waiting on; the rest names the question.
REQUIREMENTS_QUESTION = "requirements-question"
# The stage's own prose beside a question, kept as its own message so it cannot displace the question.
REQUIREMENTS_SAY = "requirements-say"


# What approving a chat's understanding starts, by the job Requirements named (#1846): the button's
# label, then the spend confirmation's title, detail and button. A question is never offered (it is
# answered at once), and an unnamed job is offered as the plan it becomes.
APPROVAL_OFFERS = {
    "build": (
        "Approve understanding and prepare plan",
        "Prepare a reviewed plan?",
        "Planning uses your selected models and saved limits. Building still waits for your approval of the displayed plan.",
        "Prepare plan",
    ),
    "bugfix": (
        "Approve understanding and investigate",
        "Investigate the bug?",
        "The investigation uses your selected models and saved limits. Fixing still waits for your approval of the displayed plan.",
        "Investigate",
    ),
    "review": (
        "Approve understanding and start the review",
        "Start the review?",
        "The review uses your selected models and saved limits. It changes no files.",
        "Start review",
    ),
    "design": (
        "Approve understanding and start the design",
        "Start the design work?",
        "The design work uses your selected models and saved limits. Nothing is built.",
        "Start design",
    ),
}


def notice(public):
    """Explain the next human step without exposing the diagnostic dashboard."""
    status = public.get("status") or ""
    if status == "PAUSED_TRANSPORT_CHANGED":
        return "Your model connection settings changed. Check Connections before continuing."
    if status == "PAUSED_PROVIDER_UNCERTAIN":
        return "I couldn’t confirm the model connection. Check Connections, then try again."
    ended = (public.get("recovery") or {}).get("outcome") if status == "PAUSED_INTERVENTION" else None
    if isinstance(ended, dict) and ended.get("reason"):
        # AutoCode ended the run itself with a recorded outcome (#2485): say so, with the reason; ask nothing.
        return f"I couldn’t finish this task: {ended['reason']} Your work so far is saved; new work is a new task."
    if status in ("PAUSED_INTERVENTION", "PAUSED_REQUESTED"):
        # A pause someone asked for is not a failure (#1598).
        return "Paused as you asked. Your work so far is saved; resume when you’re ready."
    if status == "PAUSED_INTERVENTION_PENDING":
        # The run stopped to apply a pause, feedback or stop someone asked for; not a failure either.
        return "Paused to apply your request. Your work so far is saved; continue when you’re ready."
    if status.startswith("PAUSED"):
        return "I couldn’t finish this step. The run’s details say what needs attention."
    if status == "RUNNING":
        liveness = public.get("liveness") or {}
        if isinstance(liveness, dict) and liveness.get("kind") in ("supervised", "unsupervised"):
            return "Working on your request…"
        return None
    return None


def context(state: RunState, public):
    """The same opaque decision context for status and cold-start run inventory."""
    return hashlib.sha256(
        json.dumps(
            [
                public.get("status"),
                len(state.get("turns") or []),
                public.get("needs") or {},
                state.get("displayed_goal"),
                len(state.get("user_events") or []),
            ],
            sort_keys=True,
        ).encode()
    ).hexdigest()


def project(state: RunState, public, *, run_dir=None, workspace=None):
    """Expose only actions admitted at this checkpoint; the CLI rechecks on write."""
    messages = history.messages(state, completed=public.get("done", False))

    need = public.get("needs") or {}
    status = public.get("status")
    resolver_reply = (
        status == "WAITING_FOR_USER"
        and need.get("kind") == "answer"
        and need.get("resolver_scope") in ("blocker", "operational_exhaustion")
        and need.get("resolver_request_id")
        and need.get("resolver_token")
        and not need.get("route")
        and (state.get("user_request") or {}).get("kind") != "human_review"
        and run_dir is not None
        and workspace is not None
    )
    text = requirements_chat.streaming_reply(state, run_dir)
    if text:
        messages.append({"id": "requirements-live", "role": "assistant", "text": text, "streaming": True})
    elif need.get("kind") == "answer" and not resolver_reply and requirements_chat.enabled(state):
        asked = [q.get("id") for q in need.get("questions", [])]
        # A client puts the requirements-question message in front of the person as the
        # question to answer, so it carries the questions' own text. The stage's prose only
        # frames them, and after a report repair that prose describes the repair, so it may
        # not stand in for the question.
        question = "\n\n".join(q.get("question", "") for q in need.get("questions", [])).strip()
        spoken = requirements_chat.reply(state)
        if spoken and spoken != question:
            messages.append(
                {
                    "id": f"{REQUIREMENTS_SAY}-{hashlib.sha256(spoken.encode()).hexdigest()[:12]}",
                    "role": "assistant",
                    "text": spoken,
                }
            )
        text = question or spoken
        if text:
            # Each question is its own message: a client shows a message id once, so a shared id
            # hid every Requirements question after the first (#1792).
            key = hashlib.sha256(json.dumps([asked, text]).encode()).hexdigest()[:12]
            messages.append({"id": f"{REQUIREMENTS_QUESTION}-{key}", "role": "assistant", "text": text})
    action = None
    blocker_hold = False  # an answered semantic blocker waiting for its contract decision (#2434)
    # A completed stage record may remain as active_stage: use the public
    # checkpoint, never infer liveness from the presence of a retained attempt.
    if status == "TASK_COMPLETE" and public.get("done"):
        action = ("follow_up", "Send follow-up", "--follow-up")
    elif (
        status in ("AWAITING_GOAL_APPROVAL", "WAITING_FOR_USER")
        and need.get("kind") in ("approve_plan", "answer")
        and need.get("resolver_scope") not in ("blocker", "operational_exhaustion")
        and need.get("request_kind") != "blocker"
        and (state.get("user_request") or {}).get("kind") not in ("human_review", "blocker")
        and not pause_authority.feedback_refusal(state)
    ):
        action = (
            "feedback",
            "Send reply"
            if need.get("kind") == "answer"
            else ("Send message" if (state.get("settings") or {}).get("spec_first") else "Send plan feedback"),
            "--feedback",
        )
    elif status == acceptance_tests_job.PAUSE and not pause_authority.feedback_refusal(state):
        # The Tester's pause says --feedback revises the plan; offer it so a client can take it (#1869).
        action = ("feedback", "Send plan feedback", "--feedback")
    elif (
        (
            planning_feedback.semantic_stop(state, status)
            # An answered semantic blocker holds for its contract decision: the person's explicit
            # plan feedback takes it, through the same named --feedback action (#2434).
            or (blocker_hold := status == state.get("status") and pause_authority.answered_blocker_decision(state))
        )
        and need.get("kind") == "resume"
        and need.get("resolver_scope") not in ("blocker", "operational_exhaustion")
        and not pause_authority.feedback_refusal(state)
    ) or (
        status == "PAUSED_INVALID_OUTPUT"
        and planning_feedback.invalid_report(state)
        and not pause_authority.feedback_refusal(state)
    ):
        action = (
            "feedback",
            "Send plan feedback" if blocker_hold else "Explain what should change",
            "--feedback",
        )
    context_token = context(state, public)
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
    if resolver_reply:
        question_text = "\n\n".join(
            "\n".join(filter(None, [q.get("question"), q.get("why"), *q.get("options", [])]))
            for q in need.get("questions", [])
        )
        # The recovery card owns the human explanation; raw causes and full
        # question/options remain available in Run details.
        if need.get("resolver_scope") == "operational_exhaustion":
            recovery = public.get("recovery") or {}
            explanation = recovery.get("what_happened") or "I couldn’t finish this step."
            question_text = explanation + (
                " The run’s details say what needs attention.\n\n"
                "Your saved work and evidence are kept. Reply with what changed so recovery can be reviewed. "
                "Sending a reply does not authorize a retry or change your limits; the run stays paused until "
                "you take its next recovery action."
            )
        if question_text:
            messages.append(
                {"id": "resolver-" + need["resolver_request_id"], "role": "assistant", "text": question_text}
            )
        message_input = {
            "kind": "resolver_response",
            "label": "Send recovery reply",
            "next_command": shlex.join(["AUTOCODE_RESOLVER_TOKEN=" + need["resolver_token"]])
            + " "
            + shlex.join(
                [
                    "autocode",
                    "--workspace",
                    str(workspace),
                    "--run-dir",
                    str(run_dir),
                    "--resolver-request",
                    need["resolver_request_id"],
                    "--resolver-token",
                    "-",
                    "--resolver-response",
                    "provide_information",
                    "--resolver-message",
                    "MESSAGE",
                ]
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
        label, title, detail, confirm = APPROVAL_OFFERS.get(
            requirements_review.job(state) or "", APPROVAL_OFFERS["build"]
        )
        review = {
            "question_id": questions[0]["id"],
            "answer": requirements_review.APPROVE,
            "label": label,
            "summary": requirements_review.review_question(state)["question"],
            "spend_confirmation": {"match": "trim_lower", "title": title, "detail": detail, "label": confirm},
        }
    return {
        "messages": messages,
        "context": context_token,
        "input": message_input,
        "review": review,
        "notice": notice(public),
        "phase": "requirements"
        if requirements_chat.enabled(state) and state.get("next_stage") in ("requirements_gather", "astra_discovery")
        else None,
    }
