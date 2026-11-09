"""Settle retained validation exhaustion without asking about runner machinery."""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_contract_identity as contracts
    from . import autocode_findings as findings
    from . import autocode_resolver_human as human
    from . import autocode_stop as stop
    from . import autocode_validation_rounds as rounds
    from . import autocode_validation_scope_recovery as scope_recovery
except ImportError:
    import autocode_contract_identity as contracts
    import autocode_findings as findings
    import autocode_resolver_human as human
    import autocode_stop as stop
    import autocode_validation_rounds as rounds
    import autocode_validation_scope_recovery as scope_recovery


NOTICE = "AutoCode will settle the retained validation limit against current evidence."


def notice(state: RunState):
    return scope_recovery.NOTICE if scope_recovery.pending(state) else NOTICE


def pending(state: RunState):
    """Pure offer for an authentic, unanswered legacy validation-limit request.

    Recognize its structured origin and finding set, never its question prose.
    The saved rounds must still prove exhaustion; live source is checked again
    under the run lock by ``settle``. No answer or execution authority is issued.
    """
    if issued := scope_recovery.pending(state):
        return issued
    if state.get("status") != "WAITING_FOR_USER" or any(
        state.get(key)
        for key in ("active_stage", "active_runner_check", "pending_report_repair", "uncertain_artifacts")
    ):
        return None
    issued = human.retained(state)
    if not issued or issued["scope"] != "operational_exhaustion" or not contracts.approved(state):
        return None
    proposal = state["resolver"]["human_escalations"][issued["request_id"]]["identity"]["proposal"]
    origin, request = proposal.get("origin") or {}, issued["request"]
    ids = request.get("finding_ids")
    if (
        origin.get("pause_status") != "PAUSED_RESOLVER"
        or origin.get("stage") != "sol"
        or request.get("kind") != "blocker"
        or not isinstance(ids, list)
        or not ids
        or any(not isinstance(ident, str) for ident in ids)
    ):
        return None
    revision = (state.get("validation") or {}).get("source_revision")
    decision = rounds.admit(deepcopy(state), findings.blocking_entries(state), revision)
    if decision and sorted(ids) == decision["evidence"]["finding_ids"]:
        return issued
    return None


def settle(state: RunState, revision, *, at):
    """At ordinary resume, replace only a still-exhausted internal question.

    A repaired runtime may invalidate the old answer token. Retiring that token
    grants nothing: the current approved contract, task, findings and fresh
    source must independently satisfy today's admission limit. History, routes,
    caps and findings remain intact; no provider is launched or retry granted.
    """
    if scope_recovery.pending(state):
        return scope_recovery.settle(state, revision, at=at)
    issued = pending(state)
    if not issued or any(human.pending_interruptions(state.get("run_dir")).values()):
        return False
    decision = rounds.admit(deepcopy(state), findings.blocking_entries(state), revision)
    if not decision:
        return False
    reason = "AutoCode settled the retained validation limit without requesting a human machinery decision"
    if not human.supersede_operational(state, reason):
        return False
    evidence = {**decision["evidence"], "superseded_request_id": issued["request_id"]}
    stop.end(state, decision["reason"], evidence, at=at)
    return True
