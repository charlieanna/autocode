"""Accept a Requirements report (requirements_gather, default planning flow) as the run's handoff.

Checks the report, archives the previous handoff to ``requirements_history``, binds its local source
references (the caller's ``check_code_refs``, autopilot's source-inspection gate) and hands the run to
Discovery. A spec-first run, or a handoff that has only questions so far, stops for the user first
(autocode_requirements_review, #1200), except a question Requirements understood, which the Analyst
answers at once (autocode_requirements_chat.answer_now, #1846). With no stop, the job the report
names starts (autocode_requirements_chat.start_named_job, #1860).

Moved out of autopilot.apply_planning, so the controller does not grow; besides that stop, the only
change is that continuing goes through autocode_run_status, which also retires a stale stop reason.
State keys written: requirements_history, requirements_handoff (with its validated workspace_scope
and any dropped_external_parts, autocode_workspace_scope), status/phase/next_stage, discovery_summary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import copy
from pathlib import Path

try:
    from . import autocode_coordination_context as coordination_context
    from . import autocode_goals as goals
    from . import autocode_plan_chat as plan_chat
    from . import autocode_project_memory_lifecycle as project_memory
    from . import autocode_requirements_chat as requirements_chat
    from . import autocode_requirements_review as requirements_review
    from . import autocode_run_status as run_status
    from . import autocode_technical_questions as technical_questions
    from . import autocode_util as util
    from . import autocode_workspace_scope as workspace_scope
except ImportError:
    import autocode_coordination_context as coordination_context
    import autocode_goals as goals
    import autocode_plan_chat as plan_chat
    import autocode_project_memory_lifecycle as project_memory
    import autocode_requirements_chat as requirements_chat
    import autocode_requirements_review as requirements_review
    import autocode_run_status as run_status
    import autocode_technical_questions as technical_questions
    import autocode_util as util
    import autocode_workspace_scope as workspace_scope


def accept(state: RunState, value: dict, record: dict, *, check_code_refs, queue) -> None:
    """Install ``value`` as the requirements handoff; ``queue`` is resolver_human.queue for the spec-first stop."""
    if requirements_review.enabled(state) and not_yet_a_goal(value):
        value["open_questions"] = [outcome_question(state)]
    questions = value["open_questions"]
    # Requirements keeps behaviors and acceptance tests empty while it is still clarifying (its prompt says
    # so). Such a questions-only handoff is accepted on every flow; the run then waits for the answers (wait,
    # below) instead of refusing it as invalid output, as the default flow did.
    clarification_only = bool(questions) and (
        requirements_review.enabled(state) or requirements_review.questions_only(value)
    )
    intake_only = (
        requirements_review.enabled(state)
        and clarification_only
        and not any(value[key] for key in ("requirements", "required_behaviors", "acceptance_tests"))
    )
    # A chat that has not named any software outcome yet ("what is your name?")
    # may say so honestly, provided it asks the person for that outcome.
    asks_outcome = intake_only and any(question.get("category") == "requested_outcome" for question in questions)
    # Behaviors and acceptance tests describe code to build. A review, design or question names its
    # outcome and nothing else; its own stage checks the result (#1846).
    builds_code = requirements_review.builds_code(requirements_review.job(state, value))
    if (not value["intended_outcome"].strip() and not asks_outcome) or (
        builds_code and not clarification_only and (not value["required_behaviors"] or not value["acceptance_tests"])
    ):
        raise ValueError("Requirements handoff needs an outcome, behaviors, and acceptance tests")
    ids = [question["id"] for question in questions]
    if len(ids) != len(set(ids)) or any(not question_id.strip() for question_id in ids):
        raise ValueError("Requirements question IDs must be nonempty and unique")
    # Validate the reported scope before any state is written (#2066 grounding, and a
    # coordinated member's role-noun label refused so the bounded report repair
    # attempts the correction, #2420; never a silent drop).
    coordinated = coordination_context.context(state)
    try:
        from . import autocode_catalog_context as source_catalog
    except ImportError:
        import autocode_catalog_context as source_catalog
    dropped = None
    if "workspace_scope" in value:
        value["workspace_scope"], dropped = workspace_scope.validated(
            value["workspace_scope"],
            sources=goals.source_texts(state),
            source_refs=value.get("source_refs"),
            workspace=state.get("workspace"),
            member_names=tuple(coordinated["members"]) if coordinated else (),
            evidence_resolver=lambda ref: source_catalog.evidence(state, ref),
        )
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
    catalog_refs = [ref for ref in local_refs if ref.startswith("catalog/")]
    if any(not source_catalog.evidence(state, ref) for ref in catalog_refs):
        raise ValueError("Requirements source_refs contains an unverified catalog source citation")
    local_refs = [ref for ref in local_refs if ref not in catalog_refs]
    # User requirements and constraints can be retained while asking what the
    # outcome means. Until there are behaviors or acceptance tests, that intent
    # clarification needs no unrelated source citation. A complete handoff and
    # every explicitly supplied source reference still go through inspection.
    intent_only = clarification_only and not any(value[key] for key in ("required_behaviors", "acceptance_tests"))
    if local_refs or (builds_code and not intent_only and not catalog_refs):
        check_code_refs(state, local_refs, "source_refs")
    state["requirements_handoff"] = {"report": copy.deepcopy(value), "output": record["output"]}
    if "workspace_scope" in value:
        workspace_scope.record(
            state, value["workspace_scope"], record, stage="requirements_gather", dropped=dropped or []
        )
    if dropped:
        state["requirements_handoff"]["dropped_external_parts"] = dropped
    project_memory.capture(state, value)
    remaining = technical_questions.settle(state, questions, "requirements:" + goals.handoff_ref(state))
    if len(remaining) != len(questions):
        run_status.running(state, "DISCOVERING", next_stage="requirements_gather", discovery_summary=value["summary"])
        return
    run_status.running(state, "PLANNING", next_stage="astra_discovery", discovery_summary=value["summary"])
    if requirements_chat.answer_now(state, record):
        return
    if requirements_review.questions(state):
        requirements_review.wait(state, record, queue)
        return
    # Nothing waits for the person (a plain CLI run, a follow-up turn): start the job Requirements named.
    requirements_chat.start_named_job(state, record)


OUTCOME_QUESTION = "What would you like me to help you build, change, fix, or review in this project?"


def not_yet_a_goal(value: dict) -> bool:
    """A chat reply that names no software outcome and asks nothing ("Nothing yet, just saying hello").

    Requirements answered correctly, but a handoff needed either an outcome or a question, so the reply was
    refused, repaired twice (the second invented a source reference) and sent to the Investigator.
    """
    return bool(str(value.get("assistant_reply") or "").strip()) and not (
        str(value.get("intended_outcome") or "").strip()
        or any(value.get(key) for key in ("requirements", "required_behaviors", "acceptance_tests", "open_questions"))
    )


def outcome_question(state: RunState) -> dict:
    """The runner's own requested-outcome question, under an id no earlier answer used."""
    return {
        "id": "outcome-"
        + util.digest(
            {"round": len(state.get("requirements_history") or []), "answers": sorted(state.get("answers") or {})}
        )[:12],
        "question": OUTCOME_QUESTION,
        "why": "No software outcome has been named yet.",
        "options": [],
        "proposed_default": "",
        "kind": "decision",
        "category": "requested_outcome",
        "delegable": False,
    }


def accept_chat(state: RunState, value: dict, record: dict, *, queue) -> bool:
    """Answer a plan message or forward an actual correction through normal review."""
    if not plan_chat.pending(state):
        return False
    event = plan_chat.accept(state, value, record)
    if event["intent"] == "change":
        goals.feedback(state, event["text"], interpret=False)
        state["brief_feedback"][-1]["reply_after"] = event["reply_after"]
        event["forwarded"] = True
    elif not event.get("return_pause"):
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
