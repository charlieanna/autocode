"""Conservative task-chat intent rules; this module cannot execute a control."""

import hashlib
import json
import re

try:
    from .dashboard_work_summary import project as work_summary
except ImportError:
    from dashboard_work_summary import project as work_summary


def classify(text, question_id=None):
    words = text.strip().lower().rstrip(".!?")
    if re.fullmatch(
        r"(?:please )?(?:stop|pause|resume|continue)(?: (?:now|the task|after (?:this|the current) step))?", words
    ):
        return "control"
    if (
        "?" in text
        or re.match(r"^(?:how|why|what|when|where|is|are|did|does|can|could|would)\b", words)
        or words in ("status", "status update", "progress", "update")
    ):
        return "question"
    if question_id:
        return "answer"
    if re.fullmatch(r"(?:yes|yep|yeah|ok|okay|approve|approved|i approve|go ahead|looks good)", words):
        return "approval"
    # Uncertain prose is proposed, never silently treated as an applied change.
    return "proposed_change"


def status_reply(view):
    status = str(view.get("status") or "unavailable").replace("_", " ").capitalize()
    result = ["Saved task status: " + status + "."]
    reason = view.get("stop_reason")
    if isinstance(reason, str) and reason.strip():
        result.append(reason.strip())
    summary = work_summary(view)
    counts = summary["counts"]
    if view.get("status") == "TASK_COMPLETE" and view.get("completion_current") is not True:
        result[0] = "Completion was recorded earlier; current completion needs verification."
    if summary["verification_stale"]:
        result.extend(
            summary["verification_reasons"]
            or ["The saved check results have not been inspected against current source and evidence."]
        )
    if counts["requirements"]:
        result.append(
            f"{counts['checked']} requirements checked; {counts['failed']} failed or blocked; "
            f"{counts['unchecked']} unchecked."
        )
    request = view.get("human_escalation") or {}
    if isinstance(request, dict) and request.get("decision_needed"):
        result.append(str(request["decision_needed"]))
    pending = [row for row in summary["requirements"] if row["state"] != "checked"]
    problems = summary["problems"]
    if pending:
        result.append(
            "Requirements still needing proof: "
            + "; ".join(f"{row['id']}: {row['label']} ({row['state']})" for row in pending[:5])
            + (f"; and {len(pending) - 5} more in Work." if len(pending) > 5 else ".")
        )
    if problems:
        result.append(
            "Open problems: "
            + "; ".join(f"{row['id']}: {row['label']}" for row in problems[:5])
            + (f"; and {len(problems) - 5} more in Work." if len(problems) > 5 else ".")
        )
    result.append("This reply uses saved records and does not change the task.")
    return "\n".join(result)


def prepare(row, view):
    row["kind"] = classify(row["submitted_text"], row.get("question_id"))
    row["classification_rule"] = "context-first-v1"
    if row["kind"] == "answer":
        return row
    row["status"] = "received"
    if row["kind"] == "proposed_change":
        scope = {"id": row["id"], "text": row["submitted_text"], "goal_token": view.get("goal_token")}
        token = hashlib.sha256(json.dumps(scope, sort_keys=True).encode()).hexdigest()
        row.update(
            status="awaiting_confirmation",
            confirmation={"token": token, "goal_token": view.get("goal_token"), "status": "pending"},
            reply="Apply this as a change to the plan? It will be applied at the next safe step, restart planning, and require your approval again.",
        )
    elif row["kind"] == "control":
        row["reply"] = (
            "Use the Pause, Stop or Continue button beside the composer. Typing a control word does not execute it."
        )
    elif row["kind"] == "approval":
        row["reply"] = (
            "Use the approval card for the exact reviewed plan. Typed approval does not approve a plan or start work."
        )
    else:
        row["reply"] = status_reply(view)
    return row


def decide(row, decision, token, view, timestamp):
    if decision not in ("confirm", "question"):
        raise ValueError("Choose whether to change the plan or keep this as a question")
    confirmation = row.get("confirmation") or {}
    if not token or token != confirmation.get("token"):
        raise ValueError("This confirmation does not match the saved message")
    if confirmation.get("status") != "pending":
        if confirmation.get("decision") != decision:
            raise ValueError("This message already has a different saved decision")
        return row
    if confirmation.get("goal_token") != view.get("goal_token"):
        raise ValueError("The plan changed since this message. Send a new message to review the current plan.")
    row["confirmation"] = {
        **confirmation,
        "status": "confirmed" if decision == "confirm" else "declined",
        "decision": decision,
        "decided_at": timestamp,
    }
    row["kind"] = "correction" if decision == "confirm" else "question"
    row["status"] = "saved" if decision == "confirm" else "received"
    row["reply"] = (
        "Plan change confirmed. Delivery is recorded below; a new reviewed plan needs approval."
        if decision == "confirm"
        else status_reply(view)
    )
    return row
