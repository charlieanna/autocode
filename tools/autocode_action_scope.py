"""Apply only an exact core-owned assignment of the current external scope."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_resolver_human as human
    from . import autocode_run_status as run_status
    from . import autocode_scope_assignment as assignment
    from . import autocode_workflows as workflows
except ImportError:
    import autocode_resolver_human as human
    import autocode_run_status as run_status
    import autocode_scope_assignment as assignment
    import autocode_workflows as workflows


def apply(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    requested = getattr(args, "assign_linked_scope", None)
    if not requested:
        return
    receipt = assignment.current(state)
    if not receipt or requested != receipt["identity"] or state.get("active_stage"):
        raise ValueError("Repository assignment is stale or incomplete; no question or approval was changed")
    prefix = "workspace-scope-" + requested
    questions = state.get("pending_questions") or []
    matching = [row for row in questions if row.get("id") == prefix]
    if not matching:
        # Idempotent retry after the exact assignment was already applied.
        if not any(str(row.get("id", "")).startswith("workspace-scope-") for row in questions):
            return 0
        raise ValueError("The current repository question differs from the assigned scope")
    published = human.current(state)
    if not published or published.get("scope") not in ("clarification", "intake"):
        raise ValueError("The scope assignment cannot release this human gate")
    remaining = [row for row in questions if row not in matching]
    human.supersede_assigned_scope(state, published, remaining, receipt)
    if not remaining:
        run_status.running(state, "PLANNING", next_stage=workflows.planner_stage(state))
    runner.write_json(state_path, state)
    print("External work assigned to linked repository runs; plan approval remains required.")
    return 0
