"""Requirements' audit of what the brief left unsaid (board #11 P1.2), checked by the runner.

A requirements report states, for each of six dimensions of a change, where its behavior comes
from: the person's own words (``specified_in_source``), a convention in code the stage read
(``repo_convention``), nothing to decide (``not_applicable``), or a question to the person in this
report (``open_question``). The runner holds each answer to its evidence, from one place:

- ``specified_in_source`` cites a requirement id of this report, a ``source_inventory`` or pinned
  ``catalog/...`` id, ``task``, or a saved answer or user event id;
- ``repo_convention`` cites ``path[:line]`` the stage actually read (``code_citations``);
- ``open_question`` names questions of this report, and every question the person decides
  (``kind=decision``, any category but technical) is named by some dimension, so the audit and
  the questions cannot disagree.

A report without the field (saved before it existed, or the triage pass) is not checked. Domain
layer: pure over the run state and the report.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_brief_obligations as brief_obligations
    from . import autocode_catalog_context as source_catalog
    from . import autocode_code_citations as code_citations
except ImportError:
    import autocode_brief_obligations as brief_obligations
    import autocode_catalog_context as source_catalog
    import autocode_code_citations as code_citations

FIELD = "ambiguity_audit"
STAGE = "requirements_gather"
DIMENSIONS = (
    "outcome_scope",
    "inputs_and_edge_cases",
    "error_and_failure_behavior",
    "compatibility_and_callers",
    "ux_and_output_contract",
    "state_and_side_effects",
)
STATUSES = ("specified_in_source", "repo_convention", "not_applicable", "open_question")
ENTRY = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": list(STATUSES)},
        "refs": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "refs"],
    "additionalProperties": False,
}
SCHEMA = {
    "type": "object",
    "properties": {dimension: ENTRY for dimension in DIMENSIONS},
    "required": list(DIMENSIONS),
    "additionalProperties": False,
}


def source_ids(state: RunState, report: dict) -> set[str]:
    """What a ``specified_in_source`` dimension may cite: the person's words, pinned sources or a requirement."""
    ids = {"task", *(state.get("answers") or {})}
    ids.update(row["id"] for row in brief_obligations.sources(state) or [] if isinstance(row, dict) and row.get("id"))
    ids.update(event["id"] for event in state.get("user_events") or [] if isinstance(event, dict) and event.get("id"))
    ids.update(row["id"] for row in report.get("requirements") or [] if isinstance(row, dict) and row.get("id"))
    return {value for value in ids if isinstance(value, str) and value}


def _valid_source(state: RunState, sources: set[str], ref: str) -> bool:
    return ref in sources or (ref.startswith("catalog/") and bool(source_catalog.evidence(state, ref)))


def validate(state: RunState, report: dict) -> None:
    """Raise ValueError naming every dimension whose status its refs do not support."""
    audit = report.get(FIELD)
    if audit is None:
        return
    if not isinstance(audit, dict) or set(audit) != set(DIMENSIONS):
        raise ValueError(f"{FIELD} must state exactly these dimensions: {', '.join(DIMENSIONS)}")
    questions = {
        row["id"]: row for row in report.get("open_questions") or [] if isinstance(row, dict) and row.get("id")
    }
    sources = source_ids(state, report)
    problems: list[str] = []
    named: set[str] = set()
    for dimension in DIMENSIONS:
        entry = audit[dimension] if isinstance(audit[dimension], dict) else {}
        status, refs = entry.get("status"), [ref for ref in entry.get("refs") or [] if isinstance(ref, str)]
        label = f"{FIELD}.{dimension}"
        if status not in STATUSES:
            problems.append(f"{label} has no status from {', '.join(STATUSES)}")
        elif status == "not_applicable":
            continue
        elif not refs:
            problems.append(f"{label} is {status} but cites nothing")
        elif status == "open_question":
            missing = [ref for ref in refs if ref not in questions]
            named.update(ref for ref in refs if ref in questions)
            if missing:
                problems.append(f"{label} names {', '.join(missing)}, which are not open_questions ids in this report")
        elif status == "specified_in_source":
            missing = [ref for ref in refs if not _valid_source(state, sources, ref)]
            if missing:
                problems.append(
                    f"{label} cites {', '.join(missing)}: cite a requirement id of this report, a source_inventory "
                    "or pinned catalog/... id, task, or a saved answer or user event id"
                )
        else:
            problems.extend(
                reason
                for ref in refs
                if (reason := code_citations.verify_citation(state, STAGE, ref, field=label)) is not None
            )
    unnamed = [
        question_id
        for question_id, row in questions.items()
        if row.get("kind") == "decision" and row.get("category") != "technical" and question_id not in named
    ]
    if unnamed:
        problems.append(
            f"open_questions {', '.join(unnamed)} ask the person to decide, but no {FIELD} dimension is "
            "open_question naming them"
        )
    if problems:
        raise ValueError("; ".join(problems))
