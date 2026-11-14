"""Cycle-free contract identity and authenticated approval predicates."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_util as util
    from . import autocode_workflows as workflows
except ImportError:
    import autocode_util as util
    import autocode_workflows as workflows


def token(contract):
    return f"r{contract['revision']}:{contract['hash']}"


def sealed(contract):
    return contract.get("hash") == util.digest({k: contract[k] for k in ("task_id", "revision", "body")})


_REVIEW_RECEIPT_KEYS = frozenset({"stage", "events", "events_sha256", "output", "output_sha256"})
_REVIEW_WRAPPERS = ("source_trace", "brief_acceptance", "risk_acceptance")


def _normalized_review_body(body: dict) -> dict | None:
    if not isinstance(body, dict):
        return None
    normalized = dict(body)
    for key in _REVIEW_WRAPPERS:
        if key not in normalized or normalized[key] is None:
            continue
        wrapper = normalized[key]
        if not isinstance(wrapper, dict):
            return None
        review = wrapper.get("review")
        if not isinstance(review, dict):
            return None
        substantive_review = {rk: rv for rk, rv in review.items() if rk not in _REVIEW_RECEIPT_KEYS}
        normalized[key] = {**wrapper, "review": substantive_review}
    return normalized


def equivalent_reviewed_body(left: dict, right: dict) -> bool:
    """True when two reviewed contract bodies have identical terms and review judgments, differing at most in Plan Reviewer execution file receipts."""
    norm_left = _normalized_review_body(left)
    norm_right = _normalized_review_body(right)
    return norm_left is not None and norm_left == norm_right


def _retained_approval_valid(state: RunState, contract: dict, approval: dict) -> bool:
    if approval.get("actor") != "workflow_policy" or approval.get("source") != workflows.RETAINED_APPROVAL_SOURCE:
        return True
    retained_from = approval.get("retained_from_token")
    if not isinstance(retained_from, str) or not retained_from or retained_from == token(contract):
        return False
    events = state.get("user_events") or []
    prior_events = [
        event
        for event in events
        if isinstance(event, dict)
        and event.get("kind") == "goal_approval"
        and event.get("actor") == "user_cli"
        and event.get("token") == retained_from
    ]
    if len(prior_events) != 1 or events.index(prior_events[0]) >= events.index(approval):
        return False
    if "contract_history" not in state:
        return True
    history = state.get("contract_history") or []
    matches = [
        row
        for row in history
        if isinstance(row, dict)
        and type(row.get("revision")) is int
        and isinstance(row.get("hash"), str)
        and token(row) == retained_from
    ]
    if len(matches) != 1:
        return False
    prior = matches[0]
    return bool(
        prior.get("task_id") == contract.get("task_id")
        and type(contract.get("revision")) is int
        and prior["revision"] < contract["revision"]
        and historical_approval(state, prior) == prior_events[0]
        and equivalent_reviewed_body(contract.get("body") or {}, prior.get("body") or {})
    )


def approved(state: RunState):
    contract = cast(dict, state.get("goal_contract", {}))
    approval = contract.get("approval_event") or {}
    return bool(
        contract
        and sealed(contract)
        and contract.get("approval_status") == "approved"
        and approval.get("token") == token(contract)
        and workflows.approval_actor_ok(contract.get("origin"), approval)
        and approval in state.get("user_events", [])
        and _retained_approval_valid(state, contract, approval)
        and not contract["body"]["open_blocking_questions"]
    )


def historical_approval(state: RunState, contract):
    """Authenticate earlier authority without restoring revoked execution approval."""
    events = [
        event
        for event in state["user_events"]
        if event.get("kind") == "goal_approval" and event.get("token") == token(contract)
    ]
    if len(events) != 1 or contract.get("approval_event") not in (None, events[0]):
        return None
    prior = {**contract, "approval_status": "approved", "approval_event": events[0]}
    return events[0] if approved({**state, "goal_contract": prior}) else None
