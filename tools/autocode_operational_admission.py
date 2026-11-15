"""Writer admission for an exact exhausted-recovery incident, never new allowance."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import copy
import sys

try:
    from . import autocode_contract_identity as contract_identity
    from . import autocode_operational_disposition as disposition
    from . import autocode_pause_authority as pause_authority
    from . import autocode_resolver_human as human
    from . import autocode_run_records as records
    from . import autocode_run_status as run_status
    from . import autocode_util as util
except ImportError:
    import autocode_contract_identity as contract_identity
    import autocode_operational_disposition as disposition
    import autocode_pause_authority as pause_authority
    import autocode_resolver_human as human
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


def _same_run(state: RunState, binding):
    return all(binding.get(key) == state.get(key) for key in ("task_id", "task", "workspace", "run_dir"))


def planning_input(state: RunState, row):
    """A committed owner correction to this planning incident, never a flag or answer."""
    detail = row.get("receipt") or {}
    evidence, binding = detail.get("evidence") or {}, detail.get("evaluation_binding") or {}
    if evidence.get("status") not in pause_authority.FEEDBACK_ACKNOWLEDGES or not _same_run(state, binding):
        return None
    contract = binding.get("contract")
    if not isinstance(contract, dict) or not isinstance(contract.get("hash"), str):
        return None
    token = contract_identity.token(contract)
    events = state.get("user_events")
    if not isinstance(events, list):
        return None
    # The diagnostic commits the exact event prefix. Metadata changes and a
    # pre-existing correction cannot become new input for this incident.
    start = next((index for index in range(len(events), -1, -1) if util.digest(events[:index]) == binding.get("user_events")), None)
    if start is None:
        return None
    for event in events[start:]:
        if not (
            isinstance(event, dict)
            and event.get("kind") == "brief_feedback"
            and event.get("actor") in ("user_cli", "user_intervention")
            and isinstance(event.get("id"), str)
            and event.get("id")
            and isinstance(event.get("text"), str)
            and event.get("text").strip()
            and event.get("contract_token") == token
            and event in (state.get("brief_feedback") or [])
        ):
            continue
        if event["actor"] == "user_intervention" and not any(
            isinstance(applied, dict)
            and applied.get("id") == event.get("receipt_id")
            and applied.get("kind") == "feedback"
            and applied.get("applied_at") == event.get("at")
            for applied in state.get("applied_interventions") or []
        ):
            continue
        return {"kind": "brief_feedback", "event_id": event["id"], "contract_token": token}
    current = state.get("goal_contract")
    if (
        contract_identity.sealed(current)
        and current.get("origin") == "user_cli_edit"
        and current.get("task_id") == contract.get("task_id")
        and current.get("revision") == contract.get("revision") + 1
        and any(
            contract_identity.sealed(previous)
            and all(previous.get(key) == value for key, value in contract.items())
            for previous in state.get("contract_history") or []
        )
    ):
        return {"kind": "goal_revision", "contract_token": token, "revision_token": contract_identity.token(current)}
    return None


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
    state.clear()
    state.update(candidate)


def guard(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Refuse ordinary advancement before recovery bookkeeping or dispatch can run."""
    row = disposition.held(state)
    if row is None:
        return None
    decided.operational_incident = disposition.incident_identity(state, row)
    evidence = (row.get("receipt") or {}).get("evidence") or {}
    cause, stage = evidence.get("status"), evidence.get("stage")
    interruptions = human.pending_interruptions(run_dir)
    if interruptions.get("pending"):
        _normalize(runner, state, state_path, run_dir, row, decided.operational_incident)
        runner.consume_interventions(state, run_dir, workspace, released=False)
        return refuse()
    if getattr(args, "stage_model", None) and not getattr(args, "resume_paused", False):
        return None  # This existing metadata handler returns without task admission.
    selection = None
    binding = (row.get("receipt") or {}).get("evaluation_binding") or {}
    same_context = _same_context(state, binding)
    if decided.operational_incident and _same_run(state, binding):
        if proof := planning_input(state, row):
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
            and (getattr(args, "feedback", None) is not None or getattr(args, "edit_goal", None) or getattr(args, "goal_file", None))
        ):
            selection = "planning_input"
    if decided.operational_incident and same_context and stage == state.get("next_stage"):
        active = state.get("active_stage") or {}
        bound_active = (row["receipt"].get("evaluation_binding") or {}).get("active_stage")
        same_active = bool(active) and (bound_active == util.digest(active) or bound_active == active)
        repair = state.get("pending_report_repair") or {}
        bound_repair = (row["receipt"].get("evaluation_binding") or {}).get("pending_report_repair")
        same_repair = bool(repair) and (bound_repair == util.digest(repair) or bound_repair == repair)
        if getattr(args, "abandon_stage", None) and same_active and args.abandon_stage == records.attempt_id(active):
            selection = "abandon"
        elif getattr(args, "resume_paused", False):
            if (
                cause == "PAUSED_REPEATED_FAILURE"
                and getattr(args, "retry_failed_stage", False)
                and binding.get("failure_history") == util.digest(state.get("failure_history", {}))
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
                cause == "PAUSED_REPORT_REPAIR_LIMIT"
                and getattr(args, "retry_report", None)
                and (
                    same_active
                    and args.retry_report == records.attempt_id(active)
                    or same_repair
                    and args.retry_report == records.attempt_id(repair.get("original") or {})
                )
            ):
                selection = "report"
            elif (
                cause == "PAUSED_BUILDER_RETRY_LIMIT"
                and getattr(args, "retry_builder", None)
                and args.retry_builder == (state.get("current_task") or {}).get("milestone_id")
            ):
                selection = "builder"
            elif pause_authority.changes_held_bound(
                getattr(args, "_explicit_budget_flags", None) or set(),
                {"pause_status": cause, "budget": evidence.get("budget")},
            ):
                selection = "bound"
    if selection is None:
        return refuse()
    _normalize(runner, state, state_path, run_dir, row, decided.operational_incident)
    decided.operational_selection = selection
    # Selection permits trying the existing exact handler, not another stage.
    # A stale publication does not change this original incident's cause.
    if state.get("status") in ("WAITING_FOR_USER", "RESOLVER_PENDING"):
        run_status.pause(state, cause, state.get("stop_reason"))
    return None


