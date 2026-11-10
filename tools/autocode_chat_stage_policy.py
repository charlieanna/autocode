"""The terminal chat's view of the planning path a run took (#1884 F2).

A bug-fix run that opted into compact planning (#2021) records which path it took under the status view's
``stage_policy`` field (autocode_stage_policy): the path, every trusted signal that kept the full path, the
Planner draft's omitted/included decision with what covers an omission, and a fallback once a review could
not approve the runner's draft. This module renders that record for the side panel (compact) and ``/details``
(details), from the public view alone: it never recomputes a decision, adds authority or takes an action, and
a view without the field (an older core, a job that never decided) shows nothing. ``decisions`` renders the
view's ``stage_decisions`` (autocode_stage_decisions) for ``/details`` the same way: every stage that ran and
every stage a saved decision skipped, with why.

Client layer, like autocode_chat_cost: imports nothing from AutoCode.
"""

from __future__ import annotations

# How the record names what covers an omitted stage, in a person's words.
COVERAGE = {
    "runner:compact_draft": "the runner's draft from the diagnosis",
    "review_plan": "the Plan Reviewer",
    "person:approve_goal": "your approval of the plan",
    "runner:regression_proof": "the regression proof",
    "requirements_gather": "Requirements' own answer",
}
STAGES = {"draft_plan": "Planner draft"}


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(value) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _texts(value) -> list[str]:
    return [str(item) for item in value if str(item).strip()] if isinstance(value, list) else []


def available(view: dict) -> bool:
    """Whether the view carries a decision to show."""
    return isinstance(_mapping(view).get("stage_policy"), dict)


def _record(view: dict) -> dict:
    return _mapping(_mapping(view).get("stage_policy"))


def _path(record: dict) -> str:
    path = record.get("path")
    return path if path in ("compact", "full") else "unknown"


def _covered(decision: dict) -> str:
    names = [COVERAGE.get(item, item) for item in _texts(decision.get("covered_by"))]
    return ", ".join(names)


def compact(view: dict) -> tuple[str, list[str]] | None:
    """A short sidebar summary: the path and what decided it; the reasons and record stay in /details."""
    if not available(view):
        return None
    record = _record(view)
    path = _path(record)
    decisions = _rows(record.get("decisions"))
    omitted = [row for row in decisions if row.get("decision") == "omitted"]
    fallback = _mapping(record.get("fallback"))
    # Short enough for the 40-column panel beside the section's title; the words are in /details.
    if path == "compact":
        summary = "compact · no " + (
            ", ".join(STAGES.get(str(r.get("stage")), str(r.get("stage"))) for r in omitted) or "omitted stage"
        )
    elif path == "full":
        summary = "full · every stage runs"
    else:
        summary = "unknown"
    rows = []
    if path == "compact" and omitted:
        covered = _covered(omitted[0])
        if covered:
            rows.append("covered by " + covered)
    if fallback:
        rows.append(
            "fell back at "
            + str(fallback.get("from") or "a review")
            + ": "
            + str(fallback.get("reason") or "unknown reason")
        )
    elif path == "full":
        reasons = _texts(record.get("reasons"))
        rows.append(reasons[0] if reasons else "no reason recorded")
        if len(reasons) > 1:
            rows.append(f"and {len(reasons) - 1} more: /details")
    rows.append("Why: /details")
    return summary, rows


def details(view: dict) -> str:
    """The whole record in words: path, each stage decision with its coverage, every reason, the fallback and
    the records the decision rests on. Only core-authored values; nothing is inferred."""
    if not available(view):
        return ""
    record = _record(view)
    path = _path(record)
    lines = ["Planning path", f"Path: {path}"]
    for row in _rows(record.get("decisions")):
        stage = STAGES.get(str(row.get("stage")), str(row.get("stage") or "stage unknown"))
        decision = str(row.get("decision") or "unknown")
        lines.append(f"{stage}: {decision}" + (f" ({row['reason']})" if str(row.get("reason") or "").strip() else ""))
        covered = _covered(row)
        if covered:
            lines.append("  Covered by: " + covered)
    reasons = _texts(record.get("reasons"))
    if reasons:
        lines.append("Kept the full path because:")
        lines.extend(f"  - {reason}" for reason in reasons)
    fallback = _mapping(record.get("fallback"))
    if fallback:
        lines.append(
            f"Fallback: at {fallback.get('from') or 'a review'}, {fallback.get('reason') or 'unknown reason'}"
            + ("; the review call was refunded" if fallback.get("review_call_refunded") is True else "")
        )
    basis = _mapping(record.get("basis"))
    shown = [
        f"{label} {str(basis[key])[:12]}"
        for key, label in (
            ("diagnosis_output_hash", "diagnosis"),
            ("diagnosis_source_revision", "source"),
        )
        if str(basis.get(key) or "").strip()
    ]
    if shown:
        lines.append("Decided from: " + ", ".join(shown))
    lines.append("The record reports a decision the core made from trusted inputs; it grants nothing.")
    return "\n".join(lines)


def decisions(view: dict) -> str:
    """The status view's ``stage_decisions`` in words for /details: each stage that ran, how often and at which
    level when a rule moved it, and each stage a saved decision skipped, with why and what covers it. Only
    core-authored values; an older core without the record shows nothing."""
    rows = _rows(_mapping(view).get("stage_decisions"))
    if not rows:
        return ""
    lines = ["Stages"]
    for row in rows:
        label = str(row.get("label") or row.get("stage") or "stage unknown")
        if row.get("decision") == "skip":
            reason = str(row.get("reason") or "").strip()
            lines.append(f"{label}: skipped" + (f" ({reason})" if reason else ""))
            covered = _covered(row)
            if covered:
                lines.append("  Covered by: " + covered)
            continue
        launches = row.get("launches")
        times = f"{launches} times" if type(launches) is int and launches > 1 else "once"
        choice = _mapping(row.get("effort_choice"))
        level = ""
        if choice.get("lowered") and isinstance(choice.get("level"), str) and isinstance(choice.get("ceiling"), str):
            level = f", at {choice['level']}, below your {choice['ceiling']}"
        elif choice.get("raised") and isinstance(choice.get("level"), str) and isinstance(choice.get("saved"), str):
            level = f", at {choice['level']}, above your {choice['saved']}"
        lines.append(f"{label}: ran {times}{level}")
    lines.append("The record reports what the core decided; it grants nothing.")
    return "\n".join(lines)
