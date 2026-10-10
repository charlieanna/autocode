"""The fix must hold the exactly-once invariant under every timing (hidden tests),
keep retries for lost requests, and come with a diagnosis that names the real
cause. Run checks: the workflow starts by investigating, not gathering requirements."""

from harness.oracle import Check, load_json, mentions, python_change_checks, run_checks

DIAGNOSIS_FIELDS = ("observed", "reproduction", "root_cause", "affected_paths", "invariant")


def check(project, scenario, run=None):
    checks = python_change_checks(project, scenario, package="epp")
    note, error = load_json(project / "docs" / "bugs" / "duplicate-renew.json")
    checks.append(Check("diagnosis_present_and_valid", isinstance(note, dict), error))
    if isinstance(note, dict):
        missing = [field for field in DIAGNOSIS_FIELDS if not note.get(field)]
        checks.append(Check("diagnosis_has_required_fields", not missing, f"missing: {missing}"))
        cause = note.get("root_cause", "")
        checks.append(
            Check(
                "root_cause_names_the_retry_after_uncertain_outcome",
                mentions(
                    cause,
                    ("retry", "retries", "resend", "resent", "re-send", "again"),
                    ("timeout", "reply", "uncertain", "lost"),
                ),
                cause[:200],
            )
        )
        paths = note.get("affected_paths") or []
        checks.append(
            Check(
                "affected_paths_name_the_client",
                any(isinstance(p, str) and p.rstrip("/").endswith("epp/client.py") for p in paths),
                str(paths),
            )
        )
    checks += run_checks(run, workflow="bugfix", no_requirements=True)
    return checks
