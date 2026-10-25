"""Ground report repairs in saved context without rewriting execution history."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import copy
import json

try:
    from . import autocode_goals as goals
    from . import autocode_plan_retention as retention
    from . import autocode_regression_reference as regression_reference
    from . import autocode_roles as roles
except ImportError:
    import autocode_goals as goals
    import autocode_plan_retention as retention
    import autocode_regression_reference as regression_reference
    import autocode_roles as roles

PLANNING_STAGES = frozenset(roles.ALL_PLANNING_STAGES)
DECISION_STAGES = frozenset({"astra_plan", "astra_review", "astra_checkpoint", "astra_resolve"})


def requirements_sources(state: RunState, stage: str) -> dict:
    """Give a Requirements repair the saved IDs needed to cite a user correction."""
    if stage != "requirements_gather":
        return {"previous_requirements": None}
    sources = [
        {"basis": "user_feedback", "event_id": event["id"], "text": event.get("text", "")}
        for event in state.get("brief_feedback", [])
        if event.get("id") and event in state.get("user_events", [])
    ]
    sources.extend(
        {"basis": "user_answer", "event_id": key, "text": value.get("text", "") if isinstance(value, dict) else value}
        for key, value in (state.get("answers") or {}).items()
    )
    return {
        "previous_requirements": copy.deepcopy(
            ((state.get("requirements_handoff") or {}).get("report") or {}).get("requirements", [])
        ),
        "requirements_correction_sources": sources,
    }


def baseline_instruction(stage: str) -> str:
    """Distinguish planning and proposed tasks from immutable execution history."""
    if stage == "requirements_gather":
        return prompts.get("fragments/report-repair-context/baseline-instruction-02.md") + prompts.get(
            "fragments/report-repair-context/requirements-corrections.md"
        )
    if stage in PLANNING_STAGES:
        return prompts.get("fragments/report-repair-context/baseline-instruction-02.md") + prompts.get(
            "fragments/autoplanner/proof-reference-rule.md"
        )
    if stage in DECISION_STAGES:
        return prompts.get("fragments/report-repair-context/baseline-instruction-03.md")
    return prompts.get("fragments/report-repair-context/baseline-instruction.md")


def clarification_context(state: RunState, stage: str) -> dict:
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
        **(
            {"retained_contract": retention.token(state), "retained_criteria": retention.inventory(state)}
            if retention.token(state)
            else {}
        ),
        "protected_contract": goals.protected_contract_snapshot(state) if stage in goals.PLANNER_ORIGINS else None,
        "planning_exchange": planning_exchange(reports, predecessors),
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


def planning_exchange(reports: dict, predecessors: tuple[str, ...]) -> dict:
    """The predecessor reports a plan-finalize repair sees, with the draft under review included once.

    The newest predecessor contract is the draft the rejected stage reviewed; an earlier one is
    superseded. The stage prompt includes the current contract once (units/autoplanner.context);
    a program child's plan-finalize repair that carried both drafts in full passed the 256 KiB
    repair cap (#1337). A superseded report keeps every other field, and its whole text stays at
    its output path."""
    exchange = {name: copy.deepcopy(reports[name]) for name in predecessors if name in reports}
    drafts = [
        entry["report"]
        for entry in exchange.values()
        if isinstance(entry, dict) and isinstance(entry.get("report"), dict) and "contract" in entry["report"]
    ]
    for report in drafts[:-1]:
        del report["contract"]
    return exchange


def repair_payloads(state: RunState, stage: str) -> tuple[dict, dict | None]:
    """Separate clarification and approved-contract handoffs without copying state twice."""
    clarification = clarification_context(state, stage)
    protected_contract = clarification.pop("protected_contract", None)
    return clarification, protected_contract


def criterion_retention(clarification: dict, stage: str) -> tuple[str, dict]:
    """Author repairs need exact current references, without repeating earlier plan reports."""
    if stage not in retention.AUTHORS - retention.FINAL or not clarification.get("retained_contract"):
        return "", {}
    return retention.RULE, {
        "retained_criteria": copy.deepcopy(clarification["retained_criteria"]),
        "retained_contract": clarification["retained_contract"],
    }


def _original_overlay(original, latest) -> dict:
    """Losslessly encode an older JSON value relative to the complete current value."""
    delta: dict = {"remove": [], "replace": []}

    def compare(old, new, path):
        if isinstance(old, dict) and isinstance(new, dict):
            delta["remove"].extend([*path, key] for key in new if key not in old)
            for key, value in old.items():
                if key in new:
                    compare(value, new[key], [*path, key])
                else:
                    delta["replace"].append({"path": [*path, key], "value": copy.deepcopy(value)})
        elif isinstance(old, list) and isinstance(new, list) and len(old) == len(new):
            for index, (before, after) in enumerate(zip(old, new, strict=True)):
                compare(before, after, [*path, index])
        elif type(old) is not type(new) or old != new:
            delta["replace"].append({"path": path, "value": copy.deepcopy(old)})

    compare(original, latest, [])
    return delta


def historical_author_report(stage: str, original: dict, latest: dict | None) -> tuple[str, dict | None]:
    """Keep every older author value inline without duplicating unchanged JSON subtrees."""
    if latest is None:
        return "", None
    if (
        stage not in retention.AUTHORS - retention.FINAL
        or any(source.get("format") != "json" or source.get("truncated") for source in (original, latest))
        or any(not isinstance(source.get("content"), dict) for source in (original, latest))
    ):
        return "", original
    reference = {key: copy.deepcopy(value) for key, value in original.items() if key != "content"}
    reference["content_base"] = "rejected_report.content"
    reference["content_delta"] = _original_overlay(original["content"], latest["content"])
    if len(json.dumps(reference, indent=2)) >= len(json.dumps(original, indent=2)):
        return "", original
    return (
        "HISTORICAL PLANNING DRAFT. original_report encodes its complete older JSON inline relative to "
        "rejected_report.content: copy that base, remove each content_delta.remove path, then replace each "
        "content_delta.replace path with its exact supplied value. Path arrays name object keys or list indices; "
        "an empty path replaces the root. No file access is needed. The original artifact path/hash stay pinned. "
        "The complete latest rejected_report and protected_contract remain below; repair the latest draft, "
        "not the superseded one.\n",
        reference,
    )


def sources(pending: dict, read_source) -> tuple[dict, dict]:
    """Load the original and current pinned report once, in their existing order."""
    original = read_source(pending["original"])
    latest = read_source(pending["latest_rejected"]) if pending.get("latest_rejected") else original
    return original, latest


def prompt_parts(
    state: RunState, pending: dict, original: dict, latest: dict, receipt_parts: tuple
) -> tuple[str, dict]:
    """Assemble bounded planning context while keeping receipt policy with its caller."""
    stage = pending["original"]["stage"]
    clarification, protected = repair_payloads(state, stage)
    retention_instruction, references = criterion_retention(clarification, stage)
    historical_instruction, historical_source = historical_author_report(
        stage, original, latest if pending.get("latest_rejected") else None
    )
    receipt_instruction, receipt_handoff = receipt_parts
    interpretations = goals.feedback_interpretation_context(state) if stage in PLANNING_STAGES else {}
    return receipt_instruction + retention_instruction + historical_instruction, {
        "header": {
            **receipt_handoff,
            **references,
            **({"feedback_interpretations": interpretations} if interpretations else {}),
            **({"regression_reference": regression_reference.context(state)} if stage in PLANNING_STAGES else {}),
        },
        "original_report": historical_source,
        "protected_contract": protected,
        "clarification": {"clarification_context": clarification} if stage == "astra_finalize" else {},
    }


def instruction(stage: str) -> str:
    """Explain how a planning report repair must use the saved clarification sources."""
    if stage not in PLANNING_STAGES:
        return ""
    if stage == "astra_finalize":
        return prompts.get("fragments/report-repair-context/instruction-02.md")
    return prompts.get("fragments/report-repair-context/instruction.md")
