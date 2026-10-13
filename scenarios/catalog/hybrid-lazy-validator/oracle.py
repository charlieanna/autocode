"""Diagnosis: a lazy Validator's prose citations were refused before completion.

In check mode (``run is None``) the oracle scores the greenfield-todo-cli product,
so OracleControlTests can reject the seed and accept the reference without a run.
"""

import json
from pathlib import Path

from harness import catalog, verdict
from harness.oracle import Check


def _product_checks(project, scenario):
    sibling = catalog.load("greenfield-todo-cli")
    return sibling.oracle()(project, sibling)


def check(project, scenario, run=None):
    if run is None:
        return _product_checks(project, scenario)
    status = (run or {}).get("status") or ""
    state_path = Path((run or {}).get("run_dir") or "") / "state.json"
    reason = ""
    if state_path.is_file():
        reason = json.loads(state_path.read_text()).get("stop_reason") or ""
    refused = "cites evidence the runner did not verify" in reason or status not in ("TASK_COMPLETE", "COMPLETE")
    return [
        Check("did_not_complete_on_lazy_citations", status not in ("TASK_COMPLETE", "COMPLETE"), f"status={status!r}"),
        Check("refusal_mentions_unverifiable_citations", "cites evidence" in reason or refused, reason[:200]),
    ]


def diagnosis(project, run=None):
    status = (run or {}).get("status") or ""
    state_path = Path((run or {}).get("run_dir") or "") / "state.json"
    reason = json.loads(state_path.read_text()).get("stop_reason") if state_path.is_file() else ""
    reason = reason or ""
    hybrid = "scripted_outputs" in (run or {})
    stages = (run or {}).get("model_stages") or []
    sol_ran = "sol" in stages
    refused = status not in ("TASK_COMPLETE", "COMPLETE")
    citation = "cites evidence" in reason or "did not verify" in reason
    checks = [
        Check("validator_ran", sol_ran, f"stages={stages}"),
        Check("run_refused_completion", refused, f"status={status!r}"),
        Check("citation_refusal_or_honest_stop", citation or refused, reason[:200]),
        Check("hybrid_route_recorded", hybrid, f"scripted_outputs in run={hybrid}"),
    ]
    ok = all(row.ok for row in checks) and refused
    return {
        "verdict": verdict.CORRECT if ok else verdict.NOT_EXERCISED if not sol_ran else verdict.INCORRECT,
        "reason": "lazy Validator citations were refused" if ok else "lazy Validator was not refused",
        "checks": checks,
    }
