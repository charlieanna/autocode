"""A correct PR must be approved: no blocking findings, verdict approve, and the
repository left untouched."""
from harness.oracle import Check, findings_of, load_json, only_changed_under, run_checks


def check(project, scenario, run=None):
    report, error = load_json(project / "review" / "findings.json")
    checks = [Check("findings_json_present_and_valid", isinstance(report, dict), error)]
    if isinstance(report, dict):
        blocking = [f.get("id") or f.get("summary") for f in findings_of(report, "blocking")]
        checks.append(Check("no_blocking_findings", not blocking, f"blocking: {blocking}"))
        checks.append(Check("verdict_approves", report.get("verdict") == "approve", f"verdict={report.get('verdict')!r}"))
    checks.append(only_changed_under(project, "review/"))
    checks += run_checks(run, workflow="review", no_build=True, no_requirements=True)
    return checks
