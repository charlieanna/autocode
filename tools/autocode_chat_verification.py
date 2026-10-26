"""Render existing verification projections without granting evidence authority."""

from __future__ import annotations

import math


def _map(value):
    return value if isinstance(value, dict) else {}


def _rows(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _text(value, default="unknown"):
    return " ".join(value.split()) if isinstance(value, str) and value.strip() else default


def review_result(view):
    if view.get("workflow") == "review":
        return _map(_map(view.get("evidence")).get("workflow_result"))
    return {}


def title(view):
    return "Review result" if review_result(view) else "Verification"


def _gate(view):
    value = _map(view.get("delivery")).get("verified_complete")
    return value if type(value) is bool else None


def _delivery(view):
    gate = _gate(view)
    return "verified by core gate" if gate is True else "not verified" if gate is False else "unknown"


def available(view):
    return bool(review_result(view)) or any(
        isinstance(view.get(key), dict) and view[key]
        for key in ("verification", "delivery", "recovery", "runner_check", "efficiency", "code_checkpoints")
    )


def cue(view, *, unavailable=False):
    """A last-read cue, never a new freshness clock or a worker-liveness decision."""
    if unavailable:
        return "Status unavailable; " + ("showing the previous read" if isinstance(view, dict) else "no previous read")
    if not isinstance(view, dict):
        return "Reading run status"
    if view.get("status") in ("TASK_COMPLETE", "COMPLETE"):
        if result := review_result(view):
            return (
                "Review complete; recorded "
                + _text(result.get("outcome")).replace("_", " ")
                + "; "
                + (
                    "report and source current"
                    if result.get("freshness") == "current"
                    else "current review " + _text(result.get("freshness")).replace("_", " ")
                )
            )
        return (
            "Complete; current delivery verified"
            if _gate(view) is True
            else "Saved complete; current delivery " + ("not verified" if _gate(view) is False else "unknown")
        )
    return _text(_map(view.get("progress")).get("headline"), _text(view.get("status"))).replace("_", " ")


def compact(view):
    if not available(view):
        return None
    if result := review_result(view):
        return "recorded " + _text(result.get("outcome")).replace("_", " "), [
            _text(result.get("summary")),
            "Review inspection: " + _text(result.get("freshness")).replace("_", " "),
            "Report: " + _text(result.get("artifact")),
            "Evidence and recovery: /details",
        ]
    report = _map(view.get("verification"))
    rows = ["Report: " + _text(report.get("freshness")).replace("_", " ")]
    pending = sum(row.get("human_acceptance_pending") is True for row in _rows(report.get("coverage")))
    if pending:
        rows.append(f"{pending} human acceptance pending")
    if _map(view.get("code_checkpoints")).get("working"):
        rows.append("Working changes still need verification")
    rows.append("Evidence and recovery: /details")
    return _delivery(view), rows


def _seconds(value):
    try:
        valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
    except OverflowError:
        valid = False
    return f"{value:,.1f}s" if valid else "unknown"


def details(view):
    if not available(view):
        return ""
    report = _map(view.get("verification"))
    result = review_result(view)
    if result:
        report = {}  # build coverage is not the evidence for a review workflow
    lines = [
        "Verification and delivery",
        "Current delivery: " + _delivery(view),
        "Report inspection: " + _text(report.get("freshness")).replace("_", " "),
    ]
    if result:
        lines = [
            "Review result",
            "Recorded outcome: " + _text(result.get("outcome")).replace("_", " "),
            _text(result.get("summary")),
            "Report: " + _text(result.get("artifact")),
            "Report SHA-256: " + _text(result.get("artifact_sha256")),
            "Review inspection: " + _text(result.get("freshness")).replace("_", " "),
        ]
        lines.extend(_text(reason) for reason in result.get("reasons", []) if isinstance(reason, str))
    reasons = report.get("reasons")
    if isinstance(reasons, list):
        lines.extend(_text(reason) for reason in reasons if isinstance(reason, str))
    for row in _rows(report.get("coverage")):
        pending = "; human acceptance pending" if row.get("human_acceptance_pending") is True else ""
        lines.append(
            f"{_text(row.get('id'))}: recorded {_text(row.get('recorded_status'))}; "
            f"coverage state {_text(row.get('state'))}{pending}"
        )
        if row.get("criterion"):
            lines.append("  " + _text(row["criterion"]))
        refs = row.get("evidence_refs")
        if isinstance(refs, list):
            lines.extend("  Evidence: " + _text(ref) for ref in refs if isinstance(ref, str))
    checkpoints = _map(view.get("code_checkpoints"))
    if checkpoints.get("working"):
        lines.append("Working comparison available; edits still need verification.")
    if _rows(checkpoints.get("rows")):
        lines.append("Saved code comparisons: /diff lists the core's available steps.")
    check = _map(view.get("runner_check"))
    if check:
        lines.append("Runner check: " + _text(check.get("summary")))
        if check.get("command"):
            lines.append("  Command: " + _text(check["command"]))
        lines.append("A recorded command alone does not establish accepted evidence.")
    recovery, need = _map(view.get("recovery")), _map(view.get("needs"))
    if recovery or need.get("recovery_hint") or need.get("guidance"):
        lines.extend(["", "Recovery and next decision"])
        for value in (
            recovery.get("what_happened"),
            recovery.get("saved_reason"),
            recovery.get("retained"),
            need.get("recovery_hint"),
            need.get("guidance"),
        ):
            if isinstance(value, str) and value.strip():
                lines.append(_text(value))
        for action in _rows(recovery.get("actions")):
            label = _text(action.get("label"))
            effect = _text(action.get("effect") or action.get("description"), "")
            lines.append(label + (": " + effect if effect else ""))
        if not view.get("next_command"):
            lines.append("No primary action is currently offered.")
    timing = _map(_map(view.get("efficiency")).get("time"))
    lines.extend(["", "Recorded time"])
    lines.append("Summed execution: " + _seconds(timing.get("summed_execution_seconds")))
    if timing.get("summed_execution_seconds") is None and "known_summed_execution_seconds" in timing:
        lines.append("Known execution subtotal: " + _seconds(timing["known_summed_execution_seconds"]))
    lines.append("Observed interval wall time: " + _seconds(timing.get("wall_union_seconds")))
    waiting = _map(timing.get("exclusive_wall_seconds_by_category")).get("waiting")
    lines.append("Answered-request waiting: " + _seconds(waiting))
    attempts = _rows(_map(_map(view.get("usage")).get("accounting")).get("attempts"))
    for attempt in attempts:
        if attempt.get("active") is True:
            lines.append(
                "Current attempt recorded duration ("
                + _text(attempt.get("job"))
                + "): "
                + _seconds(attempt.get("duration_seconds"))
            )
    lines.append("These observed intervals do not supply a last-meaningful-progress timestamp.")
    return "\n".join(lines)
