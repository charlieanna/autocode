"""Recover structural planning metadata without changing a contract or evidence.

The report loader retains the provider's response before saving the canonical
report. Schema, requirement coverage and contract revision checks still apply.
This module imports no runner or controller code.
"""
from __future__ import annotations

import copy

PLANNING_STAGES = frozenset({"astra_discovery", "glm_revise", "astra_finalize",
                            "plan", "plan_revise", "plan_finalize"})
PROTECTED_LISTS = ("required_behaviors", "scope_exclusions", "constraints", "important_failure_cases")


def _trace_identity(report, state):
    rows = report.get("requirement_trace")
    if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
        return
    missing = [index for index, row in enumerate(rows) if "requirement_id" not in row]
    if not missing:
        return
    requirements = ((state.get("requirements_handoff") or {}).get("report") or {}).get("requirements") or []
    known = [row.get("id") for row in requirements if isinstance(row, dict)]
    assigned = [row["requirement_id"] for row in rows if "requirement_id" in row]
    known_valid = (len(known) == len(requirements) and all(isinstance(rid, str) and rid for rid in known)
                   and len(set(known)) == len(known))
    valid_ids = (known_valid and all(isinstance(rid, str) and rid in known for rid in assigned)
                 and len(set(assigned)) == len(assigned))
    remaining = [rid for rid in known if rid not in assigned] if known_valid else []
    # One-to-one identity recovery only. Never use row order, overwrite an
    # explicit ID, or choose between two possible remaining requirements.
    if valid_ids and len(rows) == len(known) and len(missing) == len(remaining) == 1:
        rows[missing[0]]["requirement_id"] = remaining[0]
        return
    paths = ", ".join(f"requirement_trace[{index}].requirement_id" for index in missing)
    raise ValueError(f"Planner report is missing {paths}; unassigned saved requirement IDs: {remaining!r}. "
                     "Supply explicit IDs for these rows; their order does not establish identity")


def _addition_declarations(report, state):
    previous = (state.get("goal_contract") or {}).get("body")
    current, changes = report.get("contract"), report.get("contract_changes")
    if not isinstance(previous, dict) or not isinstance(current, dict) or not isinstance(changes, list):
        return
    old, new = previous.get("acceptance_criteria"), current.get("acceptance_criteria")
    if not isinstance(old, list) or not isinstance(new, list):
        return
    if not all(isinstance(row, dict) and isinstance(row.get("id"), str) for row in [*old, *new]):
        return
    old_ids, new_ids = [row["id"] for row in old], [row["id"] for row in new]
    if len(set(old_ids)) != len(old_ids) or len(set(new_ids)) != len(new_ids):
        return
    protected = set(old_ids)
    for field in (*PROTECTED_LISTS, "permission_boundaries"):
        values = previous.get(field) or []
        if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
            return
        protected.update(values)

    def redundant(row):
        fields = {"item", "change", "basis", "answer_id", "replacement"}
        if (not isinstance(row, dict) or row.keys() - (fields | {"example_correction"})
                or any(not isinstance(row.get(field), str) for field in fields)
                or row["change"] != "reworded" or row["basis"] != "agent_proposed"
                or row["answer_id"] != ""):
            return False
        if "example_correction" in row:
            correction = row["example_correction"]
            if correction is not None and (not isinstance(correction, dict) or set(correction) != {"concern_id", "before", "after"}
                    or any(value != "" for value in correction.values())):
                return False
        item = row.get("item")
        if not isinstance(item, str) or item in protected:
            return False
        if item in new_ids and item not in old_ids:
            return True
        if item in PROTECTED_LISTS:
            before, after = previous.get(item) or [], current.get(item)
            return (isinstance(after, list) and all(isinstance(value, str) for value in after)
                    and all(value in after for value in before)
                    and any(value not in before for value in after))
        return False

    # The contract itself is untouched. Any actual edit/removal/permission
    # change must still pass revision_guard; a redundant receipt cannot hide it.
    report["contract_changes"] = [row for row in changes if not redundant(row)]


def normalize_planning_metadata(value, state, record):
    stage = record.get("original_stage") or str(record.get("stage", "")).removesuffix("_report_repair")
    if stage not in PLANNING_STAGES or not isinstance(value, dict) or not state:
        return value
    result = copy.deepcopy(value)
    _trace_identity(result, state)
    _addition_declarations(result, state)
    return result
