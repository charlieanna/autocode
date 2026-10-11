"""Ground report repairs in saved context without rewriting execution history."""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import copy

try:
    from . import autocode_goals as goals
except ImportError:
    import autocode_goals as goals

PLANNING_STAGES = frozenset({"requirements_gather", "astra_discovery", "glm_revise", "astra_finalize"})
DECISION_STAGES = frozenset({"astra_plan", "astra_review", "astra_checkpoint", "astra_resolve"})


def baseline_instruction(stage: str) -> str:
    """Distinguish planning and proposed tasks from immutable execution history."""
    if stage in PLANNING_STAGES:
        return prompts.get("fragments/report-repair-context/baseline-instruction-02.md")
    if stage in DECISION_STAGES:
        return prompts.get("fragments/report-repair-context/baseline-instruction-03.md")
    return prompts.get("fragments/report-repair-context/baseline-instruction.md")


def clarification_context(state: dict, stage: str) -> dict:
    """Return the human and investigation context needed to repair a planning report."""
    if stage not in PLANNING_STAGES:
        return {}

    handoff = copy.deepcopy(state.get("requirements_handoff"))
    questions = ((handoff or {}).get("report") or {}).get("open_questions", [])
    investigation = state.get("investigation_request")
    if not isinstance(investigation, dict) or investigation.get("stage") != stage:
        investigation = None
    question_ids = {row.get("id") for row in questions if isinstance(row, dict)}
    if investigation:
        question_ids.update(investigation.get("all_question_ids") or investigation.get("question_ids") or [])
        question_ids.update(row.get("id") for row in investigation.get("questions", []) if isinstance(row, dict))
    answers = state.get("answers") or {}
    feedback = state.get("brief_feedback") or []
    reports = (state.get("planning") or {}).get("reports") or {}
    predecessors = (
        ("astra_discovery", "astra_challenge", "glm_revise")
        if stage == "astra_finalize"
        else ("astra_discovery", "astra_challenge")
        if stage == "glm_revise"
        else ()
    )
    return {
        "protected_contract": goals.protected_contract_snapshot(state) if stage in goals.PLANNER_ORIGINS else None,
        "planning_exchange": {name: copy.deepcopy(reports[name]) for name in predecessors if name in reports},
        "requirements_handoff": handoff,
        "investigation_request": copy.deepcopy(investigation),
        "clarification_episode": copy.deepcopy(state.get("clarification_episode")),
        "previous_requirements": ((handoff or {}).get("report") or {}).get("requirements", [])
        if stage == "requirements_gather"
        else None,
        "required_source_quotes": {
            row["id"]: row["source_quote"]
            for row in ((handoff or {}).get("report") or {}).get("requirements", [])
            if isinstance(row, dict) and "id" in row and "source_quote" in row
        },
        "saved_answers": {key: copy.deepcopy(answers[key]) for key in question_ids if key in answers},
        "saved_feedback": [
            {key: event[key] for key in ("id", "actor", "text") if key in event}
            for event in feedback
            if isinstance(event, dict)
        ],
    }


def repair_payloads(state: dict, stage: str) -> tuple[dict, dict | None]:
    """Separate clarification and approved-contract handoffs without copying state twice."""
    clarification = clarification_context(state, stage)
    protected_contract = clarification.pop("protected_contract", None)
    return clarification, protected_contract


def instruction(stage: str) -> str:
    """Explain how a planning report repair must use the saved clarification sources."""
    if stage not in PLANNING_STAGES:
        return ""
    if stage == "astra_finalize":
        return prompts.get("fragments/report-repair-context/instruction-02.md")
    return prompts.get("fragments/report-repair-context/instruction.md")
