"""Render existing task receipts and accepted answers as a durable chat history."""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_build_worktree as build_worktree
    from . import autocode_contract_identity as contract_identity
    from . import autocode_message_context as message_context
    from . import autocode_project_delivery as project_delivery
    from . import autocode_requirements_chat as requirements_chat
    from . import autocode_requirements_review as requirements_review
    from . import autocode_scratch_projects as scratch_projects
except ImportError:
    import autocode_build_worktree as build_worktree
    import autocode_contract_identity as contract_identity
    import autocode_message_context as message_context
    import autocode_project_delivery as project_delivery
    import autocode_requirements_chat as requirements_chat
    import autocode_requirements_review as requirements_review
    import autocode_scratch_projects as scratch_projects

# What a person did when they pressed a button, in their words rather than the CLI's answer value.
CONFIRMED_REQUIREMENTS = "Looks right. Prepare the plan."
APPROVED_PLAN = "Approved the plan."
# A client shows a message id once, so the bootstrap disclosure keeps a stable one (#1908).
BOOTSTRAP_NOTICE = "workspace-bootstrap"
WORKTREE_NOTICE = "build-worktree"  # where the run plans and builds, said once (autocode_build_worktree)
# What a completed build in a scratch project said about the named project it delivered, per turn.
DELIVERY_NOTICE = "project-delivered"


def response(state: RunState) -> str:
    """The completed turn's saved result, without reading report files or logs."""
    workflow = (state.get("workflow") or {}).get("kind")
    if workflow == "discuss":
        answer = state.get("answer") or {}
        parts = [answer.get("answer", "")]
        parts.extend(f"{row.get('claim', '')} ({row.get('source', '')})" for row in answer.get("evidence") or [])
        parts.extend(answer.get("questions") or [])
        return "\n\n".join(part for part in parts if part)
    if workflow == "review":
        review = state.get("review") or {}
        return (
            f"Review complete: {review.get('blocking', 0)} blocking findings, "
            f"{review.get('advisory', 0)} advisory findings.\nReport: {review.get('report_path', '')}"
        )
    decision = state.get("final_decision") or state.get("last_decision") or {}
    report = decision.get("report") or decision
    text = report.get("summary") or report.get("reason") or "The requested task is complete."
    where = delivery(state)
    return f"{text}\n\n{where}" if where else text


def delivery(state: RunState) -> str:
    """Where a task-worktree run's finished work is and the commands that take it: the branch, the
    merge and the clean-up, as the runner prints them (#2344). Empty for an in-place run."""
    branch, project, workspace = (state.get(key) for key in ("task_branch", "project_workspace", "workspace"))
    if not (isinstance(branch, str) and branch and isinstance(project, str) and isinstance(workspace, str)):
        return ""
    return (
        f"Delivered on branch {branch}; your checkout is unchanged until you merge it:\n"
        f"  autocode merge --workspace {shlex.quote(project)} {shlex.quote(workspace)}\n"
        f"then remove the task worktree with:\n"
        f"  autocode clean-worktrees --workspace {shlex.quote(project)} --yes"
    )


def _approved_plan(state: RunState, event) -> str | None:
    """Recover the plan this receipt approved, including revisions archived by later turns."""
    for contract in [state.get("goal_contract"), *(state.get("contract_history") or [])]:
        if not isinstance(contract, dict) or contract.get("approval_event") != event:
            continue
        try:
            if not contract_identity.approved(
                {"goal_contract": contract, "user_events": state.get("user_events") or []}
            ):
                continue
        except (KeyError, TypeError, ValueError):
            continue  # A missing or damaged plan cannot supply historical approval text.
        body = contract["body"]
        lines = [f"Approved plan · revision {contract['revision']}"]
        if body.get("intended_outcome"):
            lines += [body["intended_outcome"]]
        for criterion in body.get("acceptance_criteria") or []:
            lines += ["- " + criterion["criterion"]]
            if criterion.get("verification_method"):
                lines += ["  Checked by: " + criterion["verification_method"]]
            if criterion.get("human_review") is True:
                lines += ["  Also needs your review."]
        for key, title in (("constraints", "Constraints"), ("permission_boundaries", "What it may do")):
            if body.get(key):
                lines += ["", title + ":", *("- " + text for text in body[key])]
        delivery = body.get("git_delivery") or {}
        if delivery.get("kind") == "atomic_commit":
            lines += ["", "Git delivery: one atomic commit — " + delivery["message"]]
        return "\n".join(lines)
    return None


