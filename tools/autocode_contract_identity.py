"""Cycle-free contract identity and authenticated approval predicates."""

from __future__ import annotations

import copy
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


def approval_identity(state: RunState):
    """The saved contract's identity and approval, the one projection admission and the view compare."""
    contract = state.get("goal_contract")
    if not contract:
        return None
    expected = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
    if contract.get("hash") != expected:
        raise ValueError("Cannot authorize a question against an unsealed contract")
    return {
        "task_id": contract["task_id"],
        "revision": contract["revision"],
        "hash": contract["hash"],
        "approval_status": contract.get("approval_status"),
        "approval_event": copy.deepcopy(contract.get("approval_event")),
    }


def sealed(contract):
    """A complete, correctly shaped identity whose digest matches its actual body."""
    if (
        not isinstance(contract, dict)
        or not isinstance(contract.get("task_id"), str)
        or not contract["task_id"]
        or type(contract.get("revision")) is not int
        or not isinstance(contract.get("body"), dict)
    ):
        return False
    fields = ("task_id", "revision", "body")
    return contract.get("hash") == util.digest({key: contract[key] for key in fields})


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
    contract = state.get("goal_contract")
    if not sealed(contract):
        return False
    contract = cast(dict, contract)
    approval = contract.get("approval_event")
    events = state.get("user_events")
    questions = contract["body"].get("open_blocking_questions")
    return bool(
        isinstance(approval, dict)
        and isinstance(events, list)
        and isinstance(questions, list)
        and contract.get("approval_status") == "approved"
        and approval.get("token") == token(contract)
        and workflows.approval_actor_ok(contract.get("origin"), approval)
        and approval in events
        and _retained_approval_valid(state, contract, approval)
        and not questions
    )


def historical_approval(state: RunState, contract):
    """Authenticate earlier authority without restoring revoked execution approval."""
    ledger = state.get("user_events")
    if not sealed(contract) or not isinstance(ledger, list):
        return None
    events = [
        event
        for event in ledger
        if isinstance(event, dict) and event.get("kind") == "goal_approval" and event.get("token") == token(contract)
    ]
    if len(events) != 1 or contract.get("approval_event") not in (None, events[0]):
        return None
    prior = {**contract, "approval_status": "approved", "approval_event": events[0]}
    return events[0] if approved({**state, "goal_contract": prior}) else None
