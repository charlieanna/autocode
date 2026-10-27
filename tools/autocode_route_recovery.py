"""Switch a stopped model's role to its next permitted route (#1426 step 9).

Rule 4 of the reopened issue: on a quota, content-filter or output stop, AutoCode
moves the role on its own to the next qualified model on an account the run was
already permitted to bill — checkers stay independent — and asks only when no such
model remains, which is the pause that has always stood (#184). Only a run that
saved its permitted set (``--auto-select``, ``route_selection.candidates``) can do
this: every other run keeps today's question. Native runs and workflow jobs keep
asking too — a retained native attempt grants no automatic recovery, and a job's
retry is bound to its exact token (#463). So does a role whose model the person pinned
(``--<role>-model``, origin ``user_explicit`` in the saved selection): the switch is for
the selector's own choices, never for a model a person named.

The move is the same recorded ``route_assignment`` a person's answer applies
(``autocode_quota_route.assign``), via ``runner``, so the route record reads it as
``quota_assigned`` like any answered switch. Imports only lower-level modules; the
runtime is passed in, keeping this below the controller.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_model_identity as model_identity
    from . import autocode_quota_route as quota_route
    from . import autocode_route_selector as route_selector
    from . import autocode_run_status as run_status
    from . import autocode_support as support
    from . import autocode_verifier_independence as independence
except ImportError:
    import autocode_model_identity as model_identity
    import autocode_quota_route as quota_route
    import autocode_route_selector as route_selector
    import autocode_run_status as run_status
    import autocode_support as support
    import autocode_verifier_independence as independence


def billing(selection, model):
    """How the saved permitted set says ``model`` bills, or None when it does not say."""
    wanted = model_identity.identity(model)
    for candidates in ((selection or {}).get("candidates") or {}).values():
        for candidate in candidates:
            if model_identity.identity(candidate.get("model")) == wanted and candidate.get("billing"):
                return candidate["billing"]
    return None


def recover(runtime, state: RunState, run_dir, workspace, error) -> bool:
    """One quota-class stop, one automatic switch; False leaves the pause standing."""
    if getattr(runtime, "native_calls", None) is not None:
        return False
    if error.status not in quota_route.STATUSES:
        return False
    selection = (state.get("settings") or {}).get("route_selection") or {}
    if not selection.get("candidates"):
        return False
    stopped = quota_route.stopped_attempt(state, failure_status=support.failure_status)
    if not stopped or not stopped.get("active") or stopped.get("workflow_job"):
        return False
    role = stopped.get("role")
    if role not in quota_route.ROLES:
        return False
    if ((selection.get("routes") or {}).get(role) or {}).get("origin") == route_selector.role_route.USER_EXPLICIT:
        return False  # the person pinned this role's model; it is theirs to change, so the pause asks
    route = ((state.get("settings") or {}).get("roles") or {}).get(role) or {}
    dead = stopped.get("model") or route.get("model")
    if not dead:
        return False
    failed = [
        event.get("from")
        for event in state.get("user_events", [])
        if event.get("kind") == quota_route.KIND and event.get("role") == role and event.get("actor") == "runner"
    ]
    # Pin the routes that would actually check the retry.
    current = route_selector.current(selection, (state.get("settings") or {}).get("roles"))
    switched = route_selector.next_route(current, role, dead, failed_models=failed)
    if switched is None:
        return False
    if state.get("pause_requested") or (Path(run_dir) / "pause-requested").exists():
        return False
    runtime.timeout_recovery_guard(state)
    # Use the existing abandonment boundary: it proves the process stopped, accounts
    # usage, retains partial work and durably archives the failed attempt. Include
    # the replacement and failure history in that save, so a crash cannot retry an
    # exhausted route or discard its evidence. Work on a copy until it succeeds.
    candidate = copy.deepcopy(state)
    quota_route.assign(
        candidate, role, switched["model"], at=runtime.now(), via="runner", attempt=stopped, actor="runner"
    )
    # A switch may move the role onto another way of paying (#2061): say so in the record and the line.
    was, now = billing(selection, dead), billing(selection, switched["model"])
    changed = bool(was and now and was != now)
    if changed:
        candidate["user_events"][-1]["billing"] = {"from": was, "to": now}
    candidate["settings"]["roles"][role]["reasoning_effort"] = switched.get("reasoning_effort")
    candidate["settings"]["route_selection"]["routes"][role] = {**switched, "origin": "quota_assigned"}
    if independence.violations(candidate["settings"]):
        return False
    runtime.count_automatic_recovery(candidate)
    runtime.abandon_stage(candidate, Path(run_dir), Path(workspace), stopped["attempt_id"], actor="runner")
    run_status.running(candidate, state.get("phase") or "IMPLEMENTING")
    runtime.write_json(Path(run_dir) / "state.json", candidate)
    cast(dict, state).clear()
    state.update(candidate)
    job = quota_route.roles.screen_name(stopped.get("stage") or role, state)
    account = switched.get("account") or "the run's tool"
    change = f"; it bills {now} where {dead} was {was}" if changed else ""
    print(
        f"{job}: {dead} stopped ({error.status}); switched to {switched['model']} on {account} without asking{change}",
        flush=True,
    )
    return True
