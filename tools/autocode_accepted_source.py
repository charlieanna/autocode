"""An operator hands a paused repair the source they edited while it was paused.

The approved contract, task, settings, budget, proof and evidence pins stay.
The pending resolution's source revision becomes the current snapshot. A recovery
packet bound to the replaced source stays on disk and is detached from the
request: that packet cannot be repinned, so admission must not compare it.
"""
from __future__ import annotations

from pathlib import Path

try:
    from . import autocode_source_scope as source_scope
    from . import autocode_util as util
except ImportError:
    import autocode_source_scope as source_scope
    import autocode_util as util


# The two pauses a source-only mismatch records. Anything else, including the
# Resolver writing the source or evidence changing, is not this command.
SOURCE_REASONS = frozenset({
    "Repair diagnosis needs the current reviewed source and task",
    "Recovery packet: current source, task, settings, contract or scope changed before admission",
})
RESOLVER_WROTE = frozenset({
    "Resolver must leave the reviewed source unchanged",
    "Diagnosis must leave the reviewed source unchanged",
})
COMMAND = "--resume-paused --accept-source-edit"


def resume_action(state) -> str | None:
    """The status-view command for a source-only stale repair, or None."""
    request = state.get("resolution_request") or {}
    if (state.get("status") == "PAUSED_STALE_HANDOFF" and request.get("source_revision")
            and state.get("stop_reason") in SOURCE_REASONS):
        return COMMAND
    return None


def accept_reviewed_source(state, workspace) -> dict:
    """Rebind the pending repair to the current source. Raise ValueError otherwise.

    Nothing is written until every check passes. The returned event is also
    appended to ``user_events``.
    """
    if state.get("status") != "PAUSED_STALE_HANDOFF":
        raise ValueError("--accept-source-edit applies to a repair paused because its reviewed source changed")
    reason = state.get("stop_reason") or ""
    if reason in RESOLVER_WROTE:
        raise ValueError("--accept-source-edit does not accept source the Resolver wrote")
    if reason not in SOURCE_REASONS:
        raise ValueError("--accept-source-edit only accepts a source edit of a paused repair diagnosis")
    request = state.get("resolution_request")
    if not isinstance(request, dict) or not request.get("source_revision"):
        raise ValueError("--accept-source-edit needs a pending repair diagnosis")
    if request.get("diagnosis_output"):
        raise ValueError("The saved blocker already has a diagnosis; reconcile it before accepting a source edit")
    contract = state.get("goal_contract") or {}
    task = state.get("current_task") or {}
    if request.get("contract_hash") != contract.get("hash") or request.get("task_id") != task.get("id"):
        raise ValueError("--accept-source-edit does not accept a contract or task change")
    for path, digest in (request.get("evidence_hashes") or {}).items():
        if not Path(path).is_file() or util.file_hash(path) != digest:
            raise ValueError("--accept-source-edit does not accept changed repair evidence")
    current = source_scope.snapshot(Path(workspace), state)["revision"]
    previous = request["source_revision"]
    if previous == current:
        raise ValueError("The reviewed source already matches this workspace")
    pointer = request.get("recovery_packet")
    if pointer:
        _packet_still_binds(state, pointer, previous)
    event = {
        "kind": "source_accepted", "actor": "user_cli", "at": util.now(),
        "previous_source_revision": previous, "source_revision": current,
        "recovery_packet": pointer,
    }
    request["source_revision"] = current
    for key in ("recovery_packet", "novelty_hold", "recovery_change", "recovery_change_id"):
        request.pop(key, None)
    state.setdefault("user_events", []).append(event)
    return event


def _packet_still_binds(state, pointer, previous):
    """The packet may differ from the workspace only in the source revision it names."""
    try:
        from . import autocode_resolver_recovery as recovery
    except ImportError:
        import autocode_resolver_recovery as recovery
    run = Path(state["run_dir"]) if state.get("run_dir") else recovery._run_root(
        state, {"output": state["resolution_request"].get("source_output", "")})
    try:
        packet = recovery.load_packet(pointer, run)
    except util.Paused as error:
        raise ValueError(str(error)) from error
    if packet["binding"]["source_revision"] != previous:
        raise ValueError("--accept-source-edit does not accept a packet bound to a different source")
    if packet["scope"] != recovery._scope(state):
        raise ValueError("--accept-source-edit only accepts a source edit; the task scope also changed")
    bound = recovery._binding(state, previous)
    if packet["binding"] != bound:
        changed = [key for key in ("contract_hash", "contract_revision", "task_id", "settings_hash")
                   if packet["binding"].get(key) != bound.get(key)]
        raise ValueError("--accept-source-edit only accepts a source edit; also changed: " + ", ".join(changed))
