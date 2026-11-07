"""Ground a Planner's late repository discovery before accepting its local plan.

This records work, never an assignment, host path or implementation approval.
The existing scope boundary and coordinator own the resulting named actions.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_brief_obligations as brief_obligations
    from . import autocode_catalog_context as catalog
    from . import autocode_coordination_context as coordination
    from . import autocode_goals as goals
    from . import autocode_planning_clarification as clarification
    from . import autocode_prompts as prompts
    from . import autocode_run_status as run_status
    from . import autocode_scope_question as scope_question
    from . import autocode_source_scope as source_scope
    from . import autocode_source_trace as source_trace
    from . import autocode_util as util
    from . import autocode_workspace_scope as workspace_scope
except ImportError:
    import autocode_brief_obligations as brief_obligations
    import autocode_catalog_context as catalog
    import autocode_coordination_context as coordination
    import autocode_goals as goals
    import autocode_planning_clarification as clarification
    import autocode_prompts as prompts
    import autocode_run_status as run_status
    import autocode_scope_question as scope_question
    import autocode_source_scope as source_scope
    import autocode_source_trace as source_trace
    import autocode_util as util
    import autocode_workspace_scope as workspace_scope

STAGES = ("astra_discovery", "glm_revise")


def prepare_handoff(state: RunState, stage, value, record, *, investigation_stages, check_code_refs):
    """Settle clarification obligations before admitting a draft's repository scope."""
    pending = (state.get("workspace_scope") or {}).get("pending_handoff")
    if pending and pending["stage"] == stage and pending["record"] == record:
        _check_pending(state, pending)
        if scope_question.decision(state) == "linked_repositories":
            expected = deepcopy(pending["report"])
            # Normal report expansion adds authenticated peer exclusions after
            # assignment. It must not repeat the clarification already consumed
            # before this scope pause; no other report change is accepted here.
            expected["contract"] = scope_question.retain_assigned_exclusions(
                state, expected["contract"], expected.get("contract_changes") or []
            )
            if value in (pending["report"], expected):
                state["workspace_scope"].pop("pending_handoff")
                return deepcopy(value), False
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
    # A deferred report leaves the normal completed-stage rejection path. Check
    # its citations now so malformed output gets bounded report-only repair
    # before a coordinator is offered an assignment it cannot apply.
    source_trace.check_citations(value.get("contract") or {}, brief_obligations.sources(state))
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
        output = Path(record.get("output") or "")
        if output.is_file():
            state["workspace_scope"]["pending_handoff"] = {
                "stage": stage,
                "report": deepcopy(value),
                "record": deepcopy(record),
                "output_sha256": util.file_hash(output),
                "binding": _binding(state),
            }
        return True
    return False


# Only this completed handoff is reusable. A new human input, source change or
# contract/settings/runtime change must pass through normal planning again.
def _binding(state: RunState):
    runtime = Path(__file__).resolve().parent
    keys = (
        "task_id",
        "task",
        "workspace",
        "run_dir",
        "turns",
        "settings",
        "answers",
        "user_events",
        "brief_feedback",
        "requirements_handoff",
        "goal_contract",
        "current_task",
        "planning",
        "machine_resolutions",
        "clarification_episode",
        "deferred_obligations",
    )
    return util.digest(
        {
            "context": {key: state.get(key) for key in keys},
            "scope": workspace_scope.identity(state),
            "source": source_scope.snapshot(Path(state["workspace"]), state)["revision"],
            "prompts": prompts.HASH,
            "runtime": {str(path.relative_to(runtime)): util.file_hash(path) for path in sorted(runtime.rglob("*.py"))},
        }
    )


def _check_pending(state: RunState, pending):
    output = Path(pending["record"]["output"])
    if (
        not output.is_file()
        or util.file_hash(output) != pending["output_sha256"]
        or _binding(state) != pending["binding"]
    ):
        raise ValueError("The saved Planner scope handoff changed; it cannot be reused")


def resume(state: RunState, *, apply):
    """Apply one completed report after its exact assignment, without a model call.

    The controller callback still applies all normal draft/revision checks. Only
    already-applied clarification and scope bookkeeping is consumed once.
    """
    pending = (state.get("workspace_scope") or {}).get("pending_handoff")
    if not pending:
        return False
    _check_pending(state, pending)
    if scope_question.decision(state) != "linked_repositories":
        return False
    candidate = deepcopy(state)
    apply(candidate, pending["stage"], deepcopy(pending["report"]), deepcopy(pending["record"]))
    candidate["workspace_scope"].pop("pending_handoff", None)
    candidate["workspace_scope"]["resumed_handoff"] = {
        "stage": pending["stage"],
        "output": pending["record"]["output"],
        "output_sha256": pending["output_sha256"],
        "scope_identity": workspace_scope.identity(state),
    }
    cast(dict, state).clear()
    state.update(candidate)
    return True
