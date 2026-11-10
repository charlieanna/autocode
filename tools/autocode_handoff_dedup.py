"""Lossless references for repeated text in Builder and independent-review prompts.

The approved contract and all reported/replayed results remain inline. References
are presentation details of a copied handoff, never persisted run-state changes.
"""

import copy
import hashlib
import json

REFERENCE_POLICY = (
    "Fields ending in _ref below can be inline JSON pointers beginning #/. "
    "Resolve these against this handoff, not an external file. acceptance_criteria_ref "
    "selects the named fields from EVERY criterion in the complete approved contract. "
    "command_ref supplies only the command text; each replay keeps its own outcome "
    "and evidence. References do not narrow scope or turn reported PASS into verified PASS."
)
CHANGED_CODE_POLICY = (
    " A changed-line record's reason_ref supplies its reason text from the obligation with the same gap_id; "
    "each record keeps its own gap_id, decision, status and evidence. changed_code_receipts_ref names the "
    "identical receipt list at its path. A changed_code_receipts entry holding only receipt_ref is the "
    "identical receipt at that path, in the same position of the list."
)


def _claim_references(packet, criteria):
    """Reference only exact, identity-bound criterion declarations in this packet."""
    if packet.get("stage") == "write_acceptance_tests":
        return False
    obligations = packet.get("verification_obligations")
    checks = obligations.get("checks") if isinstance(obligations, dict) else None
    if not isinstance(checks, list):
        return False
    changed = False
    for check in checks:
        if not isinstance(check, dict) or "claim_ref" in check:
            continue
        ids = check.get("criterion_ids")
        if not isinstance(ids, list) or len(ids) != 1 or not isinstance(ids[0], str) or not ids[0]:
            continue
        matches = [(index, row) for index, row in enumerate(criteria) if row["id"] == ids[0]]
        if len(matches) != 1:
            continue
        index, criterion = matches[0]
        text = criterion["criterion"]
        identity = hashlib.sha256(json.dumps(criterion, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        purpose = "human_review" if criterion.get("human_review") else "approved_acceptance"
        reference = f"#/goal_contract/body/acceptance_criteria/{index}/criterion"
        if (
            not isinstance(text, str)
            or not text
            or check.get("claim") != text
            or check.get("criterion_identity") != identity
            or check.get("purpose") != purpose
            or len(json.dumps(text)) <= len(json.dumps(reference)) + 4
        ):
            continue
        del check["claim"]
        check["claim_ref"] = reference
        changed = True
    return changed


def _changed_code_references(packet):
    """Reference changed-line reason text and receipts repeated byte for byte in this packet.

    The obligation that first states a gap's reason keeps it; a later record of the same gap_id
    whose reason is identical points at it. Gap ids, hashes, decisions and evidence stay in place.
    """
    changed = False
    proof = packet.get("regression_proof")
    proof = proof if isinstance(proof, dict) else {}
    coverage = proof.get("diff_coverage")
    receipts = proof.get("changed_code_receipts")
    if (
        isinstance(coverage, dict)
        and isinstance(receipts, list)
        and receipts
        and "changed_code_receipts_ref" not in proof
        and coverage.get("command_receipts") == receipts
    ):
        del proof["changed_code_receipts"]
        proof["changed_code_receipts_ref"] = "#/regression_proof/diff_coverage/command_receipts"
        changed = True
    elif (
        isinstance(coverage, dict) and isinstance(receipts, list) and isinstance(coverage.get("command_receipts"), list)
    ):
        # The changed-code receipts begin with the coverage run's own (autocode_changed_code_proof), then
        # add the mutation runs', so the whole-list reference above never applies once a mutant ran: a
        # live two-line fix carried both coverage receipts twice, about 4.5 KB per review prompt.
        for index, (row, original) in enumerate(zip(receipts, coverage["command_receipts"], strict=False)):
            pointer = f"#/regression_proof/diff_coverage/command_receipts/{index}"
            if row != original or not isinstance(row, dict) or len(json.dumps(row)) <= len(pointer) + 20:
                break
            receipts[index] = {"receipt_ref": pointer}
            changed = True
    reasons = {}

    def refer(record, gap, pointer):
        nonlocal changed
        reason = record.get("reason")
        if (
            "reason_ref" in record
            or not isinstance(reason, str)
            or reason != reasons.get(gap, (None,))[0]
            or len(json.dumps(reason)) <= len(json.dumps(pointer)) + 4
        ):
            return
        del record["reason"]
        record["reason_ref"] = pointer
        changed = True

    for prefix, evidence in (
        ("#/regression_proof/changed_code_evidence", proof.get("changed_code_evidence")),
        ("#/changed_code_evidence", packet.get("changed_code_evidence")),
    ):
        rows = evidence.get("obligations") if isinstance(evidence, dict) else None
        for index, row in enumerate(rows if isinstance(rows, list) else []):
            if not isinstance(row, dict) or not isinstance(row.get("gap_id"), str):
                continue
            gap = row["gap_id"]
            if isinstance(row.get("reason"), str) and gap not in reasons:
                reasons[gap] = (row["reason"], f"{prefix}/obligations/{index}/reason")
            exception = row.get("exception")
            if isinstance(exception, dict) and gap in reasons:
                refer(exception, gap, reasons[gap][1])
    validation = packet.get("validation")
    for key in ("changed_code_decisions", "changed_code_exceptions"):
        rows = validation.get(key) if isinstance(validation, dict) else None
        for row in rows if isinstance(rows, list) else []:
            if isinstance(row, dict) and isinstance(row.get("gap_id"), str) and row["gap_id"] in reasons:
                refer(row, row["gap_id"], reasons[row["gap_id"]][1])
    return changed


def deduplicate(base):
    """Copy a packet, replacing only exact duplicates when the whole packet shrinks."""
    result = copy.deepcopy(base)
    if result.get("stage") not in (
        "builder",
        "tester",
        "review_completion",
        "review_checkpoint",
        "write_acceptance_tests",
    ):
        return result
    changed = False
    case_references = False
    contract = result.get("goal_contract") or {}
    body = contract.get("body") if isinstance(contract, dict) else None
    criteria = body.get("acceptance_criteria") if isinstance(body, dict) else None
    if (
        isinstance(criteria, list)
        and criteria
        and all(isinstance(c, dict) and "id" in c and "criterion" in c for c in criteria)
    ):
        changed = _claim_references(result, criteria) or changed
        definitions = [{k: c[k] for k in ("id", "criterion")} for c in criteria]
        if result.get("acceptance_criteria") == definitions and "acceptance_criteria_ref" not in result:
            del result["acceptance_criteria"]
            result["acceptance_criteria_ref"] = {
                "path": "#/goal_contract/body/acceptance_criteria",
                "fields": ["id", "criterion"],
            }
            changed = True
        if result.get("stage") == "write_acceptance_tests":
            for row in result.get("cases") or []:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str) or "criterion_ref" in row:
                    continue
                matches = [(i, c) for i, c in enumerate(criteria) if c["id"] == row.get("id")]
                if len(matches) != 1:
                    continue
                index, criterion = matches[0]
                text = criterion["criterion"]
                if not isinstance(text, str) or not text:
                    continue
                prefix = f"{row['id']}: "
                if row.get("criterion") == text:
                    prefix = ""
                elif row.get("criterion") != prefix + text:
                    continue
                row["criterion_ref"] = {
                    "path": f"#/goal_contract/body/acceptance_criteria/{index}/criterion",
                    "prefix": prefix,
                }
                del row["criterion"]
                changed = case_references = True
    validation = result.get("validation")
    if isinstance(validation, dict):
        checks = validation.get("checks")
        replay = validation.get("check_replay")
        if isinstance(checks, list) and isinstance(replay, dict) and isinstance(replay.get("checks"), list):
            commands = {}
            for i, check in enumerate(checks):
                command = check.get("command") if isinstance(check, dict) else None
                if isinstance(command, str) and command:
                    commands.setdefault(command, f"#/validation/checks/{i}/command")
            for row in replay["checks"]:
                if not isinstance(row, dict) or "command_ref" in row:
                    continue
                command = row.get("command")
                ref = commands.get(command) if isinstance(command, str) else None
                if ref and len(json.dumps(command)) > len(json.dumps(ref)) + 4:
                    del row["command"]
                    row["command_ref"] = ref
                    changed = True
    changed_code = _changed_code_references(result)
    changed = changed or changed_code
    if changed:
        result["handoff_reference_policy"] = REFERENCE_POLICY
        if changed_code:
            result["handoff_reference_policy"] += CHANGED_CODE_POLICY
        if case_references:
            result["handoff_reference_policy"] += (
                " Each cases[].criterion_ref supplies the case text: concatenate its prefix with the string "
                "at its path in this handoff. Keep every case ID, test name and kind, including shared test names."
            )
        if len(json.dumps(result).encode()) >= len(json.dumps(base).encode()):
            return copy.deepcopy(base)
    return result