def refuse():
    print(disposition.MESSAGE, file=sys.stderr)
    return 2


def checked(state: RunState, decided, selection, proof):
    """Called only after the named existing handler validated this invocation."""
    row = disposition.held(state)
    if row is None:
        return
    evidence = (row.get("receipt") or {}).get("evidence") or {}
    if selection == "failure_retry" and (
        not isinstance(proof, dict)
        or (proof.get("stage") or (proof.get("identity") or {}).get("stage")) != evidence.get("stage")
    ):
        return
    if getattr(decided, "operational_selection", None) == selection and proof is not None:
        decided.operational_recovery_checked = {
            "incident": decided.operational_incident,
            "selection": selection,
            "proof": proof,
        }


def ready(state: RunState, decided):
    row = disposition.held(state)
    if row is None:
        return True
    identity = disposition.incident_identity(state, row)
    if identity and planning_input(state, row):
        return True
    admitted = getattr(decided, "operational_recovery_checked", None) or {}
    binding = (row.get("receipt") or {}).get("evaluation_binding") or {}
    if admitted.get("selection") == "failure_retry" and binding.get("failure_history") != util.digest(
        state.get("failure_history", {})
    ):
        return False
    return bool(
        identity
        and _same_context(state, (row.get("receipt") or {}).get("evaluation_binding") or {})
        and admitted.get("incident") == identity
        and admitted.get("selection") == getattr(decided, "operational_selection", None)
    )


def retire(runner, state: RunState, run_dir, decided):
    """Only final admission calls this, after source/route/approval guards pass."""
    row = disposition.held(state)
    if row is None:
        return False
    if not ready(state, decided):
        raise ValueError(disposition.MESSAGE)
    evidence = row["receipt"].get("evidence") or {}
    # These IDs are generated by Core in this run, never copied from a model
    # response or a recorded replay. A replay rebuilds its own diagnostic first.
    runner.resolver_runtime._operational_receipt(
        state,
        run_dir,
        "recovered",
        "The exact internal incident passed its existing explicit recovery and final admission guards.",
        {
            "recovered_receipt_id": disposition.incident_identity(state, row),
            "status": evidence.get("status"),
            "stage": evidence.get("stage"),
            "budget": evidence.get("budget"),
            "recovery": getattr(decided, "operational_recovery_checked", None),
        },
    )

    pending = state.get(runner.resolver_human.PRIVATE) or {}
    if (pending.get("evidence") or {}).get("resolver_receipt_id") == disposition.incident_identity(state, row):
        runner.resolver_human.withdraw_queued_operational(state, evidence.get("stage"))
    return True
