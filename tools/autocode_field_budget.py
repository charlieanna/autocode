"""Per-field byte budgets and fail-closed summarization for oversized handoff objects (#3347).

Utility layer: shrinks oversized derived handoff blobs into ``moved`` (for hash-pinned
context_artifact persistence) while keeping verdicts, failing checks, unsettled
obligations, and never-summarized requirement/finding fields verbatim inline.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import Any

MIN_SAVINGS = 1024

NEVER_SUMMARIZE: frozenset[str] = frozenset(
    {
        "acceptance_criteria",
        "clarification_episode",
        "goal_contract",
        "open_findings",
        "requirements",
        "saved_answers",
        "task",
        "unresolved_findings",
        "user_events",
        "verification_obligations",
    }
)

FIELD_BUDGETS: dict[str, dict[str, Any]] = {
    "regression_proof": {
        "bytes": 12288,
        "stages": frozenset({"review_completion", "review_checkpoint"}),
    },
    "validation": {
        "bytes": 12288,
        "stages": frozenset({"review_completion", "review_checkpoint", "plan_task"}),
    },
    "contract_author_rules": {
        "bytes": 8192,
        "stages": frozenset({"review_plan", "plan_review", "finalize_plan", "plan_finalize"}),
    },
    "changed_code_evidence": {
        "bytes": 6144,
        "stages": frozenset({"builder"}),
    },
}

_PASSING_STATES = frozenset({"PASS", "pass", "passed", "ok", "accepted", "settled", "tested", "excepted", "killed"})


def _json_bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def _is_passing_row(row: Mapping[str, Any]) -> bool:
    if row.get("passed") is False or row.get("settled") is False or row.get("killed") is False:
        return False
    exit_code = row.get("exit_code")
    if isinstance(exit_code, int) and exit_code != 0:
        return False
    state_vals = [row.get(k) for k in ("verdict", "status") if row.get(k) is not None]
    if any(val not in _PASSING_STATES for val in state_vals):
        return False
    return bool(
        row.get("passed") is True
        or row.get("settled") is True
        or row.get("killed") is True
        or exit_code == 0
        or state_vals
    )


def _filter_list_field(rows: Any) -> tuple[list[dict[str, Any]], int] | None:
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return None
    kept = [row for row in rows if not _is_passing_row(row)]
    return kept, len(rows)


def _summarize_regression_proof(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not value:
        return None
    bulky = {
        "mutation_check",
        "changed_code_evidence",
        "changed_code_receipts",
        "criteria",
        "criterion_checks",
        "checks",
        "tests",
        "files",
    }
    summary: dict[str, Any] = {k: copy.deepcopy(v) for k, v in value.items() if k not in bulky}
    for key in ("criteria", "criterion_checks", "checks", "tests"):
        if key not in value:
            continue
        filtered = _filter_list_field(value[key])
        if filtered is None:
            return None
        kept, total = filtered
        summary[key] = kept
        summary[f"{key}_total"] = total
    if "changed_code_receipts" in value:
        receipts = value["changed_code_receipts"]
        if not isinstance(receipts, list) or not all(isinstance(row, dict) for row in receipts):
            return None
        summary["changed_code_receipts"] = [
            row
            for row in receipts
            if not (isinstance(row.get("exit_code"), int) and row["exit_code"] == 0 and row.get("passed", True) is True)
        ]
        summary["changed_code_receipts_total"] = len(receipts)
    for nested_key in ("mutation_check", "changed_code_evidence"):
        nested = value.get(nested_key)
        if nested is None:
            continue
        if not isinstance(nested, dict):
            return None
        compact_nested = {
            k: copy.deepcopy(nested[k])
            for k in (
                "verdict",
                "summary",
                "settled_lines",
                "unsettled_lines",
                "unmeasured_lines",
                "survivors_count",
                "killed_count",
            )
            if k in nested
        }
        if isinstance(nested.get("files"), (dict, list)):
            compact_nested["files_count"] = len(nested["files"])
        summary[nested_key] = compact_nested
    if isinstance(value.get("files"), (dict, list)):
        summary["files_count"] = len(value["files"])
    summary["details_artifact"] = {"field": "regression_proof"}
    return summary


def _summarize_validation(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not value:
        return None
    bulky = {"checks", "criteria", "results", "tests", "passing_checks", "output", "details"}
    summary: dict[str, Any] = {k: copy.deepcopy(v) for k, v in value.items() if k not in bulky}
    for key in ("checks", "criteria", "results", "tests"):
        if key not in value:
            continue
        filtered = _filter_list_field(value[key])
        if filtered is None:
            return None
        kept, total = filtered
        summary[key] = kept
        summary[f"{key}_total"] = total
        summary[f"{key}_passed_omitted"] = total - len(kept)
    if isinstance(value.get("passing_checks"), (list, dict)):
        summary["passing_checks_count"] = len(value["passing_checks"])
    summary["details_artifact"] = {"field": "validation"}
    return summary


def _summarize_contract_author_rules(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not value or not all(isinstance(v, str) for v in value.values()):
        return None
    summary: dict[str, Any] = {}
    for key, text in value.items():
        byte_len = len(text.encode("utf-8"))
        if byte_len <= 1024:
            summary[key] = text
        else:
            first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
            summary[key] = {
                "bytes": byte_len,
                "summary": first_line[:240],
                "details_artifact": {"field": "contract_author_rules", "key": key},
            }
    return summary


def _summarize_changed_code_evidence(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict) or not value:
        return None
    bulky = {"obligations", "files", "lines", "receipts"}
    summary: dict[str, Any] = {k: copy.deepcopy(v) for k, v in value.items() if k not in bulky}
    if "obligations" in value:
        filtered = _filter_list_field(value["obligations"])
        if filtered is None:
            return None
        kept, total = filtered
        summary["obligations"] = kept
        summary["obligations_total"] = total
        summary["settled_obligations_omitted"] = total - len(kept)
    for count_key in ("files", "lines", "receipts"):
        container = value.get(count_key)
        if isinstance(container, (dict, list)):
            summary[f"{count_key}_count"] = len(container)
    summary["details_artifact"] = {"field": "changed_code_evidence"}
    return summary


_SUMMARIZERS = {
    "regression_proof": _summarize_regression_proof,
    "validation": _summarize_validation,
    "contract_author_rules": _summarize_contract_author_rules,
    "changed_code_evidence": _summarize_changed_code_evidence,
}


def apply(result: dict[str, Any], moved: dict[str, Any], *, stage: str | None = None) -> list[str]:
    """Archive and summarize over-budget fields for ``stage`` into ``moved``.

    Returns the list of field names actually shrunk and archived. Fail-closed on
    unrecognized structures or insufficient byte savings (``< MIN_SAVINGS``).
    """
    active_stage = stage if isinstance(stage, str) and stage else result.get("stage")
    if not isinstance(active_stage, str) or not active_stage:
        return []
    budgeted: list[str] = []
    for field, policy in FIELD_BUDGETS.items():
        if field in NEVER_SUMMARIZE or active_stage not in policy["stages"]:
            continue
        if field not in result:
            continue
        value = result[field]
        orig_bytes = _json_bytes(value)
        if orig_bytes <= policy["bytes"]:
            continue
        summarizer = _SUMMARIZERS.get(field)
        if summarizer is None:
            continue
        summary = summarizer(value)
        if summary is None:
            continue
        summary_bytes = _json_bytes(summary)
        if orig_bytes - summary_bytes < MIN_SAVINGS:
            continue
        moved[field] = copy.deepcopy(value)
        result[field] = summary
        budgeted.append(field)
    return budgeted
