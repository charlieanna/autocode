"""Canonical empty tests for a Requirements interview that has drafted no behavior.

An unanswered clarification cannot start planning: handoff acceptance waits for its
answer and returns to Requirements. Omitting the empty tests list here is therefore
formatting, not missing test evidence. Work-bearing reports keep the strict schema.
The report loader preserves the raw response and validates the canonical report.
"""


def normalize(value, state, schema, record):
    stage = str(record.get("original_stage") or record.get("stage", "")).removesuffix("_report_repair")
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
