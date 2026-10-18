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
from pathlib import Path

try:
    from . import autocode_goals as goals
    from . import autocode_plan_chat as plan_chat
    from . import autocode_project_memory_lifecycle as project_memory
    from . import autocode_requirements_review as requirements_review
    from . import autocode_run_status as run_status
    from . import autocode_technical_questions as technical_questions
    from . import autocode_util as util
except ImportError:
    import autocode_goals as goals
    import autocode_plan_chat as plan_chat
    import autocode_project_memory_lifecycle as project_memory
    import autocode_requirements_review as requirements_review
    import autocode_run_status as run_status
    import autocode_technical_questions as technical_questions
    import autocode_util as util


def accept(state: RunState, value: dict, record: dict, *, check_code_refs, queue) -> None:
    """Install ``value`` as the requirements handoff; ``queue`` is resolver_human.queue for the spec-first stop."""
    questions = value["open_questions"]
    clarification_only = requirements_review.enabled(state) and bool(questions)
    intake_only = clarification_only and not any(
        value[key] for key in ("requirements", "required_behaviors", "acceptance_tests")
    )
    # A chat that has not named any software outcome yet ("what is your name?")
    # may say so honestly, provided it asks the person for that outcome.
    asks_outcome = intake_only and any(question.get("category") == "requested_outcome" for question in questions)
    if (not value["intended_outcome"].strip() and not asks_outcome) or (
        not clarification_only and (not value["required_behaviors"] or not value["acceptance_tests"])
    ):
        raise ValueError("Requirements handoff needs an outcome, behaviors, and acceptance tests")
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
    # Asking the user for a goal makes no code claim. Do not demand a citation
    # to unrelated editor settings merely because this is a nonempty checkout.
    if local_refs or not intake_only:
        check_code_refs(state, local_refs, "source_refs")
    state["requirements_handoff"] = {"report": copy.deepcopy(value), "output": record["output"]}
    project_memory.capture(state, value)
    remaining = technical_questions.settle(state, questions, "requirements:" + goals.handoff_ref(state))
    if len(remaining) != len(questions):
        run_status.running(state, "DISCOVERING", next_stage="requirements_gather", discovery_summary=value["summary"])
        return
    run_status.running(state, "PLANNING", next_stage="astra_discovery", discovery_summary=value["summary"])
    requirements_review.wait(state, record, queue)


def accept_chat(state: RunState, value: dict, record: dict, *, queue) -> bool:
    """Answer a plan message or forward an actual correction through normal review."""
    if not plan_chat.pending(state):
        return False
    event = plan_chat.accept(state, value, record)
    if event["intent"] == "change":
        goals.feedback(state, event["text"], interpret=False)
        state["brief_feedback"][-1]["reply_after"] = event["reply_after"]
        event["forwarded"] = True
    else:
        output = record.get("output")
        evidence = {"hashes": {output: util.file_hash(Path(output))} if output and Path(output).is_file() else {}}
        queue(
            state,
            "goal_approval",
            {"stage": "requirements_gather", "output": output},
            evidence=evidence,
            status="AWAITING_GOAL_APPROVAL",
            phase="AWAITING_GOAL_APPROVAL",
            next_stage=event["return_stage"],
        )
    return True
