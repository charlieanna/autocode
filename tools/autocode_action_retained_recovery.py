"""Existing retained-checkpoint owners behind the shared observation admission.

An old publication may have an obsolete runtime token. Its domain owner checks
retained evidence, never treats that token as permission, and either commits a
metadata-only outcome or supplies a proof rechecked before any work dispatch.
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_classification_recovery as classification
    from . import autocode_contract_identity as contracts
    from . import autocode_no_progress_recovery as no_progress
    from . import autocode_operational_admission as admission
    from . import autocode_operational_outcome as outcome
    from . import autocode_owned_operations as operations
    from . import autocode_recovery_progress as progress
    from . import autocode_resolver_human as human
    from . import autocode_source_scope as source_scope
    from . import autocode_support as support
    from . import autocode_validation_exhaustion_recovery as validation
except ImportError:
    import autocode_classification_recovery as classification
    import autocode_contract_identity as contracts
    import autocode_no_progress_recovery as no_progress
    import autocode_operational_admission as admission
    import autocode_operational_outcome as outcome
    import autocode_owned_operations as operations
    import autocode_recovery_progress as progress
    import autocode_resolver_human as human
    import autocode_source_scope as source_scope
    import autocode_support as support
    import autocode_validation_exhaustion_recovery as validation


def _wave(runner, state: RunState):
    return runner.dispatch.select(state) if state.get("next_stage") == "orchestrator" else None


def _revision(state: RunState, workspace):
    return source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)["revision"]


def settle(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Return (handled, exit code); no matching evidence is not a successful owner."""
    if any(human.pending_interruptions(run_dir).values()):
        return False, None
    if classification.pending(state) and classification.conclude(state, workspace, at=runner.now()):
        runner.write_json(state_path, state)
        print(state.get("stop_reason") or state.get("next_action"))
        return True, 2  # A recorded outcome launches nothing; the failure and its history stay.
    if validation.pending(state) and validation.settle(state, _revision(state, workspace), at=runner.now()):
        runner.write_json(state_path, state)
        print(state.get("stop_reason") or state.get("next_action"))
        return True, 2  # Settlement grants no dispatch; keep the incident and history.
    if no_progress.pending_legacy(state):
        settled = no_progress.reconcile_legacy(runner, state, run_dir, workspace, wave=_wave(runner, state))
        if settled == "ended":
            print(state.get("stop_reason") or state.get("next_action"))
            return True, 2
        if settled == "continue":
            proof = no_progress.authenticated_intervening_proof(state, workspace, wave=_wave(runner, state))
            decided.evidence_reconciled = True
            decided.operational_selection = "retained_acceptance"
            admission.checked(runner, state, run_dir, decided, "retained_acceptance", proof)
            return True, None
    # Both accounting corrections inspect the complete retained ledger. A runtime
    # upgrade invalidates old answer tokens, not these independently checked facts.
    if progress.reconcile(
        state,
        issued=human.retained(state),
        approved=contracts.approved(state),
        supersede=human.supersede_operational,
        now=runner.now,
    ):
        proof = {"event": copy.deepcopy(state["user_events"][-1]), "source_revision": _revision(state, workspace)}
        runner.write_json(state_path, state)
        decided.evidence_reconciled = True
        decided.operational_selection = "retained_accounting"
        admission.checked(runner, state, run_dir, decided, "retained_accounting", proof)
        return True, None
    return False, None


def validate_acceptance(runner, args, state: RunState, run_dir, workspace, decided, row, observed, *, final):
    proof = outcome.proof(state, observed)
    if not proof or proof != no_progress.authenticated_intervening_proof(state, workspace, wave=_wave(runner, state)):
        return None
    if no_progress.proof_already_spent(state, proof) or no_progress.unsettled_reasons(state, run_dir):
        return None
    return outcome.answer(
        state,
        proof,
        {"intervening_proof": proof},
        fields=("settings", "current_task", "goal_contract", "no_progress_batches", "acceptance_tests"),
    )


def validate_accounting(runner, args, state: RunState, run_dir, workspace, decided, row, observed, *, final):
    proof = outcome.proof(state, observed)
    if not isinstance(proof, dict) or proof.get("source_revision") != _revision(state, workspace):
        return None
    event = proof.get("event")
    if not isinstance(event, dict) or event.get("kind") != "recovery_accounting_correction":
        return None
    candidate = copy.deepcopy(state)
    if "previous_no_progress_batches" in event:
        candidate["no_progress_batches"] = event["previous_no_progress_batches"]
        if state.get("no_progress_batches") != 0 or progress.planning_only_count(candidate) != event.get(
            "planning_attempts"
        ):
            return None
    else:
        previous = event.get("previous_count")
        if type(previous) is not int or previous < 0:
            return None
        candidate["automatic_recoveries_since_resume"] = previous
        credit = progress.accounting.progress_credit(candidate)
        if not credit or any(event.get(key) != value for key, value in credit.items()):
            return None
        if state.get("automatic_recoveries_since_resume") != credit["remaining_count"]:
            return None
    if not contracts.approved(state) or event not in state.get("user_events", []):
        return None
    return outcome.answer(
        state,
        proof,
        {"accounting_correction": event},
        fields=(
            "settings",
            "current_task",
            "goal_contract",
            "no_progress_batches",
            "automatic_recoveries_since_resume",
            "consecutive_timeout_recoveries",
            "automatic_timeout_recoveries",
        ),
        members=(("user_events", event),),
    )


OPERATIONS = operations.register(
    operations.Operation(
        "retained_acceptance", operations.no_work, validate_acceptance, observes=no_progress.pending_legacy
    ),
    operations.Operation("retained_accounting", operations.no_work, validate_accounting, observes=progress.pending),
    operations.Operation("retained_validation", operations.no_work, operations.no_work, observes=validation.pending),
    operations.Operation(
        "retained_classification", operations.no_work, operations.no_work, observes=classification.pending
    ),
)
