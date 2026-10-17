"""Render existing task receipts and accepted answers as a durable chat history."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_requirements_chat as requirements_chat
except ImportError:
    import autocode_requirements_chat as requirements_chat


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
    return report.get("summary") or report.get("reason") or "The requested task is complete."


def messages(state: RunState, *, completed=False) -> list[dict]:
    turns = state.get("turns") or []
    initial = (turns[0].get("previous") or {}).get("task") if turns else state.get("task")
    # A chat shows the message it opened with, not a later reply that became the task.
    initial = requirements_chat.opener(state) or initial
    rows = [{"id": "initial", "role": "user", "text": initial}] if initial else []
    prior = {turn.get("event_id"): (turn.get("previous") or {}).get("chat_response") for turn in turns}
    for index, event in enumerate(state.get("user_events") or []):
        if (
            event.get("kind") not in ("brief_feedback", "answer", "plan_message")
            or not event.get("text")
            or event.get("forwarded")
        ):
            continue
        ident = event.get("id") or f"event-{index}"
        if prior.get(ident):
            rows.append({"id": f"{ident}-response", "role": "assistant", "text": prior[ident]})
        question = event.get("question")
        if event.get("assistant_reply"):
            rows.append({"id": f"{ident}-reply", "role": "assistant", "text": event["assistant_reply"]})
        elif isinstance(question, dict) and question.get("question"):
            rows.append({"id": f"{ident}-question", "role": "assistant", "text": question["question"]})
        rows.append({"id": ident, "role": "user", "text": event["text"]})
        if event.get("reply_after"):
            rows.append({"id": f"{ident}-reply-after", "role": "assistant", "text": event["reply_after"]})
    if completed:
        rows.append({"id": f"response-{len(turns)}", "role": "assistant", "text": response(state)})
    return rows
