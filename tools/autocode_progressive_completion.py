"""Read-only additional whole-product completion gate.

The sole progressive writer publishes ``completion_proof`` only after replay.
Its payload is {version: 1, contract_token, plan_hash, source_revision, slice_id,
required_checks: [check definitions], results: {check_id: receipt},
criterion_ids: [all original product IDs], verified_slices: [history slice IDs]}.
The state proof adds ``artifact``; the persisted checkpoint's report is exactly
the payload. Envelope token/plan/source match the payload, candidate_identity
is plan_hash and predecessor_identity is the active candidate artifact SHA256.

Each receipt has normalize_receipt's fields plus replayed=True and executions:
[{command, status: 'PASS', exit_code: 0, evidence_hashes: {path: SHA256}}], one
per parsed command in order. Receipt evidence includes every execution pin.
History is [{slice_id, plan_hash, artifact, required_checks}], with the final
proof checkpoint last. Each history checkpoint report uses the same payload
format. Historical token/source need not equal current token/source: an old token
must identify a sealed, previously approved same-task contract in contract_history,
authenticated against the run's actual user_events by contract_identity.approved.
Old receipts are checked against their own checkpoint token/source, never rebound.
Historical PASS never substitutes for current replay. Retirements require the
exact canonical scope_exclusions line, also visibly shown in constraints, in an
authenticated user-approved contract.
The grant's proposal/revision artifact report must contain proposal, contract_body
(exact approved body), retired_checks (old definitions), and
predecessor_contract_token (the actual earlier approved contract). Only an exact
known old definition may be retired; all unrelated obligations remain mandatory.
Future and outstanding_criteria must be explicit
empty lists. Missing writer/checkpoint wiring therefore safely refuses completion.
Original explicit product commands due for full verification must already be
represented in the persisted checklist; local full-verification claims cannot
replace them. Prose methods remain independent Validator work, not guessed commands.
This reader never writes state, accepts a model authentication flag, or replaces
the ordinary criteria, findings, full-flow, independent-review or human gates.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

try:
    from . import autocode_contract_identity as contracts
    from . import autocode_progressive_artifacts as artifacts
    from . import autocode_progressive_plan as plan
    from . import autocode_progressive_state as ledger
    from . import autocode_util as util
    from . import autocode_verification_plan as verification_plan
except ImportError:
    import autocode_contract_identity as contracts
    import autocode_progressive_artifacts as artifacts
    import autocode_progressive_plan as plan
    import autocode_progressive_state as ledger
    import autocode_util as util
    import autocode_verification_plan as verification_plan


def _pins(pins, *, current=True):
    if type(pins) is not dict or not pins:
        raise ValueError("missing replay evidence")
    for path, digest in pins.items():
        if (
            type(path) is not str
            or not path
            or type(digest) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
            or (current and (not Path(path).is_file() or util.file_hash(path) != digest))
        ):
            raise ValueError("missing or changed replay evidence")


def _checks(rows):
    if type(rows) is not list or not rows:
        raise ValueError("missing cumulative checklist")
    indexed = {}
    for row in rows:
        identity = plan.check_identity(row)
        if row["id"] in indexed:
            raise ValueError("duplicate check identity")
        indexed[row["id"]] = identity
    return indexed


def _receipt(receipt, check, token, source, *, current):
    plan.normalize_receipt(receipt, check, contract_token=token, source_revision=source, authenticated=True)
    if receipt.get("replayed") is not True:
        raise ValueError("no explicit successful replay")
    _pins(receipt["evidence_hashes"], current=current)
    executions = receipt.get("executions")
    commands = plan.check_commands(check)
    if type(executions) is not list or len(executions) != len(commands):
        raise ValueError("incomplete command replay")
    for command, execution in zip(commands, executions, strict=False):
        if (
            type(execution) is not dict
            or execution.get("command") != command
            or execution.get("status") != "PASS"
            or type(execution.get("exit_code")) is not int
            or execution["exit_code"] != 0
        ):
            raise ValueError("unsuccessful command replay")
        _pins(execution.get("evidence_hashes"), current=current)
        if any(receipt["evidence_hashes"].get(path) != digest for path, digest in execution["evidence_hashes"].items()):
            raise ValueError("execution evidence absent from receipt")


def _checkpoint(state, identity):
    envelope = artifacts.verify(state.get("run_dir"), identity)
    proof = envelope["report"]
    if envelope["kind"] != "checkpoint" or type(proof.get("version")) is not int or proof["version"] != 1:
        raise ValueError("missing versioned checkpoint proof")
    for field, binding in (
        ("contract_token", "contract_token"),
        ("plan_hash", "plan_identity"),
        ("source_revision", "source_snapshot_identity"),
        ("plan_hash", "candidate_identity"),
    ):
        if proof.get(field) != envelope[binding]:
            raise ValueError("checkpoint binding differs from its stored report")
    return envelope, proof


def _approved_contract(state, token):
    """Resolve authority from the actual sealed approval, never a grant assertion."""
    if type(token) is not str or not token:
        return None
    current = state["goal_contract"]
    if contracts.token(current) == token and contracts.approved(state):
        return current
    history = state.get("contract_history")
    if type(history) is not list:
        return None
    for contract in history:
        if (
            type(contract) is dict
            and type(contract.get("revision")) is int
            and 0 < contract["revision"] < current["revision"]
            and contract.get("task_id") == current["task_id"]
            and contract.get("hash")
            and contracts.token(contract) == token
            and contracts.approved({"goal_contract": contract, "user_events": state.get("user_events", [])})
        ):
            return contract
    return None


def _retirement_contract(state, grant):
    fields = {"kind", "check_id", "check_hash", "removes", "contract_token", "visible_removal", "artifact"}
    if type(grant) is not dict or set(grant) != fields or grant["kind"] != "product_change":
        raise ValueError("retirement requires an exact approval-bound product-change grant")
    payload = {key: grant[key] for key in ("check_id", "check_hash", "removes")}
    if any(
        type(value) is not str or not value.strip() or "\x00" in value for value in payload.values()
    ) or not re.fullmatch(r"[0-9a-f]{64}", grant["check_hash"]):
        raise ValueError("retirement does not name an exact check and visible removal")
    line = "Progressive check retirement: " + json.dumps(payload, sort_keys=True, separators=(",", ":"))
    contract = _approved_contract(state, grant["contract_token"])
    if (
        contract is None
        or contract["approval_event"].get("actor") != "user_cli"
        or grant["visible_removal"] != line
        or type(contract["body"].get("scope_exclusions")) is not list
        or line not in contract["body"]["scope_exclusions"]
        or type(contract["body"].get("constraints")) is not list
        or line not in contract["body"]["constraints"]
    ):
        raise ValueError("retirement is absent from an authenticated user-approved contract")
    return contract


def _retirements(state, record, identities, obligations):
    grants = record.get("retirements", [])
    if type(grants) is not list:
        raise ValueError("retirements must be explicit approval-bound grants")
    retired = {}
    for grant in grants:
        contract = _retirement_contract(state, grant)
        envelope = artifacts.verify(state.get("run_dir"), grant["artifact"])
        report = envelope["report"]
        predecessor = _approved_contract(state, report.get("predecessor_contract_token"))
        if (
            envelope["kind"] not in ("proposal", "revision")
            or envelope["contract_token"] != grant["contract_token"]
            or report.get("contract_body") != contract["body"]
            or predecessor is None
            or predecessor["revision"] >= contract["revision"]
            or envelope["candidate_identity"] != envelope["plan_identity"]
            or plan.plan_identity(report["proposal"]) != envelope["plan_identity"]
        ):
            raise ValueError("retirement artifact does not bind the actual revised approved contract")
        old_checks = report.get("retired_checks")
        _checks(old_checks)
        pair = (grant["check_id"], grant["check_hash"])
        if (
            pair in retired
            or grant["check_id"] in identities
            or not any((check["id"], plan.check_identity(check)) == pair for check in old_checks)
            or not any(
                (check["id"], plan.check_identity(check)) == pair and old["revision"] <= predecessor["revision"]
                for check, old in obligations
            )
        ):
            raise ValueError("retirement is unknown, kept, duplicated or mismatches the old definition")
        retired[pair] = contract["revision"]
    return retired


def ready(state, current):
    """Additional predicate; ordinary absence is True, invalid progressive proof False."""
    try:
        body = (state.get("goal_contract") or {}).get("body") or {}
        prefixes = (plan.DISCLOSURE_DELEGATION, plan.DISCLOSURE_SLICE, plan.DISCLOSURE_OUTSTANDING)
        disclosed = any(
            isinstance(line, str) and line.startswith(prefixes)
            for field in ("constraints", "technical_approach")
            for line in body.get(field, [])
        )
        record = ledger.view(state)
        if not disclosed and not record and "progressive" not in state:
            return True
        # A candidate-only ordinary planning record is not execution authority.
        if (
            not disclosed
            and type(record) is dict
            and type(record.get("version")) is int
            and record["version"] == 1
            and set(record) <= {"version", "candidate"}
        ):
            return True
        if type(record) is not dict or type(record.get("version")) is not int or record["version"] != 1:
            return False
        active = ledger.require_active(state)
        candidate = artifacts.verify(state.get("run_dir"), active["artifact"])
        review = artifacts.verify(state.get("run_dir"), active["review"])
        proposal = candidate["report"]["proposal"]
        if (
            candidate["kind"] not in ("proposal", "revision")
            or review["predecessor_identity"] != candidate["predecessor_identity"]
            or record.get("future") != []
            or record.get("outstanding_criteria") != []
            or proposal.get("outstanding_criteria") != []
            or len(proposal["slices"]) != 1
        ):
            return False
        proof = record.get("completion_proof")
        if type(proof) is not dict:
            return False
        envelope, stored = _checkpoint(state, proof["artifact"])
        if stored != {key: value for key, value in proof.items() if key != "artifact"}:
            return False
        token = contracts.token(state["goal_contract"])
        source = current["revision"]
        if (
            type(source) is not str
            or not source
            or proof.get("contract_token") != token
            or proof.get("plan_hash") != active["plan_hash"]
            or proof.get("source_revision") != source
            or proof.get("slice_id") != active["definition"]["id"]
            or envelope["predecessor_identity"] != active["artifact"]["sha256"]
        ):
            return False
        required = record.get("required_checks")
        identities = _checks(required)
        if proof.get("required_checks") != required or verification_plan.product_checks(body, required):
            return False
        for check in active["definition"]["checks"]:
            if identities.get(check["id"]) != plan.check_identity(check):
                return False
        history = record.get("history")
        if type(history) is not list or not history or history[-1].get("artifact") != proof["artifact"]:
            return False
        slices = []
        obligations = [
            (check, state["goal_contract"]) for check in record["initial_plan"]["proposal"]["slices"][0]["checks"]
        ]
        for entry in history:
            _, past = _checkpoint(state, entry["artifact"])
            past_token = past.get("contract_token")
            if (
                entry.get("slice_id") != past.get("slice_id")
                or entry.get("plan_hash") != past.get("plan_hash")
                or entry.get("required_checks") != past.get("required_checks")
                or not _approved_contract(state, past_token)
            ):
                return False
            _checks(entry["required_checks"])
            slices.append(entry["slice_id"])
            for check in entry["required_checks"]:
                obligations.append((check, _approved_contract(state, past_token)))
                _receipt(past["results"][check["id"]], check, past_token, past["source_revision"], current=False)
        if record.get("retirements"):
            archives = record.get("execution_history", [])
            if type(archives) is not list:
                return False
            for archive in archives:
                saved = archive["active"]
                previous = artifacts.verify(state.get("run_dir"), saved["artifact"])
                old = _approved_contract(state, previous["contract_token"])
                if old is None:
                    return False
                archived_state = {
                    "run_dir": state.get("run_dir"),
                    "goal_contract": old,
                    "user_events": state.get("user_events", []),
                    "progressive": {
                        "version": 1,
                        "active": saved,
                        "initial_plan": archive["initial_plan"],
                        "delegation": archive["delegation"],
                    },
                }
                verified = ledger.require_active(archived_state)
                checks = verified["definition"]["checks"]
                obligations.extend((check, old) for check in checks)
                obligations.extend((check, old) for check in verification_plan.product_checks(old["body"], checks))
        retired = _retirements(state, record, identities, obligations)
        for check, old in obligations:
            pair = (check["id"], plan.check_identity(check))
            if pair in retired:
                if old["revision"] >= retired[pair]:
                    return False
                continue
            replacement = next((row for row in required if row["id"] == check["id"]), None)
            if (
                replacement is None
                or replacement["relation"] != check["relation"]
                or set(replacement["criterion_ids"]) != set(check["criterion_ids"])
            ):
                return False
        if (
            len(slices) != len(set(slices))
            or proof.get("verified_slices") != slices
            or set(slices) != set(proposal["done_slices"]) | {active["definition"]["id"]}
        ):
            return False
        results = proof.get("results")
        if type(results) is not dict or set(results) != set(identities):
            return False
        for check in required:
            _receipt(results[check["id"]], check, token, source, current=True)
        criteria = [row["id"] for row in body["acceptance_criteria"]]
        plan.validate_proposal(proposal, criteria, verified_done=slices[:-1], verified_criteria=criteria)
        claimed = proof.get("criterion_ids")
        proven = plan.criterion_proof(
            required, results, contract_token=token, source_revision=source, receipts_authenticated=True
        )
        return (
            type(claimed) is list
            and len(claimed) == len(set(claimed))
            and set(claimed) == set(criteria)
            and set(proven) == set(criteria)
            and all(proven.values())
        )
    except (ValueError, TypeError, KeyError, AttributeError, OSError):
        return False
