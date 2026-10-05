"""Refunds follow the store-time window and the running cap (hidden tests): that is check(), the
product and the run verdict. diagnosis() scores apart from it whether AutoResolver's accepted
diagnoses named a planted defect rather than something vague, so a poor diagnosis is never
reported as a false completion of correct code. Reaching AutoResolver at all is the scenario's
precondition (`requires_stages`), judged by the harness."""
from harness.oracle import Check, mentions, python_change_checks

# Words a diagnosis would use for each planted defect.
DEFECTS = {
    "store time": (("store", "utc", "offset", "timezone", "time zone", "-8", "calendar day", "local"),),
    "running cap": (("total", "cumulative", "running", "already refunded", "sum", "previous refunds", "partial"),),
}


def check(project, scenario, run=None):
    return python_change_checks(project, scenario, "shop")


def diagnosis(project, run):
    """The diagnosis block (scenarios/README.md, "Diagnosis"): lexical, over the accepted diagnoses."""
    if run is None or "astra_resolve" not in (run.get("model_stages") or []):
        return {"verdict": "NOT_EXERCISED", "reason": "AutoResolver never ran", "checks": []}
    diagnoses = [row.get("diagnosis") for row in run.get("resolutions") or []]
    if not diagnoses:
        return {"verdict": "UNSCORED", "reason": "AutoResolver ran but none of its diagnoses was accepted",
                "checks": []}
    named = sorted(name for name, words in DEFECTS.items() if any(mentions(d, *words) for d in diagnoses))
    checks = [Check("resolver_named_a_planted_defect", bool(named),
                    f"named: {named}" if named else f"diagnoses named neither defect: {diagnoses}")]
    return {"verdict": "CORRECT" if named else "INCORRECT",
            "reason": f"named: {named}" if named else "named neither planted defect", "checks": checks}
