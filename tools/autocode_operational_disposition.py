"""Pure saved-fact policy for exhausted internal recovery; never retry authority."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_pause_authority as pause_authority
    from . import autocode_quota_route as quota_route
    from . import autocode_stop as stop
    from . import autocode_util as util
    from .autocode_goals import RESOLVER_PROPOSAL_KEY
except ImportError:
    import autocode_pause_authority as pause_authority
    import autocode_quota_route as quota_route
    import autocode_stop as stop
    import autocode_util as util
    from autocode_goals import RESOLVER_PROPOSAL_KEY

SCOPE = "operational_exhaustion"
MESSAGE = "AutoCode could not safely continue its internal recovery. Saved work, attempts and evidence are kept."
MANAGED = frozenset({"runner_default", "resolver_delegated"})


def budget(state: RunState, status):
    """The same saved bound/origin description for new and retained decisions."""
    kind = (
        "planning_review_call_limit"
        if status == "PAUSED_PLANNING_BUDGET"
        else pause_authority.PAUSE_BUDGET_KIND.get(status)
    )
    settings = state.get("settings") or {}
    origin = (settings.get("budget_origins") or {}).get(kind, "unknown")
    limit = (settings.get("limits") or {}).get(kind)
    if kind == "planning_review_call_limit":
        planning = state.get("planning") or {}
        origin = planning.get(
            "review_call_limit_origin", "runner_default" if "review_call_limit" not in planning else "unknown"
        )
        limit = planning.get("review_call_limit", 2)
    elif kind == "milestone_max_seconds":
        limit = (settings.get("milestone_checkpoints") or {}).get("max_seconds")
    category = (
        (
            "no_progress"
            if status == "PAUSED_NO_PROGRESS"
            else "internal_default"
            if origin in MANAGED
            else "explicit_user_cap"
            if origin == "user_explicit"
            else "protected_saved_limit"
        )
        if kind
        else "provider_or_spending_guard"
        if status in ("PAUSED_BUDGET", "PAUSED_RATE_LIMIT")
        else "provider_content_filter"
        if status == quota_route.REFUSAL_STATUS
        else "operational_recovery"
    )
    return {"category": category, "kind": kind, "origin": origin, "limit": limit}


def classify(proposal):
    """One disposition for structured operational facts, without interpreting prose."""
    if not isinstance(proposal, dict) or proposal.get("scope") != SCOPE:
        return None
    origin = proposal.get("origin") or {}
    status = origin.get("pause_status")
    if status in ("PAUSED_COST_LIMIT", "PAUSED_PLANNING_CALL_LIMIT"):
        return "limit"
    bound = origin.get("budget") or {}
    kind = (
        "planning_review_call_limit"
        if status == "PAUSED_PLANNING_BUDGET"
        else pause_authority.PAUSE_BUDGET_KIND.get(status)
    )
    if kind is None and isinstance(bound, dict):
        kind = bound.get("kind")
    if kind and (not isinstance(bound, dict) or bound.get("kind") != kind or bound.get("origin") not in MANAGED):
        return "limit"
    if any(
        quota_route.asked_route(proposal.get("questions"), question.get("id"))
        for question in proposal.get("questions") or []
        if isinstance(question, dict)
    ):
        return "model"
    if (proposal.get("evidence") or {}).get("recovery_available"):
        return "recover"
    return "machinery"


def diagnostic(state: RunState, identity):
    """Authenticate the runner's original diagnostic, without current source I/O."""
    if not isinstance(identity, str) or not identity:
        return None
    resolver = state.get("resolver") or {}
    records = resolver.get("operational_receipts") or {}
    row = records.get(identity)
    if not isinstance(row, dict) or identity not in (resolver.get("operational_diagnostics") or []):
        return None
    detail = row.get("receipt") or {}
    decision = row.get("decision") or {}
    if not isinstance(detail, dict) or not isinstance(decision, dict):
        return None
    if not (
        row.get("runner_owned") is True
        and row.get("engine") == "runner"
        and row.get("stage") == "resolver"
        and detail.get("scope") == "operational_diagnostic"
        and detail.get("id") == identity
        and row.get("summary") == decision.get("rationale")
        and util.digest(
            {
                "scope": detail["scope"],
                "action": decision.get("action"),
                "detail": row.get("summary"),
                "evidence": detail.get("evidence"),
                "binding": detail.get("evaluation_binding"),
            }
        )
        == identity
    ):
        return None
    return row


