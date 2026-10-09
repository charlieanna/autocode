"""Invoice, charge and refunds agree to the cent, half up, for every order (hidden tests), with a
diagnosis that names the real cause. Run checks: the run investigates first, then plans the fix
with plan review and the user's approval, never gathering requirements."""

from harness.oracle import Check, load_json, mentions, python_change_checks, run_checks

DIAGNOSIS_FIELDS = ("observed", "reproduction", "root_cause", "affected_paths", "invariant")


def check(project, scenario, run=None):
    checks = python_change_checks(project, scenario, package="billing")
    note, error = load_json(project / "docs" / "bugs" / "cent-drift.json")
    checks.append(Check("diagnosis_present_and_valid", isinstance(note, dict), error))
    if isinstance(note, dict):
        missing = [field for field in DIAGNOSIS_FIELDS if not note.get(field)]
        checks.append(Check("diagnosis_has_required_fields", not missing, f"missing: {missing}"))
        cause = note.get("root_cause", "")
        checks.append(
            Check(
                "root_cause_names_inconsistent_rounding",
                mentions(cause, ("round", "float", "decimal", "precision")),
                cause[:200],
            )
        )
        paths = {p.rstrip("/") for p in note.get("affected_paths") or [] if isinstance(p, str)}
        modules = sorted(p for p in paths if p.startswith("billing/") and p.endswith(".py"))
        checks.append(Check("affected_paths_span_modules", len(modules) >= 3, str(sorted(paths))))
    checks += run_checks(run, workflow="bugfix", no_requirements=True, plan_approved=True)
    return checks
