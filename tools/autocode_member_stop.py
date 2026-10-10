"""The one way forward at a parallel Builder member's model stop once its question is answered otherwise (#541).

A batch member stopped by its quota or by its provider's content filter (quota_route.STATUSES) asks the
parent's route-terra question (autocode_worker_quota). A person may answer that request with corrective
information or leave the run paused instead (autocode_resolver_human.respond_operational). Neither names a
model or authorizes another attempt, and neither leaves a question open. One control continues from there,
``--resume-paused --retry-builder M``:

- a member its provider's content filter refused never reruns on that model (#465): the control collects its
  saved stop again, so AutoResolver asks its route-terra question again, and nothing launches;
- a quota-stopped member reruns unchanged (#458), for example once its quota has reset.

The request's advice, the response's acknowledgement and stop reason, AutoResolver's evaluation of the
information (autocode_operational_information), the status view's needs.action and the recovery card all name
it. A plain resume holds there. Reads saved state; imports only autocode_quota_route.

When the control asks a refused member's question again, ``ask_again`` (called by autocode_run_actions)
appends a ``builder_route_question`` user event (actor, at, milestone_id), its only write. Nothing reads it but
the request binding: it makes the asked-again request new, where an identical one would be held as already
answered.
"""

from __future__ import annotations

try:
    from . import autocode_quota_route as quota_route
except ImportError:
    import autocode_quota_route as quota_route


def _dict(value):
    return value if isinstance(value, dict) else {}


def current(state, origin):
    """(row, worker) for the stopped member of this exact BUILDING batch that ``origin`` names, or None."""
    worker = _dict(_dict(origin).get("quota_worker"))
    batch = _dict(state.get("orchestration_batch"))
    if batch.get("status") != "BUILDING" or state.get("next_stage") != "orchestrator":
        return None
    for row in batch.get("workers") or []:
        if (
            isinstance(row, dict)
            and row.get("milestone_id") == worker.get("milestone_id")
            and row.get("run_dir") == worker.get("run_dir")
            and row.get("workspace") == worker.get("workspace")
            and row.get("status") in quota_route.STATUSES
            and row.get("status") == worker.get("pause_status", quota_route.QUOTA_STATUS)
        ):
            return row, worker
    return None


def _answered_proposal(state):
    resolver = _dict(state.get("resolver"))
    frontier = _dict(resolver.get("human_response_frontier"))
    rid = frontier.get("request_id")
    entry = _dict(_dict(resolver.get("human_escalations")).get(rid)) if isinstance(rid, str) else {}
    return frontier, entry, _dict(_dict(entry.get("identity")).get("proposal"))


def answered(state):
    """(row, worker) for the member whose model-stop request a person answered, while no question is open.

    The answered request is the one autocode_resolver_human saved as the response frontier; the run is
    still at that stop, and the member is still the stopped member of the current batch.
    """
    frontier, entry, proposal = _answered_proposal(state)
    origin = _dict(proposal.get("origin"))
    if (
        entry.get("status") != "consumed"
        or not isinstance(origin.get("quota_worker"), dict)
        or state.get("status") not in quota_route.STATUSES
        or state.get("status") != origin.get("pause_status")
        or frontier.get("pause_status") != state.get("status")
        or state.get("pending_questions")
        or state.get("resolver_human_request")
    ):
        return None
    return current(state, origin)


def action(state):
    """The one command that continues from an answered member's model stop, or None."""
    found = answered(state)
    return f"--resume-paused --retry-builder {found[0]['milestone_id']}" if found else None


def _refused(row):
    return row.get("status") == quota_route.REFUSAL_STATUS


def reason(found):
    """Why information cannot continue this member, for AutoResolver's held evaluation."""
    row, worker = found
    milestone, on = row["milestone_id"], f" on {worker['model']}" if worker.get("model") else ""
    if _refused(row):
        return (
            f"Builder {milestone} was refused by its provider's content filter{on}, and information cannot name "
            f"the model it continues on; --retry-builder {milestone} asks its route-terra question again, "
            "without rerunning the refused model"
        )
    return (
        f"Builder {milestone} stopped on quota{on}, and information cannot authorize its retry; once the quota "
        f"resets, --retry-builder {milestone} retries it unchanged"
    )


def next_step(state):
    """One sentence naming the command that continues from an answered member's model stop, or None."""
    found = answered(state)
    if not found:
        return None
    milestone = found[0]["milestone_id"]
    if _refused(found[0]):
        return (
            f"That answer names no model for Builder {milestone}: --resume-paused --retry-builder {milestone} "
            "asks its route-terra question again, and nothing reruns the refused model."
        )
    return (
        f"That answer does not retry Builder {milestone}: once its quota resets, --resume-paused "
        f"--retry-builder {milestone} retries it unchanged."
    )


def advice(worker):
    """The request's advice on corrective information at this member's stop; never a plain resume (#541)."""
    milestone = worker["milestone_id"]
    after = (
        "asks this question again"
        if worker.get("pause_status") == quota_route.REFUSAL_STATUS
        else "retries it unchanged once the quota resets"
    )
    return (
        "Corrective information (--resolver-request ID --resolver-token TOKEN --resolver-response "
        f"provide_information --resolver-message TEXT) names no model for Builder {milestone}; after it, "
        f"--resume-paused --retry-builder {milestone} {after}."
    )


def card(state, milestone):
    """(label, effect) of the recovery card's action for an answered member's model stop, or None."""
    found = answered(state)
    if not found or found[0]["milestone_id"] != milestone:
        return None
    if _refused(found[0]):
        return (
            f"Ask which model Builder task {milestone} continues on",
            "Its provider's content filter refused this member; it never reruns on that model. AutoResolver "
            "asks its model question again and nothing launches. Completed members and their work are kept.",
        )
    return (
        f"Retry Builder task {milestone} unchanged",
        "Rerun only this member on the same model, for example once its quota has reset. Completed members "
        "and their work are kept. The runner rechecks worker liveness and the approved batch.",
    )


def ask_again(state, error, *, at):
    """Set the run at a refused member's stop that its --retry-builder collected again (``error``).

    The person's request is the event that makes the request AutoResolver asks next new.
    """
    state.setdefault("user_events", []).append(
        {
            "kind": "builder_route_question",
            "actor": "user_cli",
            "at": at,
            "milestone_id": error.quota_worker["milestone_id"],
        }
    )
    state.update(status=error.status, stop_reason=str(error), phase="PAUSED_OR_BLOCKED")


def restore(state, error, *, detail=True):
    """``error`` as the answered member's own stop, so AutoResolver asks its route-terra question again.

    Its reason is the stop the request first reported, followed by ``error``'s own reason when
    ``detail``; any other stop is returned unchanged.
    """
    found = answered(state)
    if not found or error.status != found[0]["status"]:
        return error
    first = str(_dict(_answered_proposal(state)[2].get("request")).get("discovered") or "").strip()
    rest = str(error).strip() if detail or not first else ""
    if first and rest:
        first += "" if first.endswith(".") else "."
    restored = type(error)(error.status, " ".join(part for part in (first, rest) if part))
    restored.quota_worker = found[1]
    return restored