def incident_identity(state: RunState, row):
    """A normalized witness names the exact original diagnostic, never a new incident."""
    detail = row.get("receipt") or {}
    evidence = detail.get("evidence") or {}
    original = evidence.get("original_receipt_id")
    if original is None:
        return detail.get("id")
    source = diagnostic(state, original)
    if source is None or (source.get("decision") or {}).get("action") != "hold":
        return None
    facts = source["receipt"].get("evidence") or {}
    source_binding = source["receipt"].get("evaluation_binding") or {}
    binding = detail.get("evaluation_binding") or {}
    if any(binding.get(key) != source_binding.get(key) for key in ("task_id", "workspace", "run_dir", "current_task")):
        return None
    if any(evidence.get(key) != facts.get(key) for key in ("status", "stage", "budget")):
        return None
    return original


def saved_disposition(state: RunState, proposal):
    """Interpret a request only against its exact runner-held diagnostic facts."""
    if not isinstance(proposal, dict) or proposal.get("scope") != SCOPE:
        return None
    row = diagnostic(state, (proposal.get("evidence") or {}).get("resolver_receipt_id"))
    if row is None:
        return "invalid"
    evidence = row["receipt"].get("evidence") or {}
    origin = proposal.get("origin") or {}
    if any(
        origin.get(key) != evidence.get(field)
        for key, field in (("pause_status", "status"), ("stage", "stage"), ("budget", "budget"))
    ):
        return "invalid"
    return classify(proposal)


def refused(state: RunState, proposal):
    return saved_disposition(state, proposal) in ("machinery", "invalid")


def held(state: RunState):
    """The exact unresolved internal incident, independent of later status/settings."""
    resolver = state.get("resolver") or {}
    retired = set()
    for identity in reversed(resolver.get("operational_diagnostics") or []):
        row = diagnostic(state, identity)
        if row is None:
            # Missing or altered runner evidence cannot silently discharge an
            # incident. This is denial only; no identity can authorize recovery.
            return {"receipt": {"id": None}, "summary": MESSAGE}
        detail = row["receipt"]
        evidence = detail.get("evidence") or {}
        action = row["decision"].get("action")
        if action == "recovered":
            original = diagnostic(state, evidence.get("recovered_receipt_id"))
            facts = (original.get("receipt") or {}).get("evidence") or {} if original else {}
            if original and all(evidence.get(key) == facts.get(key) for key in ("status", "stage", "budget")):
                retired.add(evidence["recovered_receipt_id"])
        elif action == "blocked" and incident_identity(state, row) not in retired:
            return row
    # Authenticate legacy publications (including consumed replies) before reading facts.
    for key, entry in reversed(list((resolver.get("human_escalations") or {}).items())):
        identity = entry.get("identity") or {}
        proposal = identity.get("proposal") or {}
        if identity.get("issuer") != "resolver" or util.digest(identity) != key or entry.get("receipt_hash") != key:
            continue
        if not refused(state, proposal):
            continue
        source = (proposal.get("evidence") or {}).get("resolver_receipt_id")
        row = diagnostic(state, source)
        if source not in retired:
            return row or {"receipt": {"id": None}, "summary": MESSAGE}
    proposal = state.get(RESOLVER_PROPOSAL_KEY)
    if refused(state, proposal):
        source = (proposal.get("evidence") or {}).get("resolver_receipt_id")
        row = diagnostic(state, source)
        if source not in retired:
            return row or {"receipt": {"id": None}, "summary": MESSAGE}
    return None


def presentation(state: RunState):
    if stop.applied_stop(state) or held(state) is None:
        return None
    return {
        "category": "blocked",
        "title": "AutoCode is blocked on internal recovery",
        "what_happened": MESSAGE,
        "what_it_means": "The task is not complete. AutoCode retained the failed attempts and will not ask you to decide its machinery.",
    }
