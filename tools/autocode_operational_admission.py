"""Writer admission for an exact exhausted-recovery incident, never new allowance."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import copy
import sys
from pathlib import Path

try:
    from . import autocode_conversation_ingress as conversation_ingress
    from . import autocode_goals as goals
    from . import autocode_model_listing_failure as model_listing_failure
    from . import autocode_model_listing_failure_recovery as model_listing_recovery
    from . import autocode_operational_block_view as block_view
    from . import autocode_operational_disposition as disposition
    from . import autocode_owned_operations as operation_contract
    from . import autocode_recovery_accounting as recovery_accounting
    from . import autocode_resolver_human as human
    from . import autocode_resolver_runtime as resolver_runtime
    from . import autocode_role_transport as role_transport
    from . import autocode_run_records as records
    from . import autocode_run_status as run_status
    from . import autocode_util as util
except ImportError:
    import autocode_conversation_ingress as conversation_ingress
    import autocode_goals as goals
    import autocode_model_listing_failure as model_listing_failure
    import autocode_model_listing_failure_recovery as model_listing_recovery
    import autocode_operational_block_view as block_view
    import autocode_operational_disposition as disposition
    import autocode_owned_operations as operation_contract
    import autocode_recovery_accounting as recovery_accounting
    import autocode_resolver_human as human
    import autocode_resolver_runtime as resolver_runtime
    import autocode_role_transport as role_transport
    import autocode_run_records as records
    import autocode_run_status as run_status
    import autocode_util as util


def _same_context(state: RunState, binding):
    return all(
        binding.get(key) == value
        for key, value in (
            ("task_id", state.get("task_id")),
            ("task", state.get("task")),
            ("workspace", state.get("workspace")),
            ("run_dir", state.get("run_dir")),
            ("current_task", util.digest(state.get("current_task"))),
            ("contract", human._contract(state)),
        )
    )


def _same_repair(state: RunState, binding):
    current = state.get("pending_report_repair")
    expected = binding.get("pending_report_repair")
    return (
        expected == util.digest(current)
        or current is None
        and any(
            isinstance(row, dict) and expected == util.digest(row.get("repair"))
            for row in state.get("report_repair_archive") or []
        )
    )


def _normalize(runner, state: RunState, state_path, run_dir, row, identity):
    if (row.get("decision") or {}).get("action") != "hold":
        return
    # Save the witness before any metadata/input handler can withdraw the sole
    # queued proposal. An uncertain save never reaches that handler.
    candidate = copy.deepcopy(state)
    runner.resolver_runtime._operational_receipt(
        candidate,
        run_dir,
        "blocked",
        disposition.MESSAGE,
        {**copy.deepcopy(row["receipt"].get("evidence") or {}), "original_receipt_id": identity},
    )
    runner.write_json(state_path, candidate)
    cast(dict, state).clear()
    state.update(candidate)


def _fresh_try(state: RunState, identity, previous_settings):
    """A status-gated handler inspects only this exact authenticated request.

    Locked CLI setup may already have selected new settings. Its one saved
    prior setting is the existing recovery contract; every other frontier fact
    must still be fresh. No normalization or cause projection precedes this.
    """
    versions = [state]
    if previous_settings is not None:
        versions.append({**state, "settings": previous_settings})
    for checked_state in versions:
        issued = human.current(checked_state) or human.recovery_request(checked_state)
        if issued:
            proposal = checked_state["resolver"]["human_escalations"][issued["request_id"]]["identity"]["proposal"]
        else:
            proposal = human.queued_operational(checked_state)
        if (
            isinstance(proposal, dict)
            and (proposal.get("evidence") or {}).get("resolver_receipt_id") == identity
            and disposition.saved_disposition(checked_state, proposal) == "machinery"
        ):
            return True
    return False


def _frontier_matches(state: RunState, binding):
    """The retained attempt/repair and spent recovery ledger did not change."""
    return (
        binding.get("active_stage") == util.digest(state.get("active_stage"))
        and binding.get("failure_history") == util.digest(state.get("failure_history", {}))
        and _same_repair(state, binding)
        and all(
            value == state.get(key)
            for key, value in (binding.get("accounting") or {}).items()
            if key
            in (
                "automatic_timeout_recoveries",
                "consecutive_timeout_recoveries",
                "automatic_recoveries_since_resume",
            )
        )
    )


def original_cause(state: RunState, decided):
    """The authenticated saved incident, without changing the handler's status."""
    row = disposition.diagnostic(state, decided.operational_incident)
    return ((row or {}).get("receipt", {}).get("evidence") or {}).get("status")


