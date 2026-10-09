"""Read-only preparation of a reviewed progressive activation, not integration.

The single state owner must authenticate the current approval, previous plan,
artifact indexes, completed slice/criterion proof and normalized findings. It
must capture the *actual retained* source snapshot immediately before calling,
not a model-supplied digest. ``contract_authenticated`` means the current
approval event was checked against the real user-event ledger.

``review_receipt`` is produced exclusively by the runner after checking its
actual accepted output, configured review stage/role, session and independent
planner session. Its authenticated flag is not report data. The runner must
check that these IDs belong to the actual accepted independent review (not a
model label), and hash the exact normalized output stored in the review report.
This helper checks all resulting bindings; it cannot authenticate provenance.

The caller holds its activation boundary lock, supplies every blocker as an
explicit boolean, and rechecks source/approval/boundary freshness before its
atomic pointer update. Persist both artifacts first. Restart safety is by
determinism: re-preparation from the same immutable artifacts re-derives an
identical result, and the state owner pauses any saved transition instead of
treating the candidate as its own predecessor. This module never writes state,
allocates allowances, resets counters or substitutes historical PASS for
current replay.
"""
from __future__ import annotations

from copy import deepcopy

try:
    from . import autocode_contract_identity as contract_identity
    from . import autocode_progressive_artifacts as artifacts
    from . import autocode_progressive_plan as plan
    from . import autocode_util as util
except ImportError:
    import autocode_contract_identity as contract_identity
    import autocode_progressive_artifacts as artifacts
    import autocode_progressive_plan as plan
    import autocode_util as util


BLOCKERS = frozenset({"active_worker", "uncertain_worker", "unreconciled_result",
                      "repair", "user_intervention"})


def _text(value, name):
    if type(value) is not str or not value.strip() or "\x00" in value:
        raise ValueError(f"{name} must be a nonempty identity")
    return value


def _review(report, receipt, candidate_artifact, review_artifact):
    if type(receipt) is not dict or receipt.get("authenticated") is not True:
        raise ValueError("review requires authenticated runner acceptance")
    if receipt.get("accepted") is not True or report.get("accepted") is not True:
        raise ValueError("independent review must explicitly accept the candidate")
    for key in ("product_changes", "permission_changes", "unresolved_product_decisions"):
        if report.get(key) is not False:
            raise ValueError(f"review {key} requires user intervention")
    if report.get("candidate_sha256") != candidate_artifact["sha256"]:
        raise ValueError("review does not bind the exact candidate artifact")
    if receipt.get("artifact") != review_artifact:
        raise ValueError("runner acceptance belongs to another review artifact")
    if receipt.get("output_hash") != util.digest(report):
        raise ValueError("review output does not match runner accepted output")
    for key in ("stage", "role", "session"):
        expected = _text(receipt.get(key), f"runner review {key}")
        if report.get(key) != expected:
            raise ValueError(f"review {key} differs from runner acceptance")
        _text(receipt.get("planner_" + key), f"runner planner {key}")
    if (receipt["session"] == receipt["planner_session"]
            or receipt["role"] == receipt["planner_role"]
            or receipt["stage"] == receipt["planner_stage"]):
        raise ValueError("review must have an independent stage, role and session")


def prepare_activation(*, run_dir, contract, contract_authenticated, progressive,
                       previous_plan, candidate_artifact, review_artifact,
                       source_snapshot, review_receipt, verified_done=(),
                       verified_criteria=(), required_checks=(), product_findings=(),
                       blockers):
    """Return detached active/checklist/criteria data and a stable transition ID.

    ``previous_plan`` is the runner-recorded {proposal, plan_hash}. This helper
    prepares revisions only, not the initial approval/sealing action. Revision
    artifacts store report={"proposal": proposal}; candidate_identity and
    plan_identity are its plan hash. Review reports bind candidate_sha256 to the
    exact candidate artifact hash.
    Review envelopes repeat all candidate envelope bindings, including its
    predecessor. Their report schema is checked by ``_review`` above. Source
    binding is util.digest(source_snapshot). Findings are runner-normalized rows
    with explicit ``blocking`` booleans; unknown blocking status fails closed.
    Existing required_checks includes all product/constraint obligations and
    earlier demonstrations, not just checks selected for the current task.
    No retirement authority is accepted by this technical revision helper.
    """
    if type(blockers) is not dict or set(blockers) != BLOCKERS:
        raise ValueError("activation needs the complete normalized boundary blockers")
    if any(value is not False for value in blockers.values()):
        raise ValueError("activation boundary is blocked")
    if contract_authenticated is not True or not contract_identity.sealed(contract):
        raise ValueError("activation requires the authenticated current sealed approval")
    if contract.get("approval_status") != "approved":
        raise ValueError("activation requires current contract approval")
    token = contract_identity.token(contract)
    plan.require_delegation(progressive, contract_token=token, contract_body=contract["body"],
                            contract_approved=True, contract_sealed=True)
    if type(source_snapshot) is not dict or not source_snapshot:
        raise ValueError("activation needs the actual retained source snapshot")
    source_hash = util.digest(source_snapshot)
    for finding in product_findings:
        if type(finding) is not dict or type(finding.get("blocking")) is not bool:
            raise ValueError("product finding blocking status must be normalized")
        if finding["blocking"]:
            raise ValueError("blocking product findings prevent activation")

    candidate = artifacts.verify(run_dir, candidate_artifact)
    review = artifacts.verify(run_dir, review_artifact)
    proposal = candidate["report"].get("proposal")
    if type(proposal) is not dict:
        raise ValueError("candidate report must contain its structured proposal")
    plan_hash = plan.plan_identity(proposal)
    criteria = [row["id"] for row in contract["body"]["acceptance_criteria"]]
    if type(previous_plan) is not dict or type(previous_plan.get("proposal")) is not dict:
        raise ValueError("previous plan must be runner-recorded proposal and identity")
    predecessor = plan.plan_identity(previous_plan["proposal"])
    if previous_plan.get("plan_hash") != predecessor:
        raise ValueError("previous plan identity mismatch")
    bindings = {"contract_token": token, "predecessor_identity": predecessor,
                "candidate_identity": plan_hash, "plan_identity": plan_hash,
                "source_snapshot_identity": source_hash}
    if candidate["kind"] != "revision":
        raise ValueError("candidate artifact has the wrong activation kind")
    if review["kind"] != "review":
        raise ValueError("review artifact has the wrong kind")
    for key, expected in bindings.items():
        if candidate[key] != expected or review[key] != expected:
            raise ValueError(f"candidate/review {key} binding mismatch")
    _review(review["report"], review_receipt, candidate_artifact, review_artifact)
    parsed = plan.validate_proposal(proposal, criteria,
                                    verified_done=verified_done, verified_criteria=verified_criteria)
    carried = list(required_checks)
    revision = plan.validate_revision(previous_plan["proposal"], proposal, criteria,
                                      established=carried, contract_token=token,
                                      verified_done=verified_done, verified_criteria=verified_criteria)
    carried = revision["carried"]
    checks = plan.cumulative_checks(proposal, carried)
    for check in checks:
        normalized = plan.validate_check(check)
        if set(normalized["criterion_ids"]) - set(criteria):
            raise ValueError("required check names unknown product criteria")
    result = {"active": {"definition": parsed["slices"][0], "plan_hash": plan_hash,
                         "artifact": candidate_artifact, "review": review_artifact},
              "required_checks": checks,
              "outstanding_criteria": sorted(set(criteria) - set(verified_criteria))}
    return deepcopy(result)