def _bootstrap_notice(state: RunState) -> dict | None:
    """Say once where the work happens when the selected folder held no Git repository (#1908).

    A launch prints the project it created, but a chat client starts the CLI as a subprocess and
    never shows that output, so the person would otherwise find the path only in a footer. It
    matters only once AutoCode is about to write files, so ``messages`` shows it after the plan's
    approval: a "hi", a question or a review never builds, and was answered with a paragraph about
    folders. The private project's own path is machinery; the status view's ``workspace`` keeps it.
    """
    record = state.get("workspace_bootstrap")
    if not isinstance(record, dict):
        return None
    project = record.get("project")
    if not isinstance(project, str) or not project:
        return None
    selected = record.get("selected")
    if isinstance(selected, str) and scratch_projects.is_scratch(selected, project):
        text = (
            f"{selected} is not in Git, so I build in a private project of my own and leave your files as "
            f"they are. When the build is done, I put it in a new folder in {selected}."
        )
    elif selected == project and record.get("kept_files"):
        text = (
            f"{project} was not a Git repository, so I made it one and committed none of your files; "
            "I work on the files there."
        )
    elif selected == project and record.get("first_commit"):
        text = f"{project} had no commit yet, so I made an empty first one; what I build is written there."
    elif selected == project:
        text = f"{project} was not a Git repository, so I made it one; what I build is written there."
    else:
        text = (
            f"{selected} is not a Git repository, so I created a project inside it and the work happens there: "
            f"{project}. Your own files stay where they are; the files I write land in that project."
        )
    return {"id": BOOTSTRAP_NOTICE, "role": "assistant", "text": text}


def messages(state: RunState, *, completed=False) -> list[dict]:
    turns = state.get("turns") or []
    initial = (turns[0].get("previous") or {}).get("task") if turns else state.get("task")
    # A chat shows the message it opened with, not a later reply that became the task.
    initial = requirements_chat.opener(state) or initial
    rows = [{"id": "initial", "role": "user", "text": initial}] if initial else []
    notice = _bootstrap_notice(state)  # shown after the first approved plan, the first point files are written
    worktree = build_worktree.notice(state)  # where the run plans and builds: a branch of its own
    # A run that moved before planning says so after the person's last message before the move (#3081).
    moved_after = (state.get(build_worktree.KEY) or {}).get("after_events")
    prior = {turn.get("event_id"): (turn.get("previous") or {}).get("chat_response") for turn in turns}
    # Turn k's response is shown where follow-up k was said; its delivery note follows it.
    turn_of = {turn.get("event_id"): number for number, turn in enumerate(turns)}
    delivered = project_delivery.notes(state)

    def delivery(number):
        note = delivered.get(number)
        return [{"id": f"{DELIVERY_NOTICE}-{number}", "role": "assistant", "text": note}] if note else []

    for index, event in enumerate(state.get("user_events") or []):
        if worktree is not None and isinstance(moved_after, int) and index >= moved_after:
            rows.append({"id": WORKTREE_NOTICE, "role": "assistant", "text": worktree})
            worktree = None
        if event.get("kind") == "goal_approval" and event.get("token") and event.get("actor") != "workflow_policy":
            plan = _approved_plan(state, event)
            if plan:
                rows.append({"id": f"plan-{event['token']}", "role": "assistant", "text": plan})
            rows.append({"id": f"approval-{index}", "role": "user", "text": APPROVED_PLAN})
            if notice is not None:
                rows.append(notice)
                notice = None
            if worktree is not None and not isinstance(moved_after, int):
                rows.append({"id": WORKTREE_NOTICE, "role": "assistant", "text": worktree})
                worktree = None
            continue
        if (
            event.get("kind") not in ("brief_feedback", "answer", "plan_message", "gate_message")
            or not event.get("text")
            or event.get("forwarded")
        ):
            continue
        ident = event.get("id") or f"event-{index}"
        if prior.get(ident):
            rows.append({"id": f"{ident}-response", "role": "assistant", "text": prior[ident]})
            rows += delivery(turn_of[ident])
        question = event.get("question")
        if event.get("assistant_reply"):
            rows.append({"id": f"{ident}-reply", "role": "assistant", "text": event["assistant_reply"]})
        elif isinstance(question, dict) and question.get("question"):
            rows.append({"id": f"{ident}-question", "role": "assistant", "text": question["question"]})
        text = event["text"]
        if (
            event.get("kind") == "answer"
            and text == "plan"
            and str(event.get("question_id") or "").startswith(requirements_review.PREFIX)
        ):
            text = CONFIRMED_REQUIREMENTS
        rows.append({"id": ident, "role": "user", "text": text})
        if event.get("reply_after"):
            rows.append(
                {
                    "id": f"{ident}-reply-after",
                    "role": "assistant",
                    "text": event["reply_after"],
                    **message_context.receipt(event),
                }
            )
    if worktree is not None and isinstance(moved_after, int):
        rows.append({"id": WORKTREE_NOTICE, "role": "assistant", "text": worktree})
    resolver = state.get("resolver") or {}
    for event in resolver.get("human_responses", []):
        if event.get("actor") != "user_cli" or not event.get("text"):
            continue
        ident = "resolver-" + event["request_id"]
        entry = (resolver.get("human_escalations") or {}).get(event["request_id"], {})
        request = ((entry.get("identity") or {}).get("proposal") or {}).get("request") or {}
        question = "\n\n".join(q.get("question", "") for q in request.get("questions", []))
        if question:
            rows.append({"id": ident + "-question", "role": "assistant", "text": question})
        rows.append({"id": ident + "-response", "role": "user", "text": event["text"]})
    if completed:
        rows.append({"id": f"response-{len(turns)}", "role": "assistant", "text": response(state)})
        rows += delivery(len(turns))
    return rows
