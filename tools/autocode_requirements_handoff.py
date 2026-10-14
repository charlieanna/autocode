"""Accept a Requirements report (requirements_gather, default planning flow) as the run's handoff.

Checks the report, archives the previous handoff to ``requirements_history``, binds its local source
references (the caller's ``check_code_refs``, autopilot's source-inspection gate) and hands the run to
Discovery. A spec-first run stops for the user first (autocode_requirements_review, #1200).

Moved out of autopilot.apply_planning, so the controller does not grow; besides that stop, the only
change is that continuing goes through autocode_run_status, which also retires a stale stop reason.
State keys written: requirements_history, requirements_handoff, status/phase/next_stage,
discovery_summary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import copy

try:
    from . import autocode_goals as goals
    from . import autocode_requirements_review as requirements_review
    from . import autocode_run_status as run_status
except ImportError:
    import autocode_goals as goals
    import autocode_requirements_review as requirements_review
    import autocode_run_status as run_status


def accept(state: RunState, value: dict, record: dict, *, check_code_refs, queue) -> None:
    """Install ``value`` as the requirements handoff; ``queue`` is resolver_human.queue for the spec-first stop."""
    if not value["intended_outcome"].strip() or not value["required_behaviors"] or not value["acceptance_tests"]:
        raise ValueError("Requirements handoff needs an outcome, behaviors, and acceptance tests")
    questions = value["open_questions"]
    ids = [question["id"] for question in questions]
    if len(ids) != len(set(ids)) or any(not question_id.strip() for question_id in ids):
        raise ValueError("Requirements question IDs must be nonempty and unique")
    previous = state.get("requirements_handoff")
    if previous:
        state.setdefault("requirements_history", []).append(copy.deepcopy(previous))
    goals.check_requirement_handoff(state, value)
    input_refs = {"task", *state.get("answers", {})}
    input_refs.update(event["id"] for event in state.get("user_events", []) if event.get("id"))
    # External design/spec references may accompany local inspection evidence;
    # they are not filesystem paths and do not satisfy the source-inspection gate.
    local_refs = [
        ref for ref in value["source_refs"] if ref not in input_refs and not ref.startswith(("https://", "http://"))
    ]
    check_code_refs(state, local_refs, "source_refs")
    state["requirements_handoff"] = {"report": copy.deepcopy(value), "output": record["output"]}
    run_status.running(state, "PLANNING", next_stage="astra_discovery", discovery_summary=value["summary"])
    requirements_review.wait(state, record, queue)
