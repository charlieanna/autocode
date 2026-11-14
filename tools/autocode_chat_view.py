"""Read-only chat projection and named message actions for task-run clients.

The history comes from existing task/user receipts. This adds no conversation
controller, private journal or run-state key. Sending a message never approves
a plan; clients explicitly continue the ordinary run after a saved action.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_tests_job as acceptance_tests_job
    from . import autocode_chat_history as history
    from . import autocode_gate_chat as gate_chat
    from . import autocode_pause_authority as pause_authority
    from . import autocode_planning_feedback as planning_feedback
    from . import autocode_requirements_chat as requirements_chat
    from . import autocode_requirements_review as requirements_review
    from . import autocode_run_status as run_status
    from . import autocode_scope_question as scope_question
except ImportError:
    import autocode_acceptance_tests_job as acceptance_tests_job
    import autocode_chat_history as history
    import autocode_gate_chat as gate_chat
    import autocode_pause_authority as pause_authority
    import autocode_planning_feedback as planning_feedback
    import autocode_requirements_chat as requirements_chat
    import autocode_requirements_review as requirements_review
    import autocode_run_status as run_status
    import autocode_scope_question as scope_question


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


def raised_bound(need) -> dict | None:
    """The one bound a person may raise to continue (autocode_command_inputs, #2044), or None."""
    rows = (need or {}).get("command_inputs")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return None
    row = rows[0]
    if row.get("id") == "grant_recovery":
        return None
    return row if isinstance(row.get("label"), str) and row.get("minimum") is not None else None


def _amount(row, value) -> str:
    if row.get("id") == "max_cost_usd":
        return f"${value:.2f}" if isinstance(value, (int, float)) else str(value)
    if str(row.get("id", "")).endswith("seconds") and isinstance(value, (int, float)):
        seconds = int(value)
        if seconds < 60:
            return f"{seconds}s"
        if seconds < 3600:
            return f"{seconds // 60}m{seconds % 60:02d}s"
        return f"{seconds // 3600}h{seconds % 3600 // 60:02d}m"
    return str(value)


def bound_words(row) -> str:
    """'total active time (2m05s used of 2m00s)': a bound and how much of it the run used, in words."""
    name = row["label"].split(" (")[0].lower()
    used = next((row[key] for key in ("elapsed", "used") if key in row), None)
    current = row.get("current")
    if used is None or current is None:
        return name
    return f"{name} ({_amount(row, used)} used of {_amount(row, current)})"


# Text a client never shows a person: a CLI flag, an internal status code or a code span.
MACHINERY = re.compile(r"(?:^|\s)--[a-z]|\b[A-Z]+(?:_[A-Z]+)+\b|`")
NEW_TASK = re.compile(r"\bnew (?:task|run|chat|conversation)\b", re.IGNORECASE)


def said(text) -> str:
    """``text`` as one sentence for a person, or "" when it is empty or names machinery."""
    text = " ".join(str(text or "").split())
    if not text or MACHINERY.search(text):
        return ""
    return text if text.endswith((".", "?", "!")) else text + "."


def offered(public) -> str | None:
    """The label of the recovery action the view's own ``next_command`` runs, or None."""
    try:
        command = shlex.split(public.get("next_command") or "")
    except ValueError:
        return None
    for row in (public.get("recovery") or {}).get("actions") or []:
        args = row.get("args") if isinstance(row, dict) else None
        if (
            args
            and isinstance(row.get("label"), str)
            and any(command[i : i + len(args)] == args for i in range(len(command)))
        ):
            return row["label"]
    return None


def stopped(public) -> str:
    """Why a stopped step stopped and what the person can do next, in the core's own words.

    The reason is the recovery view's plain summary (raw causes stay in the run's details); the
    next action is the one the view itself offers. A run that cannot continue says to start a new
    task, and a stop with no recorded summary says so instead of pointing at empty details."""
    need = public.get("needs") or {}
    recovery = public.get("recovery") or {}
    if need.get("new_run_required"):
        why = said(need.get("recovery_hint")) or said(need.get("reason"))
        follow = "" if NEW_TASK.search(why) else "Start a new task to carry on; this chat’s saved work stays readable."
        return " ".join(filter(None, ["I can’t continue this run.", why, follow]))
    what = said(recovery.get("what_happened"))
    text = (
        f"I couldn’t finish this step. {what}" if what else "I couldn’t finish this step, and no reason was recorded."
    )
    label = offered(public)
    if label:
        text += f" Your work so far is saved; choose “{label}” when you’re ready."
    elif "--resume-paused" in (public.get("next_command") or ""):
        text += " Your work so far is saved; continue to try this step again."
    else:
        text += " Your work so far is saved."
    if what and recovery.get("saved_reason"):
        text += " The run’s details show the recorded reason."
    return text


def tester_disagreement(public) -> str:
    """The Tester's stop before building is a question about the plan: say what it found and what to send.

    It was shown as a failed step that pointed at a saved reason the chat never showed, so the person was
    offered plan feedback with nothing to say it about (live run, 2026-11-11)."""
    recovery = public.get("recovery") or {}
    found = said(acceptance_tests_job.finding(recovery.get("saved_reason")))
    head = "Before building, the Tester found that the approved plan and today’s code disagree"
    text = f"{head}: {found}" if found else f"{head}; the run’s details show where."
    return text + " Tell me how the plan should change; the Tester then runs again on the revised plan."


def notice(public):
    """Explain the next human step without exposing the diagnostic dashboard."""
    status = public.get("status") or ""
    recovery = public.get("recovery") or {}
    if recovery.get("category") == "blocked":
        return recovery.get("what_happened")
    if status == "PAUSED_TRANSPORT_CHANGED":
        return "Your model connection settings changed. Check Connections before continuing."
    if status == "PAUSED_PROVIDER_UNCERTAIN":
        return "I couldn’t confirm the model connection. Check Connections, then try again."
    ended = (public.get("recovery") or {}).get("outcome") if status == "PAUSED_INTERVENTION" else None
    if isinstance(ended, dict) and ended.get("reason"):
        # AutoCode ended the run itself with a recorded outcome (#2485): say so, with the reason; ask nothing.
        return f"I couldn’t finish this task: {ended['reason']} Your work so far is saved; new work is a new task."
    need = public.get("needs") or {}
    if (
        status == "PAUSED_INTERVENTION"
        and need.get("kind") == "resume"
        and str(need.get("reason") or "").startswith(run_status.APPLIED_FEEDBACK_PREFIX)
    ):
        return "Feedback applied. Your work is saved; continue to update the plan."
    if status in ("PAUSED_INTERVENTION", "PAUSED_REQUESTED"):
        # A pause someone asked for is not a failure (#1598).
        return "Paused as you asked. Your work so far is saved; resume when you’re ready."
    if status == "PAUSED_INTERVENTION_PENDING":
        # The run stopped to apply a pause, feedback or stop someone asked for; not a failure either.
        return "Paused to apply your request. Your work so far is saved; continue when you’re ready."
    if status == acceptance_tests_job.PAUSE and acceptance_tests_job.finding(
        (public.get("recovery") or {}).get("saved_reason")
    ):
        return tester_disagreement(public)
    if status.startswith("PAUSED"):
        return stopped(public)
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


REWORD_WORDS = 6  # a question shorter than this is too brief to tell from a different one by its words
REWORD_SHARE = 0.85  # of the question's words that the stage's prose must already use


def plain(text: str) -> str:
    """``text`` without Markdown emphasis or code marks and with its spacing collapsed, for comparing
    a reply with its question: a model wrote `pages.py` in its reply and pages.py in the question."""
    return " ".join(re.sub(r"[`*_]", "", text).split())


def rewords(spoken: str, question: str) -> bool:
    """Whether the stage's prose already asks ``question``, copied or in other words.

    The Requirements stage writes its reply and then its question, often as two phrasings of one ask
    ("I couldn't access DTI-12447. Could you paste the ticket description ...?" and then "Could you
    paste the description of DTI-12447 ...?"), and a client showed both back to back. A prose that asks
    something (it has a question mark) and already uses nearly every word of a long enough question
    stands in for it; a note that only describes a repair, or a different question, shares too few."""
    if "?" not in spoken:
        return False
    wanted = re.findall(r"[\w'-]+", plain(question).lower())
    if len(wanted) < REWORD_WORDS:
        return False
    have = set(re.findall(r"[\w'-]+", plain(spoken).lower()))
    return sum(word in have for word in wanted) / len(wanted) >= REWORD_SHARE


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
        questions = need.get("questions", [])
        routing = (
            scope_question.question(state)
            if public.get("coordination") or (state.get("settings") or {}).get("coordination_member")
            else None
        )
        displayed = [q for q in questions if not routing or q.get("id") != routing["id"]]
        asked = [q.get("id") for q in displayed]
        # A client puts the requirements-question message in front of the person as the
        # question to answer, so it carries the questions' own text. The stage's prose only
        # frames them, and after a report repair that prose describes the repair, so it may
        # not stand in for the question.
        # The coordinator settles its own routing gate. Keep the exact gate in
        # public needs, without asking the person to narrow the coordinated task.
        question = "\n\n".join(q.get("question", "") for q in displayed).strip()
        spoken = requirements_chat.reply(state)
        # A greeting or small talk names nothing to do, so the runner asks its own requested-outcome
        # question only to keep the chat open. The reply is the whole answer: the person said "hi"
        # and is answered like in any chat, not with a second, canned question under it.
        holds_open = bool(spoken and displayed) and all(requirements_chat.holds_chat_open(q) for q in displayed)
        contains_question = bool(
            question
            and (plain(question) in plain(spoken) or rewords(spoken, question) or holds_open)
            and len(displayed) == len(questions)
        )
        if spoken and spoken != question and not contains_question:
            messages.append(
                {
                    "id": f"{REQUIREMENTS_SAY}-{hashlib.sha256(spoken.encode()).hexdigest()[:12]}",
                    "role": "assistant",
                    "text": spoken,
                }
            )
        text = spoken if contains_question else question or (spoken if len(displayed) == len(questions) else "")
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
    elif status == run_status.DESIGN_CONFLICT and not pause_authority.feedback_refusal(state):
        # The design check's stop says --feedback records the person's decision. Without this offer a
        # client read a typed decision at "Continue? [y/N]" as a no and left the chat (live run, 2026-11-11).
        action = ("feedback", "Send your decision", "--feedback")
    elif (
        status == "RUNNING"
        and need.get("kind") == "continue"
        and (public.get("controller") or {}).get("kind") == "ready"
        and planning_feedback.between_stages(state)
        and not pause_authority.feedback_refusal(state)
    ):
        action = ("feedback", "Send plan feedback", "--feedback")
    elif (
        (
            planning_feedback.semantic_stop(state, status)
            or (status == state.get("status") and pause_authority.requested_feedback(state))
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
    if (
        status == "RUNNING"
        and (state.get("settings") or {}).get("spec_first")
        and (state.get("intervention_capability") or {}).get("supports_messages") is True
        and not (state.get("settings") or {}).get("coordination_member")
        and not public.get("coordination")
        and not any(row.get("kind") == "stop" for row in state.get("applied_interventions", []))
        and run_dir is not None
        and workspace is not None
    ):
        message_input = {
            "kind": "message",
            "label": "Send message",
            "next_command": shlex.join(
                [
                    "autocode",
                    "intervention",
                    "submit",
                    "--workspace",
                    str(workspace),
                    "--run-dir",
                    str(run_dir),
                    "--request-id",
                    "REQUEST_ID",
                    "--kind",
                    "message",
                    "--text",
                    "MESSAGE",
                    "--json",
                ]
            ),
        }
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
        bound = raised_bound(need)
        if need.get("resolver_scope") == "operational_exhaustion" and bound:
            # A limit is lifted only by a larger total (#2607): never ask for a reply that cannot do it.
            question_text = (
                f"The run reached its limit for {bound_words(bound)}. A written reply can't lift a limit: "
                "give the run a larger total to continue, or leave it paused. Your saved work and evidence are kept."
            )
        elif need.get("resolver_scope") == "operational_exhaustion":
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
            # No token value in the named command: `--resolver-token -` reads this run's own saved
            # request, so relaying or logging this text never carries the credential (#2958).
            "next_command": shlex.join(
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
            requirements_review.confirmation_job(state) or "", APPROVAL_OFFERS["build"]
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
        "gate": gate_chat.offer(state),
        "context": context_token,
        "input": message_input,
        "review": review,
        "notice": notice(public),
        "phase": "requirements"
        if requirements_chat.enabled(state) and state.get("next_stage") in ("requirements_gather", "draft_plan")
        else None,
    }
