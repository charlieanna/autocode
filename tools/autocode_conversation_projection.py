"""Pure public projection of a core conversation and accepted planning records.

Human messages live in the runner-owned journal. Accepted drafts, replies and
Reviewer findings stay in the existing task's durable planning records; the
projection joins them without promoting any assistant text to a requirement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

from copy import deepcopy

try:
    from . import autocode_conversation_policy as policy
    from . import autocode_roles as roles
except ImportError:
    import autocode_conversation_policy as policy
    import autocode_roles as roles

REVIEW_STAGES = ("astra_challenge", "astra_finalize", "plan_review", "plan_finalize")


def _records(state: RunState, planning, *, current_cycle):
    reports = dict(planning.get("reports") or {})
    if current_cycle and isinstance(state.get("requirements_handoff"), dict):
        reports.setdefault("requirements_gather", state["requirements_handoff"])
    records = []
    for stage, accepted in reports.items():
        if not isinstance(accepted, dict) or not isinstance(accepted.get("report"), dict):
            continue
        record = next(
            (
                row
                for row in reversed(state.get("stages", []))
                if isinstance(row, dict) and accepted.get("output") and row.get("output") == accepted["output"]
            ),
            {},
        )
        route = record.get("launch_route") or {}
        records.append(
            {
                "stage": stage,
                "role": roles.screen_name(stage, state),
                "model": route.get("model"),
                "engine": route.get("engine") or record.get("engine"),
                "provider": route.get("provider"),
                "reasoning_effort": record.get("reasoning_effort") or route.get("reasoning_effort"),
                "output": accepted.get("output"),
                "saved_output_sha256": record.get("sha256"),
                "artifact": deepcopy(accepted.get("artifact")),
                "report": deepcopy(accepted["report"]),
                "current_cycle": current_cycle,
                "availability": "saved_accepted"
                if record and route.get("model")
                else "saved_without_launch_attribution",
            }
        )
    return records


def project(
    state: RunState,
    journal,
    *,
    displayed_plan=None,
    needs=None,
    public_status=None,
    review_findings=None,
    review_scope_moves=None,
):
    if journal is not None and not isinstance(journal, dict):
        return {"availability": "unavailable", "reason": "Conversation journal is not an object"}
    if journal is None or ("session" not in journal and "session_error" not in journal):
        return None  # Existing dashboard/legacy journals retain their original interface.
    try:
        if journal.get("session_error"):
            raise ValueError(journal["session_error"])
        core = policy.session(journal)
        return _project(
            state,
            journal,
            core,
            displayed_plan=displayed_plan,
            needs=needs,
            public_status=public_status,
            review_findings=review_findings,
            review_scope_moves=review_scope_moves,
        )
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        return {"availability": "unavailable", "reason": str(error)}


def _project(
    state: RunState, journal, core, *, displayed_plan, needs, public_status, review_findings, review_scope_moves
):
    pointer = state.get("conversation_handoff") or {}
    if (pointer.get("conversation_id"), pointer.get("digest")) != (
        journal["conversation_id"],
        journal["handoff_digest"],
    ):
        raise ValueError("Conversation journal differs from the saved task source")
    actions = policy.acknowledgements(state, journal["conversation_id"])
    applied = set()
    applied_revision = core["applied_revision"]
    for action in actions:
        record = core["refreshes"].get(action.get("request_id"))
        if (
            not record
            or action.get("action_digest") != record.get("digest")
            or action.get("message_ids") != record.get("message_ids")
            or action.get("revision") != record.get("revision")
        ):
            raise ValueError("Saved conversation action cannot be reconciled")
        applied.update(action["message_ids"])
        applied_revision = max(applied_revision, action["revision"])
    pending = [
        row["message_id"]
        for row in core["messages"].values()
        if not row["applied"] and not row.get("superseded_by") and row["message_id"] not in applied
    ]
    planning = state.get("planning") or {}
    records = _records(state, planning, current_cycle=True)
    history = [
        _records(state, cycle, current_cycle=False)
        for cycle in state.get("planning_history", [])
        if isinstance(cycle, dict)
    ]
    review_records = [row for row in records if row["stage"] in REVIEW_STAGES]
    reviewed = bool(
        displayed_plan
        and planning.get("final_token") == displayed_plan["token"]
        and review_records
        and all(row["availability"] == "saved_accepted" for row in review_records)
    )
    current = not pending and applied_revision == core["revision"] and reviewed
    contract = state.get("goal_contract") or {}
    draft = None
    if isinstance(contract.get("body"), dict):
        draft = {
            "revision": contract.get("revision"),
            "hash": contract.get("hash"),
            "token": displayed_plan.get("token") if displayed_plan else None,
            "body": deepcopy(contract["body"]),
            "freshness": "current" if current else "stale",
        }
    findings: list = []
    for record in review_records:
        report = record["report"]
        for key in ("concerns", "decisions"):
            findings.extend(
                {"stage": record["stage"], "role": record["role"], **deepcopy(row)}
                for row in report.get(key, [])
                if isinstance(row, dict)
            )
    # Ordinary public TaskRun.answer/feedback remains compatible. These saved
    # human events are source evidence even when no conversation request sealed
    # them; a client never has to parse state.json to recover that transcript.
    source_events = [
        deepcopy(row)
        for row in state.get("user_events", [])
        if isinstance(row, dict) and row.get("kind") in ("brief_feedback", "answer")
    ]
    drafts = [
        {key: deepcopy(row.get(key)) for key in ("revision", "hash", "body")}
        for row in state.get("contract_history", [])
        if isinstance(row, dict)
    ]
    messages = deepcopy(journal["conversation"]["messages"])
    superseded = {
        row["message_id"]: row["superseded_by"] for row in core["messages"].values() if row.get("superseded_by")
    }
    for message in messages:
        if message["id"] in superseded:
            message["superseded_by"] = superseded[message["id"]]
    return {
        "availability": "available",
        **deepcopy(public_status or {}),
        "needs": deepcopy(needs or {}),
        "id": journal["conversation_id"],
        "original_handoff_digest": journal["handoff_digest"],
        "title": journal["conversation"]["title"],
        "revision": core["revision"],
        "applied_revision": applied_revision,
        "messages": messages,
        "rejections": deepcopy(core.get("rejections", {})),
        "saved_human_actions": source_events,
        "pending_message_ids": pending,
        "actions": actions,
        "refreshes": {
            # The view keeps the public name "status"; the journal's own field is "delivery", because a
            # refresh's delivery state is not the run's status (tests.test_architecture owns that name).
            key: {
                "request_id": row.get("request_id"),
                "revision": row.get("revision"),
                "status": row.get("delivery"),
                "question_id": row.get("question_id"),
            }
            for key, row in core["refreshes"].items()
        },
        "declared_seed_routes": deepcopy(journal["conversation"]["configured_routes"]),
        "planning_records": records,
        "planning_history": history,
        "draft_history": drafts,
        "current_draft": draft,
        "review": {
            "current": current,
            "records": review_records,
            "findings": deepcopy(review_findings or []),
            "finding_scope_moves": deepcopy(review_scope_moves or []),
            "reported_findings": findings,
        },
        "handoff_ready": current and (needs or {}).get("kind") == "approve_plan",
        "active": bool(state.get("active_stage") or state.get("active_runner_check")),
        "handed_off": deepcopy(core["handoffs"]),
    }
