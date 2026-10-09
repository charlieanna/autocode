"""Every worker charges the current price after any write while checkout still caches (hidden
tests), with a diagnosis that names the real cause. Run checks: the run investigates first and never
gathers requirements. Either fix route passes: live, the Investigator fixed it in two files and sized it
small (2026-09-28), which its own rule allows; bugfix-cent-drift is the one that must be planned."""
from harness.oracle import Check, load_json, mentions, python_change_checks, run_checks

DIAGNOSIS_FIELDS = ("observed", "reproduction", "root_cause", "affected_paths", "invariant")


def check(project, scenario, run=None):
    checks = python_change_checks(project, scenario, package="shop")
    note, error = load_json(project / "docs" / "bugs" / "stale-prices.json")
    checks.append(Check("diagnosis_present_and_valid", isinstance(note, dict), error))
    if isinstance(note, dict):
        missing = [field for field in DIAGNOSIS_FIELDS if not note.get(field)]
        checks.append(Check("diagnosis_has_required_fields", not missing, f"missing: {missing}"))
        cause = note.get("root_cause", "")
        checks.append(Check("root_cause_names_invalidation_left_to_write_paths",
                            mentions(cause, ("invalidat", "stale", "evict"),
                                     ("import", "discount", "promotion", "write path", "caller", "set_price")),
                            cause[:200]))
        paths = {p.rstrip("/") for p in note.get("affected_paths") or [] if isinstance(p, str)}
        modules = sorted(p for p in paths if p.startswith("shop/") and p.endswith(".py"))
        checks.append(Check("affected_paths_span_modules", len(modules) >= 2, str(sorted(paths))))
    checks += run_checks(run, workflow="bugfix", no_requirements=True)
    return checks
