"""A sound design gets no blocking concerns and the repository is left untouched."""

from harness.oracle import Check, load_json, only_changed_under, run_checks


def check(project, scenario, run=None):
    report, error = load_json(project / "review" / "design-review.json")
    checks = [Check("design_review_json_present_and_valid", isinstance(report, dict), error)]
    if isinstance(report, dict):
        blocking = [
            c.get("id") or c.get("summary")
            for c in report.get("concerns") or []
            if isinstance(c, dict) and c.get("severity") == "blocking"
        ]
        checks.append(Check("no_blocking_concerns", not blocking, f"blocking: {blocking}"))
        satisfied = report.get("satisfied") or []
        checks.append(Check("credits_the_goals_the_design_meets", len(satisfied) >= 3, f"{len(satisfied)} listed"))
    checks.append(only_changed_under(project, "review/"))
    checks += run_checks(run, workflow="design", no_build=True)
    return checks
