"""Diagnosis: a lazy Validator's prose citations were refused before completion.

In check mode (``run is None``) the oracle scores the greenfield-todo-cli product,
so OracleControlTests can reject the seed and accept the reference without a run.
"""

from harness import catalog, verdict
from harness.oracle import Check


def _product_checks(project, scenario):
    sibling = catalog.load("greenfield-todo-cli")
    return sibling.oracle()(project, sibling)


def _refusal_checks(run):
    status = (run or {}).get("status") or ""
    reason = ((run or {}).get("view") or {}).get("stop_reason") or ""
    stages = (run or {}).get("model_stages") or []
    return [
        Check("validator_ran", "sol" in stages, f"stages={stages}"),
        Check(
            "did_not_complete_on_lazy_citations",
            status.startswith(("PAUSED_", "WAITING_FOR_USER", "BLOCKED_")),
            f"status={status!r}",
        ),
        Check(
            "refusal_mentions_unverifiable_citations",
            "cites evidence the runner did not verify" in reason,
            reason[:200],
        ),
    ]


def check(project, scenario, run=None):
    return _product_checks(project, scenario) if run is None else _refusal_checks(run)


def diagnosis(project, run=None):
    hybrid = "scripted_outputs" in (run or {})
    stages = (run or {}).get("model_stages") or []
    sol_ran = "sol" in stages
    checks = [
        *_refusal_checks(run),
        Check("hybrid_route_recorded", hybrid, f"scripted_outputs in run={hybrid}"),
    ]
    ok = all(row.ok for row in checks)
    return {
        "verdict": verdict.CORRECT if ok else verdict.NOT_EXERCISED if not sol_ran else verdict.INCORRECT,
        "reason": "lazy Validator citations were refused" if ok else "lazy Validator was not refused",
        "checks": checks,
    }
