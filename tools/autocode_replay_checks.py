"""Counterfactual check receipts: archival observations, never current proof.

Only replay mode calls this adapter. Ordinary command_receipt, source/proof
verification and real check execution remain unchanged. Each consumed obligation
is matched to its recorded dispatch, source, contract, task and named case scope.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import copy
import hashlib
import json
import uuid
from pathlib import Path

try:
    from . import autocode_changed_code_proof as proof_policy
    from . import autocode_command_receipt as receipts
    from . import autocode_liveness as liveness
    from . import autocode_replay_bindings as identity_bindings
    from . import autocode_replay_recording as recording
    from . import autocode_replay_source_trace as trace_binding
    from . import autocode_replay_transport as transport
    from . import autocode_source_scope as source_scope
    from . import autocode_test_cases as test_cases
    from . import autocode_util as util
except ImportError:
    import autocode_changed_code_proof as proof_policy
    import autocode_command_receipt as receipts
    import autocode_liveness as liveness
    import autocode_replay_bindings as identity_bindings
    import autocode_replay_recording as recording
    import autocode_replay_source_trace as trace_binding
    import autocode_replay_transport as transport
    import autocode_source_scope as source_scope
    import autocode_test_cases as test_cases
    import autocode_util as util


def simulated(settings):
    return (settings or {}).get("replay_recording", {}).get("checks") == "recorded"


def archival_result(tape):
    return {
        "simulated": True,
        "current_proof": False,
        "recorded_verdict": tape.document["expected"]["verdict"],
        "manifest_sha256": tape.sha256,
        "provenance": "archival_recording",
    }


def archived_completed(tape, row, value):
    """Authenticate original cleanup entirely from declared archival payloads.

    This is counterfactual evidence, not an ordinary command receipt admission.
    Original absolute names are lookup keys only; no original path is opened.
    """
    if (
        not isinstance(value, dict)
        or type(value.get("exit_code")) is not int
        or any(value.get(key) for key in ("timed_out", "interrupted", "error"))
    ):
        return False
    present = set(receipts.OWNERSHIP_FIELDS) & set(value)
    if not present:
        return True  # Explicit legacy integer exit; no ownership fact is invented.
    if present != set(receipts.OWNERSHIP_FIELDS) or value.get("supervision_errors") != []:
        return False
    metadata, pin = value["supervision"], value["supervision_sha256"]
    if not isinstance(metadata, dict):
        return False
    identity = row.get("supervision", {}).get(metadata.get("receipt"))
    if identity is None:
        return False
    artifact = tape.document["artifacts"][identity]
    if artifact.get("redacted") or artifact.get("original_sha256", artifact["sha256"]) != pin:
        return False
    try:
        raw = tape.artifact(identity)
        if len(raw) > receipts.MAX_RECEIPT_BYTES or hashlib.sha256(raw).hexdigest() != pin:
            return False
        captured = recording._json(raw)
        receipt = liveness.classify(metadata, {"receipt": captured})["receipt"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return bool(
        receipt
        and receipt["phase"] == "stopped"
        and not receipt["cleanup_error"]
        and receipt["cause"] in receipts.NORMAL_CAUSES
    )


def _commands(value):
    checks = value.get("checks")
    return list(checks.values()) if isinstance(checks, dict) else checks if isinstance(checks, list) else []


def authenticated(tape, row):
    """Reauthenticate portable raw receipt/ownership facts without filesystem effects."""
    if row["authentication"] != "intact" or tape.document["artifacts"][row["receipt"]].get("redacted"):
        raise recording.RecordingError("Replay diverged: archived check authentication is missing or invalid")
    receipt_bytes, output_bytes = tape.artifact(row["receipt"]), tape.artifact(row["output"])
    value = recording._json(receipt_bytes)
    if not isinstance(value, dict):
        raise recording.RecordingError("Replay diverged: archived check is malformed")
    # Retain normal nonzero base/negative evidence. Bool/unknown/partial exits
    # cannot be made valid by copying an archival envelope.
    if "exit_code" in value and (type(value["exit_code"]) is not int or not archived_completed(tape, row, value)):
        raise recording.RecordingError("Replay diverged: archived command is not complete")
    if any(not archived_completed(tape, row, item) for item in _commands(value)):
        raise recording.RecordingError("Replay diverged: archived command ownership is unavailable")
    if isinstance(value.get("receipt"), dict) and not archived_completed(tape, row, value["receipt"]):
        raise recording.RecordingError("Replay diverged: archived baseline ownership is unavailable")
    for item in [*_commands(value), *([value["receipt"]] if isinstance(value.get("receipt"), dict) else []), value]:
        for path, pin in recording.output_pins(item):
            identity = row.get("outputs", {}).get(path)
            if identity is None:
                raise recording.RecordingError("Replay diverged: archived raw command output is missing")
            artifact = tape.document["artifacts"][identity]
            if artifact.get("redacted") or hashlib.sha256(tape.artifact(identity)).hexdigest() != pin:
                raise recording.RecordingError("Replay diverged: archived raw command output is damaged or redacted")
    primary = tape.document["artifacts"][row["output"]]
    for _path, pin in recording.output_pins(value):
        if primary.get("redacted") or hashlib.sha256(output_bytes).hexdigest() != pin:
            raise recording.RecordingError("Replay diverged: archived output pin is damaged")
    return receipt_bytes, output_bytes, value


def replay_receipt(tape, check_id, destination):
    row = next((row for row in tape.document["checks"] if row["id"] == check_id), None)
    if row is None:
        raise recording.RecordingError("Replay diverged: archived check is missing")
    receipt_bytes, output_bytes, value = authenticated(tape, row)
    root = recording._root(destination)
    root.mkdir(parents=True, mode=0o700, exist_ok=True)
    directory = root / ("archival-" + uuid.uuid4().hex)
    directory.mkdir(mode=0o700)
    receipt_path, output_path = directory / "recorded-receipt.json", directory / "recorded-output.log"
    receipt_path.write_bytes(receipt_bytes)
    output_path.write_bytes(output_bytes)
    # Do not repin source_revision, capture_context, supervision or full_output.
    artifact = tape.document["artifacts"][row["output"]]
    envelope = {
        "simulated": True,
        "current_proof": False,
        "check_id": check_id,
        "receipt": str(receipt_path),
        "output": str(output_path),
        "recorded": value,
        "manifest_sha256": tape.sha256,
        "dispatch": row["dispatch"],
        "purpose": row["purpose"],
        "original_receipt_sha256": tape.document["artifacts"][row["receipt"]].get(
            "original_sha256", hashlib.sha256(receipt_bytes).hexdigest()
        ),
        "original_output_sha256": artifact.get("original_sha256", artifact["sha256"]),
    }
    util.atomic_json(directory / "archival.json", envelope)
    return envelope


def _context(state: RunState):
    task = identity_bindings.semantic_task(state.get("current_task"))
    return {
        "contract_body": copy.deepcopy((state.get("goal_contract") or {}).get("body")),
        "task": task,
        "case_ids": sorted(row["id"] for row in test_cases.proof_cases(state)),
    }


def _retained(state: RunState, tape, summary, purpose, *, case_ids=None, dispatch=None):
    """Authenticate reused archival bytes and their exact semantic obligation."""
    ledger = state.get("replay_result") or {}
    used = ledger.get("consumed_checks")
    ids = [row["id"] for row in tape.document["checks"]]
    if ledger.get("manifest_sha256") != tape.sha256 or not isinstance(used, list) or used != ids[: len(used)]:
        raise recording.RecordingError("Replay diverged: retained archival ledger is foreign or reordered")
    archive = summary.get("archival") or {}
    row = next((item for item in tape.document["checks"] if item["id"] == archive.get("check_id")), None)
    if (
        row is None
        or row["id"] not in used
        or row["purpose"] != purpose
        or archive.get("manifest_sha256") != tape.sha256
        or archive.get("dispatch") != row["dispatch"]
        or archive.get("purpose") != row["purpose"]
        or (dispatch is not None and row["dispatch"] != dispatch)
    ):
        raise recording.RecordingError("Replay diverged: retained archival occurrence differs")
    raw, output, value = authenticated(tape, row)
    receipt_entry = tape.document["artifacts"][row["receipt"]]
    output_entry = tape.document["artifacts"][row["output"]]
    if (
        archive.get("simulated") is not True
        or archive.get("current_proof") is not False
        or archive.get("original_receipt_sha256") != receipt_entry.get("original_sha256", receipt_entry["sha256"])
        or archive.get("original_output_sha256") != output_entry.get("original_sha256", output_entry["sha256"])
    ):
        raise recording.RecordingError("Replay diverged: retained archival original pins differ")
    source = tape.document["snapshots"][row["snapshot"]]
    if purpose == "regression_baseline":
        base = tape.document["snapshots"][tape.document["launch"]["base"]]["head"]
        if value.get("base") != base or state.get("base_commit") != base:
            raise recording.RecordingError("Replay diverged: retained pristine baseline differs")
    elif value.get("source_revision") != util.digest({"head": source["head"], "files": source["files"]}):
        raise recording.RecordingError("Replay diverged: retained archival source context is foreign")
    expected = _context(state)
    if case_ids is not None:
        expected["case_ids"] = sorted(case_ids)
    if trace_binding.rebind_context(tape, state, row.get("context"), row["dispatch"]) != expected:
        raise recording.RecordingError("Replay diverged: retained archival semantic scope differs")
    recording.verify_source(tape, row["snapshot"], state["workspace"])
    directory = recording._root(Path(state["workspace"]) / ".autocode" / "replay" / "checks")
    try:
        receipt_path, output_path = Path(archive["receipt"]), Path(archive["output"])
        receipt_name, output_name = receipt_path.relative_to(directory), output_path.relative_to(directory)
        if (
            receipt_path.parent != output_path.parent
            or len(receipt_name.parts) != 2
            or not receipt_name.parts[0].startswith("archival-")
            or receipt_name.name != "recorded-receipt.json"
            or output_name.name != "recorded-output.log"
            or recording._read(directory, receipt_name.as_posix())[0] != raw
            or recording._read(directory, output_name.as_posix())[0] != output
            or archive.get("recorded") != value
        ):
            raise recording.RecordingError("Replay diverged: retained archival payload changed")
    except (OSError, KeyError, ValueError, TypeError):
        raise recording.RecordingError("Replay diverged: retained archival payload is unavailable or changed") from None
    actual = {
        key: item for key, item in summary.items() if key not in ("simulated", "current_proof", "path", "archival")
    }
    if actual != value and not _reviewed_only(state, actual, value):
        raise recording.RecordingError("Replay diverged: retained archival summary differs")
    return row, value


def _reviewed_only(state: RunState, actual, value):
    """The retained proof differs from its archive only by the independent Tester's review of it (#2500).

    The review re-assesses the archived proof's own inputs with the decisions the Tester's report bound
    (autocode_changed_code_review_application); the same deterministic reassessment must reproduce it.
    """
    field = "changed_code_evidence"
    if {key: item for key, item in actual.items() if key != field} != {
        key: item for key, item in value.items() if key != field
    }:
        return False
    decisions = (state.get("validation") or {}).get("changed_code_decisions") or []
    return bool(decisions) and actual.get(field) == proof_policy.reviewed(value, decisions)


def _portable(commands):
    """Archived commands compared without the interpreter's location (#2500).

    A detected suite names the interpreter the recording host resolved (``/usr/bin/python3 -m unittest
    discover -v``); the replay host resolves its own. The program and its arguments are the obligation,
    where it was installed is the environment, so an absolute first word compares by its name only.
    """
    portable = []
    for command in commands or []:
        words = command.split(None, 1) if isinstance(command, str) else []
        if words and Path(words[0]).is_absolute():
            command = " ".join([Path(words[0]).name, *words[1:]])
        portable.append(command)
    return portable


def _obligation(state: RunState, workspace, record, purpose, commands, *, case_ids=None):
    tape = transport.selected(state["settings"])
    dispatch = (record.get("replay") or {}).get("dispatch_id") if record else None
    if not dispatch:
        count = transport.consumed(state)
        rows = tape.document["dispatches"]
        dispatch = rows[count]["id"] if count < len(rows) else None
    ledger = state.get("replay_result") or {}
    used = ledger.get("consumed_checks", [])
    wanted = [row["id"] for row in tape.document["checks"]]
    if (
        not isinstance(used, list)
        or used != wanted[: len(used)]
        or (ledger and ledger.get("manifest_sha256") != tape.sha256)
    ):
        raise recording.RecordingError("Replay diverged: archival obligation ledger is foreign or reordered")
    if len(used) >= len(wanted):
        raise recording.RecordingError("Replay diverged: archived obligation is missing or already consumed")
    row = tape.document["checks"][len(used)]
    if row["dispatch"] != dispatch or row["purpose"] != purpose:
        raise recording.RecordingError("Replay diverged: archived obligation occurrence differs")
    repetition = 1 + sum(
        item["dispatch"] == dispatch and item["purpose"] == purpose for item in tape.document["checks"][: len(used)]
    )
    if row["repetition"] != repetition:
        raise recording.RecordingError("Replay diverged: archived obligation repetition differs")
    recording.verify_source(tape, row["snapshot"], workspace)
    expected = trace_binding.rebind_context(tape, state, row.get("context"), row["dispatch"])
    actual = _context(state)
    if case_ids is not None:
        actual["case_ids"] = sorted(case_ids)
    if not isinstance(expected, dict) or expected != actual:
        raise recording.RecordingError("Replay diverged: archived contract, task or case scope differs")
    envelope = replay_receipt(tape, row["id"], Path(state["workspace"]) / ".autocode" / "replay" / "checks")
    value = envelope["recorded"]
    recorded_commands = value.get("commands")
    if isinstance(recorded_commands, dict) and purpose == "regression_proof":
        recorded_commands = [recorded_commands.get("suite"), recorded_commands.get("regression")]
    if recorded_commands is None and isinstance(value.get("checks"), list):
        recorded_commands = [item.get("command") for item in value["checks"]]
    if recorded_commands is None and value.get("command"):
        recorded_commands = [value["command"]]
    if _portable(recorded_commands) != _portable(list(commands)):
        raise recording.RecordingError(
            "Replay diverged: archived executable obligations differ: recorded "
            + json.dumps(recorded_commands)
            + ", current "
            + json.dumps(list(commands))
        )
    original_revision = util.digest(
        {
            "head": tape.document["snapshots"][row["snapshot"]]["head"],
            "files": tape.document["snapshots"][row["snapshot"]]["files"],
        }
    )
    if purpose == "regression_baseline":
        expected_base = tape.document["snapshots"][tape.document["launch"]["base"]]["head"]
        if value.get("base") != expected_base or state.get("base_commit") != expected_base:
            raise recording.RecordingError("Replay diverged: archived pristine baseline differs")
    elif value.get("source_revision") != original_revision:
        raise recording.RecordingError("Replay diverged: archived source context is foreign")
    state["replay_result"] = {**archival_result(tape), "pending": True, "consumed_checks": [*used, row["id"]]}
    return envelope


def replay(checks, state: RunState, workspace, record, *, prescribed=()):
    commands = [row["command"] for row in checks]
    commands.extend(command for command in prescribed if command not in commands)
    envelope = _obligation(state, workspace, record, "independent_clean_replay", commands)
    value = copy.deepcopy(envelope["recorded"])
    rows = value.get("checks")
    if not isinstance(rows, list) or any(
        not isinstance(row, dict)
        or type(row.get("exit_code")) is not int
        or row.get("timed_out")
        or row.get("interrupted")
        or row.get("error")
        for row in rows
    ):
        raise recording.RecordingError("Replay diverged: archived replay commands are incomplete")
    value.update(simulated=True, current_proof=False, archival=envelope)
    if value.get("verdict") != "PASS" or any(row["exit_code"] != 0 for row in rows):
        raise ValueError("Recorded check replay failed; original FAIL receipts remain in archival storage")
    return value


def require_supported(state: RunState):
    """Refuse obligations whose portable archival semantics v1 does not implement."""
    settings = state.get("settings") or {}
    body = (state.get("goal_contract") or {}).get("body") or {}
    protected = settings.get("protected_tests") or {}
    progressive = state.get("progressive") or {}
    milestones = body.get("milestones") or []
    if (
        protected.get("files")
        or protected.get("command")
        or settings.get("design_manifest")
        or settings.get("visual_runtime")
        or settings.get("visual_profile")
        or body.get("brief_acceptance")
        or body.get("risk_acceptance")
        or len(milestones) > 1
        or (isinstance(progressive, dict) and set(progressive) - {"version", "candidate"})
        or (
            (settings.get("orchestration") or {}).get("enabled")
            and (settings.get("orchestration") or {}).get("max_parallel", 1) > 1
        )
    ):
        raise recording.RecordingError(
            "Recorded replay has unsupported protected, lifecycle, visual or progressive obligations in v1"
        )
    if milestones:
        only = milestones[0]
        criteria = [row["id"] for row in body.get("acceptance_criteria") or []]
        task = state.get("current_task") or {}
        covered = only.get("acceptance_criteria") or []
        if (
            not criteria
            or len(covered) != len(set(covered))
            or set(covered) != set(criteria)
            or task.get("milestone_id") != only.get("id")
            or task.get("milestone_ids")
        ):
            raise recording.RecordingError(
                "Recorded replay requires one serial milestone covering the approved criteria"
            )


def regression(state: RunState, workspace, *, case_ids, suite, regression_command):
    require_supported(state)
    # A repeated pre-review guard may reuse only a freshly reauthenticated
    # archival result. It remains ineligible for every current-proof reader.
    tape = transport.selected(state["settings"])
    saved = state.get("regression_proof") or {}
    revision = source_scope.snapshot(workspace, state)["revision"]
    if (
        saved.get("simulated") is True
        and saved.get("source_revision") == revision
        and sorted(saved.get("case_scope") or []) == sorted(case_ids)
    ):
        row, value = _retained(state, tape, saved, "regression_proof", case_ids=case_ids)
        commands = value.get("commands") or {}
        if _portable([commands.get("suite"), commands.get("regression")]) != _portable([suite, regression_command]):
            raise recording.RecordingError("Replay diverged: retained archival executable obligations differ")
        _retained(state, tape, state.get("regression_baseline") or {}, "regression_baseline", case_ids=case_ids)
        return saved
    base = _obligation(state, workspace, None, "regression_baseline", [suite], case_ids=case_ids)
    baseline = base["recorded"]
    if not isinstance(baseline.get("receipt"), dict) or _portable([baseline.get("command")]) != _portable([suite]):
        raise recording.RecordingError("Replay diverged: archived baseline is incomplete")
    state["regression_baseline"] = {
        **copy.deepcopy(baseline),
        "simulated": True,
        "current_proof": False,
        "path": base["receipt"],
        "archival": base,
    }
    envelope = _obligation(state, workspace, None, "regression_proof", [suite, regression_command], case_ids=case_ids)
    value = copy.deepcopy(envelope["recorded"])
    names, scope = value.get("case_tests"), value.get("case_scope")
    # A build whose criteria name no test still owes the changed-line proof (#2474): its recorded
    # proof has no named cases, and its fail-to-pass tests are the Builder's own (#2500).
    named = (
        isinstance(names, dict) and not any(not names.get(case) for case in case_ids)
        if case_ids
        else names in (None, {}) and bool(value.get("fail_to_pass"))
    )
    if (
        not named
        or not isinstance(scope, list)
        or sorted(scope) != sorted(case_ids)
        or not isinstance(value.get("checks"), dict)
        or not value["checks"]
        or (value.get("baseline") or {}).get("health") != baseline.get("health")
        or (value.get("baseline") or {}).get("exit_code") != baseline["receipt"].get("exit_code")
    ):
        raise recording.RecordingError("Replay diverged: archived named regression or baseline semantics differ")
    value.update(simulated=True, current_proof=False, path=envelope["receipt"], archival=envelope)
    state["regression_proof"] = value
    return value


def proof(state: RunState, workspace, *, case_ids, command):
    """One explicitly declared standalone proof obligation (legacy fixture API)."""
    envelope = _obligation(state, workspace, None, "regression_proof", [command], case_ids=case_ids)
    value = copy.deepcopy(envelope["recorded"])
    if sorted(value.get("case_scope") or []) != sorted(case_ids) or any(
        not (value.get("case_tests") or {}).get(case) for case in case_ids
    ):
        raise recording.RecordingError("Replay diverged: archived named regression cases are missing")
    value.update(simulated=True, current_proof=False, path=envelope["receipt"], archival=envelope)
    state["regression_proof"] = value
    return value


def archival_ready(state: RunState, current, *, logical_ready, required_case_ids=None):
    """Logical eligibility is supplied by the existing shared domain predicate.

    This authenticates only consumed archival obligations. It grants neither
    current completion nor a command receipt/pin used by ordinary readers.
    """
    if logical_ready is not True or not simulated(state.get("settings")):
        return False
    try:
        require_supported(state)
        tape = transport.selected(state["settings"])
        ledger = state.get("replay_result") or {}
        expected = [row["id"] for row in tape.document["checks"]]
        used = ledger.get("consumed_checks")
        replay = (state.get("validation") or {}).get("check_replay") or {}
        archive = replay.get("archival") or {}
        rows = replay.get("checks")
        if (
            not expected
            or ledger.get("manifest_sha256") != tape.sha256
            or used != expected
            or replay.get("simulated") is not True
            or replay.get("current_proof") is not False
            or replay.get("verdict") != "PASS"
            or replay.get("source_revision") != current["revision"]
            or archive.get("check_id") not in used
            or not isinstance(rows, list)
            or not rows
            or any(
                type(row.get("exit_code")) is not int
                or row["exit_code"] != 0
                or row.get("timed_out")
                or row.get("interrupted")
                or row.get("error")
                for row in rows
            )
        ):
            return False
        if required_case_ids is not None and not _proof_retained(state, current["revision"], required_case_ids):
            return False
        validation = state.get("validation") or {}
        actual_record = next(
            (
                record
                for record in state.get("stages", [])
                if record.get("output") == validation.get("output") and record.get("replay")
            ),
            None,
        )
        if actual_record is None:
            return False
        _retained(state, tape, replay, "independent_clean_replay", dispatch=actual_record["replay"]["dispatch_id"])
        return True
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _proof_retained(state: RunState, revision, case_ids):
    """The simulated proof is the archived passing proof of ``revision`` for ``case_ids``; raises on divergence."""
    tape = transport.selected(state["settings"])
    used = (state.get("replay_result") or {}).get("consumed_checks") or []
    proof = state.get("regression_proof") or {}
    wanted = set(case_ids)
    if (
        proof.get("simulated") is not True
        or proof.get("current_proof") is not False
        or proof.get("verdict") != "PASS"
        or proof.get("source_revision") != revision
        or not wanted <= set(proof.get("case_scope") or [])
        or any(not (proof.get("case_tests") or {}).get(case) for case in wanted)
        or (proof.get("archival") or {}).get("check_id") not in used
    ):
        return False
    _retained(state, tape, proof, "regression_proof", case_ids=case_ids)
    _retained(state, tape, state.get("regression_baseline") or {}, "regression_baseline", case_ids=case_ids)
    return True


def proof_retained(state: RunState, revision, case_ids):
    """Whether the runner's own archived proof stands for ``revision`` in a recorded-checks replay (#2500).

    Recorded simulation never grants current proof or completion (completion_ready); the gates that need
    only the runner's proof of the source, such as the proof finding (autocode_proof_findings), read the
    archive's authentication through here instead of a flat refusal.
    """
    if not simulated(state.get("settings")):
        return False
    try:
        require_supported(state)
        return _proof_retained(state, revision, list(case_ids))
    except (OSError, ValueError, KeyError, TypeError):
        return False


def finish_decision(
    state: RunState, current, record, decision, workspace, *, native, completion_ready, regression_required
):
    """Prepare the same archival terminal result without promoting current native proof."""
    if native is not None:
        raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Native completion requires fresh executed proof")
    require_supported(state)
    logical = completion_ready(state, decision, current)
    finish(
        state,
        record,
        decision,
        workspace,
        logical_ready=archival_ready(
            state,
            current,
            logical_ready=logical,
            required_case_ids=[case["id"] for case in test_cases.proof_cases(state, all_due=True)]
            if regression_required(state)
            else None,
        ),
    )


def finish(state: RunState, record, decision, workspace, *, logical_ready):
    """Interception before ordinary completion; no TASK_COMPLETE/delivery promotion."""
    tape = transport.selected(state["settings"])
    index = (record.get("replay") or {}).get("ordinal")
    if type(index) is not int or index != len(tape.document["dispatches"]) - 1:
        raise recording.RecordingError("Replay diverged: recorded completion has remaining dispatches")
    recording.verify_source(tape, tape.document["dispatches"][index]["after"], workspace)
    if (
        decision.get("status") not in ("COMPLETE", "TASK_COMPLETE")
        or tape.document["expected"]["verdict"] != "TASK_COMPLETE"
    ):
        raise recording.RecordingError("Replay diverged: recorded completion decision differs")
    ledger = state.get("replay_result") or {}
    expected_checks = [row["id"] for row in tape.document["checks"]]
    used = ledger.get("consumed_checks")
    if (
        logical_ready is not True
        or not expected_checks
        or ledger.get("manifest_sha256") != tape.sha256
        or not isinstance(used, list)
        or used != expected_checks
    ):
        raise recording.RecordingError("Replay diverged: logical completion or archival obligations are incomplete")
    state.update(
        {
            "status": "REPLAY_COMPLETE",
            "next_stage": None,
            "replay_result": {**archival_result(tape), "consumed_checks": used},
        }
    )
    return True
