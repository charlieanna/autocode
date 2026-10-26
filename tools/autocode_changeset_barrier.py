"""Core participant of the public change-set approval protocol.

The coordinator uses TaskRun and status only. Each core publishes its own exact
approval at its checkpoint boundary; execution reads the shared release receipt,
never another run's state. Membership and plan changes withdraw that receipt.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_changeset_store as store
    from . import autocode_contract_identity as identity
    from . import autocode_run_status as run_status
    from . import autocode_util as util
except ImportError:
    import autocode_changeset_store as store
    import autocode_contract_identity as identity
    import autocode_run_status as run_status
    import autocode_util as util


STATUS = "PAUSED_CHANGESET_APPROVAL"


def observation(state: RunState) -> dict:
    contract = state.get("goal_contract") or {}
    try:
        token = identity.token(contract) if identity.sealed(contract) else None
        approved = identity.approved(state)
    except (KeyError, TypeError, ValueError):
        token, approved = None, False
    return {
        "plan_token": token,
        "approved": approved,
        "task_digest": util.digest({"task": state.get("task", ""), "turn": len(state.get("turns") or [])}),
        "grant": state.get("changeset_approval"),
    }


def refusal(state: RunState) -> str | None:
    run_dir = state.get("parent_run") or state.get("run_dir")
    if not run_dir:
        return None
    own = observation(state)
    grant = own.get("grant") or {}
    try:
        record = store.find(run_dir)
    except (store.ChangesetError, OSError) as error:
        return f"Change-set approval cannot be checked: {error}"
    if record is None:
        return (
            "The approved change-set record is unavailable; restore it before continuing"
            if grant.get("set_token")
            else None
        )
    if (
        grant.get("standalone")
        and grant.get("plan_token") == own["plan_token"]
        and grant.get("task_digest") == own["task_digest"]
    ):
        return None
    approval = record.get("approval") or {}
    if (
        approval.get("released")
        and approval.get("token") == grant.get("set_token")
        and approval.get("plans", {}).get(str(Path(run_dir).resolve())) == own["plan_token"]
        and grant.get("plan_token") == own["plan_token"]
        and grant.get("task_digest") == own["task_digest"]
    ):
        return None
    return (
        "Change-set execution is held until every current member plan is approved together. "
        "Read `autocode changeset status --run-dir " + str(run_dir) + "`; retry its set approval after "
        "reviewing all plans, or explicitly approve this run alone to release only it."
    )


def held_approval(state: RunState, *, queued_from: str | None = None) -> bool:
    """Recognize an eligible hold, including its staged Resolver request."""
    status = queued_from if state.get("status") == "RESOLVER_PENDING" else state.get("status")
    return bool(
        observation(state)["approved"]
        and not state.get("parent_run")
        and status in ("RUNNING", STATUS, "AWAITING_GOAL_APPROVAL")
        and state.get("phase") not in ("PLANNING", "DISCOVERING")
        and not any(
            state.get(key)
            for key in (
                "active_stage",
                "active_runner_check",
                "pending_questions",
                "user_request",
                "uncertain_artifacts",
                "pending_report_repair",
            )
        )
        and refusal(state)
    )


def reapproval_origin(state: RunState) -> dict:
    """Bind a fresh person decision to the current durable set preparation."""
    try:
        record = store.find(state["run_dir"])
    except (store.ChangesetError, OSError) as error:
        raise ValueError(str(error)) from error
    return {"stage": "changeset_reapproval", "changeset_revision": (record or {}).get("revision", 0)}


def execution_guard(state: RunState) -> None:
    if reason := refusal(state):
        raise util.Paused(STATUS, reason)


def approve(state: RunState, selected: str, set_token: str | None) -> None:
    """Bind the person's exact plan approval to a set preparation, or release this run alone."""
    own = observation(state)
    if own["plan_token"] != selected or not own["approved"]:
        raise ValueError("Change-set approval needs the exact approved current plan")
    try:
        record = store.find(state.get("run_dir")) if state.get("run_dir") else None
    except (store.ChangesetError, OSError) as error:
        raise ValueError(str(error)) from error
    if set_token:
        preparation = (record or {}).get("approval") or {}
        if preparation.get("token") != set_token or preparation.get("plans", {}).get(state.get("run_dir")) != selected:
            raise ValueError("Change-set membership or plans changed; inspect every plan again")
        state["changeset_approval"] = {
            "set_token": set_token,
            "plan_token": selected,
            "task_digest": own["task_digest"],
        }
    elif record or state.get("changeset_approval"):
        state["changeset_approval"] = {"standalone": True, "plan_token": selected, "task_digest": own["task_digest"]}
    if state.get("status") in (STATUS, "AWAITING_GOAL_APPROVAL"):
        run_status.running(state, "READY_TO_EXECUTE")


@contextmanager
def checkpoint(path, state: RunState):
    if state.get("parent_run"):
        yield  # parallel workers consume their parent's release; never publish over its observation
        return
    try:
        with store.publishing_approval(Path(path).parent, observation(state)):
            yield
    except store.ChangesetError as error:
        raise util.Paused(STATUS, str(error)) from error
