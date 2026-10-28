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
