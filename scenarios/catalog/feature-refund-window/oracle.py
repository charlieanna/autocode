"""Refunds follow the store-time window and the running cap (hidden tests), and, when
the run needed a correction, AutoResolver's diagnosis named a real defect rather than
something vague. Reaching AutoResolver at all is the scenario's precondition
(`requires_stages`), judged by the harness, not here."""
from harness.oracle import Check, mentions, python_change_checks

# Words a diagnosis would use for each planted defect.
DEFECTS = {
    "store time": (("store", "utc", "offset", "timezone", "time zone", "-8", "calendar day", "local"),),
    "running cap": (("total", "cumulative", "running", "already refunded", "sum", "previous refunds", "partial"),),
}


def check(project, scenario, run=None):
    checks = python_change_checks(project, scenario, "shop")
    if run is not None and run.get("resolutions"):
        diagnoses = [row.get("diagnosis") for row in run["resolutions"]]
        named = sorted(name for name, words in DEFECTS.items() if any(mentions(d, *words) for d in diagnoses))
        checks.append(Check("resolver_named_a_planted_defect", bool(named),
                            f"named: {named}" if named else f"diagnoses named neither defect: {diagnoses}"))
    return checks