def guard(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Select only a registered owner; ordinary advancement has no authority."""
    current = disposition.decision(state)
    if current is None:
        return None
    row = current["record"]
    decided.operational_incident = disposition.incident_identity(state, row)
    evidence = (row.get("receipt") or {}).get("evidence") or {}
    binding = (row.get("receipt") or {}).get("evaluation_binding") or {}
    same_context = _same_context(state, binding)
    if getattr(args, "stage_model", None) and not getattr(args, "resume_paused", False):
        return None  # The existing earlier metadata handler returns without admission.
    if decided.owned_operations is None:
        decided.owned_operations = runner.run_actions.owned_operations(args, state)
    continuation = not decided.owned_operations or all(
        operation.name == "observe" for operation in decided.owned_operations
    )
    if continuation and same_context and (observed := disposition.checked_recovery(state, row)):
        decided.operational_selection = observed["selection"]
        decided.operational_recovery_checked = {"incident": decided.operational_incident, **observed}
        decided.owned_operations = ()  # A saved owner commit is revalidated, never replayed.
        decided.owner_lane = True
        return None
    planning_input = (
        disposition.planning_input(state, row)
        if decided.operational_incident and disposition.same_run(state, binding)
        else None
    )
    operations = decided.owned_operations
    if planning_input and continuation:
        decided.operational_selection = "planning_input"
        decided.operational_recovery_checked = {
            "incident": decided.operational_incident,
            "selection": "planning_input",
            "proof": planning_input,
        }
        # This input already committed through its owner. Let ordinary pause,
        # source and approval guards run; dispatch still requires RUNNING and
        # revalidates both input ledgers at the final admission boundary.
        decided.owned_operations = ()
        decided.owner_lane = False
        return None
    if not operations:
        if human.pending_interruptions(run_dir).get("pending"):
            _normalize(runner, state, state_path, run_dir, row, decided.operational_incident)
            runner.consume_interventions(state, run_dir, workspace, released=False)
            return refuse()
        try:
            if runner.milestones.apply_queued_activation(state, run_dir):
                print("Requested milestone settings saved; no provider launched.")
        except ValueError as error:
            print(f"Requested milestone settings remain queued: {error}", file=sys.stderr)
        return refuse()
    if not decided.operational_incident or not (same_context or planning_input):
        return refuse()
    if human.pending_interruptions(run_dir).get("pending"):
        _normalize(runner, state, state_path, run_dir, row, decided.operational_incident)
        runner.consume_interventions(state, run_dir, workspace, released=False)
        return refuse()
    if any(operation.frontier_bound for operation in operations) and not _frontier_matches(state, binding):
        return refuse()
    inspecting_request = state.get("status") in ("WAITING_FOR_USER", "RESOLVER_PENDING")
    if (
        inspecting_request
        and any(operation.request_bound for operation in operations)
        and not _fresh_try(state, decided.operational_incident, getattr(args, "_recovery_grant_settings", None))
    ):
        return refuse()
    if not any(operation.preserve_request for operation in operations):
        _normalize(runner, state, state_path, run_dir, row, decided.operational_incident)
    # A request-preserving owner validates the exact original publication/queue.
    # Its immutable diagnostic already anchors the hold if that transaction
    # withdraws the proposal; only a committed return can add checked evidence.
    decided.operational_selection = operations[0].name
    decided.owner_lane = True
    first = operations[0]
    if inspecting_request and first.request_bound and not first.preserve_request:
        run_status.pause(state, cast(str, evidence.get("status")), state.get("stop_reason"))
    return None


def refuse():
    print(disposition.MESSAGE, file=sys.stderr)
    return 2


def checked(runner, state: RunState, run_dir, decided, selection, proof):
    """Called only after the named existing handler validated this invocation."""
    row = next(
        (
            row
            for row in disposition.incidents(state)
            if disposition.incident_identity(state, row) == getattr(decided, "operational_incident", None)
        ),
        None,
    )
    if row is None:
        return
    evidence = (row.get("receipt") or {}).get("evidence") or {}
    if getattr(decided, "operational_selection", None) == selection and proof is not None:
        operation = operation_contract.resolve(selection)
        if operation is None or not callable(operation.validate):
            return
        outcome = operation.validate(
            runner,
            None,
            state,
            run_dir,
            Path(state["workspace"]),
            decided,
            row,
            {"selection": selection, "result": proof},
            final=False,
        )
        facts = disposition.recovery_facts(state, row, selection, outcome)
        if facts is None:
            return
        events = disposition.later_events(state, row)
        if events is None or any(
            member["value"] not in events for member in outcome["members"] if member["key"] == "user_events"
        ):
            return
        candidate = copy.deepcopy(state)
        identity = runner.resolver_runtime._operational_receipt(
            candidate,
            run_dir,
            "recovery_checked",
            "The exact existing recovery handler validated this incident; final task admission is still required.",
            {
                "original_receipt_id": decided.operational_incident,
                "status": evidence.get("status"),
                "stage": evidence.get("stage"),
                "budget": evidence.get("budget"),
                "selection": selection,
                "result": copy.deepcopy(outcome),
                "facts": facts,
            },
        )
        runner.write_json(run_dir / "state.json", candidate)
        cast(dict, state).clear()
        state.update(candidate)
        decided.operational_recovery_checked = {
            "incident": decided.operational_incident,
            "selection": selection,
            "receipt_id": identity,
            "facts": facts,
        }


def _validate_abandoned_archive(proof):
    """Use the original stopped-stage and exact retained-byte checks again."""
    record, pins = proof.get("record"), proof.get("files")
    if not isinstance(record, dict) or not isinstance(pins, dict) or not pins:
        raise ValueError(disposition.MESSAGE)
    paths = record.get("archived_paths") or {}
    if (
        not isinstance(paths, dict)
        or set(pins) != set(paths.values())
        or any(not Path(path).is_file() or util.file_hash(path) != digest for path, digest in pins.items())
        or any(record.get(key) not in pins for key in ("events", "before_ref", "after_ref"))
    ):
        raise ValueError(disposition.MESSAGE)
    records.assert_stage_stopped(record)


def abandoned(runner, state: RunState, run_dir, decided, original):
    """Only the actual successful abandon handler calls this after its save."""
    if getattr(decided, "operational_selection", None) != "abandon" or not isinstance(original, dict):
        return
    matches = [
        row
        for row in state.get("stages") or []
        if isinstance(row, dict)
        and all(row.get(key) == original.get(key) for key in ("stage", "role", "iteration"))
        and (row.get("archived_paths") or {}).get(original.get("events")) == row.get("events")
        and (row.get("archived_paths") or {}).get(original.get("before_ref")) == row.get("before_ref")
    ]
    if len(matches) != 1:
        return
    proof = {
        "selected": records.attempt_id(original),
        "original": copy.deepcopy(original),
        "record": copy.deepcopy(matches[0]),
        "files": {path: util.file_hash(path) for path in (matches[0].get("archived_paths") or {}).values()},
    }
    _validate_abandoned_archive(proof)
    checked(runner, state, run_dir, decided, "abandon", proof)


def authorized(state: RunState, decided):
    """Existing handlers may inspect genuine new input; this is not dispatch."""
    if block_view.receipt(state) is not None:
        return False
    for row in disposition.incidents(state):
        identity = disposition.incident_identity(state, row)
        if identity and disposition.planning_input(state, row):
            continue
        if (
            identity
            and _same_context(state, row["receipt"].get("evaluation_binding") or {})
            and disposition.checked_recovery(state, row)
        ):
            continue
        return False  # An invocation marker without the saved matching ledger grants nothing.
    return True


def ready(state: RunState, decided):
    if not authorized(state, decided):
        return False
    if any(disposition.planning_input(state, row) for row in disposition.incidents(state)):
        return state.get("status") == "RUNNING" and not any(
            state.get(key) for key in ("pending_questions", "active_stage", "pending_report_repair")
        )
    return True


def retire(runner, state: RunState, run_dir, decided):
    """Only final admission calls this, after source/route/approval guards pass."""
    rows = disposition.incidents(state)
    if not rows:
        return False
    if not ready(state, decided):
        raise ValueError(disposition.MESSAGE)
    # These IDs are generated by Core in this run, never copied from a model
    # response or a recorded replay. A replay rebuilds its own diagnostic first.
    for row in rows:
        evidence = row["receipt"].get("evidence") or {}
        proof = disposition.planning_input(state, row)
        runner.resolver_runtime._operational_receipt(
            state,
            run_dir,
            "recovered",
            "The exact internal incident passed its existing recovery or owner-input authority and final admission guards.",
            {
                "recovered_receipt_id": disposition.incident_identity(state, row),
                "status": evidence.get("status"),
                "stage": evidence.get("stage"),
                "budget": evidence.get("budget"),
                "recovery": {"selection": "planning_input", "proof": proof}
                if proof
                else disposition.checked_recovery(state, row),
            },
        )
        pending = cast(dict, state.get(runner.resolver_human.PRIVATE) or {})
        if (pending.get("evidence") or {}).get("resolver_receipt_id") == disposition.incident_identity(state, row):
            runner.resolver_human.withdraw_queued_operational(state, evidence.get("stage"))
    return True


def work_guard(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided, *, final=False):
    """Fresh existing work guards, without consuming or retiring any authority."""
    if not ready(state, decided):
        return refuse()
    try:
        conversation_ingress.require_expected_goal(
            state, getattr(args, "expected_goal_token", None), token_for=goals.token, is_approved=goals.approved
        )
    except ValueError as error:
        print(f"Input rejected: {error}", file=sys.stderr)
        return 2
    for row in disposition.incidents(state):
        observed = disposition.checked_recovery(state, row)
        if observed is None:
            proof = disposition.planning_input(state, row)
            observed = {"selection": "planning_input", "proof": proof} if proof else None
        operation = operation_contract.resolve(observed.get("selection")) if observed else None
        if (
            operation is None
            or not callable(operation.validate)
            or operation.validate(runner, args, state, run_dir, workspace, decided, row, observed, final=final)
            != observed.get("result", observed.get("proof"))
        ):
            return refuse()
    if state["settings"].get("engine") == "opencode":
        try:
            role_transport.check_models(
                state["settings"], runner.opencode, workspace, inventory=getattr(args, "_model_inventory", None)
            )
        except model_listing_failure.ModelListingTimeout as error:
            if model_listing_recovery.settle(runner, state, run_dir, workspace, error):
                print(f"{state['status']}: {state['stop_reason']}", file=sys.stderr)
                return 2
            raise
    return None


def finalize(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Final guards shared with a checked fast dispatch, without generic resume resets."""
    if (
        code := work_guard(runner, args, parser, state, state_path, run_dir, workspace, decided, final=True)
    ) is not None:
        return code
    if conversation_ingress.record_build_start(
        state, getattr(args, "expected_goal_token", None), token_for=goals.token, is_approved=goals.approved
    ):
        runner.write_json(state_path, state)
    if decided.resume_initialization:
        recovery_accounting.initialize_milestone_time(state, args.max_milestone_seconds)
        decided.resume_initialization = False
    if ready(state, decided) and getattr(decided, "operational_resume_deferred", False):
        resolver_runtime.reset_for_resume(state)
        runner.reset_report_repair_for_resume(state)
        decided.operational_resume_deferred = False
    if retire(runner, state, run_dir, decided):
        runner.write_json(state_path, state)
        if not ready(state, decided):
            return refuse()
    return None


def dispatch(code, runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Checked fast dispatch retains the final guards, without generic resume resets."""
    if code is not None:
        return code  # A metadata handler's early return retains the incident.
    if not ready(state, decided):
        return refuse()
    if getattr(decided, "operational_incident", None) is not None or disposition.held(state) is not None:
        return finalize(runner, args, parser, state, state_path, run_dir, workspace, decided)
    return None


def apply_owned(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """No ambient step runs until an actual selected owner established authority."""
    selected = () if decided.operational_recovery_checked and ready(state, decided) else decided.owned_operations
    for operation in selected or ():
        if not operation_contract.authentic(operation):
            return refuse()
        decided.operational_selection = operation.name
        code = operation.apply(runner, args, parser, state, state_path, run_dir, workspace, decided)
        if code is not None:
            return code  # Metadata exit: the incident and committed evidence remain.
        decided.completed_operations = (*decided.completed_operations, operation.name)
    if not ready(state, decided):
        return refuse()
    if (code := work_guard(runner, args, parser, state, state_path, run_dir, workspace, decided)) is not None:
        return code
    if state.get("status") in ("WAITING_FOR_USER", "RESOLVER_PENDING") and any(
        operation.request_bound and not operation.preserve_request for operation in selected or ()
    ):
        # The owner's durable save can normalize the still-retained legacy
        # request. Only its now-validated outcome restores the same cause for
        # ordinary status-gated guards; the request is retired only at final admission.
        run_status.pause(state, original_cause(state, decided), state.get("stop_reason"))
    decided.owner_lane = False
    return None


def apply_step(step, runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Keep owner execution at admission, before returning to ordinary steps."""
    code = step(runner, args, parser, state, state_path, run_dir, workspace, decided)
    return (
        apply_owned(runner, args, parser, state, state_path, run_dir, workspace, decided)
        if code is None and decided.owner_lane
        else code
    )


def recorded_frontier(state: RunState, row, observed):
    """One authenticated current-state outcome; owners add their real I/O frontier."""
    return disposition.checked_recovery(state, row) == observed


def planning_frontier(state: RunState, row, observed):
    proof = disposition.planning_input(state, row)
    return proof is not None and observed == {"selection": "planning_input", "proof": proof}
