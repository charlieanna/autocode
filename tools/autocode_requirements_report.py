"""Canonical report normalizations for a Requirements response before schema validation.

An unanswered clarification cannot start planning: handoff acceptance waits for its
answer and returns to Requirements. Omitting the empty tests list here is therefore
formatting, not missing test evidence. Likewise, structured ``proposed_assumptions``
entries that state the assumption in ``rationale`` and omit ``text`` copy ``rationale``
into ``text`` while still rejecting empty assumptions. Work-bearing reports keep the
strict schema; the report loader preserves the raw response and validates the
canonical report.
"""


def backfill_assumption_text(report: dict) -> dict:
    """Populate a structured assumption's missing ``text`` from its non-empty ``rationale``."""
    if not isinstance(report, dict) or not isinstance(report.get("proposed_assumptions"), list):
        return report
    updated = False
    rows = []
    for row in report["proposed_assumptions"]:
        if (
            isinstance(row, dict)
            and not str(row.get("text") or "").strip()
            and isinstance(row.get("rationale"), str)
            and row["rationale"].strip()
        ):
            rows.append({**row, "text": row["rationale"].strip()})
            updated = True
        else:
            rows.append(row)
    return {**report, "proposed_assumptions": rows} if updated else report


def normalize(value, state, schema, record):
    stage = str(record.get("original_stage") or record.get("stage", "")).removesuffix("_report_repair")
    if stage == "requirements_gather" and isinstance(value, dict):
        backfilled = backfill_assumption_text(value)
        if backfilled is not value:
            value = backfilled
            record["defaulted_fields"] = sorted({*record.get("defaulted_fields", []), "proposed_assumptions.text"})
    if (
        stage != "requirements_gather"
        or state is None
        or state.get("goal_contract")
        or "acceptance_tests" in value
        or schema.get("properties", {}).get("acceptance_tests", {}).get("type") != "array"
        or value.get("required_behaviors") != []
    ):
        return value
    questions = value.get("open_questions")
    if not isinstance(questions, list) or not any(
        isinstance(row, dict)
        and isinstance(row.get("id"), str)
        and row["id"].strip()
        and row["id"] not in (state.get("answers") or {})
        and isinstance(row.get("question"), str)
        and row["question"].strip()
        for row in questions
    ):
        return value
    record["defaulted_fields"] = sorted({*record.get("defaulted_fields", []), "acceptance_tests"})
    return {**value, "acceptance_tests": []}
