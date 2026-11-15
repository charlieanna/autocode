"""A recorded internal recovery failure, without a person-facing machinery question."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_quota_route as quota_route
    from . import autocode_resolver_human as resolver_human
    from . import autocode_util as util
except ImportError:
    import autocode_quota_route as quota_route
    import autocode_resolver_human as resolver_human
    import autocode_util as util


def _valid_receipt(state: RunState, identity, cause):
    resolver = state.get("resolver") or {}
    record = (resolver.get("operational_receipts") or {}).get(identity) or {}
    receipt = record.get("receipt") or {}
    evidence = receipt.get("evidence") or {}
    if (
        not isinstance(identity, str)
        or identity not in (resolver.get("operational_diagnostics") or [])
        or record.get("stage") != "resolver"
        or not record.get("runner_owned")
        or (record.get("decision") or {}).get("action") != "hold"
        or receipt.get("id") != identity
        or evidence.get("status") != cause
        or (evidence.get("budget") or {}).get("category") != "operational_recovery"
        or util.digest(
            {
                "scope": "operational_diagnostic",
                "action": "hold",
                "detail": record.get("summary"),
                "evidence": evidence,
                "binding": receipt.get("evaluation_binding"),
            }
        )
        != identity
    ):
        return False
    try:
        if util.read(Path(record["output"])) != record:
            return False
    except (KeyError, OSError, ValueError):
        return False
    return True


def presentation(state: RunState):
    """Do not show an authenticated machinery request as a person question."""
    if state.get("status") != "WAITING_FOR_USER":
        return None
    retained = resolver_human.retained(state)
    if not retained or retained.get("scope") != "operational_exhaustion":
        return None
    entry = ((state.get("resolver") or {}).get("human_escalations") or {}).get(retained.get("request_id")) or {}
    proposal = (entry.get("identity") or {}).get("proposal") or {}
    origin = proposal.get("origin") or {}
    cause = origin.get("pause_status")
    receipt_id = (proposal.get("evidence") or {}).get("resolver_receipt_id")
    if (
        (origin.get("budget") or {}).get("category") != "operational_recovery"
        or cause in (*quota_route.STATUSES, "PAUSED_PROVIDER_UNCERTAIN")
        or origin.get("quota_worker")
        or proposal.get("questions")
        or not _valid_receipt(state, receipt_id, cause)
    ):
        return None
    return {
        "title": "AutoCode could not continue this task",
        "category": "blocked",
        "what_happened": "AutoCode could not automatically recover this step. Your work and the failure evidence are saved.",
    }
