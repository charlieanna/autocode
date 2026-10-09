"""Ground report repairs in saved context without rewriting execution history."""
from __future__ import annotations

import copy

PLANNING_STAGES = frozenset({"requirements_gather", "astra_discovery", "glm_revise", "astra_finalize"})
DECISION_STAGES = frozenset({"astra_plan", "astra_review", "astra_checkpoint", "astra_resolve"})


def baseline_instruction(stage: str) -> str:
    """Distinguish planning and proposed tasks from immutable execution history."""
    if stage in PLANNING_STAGES:
        return (
            "If supplied, original_report is historical planning context; rejected_report "
            "is the latest failed repair and error applies to that draft. Repair the latest "
            "draft against the current clarification context and protected contract. Do not "
            "restore invalid machine_resolutions or other rejected fields from original_report. "
        )
    if stage in DECISION_STAGES:
        return (
            "If supplied, original_report remains the execution-history baseline; rejected_report "
            "is the latest failed repair and error applies to that draft. Preserve executed commands, "
            "outcomes, Validator facts, findings, failures and uncertainty. The rejected proposed next_task "
            "is not executed history and may be corrected within the saved schema and existing approved scope "
            "(decision.next_task in a combined checkpoint report). Read goal_contract.body.milestones "
            "from the current approved contract through state_file: correct next_task.milestone_id only "
            "to an existing approved milestone ID covering the task's criterion IDs. "
            "Progressive slice IDs are never milestone IDs. For an implement/validate proposal, "
            "requirements, acceptance_criteria and validation_plan must be nonempty and derived from "
            "the existing approved scope and reviewed checks. Do not invent scope, milestones, "
            "unreviewed checks, PASS or citations. Do not change current_task or report_identity, "
            "grant authority or execute the proposed task during report repair. If the saved approved "
            "context does not establish a valid correction, preserve the uncertainty and report a blocker "
            "as the saved schema permits; do not guess an ID or default to M1. "
        )
    return (
        "If original_report is also supplied, it is the immutable execution-history baseline; "
        "rejected_report is the latest failed repair and error applies to that draft. "
    )


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
        question_ids.update(row.get("id") for row in investigation.get("questions", [])
                            if isinstance(row, dict))
    answers = state.get("answers") or {}
    feedback = state.get("brief_feedback") or []
    reports = (state.get("planning") or {}).get("reports") or {}
    predecessors = (("astra_discovery", "astra_challenge", "glm_revise") if stage == "astra_finalize"
                    else ("astra_discovery", "astra_challenge") if stage == "glm_revise" else ())
    return {
        "planning_exchange": {name: copy.deepcopy(reports[name]) for name in predecessors if name in reports},
        "requirements_handoff": handoff,
        "investigation_request": copy.deepcopy(investigation),
        "clarification_episode": copy.deepcopy(state.get("clarification_episode")),
        "previous_requirements": ((handoff or {}).get("report") or {}).get("requirements", [])
        if stage == "requirements_gather" else None,
        "required_source_quotes": {
            row["id"]: row["source_quote"]
            for row in ((handoff or {}).get("report") or {}).get("requirements", [])
            if isinstance(row, dict) and "id" in row and "source_quote" in row
        },
        "saved_answers": {key: copy.deepcopy(answers[key]) for key in question_ids if key in answers},
        "saved_feedback": [
            {key: event[key] for key in ("id", "actor", "text") if key in event}
            for event in feedback if isinstance(event, dict)
        ],
    }


def instruction(stage: str) -> str:
    """Explain how a planning report repair must use the saved clarification sources."""
    if stage not in PLANNING_STAGES:
        return ""
    if stage == "astra_finalize":
        return (
            "Planning report repair: use clarification_context.requirements_handoff, "
            "saved_answers, and saved_feedback as authoritative. "
            "The following contract and concern rules apply to ordinary final contract reports. "
            "For alternate progressive review schemas, follow only their saved fields; "
            "do not add contract, initial_task or decisions. "
            "planning_exchange.astra_challenge.report.concerns is the current review: "
            "address every saved concern ID exactly once in decisions. Do not invent a "
            "replacement concern ID or copy an older cycle's concerns. Use the saved "
            "Planner responses and source evidence for each substantive rationale. "
            "Each decision contains only concern_id, decision, rationale, acceptance_test, resolved. "
            "For an ordinary final contract report, put the initial task only in "
            "contract.initial_task and unresolved questions in contract.open_blocking_questions. "
            "Remove a root-level initial_task while preserving that task inside contract.initial_task; "
            "removing the extra root field alone still leaves the required nested task missing. "
            "Preserve each unresolved question unless its matching saved answer resolves it. "
            "Record saved human decisions and their exact source quotes in the summary and "
            "the existing contract fields that represent them; preserve the protected contract. "
            "Return only fields permitted by the saved stage schema. For the ordinary final "
            "contract schema, omit planner-only "
            "responses, code_refs, requirements, ignored_statements, machine_resolutions, "
            "access_blockers and remediation_records from this finalization report. "
            "Historical resolutions are context, not new resolutions. Do not invent "
            "answers, evidence, delegation or approval.\n"
        )
    return (
        "Planning report repair: use clarification_context.requirements_handoff, "
        "investigation_request, saved_answers, and saved_feedback as authoritative. "
        "For glm_revise and astra_finalize, planning_exchange.astra_challenge.report.concerns "
        "is the current review: address every saved concern ID exactly once in responses or "
        "decisions, with substantive changes and existing evidence_refs. Do not invent a "
        "replacement concern ID or copy an older cycle's concerns. The saved predecessor "
        "reports identify the draft and responses being reviewed. "
        "Preserve each unresolved question unless its matching saved answer resolves it; "
        "for requirements_gather, explicit saved feedback answering that question may "
        "also resolve it. Record human decisions in requirements and summary with their "
        "saved source quotes. Copy required_source_quotes for every existing requirement "
        "ID exactly, including partial-sentence boundaries and capitalization. Do not expand "
        "a prior source_quote to cover a checklist sentence; other requirement rows or "
        "explicit ignored_statements provide checklist coverage. Human decisions use their "
        "saved source quotes. Human answers and feedback are never machine_resolutions. Use "
        "machine_resolutions only when clarification_context has an investigation_request "
        "for this exact stage and question. Without that matching request, machine_resolutions "
        "and access_blockers must be []. Historical resolutions in a prior handoff are not "
        "new resolutions; do not copy them into this response. Do not invent answers or resolutions. "
        "For requirements_gather, every requirement_coverage_checklist sentence must appear "
        "verbatim as a requirement source_quote or in ignored_statements using its exact checklist "
        "text. Procedural report-format guidance may be explicitly ignored without dropping "
        "the task's behavioral requirements.\n"
    )
