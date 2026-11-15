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
    from . import autocode_pause_authority as pause_authority
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
    import autocode_pause_authority as pause_authority
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


def guard(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Refuse ordinary advancement before recovery bookkeeping or dispatch can run."""
    current = disposition.decision(state)
    if current is None:
        return None
    row = current["record"]
    decided.operational_incident = disposition.incident_identity(state, row)
    evidence = (row.get("receipt") or {}).get("evidence") or {}
    cause, stage = evidence.get("status"), evidence.get("stage")
    interruptions = human.pending_interruptions(run_dir)
    if getattr(args, "stage_model", None) and not getattr(args, "resume_paused", False):
        return None  # This existing metadata handler returns without task admission.
    selection = None
    binding = (row.get("receipt") or {}).get("evaluation_binding") or {}
    same_context = _same_context(state, binding)
    if same_context and (observed := disposition.checked_recovery(state, row)):
        decided.operational_selection = observed["selection"]
        decided.operational_recovery_checked = {"incident": decided.operational_incident, **observed}
        return None
    if decided.operational_incident and disposition.same_run(state, binding):
        if proof := disposition.planning_input(state, row):
            decided.operational_selection = "planning_input"
            decided.operational_recovery_checked = {
                "incident": decided.operational_incident,
                "selection": "planning_input",
                "proof": proof,
            }
            return None
        if (
            cause in pause_authority.FEEDBACK_ACKNOWLEDGES
            and same_context
            and (
                getattr(args, "feedback", None) is not None
                or getattr(args, "edit_goal", None)
                or getattr(args, "goal_file", None)
            )
        ):
            selection = "planning_input"
    if decided.operational_incident and same_context and stage == state.get("next_stage"):
        active = state.get("active_stage") or {}
        bound_active = (row["receipt"].get("evaluation_binding") or {}).get("active_stage")
        same_active = bool(active) and bound_active == util.digest(active)
        repair = state.get("pending_report_repair") or {}
        bound_repair = (row["receipt"].get("evaluation_binding") or {}).get("pending_report_repair")
        same_repair = bool(repair) and bound_repair == util.digest(repair)
        if getattr(args, "abandon_stage", None) and same_active and args.abandon_stage == records.attempt_id(active):
            selection = "abandon"
        elif getattr(args, "resume_paused", False):
            if (
                cause == "PAUSED_REPEATED_FAILURE"
                and getattr(args, "retry_failed_stage", False)
                and binding.get("failure_history") == util.digest(state.get("failure_history", {}))
                and _same_repair(state, binding)
                and (not active or same_active)
            ):
                selection = "failure_retry"
            elif (
                cause == "PAUSED_TIMEOUT_RECOVERY"
                and getattr(args, "grant_recovery", None) is not None
                and all(
                    (binding.get("accounting") or {}).get(key) == state.get(key)
                    for key in (
                        "automatic_timeout_recoveries",
                        "consecutive_timeout_recoveries",
                        "automatic_recoveries_since_resume",
                    )
                )
            ):
                selection = "grant"
            elif (
                cause in ("PAUSED_REPEATED_FAILURE", "PAUSED_REPORT_REPAIR_LIMIT")
                and getattr(args, "retry_report", None)
                and (
                    same_active
                    and args.retry_report == records.attempt_id(active)
                    or same_repair
                    and any(
                        args.retry_report == records.attempt_id(record)
                        and (record.get("original_stage") or record.get("stage")) == stage
                        for record in [repair.get("original") or {}, *(state.get("stages") or [])]
                        if isinstance(record, dict)
                    )
                )
            ):
                selection = "report"
            elif cause == "PAUSED_BUILDER_RETRY_LIMIT" and getattr(args, "retry_builder", None):
                selection = "builder"
            elif pause_authority.changes_held_bound(
                getattr(args, "_explicit_budget_flags", None) or set(),
                {"pause_status": cause, "budget": evidence.get("budget")},
            ):
                selection = "bound"
    if selection is None:
        if same_context and cause == "PAUSED_PLANNING_BUDGET" and human.recovery_request(state):
            _normalize(runner, state, state_path, run_dir, row, decided.operational_incident)
            decided.operational_selection = "planning_reserve"
            if not resolver_runtime.reconsider_operational_request(runner, state, run_dir, workspace):
                return refuse()
            proof = resolver_runtime.validate_operational_grant(state, stage, workspace)
            checked(runner, state, run_dir, decided, "planning_reserve", proof)
            return None if authorized(state, decided) else refuse()
        if interruptions.get("pending"):
            _normalize(runner, state, state_path, run_dir, row, decided.operational_incident)
            runner.consume_interventions(state, run_dir, workspace, released=False)
            return refuse()
        try:
            if runner.milestones.apply_queued_activation(state, run_dir):
                print("Requested milestone settings saved; no provider launched.")
        except ValueError as error:
            print(f"Requested milestone settings remain queued: {error}", file=sys.stderr)
        return refuse()
    _normalize(runner, state, state_path, run_dir, row, decided.operational_incident)
    decided.operational_selection = selection
    # Selection permits trying the existing exact handler, not another stage.
    # A stale publication does not change this original incident's cause.
    if state.get("status") in ("WAITING_FOR_USER", "RESOLVER_PENDING"):
        run_status.pause(state, cast(str, cause), state.get("stop_reason"))
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
    if selection == "failure_retry" and (
        not isinstance(proof, dict)
        or (proof.get("stage") or (proof.get("identity") or {}).get("stage")) != evidence.get("stage")
    ):
        return
    if getattr(decided, "operational_selection", None) == selection and proof is not None:
        facts = disposition.recovery_facts(state, row, selection, proof)
        if facts is None:
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
                "result": None if selection == "bound" else copy.deepcopy(proof),
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


def finalize(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Final guards shared with a checked fast dispatch, without generic resume resets."""
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
        if observed and observed["selection"] == "planning_reserve":
            # A saved diagnostic never replaces the existing file/source and
            # allowance validator at the final provider admission frontier.
            resolver_runtime.validate_operational_grant(state, state.get("next_stage"), workspace)
        elif observed and observed["selection"] == "abandon":
            witness = disposition.diagnostic(state, observed["receipt_id"])
            _validate_abandoned_archive(witness["receipt"]["evidence"]["result"])
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
    if conversation_ingress.record_build_start(
        state, getattr(args, "expected_goal_token", None), token_for=goals.token, is_approved=goals.approved
    ):
        runner.write_json(state_path, state)
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
