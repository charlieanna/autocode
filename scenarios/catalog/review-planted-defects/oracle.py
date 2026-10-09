"""Did the review find the two planted regressions in pr-184.patch, report nothing
else as blocking, and leave the repository untouched? Line spans refer to the
files as they are after the patch."""
from harness.oracle import Check, finding_matches, findings_of, load_json, only_changed_under, run_checks

# F1: the Timeout branch of RegistryClient.send no longer asks the registry for
# the transaction's status before resending.
TIMEOUT_REGRESSION = dict(file="regclient/client.py", lines=(26, 28),
                          words=(("timeout",), ("reconcil", "status", "executed", "duplicate", "twice", "already")))
# F2: the REGISTRIES table replaces POLICIES and drops the .de entry, so .de
# falls back to DEFAULT and is retried.
DE_REGRESSION = dict(file="regclient/policies.py", lines=(33, 39),
                     words=((".de", "denic", " de ", "never retry"),))


def check(project, scenario, run=None):
    report, error = load_json(project / "review" / "findings.json")
    checks = [Check("findings_json_present_and_valid", isinstance(report, dict), error)]
    if isinstance(report, dict):
        blocking = findings_of(report, "blocking")
        checks.append(Check("verdict_requests_changes", report.get("verdict") == "request_changes",
                            f"verdict={report.get('verdict')!r}"))
        checks.append(Check("finds_timeout_reconciliation_regression",
                            any(finding_matches(f, **TIMEOUT_REGRESSION) for f in blocking)))
        checks.append(Check("finds_de_policy_regression",
                            any(finding_matches(f, **DE_REGRESSION) for f in blocking)))
        false = [f.get("id") or f.get("summary") for f in blocking
                 if not (finding_matches(f, **TIMEOUT_REGRESSION) or finding_matches(f, **DE_REGRESSION))]
        checks.append(Check("no_false_blockers", not false, f"blocking but not a real regression: {false}"))
    checks.append(only_changed_under(project, "review/"))
    checks += run_checks(run, workflow="review", no_build=True, no_requirements=True)
    return checks
