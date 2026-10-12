"""Diagnosis: the Plan Reviewer blocked unbracketed list examples that disagree with the brief."""

import json
from pathlib import Path

from harness import verdict
from harness.oracle import Check


def _challenge_reports(run) -> list[dict]:
    run_dir = Path((run or {}).get("run_dir") or "")
    if not run_dir.is_dir():
        return []
    reports = []
    for path in sorted(run_dir.glob("iterations/*/plan-challenge-*.json")):
        if path.name.endswith((".schema.json", ".tools.json", ".before.json", ".after.json")):
            continue
        try:
            reports.append(json.loads(path.read_text()))
        except (OSError, json.JSONDecodeError):
            continue
    return reports


def check(project, scenario, run=None):
    reports = _challenge_reports(run)
    blocked = any(
        any(isinstance(row, dict) and row.get("blocking") for row in (report.get("concerns") or []))
        for report in reports
    )
    return [
        Check("plan_reviewer_raised_blocking_concern", blocked, f"challenge reports: {len(reports)}"),
    ]


def diagnosis(project, run=None):
    reports = _challenge_reports(run)
    hybrid = "scripted_outputs" in (run or {})
    blocked = any(
        any(isinstance(row, dict) and row.get("blocking") for row in (report.get("concerns") or []))
        for report in reports
    )
    text = " ".join(
        str(row.get("concern", ""))
        for report in reports
        for row in (report.get("concerns") or [])
        if isinstance(row, dict)
    )
    about_brackets = any(word in text.lower() for word in ("bracket", "[open", "open|done", "list example"))
    checks = [
        Check("plan_reviewer_ran", bool(reports), f"reports={len(reports)}"),
        Check("blocking_concern", blocked, text[:200]),
        Check("concern_names_the_brackets", about_brackets or not blocked, text[:200]),
        Check("hybrid_route_recorded", hybrid, f"scripted_outputs in run={hybrid}"),
    ]
    ok = all(row.ok for row in checks) and blocked
    return {
        "verdict": verdict.CORRECT if ok else verdict.NOT_EXERCISED if not reports else verdict.INCORRECT,
        "reason": "Plan Reviewer blocked the dropped brackets" if ok else "dropped brackets were not caught",
        "checks": checks,
    }
