"""A correct PR must be approved: no blocking findings, verdict approve, and the
repository left untouched."""

from harness.oracle import (
    Check,
    apply_patch,
    findings_of,
    load_json,
    only_changed_under,
    python_tests,
    run_checks,
    scratch_copy,
    tail,
)


def check(project, scenario, run=None):
    # Judge the reviewer only when the trusted input really is a clean PR.
    # Fixture drift is a harness ERROR, never a model-quality verdict. Use the
    # catalog seed so a delivered report cannot supply its own easier baseline.
    with scratch_copy(scenario.seed) as candidate:
        applied = apply_patch(candidate, candidate / "pr-191.patch")
        if applied.returncode:
            raise RuntimeError("Invalid review fixture: patch does not apply: " + tail(applied))
        tested = python_tests(candidate)
        if tested.returncode:
            raise RuntimeError("Invalid review fixture: candidate tests fail: " + tail(tested))
    report, error = load_json(project / "review" / "findings.json")
    checks = [
        Check("review_input_valid", True),
        Check("findings_json_present_and_valid", isinstance(report, dict), error),
    ]
    if isinstance(report, dict):
        blocking = [f.get("id") or f.get("summary") for f in findings_of(report, "blocking")]
        checks.append(Check("no_blocking_findings", not blocking, f"blocking: {blocking}"))
        checks.append(
            Check("verdict_approves", report.get("verdict") == "approve", f"verdict={report.get('verdict')!r}")
        )
    checks.append(only_changed_under(project, "review/"))
    checks += run_checks(run, workflow="review", no_build=True, after_requirements="review_change")
    return checks
