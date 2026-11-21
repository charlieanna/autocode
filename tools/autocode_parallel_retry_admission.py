"""Read-only admission for a parallel Builder retry."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_builder_policy as builder_policy
    from . import autocode_goals as goals
    from . import autocode_process as processes
    from . import autocode_support as s
    from . import autocode_worker_lifeline as lifeline
    from . import autocode_worker_retry_policy as worker_quota
except ImportError:
    import autocode_builder_policy as builder_policy
    import autocode_goals as goals
    import autocode_process as processes
    import autocode_support as s
    import autocode_worker_lifeline as lifeline
    import autocode_worker_retry_policy as worker_quota


def worker_alive(row):
    """A saved worker, its lifeline keeper or anything either of them recorded still runs."""
    saved = lifeline.owned(row.get("supervision"), row.get("processes") or ())
    return bool(saved) and bool(processes.live_processes(saved))


def unverified(row):
    """Its lifeline keeper never verified the cleanup after this worker (#454).

    A BUILT result from such a worker is held, and --retry-builder may rerun it.
    Batches saved before worker lifelines have no record and are never held.
    """
    return bool(row.get("supervision")) and lifeline.settled(row["supervision"]) not in lifeline.ADOPTABLE


def member_retry_refusal(state: RunState, selected, *, issued=None, worker_alive=worker_alive, unverified=unverified):
    """The current batch's member retry rejection or collected model stop, otherwise None.

    Run setup uses the same approval, liveness and saved-result checks before a settings write.
    A ValueError or ordinary Paused refuses the command. A Paused carrying a member stop is
    collected again after its question was answered without a model (#541), and is left to
    the retry path to publish that question again.
    """
    try:
        goals.execution_guard(state)
    except s.Paused as error:
        return error
    batch = state.get("orchestration_batch")
    if (
        not batch
        or batch["status"] != "BUILDING"
        or state.get("next_stage") != "orchestrator"
        or batch["contract_hash"] != cast(dict, state["goal_contract"])["hash"]
    ):
        return ValueError("Builder retry requires the current approved batch at its build checkpoint")
    rows = {row["milestone_id"]: row for row in batch["workers"]}
    if not set(selected) <= set(rows):
        return ValueError("--retry-builder must name a milestone in the current batch")
    for row in batch["workers"]:
        if worker_alive(row):
            return s.Paused("PAUSED_ORCHESTRATOR_WORKERS", "A Builder is still running; wait before retrying")
        s.assert_no_legacy_process(Path(row["run_dir"]), Path(row["workspace"]))
    try:
        from . import autocode_resolver_human as human
    except ImportError:
        import autocode_resolver_human as human
    asked = worker_quota.asked_member(state, human.current(state) or issued)
    for mid in selected:
        result = Path(rows[mid]["run_dir"]) / "result.json"
        saved = s.read(result) if result.exists() else {}
        if saved.get("status") == "BUILT" and not unverified(rows[mid]):
            # A BUILT result whose lifeline cleanup is unverified is held, and may be rerun (#454).
            return ValueError(f"Builder {mid} already completed; its work will be retained")
        if saved.get("status") == builder_policy.SERIAL:
            return ValueError(f"Builder {mid} left its stronger attempt to a serial build after this batch")
        if saved.get("status") == builder_policy.SERIAL_USER_REQUEST:
            return ValueError(f"Builder {mid} deferred its shared-change request to a serial build after this batch")
        refused = worker_quota.refused_retry(state, rows[mid], saved, asked=asked)
        if refused:
            return refused
    settings = state.get("settings") or {}
    route_refusal = worker_quota.retry_route_refusal(state, selected, settings, settings, asked=asked)
    if route_refusal:
        return ValueError(route_refusal)
    return None
