"""Ground a Planner's late repository discovery before accepting its local plan.

This records work, never an assignment, host path or implementation approval.
The existing scope boundary and coordinator own the resulting named actions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_catalog_context as catalog
    from . import autocode_coordination_context as coordination
    from . import autocode_goals as goals
    from . import autocode_planning_clarification as clarification
    from . import autocode_run_status as run_status
    from . import autocode_scope_question as scope_question
    from . import autocode_workspace_scope as workspace_scope
except ImportError:
    import autocode_catalog_context as catalog
    import autocode_coordination_context as coordination
    import autocode_goals as goals
    import autocode_planning_clarification as clarification
    import autocode_run_status as run_status
    import autocode_scope_question as scope_question
    import autocode_workspace_scope as workspace_scope

STAGES = ("astra_discovery", "glm_revise")


def prepare_handoff(state: RunState, stage, value, record, *, investigation_stages, check_code_refs):
    """Settle clarification obligations before admitting a draft's repository scope."""
    value, deferred = clarification.clarify_discoverable(
        state, stage, value, record, investigation_stages=investigation_stages, check_code_refs=check_code_refs
    )
    if deferred:
        return value, True
    clarification.apply_obligations(state, stage, value, check_code_refs=check_code_refs)
    return value, accept(state, stage, value, record)


def accept(state: RunState, stage: str, value: dict, record: dict) -> bool:
    """Return whether grounded external work must be assigned before this draft."""
    if stage not in STAGES or "workspace_scope" not in value:
        return False
    coordinated = coordination.context(state)
    scope, dropped = workspace_scope.validated(
        value["workspace_scope"],
        sources=goals.source_texts(state),
        source_refs=value.get("code_refs"),
        workspace=state.get("workspace"),
        member_names=tuple(coordinated["members"]) if coordinated else (),
        evidence_resolver=lambda ref: catalog.evidence(state, ref),
    )
    workspace_scope.record(state, scope, record, stage=stage, dropped=dropped)
    if scope_question.question(state):
        run_status.running(state, "DISCOVERING", next_stage=stage, discovery_summary=value["summary"])
        run_status.pause(state, "PAUSED_SCOPE_DECISION", "New external work needs the current repository assignment.")
        return True
    return False
