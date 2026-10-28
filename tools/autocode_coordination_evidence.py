"""Consume current joint-run proof through TaskRun, without translating its plan."""

try:
    from . import autocode_test_cases as cases
    from .autocode_coordination_store import CoordinationError
except ImportError:
    import autocode_test_cases as cases
    from autocode_coordination_store import CoordinationError


def capture(run, base):
    """Require freshly authenticated completion and real fail-before/pass-after proof."""
    view = run.status(inspect_evidence=True)
    report = view.get("evidence_report") or {}
    document = report.get("document") or {}
    approved = view.get("approved_contract") or {}
    criteria = (approved.get("body") or {}).get("acceptance_criteria") or []
    results = document.get("criteria") or []
    revision = document.get("revision") or {}
    proof = document.get("regression_proof") or {}
    if (
        not view.get("done")
        or report.get("availability") != "current"
        or not report.get("binding")
        or document.get("binding") != report["binding"]
        or not all(report.get(key) for key in ("json_path", "json_sha256", "markdown_sha256"))
        or not approved.get("token")
        or (document.get("plan") or {}).get("contract_token") != approved["token"]
        or revision.get("base_commit") != base
        or not revision.get("source_revision")
        or proof.get("source_revision") != revision["source_revision"]
        or proof.get("verdict") != "PASS"
        or not proof.get("fail_to_pass")
        or proof.get("failures")
        or proof.get("unverified")
        or not criteria
        or len(results) != len(criteria)
    ):
        raise CoordinationError("Joint evidence needs current completion and authenticated regression proof")
    by_id = {row.get("id"): row for row in results}
    for criterion in criteria:
        result = by_id.get(criterion.get("id"), {})
        if (
            criterion.get("human_review")
            or any(
                result.get(key) != criterion.get(key) for key in ("criterion", "verification_method", "human_review")
            )
            or result.get("status") != "verified"
            or result.get("validator_status") != "PASS"
            or not result.get("evidence_refs")
        ):
            raise CoordinationError("Joint evidence does not verify every approved criterion")
    for case in cases.plan_cases(approved["body"]):
        matched = (proof.get("case_tests") or {}).get(case["id"])
        if not matched or (case.get("kind") != "preserve" and not set(matched) <= set(proof["fail_to_pass"])):
            raise CoordinationError("Joint evidence does not prove every approved named case")
    return {
        "kind": "task_run",
        "run_dir": str(run.run_dir),
        "record": report["json_path"],
        "verdict": "PASS",
        "evidence_report": report,
    }


def require_current(run, base, saved):
    fresh = capture(run, base)
    if (
        fresh["run_dir"] != saved.get("run_dir")
        or fresh["record"] != saved.get("record")
        or any(
            fresh["evidence_report"].get(key) != (saved.get("evidence_report") or {}).get(key)
            for key in ("binding", "json_sha256", "markdown_sha256")
        )
    ):
        raise CoordinationError("Joint evidence changed after verification; publication is not authorized")
