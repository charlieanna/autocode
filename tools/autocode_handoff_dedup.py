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


def deduplicate(base):
    """Copy a packet, replacing only exact duplicates when the whole packet shrinks."""
    result = copy.deepcopy(base)
    if result.get("stage") not in ("terra", "sol", "astra_review", "astra_checkpoint", "write_acceptance_tests"):
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
    if changed:
        result["handoff_reference_policy"] = REFERENCE_POLICY
        if case_references:
            result["handoff_reference_policy"] += (
                " Each cases[].criterion_ref supplies the case text: concatenate its prefix with the string "
                "at its path in this handoff. Keep every case ID, test name and kind, including shared test names."
            )
        if len(json.dumps(result).encode()) >= len(json.dumps(base).encode()):
            return copy.deepcopy(base)
    return result
