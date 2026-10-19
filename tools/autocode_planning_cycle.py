"""A run's planning cycle: begin one, archive the one before it, and decide whether a Requirements handoff
renews it.

The cycle is the ``planning`` record: its Plan Reviewer charges, reports and final token. Pure steps over
the run state. The planning unit (units/autoplanner) re-exports them, so ``autocode_planning.start`` and the
others are the same functions as before. The goal lifecycle (autocode_goal_lifecycle) begins a cycle from
here when a drafted contract goes to plan review: it used to import the planning unit for that, and the
planning unit imports the goal lifecycle, which closed an import cycle.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_roles as role_names
except ImportError:
    import autocode_roles as role_names


def planning_inputs(state: RunState):
    """What may renew a v2 planning cycle, as sorted IDs: the user's answers and brief feedback (corrections,
    queued interventions and follow-ups) and a program's plan guidance. Model output adds none of them."""
    feedback = [row for row in state.get("brief_feedback") or [] if isinstance(row, dict) and row.get("id")]
    guidance = [row for row in state.get("program_guidance") or [] if isinstance(row, dict) and row.get("id")]
    return sorted(
        ["answer:" + str(key) for key in state.get("answers") or {}]
        + ["feedback:" + str(row["id"]) for row in feedback]
        + ["guidance:" + str(row["id"]) for row in guidance]
    )


def new_cycle(state: RunState):
    """Archive the planning record, with its reports and review charges, and begin a cycle under the configured
    review allowance. A v2 cycle records the inputs it began after (planning_inputs, read by admit_requirements)."""
    archived = state.get("planning")
    if archived:
        state.setdefault("planning_history", []).append(copy.deepcopy(archived))
    state["planning"] = {"astra_calls": 0, "reports": {}, "final_token": None}
    if archived and archived.get("review_depth_reduced"):
        # A small clear brief pays one total allowance across cycles (#1564): the
        # revision restarts the record, not the counter.
        state["planning"]["review_depth_reduced"] = True
        state["planning"]["review_calls_before_policy"] = archived.get("review_calls_before_policy", 0)
        state["planning"]["astra_calls"] = archived.get("astra_calls", 0)
    saved_review_limit = state.get("settings", {}).get("planning_review_call_limit")
    if type(saved_review_limit) is int and saved_review_limit == 0:
        state["planning"].update(review_call_limit=0, review_call_limit_origin="user_explicit")
    if role_names.planning_flow(state) == "v2":
        state["planning"]["inputs"] = planning_inputs(state)
    return state["planning"]


def admit_requirements(state: RunState, previous_token):
    """The planning record for the v2 Requirements handoff just accepted (#1252); ``previous_token`` is the
    requirements_artifact_token it replaced.

    A changed handoff after new input (planning_inputs: an answer, feedback, a follow-up or program guidance)
    begins a new cycle, as each Planner draft does on the legacy flow: corrective feedback on a reviewed plan
    otherwise left the new plan's first review refused for the old cycle's charges. The same handoff admitted
    again, or a different one with no new input (a retried or repaired report), keeps the cycle and its spent
    reviews, so model output never renews the allowance. A record saved before this rule has no inputs: it is
    bound to the current ones and keeps its charges."""
    planning = state.get("planning")
    if not planning:
        return new_cycle(state)
    inputs = planning_inputs(state)
    if not isinstance(planning.get("inputs"), list):
        planning["inputs"] = inputs
        return planning
    if state.get("requirements_artifact_token") != previous_token and set(inputs) - set(planning["inputs"]):
        return new_cycle(state)
    return planning


def start(state: RunState):
    """Begin (or restart) planning: archive the previous planning record and route to plan review."""
    try:
        from . import autocode_resolver_human as human
    except ImportError:
        import autocode_resolver_human as human
    new_cycle(state)
    state.pop(human.PRIVATE, None)
    state.pop(human.PUBLIC, None)
    state.pop("user_request", None)
    next_stage = "plan_review" if role_names.planning_flow(state) == "v2" else "astra_challenge"
    state.update({"status": "RUNNING", "phase": "PLANNING", "next_stage": next_stage, "pending_questions": []})
