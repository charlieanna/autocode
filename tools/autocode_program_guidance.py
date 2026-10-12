"""A program's correction of a child run's displayed plan, saved as program guidance (#866).

A program (autocode_program) rejects a child's draft plan that drops an inherited requirement or
boundary, and tells the child what to keep through TaskRun.guide_plan (``--program-guidance TEXT
--review-token -``). That text is the program's, not the user's, so it is saved under
``program_guidance`` with actor ``program``, never in ``user_events`` or ``brief_feedback``: it is no
requirement source a contract must quote or ignore (autocode_requirement_cues), no saved user event a
decision may cite (autocode_goals), no human declaration (autocode_brief_obligations), and it starts
no clarification episode, so it renews no investigation allowance. The planning stages read it as
the program's (units.autoplanner).

It applies only to the plan the child displayed, named by that plan's token, and sends the run back
to requirements the way brief feedback does; the next plan needs approval as before.

State: ``program_guidance`` is the list of saved corrections; this module is its only writer.
"""

from __future__ import annotations

import copy
import uuid

try:
    from . import autocode_goals as goals
    from . import autocode_util as util
except ImportError:
    import autocode_goals as goals
    import autocode_util as util

KIND = "program_guidance"


def apply(state, text, selected):
    """Save the program's correction of the displayed plan ``selected`` and send the run back to planning."""
    text = (text or "").strip()
    if state.get("status") != "AWAITING_GOAL_APPROVAL" or not text:
        raise ValueError("Program guidance needs nonempty text for a plan awaiting approval")
    goals.check_displayed(state, selected)
    event = {
        "kind": KIND,
        "id": "guidance-" + uuid.uuid4().hex[:12],
        "actor": "program",
        "at": util.now(),
        "text": text,
        "contract_token": selected,
    }
    state.setdefault("program_guidance", []).append(event)
    # As brief feedback does: a pending report-only repair is bound to the plan being replaced.
    pending = state.pop("pending_report_repair", None)
    if pending:
        state.setdefault("report_repair_archive", []).append(
            {"reason": "superseded_by_program_guidance", "guidance_id": event["id"], "repair": copy.deepcopy(pending)}
        )
    state["goal_contract"].update(approval_status="draft", approval_event=None)
    goals.invalidate(state, "Program guidance requires a refreshed draft and explicit approval")
    # The stage brief feedback restarts at (autocode_goals.feedback), without its adaptive shortcut.
    settings = state.get("settings") or {}
    stage = (
        "requirements"
        if settings.get("planning_flow") == "v2"
        else "requirements_gather"
        if "requirements" in settings.get("roles", {})
        else "astra_discovery"
    )
    state.update(status="RUNNING", phase="DISCOVERING", next_stage=stage, pending_questions=[])
    return event


def current(state):
    """The program's corrections of this turn's plans, for the planning stages; none once a plan is approved."""
    if goals.approved(state):
        return []
    turns = [turn for turn in state.get("turns") or [] if isinstance(turn, dict)]
    since = str(turns[-1].get("at") or "") if turns else ""
    return [
        {"id": event["id"], "text": event["text"]}
        for event in state.get("program_guidance") or []
        if isinstance(event, dict) and event.get("id") and event.get("text") and str(event.get("at") or "") >= since
    ]
