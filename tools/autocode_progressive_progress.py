"""Pure progressive stagnation policy over runner-normalized boundary evidence.

Caller contract (none of this module's inputs grant execution authority):
* due_checks is the complete authoritative set for this boundary, as rows with
  id and check_hash (the reviewed executable definition identity). Intermediate
  tasks supply their due subset; checkpoints supply the cumulative required set.
* current_binding comes from immutable dispatch records plus the independently
  validated current source, NOT the plan/task active when a report arrives.
* results maps check IDs to receipts with status, exit_code, identity, check_hash,
  source_revision, binding and nonempty evidence_hashes. receipts_authenticated
  must be exactly True only after the caller verifies runner provenance, actual
  execution, report integrity and source/evidence hashes for ALL supplied results.
* proof_identity is an authenticated, stable identity of this boundary's evidence
  manifest, not a model label or a newly allocated ID on every classification.
  seen_proof_identities and seen_receipt_identities retain consumed identities
  across retries, repairs and slice/plan changes; they must not be reset locally.
* new_obligation_ids names independently verified new slice/task obligations,
  not a model's claim of NEW. At least one must be due and have fresh runner proof.
* gaps lists ALL outstanding criterion/task gaps, with kind, id and status.
  Only OPEN/NOT_VERIFIED gaps explicitly in deferred_criteria/deferred_tasks are
  expected. Real failures and ledger findings cannot be relabeled as deferrals.
* findings is the ENTIRE authenticated product ledger, using existing status,
  severity and blocking semantics. Closure authentication remains with its owner.

Returns {kind: progress|failure|pending, reason: stable code, proof_identity}.
Only progress returns an identity for consumption. Pending (no proof/new work or
duplicate evidence) is NOT progress and must not reset stagnation. Failures retain
ordinary rework/escalation handling. No counters, state writes, I/O or clocks.
"""

_BINDING_TEXT = ("contract_token", "plan_hash", "slice_id", "task_id", "assignment_source", "validated_source")


def _text(value):
    return type(value) is str and bool(value.strip()) and "\x00" not in value


def _ids(value):
    if type(value) not in (list, tuple, set, frozenset) or not all(_text(v) for v in value):
        raise ValueError("invalid identity collection")
    if len(value) != len(set(value)):
        raise ValueError("duplicate identity")
    return set(value)


def _binding(value):
    return (
        type(value) is dict
        and set(value) == {*_BINDING_TEXT, "attempt"}
        and all(_text(value[key]) for key in _BINDING_TEXT)
        and type(value["attempt"]) is int
        and value["attempt"] > 0
    )


def classify(
    *,
    due_checks,
    results,
    current_binding,
    proof_identity,
    receipts_authenticated,
    new_obligation_ids,
    seen_proof_identities,
    seen_receipt_identities,
    gaps,
    deferred_criteria,
    deferred_tasks,
    findings,
):
    """Classify normalized evidence; malformed input fails closed, never mutates.

    No separate checkpoint flag: the caller's authoritative due set determines
    which checks must pass. A deferred ID never exempts a due check or a blocker.
    """

    def outcome(kind, reason):
        return {"kind": kind, "reason": reason, "proof_identity": proof_identity if kind == "progress" else None}

    try:
        new = _ids(new_obligation_ids)
        seen = _ids(seen_proof_identities)
        seen_receipts = _ids(seen_receipt_identities)
        deferred = {"criterion": _ids(deferred_criteria), "task": _ids(deferred_tasks)}
    except ValueError:
        return outcome("failure", "malformed_input")
    if type(findings) is not list:
        return outcome("failure", "malformed_findings")
    for row in findings:
        if (
            type(row) is not dict
            or row.get("status") not in ("open", "resolved", "retracted")
            or row.get("severity") not in ("critical", "high", "medium", "low")
            or type(row.get("blocking", True)) is not bool
        ):
            return outcome("failure", "malformed_findings")
        # Match the product ledger: severity does not override an explicit flag.
        if row["status"] == "open" and row.get("blocking", True):
            return outcome("failure", "product_blocker")
    if type(gaps) is not list:
        return outcome("failure", "malformed_gaps")
    gap_ids = set()
    for gap in gaps:
        if (
            type(gap) is not dict
            or type(gap.get("kind")) is not str
            or gap["kind"] not in deferred
            or not _text(gap.get("id"))
            or type(gap.get("status")) is not str
        ):
            return outcome("failure", "malformed_gaps")
        key = (gap["kind"], gap["id"])
        if key in gap_ids:
            return outcome("failure", "malformed_gaps")
        gap_ids.add(key)
        if gap["status"] not in ("OPEN", "NOT_VERIFIED") or gap["id"] not in deferred[gap["kind"]]:
            return outcome("failure", "unexpected_gap")
    if not _binding(current_binding):
        return outcome("failure", "malformed_binding")
    if type(due_checks) is not list or type(results) is not dict:
        return outcome("failure", "malformed_checks")
    due = {}
    for check in due_checks:
        if (
            type(check) is not dict
            or not _text(check.get("id"))
            or not _text(check.get("check_hash"))
            or check["id"] in due
        ):
            return outcome("failure", "malformed_checks")
        due[check["id"]] = check["check_hash"]
    if any(not _text(key) or key not in due for key in results) or not new <= due.keys():
        return outcome("failure", "malformed_checks")
    if not due:
        return outcome("pending", "no_due_checks")
    if receipts_authenticated is not True:
        return outcome("failure", "unauthenticated_receipts")
    if not _text(proof_identity):
        return outcome("failure", "malformed_proof_identity")
    receipt_ids = set()
    for check_id, check_hash in due.items():
        receipt = results.get(check_id)
        if receipt is None:
            return outcome("failure", "missing_due_check")
        if type(receipt) is not dict:
            return outcome("failure", "malformed_receipt")
        if receipt.get("status") != "PASS":
            return outcome("failure", "due_check_not_pass")
        if type(receipt.get("exit_code")) is not int or receipt["exit_code"] != 0:
            return outcome("failure", "due_check_not_pass")
        if not _text(receipt.get("identity")) or receipt["identity"] in receipt_ids:
            return outcome("failure", "malformed_receipt")
        receipt_ids.add(receipt["identity"])
        if not _binding(receipt.get("binding")):
            return outcome("failure", "malformed_binding")
        if (
            receipt["binding"] != current_binding
            or receipt.get("check_hash") != check_hash
            or receipt.get("source_revision") != current_binding["validated_source"]
        ):
            return outcome("failure", "stale_receipt")
        evidence = receipt.get("evidence_hashes")
        if (
            type(evidence) is not dict
            or not evidence
            or not all(_text(path) and _text(digest) for path, digest in evidence.items())
        ):
            return outcome("failure", "malformed_receipt")
    # A duplicate never hides a regression or blocker: validate everything first.
    if proof_identity in seen or receipt_ids & seen_receipts:
        return outcome("pending", "duplicate_proof")
    if not new:
        return outcome("pending", "no_new_obligation")
    return outcome("progress", "new_verified_due_proof")
